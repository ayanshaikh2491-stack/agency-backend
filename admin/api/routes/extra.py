"""Extra API Routes — status + social tokens for the Next.js frontend.

Endpoints:
  GET /api/status                — Agency pipeline summary (dashboard)
  GET /api/social/tokens/status  — Connected social platform accounts
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["extra"])

# The agent-health monitor is a NICE-TO-HAVE dashboard signal, not a hard
# backend dependency. If the module is ever missing on a box (seen: it was
# left out of a deploy bundle and the whole backend died at import time, then
# systemd tight-looped it 2000x and pegged the CPU), we must NOT crash the
# entire API. So import it defensively and degrade gracefully to per-request
# construction probes. The monitor's own job is to surface agent failures —
# it must never be the thing that takes the backend down.
try:
    from admin.agency.agent_monitor import _AGENT_PROBES, get_monitor  # noqa: E402
    _HAVE_MONITOR = True
except Exception:  # noqa: BLE001
    _AGENT_PROBES = {}  # type: ignore[assignment]
    get_monitor = None  # type: ignore[assignment]
    _HAVE_MONITOR = False


@router.get("/api/status", tags=["system"])
async def api_status() -> dict[str, Any]:
    """Agency status — pipeline summary + workspace count.

    Shape matches what the frontend expects:
      status?.pipeline?.queue?.total / .new / .leads_found_today
    """
    from admin.agency.sba_store import list_leads
    from admin.workspace.manager import list_workspaces

    all_leads = list_leads()
    by_status: dict[str, int] = {}
    for s in ["new", "contacted", "meeting", "proposal", "negotiation", "closed", "lost"]:
        by_status[s] = len([l for l in all_leads if l["status"] == s])

    new_count = by_status.get("new", 0)
    total_in_pipeline = sum(by_status.values())
    hot_leads = len([l for l in all_leads if l.get("score", 0) >= 80 and l["status"] != "closed"])

    try:
        workspace_count = len(list_workspaces())
    except Exception:  # noqa: BLE001
        workspace_count = 0

    # Boss visibility: LLM budget guards + autonomous CEO scheduler state.
    try:
        from admin.agency.scheduler import get_scheduler
        from admin.llm_throttle import snapshot as llm_snapshot

        guards: dict[str, Any] = {
            "llm_guards": llm_snapshot(),
            "scheduled_tasks": len(get_scheduler().list_schedules()),
        }
    except Exception:  # noqa: BLE001
        guards = {}

    return {
        "success": True,
        **guards,
        "pipeline": {
            "leads_found_today": new_count,
            "queue": {"total": total_in_pipeline, "new": new_count},
            "by_status": by_status,
            "hot_leads": hot_leads,
        },
        "workspaces": workspace_count,
    }


@router.get("/api/social/tokens/status")
async def social_tokens_status() -> dict[str, Any]:
    """Return connected social accounts.

    The frontend reads `connected_accounts[].platform` to render the
    connected-platforms strip on agent pages. Currently no OAuth tokens are
    stored on this backend, so the list is empty (frontend handles that
    gracefully and shows no connected platforms).
    """
    return {
        "success": True,
        "connected_accounts": [],
        "platforms": [],
    }


# ── Agent status (frontend polls /api/agents/{slug}/status) ────────────────

_AGENT_STATUS_KEYS = {
    "seo-engine": "seo",
    "website-builder": "website",
    "social-manager": "social",
    "content-creator": "content",
    "ads-runner": "ads",
    "analytics-bot": "analytics",
    "memory-agent": "memory",  # probes admin.workspace.agents.memory (currently missing)
}


@router.get("/api/agents/{agent_id}/status")
async def api_agent_status(agent_id: str) -> dict[str, Any]:
    """Agent status used by the agent pages' mount check.

    Previously returned a hardcoded `running` for every registered agent,
    which hid real construction failures until a user opened the chat. Now it
    reads live health from the AgentHealthMonitor (probed every few minutes),
    falling back to a construction probe if the monitor has not polled yet.
    """
    key = _AGENT_STATUS_KEYS.get(agent_id, agent_id.replace("-", "_"))

    # Frontend slugs (seo-engine, memory-agent, ...) -> internal probe slug.
    # _AGENT_STATUS_KEYS maps frontend-slug -> internal-role; the monitor probe
    # table (_AGENT_PROBES) is keyed by the internal role.
    probe_slug = None
    if agent_id in _AGENT_STATUS_KEYS:
        probe_slug = _AGENT_STATUS_KEYS[agent_id]  # internal role (seo, memory, ...)
    elif agent_id in _AGENT_PROBES:
        probe_slug = agent_id
    elif key in _AGENT_PROBES:
        probe_slug = key

    if probe_slug in _AGENT_PROBES:
        try:
            if _HAVE_MONITOR:
                health = await get_monitor().get_health()
                rec = health["agents"].get(probe_slug)
                if rec is None:
                    rec = get_monitor()._health.get(probe_slug) or _probe_now(probe_slug)
                status = rec["status"]
            else:
                status = _probe_now(probe_slug)["status"]
        except Exception:  # noqa: BLE001
            status = _probe_now(probe_slug)["status"]
    else:
        # Slugs we don't actively probe default healthy so the frontend mount
        # check still passes; the monitor surfaces gaps for probed slugs.
        status = "running"

    return {
        "success": True,
        "agent_id": agent_id,
        key: {"status": status},
    }


def _probe_now(agent_id: str) -> dict[str, Any]:
    if not _HAVE_MONITOR or agent_id not in _AGENT_PROBES:
        return {"slug": agent_id, "status": "unknown", "error": "monitor unavailable", "last_checked": None}
    from admin.agency.agent_monitor import probe_agent, _AGENT_PROBES

    return probe_agent(agent_id, _AGENT_PROBES[agent_id])


@router.get("/api/agents/health")
async def api_agents_health() -> dict[str, Any]:
    """Agency-wide agent health summary from the 24/7 monitor."""
    if not _HAVE_MONITOR:
        return {
            "success": True,
            "monitor_available": False,
            "message": "agent monitor module not loaded",
            "agents": {},
        }
    from admin.agency.agent_monitor import get_monitor

    return await get_monitor().get_health()


@router.get("/api/issues")
async def api_issues(workspace_id: str | None = None) -> dict[str, Any]:
    """Aggregated issue list for the admin Issues page.

    Combines the two real "todo-ish" data sources the backend already tracks:
      - CEO pending reviews (content/quality review tasks awaiting action)
      - agent routing/error logs (failures needing attention)

    Returns a normalized list of issues with a stable shape the frontend renders:
      { id, title, status: 'todo'|'in_progress'|'done', agent, prio: 'High'|'Medium'|'Low' }
    """
    from admin.workspace.manager import list_errors, list_pending_reviews

    issues: list[dict[str, Any]] = []

    # 1) CEO pending reviews -> todo issues assigned to the content/CEO review queue.
    try:
        for r in list_pending_reviews():
            rid = str(r.get("id") or r.get("review_id") or "")
            issues.append({
                "id": f"REV-{rid}" if rid else f"REV-{len(issues)+1}",
                "title": r.get("title") or r.get("summary") or "Pending content review",
                "status": "todo",
                "agent": r.get("agent") or "Content Creator",
                "prio": "Medium",
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("issues: reviews failed: %s", exc)

    # 2) Unresolved errors -> high-priority issues.
    try:
        for e in list_errors(workspace_id, unresolved_only=True):
            eid = str(e.get("id") or e.get("error_id") or "")
            issues.append({
                "id": f"ERR-{eid}" if eid else f"ERR-{len(issues)+1}",
                "title": e.get("message") or e.get("summary") or "Agent routing error",
                "status": "todo",
                "agent": e.get("agent") or e.get("agent_type") or "System",
                "prio": "High",
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("issues: errors failed: %s", exc)

    open_count = len([i for i in issues if i["status"] in ("todo", "in_progress")])
    return {
        "success": True,
        "issues": issues,
        "counts": {"total": len(issues), "open": open_count, "done": len(issues) - open_count},
    }


# ── Workspace aliases (frontend uses /api/workspaces, backend CRUD is
#    /api/chat/workspace). Kept here so the Next.js catch-all proxy
#    (/api/workspaces/...) reaches a live backend route. ────────────────────


def _ws_item(ws: Any) -> dict[str, Any]:
    dump = getattr(ws, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return dict(ws)


@router.get("/api/workspaces")
async def api_workspaces() -> dict[str, Any]:
    from admin.workspace.manager import list_workspaces

    items = [_ws_item(w) for w in list_workspaces()]
    return {"success": True, "workspaces": items, "data": {"workspaces": items}}


@router.post("/api/workspaces")
async def api_workspaces_create(payload: dict[str, Any]) -> dict[str, Any]:
    from admin.api.models.schemas import WorkspaceCreate
    from admin.workspace.manager import create_workspace

    wc = WorkspaceCreate(
        name=(payload.get("name") or "").strip(),
        client_name=payload.get("client_name") or payload.get("name") or "",
        description=payload.get("description") or payload.get("industry") or "",
    )
    item = _ws_item(create_workspace(wc))
    return {"success": True, "workspace": item, "data": {"workspace": item}}


@router.get("/api/workspaces/{ws_id}/sba")
async def api_workspace_sba(ws_id: str) -> dict[str, Any]:
    from admin.workspace.manager import get_workspace

    ws = get_workspace(ws_id)
    if not ws:
        return {"success": False, "error": "Workspace not found"}
    return {"success": True, "data": _ws_item(ws)}


@router.get("/api/workspaces/{ws_id}/context")
async def api_workspace_context(ws_id: str) -> dict[str, Any]:
    """Everything that switches when the boss switches workspace.

    Agents are shared stateless workers; all their state is namespaced by
    workspace_id. This endpoint surfaces that per-workspace slice in one
    call so a frontend switcher only needs the id.
    """
    from admin.file_store import load_all
    from admin.workspace.manager import get_workspace

    ws = get_workspace(ws_id)
    if not ws:
        return {"success": False, "error": "Workspace not found"}

    mem_by_agent: dict[str, int] = {}
    try:
        for rec in load_all("agent_memory").values():
            if rec.get("workspace") != ws_id:
                continue
            agent = str(rec.get("agent") or "?")
            mem_by_agent[agent] = mem_by_agent.get(agent, 0) + 1
    except Exception:  # noqa: BLE001
        pass

    recent: list[dict[str, Any]] = []
    try:
        outputs = [
            r for r in load_all("agent_outputs").values()
            if r.get("workspace_id") == ws_id
        ]
        outputs.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        recent = [
            {
                "agent": r.get("agent_type"),
                "task": str(r.get("task", ""))[:80],
                "created_at": r.get("created_at"),
            }
            for r in outputs[:3]
        ]
    except Exception:  # noqa: BLE001
        pass

    return {
        "success": True,
        "workspace": {"id": ws.id, "name": ws.name, "client_name": ws.client_name},
        "agents": list(ws.agents),
        "memory_counts": mem_by_agent,
        "recent_outputs": recent,
    }


@router.delete("/api/workspaces/{ws_id}")
async def api_workspace_delete(ws_id: str) -> dict[str, Any]:
    from admin.workspace.manager import delete_workspace

    ok = delete_workspace(ws_id)
    return {"success": ok, "deleted": ok}
