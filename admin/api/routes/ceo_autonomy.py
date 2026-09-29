"""Safe API surface for the event-driven CEO autonomy control plane."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from admin.agency.ceo_autonomy import (
    decide_approval,
    emit_event,
    get_autonomy,
    list_approvals,
    list_decisions,
    list_events,
    list_tasks,
    request_approval,
)

router = APIRouter(prefix="/api/ceo/autonomy", tags=["ceo-autonomy"])


class ApprovalRequest(BaseModel):
    action_type: str = Field(..., description="email, publication, spend, contract, or external")
    description: str = Field(..., min_length=1, max_length=1000)
    workspace_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    decided_by: str = Field(default="operator", max_length=200)
    reason: str = Field(default="", max_length=1000)


@router.get("/status")
async def autonomy_status():
    """Control-plane state and bounded queue counts."""
    return {"status": "ok", "autonomy": await get_autonomy().status()}


@router.get("/events")
async def autonomy_events(
    limit: int = Query(100, ge=1, le=200),
    status: str | None = Query(None, max_length=40),
    workspace_id: str | None = Query(None, max_length=200),
):
    return {"status": "ok", "events": await list_events(limit, status, workspace_id)}


@router.get("/decisions")
async def autonomy_decisions(
    limit: int = Query(100, ge=1, le=200),
    workspace_id: str | None = Query(None, max_length=200),
):
    return {"status": "ok", "decisions": await list_decisions(limit, workspace_id)}


@router.get("/approvals")
async def autonomy_approvals(
    limit: int = Query(100, ge=1, le=200),
    status: str | None = Query(None, max_length=40),
    workspace_id: str | None = Query(None, max_length=200),
):
    return {"status": "ok", "approvals": await list_approvals(limit, status, workspace_id)}


@router.get("/tasks")
async def autonomy_tasks(
    limit: int = Query(100, ge=1, le=200),
    status: str | None = Query(None, max_length=40),
    workspace_id: str | None = Query(None, max_length=200),
):
    return {"status": "ok", "tasks": await list_tasks(limit, status, workspace_id)}


@router.post("/approvals")
async def create_autonomy_approval(body: ApprovalRequest):
    """Create a pending human gate. This endpoint never executes the action."""
    approval = await request_approval(
        body.action_type,
        body.description,
        body.workspace_id,
        body.payload,
    )
    if not approval:
        raise HTTPException(500, "Approval could not be persisted")
    return {"status": "ok", "approval": approval}


async def _decide_approval(approval_id: str, status: str, body: ApprovalDecision):
    from admin.persistence import get_workspace_db, row_to_dict

    db = await get_workspace_db()
    cursor = await db.execute(
        "SELECT status FROM ceo_autonomy_approvals WHERE id=?", (approval_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(404, "Approval not found")
    if row["status"] != "pending":
        raise HTTPException(409, "Approval is already decided")
    approval = await decide_approval(
        approval_id,
        status,
        reason=body.reason,
        decided_by=body.decided_by,
    )
    if not approval:
        raise HTTPException(409, "Approval could not be decided")
    return approval


@router.post("/approvals/{approval_id}/approve")
async def approve_autonomy_approval(approval_id: str, body: ApprovalDecision):
    """Record approval only. External execution still requires a separate explicit path."""
    return {
        "status": "ok",
        "approval": await _decide_approval(approval_id, "approved", body),
    }


@router.post("/approvals/{approval_id}/reject")
async def reject_autonomy_approval(approval_id: str, body: ApprovalDecision):
    return {
        "status": "ok",
        "approval": await _decide_approval(approval_id, "rejected", body),
    }


@router.post("/heartbeat")
async def autonomy_heartbeat(reason: str = "manual"):
    """Enqueue a manual heartbeat event for the next bounded tick."""
    event = await emit_event(
        "heartbeat",
        source="api",
        payload={"reason": reason[:300]},
    )
    if not event:
        raise HTTPException(500, "Heartbeat event could not be persisted")
    return {"status": "ok", "event": event}


@router.post("/tick")
async def autonomy_tick():
    """Run one bounded control-plane tick on demand."""
    return await get_autonomy().tick()
