"""SBA data store — in-memory + PostgreSQL dual persistence.

Reads from in-memory for speed, writes to both.
On startup, loads from DB into memory.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.database import AsyncSessionLocal, engine
from admin.models import HandoffModel, LeadModel, MeetingModel

logger = logging.getLogger(__name__)

# ── Fallback in-memory stores (always available) ─────────────────────────
_leads: dict[str, dict[str, Any]] = {}
_meetings: dict[str, dict[str, Any]] = {}
_handoffs: dict[str, dict[str, Any]] = {}

# ── Statuses ─────────────────────────────────────────────────────────────
LEAD_STATUSES = ["new", "contacted", "meeting", "proposal", "negotiation", "closed", "lost"]
MEETING_STATUSES = ["scheduled", "done", "cancelled"]


# ── Helpers ──────────────────────────────────────────────────────────────

def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_session() -> AsyncSession | None:
    """Get a DB session if available."""
    if AsyncSessionLocal is None:
        return None
    try:
        return AsyncSessionLocal()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# LEADS
# ═══════════════════════════════════════════════════════════════════════════


async def _load_leads_from_db() -> None:
    """On startup, load all leads from DB into memory."""
    session = await _get_session()
    if not session:
        return
    try:
        result = await session.execute(select(LeadModel))
        for row in result.scalars():
            _leads[row.id] = row.to_dict()
    except Exception:
        pass
    finally:
        await session.close()


async def create_lead(data: dict[str, Any]) -> dict[str, Any]:
    lid = _new_id()
    now = _now_str()
    lead = {
        "id": lid,
        "name": data.get("name", ""),
        "business_name": data.get("business_name", ""),
        "email": data.get("email", ""),
        "phone": data.get("phone", ""),
        "source": data.get("source", "manual"),
        "score": data.get("score", 50),
        "status": data.get("status", "new"),
        "notes": data.get("notes", []),
        "meeting_ids": [],
        "created_at": now,
        "updated_at": now,
        "context": data.get("context", {}),
    }
    _leads[lid] = lead

    # Async persist to DB
    session = await _get_session()
    if session:
        try:
            model = LeadModel(**lead)
            session.add(model)
            await session.commit()
        except Exception:
            await session.rollback()
        finally:
            await session.close()

    return lead


def get_lead(lid: str) -> dict[str, Any] | None:
    return _leads.get(lid)


def list_leads(status: str | None = None) -> list[dict[str, Any]]:
    leads = list(_leads.values())
    if status:
        leads = [l for l in leads if l["status"] == status]
    return sorted(leads, key=lambda l: l["updated_at"], reverse=True)


async def update_lead(lid: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    lead = _leads.get(lid)
    if not lead:
        return None
    now = _now_str()

    # Merge context instead of replacing
    if "context" in updates and isinstance(updates["context"], dict):
        existing_context = lead.get("context", {})
        existing_context.update(updates["context"])
        updates["context"] = existing_context

    # Merge notes instead of replacing
    if "notes" in updates and isinstance(updates["notes"], list):
        existing_notes = lead.get("notes", [])
        existing_notes.extend(updates["notes"])
        updates["notes"] = existing_notes

    lead.update(updates)
    lead["updated_at"] = now

    session = await _get_session()
    if session:
        try:
            result = await session.execute(select(LeadModel).where(LeadModel.id == lid))
            model = result.scalar_one_or_none()
            if model:
                for k, v in updates.items():
                    setattr(model, k, v)
                model.updated_at = now
                await session.commit()
        except Exception:
            await session.rollback()
        finally:
            await session.close()

    return lead


async def delete_lead(lid: str) -> bool:
    existed = _leads.pop(lid, None) is not None
    session = await _get_session()
    if session:
        try:
            result = await session.execute(select(LeadModel).where(LeadModel.id == lid))
            model = result.scalar_one_or_none()
            if model:
                await session.delete(model)
                await session.commit()
        except Exception:
            await session.rollback()
        finally:
            await session.close()
    return existed


# ═══════════════════════════════════════════════════════════════════════════
# MEETINGS
# ═══════════════════════════════════════════════════════════════════════════


async def create_meeting(data: dict[str, Any]) -> dict[str, Any]:
    mid = _new_id()
    now = _now_str()
    meeting = {
        "id": mid,
        "lead_id": data.get("lead_id", ""),
        "title": data.get("title", "Meeting"),
        "lead_name": data.get("lead_name", ""),
        "purpose": data.get("purpose", ""),
        "link": data.get("link", ""),
        "date": data.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "time": data.get("time", datetime.now(timezone.utc).strftime("%H:%M")),
        "duration_minutes": data.get("duration_minutes", 30),
        "status": data.get("status", "scheduled"),
        "notes": data.get("notes", []),
        "transcript": data.get("transcript", ""),
        "transcript_analysis": data.get("transcript_analysis", None),
        "action_items": data.get("action_items", []),
        "summary": data.get("summary", ""),
        "lead_response": data.get("lead_response", ""),
        "created_at": now,
        "updated_at": now,
    }
    _meetings[mid] = meeting

    # Link to lead
    lead = _leads.get(meeting["lead_id"])
    if lead and mid not in lead["meeting_ids"]:
        lead["meeting_ids"].append(mid)

    session = await _get_session()
    if session:
        try:
            model = MeetingModel(**meeting)
            session.add(model)
            await session.commit()
        except Exception:
            await session.rollback()
        finally:
            await session.close()

    return meeting


def get_meeting(mid: str) -> dict[str, Any] | None:
    return _meetings.get(mid)


def list_meetings(lead_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    meetings = list(_meetings.values())
    if lead_id:
        meetings = [m for m in meetings if m["lead_id"] == lead_id]
    if status:
        meetings = [m for m in meetings if m["status"] == status]
    return sorted(meetings, key=lambda m: m["date"] + " " + m["time"], reverse=True)


async def update_meeting(mid: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    meeting = _meetings.get(mid)
    if not meeting:
        return None
    now = _now_str()
    meeting.update(updates)
    meeting["updated_at"] = now

    session = await _get_session()
    if session:
        try:
            result = await session.execute(select(MeetingModel).where(MeetingModel.id == mid))
            model = result.scalar_one_or_none()
            if model:
                for k, v in updates.items():
                    setattr(model, k, v)
                model.updated_at = now
                await session.commit()
        except Exception:
            await session.rollback()
        finally:
            await session.close()

    return meeting


async def add_meeting_note(
    mid: str,
    text: str,
    language: str = "en",
    speaker: str = "lead",
) -> dict[str, Any] | None:
    meeting = _meetings.get(mid)
    if not meeting:
        return None
    note = {
        "text": text,
        "language": language,
        "speaker": speaker,
        "timestamp": _now_str(),
    }
    meeting["notes"].append(note)
    meeting["updated_at"] = _now_str()

    session = await _get_session()
    if session:
        try:
            result = await session.execute(select(MeetingModel).where(MeetingModel.id == mid))
            model = result.scalar_one_or_none()
            if model:
                model.notes = meeting["notes"]
                model.updated_at = meeting["updated_at"]
                await session.commit()
        except Exception:
            await session.rollback()
        finally:
            await session.close()

    return meeting


def backup_meeting(mid: str) -> dict[str, Any] | None:
    """Structured backup of one meeting — karan, notes, transcript, analysis."""
    meeting = _meetings.get(mid)
    if not meeting:
        return None
    lead = _leads.get(meeting.get("lead_id", ""))
    return {
        "backup_type": "meeting",
        "backup_time": _now_str(),
        "meeting": meeting,
        "lead": lead,
        "summary": {
            "id": meeting["id"],
            "title": meeting["title"],
            "lead_name": meeting["lead_name"],
            "purpose": meeting.get("purpose", ""),
            "date": meeting["date"],
            "time": meeting["time"],
            "status": meeting["status"],
            "note_count": len(meeting.get("notes", [])),
            "action_items": len(meeting.get("action_items", [])),
        },
    }


def backup_all() -> dict[str, Any]:
    """Full system backup — leads, meetings, handoffs (sab kuch ek JSON me)."""
    return {
        "backup_type": "full_sba",
        "backup_time": _now_str(),
        "leads": list(_leads.values()),
        "meetings": list(_meetings.values()),
        "handoffs": list(_handoffs.values()),
        "counts": {
            "leads": len(_leads),
            "meetings": len(_meetings),
            "handoffs": len(_handoffs),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# HANDOFFS
# ═══════════════════════════════════════════════════════════════════════════


async def create_handoff(lead_id: str, ceo_message: str = "") -> dict[str, Any]:
    lead = _leads.get(lead_id)
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    meeting_notes_full = []
    for mid in lead.get("meeting_ids", []):
        m = _meetings.get(mid)
        if m:
            meeting_notes_full.append({
                "meeting_id": mid,
                "title": m["title"],
                "date": m["date"],
                "notes": m["notes"],
                "summary": m["summary"],
                "transcript": m.get("transcript", ""),
                "transcript_analysis": m.get("transcript_analysis", None),
                "action_items": m["action_items"],
                "lead_response": m["lead_response"],
            })

    handoff = {
        "id": _new_id(),
        "lead_id": lead_id,
        "handed_off_to_ceo": True,
        "handoff_time": _now_str(),
        "workspace_id": None,
        "brief": {
            "lead_name": lead["name"],
            "business_name": lead["business_name"],
            "email": lead["email"],
            "phone": lead["phone"],
            "score": lead["score"],
            "source": lead["source"],
            "industry": lead.get("context", {}).get("industry", "unknown"),
            "key_signals": lead.get("context", {}).get("key_signals", ""),
            "client_needs": lead.get("context", {}).get("needs", []),
            "agreed_scope": lead.get("context", {}).get("scope", ""),
            "next_steps": lead.get("context", {}).get("next_steps", []),
        },
        "full_dump": {
            "lead": lead,
            "meetings": meeting_notes_full,
            "all_notes": [n for m in meeting_notes_full for n in m.get("notes", [])],
            "action_items": [ai for m in meeting_notes_full for ai in m.get("action_items", [])],
            "lead_response_history": [m["lead_response"] for m in meeting_notes_full if m.get("lead_response")],
        },
        "ceo_message": ceo_message,
    }

    _handoffs[handoff["id"]] = handoff

    # Mark lead as closed
    lead["status"] = "closed"
    lead["updated_at"] = _now_str()

    session = await _get_session()
    if session:
        try:
            model = HandoffModel(**handoff)
            session.add(model)
            # Also update lead status
            lresult = await session.execute(select(LeadModel).where(LeadModel.id == lead_id))
            lmodel = lresult.scalar_one_or_none()
            if lmodel:
                lmodel.status = "closed"
                lmodel.updated_at = lead["updated_at"]
            await session.commit()
        except Exception:
            await session.rollback()
        finally:
            await session.close()

    return handoff


def get_handoff(hid: str) -> dict[str, Any] | None:
    return _handoffs.get(hid)


def list_handoffs(lead_id: str | None = None) -> list[dict[str, Any]]:
    handoffs = list(_handoffs.values())
    if lead_id:
        handoffs = [h for h in handoffs if h["lead_id"] == lead_id]
    return sorted(handoffs, key=lambda h: h["handoff_time"], reverse=True)


async def mark_handoff_workspace_created(hid: str, workspace_id: str) -> bool:
    handoff = _handoffs.get(hid)
    if not handoff:
        return False
    handoff["workspace_id"] = workspace_id

    session = await _get_session()
    if session:
        try:
            result = await session.execute(select(HandoffModel).where(HandoffModel.id == hid))
            model = result.scalar_one_or_none()
            if model:
                model.workspace_id = workspace_id
                await session.commit()
        except Exception:
            await session.rollback()
        finally:
            await session.close()

    return True


# ═══════════════════════════════════════════════════════════════════════════
# INIT: load from DB into memory on import
# ═══════════════════════════════════════════════════════════════════════════

async def load_all_from_db() -> None:
    """Load all persisted data into memory. Call once on startup."""
    session = await _get_session()
    if not session:
        return
    try:
        # Leads
        result = await session.execute(select(LeadModel))
        for row in result.scalars():
            _leads[row.id] = row.to_dict()

        # Meetings
        result = await session.execute(select(MeetingModel))
        for row in result.scalars():
            _meetings[row.id] = row.to_dict()

        # Handoffs
        result = await session.execute(select(HandoffModel))
        for row in result.scalars():
            _handoffs[row.id] = row.to_dict()

        logger.info("Loaded %d leads, %d meetings, %d handoffs from DB",
                     len(_leads), len(_meetings), len(_handoffs))
    except Exception:
        pass
    finally:
        await session.close()
