"""AgentMail client for the TAGS Agency multi-agent system.

Gives our agents their OWN email identity on the FREE @agentmail.to domain
(no domain purchase, no mail server, no EC2 load - it's a SaaS API, lightweight).

Per-user email routing policy (the user's decision, 2026-08-23):
  * Only TWO agent inboxes are enabled (free plan = 3 inboxes max):
      - sba@agentmail.to   -> SBA talks to the CLIENT and to the USER (leads/meetings)
      - ceo@agentmail.to   -> CEO talks to the CLIENT and to the USER
  * All OTHER agents (seo, etc.) do NOT get their own inbox. They tell the CEO
    what to say; the CEO sends the email on their behalf (acting as that agent).
  * So: client + user only ever see emails from sba@ or ceo@.

Onboarding is one-time:
  1. Sign up the org with a real human email (OTP sent there).
  2. Verify the OTP -> persistent api_key (saved, gitignored).
  3. Mint the two enabled inboxes.

Config lives in config/agentmail.json (gitignored: api_key + inbox ids).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from agentmail import AgentMail
from agentmail.inboxes.types.create_inbox_request import CreateInboxRequest

logger = logging.getLogger(__name__)

# Registry of which agent is the PRIMARY sender. The CEO sends most emails and
# relays on behalf of every other (inbox-less) agent.
PRIMARY_SENDER = "ceo"

# Actors that get their OWN AgentMail inbox (free plan caps at 3; we use 2).
ENABLED_INBOXES = ["sba", "ceo"]

# Map internal actor -> AgentMail username. Some generic names (e.g. "ceo")
# are already taken globally on @agentmail.to, so we use unique handles.
ACTOR_USERNAMES = {"sba": "sba", "ceo": "tagsceo"}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.environ.get(
    "AGENTMAIL_CONFIG", os.path.join(BASE_DIR, "config", "agentmail.json")
)


def _load() -> dict[str, Any]:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _client(api_key: str) -> AgentMail:
    return AgentMail(api_key=api_key)


def get_api_key() -> Optional[str]:
    """Return a usable, verified org api_key.

    Resolution: env AGENTMAIL_API_KEY -> config/agentmail.json -> auto sign-up.
    """
    env_key = os.environ.get("AGENTMAIL_API_KEY")
    if env_key and env_key.strip():
        data = _load()
        if data.get("api_key") != env_key:
            data["api_key"] = env_key
            data["verified"] = True
            _save(data)
        return env_key.strip()
    data = _load()
    key = data.get("api_key")
    if key and data.get("verified"):
        return key
    return None


def set_api_key(key: str) -> None:
    """Persist a console-issued api_key (pre-verified)."""
    data = _load()
    data["api_key"] = key.strip()
    data["verified"] = True
    data.setdefault("inboxes", {})
    _save(data)
    print(f"Saved AgentMail api_key -> {key[:8]}... (verified)")


def get_inbox(actor: str, create: bool = True) -> Optional[dict[str, Any]]:
    """Return {inbox_id, address, username} for an actor, creating if missing.

    Only actors in ENABLED_INBOXES get a real inbox. Others fall back to the
    PRIMARY_SENDER (the CEO relays for them).
    """
    if actor not in ENABLED_INBOXES:
        actor = PRIMARY_SENDER
    data = _load()
    inboxes = data.get("inboxes", {})
    entry = inboxes.get(actor)
    if isinstance(entry, dict) and entry.get("inbox_id"):
        return entry
    if not create:
        return None

    api_key = get_api_key()
    if not api_key:
        logger.error("No AgentMail api_key; cannot create inbox for %r", actor)
        return None
    c = _client(api_key)
    label = ACTOR_USERNAMES.get(actor, actor).replace(" ", "_").lower()[:25]
    try:
        inbox = c.inboxes.create(request=CreateInboxRequest(
            username=label, client_id=f"tags-{label}"))
        entry = {
            "inbox_id": inbox.inbox_id,
            "address": inbox.email or inbox.address,
            "username": label,
        }
        inboxes[actor] = entry
        data["inboxes"] = inboxes
        _save(data)
        logger.info("AgentMail inbox created for %r -> %s", actor, entry["address"])
        return entry
    except Exception as e:
        logger.error("AgentMail inbox create failed for %r: %s", actor, e)
        return None


def delete_inbox(actor: str) -> bool:
    """Delete an actor's inbox (frees a free-plan slot)."""
    data = _load()
    inboxes = data.get("inboxes", {})
    entry = inboxes.get(actor)
    if not entry:
        return False
    api_key = get_api_key()
    if not api_key:
        return False
    c = _client(api_key)
    try:
        c.inboxes.delete(entry["inbox_id"])
        del inboxes[actor]
        data["inboxes"] = inboxes
        _save(data)
        logger.info("AgentMail inbox deleted for %r", actor)
        return True
    except Exception as e:
        logger.error("AgentMail inbox delete failed for %r: %s", actor, e)
        return False


def send(actor: str, to: str, subject: str, text: str, html: Optional[str] = None,
         reply_to: Optional[str] = None) -> bool:
    """Send an email. Inbox-less actors relay through the CEO's inbox."""
    inbox = get_inbox(actor, create=True)
    if not inbox:
        return False
    api_key = get_api_key()
    if not api_key:
        return False
    c = _client(api_key)
    try:
        c.inboxes.messages.send(
            inbox["inbox_id"], to=to, subject=subject, text=text,
            html=html, reply_to=reply_to,
        )
        return True
    except Exception as e:
        logger.error("AgentMail send failed (%s -> %s): %s", actor, to, e)
        return False


def list_messages(actor: str, limit: int = 10) -> list[Any]:
    inbox = get_inbox(actor, create=False)
    if not inbox:
        return []
    api_key = get_api_key()
    if not api_key:
        return []
    c = _client(api_key)
    try:
        resp = c.inboxes.messages.list(inbox["inbox_id"], limit=limit)
        return getattr(resp, "messages", []) or []
    except Exception as e:
        logger.error("AgentMail list failed (%s): %s", actor, e)
        return []


def wait_for_message(actor: str, from_filter: Optional[str] = None,
                     timeout: int = 180, poll: int = 10) -> Optional[Any]:
    """Poll an actor's inbox until a (matching) message arrives."""
    inbox = get_inbox(actor, create=False)
    if not inbox:
        return None
    api_key = get_api_key()
    if not api_key:
        return None
    c = _client(api_key)
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        try:
            resp = c.inboxes.messages.list(inbox["inbox_id"], limit=20)
            for m in (getattr(resp, "messages", []) or []):
                mid = getattr(m, "id", None)
                if mid in seen:
                    continue
                seen.add(mid)
                src = (getattr(m, "from_", "") or getattr(m, "from", "") or "").lower()
                if from_filter and from_filter.lower() not in src:
                    continue
                return m
        except Exception as e:
            logger.warning("AgentMail wait poll error: %s", e)
        time.sleep(poll)
    return None


def init_agentmail() -> dict[str, str]:
    """Ensure the two enabled inboxes exist; return {actor: address}."""
    out: dict[str, str] = {}
    for actor in ENABLED_INBOXES:
        inbox = get_inbox(actor, create=True)
        if inbox:
            out[actor] = inbox["address"]
    return out
