"""Orchestrator API Routes — workspace, agent, reporting, scheduling endpoints.

Endpoints:
  POST   /api/orch/workspace              — Create workspace
  GET    /api/orch/workspaces             — List workspaces
  GET    /api/orch/workspace/{wid}        — Get workspace
  DELETE /api/orch/workspace/{wid}        — Delete workspace
  POST   /api/orch/agent                  — Register agent in workspace
  GET    /api/orch/agents/{wid}           — List agents in workspace
  POST   /api/orch/run/{wid}              — Run SEO agent for workspace
  POST   /api/orch/plan/{wid}             — Create + execute plan
  GET    /api/orch/reports                — List reports
  GET    /api/orch/reports/pending/{agent} — Get pending reports
  POST   /api/orch/monitor              — Agency SEO monitor (quality check)
  POST   /api/orch/schedule               — Create schedule
  GET    /api/orch/schedules/{wid}        — List schedules for workspace
  POST   /api/orch/scheduler/tick         — Run due scheduled tasks
  POST   /api/orch/setup/client           — Full client workspace setup
  POST   /api/orch/setup/agency           — Full agency setup
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orch", tags=["orchestrator"])


# ── Request Models ───────────────────────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    name: str
    client_name: str = ""
    workspace_type: str = "client"
    settings: dict[str, Any] | None = None


class RegisterAgentRequest(BaseModel):
    workspace_id: str
    agent_type: str
    config: dict[str, Any] | None = None


class SetupClientRequest(BaseModel):
    client_name: str
    target_url: str
    keywords: list[str] | None = None


class CreateScheduleRequest(BaseModel):
    workspace_id: str
    task_type: str
    params: dict[str, Any] | None = None
    frequency: str = "daily"


# ── Workspace CRUD ──────────────────────────────────────────────────────────

@router.post("/workspace")
async def api_create_workspace(body: CreateWorkspaceRequest):
    from admin.agency.orchestrator import create_workspace
    ws = create_workspace(body.name, body.client_name, body.workspace_type, body.settings)
    return {"success": True, "data": ws}


@router.get("/workspaces")
async def api_list_workspaces(workspace_type: str | None = None):
    from admin.agency.orchestrator import list_workspaces
    return {"success": True, "data": list_workspaces(workspace_type)}


@router.get("/workspace/{workspace_id}")
async def api_get_workspace(workspace_id: str):
    from admin.agency.orchestrator import get_workspace
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return {"success": True, "data": ws}


@router.delete("/workspace/{workspace_id}")
async def api_delete_workspace(workspace_id: str):
    from admin.agency.orchestrator import delete_workspace
    if not delete_workspace(workspace_id):
        raise HTTPException(404, "Workspace not found")
    return {"success": True}


# ── Agent Registration ──────────────────────────────────────────────────────

@router.post("/agent")
async def api_register_agent(body: RegisterAgentRequest):
    from admin.agency.orchestrator import register_agent
    try:
        agent = register_agent(body.workspace_id, body.agent_type, body.config)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"success": True, "data": agent}


@router.get("/agents/{workspace_id}")
async def api_list_agents(workspace_id: str):
    from admin.agency.orchestrator import get_workspace
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return {"success": True, "data": ws.get("agents", {})}


# ── Run Agent / Plan ────────────────────────────────────────────────────────

@router.post("/run/{workspace_id}")
async def api_run_seo(workspace_id: str):
    """Run SEO agent for a client workspace (scan + report to agency)."""
    from admin.agency.orchestrator import run_seo_agent_for_workspace
    result = run_seo_agent_for_workspace(workspace_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return {"success": True, "data": result}


@router.post("/plan/{workspace_id}")
async def api_plan_and_execute(workspace_id: str):
    """Create plan + execute all tasks + report results."""
    from admin.agency.planner import create_seo_plan, execute_plan_and_report
    from admin.agency.orchestrator import get_workspace
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")

    url = ws.get("settings", {}).get("target_url", "")
    kws = ws.get("settings", {}).get("keywords", [])
    if not url:
        raise HTTPException(400, "No target_url in workspace settings")

    plan = create_seo_plan(workspace_id, url, kws)
    result = execute_plan_and_report(workspace_id)
    return {"success": True, "data": result}


# ── Reports ─────────────────────────────────────────────────────────────────

@router.get("/reports")
async def api_list_reports(
    to_agent_type: str | None = None,
    from_workspace_id: str | None = None,
    report_type: str | None = None,
):
    from admin.agency.orchestrator import get_reports
    return {"success": True, "data": get_reports(to_agent_type, from_workspace_id, report_type)}


@router.get("/reports/pending/{agent_type}")
async def api_pending_reports(agent_type: str):
    from admin.agency.orchestrator import get_pending_reports
    return {"success": True, "data": get_pending_reports(agent_type)}


@router.post("/report/aggregate")
async def api_aggregate_reports():
    """Agency SEO: review all client reports, quality check, catch mistakes."""
    from admin.agency.orchestrator import agency_seo_monitor
    return {"success": True, "data": agency_seo_monitor()}


@router.post("/report/workspace-ceo-to-agency/{workspace_id}")
async def api_workspace_ceo_to_agency(workspace_id: str):
    """Workspace CEO: forward workspace reports to Agency CEO."""
    from admin.agency.orchestrator import workspace_ceo_to_agency_ceo
    return {"success": True, "data": workspace_ceo_to_agency_ceo(workspace_id)}


# ── Scheduling ──────────────────────────────────────────────────────────────

@router.post("/schedule")
async def api_create_schedule(body: CreateScheduleRequest):
    from admin.agency.scheduler import create_schedule
    s = create_schedule(body.workspace_id, body.task_type, body.params, body.frequency)
    return {"success": True, "data": s}


@router.get("/schedules/{workspace_id}")
async def api_list_schedules(workspace_id: str):
    from admin.agency.scheduler import get_schedules
    return {"success": True, "data": get_schedules(workspace_id)}


@router.post("/scheduler/tick")
async def api_scheduler_tick():
    """Run all due scheduled tasks."""
    from admin.agency.scheduler import run_due_tasks
    return {"success": True, "data": run_due_tasks()}


@router.get("/scheduler/due")
async def api_due_tasks():
    from admin.agency.scheduler import get_due_tasks
    return {"success": True, "data": get_due_tasks()}


# ── Quick Setup ─────────────────────────────────────────────────────────────

@router.post("/setup/agency")
async def api_setup_agency():
    """Create agency workspace with all agent slots."""
    from admin.agency.orchestrator import setup_agency
    from admin.agency.scheduler import setup_agency_schedules
    agency = setup_agency()
    schedules = setup_agency_schedules()
    return {"success": True, "data": {"workspace": agency, "schedules": len(schedules)}}


@router.post("/setup/client")
async def api_setup_client(body: SetupClientRequest):
    """Create client workspace with SEO agent + default schedules."""
    from admin.agency.orchestrator import setup_client_workspace
    from admin.agency.scheduler import setup_default_schedules
    ws = setup_client_workspace(body.client_name, body.target_url, body.keywords)
    schedules = setup_default_schedules(ws["id"])
    return {"success": True, "data": {"workspace": ws, "schedules": len(schedules)}}
