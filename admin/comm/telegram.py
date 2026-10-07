"""Telegram Bot for CEO internal commands, alerts, and direct CEO chat."""
import asyncio
import os
import httpx
import json
import logging
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Set
from fastapi import APIRouter, Request, HTTPException

# Use settings which properly loads .env from project root
from admin.config import settings

TELEGRAM_BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = settings.TELEGRAM_CHAT_ID
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""

router = APIRouter(prefix="/telegram", tags=["telegram"])

logger = logging.getLogger(__name__)

# Strong references to in-flight webhook tasks, so they are never garbage
# collected before they deliver their reply. See _dispatch_in_background().
_PENDING: Set["asyncio.Task[Any]"] = set()


# ── Chat authorization guard ──────────────────────────────────────────────────
#
# Every Telegram update that can spend money, contact a lead, publish, or talk to
# the CEO passes through `authorize_update()`. There is exactly one dispatcher,
# `process_telegram_update()`, and it calls the guard before it reaches a handler,
# so a new command cannot skip the check by accident.
#
# Decisions, and why:
#
# 1. TELEGRAM_CHAT_ID unset or blank -> FAIL CLOSED. An empty allowlist must never
#    mean "everyone is the owner"; that is the hole being closed. Nothing runs.
#    The failure is loud (ERROR at import time, ERROR per rejected update) and it
#    does not take the webhook route down: the HTTP call still returns 200 so
#    every other route keeps working and Telegram stops retrying a request that
#    can never be authorised. The process still boots, because a missing optional
#    env var must not kill the whole container.
#
# 2. Several chat ids may be configured. The value is read as a comma, semicolon,
#    whitespace or newline separated string, or as a real list/tuple/set. Every
#    parsed id is authorised. Entries that are not plain integers are ignored
#    with an ERROR, because silently ignoring them would leave the operator with
#    an allowlist smaller than they think.
#
# 3. Authorization is against `message.chat.id`, NOT `message.from.id`.
#    TELEGRAM_CHAT_ID names the conversation the owner drives the agency from, it
#    is the id replies are sent back to (the default target of
#    send_telegram_message), and matching it keeps working when the owner adds
#    the bot to a group or a channel. Matching the sender id instead would mean
#    checking a different identifier than the one that is actually configured, and
#    would silently lock the operator out of their own console.
#    A chat id alone is not proof of WHO spoke once the bot sits in a group, so
#    an optional sender allowlist (TELEGRAM_ALLOWED_USER_IDS) is enforced on top
#    of the chat check whenever the operator sets it. Off by default: it only
#    ever makes access stricter, never looser.
#
# 4. `message.from` is absent for channel posts and for anonymous admins in a
#    supergroup. The chat check still applies either way. When a sender allowlist
#    is configured the update is rejected, because we cannot prove who wrote it
#    (fail closed). Without a sender allowlist the chat allowlist is the whole
#    trust boundary, exactly as it is for every other message in that chat.
#    Note the webhook deliberately still acts only on `message`/`edited_message`,
#    as before: channel posts are not a new command path.

AUTH_OK = "authorized"
AUTH_CHAT_NOT_ALLOWED = "chat_not_allowed"
AUTH_USER_NOT_ALLOWED = "user_not_allowed"
AUTH_NOT_CONFIGURED = "chat_id_not_configured"
AUTH_NO_SENDER = "sender_unverifiable"


class AuthDecision(NamedTuple):
    """Outcome of the guard. Never raises, never executes anything."""

    allowed: bool
    reason: str
    chat_id: Optional[int]
    user_id: Optional[int]


def _iter_id_tokens(raw: Any) -> Iterable[str]:
    """Yield candidate ids from a string, list, tuple or set config value."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set, frozenset)):
        return [str(item) for item in raw]
    # Split on every separator an operator is likely to paste in.
    cleaned = str(raw).replace(",", " ").replace(";", " ").replace("\n", " ")
    return cleaned.split()


def _parse_ids(raw: Any) -> Set[int]:
    """Parse an allowlist value into a set of integer Telegram ids."""
    ids: Set[int] = set()
    invalid: List[str] = []
    for token in _iter_id_tokens(raw):
        token = token.strip()
        if not token:
            continue
        try:
            ids.add(int(token))
        except ValueError:
            invalid.append(token)
    if invalid:
        # Chat usernames (@name) are refused on purpose: a username can be
        # re-registered by a different owner after the original chat dies, which
        # would hand the agency to a stranger.
        logger.error(
            "Telegram: ignoring %d non numeric allowlist entry/entries: %s",
            len(invalid),
            ", ".join(invalid),
        )
    return ids


def _configured_chat_ids() -> Set[int]:
    """Owner chat allowlist, re-read from settings on every call.

    Re-reading (rather than trusting the import time constant) keeps the guard
    correct across reloads and lets tests override the configuration.
    """
    raw = getattr(settings, "TELEGRAM_CHAT_ID", "")
    if not str(raw or "").strip():
        raw = TELEGRAM_CHAT_ID
    return _parse_ids(raw)


def _configured_user_ids() -> Set[int]:
    """Optional sender allowlist, read straight from the environment.

    It is read with os.getenv so this optional knob does not require touching
    the shared settings module. Absent or blank means "do not enforce senders".
    """
    raw = os.getenv(
        "TELEGRAM_ALLOWED_USER_IDS",
        getattr(settings, "TELEGRAM_ALLOWED_USER_IDS", ""),
    )
    return _parse_ids(raw)


def _primary_chat_id() -> Optional[str]:
    """First configured chat id, used as the default outbound target."""
    for token in _iter_id_tokens(
        getattr(settings, "TELEGRAM_CHAT_ID", "") or TELEGRAM_CHAT_ID
    ):
        token = token.strip()
        if token:
            return token
    return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def authorize_update(message: Dict[str, Any]) -> AuthDecision:
    """THE chat authorization guard for Telegram updates.

    Returns a decision and performs no side effects other than logging. Chat id,
    user id and the configured allowlist are logged for debugging; the bot token
    and message contents are never logged here.
    """
    raw_chat = (message.get("chat") or {}).get("id")
    chat_id = _as_int(raw_chat)

    sender = message.get("from")
    user_id = _as_int(sender.get("id")) if isinstance(sender, dict) else None

    allowed_chats = _configured_chat_ids()
    if not allowed_chats:
        # Decision 1: fail closed.
        logger.error(
            "Telegram DENIED (chat_id=%s user_id=%s): TELEGRAM_CHAT_ID is not "
            "configured, failing closed: no chat is authorised and every "
            "command is disabled until it is set.",
            raw_chat,
            user_id,
        )
        return AuthDecision(False, AUTH_NOT_CONFIGURED, chat_id, user_id)

    if chat_id is None or chat_id not in allowed_chats:
        logger.warning(
            "Telegram DENIED (chat_id=%s user_id=%s): chat is not the "
            "authorised chat %s.",
            raw_chat,
            user_id,
            sorted(allowed_chats),
        )
        return AuthDecision(False, AUTH_CHAT_NOT_ALLOWED, chat_id, user_id)

    allowed_users = _configured_user_ids()
    if allowed_users:
        # Decisions 3 and 4: chat matches, now prove the sender.
        if user_id is None:
            logger.warning(
                "Telegram DENIED (chat_id=%s): update has no `from` sender "
                "(channel post or anonymous admin), cannot verify it against "
                "TELEGRAM_ALLOWED_USER_IDS %s.",
                chat_id,
                sorted(allowed_users),
            )
            return AuthDecision(False, AUTH_NO_SENDER, chat_id, user_id)
        if user_id not in allowed_users:
            logger.warning(
                "Telegram DENIED (chat_id=%s user_id=%s): sender is not an "
                "authorised user of that chat (authorised senders: %s).",
                chat_id,
                user_id,
                sorted(allowed_users),
            )
            return AuthDecision(False, AUTH_USER_NOT_ALLOWED, chat_id, user_id)

    logger.info(
        "Telegram authorized (chat_id=%s user_id=%s).", chat_id, user_id
    )
    return AuthDecision(True, AUTH_OK, chat_id, user_id)


if not str(TELEGRAM_CHAT_ID or "").strip():
    # Loud at boot as well, so the misconfiguration is visible even before the
    # first update arrives. Warning and above reach stderr through the logging
    # last resort handler even when the app has not configured logging yet.
    logger.error(
        "Telegram: TELEGRAM_CHAT_ID is not set. The webhook will reject every "
        "update (fail closed) until it is configured."
    )


async def send_telegram_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True
) -> dict:
    """Send message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

    # TELEGRAM_CHAT_ID may hold several ids; alerts go to the first one.
    target_chat = chat_id or _primary_chat_id()
    if not target_chat:
        return {"success": False, "error": "No chat_id configured"}

    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
            if resp.status_code == 200:
                return {"success": True, "message_id": resp.json().get("result", {}).get("message_id")}
            return {"success": False, "error": f"{resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def send_telegram_alert(title: str, message: str, level: str = "info") -> dict:
    """Send formatted alert to CEO."""
    emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "success": "✅"}.get(level, "ℹ️")
    text = f"{emoji} <b>{title}</b>\n\n{message}"
    return await send_telegram_message(text)


# CEO Commands
COMMANDS = {
    "/status": "Show autonomy status",
    "/leads": "List recent leads",
    "/lead <id>": "Show lead details",
    "/approve <id>": "Approve pending action",
    "/reject <id>": "Reject pending action",
    "/blast <niche>": "Run multi-agent blast for niche",
    "/finance": "Show finance snapshot",
    "/help": "Show this help",
}


async def handle_ceo_command(command: str, args: list[str]) -> str:
    """Process CEO command and return response."""
    cmd = command.lower().split()[0] if command else "/help"

    if cmd == "/help":
        return "🤖 <b>CEO Commands:</b>\n" + "\n".join(f"<code>{k}</code> — {v}" for k, v in COMMANDS.items())

    if cmd == "/status":
        # Import here to avoid circular imports
        from admin.ceo_data import get_agency_overview
        try:
            overview = get_agency_overview()
            s = overview.get("summary", {})
            return (
                f"📊 <b>Agency Status</b>\n"
                f"Leads: {s.get('total_leads', 0)} total, {s.get('active_leads', 0)} active\n"
                f"Pipeline: ₹{s.get('pipeline_value', 0):,}\n"
                f"Reviews pending: {s.get('pending_reviews', 0)}"
            )
        except Exception as e:
            return f"❌ Error: {e}"

    if cmd == "/leads":
        from admin.agency.sba_store import list_leads
        try:
            leads = list_leads()[:10]
            if not leads:
                return "📭 No leads yet"
            lines = ["📋 <b>Recent Leads:</b>"]
            for l in leads:
                lines.append(f"• <code>{l['id'][:8]}</code> {l['business_name']} — {l['status']} (score: {l['score']})")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error: {e}"

    if cmd == "/lead" and args:
        from admin.agency.sba_store import get_lead
        try:
            lead = get_lead(args[0])
            if not lead:
                return f"❌ Lead {args[0]} not found"
            return (
                f"🎯 <b>Lead Details</b>\n"
                f"ID: <code>{lead['id']}</code>\n"
                f"Name: {lead['business_name']}\n"
                f"Contact: {lead.get('email', '—')} / {lead.get('phone', '—')}\n"
                f"Status: {lead['status']}\n"
                f"Score: {lead['score']}\n"
                f"Notes: {', '.join(lead.get('notes', []))}"
            )
        except Exception as e:
            return f"❌ Error: {e}"

    if cmd == "/approve" and args:
        from admin.agency.ceo_autonomy import decide_approval
        try:
            result = await decide_approval(args[0], "approved", "Approved via Telegram")
            if result:
                return f"✅ Approved {args[0]}"
            return f"❌ Could not approve {args[0]} (not pending?)"
        except Exception as e:
            return f"❌ Error: {e}"

    if cmd == "/reject" and args:
        from admin.agency.ceo_autonomy import decide_approval
        try:
            result = await decide_approval(args[0], "rejected", "Rejected via Telegram")
            if result:
                return f"❌ Rejected {args[0]}"
            return f"❌ Could not reject {args[0]}"
        except Exception as e:
            return f"❌ Error: {e}"

    if cmd == "/blast" and args:
        from admin.agency.ceo import _ceo
        try:
            niche = " ".join(args)
            result = await _ceo.parallel_blast(
                workspace_id="ws_agency",
                client_brief=f"Multi-agent research sprint: identify starving crowd for {niche}. SBA: 5 prospects with hook. Content: hook paragraph. Website: landing outline. NO external actions.",
                campaign_name=f"Blast: {niche}",
                deadline="ASAP"
            )
            return f"🚀 Blast launched for <b>{niche}</b>: {result.get('ran', 0)} agents ran"
        except Exception as e:
            return f"❌ Error: {e}"

    if cmd == "/finance":
        from admin.api.routes.sba import get_finance
        try:
            # Call the finance endpoint logic
            from admin.agency.sba_store import list_leads
            from admin.api.routes.analytics import get_revenue_forecast
            leads = list_leads()
            pipeline = len([l for l in leads if l['status'] == 'active'])
            return (
                f"💰 <b>Finance Snapshot</b>\n"
                f"Active leads: {pipeline}\n"
                f"Pipeline value: ₹0\n"
                f"Forecast: Run /blast to generate"
            )
        except Exception as e:
            return f"❌ Error: {e}"

    return f"❓ Unknown command: {command}\nUse /help for list"


async def process_telegram_update(body: Dict[str, Any]) -> Dict[str, Any]:
    """The single funnel for every incoming Telegram update.

    Authorization happens here, before any handler is reachable, so this is the
    only place a new command has to be wired through. New routes must call this
    function, never a handler directly.
    """
    message = body.get("message") or body.get("edited_message")
    if not isinstance(message, dict) or not message:
        # No message payload at all (callback query, reaction, ...): nothing to
        # authorize and nothing to run.
        return {"ok": True, "authorized": True, "handled": False}

    decision = authorize_update(message)
    if not decision.allowed:
        # Rejected. The response is deliberately non-leaky: HTTP 200 (Telegram
        # retries non 2xx, and retrying can never succeed), a fixed body that
        # names no config value, no stack trace, and no Telegram reply sent back
        # into the unauthorised chat. The reason lives in the server log.
        return {"ok": True, "authorized": False, "handled": False}

    chat_id = str(decision.chat_id)
    text = (message.get("text") or "").strip()

    # Commands
    if text.startswith("/"):
        parts = text.split()
        cmd = parts[0]
        args = parts[1:]
        logger.info(
            "Telegram command from chat_id=%s user_id=%s cmd=%s",
            decision.chat_id,
            decision.user_id,
            cmd,
        )
        response = await handle_ceo_command(cmd, args)
        await send_telegram_message(response, chat_id)
        return {"ok": True, "authorized": True, "handled": True}

    # Non-command text -> CEO chat (direct agency conversation)
    if text:
        await handle_ceo_chat(text, chat_id)
        return {"ok": True, "authorized": True, "handled": True}

    return {"ok": True, "authorized": True, "handled": False}


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram messages - commands + CEO chat.

    This route acknowledges Telegram IMMEDIATELY and does the real work in a
    background task.

    The ordering is the whole point. A CEO reply is a full LLM round trip, which
    takes tens of seconds. Telegram only waits about 10 seconds for a webhook
    response; anything slower is treated as a failure and the update is redelivered,
    which is what made replies arrive late and sometimes twice. Ack first, think
    after, and let `send_telegram_message` deliver the answer out of band.

    This route is a thin adapter only: it turns HTTP into an update dict and
    hands it to process_telegram_update(), which owns the authorization guard
    and is the sole dispatcher to handle_ceo_command()/handle_ceo_chat().
    """
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(500, "Bot not configured")

    try:
        # Read raw body first to handle empty bodies
        raw_body = await request.body()
        if not raw_body:
            logger.warning("Empty webhook body received")
            return {"ok": True}

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in webhook body")
            return {"ok": True}

        if not isinstance(body, dict):
            logger.warning("Telegram webhook body is not a JSON object")
            return {"ok": True}

        # No message text is logged at INFO level; the guard logs chat and user
        # ids only.
        #
        # Authorization runs here, synchronously, because it is pure id
        # comparison with no I/O and it is the answer the caller (and the tests,
        # and anyone auditing an incident) needs to see in the response. Only
        # the slow part is detached.
        message = body.get("message") or body.get("edited_message")
        if not isinstance(message, dict) or not message:
            return {"ok": True, "authorized": True, "handled": False}

        decision = authorize_update(message)
        if not decision.allowed:
            logger.warning(
                "Telegram update rejected from chat_id=%s user_id=%s (%s)",
                decision.chat_id, decision.user_id, decision.reason,
            )
            return {"ok": True, "authorized": False, "handled": False}

        # process_telegram_update re-runs the guard. That is deliberate: it stays
        # the single funnel every update must pass through, and the check costs
        # nothing next to the LLM call it is protecting.
        _dispatch_in_background(body)
        return {"ok": True, "authorized": True, "handled": False}

    except Exception as e:
        logger.error(f"Telegram webhook error: {e}", exc_info=True)
        return {"ok": True}


def _dispatch_in_background(body: Dict[str, Any]) -> None:
    """Run process_telegram_update() off the request path.

    The task holds a strong reference for its whole life. asyncio only keeps
    weak references to tasks, so a fire-and-forget task that nobody holds can be
    garbage collected mid-flight. That would silently drop the reply rather than
    make it late, which is the failure mode that is much harder to notice.
    """
    task = asyncio.create_task(_guarded_process(body))
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def _guarded_process(body: Dict[str, Any]) -> None:
    """process_telegram_update() with the exception handling it now needs.

    It used to run inside the request, where the route's try/except caught
    everything. Detached, an unhandled exception would only reach the event loop
    logger, so the guarantee is made explicit here instead.
    """
    try:
        await process_telegram_update(body)
    except Exception as e:
        logger.error("Telegram background dispatch failed: %s", e, exc_info=True)
        try:
            chat_id = ((body.get("message") or body.get("edited_message") or {})
                       .get("chat") or {}).get("id")
            if chat_id:
                await send_telegram_message(
                    f"CEO error: {str(e)[:100]}", str(chat_id))
        except Exception:
            logger.error("Could not deliver the Telegram error notice", exc_info=True)


async def set_webhook(url: str) -> dict:
    """Set Telegram webhook."""
    if not TELEGRAM_BOT_TOKEN:
        return {"success": False, "error": "Bot token not configured"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{TELEGRAM_API_URL}/setWebhook", json={"url": url})
            return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_ceo_chat(text: str, chat_id: str) -> None:
    """Send user message to CEO agent and return response."""
    # Only the ids and the length are logged at INFO. The text itself stays out
    # of INFO level logs.
    logger.info("CEO chat called for chat_id=%s (message length %d)", chat_id, len(text))
    logger.debug("CEO chat text (chat_id=%s): %s", chat_id, text[:50])
    try:
        from admin.api.routes.ceo import _ceo
        from admin.agency.ceo_autonomy import emit_event
        
        logger.info("CEO imported successfully, calling chat...")
        
        # Use CEO agent's chat method (no user_id parameter)
        response, conv_id, _ = await _ceo.chat(
            message=text,
            conversation_id=f"telegram_{chat_id}"
        )
        
        logger.debug("CEO response received: %s", response[:100])
        
        # Send CEO response back to Telegram
        await send_telegram_message(response, chat_id)
        logger.info("Response sent to Telegram")
        
        # Log the interaction
        await emit_event(
            "telegram_chat",
            workspace_id="ws_agency",
            source="telegram",
            payload={"chat_id": chat_id, "user_message": text, "ceo_response": response[:200]}
        )
        logger.info("Event emitted")
    except Exception as e:
        logger.error(f"CEO chat error: {e}", exc_info=True)
        # Fallback response
        await send_telegram_message(f"CEO error: {str(e)[:100]}", chat_id)


# Export for use in other modules
async def alert_ceo(title: str, message: str, level: str = "info"):
    """Convenience function for other modules to alert CEO."""
    return await send_telegram_alert(title, message, level)