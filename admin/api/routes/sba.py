"""Agency-level SBA (Sales/Business Agent) — full lead-to-client pipeline.

SBA handles:
  - Lead discovery & management (Chrome browser for prospecting)
  - Meeting scheduling & notes (any language)
  - Pipeline tracking
  - SBA → CEO handoff when lead converts
  - Finance/pipeline reporting
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from admin.agency.sba import SBAAgent
from admin.agency.sba_skills import build_skill_context, detect_skills, list_sba_skills
from admin.agency.sba_store import (
    add_meeting_note,
    backup_all,
    backup_meeting,
    create_handoff,
    create_lead,
    create_meeting,
    delete_lead,
    get_handoff,
    get_lead,
    get_meeting,
    list_handoffs,
    list_leads,
    list_meetings,
    load_all_from_db,
    mark_handoff_workspace_created,
    update_lead,
    update_meeting,
)
from admin.agency.sba_pipeline import load_leads_preferred as _load_leads_preferred
from admin.api.models.schemas import ChatResponse
import openai

logger = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    """JSON serializer for non-standard types (mirrors agent_bus._json_default)."""
    if isinstance(obj, (dict, list, str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


router = APIRouter(prefix="/api/sba", tags=["sba"])
_sba: SBAAgent | None = None

# ── Schemas ─────────────────────────────────────────────────────────────────


class LeadCreate(BaseModel):
    name: str
    business_name: str = ""
    email: str = ""
    phone: str = ""
    source: str = "manual"
    score: int = 50
    notes: list = []
    context: dict = {}


class LeadUpdate(BaseModel):
    name: str | None = None
    business_name: str | None = None
    email: str | None = None
    phone: str | None = None
    score: int | None = None
    status: str | None = None
    notes: list | None = None
    context: dict | None = None


class MeetingCreate(BaseModel):
    lead_id: str
    title: str = "Meeting"
    lead_name: str = ""
    purpose: str = ""  # Meeting ka karan / agenda
    date: str = ""
    time: str = ""
    duration_minutes: int = 30


class MeetingUpdate(BaseModel):
    title: str | None = None
    purpose: str | None = None  # Meeting ka karan / agenda
    date: str | None = None
    time: str | None = None
    duration_minutes: int | None = None
    status: str | None = None
    summary: str | None = None
    action_items: list | None = None
    lead_response: str | None = None  # "haan", "nahi", "maybe"


class MeetingNoteAdd(BaseModel):
    text: str
    language: str = "en"
    speaker: str = "lead"  # lead, me (SBA/Ayan)


class HandoffRequest(BaseModel):
    lead_id: str
    ceo_message: str = ""


class HandoffWorkspaceCreated(BaseModel):
    workspace_id: str


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_agency_sba() -> SBAAgent:
    global _sba
    if _sba is None:
        _sba = SBAAgent(
            workspace_name="TAGS Agency",
            client_name="TAGS Agency (Internal)",
        )
    return _sba


# ── Mirror lead to PocketBase + JSON files ────────────────────────────────

def _mirror_lead(record, delete: bool = False) -> None:
    """Best-effort mirror of a lead (LeadModel or dict) to data/store + PocketBase.

    Collection: "sba_leads", keyed on "record_id" (the lead's primary id,
    stringified). Payload is flattened to JSON-safe strings only (datetimes
    become ISO strings, nested dict/list are json.dumps'd). The file write is
    attempted first, then PocketBase.

    Fully wrapped in try/except and NEVER raises, so a mirror failure can
    never break the main lead DB write (CREATE/UPDATE/DELETE).
    """
    # Extract a plain dict from either a LeadModel or a plain dict.
    try:
        if hasattr(record, "to_dict"):
            rec = record.to_dict()
        elif isinstance(record, dict):
            rec = record
        else:
            rec = {}
        record_id = str(rec.get("id", "") or "")
        if not record_id:
            logger.debug("mirror_lead skipped: missing lead id")
            return
    except Exception as exc:  # noqa: BLE001
        logger.debug("mirror_lead id extract failed (non-fatal): %s", exc)
        return

    if delete:
        try:
            from admin.file_store import delete_record as _fs_del
            _fs_del("sba_leads", record_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("file delete (sba_leads) failed (non-fatal): %s", exc)
        try:
            from admin.pocketbase_client import get_pb_client
            pb = get_pb_client()
            if pb and pb.is_configured():
                pb.delete_by_key("sba_leads", "record_id", record_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("PocketBase delete (sba_leads) failed (non-fatal): %s", exc)
        return

    # Build a flat, strings-only payload.
    try:
        payload: dict = {}
        for key in (
            "id", "name", "business_name", "email", "phone", "source",
            "score", "status", "notes", "meeting_ids", "context",
            "created_at", "updated_at",
        ):
            val = rec.get(key, "")
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif isinstance(val, (dict, list)):
                val = json.dumps(val, default=_json_default)
            elif val is None:
                val = ""
            else:
                val = str(val)
            payload[key] = val
        # Keep our id under "record_id" (PB ids are 15-char only).
        payload["record_id"] = payload.pop("id", "")
        payload.pop("id", None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("sba_leads mirror payload build failed (non-fatal): %s", exc)
        return

    try:
        from admin.file_store import save_record as _fs_save
        _fs_save("sba_leads", record_id, payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("file mirror (sba_leads) failed (non-fatal): %s", exc)
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        pb.upsert_by_key("sba_leads", "record_id", payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase mirror (sba_leads) failed (non-fatal): %s", exc)


# ── Status ──────────────────────────────────────────────────────────────────


@router.get("/status")
async def sba_status():
    """SBA agent status + pipeline summary."""
    all_leads = _load_leads_preferred()
    pipeline = {}
    for s in ["new", "contacted", "meeting", "proposal", "negotiation", "closed", "lost"]:
        pipeline[s] = [l for l in all_leads if l["status"] == s]

    return {
        "success": True,
        "sba": {
            "status": "running",
            "brain": "multi-phase + Chrome tool-calling",
        },
        "pipeline_summary": {
            "total_leads": len(all_leads),
            "by_status": {s: len(pipeline[s]) for s in pipeline},
            "hot_leads": len([l for l in all_leads if l["score"] >= 80 and l["status"] != "closed"]),
        },
    }


@router.get("/autopilot/status")
async def sba_autopilot_status():
    from admin.agency.sba_autopilot import SBAAutopilot
    return {"status": SBAAutopilot().status()}


@router.get("/reasoning")
async def sba_reasoning(limit: int = 25, event: str = ""):
    """Show the agent's reasoning journal — why it scored, skipped,
    rejected, or emailed each lead. `event` filters to one decision type.
    """
    from admin.agency import sba_reason as reason
    return {"events": reason.recent_decisions(limit=limit, event=event or None)}


@router.get("/strategy")
async def sba_strategy():
    """The agent's current strategy: angle, focus targets, actions, history."""
    from admin.agency import sba_strategy as strat
    return {"success": True, "data": strat.active_strategy()}


@router.get("/dashboard", response_class=HTMLResponse)
async def sba_dashboard():
    """Self-contained live view: pipeline, agent reasoning, and strategy."""
    return HTMLResponse(content=_SBA_DASHBOARD_PAGE)


# ── Chat ────────────────────────────────────────────────────────────────────


@router.post("/chat", response_model=ChatResponse)
async def sba_chat(body: dict):
    """Chat with SBA for lead generation, sales strategy, etc.

    Skills from Jcode's catalog are auto-detected from the message
    and injected as context so SBA can use them for better results.
    Pass `skip_skills: true` to disable auto-detection.
    """
    message = body.get("message", "")
    if not message:
        raise HTTPException(400, "Message is required")

    # Auto-detect relevant skills from Jcode catalog
    skill_context_auto = ""
    if not body.get("skip_skills", False):
        matched_skills = detect_skills(message)
        if matched_skills:
            skill_context_auto = build_skill_context(matched_skills)
            logger.info(
                "SBA skills detected: %s",
                [s["name"] for s in matched_skills],
            )

    # Merge with explicit skill_context if provided
    skill_context = body.get("skill_context", "")
    if skill_context_auto and skill_context:
        skill_context = skill_context_auto + "\n\n" + skill_context
    elif skill_context_auto:
        skill_context = skill_context_auto

    if skill_context:
        message = f"{skill_context}\n\n{message}"

    agent = _get_agency_sba()
    response, phases = await agent.chat(message=message)
    return ChatResponse(
        response=response,
        conversation_id=body.get("session_id", "sba"),
        agent_type="sba",
        thinking_phases=phases,
    )


@router.get("/skills")
async def api_list_sba_skills():
    """List all skills available to SBA agent."""
    return {"success": True, "data": {"skills": list_sba_skills()}}


# ── Leads CRUD ──────────────────────────────────────────────────────────────


@router.get("/leads")
async def api_list_leads(status: str | None = None):
    """List all leads, optionally filtered by status."""
    all_leads = _load_leads_preferred()
    if status:
        all_leads = [l for l in all_leads if l["status"] == status]
    return {"success": True, "data": {"leads": all_leads}}


@router.post("/leads")
async def api_create_lead(payload: LeadCreate):
    """Create a new lead."""
    lead = await create_lead(payload.model_dump())
    _mirror_lead(lead)
    return {"success": True, "data": {"lead": lead}}


@router.get("/leads/{lead_id}")
async def api_get_lead(lead_id: str):
    lead = get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return {"success": True, "data": {"lead": lead}}


@router.patch("/leads/{lead_id}")
async def api_update_lead(lead_id: str, payload: LeadUpdate):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    lead = await update_lead(lead_id, updates)
    if not lead:
        raise HTTPException(404, "Lead not found")
    _mirror_lead(lead)
    return {"success": True, "data": {"lead": lead}}


@router.delete("/leads/{lead_id}")
async def api_delete_lead(lead_id: str):
    if not await delete_lead(lead_id):
        raise HTTPException(404, "Lead not found")
    _mirror_lead({"id": lead_id}, delete=True)
    return {"success": True}


# ── Pipeline ────────────────────────────────────────────────────────────────


@router.get("/pipeline")
async def api_pipeline():
    """Full pipeline data — kanban format for frontend."""
    all_leads = _load_leads_preferred()
    stages_order = ["new", "contacted", "meeting", "proposal", "negotiation", "closed"]

    pipeline = {}
    for stage in stages_order:
        pipeline[stage] = [
            {
                "id": l["id"],
                "name": l["name"],
                "business_name": l["business_name"],
                "email": l["email"],
                "score": l["score"],
                "source": l["source"],
                "meeting_count": len(l.get("meeting_ids", [])),
            }
            for l in all_leads if l["status"] == stage
        ]

    # Pipeline value estimate
    pipeline_value = sum(
        l.get("context", {}).get("estimated_value", 0)
        for l in all_leads
        if l["status"] in ("meeting", "proposal", "negotiation")
    )

    return {
        "success": True,
        "data": {
            "stages": stages_order,
            "pipeline": pipeline,
            "pipeline_value": pipeline_value,
        },
    }


# ── Meetings ────────────────────────────────────────────────────────────────


@router.get("/meetings")
async def api_list_meetings(lead_id: str | None = None, status: str | None = None):
    """List meetings, optionally filtered by lead or status."""
    return {"success": True, "data": {"meetings": list_meetings(lead_id, status)}}


@router.post("/meetings")
async def api_create_meeting(payload: MeetingCreate):
    """Schedule a new meeting with a lead."""
    meeting = await create_meeting(payload.model_dump())
    return {"success": True, "data": {"meeting": meeting}}


@router.get("/meetings/{meeting_id}")
async def api_get_meeting(meeting_id: str):
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    return {"success": True, "data": {"meeting": meeting}}


@router.patch("/meetings/{meeting_id}")
async def api_update_meeting(meeting_id: str, payload: MeetingUpdate):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    meeting = await update_meeting(meeting_id, updates)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    return {"success": True, "data": {"meeting": meeting}}


@router.post("/meetings/{meeting_id}/notes")
async def api_add_meeting_note(meeting_id: str, payload: MeetingNoteAdd):
    """Add a note to a meeting. Supports ANY language (en, hi, hinglish, etc.)."""
    meeting = await add_meeting_note(
        meeting_id,
        text=payload.text,
        language=payload.language,
        speaker=payload.speaker,
    )
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    return {"success": True, "data": {"meeting": meeting}}


# ── Backup (meeting notes + karan + transcript export) ──────────────────────


@router.get("/meetings/{meeting_id}/backup")
async def api_backup_meeting(meeting_id: str):
    """Meeting ka full backup — karan, notes, transcript, analysis, action items.

    Returns structured JSON jo download kar ke save kar sakte ho.
    """
    backup = backup_meeting(meeting_id)
    if not backup:
        raise HTTPException(404, "Meeting not found")
    return {"success": True, "data": backup}


@router.get("/backup")
async def api_backup_all():
    """Full SBA backup — saare leads, meetings, handoffs ek JSON me."""
    return {"success": True, "data": backup_all()}


# ── Handoff (SBA → CEO) ────────────────────────────────────────────────────


@router.post("/leads/{lead_id}/handoff")
async def api_handoff_lead(lead_id: str, payload: HandoffRequest | None = None):
    """SBA → CEO handoff. When lead says 'haan', SBA hands off with full context.

    Creates a structured brief + full data dump (per Q17 interview).
    CEO can then create a workspace for this client.
    """
    ceo_msg = payload.ceo_message if payload else ""
    try:
        handoff = await create_handoff(lead_id, ceo_message=ceo_msg)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {
        "success": True,
        "data": {
            "handoff": handoff,
            "message": (
                f"✅ Lead '{handoff['brief']['lead_name']}' handed off to CEO! "
                f"Sari meetings, notes, aur context CEO ke paas hai. "
                f"CEO ab workspace create karega."
            ),
        },
    }


@router.get("/handoffs")
async def api_list_handoffs(lead_id: str | None = None):
    """List all handoffs (SBA → CEO)."""
    return {"success": True, "data": {"handoffs": list_handoffs(lead_id)}}


@router.get("/handoffs/{handoff_id}")
async def api_get_handoff(handoff_id: str):
    handoff = get_handoff(handoff_id)
    if not handoff:
        raise HTTPException(404, "Handoff not found")
    return {"success": True, "data": {"handoff": handoff}}


# ── Workspace creation from handoff ─────────────────────────────────────────


@router.post("/handoffs/{handoff_id}/create-workspace")
async def api_create_workspace_from_handoff(
    handoff_id: str,
    payload: HandoffWorkspaceCreated | None = None,
):
    """CEO ne handoff ke liye workspace create kar diya."""
    ws_id = payload.workspace_id if payload else None
    if not ws_id:
        raise HTTPException(400, "workspace_id is required")
    if not await mark_handoff_workspace_created(handoff_id, ws_id):
        raise HTTPException(404, "Handoff not found")
    return {"success": True, "message": f"Workspace {ws_id} linked to handoff."}


# ── Finance ─────────────────────────────────────────────────────────────────


@router.get("/finance")
async def api_finance():
    """Finance overview — deals, revenue forecast."""
    all_leads = list_leads()
    closed_deals = [l for l in all_leads if l["status"] == "closed"]
    active_pipeline = [
        l for l in all_leads
        if l["status"] in ("meeting", "proposal", "negotiation")
    ]

    pipeline_value = sum(
        l.get("context", {}).get("estimated_value", 0)
        for l in active_pipeline
    )

    deals = [
        {
            "id": l["id"],
            "lead_name": l["name"],
            "business_name": l["business_name"],
            "amount": l.get("context", {}).get("deal_value", 0),
            "date": l["updated_at"],
        }
        for l in closed_deals
    ]

    return {
        "success": True,
        "data": {
            "pipeline_value": pipeline_value,
            "total_deals": len(closed_deals),
            "active_pipeline_count": len(active_pipeline),
            "deals": deals,
            "revenue_forecast": [
                {"month": "Month 1", "projected": round(pipeline_value * 0.3), "confidence": 70},
                {"month": "Month 2", "projected": round(pipeline_value * 0.5), "confidence": 60},
                {"month": "Month 3", "projected": round(pipeline_value * 0.7), "confidence": 50},
            ],
        },
    }


# ── Lead qualify endpoint ──────────────────────────────────────────────────


@router.post("/leads/qualify")
async def api_qualify_lead(payload: dict):
    """Qualify a lead using SBA's thinking + optionally Chrome research."""
    lead_data = payload.get("lead", {})
    if not lead_data.get("name"):
        raise HTTPException(400, "Lead name is required")

    # Use SBA to think about this lead
    agent = _get_agency_sba()
    prompt = (
        f"Ek naya lead aaya hai:\n"
        f"Name: {lead_data.get('name', 'Unknown')}\n"
        f"Business: {lead_data.get('business_name', 'N/A')}\n"
        f"Email: {lead_data.get('email', 'N/A')}\n"
        f"Source: {lead_data.get('source', 'manual')}\n"
        f"\nIs lead ko qualify karo. Score do 0-100. "
        f"Batao ye lead kitna promising hai, kya red flags hain, "
        f"aur next step kya hona chahiye."
    )

    response, phases = await agent.chat(message=prompt)

    # Create lead in store
    lead = await create_lead({
        "name": lead_data.get("name", ""),
        "business_name": lead_data.get("business_name", ""),
        "email": lead_data.get("email", ""),
        "phone": lead_data.get("phone", ""),
        "source": lead_data.get("source", "manual"),
        "score": lead_data.get("score", 50),
        "context": lead_data.get("context", {}),
        "notes": [{"text": response, "source": "sba_qualify"}],
    })

    return {
        "success": True,
        "data": {
            "lead": lead,
            "sba_analysis": response,
            "thinking_phases": phases,
        },
    }


# ── Translate (any language) ────────────────────────────────────────────────


class TranslateRequest(BaseModel):
    text: str
    direction: str = "en-hi"  # en-hi, hi-en, auto
    context: str = ""


@router.post("/translate")
async def api_translate(payload: TranslateRequest):
    """Translate text between languages using SBA's LLM.

    Supports:
      - en-hi: English → Hindi/Hinglish
      - hi-en: Hindi/Hinglish → English
      - auto: Auto-detect and translate
    """
    text = payload.text
    direction = payload.direction
    context = payload.context

    if direction == "en-hi":
        sys_prompt = (
            "You are a friendly sales translator for TAGS Agency. "
            "Translate the following English text to natural Hinglish (Hindi + English mix). "
            "Keep the sales context and make it sound warm and professional. "
            "Output ONLY the translated text, nothing else."
        )
        user_prompt = f"Translate to Hinglish:\n\n{text}"
        if context:
            user_prompt = f"Context: {context}\n\nTranslate to Hinglish:\n\n{text}"
    elif direction == "hi-en":
        sys_prompt = (
            "You are a friendly sales translator for TAGS Agency. "
            "Translate the following Hinglish/Hindi text to natural English. "
            "Keep the sales context and make it sound warm and professional. "
            "Output ONLY the translated text, nothing else."
        )
        user_prompt = f"Translate to English:\n\n{text}"
        if context:
            user_prompt = f"Context: {context}\n\nTranslate to English:\n\n{text}"
    else:
        sys_prompt = "You are a translator. Detect the language and translate to the opposite language."
        user_prompt = f"Translate this:\n\n{text}"

    try:
        agent = _get_agency_sba()
        client = openai.AsyncOpenAI(
            api_key=agent.api_key,
            base_url=agent.api_base,
        )
        resp = await client.chat.completions.create(
            model=agent.model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        translated = resp.choices[0].message.content or text
        return {
            "success": True,
            "data": {
                "original": text,
                "translated": translated.strip(),
                "direction": direction,
            },
        }
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return {"success": False, "error": str(e)}


# ── SBA Think (analyze a situation) ─────────────────────────────────────────


class ThinkRequest(BaseModel):
    situation: str
    context: dict = {}


@router.post("/think")
async def api_think(payload: ThinkRequest):
    """SBA analyzes a sales situation and returns structured thinking."""
    situation = payload.situation
    meeting_ctx = payload.context.get("meeting", {})

    agent = _get_agency_sba()
    prompt = (
        f"### Situation\n{situation}\n\n"
        f"### Meeting Context\nLead: {meeting_ctx.get('lead_name', 'N/A')}\n"
        f"Title: {meeting_ctx.get('title', 'N/A')}\n"
        f"Date: {meeting_ctx.get('date', 'N/A')}\n\n"
        f"Is situation ko analyze karo. Tino cheezein do:\n"
        f"1. **analysis** — Kya ho raha hai? Lead kya chahta hai?\n"
        f"2. **decision** — Kya decision lena chahiye?\n"
        f"3. **message_to_send** — Lead ko kya bolna chahiye (Hinglish mein)?\n\n"
        f"Answer in JSON format with keys: analysis, decision, message_to_send"
    )

    response, phases = await agent.chat(message=prompt)

    # Try to parse JSON from response
    import json as _json
    thinking_result = {}
    try:
        # Find JSON in the response
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end > start:
            thinking_result = _json.loads(response[start:end])
    except Exception:
        pass

    if not thinking_result:
        thinking_result = {
            "analysis": response[:500],
            "decision": "SBA ne analysis kar liya hai",
            "message_to_send": "",
        }

    return {
        "success": True,
        "data": {
            "thinking": thinking_result,
            "thinking_phases": phases,
        },
    }


# ── Meeting Transcript ──────────────────────────────────────────────────────


class TranscriptRequest(BaseModel):
    transcript: str
    action: str = "append"  # append, analyze, finalize
    language: str = "hinglish"


@router.post("/meetings/{meeting_id}/transcript")
async def api_meeting_transcript(meeting_id: str, payload: TranscriptRequest):
    """Manage meeting transcript.

    Actions:
      - append: Add text to running transcript
      - analyze: AI analysis of the transcript so far
      - finalize: Finalize meeting with summary + action items
    """
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    action = payload.action
    new_text = payload.transcript

    if action == "append":
        # Append to existing transcript
        existing = meeting.get("transcript", "")
        updated = (existing + "\n" + new_text).strip()
        update_meeting(meeting_id, {"transcript": updated})
        return {"success": True, "data": {"transcript_length": len(updated)}}

    elif action == "analyze":
        # Use SBA to analyze the full transcript
        full_transcript = meeting.get("transcript", "") + "\n" + new_text
        agent = _get_agency_sba()
        prompt = (
            f"Yeh raha ek sales meeting ka transcript. Isko analyze karo aur yeh batao:\n\n"
            f"TRANSCRIPT:\n{full_transcript}\n\n"
            f"JSON mein answer do with these keys:\n"
            f"- client_needs: list of client requirements\n"
            f"- pain_points: list of pain points mentioned\n"
            f"- budget: budget discussion summary\n"
            f"- timeline: timeline mentioned\n"
            f"- agent_setup: {{agent_name: 'Yes/No'}} - which agents are needed\n"
            f"- action_items: list of next steps\n"
            f"- lead_response: 'haan' if lead agreed, 'nahi' if not, 'maybe' if undecided\n"
            f"- summary: 2-3 line meeting summary in Hinglish"
        )
        response, phases = await agent.chat(message=prompt)

        import json as _json
        analysis = None
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end > start:
                analysis = _json.loads(response[start:end])
        except Exception:
            pass

        if not analysis:
            analysis = {"summary": response[:300], "action_items": [], "lead_response": ""}

        # Save analysis to meeting
        lead_resp = analysis.get("lead_response", "")
        update_meeting(meeting_id, {
            "transcript": full_transcript,
            "transcript_analysis": analysis,
            "summary": analysis.get("summary", ""),
            "action_items": analysis.get("action_items", []),
            "lead_response": lead_resp,
        })

        return {
            "success": True,
            "data": {"analysis": analysis, "thinking_phases": phases},
        }

    elif action == "finalize":
        # Finalize: First analyze, then mark meeting as done
        full_transcript = meeting.get("transcript", "") + "\n" + new_text
        agent = _get_agency_sba()
        prompt = (
            f"Yeh meeting transcript hai. Final notes banao:\n\n"
            f"{full_transcript}\n\n"
            f"JSON mein do:\n"
            f"- client_needs: list\n"
            f"- pain_points: list\n"
            f"- budget: string\n"
            f"- timeline: string\n"
            f"- agent_setup: {{agent: 'Yes/No'}}\n"
            f"- action_items: list\n"
            f"- summary: string in Hinglish\n"
            f"- lead_response: 'haan'/'nahi'/'maybe'\n"
            f"- final_notes: detailed notes in Hinglish"
        )
        response, phases = await agent.chat(message=prompt)

        import json as _json
        final_notes = None
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end > start:
                final_notes = _json.loads(response[start:end])
        except Exception:
            pass

        if not final_notes:
            final_notes = {"summary": response[:300], "action_items": [], "final_notes": response}

        lead_resp = final_notes.get("lead_response", "")
        update_meeting(meeting_id, {
            "transcript": full_transcript,
            "transcript_analysis": final_notes,
            "summary": final_notes.get("summary", ""),
            "action_items": final_notes.get("action_items", []),
            "lead_response": lead_resp,
            "status": "done",
        })

        return {
            "success": True,
            "data": {
                "final_notes": final_notes,
                "meeting_status": "done",
                "lead_response": lead_resp,
            },
        }

    return {"success": False, "error": f"Unknown action: {action}"}


# ── Handoff from meeting (SBA → CEO) ───────────────────────────────────────


@router.post("/meetings/{meeting_id}/handoff-to-ceo")
async def api_handoff_from_meeting(meeting_id: str, request: Request):
    """Smart handoff lead to CEO from meeting context.

    Flow:
      1. SBA analyzes full meeting context (transcript + notes + lead_response)
      2. SBA checks confidence: Is lead ready for CEO?
         - Confidence >= 80% → Handoff directly to CEO (full dump)
         - Confidence < 80%  → Return SBA suggestions for what to clarify
      3. Force flag (`?force=true`) bypasses confidence check
    """
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    lead_id = meeting.get("lead_id", "")
    if not lead_id:
        raise HTTPException(400, "Meeting has no linked lead")

    lead = get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Linked lead not found")

    # ── Step 1: SBA analyzes full meeting context ─────────────────────
    agent = _get_agency_sba()
    transcript = meeting.get("transcript", "")
    notes_text = "\n".join([n.get("text", "") for n in meeting.get("notes", [])])
    summary = meeting.get("summary", "")
    lead_response = meeting.get("lead_response", "")

    analyze_prompt = (
        f"""Yeh raha ek lead ke saath meeting ka poora context.

LEAD INFO:
Name: {lead.get('name', 'Unknown')}
Business: {lead.get('business_name', 'N/A')}
Lead Score: {lead.get('score', 0)}
Status: {lead.get('status', 'N/A')}

MEETING SUMMARY:
{summary}

MEETING TRANSCRIPT:
{transcript[:2000]}

MEETING NOTES:
{notes_text[:1000]}

LEAD RESPONSE: {lead_response}

AB TUJHE BATANA:

1. **confidence** (0-100): Kitna confident hai tu ki ye lead ready hai CEO ko handoff karne ke liye?
   - 80+: Lead ready hai. Client ban gaya. CEO workspace bana sakta hai.
   - 50-79: Partially ready. Kuch aur clarity chahiye.
   - < 50: Not ready. Bahut kuch missing hai.

2. **analysis**: Meeting ka kya hua? Lead ne kya kaha? Kya signals mile?

3. **lead_verdict**: Ek word mein: "ready", "partial", ya "not_ready"

4. **suggestions**: Agar confident nahi hai to kya karna chahiye? Lead se kya poochna chahiye?

5. **handoff_summary**: Agar handoff ho raha hai to CEO ke liye 3-4 line summary (Hinglish mein)

Answer ONLY in JSON format with keys: confidence, analysis, lead_verdict, suggestions, handoff_summary"""
    )

    response, phases = await agent.chat(message=analyze_prompt)

    # Parse SBA's analysis
    import json as _json
    sba_analysis = {}
    try:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end > start:
            sba_analysis = _json.loads(response[start:end])
    except Exception:
        pass

    confidence = sba_analysis.get("confidence", 0)
    lead_verdict = sba_analysis.get("lead_verdict", "not_ready")
    suggestions = sba_analysis.get("suggestions", [])
    if isinstance(suggestions, str):
        suggestions = [suggestions]
    analysis_text = sba_analysis.get("analysis", response[:500])
    handoff_summary = sba_analysis.get("handoff_summary", "")

    # ── Step 2: Check confidence ──────────────────────────────────────
    force_flag = request.query_params.get("force", "").lower() == "true" if request else False

    if confidence >= 80 or lead_verdict == "ready" or force_flag:
        # ── CONFIDENT: Do the handoff ────────────────────────────────
        if not handoff_summary:
            handoff_summary = (
                f"SBA ne meeting complete ki with {lead.get('name', 'Unknown')}. "
                f"Lead ready hai client banne ke liye. Summary: {summary or 'N/A'}"
            )

        ceo_msg = (
            f"✅ SBA Handoff Report\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Lead: {lead.get('name', 'Unknown')} ({lead.get('business_name', 'N/A')})\n"
            f"SBA Confidence: {confidence}%\n"
            f"Lead Response: {lead_response or 'N/A'}\n\n"
            f"Summary:\n{handoff_summary}\n\n"
            f"Analysis:\n{analysis_text}\n\n"
            f"CEO, ab is lead ke liye workspace banao!"
        )

        try:
            handoff = await create_handoff(lead_id, ceo_message=ceo_msg)
        except ValueError as e:
            raise HTTPException(404, str(e))

        return {
            "success": True,
            "handoff_ready": True,
            "confidence": confidence,
            "data": {
                "handoff": handoff,
                "analysis": analysis_text,
                "sba_suggestions": suggestions,
                "message": (
                    f"✅ Lead '{handoff['brief']['lead_name']}' CEO ko handoff ho gaya! "
                    f"SBA {confidence}% confident hai. CEO ab workspace banayega."
                ),
            },
        }

    else:
        # ── NOT CONFIDENT: Return suggestions ────────────────────────
        if not suggestions:
            suggestions = [
                "Lead ki budget clarity nahi hai — poocho",
                "Timeline clear nahi hai — kab start karna hai?",
                "Decision maker hai ya nahi — confirm karo",
            ]

        return {
            "success": True,
            "handoff_ready": False,
            "confidence": confidence,
            "lead_verdict": lead_verdict,
            "data": {
                "analysis": analysis_text,
                "suggestions": suggestions,
                "message": (
                    f"⚠️ SBA sirf {confidence}% confident hai. "
                    f"Pehle yeh clarify karo:\n"
                    + "\n".join(f"  • {s}" for s in suggestions)
                ),
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# SBA MONITOR ENDPOINTS — 24/7 Background Pipeline
#══════════════════════════════════════════════════


@router.get("/monitor/status")
async def sba_monitor_status():
    """Get SBA monitor's latest pipeline status snapshot."""
    from admin.agency.sba_monitor import get_monitor
    monitor = get_monitor()
    status = await monitor.get_pipeline_status()
    return {"success": True, "data": status}


@router.post("/monitor/start")
async def sba_monitor_start():
    """Start the 24/7 SBA background monitoring loop."""
    from admin.agency.sba_monitor import get_monitor
    monitor = get_monitor()
    await monitor.start()
    return {"success": True, "message": "SBA 24/7 monitor started"}


@router.post("/monitor/stop")
async def sba_monitor_stop():
    """Stop the SBA background monitoring loop."""
    from admin.agency.sba_monitor import get_monitor
    monitor = get_monitor()
    await monitor.stop()
    return {"success": True, "message": "SBA monitor stopped"}


@router.get("/monitor/alerts")
async def sba_monitor_alerts():
    """Get pending CEO alerts from SBA monitor."""
    from admin.agency.sba_monitor import get_monitor
    monitor = get_monitor()
    alerts = monitor.get_ceo_alerts()
    return {"success": True, "data": {"alerts": alerts}}


# ═══════════════════════════════════════════════
# SBA EMAIL LEAD DISCOVERY
# ═══════════════════════════════════════════════════════════════════════


@router.post("/email/check")
async def sba_email_check():
    """Check email inbox for new lead inquiries and auto-create leads."""
    from admin.agency.orchestrator import sba_check_email_leads
    result = await sba_check_email_leads()
    return {"success": True, "data": result}


@router.get("/email/status")
async def sba_email_status():
    """Check if email lead service is configured and enabled."""
    from admin.tools.email_service import EmailLeadService
    service = EmailLeadService()
    return {
        "success": True,
        "data": {
            "enabled": service.enabled,
            "configured": bool(
                __import__("os").environ.get("SBA_EMAIL_IMAP_HOST", "")
            ),
        },
    }


# ═══════════════════════════════════════════════
# CEO-SBA INTEGRATION — Auto workspace from handoff
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/handoffs/{handoff_id}/auto-process")
async def sba_handoff_auto_process(handoff_id: str):
    """CEO auto-processes SBA handoff: creates workspace + registers all agents.

    This wires the full SBA -> CEO pipeline:
      1. Takes the handoff
      2. Creates client workspace
      3. Registers all 7 agents
      4. Sets up schedules
      5. Reports back to CEO
    """
    from admin.agency.orchestrator import ceo_process_sba_handoff
    result = await ceo_process_sba_handoff(handoff_id)
    return {"success": True, "data": result}


@router.post("/pipeline/scan")
async def sba_manual_pipeline_scan(workspace_id: str = "agency"):
    """Manually trigger a pipeline scan. Reports to CEO."""
    from admin.agency.orchestrator import sba_pipeline_scan
    result = sba_pipeline_scan(workspace_id)
    return {"success": True, "data": result}


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-WORKSPACE SBA — Per-client workspace config, reasoning & strategy
#══════════════════════════════════════════════════


class WorkspaceSBAUpdate(BaseModel):
    """Ek workspace ki SBA config update karne ka body."""
    enabled: bool = True
    owner_email: str = ""
    industry: str = ""


def _workspaces_summary() -> list:
    """Saare enabled SBA workspaces ka summary — dashboard ke liye.

    Kabhi raise nahi karta: har workspace ki har field safe default ke
    saath aati hai, chahe Supabase / journal / strategy fail ho jaye.
    """
    from admin.agency.sba_biztypes import (
        get_workspace_config,
        journal_path,
        list_sba_workspaces,
        strategy_path,
    )

    workspaces = []
    try:
        items = list_sba_workspaces()
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_sba_workspaces failed: %s", exc)
        return workspaces

    for ws in items:
        name = ws.get("name", "")
        if not name:
            continue
        summary = {
            "name": name,
            "category": ws.get("category", ""),
            "enabled": True,
            "owner_email": ws.get("owner_email", ""),
            "angle": ws.get("angle", ""),
            "rotation": ws.get("rotation", []),
            "leads": 0,
            "emails_sent": 0,
            "replies_yes": 0,
            "meetings": 0,
            "last_review": None,
        }

        # ── Workspace config (enabled, owner, angle, rotation) ─────────
        try:
            cfg = get_workspace_config(name) or {}
            summary["enabled"] = bool(cfg.get("enabled", True))
            summary["category"] = cfg.get("category", summary["category"])
            summary["owner_email"] = cfg.get("owner_email", summary["owner_email"])
            summary["angle"] = cfg.get("angle", summary["angle"])
            summary["rotation"] = cfg.get("rotation", summary["rotation"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_workspace_config(%s) failed: %s", name, exc)

        # ── Lead count (Supabase store; missing workspace_name = "agency")
        try:
            from admin.agency.sba_pipeline import load_leads, supabase_config

            cfg_sb = supabase_config()
            leads = load_leads(cfg_sb[0], cfg_sb[1]) if cfg_sb else []
            summary["leads"] = len(
                [l for l in leads if (l.get("workspace_name") or "agency") == name]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("leads count for %s failed: %s", name, exc)

        # ── Journal stats (per-workspace journal path) ────────────────
        try:
            from admin.agency import sba_reason as reason

            events = reason.recent_decisions(limit=500, log_path=journal_path(name))
            summary["emails_sent"] = sum(
                1 for e in events if e.get("event") == "email_sent"
            )
            summary["replies_yes"] = sum(
                1
                for e in events
                if e.get("event") == "reply_understood" and e.get("intent") == "yes"
            )
            meetings = 0
            for e in events:
                if e.get("event") == "pass_summary":
                    stats = e.get("stats")
                    if isinstance(stats, dict):
                        try:
                            meetings += int(stats.get("meetings_scheduled", 0) or 0)
                        except (TypeError, ValueError):
                            pass
            summary["meetings"] = meetings
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal stats for %s failed: %s", name, exc)

        # ── Last strategy review ───────────────────────────────────────
        try:
            from admin.agency import sba_strategy as strat

            summary["last_review"] = strat.load_strategy(
                path=strategy_path(name)
            ).get("last_review")
        except Exception as exc:  # noqa: BLE001
            logger.warning("strategy load for %s failed: %s", name, exc)

        workspaces.append(summary)
    return workspaces


@router.get("/workspaces")
async def api_sba_workspaces():
    """Saare SBA workspaces ka summary — leads, emails, replies, meetings, review."""
    return {"success": True, "workspaces": _workspaces_summary()}


@router.post("/workspaces/{name}/sba")
async def api_sba_workspace_update(name: str, payload: WorkspaceSBAUpdate):
    """Ek workspace ki SBA config update karo — enable/disable, owner, industry."""
    from admin.agency.sba_biztypes import set_workspace_config

    set_workspace_config(
        name,
        enabled=payload.enabled,
        owner_email=payload.owner_email,
        industry=payload.industry,
    )
    return {"success": True, "workspaces": _workspaces_summary()}


@router.get("/workspaces/{name}/reasoning")
async def api_sba_workspace_reasoning(name: str, limit: int = 25):
    """Ek workspace ka reasoning journal — iske leads ke saare decisions."""
    from admin.agency import sba_reason as reason
    from admin.agency.sba_biztypes import journal_path

    events = reason.recent_decisions(limit=limit, log_path=journal_path(name))
    return {"events": events, "workspace": name}


@router.get("/workspaces/{name}/strategy")
async def api_sba_workspace_strategy(name: str):
    """Ek workspace ki active strategy — angle, focus, actions, history."""
    from admin.agency import sba_strategy as strat
    from admin.agency.sba_biztypes import strategy_path

    return {
        "success": True,
        "data": strat.active_strategy(path=strategy_path(name)),
    }

# ═══════════════════════════════════════════════════════════════════════════════
# SBA ULTIMATE — New Email/Meeting/Translate Endpoints
#══════════════════════════════════════════════════════════════════


class SendLeadEmailRequest(BaseModel):
    to_email: str
    subject: str
    body_text: str


@router.post("/email/send-lead")
async def sba_email_send_lead(payload: SendLeadEmailRequest):
    """Send an email to a lead using the new SBAEmailClient (App Password)."""
    from admin.tools.sba_email_client import SBAEmailClient
    client = SBAEmailClient()
    if not client.enabled:
        return {"success": False, "error": "Email not configured. Set SBA_OWNER_EMAIL and SBA_OWNER_EMAIL_PASSWORD in .env"}
    sent = await client.send_email(
        to_email=payload.to_email,
        subject=payload.subject,
        body_text=payload.body_text,
    )
    return {"success": sent, "data": {"sent": sent}}


@router.post("/email/check-replies")
async def sba_email_check_replies(mark_read: bool = True):
    """Check inbox for lead replies using new SBAEmailClient with LLM enrichment."""
    from admin.tools.sba_email_client import SBAEmailClient
    client = SBAEmailClient()
    if not client.enabled:
        return {"success": False, "error": "Email not configured"}
    replies = await client.check_replies(mark_read=mark_read)
    return {"success": True, "data": {"replies": replies, "count": len(replies)}}


class CreateMeetingRequest(BaseModel):
    lead_id: str
    lead_name: str
    lead_email: str
    proposed_time: str
    duration_minutes: int = 30


@router.post("/meetings/new")
async def sba_meeting_create(payload: CreateMeetingRequest):
    """Create a meeting using new SBAMeetingManager (calendar + meet + email)."""
    from admin.tools.sba_meeting import SBAMeetingManager
    mgr = SBAMeetingManager()
    meeting = await mgr.create_meeting(
        lead_id=payload.lead_id,
        lead_name=payload.lead_name,
        lead_email=payload.lead_email,
        proposed_time=payload.proposed_time,
        duration_minutes=payload.duration_minutes,
    )
    return {"success": True, "data": meeting}


class TranslateTextRequest(BaseModel):
    text: str
    source_lang: str = "English"
    target_lang: str = "English"


@router.post("/translate/to-owner")
async def sba_translate_to_owner(payload: TranslateTextRequest):
    """Translate client message to Hinglish for the owner."""
    from admin.tools.sba_translate import SBATranslationEngine
    engine = SBATranslationEngine()
    result = await engine.translate_for_owner(payload.text, payload.source_lang)
    return {"success": True, "data": {"original": payload.text, "translation": result}}


@router.post("/translate/to-client")
async def sba_translate_to_client(payload: TranslateTextRequest):
    """Translate owner's Hinglish to professional English for the client."""
    from admin.tools.sba_translate import SBATranslationEngine
    engine = SBATranslationEngine()
    result = await engine.translate_for_client(payload.text, payload.target_lang)
    return {"success": True, "data": {"original": payload.text, "translation": result}}



# ── Meeting Speech-to-Speech + Notes ───────────────────────────────────────


@router.post("/meetings/audio/translate")
async def api_meeting_audio_translate(
    audio: UploadFile = File(...),
    meeting_id: str = Form(""),
    source_lang: str = Form("English"),
    play_as: str = Form("en"),
):
    """Speech-to-speech: upload a spoken utterance, get translated text + audio.

    Flow: audio → transcribe → translate (client⇄Hinglish) → TTS audio.
    """
    from admin.tools.sba_translate import SBATranslationEngine

    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "Empty audio upload")

    tmp = None
    try:
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(raw)

        engine = SBATranslationEngine()
        segments = await engine.transcribe_audio(tmp)
        translated = await engine.translate_meeting_live(segments)
        text = " ".join(s.get("translation", "") or s.get("text", "") for s in translated).strip()

        speech = await engine.synthesize(text, lang=play_as)

        if meeting_id:
            from admin.agency import sba_store

            for seg in translated:
                await sba_store.add_meeting_note(
                    mid=meeting_id,
                    text=f"{seg.get('text', '')}  →  {seg.get('translation', '')}",
                    language=source_lang,
                    speaker=seg.get("speaker", "Unknown"),
                )

        return {
            "success": True,
            "data": {
                "text": text,
                "segments": translated,
                "audio_b64": speech["audio_b64"],
                "tts_provider": speech["provider"],
                "saved_to_meeting": bool(meeting_id),
            },
        }
    except Exception as exc:
        logger.exception("Audio translate failed: %s", exc)
        raise HTTPException(500, f"Audio translate failed: {exc}")
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@router.post("/meetings/process")
async def api_meeting_process(
    audio: UploadFile = File(...),
    meeting_id: str = Form(""),
):
    """Full meeting flow: transcribe → translate → summarize → save notes.

    Notes are saved to the meeting record (when meeting_id is given) and to a
    markdown file under data/meetings/.
    """
    from admin.tools.sba_translate import SBATranslationEngine

    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "Empty audio upload")

    tmp = None
    try:
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(raw)

        engine = SBATranslationEngine()
        result = await engine.process_meeting(tmp, meeting_id=meeting_id or None)
        return {"success": True, "data": result}
    except Exception as exc:
        logger.exception("Meeting process failed: %s", exc)
        raise HTTPException(500, f"Meeting process failed: {exc}")
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@router.post("/meetings/tts")
async def api_meeting_tts(payload: TranslateTextRequest):
    """Text → speech audio for the translated phrase (speech-to-speech tail)."""
    from admin.tools.sba_translate import SBATranslationEngine

    engine = SBATranslationEngine()
    speech = await engine.synthesize(payload.text, lang=payload.target_lang)
    return {
        "success": True,
        "data": {
            "text": payload.text,
            "audio_b64": speech["audio_b64"],
            "tts_provider": speech["provider"],
        },
    }


_MEETING_TRANSLATE_PAGE = """<!doctype html>
<html lang="hi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SBA Meeting Translator</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 16px;
         max-width: 640px; margin: 0 auto; background: #0f172a; color: #e2e8f0; }
  h1 { font-size: 20px; margin: 8px 0 4px; }
  p { color: #94a3b8; font-size: 13px; margin: 4px 0 16px; }
  #btn { width: 100%; padding: 16px; font-size: 18px; border: 0; border-radius: 12px;
         background: #2563eb; color: #fff; cursor: pointer; }
  #btn.rec { background: #dc2626; }
  .box { background: #1e293b; border-radius: 12px; padding: 14px; margin-top: 14px; }
  .label { font-size: 11px; text-transform: uppercase; color: #64748b; margin-bottom: 4px; }
  .text { font-size: 16px; line-height: 1.5; white-space: pre-wrap; }
  audio { width: 100%; margin-top: 10px; }
  #log { font-size: 12px; color: #64748b; margin-top: 12px; }
</style>
</head>
<body>
  <h1>🎙️ SBA Meeting Translator</h1>
  <p>Bolo — client ko English mein sunai dega. Ya client bole — aapko Hinglish mein.
     Har utterance translate + notes mein save hota hai.</p>
  <button id="btn">▶ Start Talking</button>
  <div id="meeting" style="display:none">
    <input id="mid" placeholder="Meeting ID (optional)" style="width:100%; padding:10px;
           margin-top:12px; border-radius:8px; border:1px solid #334155; background:#0f172a;
           color:#e2e8f0; box-sizing:border-box;">
  </div>
  <div id="result" class="box" style="display:none">
    <div class="label">Translation</div>
    <div id="text" class="text"></div>
    <div class="label" style="margin-top:10px">Speech (play karne ke liye)</div>
    <audio id="play" controls autoplay></audio>
  </div>
  <div id="log"></div>
<script>
let rec = null, chunks = [], speaking = false;
const btn = document.getElementById('btn');
const result = document.getElementById('result');
const textEl = document.getElementById('text');
const play = document.getElementById('play');
const logEl = document.getElementById('log');
const mid = document.getElementById('mid');
function log(m) { logEl.textContent = m; }
function toB64(blob) {
  return new Promise((res) => {
    const r = new FileReader();
    r.onloadend = () => res(r.result.split(',')[1]);
    r.readAsDataURL(blob);
  });
}
async function send() {
  const blob = new Blob(chunks, { type: 'audio/webm' });
  chunks = [];
  const b64 = await toB64(blob);
  const body = new FormData();
  body.append('audio', new File([blob], 'utt.webm', { type: 'audio/webm' }));
  if (mid.value.trim()) body.append('meeting_id', mid.value.trim());
  log('Translating...');
  const r = await fetch('/api/sba/meetings/audio/translate', { method: 'POST', body });
  const j = await r.json();
  if (!j.success) throw new Error(j.error || 'failed');
  textEl.textContent = j.data.text;
  play.src = 'data:audio/wav;base64,' + j.data.audio_b64;
  result.style.display = 'block';
  log('Saved to meeting: ' + j.data.saved_to_meeting + ' | TTS: ' + j.data.tts_provider);
}
btn.onclick = async () => {
  if (!speaking) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = send;
      rec.start();
      speaking = true;
      btn.textContent = '⏹ Stop';
      btn.classList.add('rec');
      log('Sun raha hoon... bolo!');
    } catch (e) { log('Mic error: ' + e.message); }
  } else {
    rec.stop();
    rec.stream.getTracks().forEach(t => t.stop());
    speaking = false;
    btn.textContent = '▶ Start Talking';
    btn.classList.remove('rec');
  }
};
</script>
</body>
</html>
"""


@router.get("/meetings/translate-page", response_class=HTMLResponse)
async def sba_meeting_translate_page():
    """Companion page for live speech-to-speech translation during meetings."""
    return HTMLResponse(content=_MEETING_TRANSLATE_PAGE)

_SBA_DASHBOARD_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SBA Agent Console</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #0b1220; color: #e2e8f0; }
  .wrap { max-width: 900px; margin: 0 auto; padding: 20px 16px 60px; }
  h1 { font-size: 22px; margin: 0 0 2px; }
  .sub { color: #64748b; font-size: 13px; margin: 0 0 18px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-bottom: 18px; }
  .card { background: #111a2e; border: 1px solid #1e293b; border-radius: 12px; padding: 12px 14px; }
  .card .n { font-size: 26px; font-weight: 700; }
  .card .l { font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: .04em; }
  .sec { font-size: 14px; font-weight: 700; margin: 22px 0 8px; color: #93c5fd; }
  .row { background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; font-size: 13px; }
  .row .t { color: #94a3b8; font-size: 11px; }
  .tag { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; margin-left: 6px; }
  .tag.yes { background: #14532d; color: #86efac; }
  .tag.skip { background: #450a0a; color: #fca5a5; }
  .tag.wait { background: #78350f; color: #fcd34d; }
  .tag.info { background: #1e3a8a; color: #bfdbfe; }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; color: #7dd3fc; }
  .pill { display: inline-block; background: #1e293b; border-radius: 999px; padding: 2px 10px; margin: 0 6px 6px 0; font-size: 12px; color: #cbd5e1; }
  pre { white-space: pre-wrap; word-break: break-word; margin: 0; }
  a { color: #60a5fa; }
</style>
</head>
<body>
<div class="wrap">
  <h1>🧠 SBA Agent Console</h1>
  <p class="sub">Agent ka dimaag: strategy, har decision ki wajah, aur live results</p>
  <div id="err" style="display:none;color:#fca5a5;background:#450a0a;border-radius:10px;padding:10px;margin-bottom:14px"></div>

  <div class="sec">Current strategy</div>
  <div class="card" id="strategy"><div class="n" style="font-size:14px">loading...</div></div>

  <div class="sec">Last pass</div>
  <div class="grid" id="stats"><div class="card"><div class="n">-</div><div class="l">loading</div></div></div>

  <div class="sec">Agent decisions (reasoning journal)</div>
  <div id="journal"><div class="row">loading...</div></div>
</div>
<script>
async function j(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}
const esc = (s) => (s ?? "").toString().replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function tagFor(a) {
  const m = {contact:"info", skip:"skip", wait:"wait", yes:"yes", no:"skip", maybe:"wait", stop:"skip"};
  const cls = m[a] || "info";
  return '<span class="tag '+cls+'">'+esc(a)+'</span>';
}
async function load() {
  try {
    const [st, ap, jn] = await Promise.all([
      j("/api/sba/strategy"), j("/api/sba/autopilot/status"),
      j("/api/sba/reasoning?limit=30"),
    ]);
    const s = st.data || {};
    document.getElementById("strategy").innerHTML =
      '<div style="font-size:14px;line-height:1.6"><b>Angle:</b> ' + esc(s.angle) + '<br>' +
      '<b>Focus:</b> ' + (s.focus && s.focus.length ? s.focus.map(f => '<span class="pill">'+esc(f.join(', '))+'</span>').join('') : '<span class="pill">default rotation</span>') + '<br>' +
      (s.actions && s.actions.length ? '<b>Actions:</b> ' + s.actions.map(a => '• ' + esc(a)).join('<br>') : '') +
      (s.last_review ? '<div class="t">last review: '+esc(s.last_review)+'</div>' : '') + '</div>';
    const stt = ap.status || {};
    const items = [["emails_sent","emails"],["new_leads_found","new leads"],["owner_notified","yes-replies"],
                   ["meetings_scheduled","meetings"],["invalid_email","rejected"],["send_failed","send fails"]];
    document.getElementById("stats").innerHTML = items.map(([k,l]) =>
      '<div class="card"><div class="n">'+(stt[k] ?? 0)+'</div><div class="l">'+l+'</div></div>').join("");
    const evs = jn.events || [];
    document.getElementById("journal").innerHTML = evs.length ? evs.map(e => {
      let line = "";
      if (e.event === "lead_judged") line = '<b>'+esc(e.name)+'</b> '+tagFor(e.verdict && e.verdict.action)+' score '+(e.verdict && e.verdict.score)+' &mdash; '+esc(e.verdict && e.verdict.reason);
      else if (e.event === "email_sent") line = '📧 emailed <b>'+esc(e.name)+'</b> ('+esc(e.email)+')';
      else if (e.event === "email_rejected") line = '🚫 rejected '+esc(e.email)+' &mdash; '+esc(e.reason);
      else if (e.event === "reply_understood") line = '💬 reply from '+esc(e.from)+' -> '+tagFor(e.intent)+(e.meeting_time?' at '+esc(e.meeting_time):'');
      else if (e.event === "strategy_review") line = '🧠 strategy reviewed &mdash; '+esc(e.angle);
      else line = '<b>'+esc(e.event)+'</b>';
      if (e.event === "pass_summary" && e.stats) line = '📊 pass: ' + Object.entries(e.stats).filter(([,v])=>v).map(([k,v])=>k+'='+v).join(' · ');
      return '<div class="row">'+line+'<div class="t">'+esc(e.ts)+'</div></div>';
    }).join("") : '<div class="row">Abhi koi decisions nahi &mdash; journal khali hai. Pehla pass ke baad yahan bhar jayega.</div>';
  } catch (e) {
    document.getElementById("err").style.display = "block";
    document.getElementById("err").textContent = "Load failed: " + e.message;
  }
}
load();
setInterval(load, 30000);
</script>
</body>
</html>"""
