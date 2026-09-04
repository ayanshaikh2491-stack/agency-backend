"""Workspace CRUD + per-agent chat routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from admin.api.models.schemas import (
    ChatRequest,
    ChatResponse,
    WorkspaceCreate,
    WorkspaceOut,
)
from admin.workspace.manager import (
    create_workspace,
    get_workspace,
    list_workspaces,
    delete_workspace,
    route_to_agent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat/workspace", tags=["workspace"])


# ── CRUD ───────────────────────────────────────────────────────────────────


@router.post("", response_model=WorkspaceOut, status_code=201)
async def api_create_workspace(payload: WorkspaceCreate):
    return create_workspace(payload)


@router.get("", response_model=list[WorkspaceOut])
async def api_list_workspaces():
    return list_workspaces()


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def api_get_workspace(workspace_id: str):
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return ws


@router.delete("/{workspace_id}", status_code=204)
async def api_delete_workspace(workspace_id: str):
    if not delete_workspace(workspace_id):
        raise HTTPException(404, "Workspace not found")


# ── Agent chat inside a workspace ──────────────────────────────────────────


@router.post("/{workspace_id}/chat", response_model=ChatResponse)
async def chat_with_workspace_agent(workspace_id: str, body: ChatRequest):
    """Route a message to a specific agent inside a workspace."""
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")

    agent_type = body.agent_type or "sba"
    if agent_type not in ws.agents:
        raise HTTPException(400, f"Agent '{agent_type}' not in this workspace")

    # ── Agent dispatch ───────────────────────────────────────────────
    response_text = await route_to_agent(
        workspace_id=workspace_id,
        agent_type=agent_type,
        message=body.message,
    )

    return ChatResponse(
        response=response_text,
        conversation_id=f"{workspace_id}-{agent_type}",
        agent_type=agent_type,
        thinking_phases=[],
    )
