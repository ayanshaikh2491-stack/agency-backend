"""Autonomous CEO scheduler API — manage L1 report-only scheduled CEO triggers.

Thin wrappers over admin.agency.scheduler.get_scheduler(). Consistent with the
other routers: JSON bodies in, {"status": "ok", ...} out, failures surfaced as
4xx/5xx with a message (never crashing the app).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Path as FastPath
from pydantic import BaseModel, Field

from admin.agency.scheduler import get_scheduler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ceo/schedules", tags=["ceo-scheduler"])


# ── Request models ──────────────────────────────────────────────────────────

class ScheduleCreate(BaseModel):
    slug: str = Field(..., description="Unique schedule key")
    task: str = Field(..., description="Task/prompt the CEO runs on schedule")
    interval_minutes: int = Field(..., gt=0, description="Run cadence in minutes")
    workspace_id: str = ""
    enabled: bool = True


class ScheduleToggle(BaseModel):
    enabled: bool = Field(..., description="Enable or disable the schedule")


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("")
async def create_schedule(body: ScheduleCreate):
    """Create (or replace) a scheduled CEO run."""
    try:
        sched = await get_scheduler().upsert_schedule(body.model_dump())
    except ValueError as exc:  # invalid input
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "schedule": sched}


@router.get("")
async def list_schedules():
    """List all scheduled CEO runs."""
    return {"status": "ok", "schedules": get_scheduler().list_schedules()}


@router.patch("/{slug}")
async def toggle_schedule(
    slug: str = FastPath(..., description="Schedule slug"),
    body: ScheduleToggle = ScheduleToggle(enabled=True),
):
    """Enable/disable a scheduled CEO run via {"enabled": bool}."""
    try:
        sched = await get_scheduler().toggle_schedule(slug, body.enabled)
    except KeyError:
        return {"status": "error", "message": f"schedule '{slug}' not found"}
    return {"status": "ok", "schedule": sched}


@router.delete("/{slug}")
async def delete_schedule(slug: str = FastPath(..., description="Schedule slug")):
    """Delete a scheduled CEO run."""
    removed = await get_scheduler().delete_schedule(slug)
    return {"status": "ok", "removed": removed}
