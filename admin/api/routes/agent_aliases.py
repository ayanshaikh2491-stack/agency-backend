"""Agent-page compat routes for the Next.js frontend.

The frontend Agents pages call:
  GET  /api/agents                      — list of worker agents + status
  POST /api/agents/{agent_id}/chat      — chat with a worker agent
  GET  /api/agents/{agent_id}/status    — online check (extra.py provides this)
  /api/agents/seo-engine/*              — SEO agent page reuses the SBA
                                          pipeline/meetings/finance surface

Backed by the real workspace agents (admin/workspace/agents/*) so chats are
actual LLM agent responses, scoped to the client's workspace.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Frontend worker slugs -> workspace agent_type (admin/workspace/manager.py).
# Only agents with a real production implementation are registered here.
AGENT_SLUG_MAP: dict[str, str] = {
    "content-creator": "content",
    "seo-engine": "seo",
    "website-builder": "website",
    "ads-runner": "ads",
    "analytics-bot": "analytics",
    "social-manager": "social",
    "memory-agent": "memory",
    "analyzing-bot": "analyzing",
}

AGENT_META: dict[str, dict[str, str]] = {
    "content-creator": {"name": "Content Creator", "role": "content"},
    "seo-engine": {"name": "SEO Engine", "role": "seo"},
    "website-builder": {"name": "Website Agent", "role": "website"},
    "ads-runner": {"name": "Ads Runner", "role": "ads"},
    "analytics-bot": {"name": "Analytics Bot", "role": "analytics"},
    "social-manager": {"name": "Social Manager", "role": "social"},
    "memory-agent": {"name": "Memory Agent", "role": "memory"},
    "analyzing-bot": {"name": "Analyzing Agent", "role": "analyzing"},
}


@router.get("")
async def api_agents_list() -> dict[str, Any]:
    """List worker agents with live status (backend is the live orchestrator)."""
    items = [
        {
            "id": slug,
            "slug": slug,
            "name": AGENT_META[slug]["name"],
            "role": AGENT_META[slug]["role"],
            "status": "active",
        }
        for slug in AGENT_SLUG_MAP
    ]
    return {"success": True, "agents": items, "data": {"agents": items}}


async def _resolve_workspace(client_name: str, workspace_id: str | None) -> tuple[Any, str]:
    """Find a workspace for an agent chat.

    Priority: explicit workspace_id -> workspace whose client_name/name matches
    the frontend's selected client -> first workspace -> auto-created workspace.
    """
    from admin.api.models.schemas import WorkspaceCreate
    from admin.workspace.manager import (
        create_workspace,
        get_workspace,
        list_workspaces,
    )

    if workspace_id:
        ws = get_workspace(workspace_id)
        if ws:
            return ws, workspace_id

    if client_name:
        needle = client_name.strip().lower()
        for ws in list_workspaces():
            cand = (ws.client_name or "").strip().lower()
            if cand == needle:
                return ws, ws.id
            # also match by workspace name (e.g. "Ayan Agency")
            if (ws.name or "").strip().lower() == needle:
                return ws, ws.id

    all_ws = list_workspaces()
    if all_ws:
        return all_ws[0], all_ws[0].id

    created = create_workspace(
        WorkspaceCreate(
            name=client_name.strip() or "Agency Workspace",
            client_name=client_name.strip() or "Agency Workspace",
        )
    )
    return created, created.id


@router.post("/{agent_id}/chat")
async def api_agent_chat(agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Chat with a worker agent, routed to its real workspace agent."""
    from admin.workspace.manager import route_to_agent

    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "Message is required")

    # CEO-gated: the boss may ONLY talk to the CEO, never a worker directly.
    # All real work flows through POST /api/ceo/chat -> CEO delegation.
    # This guard runs BEFORE slug validation so any boss→worker chat attempt
    # (including unknown slugs like "sba") is rejected with a clear pointer.
    raise HTTPException(
        426,
        detail=(
            "Direct worker chat is disabled. The boss talks only to the CEO. "
            f"Use POST /api/ceo/chat and let the CEO delegate to {agent_id}."
        ),
    )

    if agent_id not in AGENT_SLUG_MAP:
        raise HTTPException(404, f"Unknown agent: {agent_id}")

    client_name = (body.get("client_name") or "").strip()
    workspace_id = body.get("workspace_id")
    agent_type = AGENT_SLUG_MAP[agent_id]

    ws, resolved_id = await _resolve_workspace(client_name, workspace_id)
    try:
        response_text = await route_to_agent(
            workspace_id=resolved_id,
            agent_type=agent_type,
            message=message,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent chat %s failed: %s", agent_id, exc)
        return {
            "success": False,
            "error": f"{agent_id} call failed: {exc}",
            "data": {"response": f"❌ Agent call failed: {exc}", "agent_type": agent_type},
        }

    return {
        "success": True,
        "data": {
            "response": response_text,
            "agent_type": agent_type,
            "workspace_id": resolved_id,
        },
    }


# ── SEO agent page aliases (page reuses the SBA pipeline surface) ───────────
# The frontend /admin/agents/seo page calls /api/agents/seo-engine/* exactly
# like the SBA page calls /api/sba/*. Delegate to the SBA handlers.

from admin.api.routes import sba as _sba_routes  # noqa: E402

_seo_router = APIRouter(prefix="/api/agents/seo-engine", tags=["agents"])
_seo_handlers: dict[str, tuple[str, Any]] = {
    "/status": ("GET", _sba_routes.sba_status),
    "/pipeline": ("GET", _sba_routes.api_pipeline),
    "/meetings": ("GET", _sba_routes.api_list_meetings),
    "/finance": ("GET", _sba_routes.api_finance),
    "/think": ("POST", _sba_routes.api_think),
    "/translate": ("POST", _sba_routes.api_translate),
    "/chat": ("POST", _sba_routes.sba_chat),
}
for _path, (_method, _handler) in _seo_handlers.items():
    _seo_router.add_api_route(_path, _handler, methods=[_method])
_seo_router.add_api_route(
    "/meetings/{meeting_id}/transcript",
    _sba_routes.api_meeting_transcript,
    methods=["POST"],
)
_seo_router.add_api_route(
    "/meetings/{meeting_id}/handoff-to-ceo",
    _sba_routes.api_handoff_from_meeting,
    methods=["POST"],
)
