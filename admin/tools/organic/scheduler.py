"""Real organic post scheduler: queue pending posts and dispatch when due.

Files live in admin/organic_data/<workspace_id>/scheduled/<id>.json
(pending). Dispatched jobs are removed from pending and recorded in the
workspace history log via history.record_post().

The backend lifespan runs a 60s loop calling dispatch_due(); a manual
POST /api/social/organic/schedule/dispatch also works (and is what tests use).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from admin.tools.organic.history import ORGANIC_DATA_DIR, record_post
from admin.tools.organic.registry import get_channel
from admin.tools.organic.base import validate_payload

logger = logging.getLogger(__name__)


def _scheduled_dir(workspace_id: str) -> Path:
    d = ORGANIC_DATA_DIR / workspace_id / "scheduled"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_for(workspace_id: str, schedule_id: str) -> Path:
    return _scheduled_dir(workspace_id) / f"{schedule_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_run_at(run_at: str) -> str:
    """Parse an ISO datetime; naive input is treated as UTC. Returns ISO UTC string."""
    try:
        dt = datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid run_at '{run_at}' — use ISO 8601 e.g. 2026-08-06T18:30:00Z") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def schedule_post(workspace_id: str, channel: str, payload: dict, run_at: str) -> dict[str, Any]:
    """Queue a post for dispatch at run_at. Validates channel + required fields."""
    meta = get_channel(channel)
    if meta is None:
        return {"status": "error", "channel": channel, "error": f"Unknown channel: {channel}"}
    missing = validate_payload(meta, payload)
    if missing:
        return {
            "status": "error", "channel": channel,
            "error": f"Missing required fields: {', '.join(missing)}",
        }
    try:
        run_at_iso = _normalize_run_at(run_at)
    except ValueError as exc:
        return {"status": "error", "channel": channel, "error": str(exc)}

    entry = {
        "id": uuid.uuid4().hex[:12],
        "channel": channel,
        "workspace_id": workspace_id,
        "payload": payload,
        "run_at": run_at_iso,
        "created_at": _now(),
        "status": "pending",
    }
    try:
        _file_for(workspace_id, entry["id"]).write_text(json.dumps(entry, indent=2), encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "channel": channel, "error": f"Cannot write schedule: {exc}"}
    logger.info("scheduled %s post %s for %s", channel, entry["id"], run_at_iso)
    return {
        "status": "scheduled",
        "channel": channel,
        "schedule_id": entry["id"],
        "run_at": run_at_iso,
        "workspace_id": workspace_id,
    }


def list_scheduled(workspace_id: str) -> list[dict]:
    """Pending + done scheduled posts for a workspace, soonest first."""
    d = _scheduled_dir(workspace_id)
    entries: list[dict] = []
    if not d.exists():
        return []
    for f in sorted(d.glob("*.json")):
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        e.setdefault("status", "done")
        entries.append(e)
    entries.sort(key=lambda e: e.get("run_at", ""))
    return entries


def cancel_scheduled(workspace_id: str, schedule_id: str) -> dict[str, Any]:
    f = _file_for(workspace_id, schedule_id)
    if not f.exists():
        return {"status": "error", "error": f"No scheduled post {schedule_id}"}
    try:
        f.unlink()
    except OSError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "cancelled", "schedule_id": schedule_id}


def _scan_all_pending() -> list[dict]:
    """Yield every pending job across all workspaces."""
    if not ORGANIC_DATA_DIR.exists():
        return []
    jobs: list[dict] = []
    for ws_dir in ORGANIC_DATA_DIR.iterdir():
        if not ws_dir.is_dir():
            continue
        pending_dir = ws_dir / "scheduled"
        if not pending_dir.is_dir():
            continue
        for f in pending_dir.glob("*.json"):
            try:
                e = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if e.get("status") != "pending":
                continue
            e["_file"] = f
            e["_workspace_id"] = ws_dir.name
            jobs.append(e)
    return jobs


def dispatch_due(now: str | None = None) -> dict[str, Any]:
    """Dispatch every pending job whose run_at <= now.

    Each job: hub.post -> history.record_post -> remove pending file.
    Returns per-status counts so callers (API / worker loop) can log progress.
    """
    from admin.tools.organic.hub import post as hub_post

    now_iso = now or _now()
    jobs = _scan_all_pending()
    due = [j for j in jobs if (j.get("run_at") or "") <= now_iso]
    stats: dict[str, Any] = {"due": len(due), "dispatched": 0, "failed": 0, "errors": []}
    for job in due:
        ws = job["_workspace_id"]
        channel = job.get("channel", "")
        payload = job.get("payload") or {}
        try:
            result = hub_post(channel, ws, payload)
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "error": str(exc)[:300]}
        record_post(ws, channel, result, payload, scheduled_for=job.get("run_at"))
        # Clean up the pending file regardless of result so we never retry
        # an already-attempted job (retry policy can be added later).
        try:
            job["_file"].unlink(missing_ok=True)
        except OSError:
            pass
        if result.get("status") == "error":
            stats["failed"] += 1
            stats["errors"].append({"channel": channel, "schedule_id": job.get("id"), "error": result.get("error", "")})
        else:
            stats["dispatched"] += 1
    return stats
