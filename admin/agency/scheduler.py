"""Agent Scheduler — daily/weekly auto-run tasks.

Manages scheduled tasks that run automatically:
  - Daily: onpage check, rank tracking
  - Weekly: site audit, keyword research, report generation
  - Monthly: full audit, schema check

Uses in-memory scheduling (no external dependencies).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# In-memory schedule store
_schedules: dict[str, dict[str, Any]] = {}
# schedule_id -> {
#   id, workspace_id, task_type, params,
#   frequency: "daily"|"weekly"|"monthly",
#   next_run: ISO string, last_run: ISO string,
#   enabled: bool, created_at
# }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_str() -> str:
    return _now().isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULE CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def create_schedule(
    workspace_id: str,
    task_type: str,
    params: dict[str, Any] | None = None,
    frequency: str = "daily",
    enabled: bool = True,
) -> dict[str, Any]:
    """Create a scheduled task."""
    sid = _new_id()
    now = _now()

    if frequency == "daily":
        next_run = now + timedelta(hours=24)
    elif frequency == "weekly":
        next_run = now + timedelta(days=7)
    elif frequency == "monthly":
        next_run = now + timedelta(days=30)
    else:
        next_run = now + timedelta(hours=24)

    schedule = {
        "id": sid,
        "workspace_id": workspace_id,
        "task_type": task_type,
        "params": params or {},
        "frequency": frequency,
        "enabled": enabled,
        "next_run": next_run.isoformat(),
        "last_run": None,
        "run_count": 0,
        "created_at": _now_str(),
    }
    _schedules[sid] = schedule
    logger.info("Created schedule: %s/%s every %s", workspace_id, task_type, frequency)
    return schedule


def get_schedules(workspace_id: str | None = None) -> list[dict[str, Any]]:
    schedules = list(_schedules.values())
    if workspace_id:
        schedules = [s for s in schedules if s["workspace_id"] == workspace_id]
    return sorted(schedules, key=lambda s: s["next_run"])


def delete_schedule(schedule_id: str) -> bool:
    return _schedules.pop(schedule_id, None) is not None


def toggle_schedule(schedule_id: str, enabled: bool) -> dict[str, Any] | None:
    s = _schedules.get(schedule_id)
    if not s:
        return None
    s["enabled"] = enabled
    return s


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def get_due_tasks() -> list[dict[str, Any]]:
    """Get all tasks that are due to run now."""
    now = _now_str()
    due = []
    for s in _schedules.values():
        if s["enabled"] and s["next_run"] <= now:
            due.append(s)
    return due


def run_due_tasks() -> dict[str, Any]:
    """Run all due scheduled tasks.

    This is the main scheduler tick. Call it periodically (e.g., every minute).
    """
    due = get_due_tasks()
    if not due:
        return {"message": "No tasks due", "ran": 0}

    results = []
    for schedule in due:
        task_type = schedule["task_type"]
        workspace_id = schedule["workspace_id"]
        params = schedule["params"]

        logger.info("Running scheduled task: %s for workspace %s", task_type, workspace_id)

        try:
            if task_type == "seo_scan":
                from admin.agency.orchestrator import run_seo_agent_for_workspace
                result = run_seo_agent_for_workspace(workspace_id)
            elif task_type == "agency_seo_monitor":
                from admin.agency.orchestrator import agency_seo_monitor
                result = agency_seo_monitor()
            elif task_type == "workspace_ceo_to_agency":
                from admin.agency.orchestrator import workspace_ceo_to_agency_ceo
                result = workspace_ceo_to_agency_ceo(workspace_id)
            elif task_type == "sba_pipeline_scan":
                from admin.agency.orchestrator import sba_pipeline_scan
                result = sba_pipeline_scan(workspace_id)
            else:
                from admin.tools.seo_tools import execute_seo_tool
                result = execute_seo_tool(task_type, params)

            results.append({
                "schedule_id": schedule["id"],
                "task_type": task_type,
                "workspace_id": workspace_id,
                "status": "success",
                "result": result,
            })
        except Exception as e:
            results.append({
                "schedule_id": schedule["id"],
                "task_type": task_type,
                "workspace_id": workspace_id,
                "status": "failed",
                "error": str(e),
            })
            logger.exception("Scheduled task failed: %s", task_type)

        # Update next_run
        schedule["last_run"] = _now_str()
        schedule["run_count"] += 1
        now = _now()
        if schedule["frequency"] == "daily":
            schedule["next_run"] = (now + timedelta(hours=24)).isoformat()
        elif schedule["frequency"] == "weekly":
            schedule["next_run"] = (now + timedelta(days=7)).isoformat()
        elif schedule["frequency"] == "monthly":
            schedule["next_run"] = (now + timedelta(days=30)).isoformat()

    return {
        "message": f"Ran {len(results)} tasks",
        "ran": len(results),
        "results": results,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-SETUP: Default schedules for a client workspace
# ═══════════════════════════════════════════════════════════════════════════════

def setup_default_schedules(workspace_id: str) -> list[dict[str, Any]]:
    """Create default daily/weekly schedules for a client workspace."""
    schedules = []

    # Daily: onpage check
    schedules.append(create_schedule(
        workspace_id, "onpage_check",
        params={"url": _get_target_url(workspace_id)},
        frequency="daily",
    ))

    # Daily: track rankings (if keywords set)
    kw = _get_keywords(workspace_id)
    if kw:
        for keyword in kw[:3]:
            schedules.append(create_schedule(
                workspace_id, "track_rankings",
                params={"keyword": keyword, "target_url": _get_target_url(workspace_id)},
                frequency="daily",
            ))

    # Weekly: site audit
    schedules.append(create_schedule(
        workspace_id, "site_audit",
        params={"url": _get_target_url(workspace_id), "max_pages": 5},
        frequency="weekly",
    ))

    # Weekly: keyword research
    if kw:
        schedules.append(create_schedule(
            workspace_id, "keyword_research",
            params={"seed_keyword": kw[0]},
            frequency="weekly",
        ))

    # Weekly: generate report
    schedules.append(create_schedule(
        workspace_id, "generate_report",
        params={"url": _get_target_url(workspace_id), "keywords": kw},
        frequency="weekly",
    ))

    # Daily: SEO scan + report up chain
    schedules.append(create_schedule(
        workspace_id, "seo_scan",
        params={},
        frequency="daily",
    ))

    # SBA: lead pipeline scan every 6 hours
    schedules.append(create_schedule(
        workspace_id, "sba_pipeline_scan",
        params={},
        frequency="daily",
    ))

    return schedules


def setup_agency_schedules() -> list[dict[str, Any]]:
    """Create agency-level schedules (monitor quality)."""
    schedules = []

    # Daily: Agency SEO monitors all client reports
    schedules.append(create_schedule(
        "agency", "agency_seo_monitor",
        params={},
        frequency="daily",
    ))

    return schedules


def _get_target_url(workspace_id: str) -> str:
    from admin.agency.orchestrator import get_workspace
    ws = get_workspace(workspace_id)
    if ws:
        return ws.get("settings", {}).get("target_url", "https://example.com")
    return "https://example.com"


def _get_keywords(workspace_id: str) -> list[str]:
    from admin.agency.orchestrator import get_workspace
    ws = get_workspace(workspace_id)
    if ws:
        return ws.get("settings", {}).get("keywords", [])
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# AUTONOMOUS CEO SCHEDULER (L1 report-only) — NEW
#
# Wakes the CEO ITSELF on a timer inside the FastAPI asyncio lifespan (single
# process: NO systemd service / threads / subprocess). Scheduled CEO runs are
# L1 REPORT-ONLY: they think, plan, delegate read-only analysis, and RECORD
# results — they must NOT perform outward-facing actions. Every scheduled task
# message is prefixed with "[SCHEDULED L1 - report only]".
#
# State mirrors to BOTH PocketBase ("ceo_schedules", key "slug") and the JSON
# file store ("ceo_schedules/<slug>.json"), best-effort, copied from
# admin/agency/agent_bus.py::_mirror_message. Appended (not replacing) so the
# legacy SEO scheduler above keeps working.
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio as _asyncio  # noqa: E402  (kept local-friendly; stdlib)
import os as _os  # noqa: E402
import threading as _threading  # noqa: E402
from typing import Optional  # noqa: E402

_COLLECTION = "ceo_schedules"
_KEY_FIELD = "slug"
_PB_FIELDS = {
    "slug": "text",
    "workspace_id": "text",
    "task": "text",
    "interval_minutes": "number",
    "enabled": "bool",
    "next_run_at": "text",
    "last_run_at": "text",
    "last_status": "text",
    "last_summary": "text",
}
_RUN_TIMEOUT_SEC = 300.0
_L1_PREFIX = "[SCHEDULED L1 - report only] "


def _ceo_utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ceo_to_iso(dt: Optional[datetime]) -> str:
    return dt.isoformat() if dt else ""


def _ceo_parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _ceo_normalize(raw: dict, is_create: bool) -> dict:
    """Validate + fill defaults for one CEO schedule dict."""
    slug = str(raw.get("slug", "") or "").strip()
    if not slug:
        raise ValueError("slug is required (unique key)")
    interval = raw.get("interval_minutes")
    try:
        interval = int(interval)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("interval_minutes must be an integer")
    if interval <= 0:
        raise ValueError("interval_minutes must be > 0")
    sched = {
        "slug": slug,
        "workspace_id": str(raw.get("workspace_id", "") or ""),
        "task": str(raw.get("task", "") or ""),
        "interval_minutes": interval,
        "enabled": bool(raw.get("enabled", True)),
        "next_run_at": str(raw.get("next_run_at", "") or ""),
        "last_run_at": str(raw.get("last_run_at", "") or ""),
        "last_status": str(raw.get("last_status", "") or ""),
        "last_summary": str(raw.get("last_summary", "") or ""),
    }
    if is_create and not sched["next_run_at"]:
        sched["next_run_at"] = _ceo_to_iso(
            _ceo_utcnow() + timedelta(minutes=interval)
        )
    return sched


def _ceo_mirror(schedule: dict) -> None:
    """Write a CEO schedule to JSON file + PocketBase (best-effort, never raise)."""
    try:
        from admin.file_store import save_record as _fs_save  # noqa: PLC0415

        _fs_save(_COLLECTION, schedule.get("slug", "unnamed"), schedule)
    except Exception as exc:  # noqa: BLE001
        logger.debug("file mirror (%s) failed (non-fatal): %s", _COLLECTION, exc)
    try:
        from admin.pocketbase_client import get_pb_client  # noqa: PLC0415

        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        pb.upsert_by_key(_COLLECTION, _KEY_FIELD, schedule)
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase mirror (%s) failed (non-fatal): %s", _COLLECTION, exc)


def _ceo_ensure_collection() -> None:
    try:
        from admin.pocketbase_client import get_pb_client  # noqa: PLC0415

        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        pb.ensure_collection(_COLLECTION, _PB_FIELDS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ensure_collection %s failed (non-fatal): %s", _COLLECTION, exc)


def _ceo_load_all() -> list[dict]:
    """Load CEO schedules: PocketBase pull first, JSON file fallback."""
    out: list[dict] = []
    try:
        from admin.pocketbase_client import get_pb_client  # noqa: PLC0415

        pb = get_pb_client()
        if pb and pb.is_configured():
            pb.ensure_collection(_COLLECTION, _PB_FIELDS)
            rows = pb.pull_all(_COLLECTION) or []
            if rows:
                out = [r for r in rows if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase load (%s) failed (non-fatal): %s", _COLLECTION, exc)
    if out:
        return out
    try:
        from admin.file_store import load_all  # noqa: PLC0415

        out = load_all(_COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.debug("file load (%s) failed (non-fatal): %s", _COLLECTION, exc)
    return out


def _ceo_delete_stores(slug: str) -> None:
    try:
        from admin.file_store import delete_record  # noqa: PLC0415

        delete_record(_COLLECTION, slug)
    except Exception as exc:  # noqa: BLE001
        logger.debug("file delete (%s/%s) failed (non-fatal): %s", _COLLECTION, slug, exc)
    try:
        from admin.pocketbase_client import get_pb_client  # noqa: PLC0415

        pb = get_pb_client()
        if pb and pb.is_configured():
            pb.delete_by_key(_COLLECTION, _KEY_FIELD, slug)
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase delete (%s/%s) failed (non-fatal): %s", _COLLECTION, slug, exc)


class CEOScheduler:
    """Asyncio background loop that fires CEO runs on a cron-like cadence.

    Single asyncio task, no threads/subprocesses. Crash-safe: next_run_at is
    bumped and persisted BEFORE the run executes, so a crash mid-run retries on
    the next interval instead of double-firing.
    """

    def __init__(self) -> None:
        self._task: Optional[_asyncio.Task] = None
        self._stop = False
        self._tick_sec = float(_os.getenv("AGENCY_SCHEDULER_TICK_SEC", "30"))
        self._schedules: list[dict] = []
        self._locks: dict[str, _asyncio.Lock] = {}
        self._ceo: Any = None

    async def start(self) -> None:
        if _os.getenv("AGENCY_SCHEDULER_OFF") == "1":
            logger.info("CEO autonomous scheduler disabled via AGENCY_SCHEDULER_OFF=1")
            return
        if self._task is not None and not self._task.done():
            logger.info("CEO autonomous scheduler already running")
            return
        self._stop = False
        self._tick_sec = float(_os.getenv("AGENCY_SCHEDULER_TICK_SEC", "30"))
        _ceo_ensure_collection()
        self._schedules = _ceo_load_all()
        self._task = _asyncio.create_task(self._loop())
        logger.info(
            "CEO autonomous scheduler started (%d schedule(s), tick %.1fs)",
            len(self._schedules),
            self._tick_sec,
        )

    async def stop(self) -> None:
        self._stop = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (Exception, _asyncio.CancelledError):  # noqa: BLE001
                pass
        logger.info("CEO autonomous scheduler stopped")

    async def _loop(self) -> None:
        while not self._stop:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.warning("CEO scheduler tick error (non-fatal): %s", exc)
            try:
                await _asyncio.sleep(self._tick_sec)
            except (_asyncio.CancelledError, Exception):  # noqa: BLE001
                break

    async def _tick(self) -> None:
        now = _ceo_utcnow()
        for sched in list(self._schedules):
            if not sched.get("enabled"):
                continue
            next_at = _ceo_parse_iso(sched.get("next_run_at"))
            if next_at is None or next_at > now:
                continue
            lock = self._locks.setdefault(sched["slug"], _asyncio.Lock())
            if lock.locked():
                continue  # previous run still in flight; next_run_at already bumped
            new_next = now + timedelta(minutes=int(sched.get("interval_minutes", 1)))
            sched["next_run_at"] = _ceo_to_iso(new_next)
            _ceo_mirror(sched)
            _asyncio.create_task(self._run_one(sched, lock))

    async def _run_one(self, sched: dict, lock: _asyncio.Lock) -> None:
        slug = sched["slug"]
        async with lock:
            status = "error"
            summary = ""
            try:
                ceo = self._get_ceo()
                task_msg = _L1_PREFIX + (sched.get("task", "") or "")
                logger.info("SCHED L1 run start: slug=%s", slug)
                try:
                    result = await _asyncio.wait_for(
                        ceo.chat(task_msg), timeout=_RUN_TIMEOUT_SEC
                    )
                except _asyncio.TimeoutError:
                    result = "(L1 scheduled run hit the 300s timeout)"
                if isinstance(result, tuple):
                    text = result[0] if result else ""
                else:
                    text = str(result)
                text = text or ""
                status = "done"
                summary = text[:500]
                logger.info(
                    "SCHED L1 run complete: slug=%s status=done len=%d",
                    slug,
                    len(text),
                )
            except Exception as exc:  # noqa: BLE001
                status = "error"
                summary = str(exc)[:500]
                logger.warning("SCHED L1 run failed: slug=%s error=%s", slug, exc)
            sched["last_run_at"] = _ceo_to_iso(_ceo_utcnow())
            sched["last_status"] = status
            sched["last_summary"] = summary
            _ceo_mirror(sched)

    def _get_ceo(self) -> Any:
        """Lazily build + cache the AgencyCEO singleton (reuse accessor)."""
        if self._ceo is None:
            from admin.agency.ceo import AgencyCEO  # noqa: PLC0415

            self._ceo = AgencyCEO()
        return self._ceo

    async def upsert_schedule(self, raw: dict) -> dict:
        is_create = not any(s["slug"] == raw.get("slug") for s in self._schedules)
        sched = _ceo_normalize(raw, is_create)
        self._schedules = [s for s in self._schedules if s["slug"] != sched["slug"]]
        self._schedules.append(sched)
        _ceo_mirror(sched)
        return sched

    def list_schedules(self) -> list[dict]:
        return [dict(s) for s in self._schedules]

    async def toggle_schedule(self, slug: str, enabled: bool) -> dict:
        for s in self._schedules:
            if s["slug"] == slug:
                s["enabled"] = bool(enabled)
                _ceo_mirror(s)
                return s
        raise KeyError(f"schedule '{slug}' not found")

    async def delete_schedule(self, slug: str) -> bool:
        before = len(self._schedules)
        self._schedules = [s for s in self._schedules if s["slug"] != slug]
        _ceo_delete_stores(slug)
        return before != len(self._schedules)


_scheduler: Optional[CEOScheduler] = None
_sched_lock = _threading.Lock()


def get_scheduler() -> CEOScheduler:
    """Return the process-wide CEO Scheduler (lazy, thread-safe)."""
    global _scheduler
    if _scheduler is None:
        with _sched_lock:
            if _scheduler is None:
                _scheduler = CEOScheduler()
    return _scheduler
