"""Agency CEO API — Full orchestration endpoints.

Endpoints:
  POST /api/ceo/chat                  — Chat with CEO (co-founder persona)
  POST /api/ceo/handoff/receive       — Receive SBA handoff (Q17)
  POST /api/ceo/parallel-blast        — Brief all agents simultaneously (Q4)
  POST /api/ceo/review                — CEO reviews agent output (Q20)
  POST /api/ceo/error/route           — CEO routes error fixes (Q21)
  POST /api/ceo/report                — Generate weekly/monthly reports (Q23)
  GET  /api/ceo/knowledge             — Cross-workspace knowledge (CRITICAL)
  POST /api/ceo/knowledge             — Add learning to knowledge pool
  GET  /api/ceo/reviews               — List pending/completed reviews
  GET  /api/ceo/errors                — List error logs
  GET  /api/ceo/state                 — Lifecycle snapshot of every agent (light, on-demand)
  POST /api/ceo/agent/{slug}/wake     — Boss/CEO manual wake override
  POST /api/ceo/agent/{slug}/sleep    — Boss/CEO manual sleep override
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Path as FastPath
from pydantic import BaseModel, Field

from admin.agency.ceo import AgencyCEO
from admin.agency import lifecycle as lc
from admin.api.models.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ceo", tags=["ceo"])
_ceo = AgencyCEO()


# ── Request/Response models ──────────────────────────────────────────────────

class HandoffRequest(BaseModel):
    handoff_id: str
    action: str = "review_only"  # accept_and_create_workspace | review_only | reject
    ceo_notes: str = ""


class ParallelBlastRequest(BaseModel):
    workspace_id: str
    client_brief: str
    campaign_name: str = "General"
    deadline: str = "TBD"
    agents: Optional[list[str]] = None


class ReviewRequest(BaseModel):
    workspace_id: str
    agent_type: str
    output_id: str = ""
    verdict: str = "approved"  # approved | needs_revision | rejected
    feedback: str = ""


class ErrorRouteRequest(BaseModel):
    workspace_id: str
    error_type: str = "other"
    severity: str = "medium"
    description: str = ""
    route_to: str = ""


class ReportRequest(BaseModel):
    report_type: str = "weekly"  # weekly | monthly | client_specific
    workspace_id: Optional[str] = None
    period_start: str = ""
    period_end: str = ""


class KnowledgeRequest(BaseModel):
    action: str = "get_all"  # get_all | get_by_domain | add_learning
    domain: str = ""
    learning: str = ""
    source_workspace: str = ""


class WakeSleepResponse(BaseModel):
    slug: str
    state: str
    message: str = ""


# ── Lifecycle endpoints (CEO-gated on-demand control) ──────────────────────────

@router.get("/state", tags=["ceo-lifecycle"])
async def ceo_state():
    """Light, on-demand lifecycle snapshot of every agent.

    No polling loop — just reads the current LifecycleState table. CEO is the
    only thing that flips states; this is read-only introspection so the boss
    (or CEO) can see who is STANDBY vs ACTIVE.
    """
    agents = lc.snapshot()
    # Ensure every known agent has a row (register lazily if missed at boot).
    for slug in ("ceo", "sba", "seo", "social", "website"):
        if not any(a["slug"] == slug for a in agents):
            lc.register(slug)
    return {
        "ceo_listener": "active_24x7_http",  # CEO listens via HTTP, not a loop
        "agents": lc.snapshot(),
        "rule": "agents sleep by default (STANDBY); CEO wakes on demand; self-sleep when done",
    }


@router.post("/agent/{slug}/wake", response_model=WakeSleepResponse, tags=["ceo-lifecycle"])
async def agent_wake(slug: str = FastPath(..., description="agent slug, e.g. sba")):
    """Boss/CEO manual wake override (forces ACTIVE)."""
    try:
        rt = lc.force_wake(slug)
        return WakeSleepResponse(slug=slug, state=rt.state.value, message=f"{slug} forced awake.")
    except Exception as exc:  # noqa: BLE001
        return WakeSleepResponse(slug=slug, state="error", message=str(exc)[:200])


@router.post("/agent/{slug}/sleep", response_model=WakeSleepResponse, tags=["ceo-lifecycle"])
async def agent_sleep(slug: str = FastPath(..., description="agent slug, e.g. sba")):
    """Boss/CEO manual sleep override (forces STANDBY)."""
    try:
        rt = lc.force_sleep(slug)
        return WakeSleepResponse(slug=slug, state=rt.state.value, message=f"{slug} forced to sleep.")
    except Exception as exc:  # noqa: BLE001
        return WakeSleepResponse(slug=slug, state="error", message=str(exc)[:200])


# ── Endpoints ────────────────────────────────────────────────────────────────

# Legacy alias (old /api/chat/agency path)
_old_router = APIRouter(prefix="/api/chat/agency", tags=["ceo-legacy"])


@_old_router.post("", response_model=ChatResponse)
async def chat_with_ceo_legacy(body: ChatRequest):
    """Legacy endpoint — redirects to new /api/ceo/chat."""
    return await chat_with_ceo(body)


@router.post("/chat", response_model=ChatResponse)
async def chat_with_ceo(body: ChatRequest):
    """Chat with the Agency CEO (co-founder strategic partner)."""
    from admin.workspace.manager import update_agent_activity, append_agent_activity_log
    update_agent_activity("ceo", "ceo", "working", body.message[:80])
    append_agent_activity_log("ceo", "ceo", "msg", f"Boss: {body.message[:160]}")
    response, conv_id, phases = await _ceo.chat(
        message=body.message,
        user_role="the agency owner",
        conversation_id=body.conversation_id,
    )
    update_agent_activity("ceo", "ceo", "idle")
    append_agent_activity_log("ceo", "ceo", "msg", f"CEO: {(response or '')[:160]}")
    return ChatResponse(
        response=response,
        conversation_id=conv_id,
        agent_type="ceo",
        thinking_phases=phases,
    )


@router.get("/floor")
async def floor_activity(workspace_id: str | None = None):
    """Live floor state — what each agent is doing right now (CEO Control Room)."""
    from admin.workspace.manager import get_floor_activity

    return {"status": "ok", "floor": get_floor_activity(workspace_id)}


@router.get("/agent-log")
async def agent_activity_log(workspace_id: str, agent_type: str, limit: int = 80):
    """Live transcript for ONE agent on the floor (munder-difflin terminal equiv)."""
    from admin.workspace.manager import get_agent_activity_log

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "agent_type": agent_type,
        "log": get_agent_activity_log(workspace_id, agent_type, limit),
    }


@router.post("/handoff/receive")
async def receive_handoff(body: HandoffRequest):
    """Receive SBA handoff (Q17) — structured brief + full data dump."""
    result = await _ceo.receive_handoff(
        handoff_id=body.handoff_id,
        action=body.action,
        ceo_notes=body.ceo_notes,
    )
    return {"status": "ok", "result": result}


@router.post("/parallel-blast")
async def parallel_blast(body: ParallelBlastRequest):
    """Brief ALL agents in a workspace simultaneously (Q4 — parallel blast)."""
    result = await _ceo.parallel_blast(
        workspace_id=body.workspace_id,
        client_brief=body.client_brief,
        campaign_name=body.campaign_name,
        deadline=body.deadline,
    )
    return {"status": "ok", "result": result}


@router.post("/review")
async def review_output(body: ReviewRequest):
    """CEO reviews agent output (Q20) — approve, revise, or reject."""
    result = await _ceo.review_output(
        workspace_id=body.workspace_id,
        agent_type=body.agent_type,
        output_id=body.output_id,
        verdict=body.verdict,
        feedback=body.feedback,
    )
    return {"status": "ok", "result": result}


@router.post("/error/route")
async def route_error(body: ErrorRouteRequest):
    """CEO routes error fix to the right agent (Q21)."""
    result = await _ceo.route_error(
        workspace_id=body.workspace_id,
        error_type=body.error_type,
        severity=body.severity,
        description=body.description,
        route_to=body.route_to,
    )
    return {"status": "ok", "result": result}


@router.post("/report")
async def generate_report(body: ReportRequest):
    """Generate weekly/monthly agency report (Q23)."""
    result = await _ceo.generate_report(
        report_type=body.report_type,
        workspace_id=body.workspace_id,
        period_start=body.period_start,
        period_end=body.period_end,
    )
    return {"status": "ok", "report": result}


@router.get("/knowledge")
async def get_knowledge(
    action: str = "get_all",
    domain: str = "",
):
    """Get cross-workspace knowledge (CRITICAL)."""
    result = await _ceo.cross_workspace_knowledge(
        action=action,
        domain=domain,
    )
    return {"status": "ok", "knowledge": result}


@router.post("/knowledge")
async def add_knowledge(body: KnowledgeRequest):
    """Add a learning to the agency knowledge pool."""
    result = await _ceo.cross_workspace_knowledge(
        action="add_learning",
        domain=body.domain,
        learning=body.learning,
        source_workspace=body.source_workspace,
    )
    return {"status": "ok", "result": result}


@router.get("/reviews")
async def list_reviews(workspace_id: str | None = None):
    """List pending and completed CEO reviews."""
    from admin.workspace.manager import list_pending_reviews, list_reviews

    pending = list_pending_reviews()
    completed = list_reviews(workspace_id)

    return {
        "status": "ok",
        "pending_count": len(pending),
        "pending": pending,
        "completed": completed,
    }


@router.get("/errors")
async def list_errors(workspace_id: str | None = None, unresolved_only: bool = False):
    """List error logs and routing history."""
    from admin.workspace.manager import list_errors

    errors = list_errors(workspace_id, unresolved_only)

    return {
        "status": "ok",
        "count": len(errors),
        "errors": errors,
    }


@router.get("/status")
async def ceo_agency_status():
    """Get CEO's view of agency status - workspaces, alerts, pending items."""
    from admin.agency.ceo_monitor import get_monitor
    monitor = get_monitor()
    status = await monitor.get_agency_status()
    return {"status": "ok", "monitor": status}


@router.get("/overview")
async def ceo_agency_overview():
    """Full agency overview for CEO dashboard."""
    from admin.ceo_data import get_agency_overview
    overview = get_agency_overview()
    return {"status": "ok", "overview": overview}


# ── CEO Email Outbox (queued client emails) ─────────────────────────────────


class EmailSendRequest(BaseModel):
    to_email: str
    subject: str = ""
    body: str = ""
    workspace_id: str = ""


@router.get("/email/outbox")
async def email_outbox(workspace_id: str | None = None, status: str | None = None, limit: int = 100):
    """List queued/emitted client emails (CEO control room visibility)."""
    from admin.tools.email_queue import list_outbox
    return {"status": "ok", "outbox": await list_outbox(workspace_id, status, limit)}


@router.post("/email/send")
async def email_send(body: EmailSendRequest):
    """Queue a client email via the CEO outbox (not sent live yet)."""
    from admin.tools.email_queue import queue_email
    msg_id = await queue_email(
        to_email=body.to_email,
        subject=body.subject,
        body=body.body,
        from_agent="ceo",
        workspace_id=body.workspace_id,
    )
    return {"status": "ok", "queued": True, "id": msg_id}
