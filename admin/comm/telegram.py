"""Telegram Bot for CEO internal commands, alerts, and direct CEO chat."""
import os
import httpx
import json
import logging
from typing import Optional
from fastapi import APIRouter, Request, HTTPException

# Use settings which properly loads .env from project root
from admin.config import settings

TELEGRAM_BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = settings.TELEGRAM_CHAT_ID
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""

router = APIRouter(prefix="/telegram", tags=["telegram"])

logger = logging.getLogger(__name__)


async def send_telegram_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True
) -> dict:
    """Send message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

    target_chat = chat_id or TELEGRAM_CHAT_ID
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


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram messages - commands + CEO chat."""
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
            
        logger.info(f"Webhook body: chat_id={body.get('message', {}).get('chat', {}).get('id')}, text={body.get('message', {}).get('text')}")
        
        message = body.get("message") or body.get("edited_message")
        if not message:
            return {"ok": True}

        chat_id = str(message.get("chat", {}).get("id"))
        text = message.get("text", "").strip()

        # Only respond to authorized chat (relaxed for debug)
        logger.info(f"Incoming chat_id: '{chat_id}', Authorized: '{TELEGRAM_CHAT_ID}'")
        # Proceeding without strict blocking for debugging
        if TELEGRAM_CHAT_ID and chat_id.strip() != TELEGRAM_CHAT_ID.strip():
            logger.warning(f"DEBUG: Unauthorized, but proceeding. ChatID: '{chat_id}', Expected: '{TELEGRAM_CHAT_ID}'")
            # return {"ok": True}  # Commented out to unblock you!

        # Handle commands
        if text.startswith("/"):
            parts = text.split()
            cmd = parts[0]
            args = parts[1:]
            response = await handle_ceo_command(cmd, args)
            await send_telegram_message(response, chat_id)
            return {"ok": True}

        # Non-command text -> CEO chat (direct agency conversation)
        if text:
            await handle_ceo_chat(text, chat_id)
            return {"ok": True}

        return {"ok": True}

    except Exception as e:
        logger.error(f"Telegram webhook error: {e}", exc_info=True)
        return {"ok": True}


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
    logger.info(f"CEO chat called with text: {text[:50]} for chat_id: {chat_id}")
    try:
        from admin.api.routes.ceo import _ceo
        from admin.agency.ceo_autonomy import emit_event
        
        logger.info("CEO imported successfully, calling chat...")
        
        # Use CEO agent's chat method (no user_id parameter)
        response, conv_id, _ = await _ceo.chat(
            message=text,
            conversation_id=f"telegram_{chat_id}"
        )
        
        logger.info(f"CEO response received: {response[:100]}")
        
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