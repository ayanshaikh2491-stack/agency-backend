"""Agent communication API routes.

Exposes the inter-agent message bus to the frontend:
- Send briefs between agents
- Parallel blast (CEO delegates to all agents)
- View communication history
- Cross-workspace knowledge sharing
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from admin.workspace.agent_bus import (
    brief_agent,
    get_communication_summary,
    get_knowledge,
    get_messages,
    parallel_blast,
    respond_to_brief,
    share_knowledge,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/communication", tags=["communication"])


# ── Request/Response Models ───────────────────────────────────────────────────


class BriefRequest(BaseModel):
    from_agent: str
    to_agent: str
    workspace_id: str
    task: str
    context: str = ""
    priority: str = "normal"


class ParallelBlastRequest(BaseModel):
    workspace_id: str
    task: str
    context: str = ""
    agents: list[str] | None = None
    from_agent: str = "ceo"


class RespondRequest(BaseModel):
    from_agent: str
    to_agent: str
    workspace_id: str
    original_message_id: str
    response: str


class KnowledgeRequest(BaseModel):
    workspace_id: str
    key: str
    value: Any
    source_agent: str = ""
    category: str = "general"


class MessageOut(BaseModel):
    id: str
    from_agent: str
    to_agent: str
    workspace_id: str
    message_type: str
    subject: str
    content: str
    metadata: dict[str, Any]
    timestamp: str
    read: bool
    responded: bool


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/brief", response_model=MessageOut)
async def api_send_brief(body: BriefRequest):
    """Send a task brief from one agent to another."""
    msg = brief_agent(
        from_agent=body.from_agent,
        to_agent=body.to_agent,
        workspace_id=body.workspace_id,
        task=body.task,
        context=body.context,
        priority=body.priority,
    )
    return MessageOut(**msg.__dict__)


@router.post("/parallel-blast", response_model=list[MessageOut])
async def api_parallel_blast(body: ParallelBlastRequest):
    """Brief ALL agents in a workspace simultaneously (CEO tool)."""
    messages = parallel_blast(
        workspace_id=body.workspace_id,
        task=body.task,
        context=body.context,
        agents=body.agents,
        from_agent=body.from_agent,
    )
    return [MessageOut(**m.__dict__) for m in messages]


@router.post("/respond", response_model=MessageOut)
async def api_respond(body: RespondRequest):
    """Respond to a brief received from another agent."""
    msg = respond_to_brief(
        from_agent=body.from_agent,
        to_agent=body.to_agent,
        workspace_id=body.workspace_id,
        original_message_id=body.original_message_id,
        response=body.response,
    )
    return MessageOut(**msg.__dict__)


@router.get("/messages/{workspace_id}", response_model=list[MessageOut])
async def api_get_messages(
    workspace_id: str,
    to_agent: str | None = None,
    from_agent: str | None = None,
    unread_only: bool = False,
):
    """Get messages for a workspace."""
    msgs = get_messages(
        workspace_id=workspace_id,
        to_agent=to_agent,
        from_agent=from_agent,
        unread_only=unread_only,
    )
    return [MessageOut(**m.__dict__) for m in msgs]


@router.get("/summary/{workspace_id}")
async def api_get_summary(workspace_id: str):
    """Get communication summary for CEO dashboard."""
    return get_communication_summary(workspace_id)


@router.post("/knowledge")
async def api_share_knowledge(body: KnowledgeRequest):
    """Share knowledge across workspaces."""
    share_knowledge(
        workspace_id=body.workspace_id,
        key=body.key,
        value=body.value,
        source_agent=body.source_agent,
        category=body.category,
    )
    return {"status": "shared", "key": body.key}


@router.get("/knowledge/{workspace_id}")
async def api_get_knowledge(
    workspace_id: str,
    key: str | None = None,
    category: str | None = None,
):
    """Get shared knowledge for a workspace."""
    return get_knowledge(workspace_id, key=key, category=category)
