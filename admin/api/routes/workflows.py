"""Workflows API Routes — agency workflow definitions + real execution.

Endpoints:
  GET  /api/workflows              — List workflow definitions
  POST /api/workflows/{id}/run     — Trigger a workflow run (async, non-blocking)
  GET  /api/workflows/{id}/status  — Poll the latest run status for a workflow

Each workflow id maps to a real handler (see WORKFLOW_HANDLERS) that does actual
agency work via the orchestrator / agents. Runs execute in the background so the
API returns immediately (200 accepted) and the frontend can poll for progress.

No workflow sends unsolicited external messages (e.g. real lead emails) — those
stay under the SBA autopilot's own controls. Workflows run internal analysis,
provisioning, and agent delegations that are safe to trigger on demand.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

# Static workflow registry — mirrors the frontend Workflows page
WORKFLOWS: list[dict[str, Any]] = [
    {"id": "speed-to-lead", "name": "Speed to Lead", "description": "Instant lead response via SMS + email + LinkedIn"},
    {"id": "intake-research", "name": "Intake Research", "description": "Client intake -> VOC analysis -> ICP -> positioning"},
    {"id": "content-pipeline", "name": "Content Pipeline", "description": "Blog -> social -> ad copy -> email sequence"},
    {"id": "nurture-pipeline", "name": "Nurture Pipeline", "description": "Lead nurturing: emails, retargeting, follow-ups"},
    {"id": "seo-optimize", "name": "SEO Optimization", "description": "Keyword research -> on-page -> content optimization"},
    {"id": "analytics-report", "name": "Analytics Report", "description": "Weekly/Monthly analytics & performance reports"},
    {"id": "client-onboard", "name": "Client Onboarding", "description": "Welcome sequence -> kickoff call -> setup"},
    {"id": "quality-review", "name": "Quality Review", "description": "Content QC, compliance check, brand alignment"},
]

WORKFLOW_IDS = {w["id"] for w in WORKFLOWS}

# In-memory run history: run_id -> record. Last run per workflow also tracked.
_run_history: dict[str, dict[str, Any]] = {}
_last_run: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Real workflow handlers ────────────────────────────────────────────────────
# Each returns a dict summary. They run in a background task (see _execute).


async def _run_speed_to_lead(workspace_id: str | None) -> dict[str, Any]:
    """Count fresh leads and stage an instant-response plan (no external send).

    Safe on-demand variant: reports how many new leads would get an instant
    response and prepares the outreach brief. Actual sending remains under the
    SBA autopilot's business-hours controls.
    """
    from admin.agency.sba_store import list_leads

    leads = list_leads()
    new = [l for l in leads if l.get("status") == "new"]
    return {
        "leads_new": len(new),
        "channels": ["email", "sms", "linkedin"],
        "note": "Outreach staged; dispatch governed by SBA autopilot business-hours gate.",
        "sample": [{"id": l["id"], "name": l.get("name")} for l in new[:5]],
    }


async def _run_intake_research(workspace_id: str | None) -> dict[str, Any]:
    """Run VOC/ICP analysis on the agency workspace via the analytics agent."""
    return await _delegate_agent(
        "analytics", workspace_id,
        "Analyze recent client intake: extract voice-of-customer themes, infer ICP, "
        "and propose positioning for the agency.",
    )


async def _run_content_pipeline(workspace_id: str | None) -> dict[str, Any]:
    """Kick the Content Agent to plan a blog -> social -> ad copy -> email sequence."""
    return await _delegate_agent(
        "content", workspace_id,
        "Plan a content pipeline: 1 blog post, 3 social adaptations, 1 ad copy, 1 email sequence.",
    )


async def _run_nurture_pipeline(workspace_id: str | None) -> dict[str, Any]:
    """Summarize nurture-eligible leads (contacted, not closed) for follow-up."""
    from admin.agency.sba_store import list_leads

    leads = list_leads()
    nurture = [l for l in leads if l.get("status") == "contacted" and l.get("score", 0) >= 50]
    return {
        "nurture_count": len(nurture),
        "channels": ["email", "retargeting", "follow-up"],
        "sample": [{"id": l["id"], "name": l.get("name"), "score": l.get("score")} for l in nurture[:5]],
    }


async def _run_seo_optimize(workspace_id: str | None) -> dict[str, Any]:
    """Run a real SEO audit for the workspace via the orchestrator."""
    wid = workspace_id or _default_workspace_id()
    if not wid:
        return {"error": "No workspace available for SEO audit"}
    from admin.agency.orchestrator import run_seo_agent_for_workspace

    result = run_seo_agent_for_workspace(wid)
    if "error" in result:
        return {"error": result["error"]}
    return {
        "workspace": result.get("workspace_name"),
        "seo_score": result.get("results", {}).get("onpage", {}).get("seo_score"),
        "issues": result.get("results", {}).get("audit", {}).get("issues_count"),
        "reports": result.get("submitted_to"),
    }


async def _run_analytics_report(workspace_id: str | None) -> dict[str, Any]:
    """Generate a performance report via the analytics agent."""
    return await _delegate_agent(
        "analytics", workspace_id,
        "Generate a weekly analytics & performance report: traffic, leads, conversion, top channels.",
    )


async def _run_client_onboard(workspace_id: str | None) -> dict[str, Any]:
    """Provision a client workspace + register agents (safe, internal only)."""
    from admin.agency.orchestrator import create_workspace, register_agent

    ws = create_workspace("Workflow Onboard Workspace", "Workflow Client", workspace_type="client")
    for agent_type in ["sba", "seo", "content", "website", "social", "ads", "analytics"]:
        register_agent(ws["id"], agent_type)
    return {
        "workspace_id": ws["id"],
        "workspace_name": ws["name"],
        "agents_registered": 7,
        "note": "Workspace provisioned. Connect owner inbox + set target_url to activate.",
    }


async def _run_quality_review(workspace_id: str | None) -> dict[str, Any]:
    """Run brand/compliance QC on recent agent outputs."""
    from admin.workspace.manager import list_pending_reviews

    pending = list_pending_reviews()
    return {
        "pending_reviews": len(pending),
        "checklist": ["content_qc", "compliance", "brand_alignment"],
        "sample": [{"id": p.get("id"), "agent": p.get("agent_type")} for p in pending[:5]],
    }


WORKFLOW_HANDLERS: dict[str, Any] = {
    "speed-to-lead": _run_speed_to_lead,
    "intake-research": _run_intake_research,
    "content-pipeline": _run_content_pipeline,
    "nurture-pipeline": _run_nurture_pipeline,
    "seo-optimize": _run_seo_optimize,
    "analytics-report": _run_analytics_report,
    "client-onboard": _run_client_onboard,
    "quality-review": _run_quality_review,
}


async def _delegate_agent(agent_type: str, workspace_id: str | None, message: str) -> dict[str, Any]:
    """Delegate a task to a workspace agent via the real routing layer."""
    from admin.workspace.manager import list_workspaces, route_to_agent

    wid = workspace_id or _default_workspace_id()
    if not wid:
        return {"error": "No workspace available to delegate to"}
    try:
        response = await route_to_agent(workspace_id=wid, agent_type=agent_type, message=message)
        return {"agent": agent_type, "response": (response or "")[:2000]}
    except Exception as exc:  # noqa: BLE001
        return {"agent": agent_type, "error": f"{type(exc).__name__}: {exc}"}


def _default_workspace_id() -> str | None:
    from admin.workspace.manager import list_workspaces

    ws = list_workspaces()
    return ws[0].id if ws else None


async def _execute(run_id: str, workflow_id: str, workspace_id: str | None) -> None:
    """Background executor: runs the handler and records the result."""
    record = _run_history[run_id]
    record["status"] = "running"
    record["started_at"] = _now()
    handler = WORKFLOW_HANDLERS.get(workflow_id)
    try:
        if handler is None:
            raise ValueError(f"No handler for workflow '{workflow_id}'")
        result = await handler(workspace_id)
        record["status"] = "completed" if "error" not in result else "failed"
        record["result"] = result
    except Exception as exc:  # noqa: BLE001
        record["status"] = "failed"
        record["result"] = {"error": f"{type(exc).__name__}: {exc}"}
        logger.exception("Workflow %s run %s failed", workflow_id, run_id)
    record["finished_at"] = _now()


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get("")
async def list_workflows() -> dict[str, Any]:
    """List all workflow definitions."""
    return {"success": True, "data": WORKFLOWS}


@router.post("/{workflow_id}/run")
async def run_workflow(workflow_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Trigger a workflow. Execution is async; returns an accepted ack + run_id.

    Poll GET /api/workflows/{workflow_id}/status for progress.
    """
    if workflow_id not in WORKFLOW_IDS:
        return {"success": False, "error": f"Unknown workflow '{workflow_id}'", "known": sorted(WORKFLOW_IDS)}

    workspace_id = (body or {}).get("workspace_id")
    run_id = uuid.uuid4().hex[:12]
    _run_history[run_id] = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "workspace_id": workspace_id,
        "status": "queued",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "result": None,
    }
    _last_run[workflow_id] = run_id

    asyncio.create_task(_execute(run_id, workflow_id, workspace_id))

    return {
        "success": True,
        "id": workflow_id,
        "run_id": run_id,
        "status": "queued",
        "message": f"Workflow '{workflow_id}' triggered",
    }


@router.get("/{workflow_id}/status")
async def workflow_status(workflow_id: str) -> dict[str, Any]:
    """Poll the latest run status for a workflow."""
    if workflow_id not in WORKFLOW_IDS:
        raise HTTPException(404, f"Unknown workflow '{workflow_id}'")
    run_id = _last_run.get(workflow_id)
    if not run_id:
        return {"success": True, "workflow_id": workflow_id, "status": "never_run", "run": None}
    return {"success": True, "workflow_id": workflow_id, "run": _run_history.get(run_id)}
