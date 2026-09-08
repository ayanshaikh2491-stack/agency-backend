"""Background task API: start agent work and poll for results.

POST /api/tasks            {"agent_type": "sba", "workspace_id": "...",
                            "message": "..."}        -> task_id (2s reply)
GET  /api/tasks            ?limit=20                 -> task list
GET  /api/tasks/{id}                                 -> full state + result

Any caller (frontend, CEO, cron) can start long agent work here without
the Render 60s edge limit: the POST returns immediately with a task_id.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from admin.agency.task_runner import get_task, list_tasks, start_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("")
async def api_start_task(body: dict[str, Any]) -> dict[str, Any]:
    """Start a background agent task; returns task_id immediately."""
    agent_type = (body.get("agent_type") or "").strip()
    message = (body.get("message") or "").strip()
    workspace_id = (body.get("workspace_id") or "").strip()

    if not agent_type or not message:
        raise HTTPException(400, "agent_type and message are required")

    task_id = await start_task(
        agent_type, workspace_id or "agency", message,
        source=body.get("source", "api"),
    )
    if task_id == "BUSY":
        return {
            "success": False,
            "error": "Server busy - 3 tasks already running, thodi der baad try karo",
        }
    return {
        "success": True,
        "task_id": task_id,
        "status": "queued",
        "poll": f"GET /api/tasks/{task_id}",
    }


@router.get("")
async def api_list_tasks(limit: int = 20) -> dict[str, Any]:
    return {"success": True, "tasks": list_tasks(limit=limit)}


@router.get("/{task_id}")
async def api_get_task(task_id: str) -> dict[str, Any]:
    st = get_task(task_id)
    if not st:
        raise HTTPException(404, f"Unknown task: {task_id}")
    return {"success": True, "task": st}
