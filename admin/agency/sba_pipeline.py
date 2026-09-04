# admin/agency/sba_pipeline.py
"""Deterministic pipeline helpers for the SBA autopilot (migrated from worker).

Supabase REST access, reply classification, owner command parsing, and
email body templates. These are the cheap, deterministic pieces the 24/7
autopilot runs without LLM calls.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("sba.pipeline")


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip() or default
    if val:
        return val
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return default


def supabase_config() -> tuple[str, str] | None:
    """Gateway config: PocketBase first, Supabase names as legacy fallback."""
    url = _env("POCKETBASE_URL", _env("SUPABASE_URL", "http://localhost:8050"))
    key = _env("POCKETBASE_SERVICE_KEY", _env("SUPABASE_SERVICE_KEY", ""))
    if not key:
        logger.warning("POCKETBASE_SERVICE_KEY missing — pipeline disabled")
        return None
    return url, key


def sb_request(url: str, key: str, path: str, method: str = "GET", body: Any = None, timeout: int = 30) -> Any:
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{url}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        if not raw:
            return None
        return json.loads(raw)


def sb_patch_lead(url: str, key: str, sid: str, updates: dict[str, Any]) -> bool:
    """Patch a stored lead by id (LOCAL per-workspace store). No-op if missing."""
    if not sid:
        return False
    try:
        from admin.agency.sba_store import get_lead, update_lead

        if get_lead(sid) is None:
            return False
        update_lead(sid, updates)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("PATCH lead %s failed: %s", sid, exc)
        return False


def load_leads(url: str, key: str) -> list[dict[str, Any]]:
    """Leads for the autopilot — now served from the LOCAL store (sba_store),
    which is per-workspace and backed by local SQLite/Postgres. The autopilot
    no longer depends on PocketBase/Supabase REST for reads. Callers filter by
    workspace_name themselves.
    """
    try:
        from admin.agency.sba_store import list_leads

        return list_leads()
    except Exception as exc:  # noqa: BLE001
        logger.warning("load leads (local store) failed: %s", exc)
        return []


# Supabase lead status -> local pipeline status (frontend kanban stages).
_STATUS_MAP = {
    "candidate": "new",
    "good": "new",
    "contacted": "contacted",
    "meeting": "meeting",
    "proposal": "proposal",
    "negotiation": "negotiation",
    "closed": "closed",
    "lost": "lost",
}


def _normalize_supabase_lead(l: dict[str, Any]) -> dict[str, Any]:
    """Shape a Supabase lead like sba_store leads so the API/frontend can render it."""
    name = (l.get("name") or "").strip()
    return {
        "id": str(l.get("id") or ""),
        "name": name,
        "business_name": name,
        "email": (l.get("email") or "").strip(),
        "phone": (l.get("phone") or "").strip(),
        "score": 80 if (l.get("status") or "") == "good" else 50,
        "source": (l.get("category") or l.get("workspace_name") or "supabase"),
        "status": _STATUS_MAP.get((l.get("status") or "candidate").lower(), "new"),
        "meeting_ids": [],
        "context": {
            "estimated_value": 0,
            "city_state": l.get("city_state") or "",
            "href": l.get("href") or "",
            "category": l.get("category") or "",
        },
        "created_at": l.get("created_at") or "",
    }


def load_leads_preferred() -> list[dict[str, Any]]:
    """Leads from Supabase (autopilot's live store) when configured, else local store.

    The autopilot writes found leads to Supabase; the API previously read only
    the local SQLite store, so the dashboard showed zero leads while Supabase
    had hundreds. Prefer Supabase so the frontend sees the real pipeline.
    """
    cfg = supabase_config()
    if cfg:
        try:
            supa = load_leads(cfg[0], cfg[1])
            if supa:
                return [_normalize_supabase_lead(l) for l in supa]
        except Exception as exc:  # noqa: BLE001
            logger.warning("supabase lead load failed, falling back to local: %s", exc)
    from admin.agency.sba_store import list_leads

    return list_leads()


def _store_lead_from_autopilot(lead: dict[str, Any]) -> dict | None:
    """Map an autopilot candidate row -> sba_store.create_lead (local, per-ws)."""
    from admin.agency.sba_store import create_lead

    raw = lead.get("raw") or {}
    ctx = {
        "city_state": lead.get("city_state") or "",
        "href": lead.get("href") or "",
        "category": lead.get("category") or "",
        "website": lead.get("website") or "",
        "has_website": lead.get("has_website", False),
        "website_status": lead.get("website_status") or "",
        "lead_score": raw.get("lead_score"),
        "lead_reason": raw.get("lead_reason"),
        "lead_action": raw.get("lead_action"),
        "mode": lead.get("mode") or "",
        "source_platforms": lead.get("sources") or [lead.get("source")],
        "workspace_name": lead.get("workspace_name") or "agency",
        "text": lead.get("text") or "",
    }
    return create_lead({
        "name": lead.get("name") or "",
        "business_name": lead.get("name") or "",
        "email": lead.get("email") or "",
        "phone": lead.get("phone") or "",
        "source": lead.get("source") or lead.get("category") or "sba",
        "score": int(raw.get("lead_score") or 50),
        "status": (lead.get("status") or "new"),
        "context": ctx,
    })


def save_lead(url: str, key: str, lead: dict[str, Any]) -> dict | None:
    """Persist an autopilot candidate lead to the LOCAL per-workspace store."""
    try:
        return _store_lead_from_autopilot(lead)
    except Exception as exc:  # noqa: BLE001
        logger.warning("save lead (local store) failed: %s", exc)
        return None


# ── Reply classification ─────────────────────────────────────────────────

INTERESTED_WORDS = ["yes", "interested", "sure", "sounds good", "let's talk", "lets talk",
                    "call me", "available", "book", "schedule", "price", "pricing", "how much",
                    "quote", "more info", "definitely", "yeah", "yep", "ok", "okay", "would love",
                    "haan", "hain", "ji haan", "theek hai", "confirm"]
NOT_INTERESTED_WORDS = ["no", "not interested", "stop", "don't", "dont", "unsubscribe",
                        "leave me alone", "not right now", "no thanks", "no thank you",
                        "nahi", "nahin", "nhi", "not now"]


def _has_phrase(text: str, phrase: str) -> bool:
    """True if phrase appears as a standalone word/word-group in text."""
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def classify_reply(text: str) -> str:
    t = (text or "").lower()
    if any(_has_phrase(t, w) for w in NOT_INTERESTED_WORDS):
        return "no"
    if any(_has_phrase(t, w) for w in INTERESTED_WORDS):
        return "yes"
    return "maybe"


def is_owner(from_addr: str) -> bool:
    owner = _env("SBA_OWNER_EMAIL", "").lower()
    return bool(owner) and owner in (from_addr or "").lower()


def _parse_time(txt: str) -> str:
    """Extract a meeting time from owner reply text. Returns 'HH:MM' or ''."""
    low = (txt or "").lower()
    tm = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", low)
    if tm:
        return f"{int(tm.group(1)):02d}:{tm.group(2)}"
    m = re.search(r"\b([1-9]|1[0-2])\s*(?:baje|o'?clock|pm|am)\b", low)
    if m:
        h = int(m.group(1))
        if "am" in low[max(0, m.start() - 2):m.start() + 8]:
            return f"{h:02d}:00"
        return f"{(h + 12) if h < 12 else h:02d}:00"
    if "shaam" in low or "evening" in low:
        return "18:00"
    if "subah" in low or "morning" in low:
        return "09:00"
    return ""


def parse_owner_command(subject: str, body: str) -> dict[str, Any]:
    txt = (subject or "") + " " + (body or "")
    low = txt.lower()
    m = re.search(r"lead[:_\-]?\s*(\d+)", txt)
    sid = m.group(1) if m else ""
    time = _parse_time(txt)
    if any(w in low for w in ["nahi", "no", "not now", "not interested", "mat karo"]):
        return {"lead_id": sid, "action": "nahi", "time": ""}
    if any(w in low for w in ["haan", "yes", "confirm", "kar do", "karo", "theek hai", "ok", "okay"]):
        return {"lead_id": sid, "action": "haan", "time": time}
    return {"lead_id": sid, "action": "unknown", "time": ""}


def tomorrow() -> str:
    return (dt.date.today() + dt.timedelta(days=1)).isoformat()


# ── Email body templates ─────────────────────────────────────────────────

def owner_notification_body(lead: dict[str, Any], reply: str) -> str:
    name = lead.get("name") or "Unknown"
    cat = lead.get("category") or "business"
    return (
        f"Boss! {name} ({cat}) ne reply kiya hai!\n\n"
        f'Unhone kaha: "{reply[:300]}"\n\n'
        "👉 Kya main meeting confirm karun?\n"
        'Reply: "Haan" -> Meeting setup (default kal 11:00 AM)\n'
        'Reply: "Haan 3 baje" -> Meeting 3:00 PM\n'
        'Reply: "Nahi" -> Lead ko polite "not right now" email\n\n'
        "SBA autopilot inbox se tumhara reply automatically padh lega."
    )


def meeting_confirm_body(lead: dict[str, Any], date: str, time: str) -> str:
    name = lead.get("name") or "there"
    return (
        f"Hi {name},\n\n"
        "Perfect! Our team is looking forward to connecting with you.\n\n"
        f"Meeting Details:\nDate: {date}\nTime: {time}\nDuration: 30 minutes\n\n"
        "Please let me know if this time still works for you.\n\n"
        "Best,\nAyan\nTAGS Agency"
    )


def rejected_body(lead: dict[str, Any]) -> str:
    name = lead.get("name") or "there"
    return (
        f"Hi {name},\n\n"
        "No worries at all! Totally understand if now isn't the right time.\n\n"
        "If your situation changes or you'd like to revisit this in the future, "
        "feel free to reach out anytime.\n\nWishing you all the best!\n\nAyan\nTAGS Agency"
    )


def meeting_link_body(lead: dict[str, Any], date: str, time: str, link: str) -> str:
    name = lead.get("name") or "there"
    return (
        f"Hi {name},\n\n"
        "Great news! Your meeting is confirmed.\n\n"
        f"Meeting Details:\nDate: {date}\nTime: {time}\nDuration: 30 minutes\n\n"
        f"Join here: {link}\n\n"
        "See you there!\n\nBest,\nAyan\nTAGS Agency"
    )
