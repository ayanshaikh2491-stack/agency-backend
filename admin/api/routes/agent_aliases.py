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
import os
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Frontend worker slugs -> workspace agent_type (admin/workspace/manager.py).
# Only agents with a real production implementation are registered here.
AGENT_SLUG_MAP: dict[str, str] = {
    "sba": "sba",
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
    "sba": {"name": "SBA Agent", "role": "sba"},
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
    from admin.config import settings

    # Lifecycle state → live status + current task pointer (from snapshot).
    state_map: dict[str, dict] = {}
    try:
        from admin.agency.lifecycle import snapshot

        for row in snapshot():
            state_map[row["slug"]] = row
    except Exception:  # noqa: BLE001
        pass

    provider = "freeapi-router"
    model = settings.WORKSPACE_AGENT_MODEL or "auto"
    items = []
    for slug, meta in AGENT_SLUG_MAP.items():
        lc = state_map.get(slug, {})
        state = lc.get("state", "standby")
        brief_id = lc.get("current_brief_id") or ""
        last_err = (lc.get("last_error") or "")[:100]
        task = (
            (f"[{state.upper()}] " if state != "standby" else "")
            + (f"brief {brief_id}" if brief_id else "idle — boss se kaam lo")
            + (f" | last err: {last_err}" if last_err else "")
        )
        items.append({
            "id": slug,
            "slug": slug,
            "name": AGENT_META[slug]["name"],
            "role": AGENT_META[slug]["role"],
            "status": "active" if state == "active" else ("cooldown" if state == "cooldown" else "standby"),
            "task": task,
            "provider": provider,
            "model": model,
            "api_key_ref": "WORKSPACE_API_KEY (unified freeapi)",
        })
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


@router.get("/{agent_id}/memories")
async def api_agent_memories(agent_id: str, limit: int = 20) -> dict[str, Any]:
    """Recent agent outputs as 'memories' — real work the agent produced
    (the frontend Memory panel previously showed Not Found because no
    such route existed)."""
    if agent_id not in AGENT_SLUG_MAP:
        raise HTTPException(404, f"Unknown agent: {agent_id}")
    agent_type = AGENT_SLUG_MAP[agent_id]
    from admin.workspace.manager import get_agent_activity_log

    # Agent outputs are logged in the activity log; surface the recent
    # task/output rows for this agent as memory entries.
    try:
        rows = get_agent_activity_log("agency", agent_type, limit=limit * 2)
    except Exception:  # noqa: BLE001
        rows = []
    memories = [
        {
            "id": r.get("id", ""),
            "text": (r.get("details") or r.get("action") or "")[:200],
            "content": (r.get("details") or "")[:200],
            "type": "activity",
            "created_at": r.get("timestamp") or r.get("created_at"),
        }
        for r in rows
    ][:limit]
    return {"success": True, "memories": memories, "data": memories}


@router.get("/{agent_id}/conversations")
async def api_agent_conversations(agent_id: str, limit: int = 10) -> dict[str, Any]:
    """Recent agent outputs (task + output) as conversation history."""
    if agent_id not in AGENT_SLUG_MAP:
        raise HTTPException(404, f"Unknown agent: {agent_id}")
    agent_type = AGENT_SLUG_MAP[agent_id]
    from admin.workspace.manager import _agent_outputs

    convs = [
        {
            "id": o.get("id", ""),
            "task": (o.get("task") or "")[:120],
            "summary": (o.get("output_preview") or "")[:200],
            "agent_type": o.get("agent_type", ""),
            "timestamp": o.get("timestamp"),
        }
        for o in reversed(_agent_outputs)
        if o.get("agent_type") == agent_type
    ][:limit]
    return {"success": True, "conversations": convs, "data": convs}


@router.post("/{agent_id}/chat")
async def api_agent_chat(agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Chat with a worker agent, routed to its real workspace agent."""
    from admin.workspace.manager import route_to_agent

    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "Message is required")

    # CEO-gated by default: the boss talks only to the CEO, who delegates.
    # Set AGENT_DIRECT_CHAT=1 to allow direct boss→worker chat (e.g. from the
    # frontend agent page) — the routing/expert-mode path below still applies.
    if os.getenv("AGENT_DIRECT_CHAT", "") != "1":
        raise HTTPException(
            426,
            detail=(
                "Direct worker chat is disabled. The boss talks only to the CEO. "
                f"Use POST /api/ceo/chat and let the CEO delegate to {agent_id}. "
                "(Set AGENT_DIRECT_CHAT=1 to enable direct chat.)"
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
