"""Minimum viable event-driven CEO autonomy control plane.

The control plane is deliberately small and dependency-free beyond the existing
FastAPI/SQLite stack. It owns only internal coordination: events are durable,
decisions are auditable, and delegated work is bounded. External actions are
represented as approvals and are never executed by this loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from admin.llm_throttle import (
    circuit_open as llm_circuit_open,
    circuit_retry_after as llm_retry_after,
    guard as llm_guard,
    record_rate_limit as llm_record_rate_limit,
    record_success as llm_record_success,
    snapshot as llm_guard_snapshot,
)
from admin.persistence import get_workspace_db, row_to_dict
from admin.workspace.llm_output import unusable_output_reason

logger = logging.getLogger(__name__)

_EXTERNAL_ACTION_TYPES = frozenset(
    {"email", "publication", "publish", "spend", "contract", "external"}
)
_EXTERNAL_ACTION_KEYWORDS = (
    "email", "send", "publish", "post", "spend", "payment", "contract", "agreement", "outreach"
)


def _is_external_action(value: Any) -> bool:
    # Determines if an action type is considered external and thus requires human approval.
    # External actions are those explicitly listed or containing external keywords.
    # Internal actions (e.g., prefixed with 'internal_') are excluded.
    normalized = str(value or "").lower().strip()
    return normalized in _EXTERNAL_ACTION_TYPES or any(
        keyword in normalized for keyword in _EXTERNAL_ACTION_KEYWORDS
    )

# Helper to decide whether to skip human approval for internal actions.
def _skip_approval_for_internal(action_type: str) -> bool:
    """Return True if the action is internal and should bypass approval.
    Internal actions are identified by the 'internal_' prefix or any action that
    is not considered external by _is_external_action. This ensures the CEO loop
    remains hands‑off for routine internal prospecting tasks.
    """
    normalized = str(action_type or "").lower().strip()
    # Explicit internal prefix overrides external keyword detection.
    if normalized.startswith("internal_"):
        return True
    # If not classified as external, treat as internal.
    return not _is_external_action(normalized)


def _normalize_external_action(value: Any) -> str:
    normalized = str(value or "external").lower().strip()[:100]
    return normalized if _is_external_action(normalized) else "external"


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "secret",
        "token",
        "authorization",
        "cookie",
        "email",
        "phone",
    }
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|password|secret|token|authorization)\s*[:=]\s*[^\s,}]+"
)


_PLAYBOOK_CACHE: str | None = None


def _load_ceo_playbook() -> str:
    """Load the CEO operating doctrine once. Missing file is survivable."""
    global _PLAYBOOK_CACHE
    if _PLAYBOOK_CACHE is not None:
        return _PLAYBOOK_CACHE
    path = Path(__file__).resolve().parent / "ceo_playbook.md"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("CEO playbook unreadable at %s: %s", path, _safe_error(exc))
        text = (
            "You are a co-founder CEO of a B2B agency. Prefer work already in "
            "the pipeline over inventing new work. Dispatch to a named agent, "
            "never do the work yourself. Internal work only: no email, no "
            "spend, no publishing, no contracts. It is correct to conclude that "
            "nothing should happen right now."
        )
    _PLAYBOOK_CACHE = text
    return _PLAYBOOK_CACHE


# Agent slugs the autonomous thinker is allowed to dispatch to. This is the
# real roster: data/support agents that feed the doers, plus the doers
# themselves. Anything else the model proposes is dropped rather than routed.
_THINK_AGENTS = frozenset({
    # data + support layer
    "analyzing", "analytics", "memory",
    # doers
    "ceo", "sba", "seo", "content", "social", "ads", "website",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _safe_value(value: Any) -> Any:
    """Return an API-safe owned copy without credential-shaped values."""
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                safe[key] = "[REDACTED]"
            else:
                safe[key] = _safe_value(item)
        return safe
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_ASSIGNMENT.sub(
            lambda match: f"{match.group(1)}=[REDACTED]", value
        )[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4000]


def _safe_error(exc: Exception) -> str:
    return _safe_value(str(exc))[:500]


def _json_dumps(value: Any) -> str:
    return json.dumps(_safe_value(value), ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return {} if default is None else default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {} if default is None else default
    return _safe_value(parsed)


def _extract_json_object(text: str | None) -> dict[str, Any] | None:
    """Pull the first complete JSON object out of a model reply.

    The previous approach was content.find("{") to content.rfind("}"). That
    fails on the replies we now actually get: a fenced block whose prose
    mentions a brace before the object, a trailing brace in a sentence, or a
    chatty model that never emits an object at all. Scanning for the first
    balanced object, while tracking string literals and escapes, handles the
    fence and the trailing prose; returning None for genuinely absent JSON lets
    the caller retry instead of silently acting on {}.
    """
    if not text:
        return None
    candidates: list[str] = []

    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(text)

    for blob in candidates:
        start = blob.find("{")
        while start >= 0:
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(blob)):
                char = blob[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        parsed = _json_loads(blob[start:index + 1], default=None)
                        if isinstance(parsed, dict) and parsed:
                            return parsed
                        break
            start = blob.find("{", start + 1)
    return None


def _default_state() -> dict[str, Any]:
    return {
        "running": False,
        "disabled": False,
        "started_at": None,
        "stopped_at": None,
        "last_tick_at": None,
        "last_event_id": None,
        "tick_count": 0,
        "last_error": "",
    }


async def emit_event(
    event_type: str,
    workspace_id: str = "",
    source: str = "system",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Persist one inbox event without making existing callers fail."""
    if not event_type or not isinstance(event_type, str):
        return None
    event = {
        "id": _id("ceo_evt"),
        "event_type": event_type[:100],
        "workspace_id": str(workspace_id or "")[:200],
        "source": str(source or "system")[:100],
        "payload": _safe_value(payload or {}),
        "status": "pending",
        "created_at": _now(),
        "processed_at": None,
        "error": "",
    }
    try:
        db = await get_workspace_db()
        await db.execute(
            "INSERT INTO ceo_autonomy_events "
            "(id, event_type, workspace_id, source, payload, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (
                event["id"],
                event["event_type"],
                event["workspace_id"],
                event["source"],
                _json_dumps(event["payload"]),
                event["created_at"],
            ),
        )
        await db.commit()
    except Exception as exc:  # existing behavior must survive persistence issues
        logger.debug("CEO autonomy event persistence failed: %s", _safe_error(exc))
        return None
    return event


def emit_event_sync(
    event_type: str,
    workspace_id: str = "",
    source: str = "system",
    payload: dict[str, Any] | None = None,
) -> None:
    """Sync compatibility wrapper used by existing synchronous store helpers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(emit_event(event_type, workspace_id, source, payload))
        except Exception as exc:
            logger.debug("CEO autonomy sync event failed: %s", _safe_error(exc))
        return
    loop.create_task(emit_event(event_type, workspace_id, source, payload))


async def _load_state() -> dict[str, Any]:
    try:
        db = await get_workspace_db()
        cursor = await db.execute(
            "SELECT value FROM ceo_autonomy_state WHERE key='control_plane'"
        )
        row = await cursor.fetchone()
        if not row:
            return _default_state()
        state = _json_loads(row["value"], {})
        base = _default_state()
        base.update(state if isinstance(state, dict) else {})
        return base
    except Exception:
        return _default_state()


async def _save_state(patch: dict[str, Any]) -> dict[str, Any]:
    state = await _load_state()
    state.update(patch)
    try:
        db = await get_workspace_db()
        await db.execute(
            "INSERT OR REPLACE INTO ceo_autonomy_state (key, value, updated_at) "
            "VALUES ('control_plane', ?, ?)",
            (_json_dumps(state), _now()),
        )
        await db.commit()
    except Exception as exc:
        logger.debug("CEO autonomy state persistence failed: %s", _safe_error(exc))
    return state


async def _reset_interrupted_work() -> None:
    """Make crash-left work eligible for one clean retry after restart."""
    try:
        db = await get_workspace_db()
        await db.execute(
            "UPDATE ceo_autonomy_events "
            "SET status='pending', processed_at=NULL, error='' "
            "WHERE status='processing'"
        )
        await db.execute(
            "UPDATE ceo_autonomy_tasks "
            "SET status='queued', started_at=NULL, finished_at=NULL, "
            "error='resumed after restart' WHERE status IN ('running', 'queued')"
        )
        await db.commit()
    except Exception as exc:
        logger.debug("CEO autonomy restart reset failed: %s", _safe_error(exc))


async def list_events(
    limit: int = 100,
    status: str | None = None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if workspace_id:
        clauses.append("workspace_id=?")
        params.append(workspace_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        db = await get_workspace_db()
        cursor = await db.execute(
            f"SELECT * FROM ceo_autonomy_events{where} "
            "ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            item = row_to_dict(row)
            item["payload"] = _json_loads(item.get("payload"), {})
            result.append(_safe_value(item))
        return result
    except Exception:
        return []


async def list_decisions(
    limit: int = 100,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    where = " WHERE workspace_id=?" if workspace_id else ""
    params: list[Any] = [workspace_id] if workspace_id else []
    try:
        db = await get_workspace_db()
        cursor = await db.execute(
            f"SELECT * FROM ceo_autonomy_decisions{where} "
            "ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            item = row_to_dict(row)
            item["result"] = _json_loads(item.get("result"), {})
            result.append(_safe_value(item))
        return result
    except Exception:
        return []


async def list_approvals(
    limit: int = 100,
    status: str | None = None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if workspace_id:
        clauses.append("workspace_id=?")
        params.append(workspace_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        db = await get_workspace_db()
        cursor = await db.execute(
            f"SELECT * FROM ceo_autonomy_approvals{where} "
            "ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            item = row_to_dict(row)
            item["payload"] = _json_loads(item.get("payload"), {})
            result.append(_safe_value(item))
        return result
    except Exception:
        return []


async def list_tasks(
    limit: int = 100,
    status: str | None = None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if workspace_id:
        clauses.append("workspace_id=?")
        params.append(workspace_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        db = await get_workspace_db()
        cursor = await db.execute(
            f"SELECT * FROM ceo_autonomy_tasks{where} "
            "ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            item = row_to_dict(row)
            item["result"] = _json_loads(item.get("result"), "")
            result.append(_safe_value(item))
        return result
    except Exception:
        return []


async def decide_approval(
    approval_id: str,
    decision: str,
    reason: str = "",
    decided_by: str = "owner",
) -> dict[str, Any] | None:
    """Record an owner decision; never execute the approved external action."""
    normalized = str(decision or "").lower()
    if normalized not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    try:
        db = await get_workspace_db()
        cursor = await db.execute(
            "SELECT * FROM ceo_autonomy_approvals WHERE id=?", (str(approval_id),)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        current_status = str(dict(row).get("status") or "")
        if current_status != "pending":
            return None
        decided_at = _now()
        await db.execute(
            "UPDATE ceo_autonomy_approvals SET status=?, decided_at=?, "
            "decided_by=?, decision_reason=? WHERE id=?",
            (
                normalized,
                decided_at,
                str(decided_by or "owner")[:100],
                str(reason or "")[:1000],
                str(approval_id),
            ),
        )
        await db.commit()
        item = row_to_dict(row)
        item["payload"] = _json_loads(item.get("payload"), {})
        item.update({
            "status": normalized,
            "decided_at": decided_at,
            "decided_by": str(decided_by or "owner")[:100],
            "decision_reason": str(reason or "")[:1000],
        })
        await emit_event(
            "approval.decided",
            workspace_id=str(item.get("workspace_id") or ""),
            source="owner",
            payload={
                "approval_id": item["id"],
                "action_type": item.get("action_type", ""),
                "decision": normalized,
            },
        )
        return _safe_value(item)
    except Exception as exc:
        logger.debug("CEO autonomy approval decision failed: %s", _safe_error(exc))
        return None


async def heartbeat(workspace_id: str = "ws_agency", reason: str = "manual") -> dict[str, Any] | None:
    """Insert a bounded heartbeat event for a manual or periodic CEO wake-up."""
    return await emit_event(
        "heartbeat",
        workspace_id=workspace_id,
        source="api",
        payload={"reason": str(reason or "manual")[:200]},
    )


async def request_approval(
    action_type: str,
    description: str,
    workspace_id: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create a human gate; this function never executes the action.
    Skips creation entirely for internal actions (e.g., prefixed 'internal_' or not external).
    """
    # Skip approval for internal actions to keep CEO autonomy hands‑off for prospecting.
    if _skip_approval_for_internal(action_type):
        logger.debug("Skipping approval for internal action %s: %s", action_type, description[:100])
        return None
    action_type = _normalize_external_action(action_type)
    approval = {
        "id": _id("ceo_appr"),
        "workspace_id": str(workspace_id or "")[:200],
        "action_type": action_type,
        "description": str(description or "External action requested")[:1000],
        "payload": _safe_value(payload or {}),
        "status": "pending",
        "created_at": _now(),
        "decided_at": None,
        "decided_by": "",
        "decision_reason": "",
    }
    try:
        db = await get_workspace_db()
        await db.execute(
            "INSERT INTO ceo_autonomy_approvals "
            "(id, workspace_id, action_type, description, payload, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (
                approval["id"],
                approval["workspace_id"],
                approval["action_type"],
                approval["description"],
                _json_dumps(approval["payload"]),
                approval["created_at"],
            ),
        )
        await db.commit()
    except Exception as exc:
        logger.debug("CEO autonomy approval persistence failed: %s", _safe_error(exc))
        return None
    return approval


async def _recent_decision_summary(limit: int = 8) -> list[dict[str, Any]]:
    """What the CEO already tried, so it can stop repeating a failed move.

    An autonomous loop that cannot see its own history cannot learn. This is
    deliberately cheap and deliberately lossy: only the action, the first part
    of the rationale and the outcome shape, because the thinker prompt is
    already long and a full history would crowd out the playbook.

    Failures are listed first and marked, because the case that matters is the
    one where the same action keeps being proposed after it kept failing.
    """
    try:
        from admin.persistence import get_db

        db = get_db()
        rows = await db.fetchall(
            "SELECT action, rationale, created_at FROM ceo_autonomy_decisions "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("CEO history unavailable: %s", _safe_error(exc))
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        action = str(r.get("action") or "")
        rationale = str(r.get("rationale") or "")
        out.append({
            "action": action,
            "when": str(r.get("created_at") or "")[:16],
            "what": rationale[:180],
            # "review_required" and "observe" are bookkeeping, not attempts at
            # real work. Marking them would fill the history with noise and hide
            # the one repetition that actually matters.
            "was_real_work": action in ("delegate", "bootstrap_prospecting"),
        })
    return out


async def _record_decision(
    event_id: str,
    workspace_id: str,
    action: str,
    rationale: str,
    result: dict[str, Any],
) -> str:
    decision_id = _id("ceo_dec")
    try:
        db = await get_workspace_db()
        await db.execute(
            "INSERT INTO ceo_autonomy_decisions "
            "(id, event_id, workspace_id, action, rationale, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                event_id,
                workspace_id,
                action[:120],
                rationale[:2000],
                _json_dumps(result),
                _now(),
            ),
        )
        await db.commit()
    except Exception as exc:
        logger.debug("CEO autonomy decision persistence failed: %s", _safe_error(exc))
    return decision_id


async def _mark_event_done(event_id: str) -> None:
    try:
        db = await get_workspace_db()
        await db.execute(
            "UPDATE ceo_autonomy_events SET status='done', processed_at=?, error='' "
            "WHERE id=?",
            (_now(), event_id),
        )
        await db.commit()
    except Exception as exc:
        logger.debug("CEO autonomy event completion failed: %s", _safe_error(exc))


async def _mark_event_error(event_id: str, exc: Exception) -> None:
    try:
        db = await get_workspace_db()
        await db.execute(
            "UPDATE ceo_autonomy_events SET status='error', processed_at=?, error=? "
            "WHERE id=?",
            (_now(), _safe_error(exc), event_id),
        )
        await db.commit()
    except Exception:
        pass


class CEOAutonomy:
    """Bounded asyncio control loop and API-facing control-plane operations."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = False
        self._tick_lock = asyncio.Lock()
        self._workers: set[asyncio.Task] = set()
        self._tick_sec = max(1.0, float(os.getenv("AGENCY_CEO_AUTONOMY_TICK_SEC", "15")))
        self._max_events = max(1, min(int(os.getenv("AGENCY_CEO_AUTONOMY_MAX_EVENTS", "3")), 10))
        self._max_workers = max(1, min(int(os.getenv("AGENCY_CEO_AUTONOMY_MAX_WORKERS", "2")), 4))
        self._agent_timeout = max(
            5.0, float(os.getenv("AGENCY_CEO_AUTONOMY_AGENT_TIMEOUT_SEC", "120"))
        )
        self._heartbeat_sec = max(
            5.0, float(os.getenv("AGENCY_CEO_AUTONOMY_HEARTBEAT_SEC", "60"))
        )
        self._last_heartbeat = 0.0

    async def start(self) -> None:
        if os.getenv("AGENCY_CEO_AUTONOMY_OFF", "0") == "1":
            await _save_state({"running": False, "disabled": True, "stopped_at": _now()})
            logger.info("CEO autonomy control plane disabled")
            return
        if self._task is not None and not self._task.done():
            return
        await _reset_interrupted_work()
        self._stop = False
        self._tick_sec = max(1.0, float(os.getenv("AGENCY_CEO_AUTONOMY_TICK_SEC", "15")))
        self._max_events = max(1, min(int(os.getenv("AGENCY_CEO_AUTONOMY_MAX_EVENTS", "3")), 10))
        self._max_workers = max(1, min(int(os.getenv("AGENCY_CEO_AUTONOMY_MAX_WORKERS", "2")), 4))
        self._agent_timeout = max(
            5.0, float(os.getenv("AGENCY_CEO_AUTONOMY_AGENT_TIMEOUT_SEC", "120"))
        )
        await _save_state(
            {
                "running": True,
                "disabled": False,
                "started_at": (await _load_state()).get("started_at") or _now(),
                "stopped_at": None,
                "last_error": "",
            }
        )
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "CEO autonomy control plane started (tick %.1fs, max events %d)",
            self._tick_sec,
            self._max_events,
        )

    async def stop(self) -> None:
        self._stop = True
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except (Exception, asyncio.CancelledError):
                pass
        workers = list(self._workers)
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()
        await _save_state({"running": False, "stopped_at": _now()})
        logger.info("CEO autonomy control plane stopped")

    async def _loop(self) -> None:
        while not self._stop:
            try:
                now = time.monotonic()
                if now - self._last_heartbeat >= self._heartbeat_sec:
                    self._last_heartbeat = now
                    await heartbeat(reason="periodic")
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _save_state({"last_error": _safe_error(exc), "last_tick_at": _now()})
                logger.warning("CEO autonomy tick failed (non-fatal): %s", _safe_error(exc))
            await asyncio.sleep(self._tick_sec)

    async def tick(self) -> dict[str, Any]:
        """Process a bounded batch; safe to call from the loop or an API request."""
        async with self._tick_lock:
            if self._stop:
                return {"status": "stopped", "claimed": 0}
            events = await self._claim_events(self._max_events)
            if not events:
                state = await _load_state()
                state["last_tick_at"] = _now()
                state["tick_count"] = int(state.get("tick_count", 0)) + 1
                # Idle is not a reason to stay idle. Emit a think event so the
                # next tick has the CEO choose its own next move, on a cadence
                # so a quiet agency does not burn an LLM call every 15 seconds.
                seeded = False
                try:
                    interval = float(os.getenv("AGENCY_CEO_THINK_INTERVAL_SEC", "300"))
                    last_think = float(state.get("last_think_at", 0.0) or 0.0)
                    now = time.time()
                    if now - last_think >= interval:
                        state["last_think_at"] = now
                        state["thinks_seeded"] = int(state.get("thinks_seeded", 0) or 0) + 1
                        await emit_event(
                            "ceo.think",
                            workspace_id=self._fallback_workspace(self._overview()),
                            source="ceo_autonomy",
                            payload={"reason": "no pending events; CEO deciding next action"},
                        )
                        seeded = True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    # Never let the self-directed step take the loop down.
                    logger.warning("CEO think seed failed: %s", _safe_error(exc))
                await _save_state(state)
                return {"status": "ok", "claimed": 0, "succeeded": 0, "failed": 0,
                        "seeded_think": seeded}

            await self._prune_workers()
            available = max(1, self._max_workers - len(self._workers))
            batch = events[:available]
            workers = [asyncio.create_task(self._process_event(event)) for event in batch]
            self._workers.update(workers)
            outcomes = await asyncio.gather(*workers, return_exceptions=True)
            succeeded = sum(1 for outcome in outcomes if outcome is True)
            failed = len(outcomes) - succeeded
            state = await _load_state()
            state["last_tick_at"] = _now()
            state["last_event_id"] = events[-1]["id"]
            state["tick_count"] = int(state.get("tick_count", 0)) + 1
            if failed:
                state["last_error"] = f"{failed} event(s) failed in tick"
            await _save_state(state)
            return {
                "status": "ok",
                "claimed": len(batch),
                "succeeded": succeeded,
                "failed": failed,
            }

    async def _claim_events(self, limit: int) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        try:
            db = await get_workspace_db()
            cursor = await db.execute(
                "SELECT * FROM ceo_autonomy_events WHERE status='pending' "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                event = row_to_dict(row)
                event["payload"] = _json_loads(event.get("payload"), {})
                update = await db.execute(
                    "UPDATE ceo_autonomy_events SET status='processing' WHERE id=? "
                    "AND status='pending'",
                    (event["id"],),
                )
                if getattr(update, "rowcount", 1):
                    claimed.append(_safe_value(event))
            await db.commit()
        except Exception as exc:
            logger.debug("CEO autonomy event claim failed: %s", _safe_error(exc))
        return claimed

    async def _prune_workers(self) -> None:
        self._workers = {worker for worker in self._workers if not worker.done()}

    async def _process_event(self, event: dict[str, Any]) -> bool:
        try:
            decision = await self._decide(event)
            await _record_decision(
                event.get("id", ""),
                event.get("workspace_id", ""),
                decision["action"],
                decision.get("rationale", ""),
                decision.get("result", {}),
            )
            if decision.get("approval"):
                approval = await request_approval(
                    decision["approval"]["action_type"],
                    decision["approval"].get("description", "External action requested"),
                    event.get("workspace_id", ""),
                    decision["approval"].get("payload", {}),
                )
                if not approval:
                    raise RuntimeError("approval could not be persisted")
                await _mark_event_done(event.get("id", ""))
                return True
            if decision.get("delegate_multi"):
                dm = decision["delegate_multi"]
                agents = dm.get("agents", []) or ["sba"]
                action_type = dm.get("action_type", "internal_analysis")
                task = dm.get("task", "")
                workspaces = dm.get("workspaces", []) or []
                if not workspaces:
                    workspaces = ["ws_agency"]
                # Fan out: one parallel multi-agent orchestration per workspace.
                orch_ws = decision.get("workspace_id", "")
                await asyncio.gather(
                    *(
                        self._run_orchestration(
                            workspace_id=ws or orch_ws or "ws_agency",
                            agents=agents,
                            action_type=action_type,
                            task=task,
                        )
                        for ws in workspaces
                    )
                )
            if decision.get("delegate"):
                await self._run_task(
                    workspace_id=decision.get("workspace_id", ""),
                    agent_type=decision["delegate"]["agent_type"],
                    action_type=decision["delegate"].get("action_type", "internal_analysis"),
                    task=decision["delegate"]["task"],
                )
            await _mark_event_done(event.get("id", ""))
            return True
        except asyncio.CancelledError:
            await _mark_event_error(event.get("id", ""), RuntimeError("cancelled"))
            raise
        except Exception as exc:
            await _mark_event_error(event.get("id", ""), exc)
            logger.warning("CEO autonomy event %s failed: %s", event.get("id"), _safe_error(exc))
            return False

    async def _think(self, overview: dict[str, Any]) -> dict[str, Any]:
        """Decide the next move by looking at agency state, not at an event.

        _decide() is a lookup table keyed on event_type. It can only react to
        events that something else already emitted, and the only place it
        originates work itself is gated on `total_leads == 0`. So once the
        agency had leads but no inbound events -- which is the normal state of
        a business that already has a pipeline -- the CEO had nothing to key
        on and idled forever. It reported a healthy heartbeat and a rising
        tick_count while every agent sat in standby with last_wake = null.

        This is the missing step: look at what is actually true right now and
        choose the single most valuable internal action.

        The model proposes; this method disposes. Its reply is untrusted
        input: parsed as JSON, matched against an allowlist of real agent
        slugs, and forced back into the internal-action contract that
        _process_event already enforces. An unrecognised agent, an external
        action, or an unparseable reply degrades to `observe` rather than
        acting. Approval routing is untouched, so the model cannot widen its
        own permissions.
        """
        summary = overview.get("summary", {}) if isinstance(overview, dict) else {}
        try:
            from admin.config import settings
        except Exception as exc:  # noqa: BLE001
            return {"action": "observe", "rationale": f"settings unavailable: {_safe_error(exc)}",
                    "result": {"overview": summary}}

        base = (settings.WORKSPACE_API_BASE or "").strip()
        key = (settings.WORKSPACE_API_KEY or "").strip()
        if not base or not key:
            # Not an error worth spamming every tick -- say it once per tick
            # in the rationale so /api/health shows why the agency is idle.
            return {
                "action": "observe",
                "rationale": (
                    "CEO has no thinker: WORKSPACE_API_BASE and WORKSPACE_API_KEY are "
                    "both required for the CEO to decide its own next move."
                ),
                "result": {"overview": summary},
            }

        # An open breaker means the gateway is throttling us. Spending a call
        # here would be refused locally anyway, so degrade to observe and say
        # why, rather than retrying into the throttle that caused it.
        if llm_circuit_open():
            wait = llm_retry_after()
            logger.warning(
                "CEO thinker skipped: LLM circuit open for another %.0fs", wait)
            return {
                "action": "observe",
                "rationale": (
                    "LLM gateway is rate limiting the agency; the thinker was "
                    f"skipped for another {wait:.0f}s while the circuit is open."
                ),
                "result": {"overview": summary},
            }

        # Recent history, so the CEO can stop repeating a move that already
        # failed. Without this the loop has no memory of its own attempts: it
        # saw an empty pipeline, dispatched prospecting, the sub-agents timed
        # out, and on the next tick saw an empty pipeline again and dispatched
        # prospecting again. Nineteen consecutive identical "successful" runs,
        # zero leads.
        recent = await _recent_decision_summary()

        prompt = (
            _load_ceo_playbook()
            + "\n\n---\n\n"
            "You are the CEO of TAGS Agency, a B2B agency that sells AI agents "
            "to local service businesses. Render is the office you live in and "
            "your agents are your staff. Nobody will hand you work.\n\n"
            f"CURRENT STATE OF THE AGENCY:\n{_json_dumps(summary)}\n\n"
            f"WHAT YOU ALREADY TRIED RECENTLY:\n{_json_dumps(recent)}\n\n"
            "Decide the single highest-value INTERNAL action to take right now, "
            "then dispatch it to exactly one agent.\n"
            "Choose an agent from: " + ", ".join(sorted(_THINK_AGENTS)) + "\n\n"
            "Before you dispatch, check the history above. If you are about to "
            "repeat an action that already failed, do NOT repeat it. Either "
            "choose a genuinely different action, or return:\n"
            '{"action":"observe","rationale":"<what already failed, and what '
            'must change before it is worth retrying>","agent_type":"",'
            '"task":""}\n\n'
            'Reply with JSON only, no prose:\n'
            '{"action":"delegate","rationale":"one sentence, in your own words",'
            '"agent_type":"<agent>","task":"<specific, checkable instruction>"}'
        )

        try:
            import httpx

            timeout = float(os.getenv("AGENCY_CEO_THINK_TIMEOUT_SEC", "60"))
            last_content = {"text": ""}

            async def _call(messages):
                # One concurrency slot per upstream call. Without it the thinker
                # and a fan-out of agent calls enter the gateway in the same
                # instant, which is what earns the 429.
                async with llm_guard():
                    resp = httpx.post(
                        base.rstrip("/") + "/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={
                            "model": settings.WORKSPACE_AGENT_MODEL or "auto",
                            "messages": messages,
                            # Free routers serve small chatty models. At 400 tokens a
                            # model restating the brief ("The user wants me to act
                            # as a CEO...") runs out of budget before the JSON
                            # arrives, and finish_reason comes back "length" with
                            # nothing parseable. Measured against the live gateway:
                            # 400 tokens truncated, 1000 gave 5 of 5 clean JSON.
                            "max_tokens": int(os.getenv("AGENCY_CEO_THINK_MAX_TOKENS", "1000")),
                            "temperature": 0.4,
                        },
                        timeout=timeout,
                    )
                if resp.status_code == 429:
                    # Register the throttle before deciding what to do next, so
                    # the breaker reflects it even if this attempt gives up.
                    llm_record_rate_limit(
                        RuntimeError(f"HTTP 429 from {base} (Retry-After="
                                     f"{resp.headers.get('retry-after')})"))
                    return None, "HTTP 429"
                if resp.status_code != 200:
                    return None, f"HTTP {resp.status_code}"
                llm_record_success()
                content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
                last_content["text"] = content
                found = _extract_json_object(content)
                return (found, None) if found else (None, "no JSON object")

            parsed, why = await _call([{"role": "user", "content": prompt}])
            if parsed is None and why != "HTTP 429":
                # Free routers serve small chatty models, and a cheap model
                # narrating ("The user is simply asking me to say...") is a
                # normal outcome, not a fault. Ask once more, handing back the
                # offending text so the model has something concrete to fix,
                # and ask for the bare object with no fence.
                logger.info("CEO thinker first attempt unusable (%s); retrying", why)
                parsed, why = await _call([
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": last_content["text"]},
                    {"role": "user", "content":
                        "That was not valid JSON. Reply with the JSON object alone. "
                        "No prose, no explanation, no markdown fence. It must "
                        "start with { and end with }."},
                ])
            if parsed is None:
                if why == "HTTP 429":
                    # Do NOT spend a second call here: the upstream is already
                    # throttling us and the breaker is now open. Say so.
                    logger.warning(
                        "CEO thinker rate limited upstream; breaker open, no second attempt")
                return {
                    "action": "observe",
                    "rationale": f"CEO thinker returned {why}",
                    "result": {"overview": summary},
                }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("CEO thinker failed: %s", _safe_error(exc))
            return {
                "action": "observe",
                "rationale": f"CEO thinker unavailable: {_safe_error(exc)}",
                "result": {"overview": summary},
            }

        agent_type = str(parsed.get("agent_type", "") or "").strip().lower()
        task = str(parsed.get("task", "") or "").strip()
        rationale = str(parsed.get("rationale", "") or "").strip()
        action = str(parsed.get("action", "") or "").strip().lower()

        # Validate rather than trust. An unknown agent or an external action
        # is dropped here, not downstream.
        if agent_type not in _THINK_AGENTS:
            return {
                "action": "observe",
                "rationale": f"CEO proposed unknown agent '{agent_type[:40]}'; not dispatched",
                "result": {"overview": summary, "proposed": parsed},
            }
        if not task or len(task) > 4000:
            return {
                "action": "observe",
                "rationale": "CEO proposed an unusable task; not dispatched",
                "result": {"overview": summary, "proposed": parsed},
            }
        if action not in ("delegate", "delegate_multi"):
            action = "delegate"

        return {
            "action": action,
            "rationale": f"CEO self-directed: {rationale or 'highest-value next action'}",
            "workspace_id": self._fallback_workspace(overview),
            "delegate": {
                "agent_type": agent_type,
                # Forced internal regardless of what the model asked for.
                "action_type": "internal_analysis",
                "task": (
                    f"[CEO assigned this autonomously] {task} "
                    "Internal analysis only: do not send email, contact anyone, "
                    "spend money, publish, or create a contract."
                ),
            },
            "result": {"overview": summary},
        }

    async def _decide(self, event: dict[str, Any]) -> dict[str, Any]:
        """Inspect the agency overview and choose one bounded next action."""
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        overview = self._overview()
        summary = overview.get("summary", {}) if isinstance(overview, dict) else {}
        workspace_id = str(payload.get("workspace_id") or event.get("workspace_id") or "")
        proposed = str(payload.get("action_type") or payload.get("proposed_action") or "")
        if _is_external_action(proposed) and not _skip_approval_for_internal(proposed):
            return {
                "action": "approval_required",
                "rationale": "External actions require explicit human approval.",
                "result": {"overview": summary},
                "approval": {
                    "action_type": _normalize_external_action(proposed),
                    "description": str(payload.get("description") or "External action requested"),
                    "payload": payload,
                },
            }
        if event_type == "ceo.think":
            # The self-directed step: no inbound event triggered this, the CEO
            # asked itself what to work on next.
            decision = await self._think(overview)
            try:
                await _save_state({
                    "last_think_at": time.time(),
                    "last_think_action": str(decision.get("action", "") or ""),
                    "last_think_rationale": str(decision.get("rationale", "") or "")[:500],
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("CEO autonomy could not record think outcome: %s", _safe_error(exc))
            return decision
        if event_type == "lead.created":
            return {
                "action": "delegate_lead_qualification",
                "rationale": "A new lead entered the inbox; ask SBA for a no-contact qualification review.",
                "workspace_id": workspace_id or self._fallback_workspace(overview),
                "delegate": {
                    "agent_type": "sba",
                    "action_type": "internal_analysis",
                    "task": (
                        f"Qualify lead {payload.get('lead_id', '')} using the persisted lead summary. "
                        "Return a recommendation only. Do not contact the lead, send email, spend money, "
                        "publish content, or create a contract."
                    ),
                },
                "result": {"overview": summary},
            }
        if event_type == "handoff.created":
            return {
                "action": "delegate_handoff_review",
                "rationale": "A handoff needs CEO review before workspace creation.",
                "workspace_id": workspace_id or self._fallback_workspace(overview),
                "delegate": {
                    "agent_type": "sba",
                    "action_type": "internal_analysis",
                    "task": (
                        f"Review SBA handoff {payload.get('handoff_id', '')} and recommend the next "
                        "internal step. Do not create a workspace, contact the lead, send email, "
                        "publish content, spend money, or create a contract."
                    ),
                },
                "result": {"overview": summary},
            }
        if event_type == "agent.error":
            return {
                "action": "delegate_error_triage",
                "rationale": "An agent error needs bounded triage before CEO escalation.",
                "workspace_id": workspace_id or self._fallback_workspace(overview),
                "delegate": {
                    "agent_type": str(payload.get("routed_to") or "analyzing")[:80],
                    "action_type": "internal_analysis",
                    "task": (
                        f"Triple-check and explain error {payload.get('error_id', '')} of type "
                        f"{payload.get('error_type', 'unknown')}. Return root cause and a safe fix plan; "
                        "do not perform external actions."
                    ),
                },
                "result": {"overview": summary},
            }
        if event_type == "agent.output":
            return {
                "action": "review_required",
                "rationale": "Agent output is persisted and awaits CEO review.",
                "result": {
                    "overview": summary,
                    "output_id": payload.get("output_id", ""),
                    "agent_type": payload.get("agent_type", ""),
                },
            }
        if event_type == "agent.review":
            return {
                "action": "review_recorded",
                "rationale": "CEO review verdict is already persisted.",
                "result": {"overview": summary, "verdict": payload.get("verdict", "")},
            }
        if event_type == "heartbeat":
            if int(summary.get("pending_handoffs", 0) or 0) > 0:
                return {
                    "action": "delegate_handoff_review",
                    "rationale": "Heartbeat found a pending SBA handoff.",
                    "workspace_id": self._fallback_workspace(overview),
                    "delegate": {
                        "agent_type": "sba",
                        "action_type": "internal_analysis",
                        "task": (
                            "Review the oldest pending SBA handoff and recommend the next internal "
                            "step. Do not create a workspace or perform any external action."
                        ),
                    },
                    "result": {"overview": summary},
                }
            if int(summary.get("pending_reviews", 0) or 0) > 0 and int(
                summary.get("total_leads", 0) or 0
            ) > 0:
                return {
                    "action": "review_required",
                    "rationale": "Heartbeat found agent output awaiting CEO review.",
                    "result": {"overview": summary},
                }
            # Bootstrap: with no leads in the pipeline the agency is idle, so the
            # CEO itself kicks off outbound prospecting on a bounded cadence.
            # This keeps the loop event-driven (no scheduler dependency) while
            # ensuring agency income work begins without human prompting. Runs
            # SBA + Content + Website agents IN PARALLEL (multi-agent mode).
            try:
                if int(summary.get("total_leads", 0) or 0) == 0:
                    state = await _load_state()
                    interval = float(os.getenv("AGENCY_PROSPECT_INTERVAL_SEC", "120"))
                    last = float(state.get("last_prospect", 0.0) or 0.0)
                    now = time.time()
                    if now - last >= interval:
                        await _save_state({"last_prospect": now})
                        return {
                            "action": "bootstrap_prospecting",
                            "rationale": (
                                "No leads in pipeline; CEO autonomously seeds multi-agent "
                                "outbound prospecting to start the income engine."
                            ),
                            "workspace_id": self._fallback_workspace(overview),
                            "delegate_multi": {
                                "agents": ["sba", "content", "website"],
                                "action_type": "internal_analysis",
                                "task": (
                                    "You are THREE agents working in parallel for the same goal: "
                                    "identify a 'starving crowd' niche for TAGS Agency (local "
                                    "B2B service businesses: dentists, physiotherapists, yoga coaches, "
                                    "local SaaS founders). Each agent contributes ONE piece, then the "
                                    "CEO merges: "
                                    "[SBA] -> research 5 high-fit target accounts: business name, city, "
                                    "pain-point our AI agents solve, warm-outreach hook. "
                                    "[CONTENT] -> write a 150-word hook blog/intro paragraph for the "
                                    "chosen niche. "
                                    "[WEBSITE] -> draft a 3-section landing page outline (hero, "
                                    "pain-points, cta). "
                                    "NO email, NO spend, NO publish, NO contract. Return only plain text."
                                ),
                                "workspaces": self._starved_workspace_ids(overview),
                            },
                            "result": {"overview": summary},
                        }
            except Exception as exc:
                logger.debug("CEO autonomy prospect bootstrap check failed: %s", _safe_error(exc))
            return {
                "action": "observe",
                "rationale": "Heartbeat inspected the agency overview; no urgent internal work.",
                "result": {"overview": summary},
            }
        return {
            "action": "observe",
            "rationale": "Event inspected; no autonomous action selected.",
            "result": {"overview": summary, "event_type": event_type},
        }

    @staticmethod
    def _overview() -> dict[str, Any]:
        try:
            from admin.ceo_data import get_agency_overview

            return _safe_value(get_agency_overview())
        except Exception as exc:
            logger.debug("CEO autonomy overview failed: %s", _safe_error(exc))
            return {"summary": {}, "alerts": []}

    @staticmethod
    def _fallback_workspace(overview: dict[str, Any]) -> str:
        workspaces = overview.get("workspaces", []) if isinstance(overview, dict) else []
        for workspace in workspaces:
            if workspace.get("id") == "ws_agency":
                return "ws_agency"
        if workspaces and workspaces[0].get("id"):
            return str(workspaces[0]["id"])
        return "ws_agency"

    def _starved_workspace_ids(self, overview: dict[str, Any]) -> list[str]:
        """Workspace ids that have zero active leads (prime for multi-agent seeding)."""
        workspaces = overview.get("workspaces", []) if isinstance(overview, dict) else []
        starved: list[str] = []
        for ws in workspaces:
            if not isinstance(ws, dict):
                continue
            ws_id = ws.get("id")
            if not ws_id:
                continue
            leads = 0
            for key in ("active_leads", "total_active_leads", "leads", "total_leads"):
                value = ws.get(key)
                if isinstance(value, int):
                    leads = value
                    break
            if leads == 0:
                starved.append(str(ws_id))
        if not starved:
            starved = [self._fallback_workspace(overview)]
        return starved

    async def _run_orchestration(
        self,
        workspace_id: str,
        agents: list[str],
        action_type: str,
        task: str,
    ) -> dict[str, Any]:
        """Run multiple agents in parallel (multi-agent mode) and merge outputs."""
        from admin.workspace.manager import route_to_agent

        orch_id = _id("ceo_orch")
        status = "running"
        results: dict[str, Any] = {}
        errors: dict[str, Any] = {}
        started_at = _now()

        try:
            db = await get_workspace_db()
            await db.execute(
                "INSERT INTO ceo_autonomy_tasks "
                "(id, workspace_id, agent_type, action_type, task, status, created_at, started_at) "
                "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
                (orch_id, workspace_id, "orchestration", action_type, task, started_at, started_at),
            )
            await db.commit()
        except Exception as exc:
            logger.debug("CEO orchestration task persistence failed: %s", _safe_error(exc))

        async def _run_one(agent_type: str) -> tuple[str, str]:
            spec = (
                f"[{agent_type.upper()} specialism] You are one of {len(agents)} agents working in parallel "
                f"for the same goal. {task}"
            )
            try:
                out = await asyncio.wait_for(
                    route_to_agent(workspace_id, agent_type, spec, safe_only=True),
                    timeout=self._agent_timeout,
                )
                out = str(out or "").strip()
                if not out:
                    return agent_type, f"ERROR: {agent_type} agent returned an empty result"
                return agent_type, out
            except Exception as exc:
                return agent_type, f"ERROR: {_safe_error(exc)}"

        try:
            for agent_type, output in await asyncio.gather(
                *[_run_one(a) for a in agents]
            ):
                if output.startswith("ERROR:"):
                    errors[agent_type] = output
                results[agent_type] = output

            # Status must reflect the sub-agents, not the gather() call. This
            # used to be unconditionally "done", which is how a task whose every
            # agent timed out got stored as a successful run. The CEO then read
            # 19 consecutive "successful" prospecting decisions that had in fact
            # produced nothing at all.
            #
            # All-failed is a failure. Partial success is still a partial success
            # and is recorded as "degraded": the real work did happen, and
            # flattening it to either done or error would both lose that.
            if agents and len(errors) == len(agents):
                status = "error"
                errors["orchestration"] = (
                    f"all {len(agents)} sub-agents failed: "
                    + ", ".join(f"{k}: {v[:120]}" for k, v in errors.items())
                )
                logger.warning(
                    "CEO orchestration %s marked error, not done: all %d "
                    "sub-agents failed", orch_id, len(agents),
                )
            elif errors:
                status = "degraded"
                logger.warning(
                    "CEO orchestration %s degraded: %d of %d sub-agents failed",
                    orch_id, len(errors), len(agents),
                )
            else:
                status = "done"
        except Exception as exc:
            status = "error"
            errors["orchestration"] = _safe_error(exc)

        merged = {
            "orchestration_id": orch_id,
            "agents": list(agents),
            "results": results,
            "errors": errors,
        }
        await emit_event(
            "agent.output",
            workspace_id=workspace_id,
            source="ceo_autonomy",
            payload={
                "task_id": orch_id,
                "agent_type": "orchestration",
                "output_id": orch_id,
                "output_preview": json.dumps(_safe_value(merged), ensure_ascii=False)[:300],
            },
        )
        finished_at = _now()
        try:
            db = await get_workspace_db()
            await db.execute(
                "UPDATE ceo_autonomy_tasks SET status=?, result=?, error=?, finished_at=? WHERE id=?",
                (
                    status,
                    json.dumps(_safe_value(merged), ensure_ascii=False)[:4000],
                    json.dumps(_safe_value(errors), ensure_ascii=False)[:500],
                    finished_at,
                    orch_id,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.debug("CEO orchestration task persistence failed: %s", _safe_error(exc))

        return {
            "task_id": orch_id,
            "workspace_id": workspace_id,
            "agent_type": "orchestration",
            "status": status,
            "result": _safe_value(merged),
            "error": errors,
        }

    async def _run_task(
        self,
        workspace_id: str,
        agent_type: str,
        action_type: str,
        task: str,
    ) -> dict[str, Any]:
        task_id = _id("ceo_task")
        if _is_external_action(action_type) and not _skip_approval_for_internal(action_type):
            approval = await request_approval(action_type, task, workspace_id, {"task": task})
            return {"task_id": task_id, "status": "approval_required", "approval": approval}
        status = "running"
        result = ""
        error = ""
        started_at = _now()
        bus_id = ""
        try:
            db = await get_workspace_db()
            await db.execute(
                "INSERT INTO ceo_autonomy_tasks "
                "(id, workspace_id, agent_type, action_type, task, status, created_at, started_at) "
                "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
                (task_id, workspace_id, agent_type, action_type, task, started_at, started_at),
            )
            await db.commit()
            try:
                from admin.agency.agent_bus import get_bus

                bus_id = get_bus().brief(
                    "ceo",
                    agent_type,
                    workspace_id,
                    task,
                    objective="CEO autonomy internal analysis",
                    context="No external action is permitted.",
                    required_action="analyze and respond",
                    metadata={"ceo_task_id": task_id},
                    status="active",
                )
            except Exception as exc:
                logger.debug("CEO autonomy agent-bus brief failed: %s", _safe_error(exc))
            try:
                from admin.workspace.manager import route_to_agent

                result = await asyncio.wait_for(
                    route_to_agent(workspace_id, agent_type, task, safe_only=True),
                    timeout=self._agent_timeout,
                )
                result = str(result or "")
                status = "done"
            except asyncio.TimeoutError:
                status = "error"
                error = f"agent task exceeded {self._agent_timeout:.0f}s timeout"
            except Exception as exc:
                status = "error"
                error = _safe_error(exc)
            # A blank, explicitly failed, or tool-call-shaped result is not a
            # completed task. Marking it done is how a failed dispatch ended up
            # stored as a successful task with an empty output.
            if status == "done":
                reason = unusable_output_reason(
                    result, context=f"{agent_type} agent")
                if reason:
                    status = "error"
                    error = reason
                    logger.warning(
                        "CEO autonomy task %s marked error, not done: %s",
                        task_id, reason,
                    )
            if bus_id:
                try:
                    from admin.agency.agent_bus import get_bus

                    get_bus().respond(
                        bus_id,
                        result=result[:4000],
                        # "degraded" is a partial success, not a failure. Mapping
                        # it to "failed" would tell the bus the whole
                        # orchestration produced nothing, which is what made the
                        # real, salvageable output look worthless downstream.
                        status=status if status in ("done", "degraded") else "failed",
                        errors=error[:500],
                    )
                except Exception as exc:
                    logger.debug("CEO autonomy agent-bus response failed: %s", _safe_error(exc))
            await emit_event(
                "agent.output",
                workspace_id=workspace_id,
                source="ceo_autonomy",
                payload={
                    "task_id": task_id,
                    "agent_type": agent_type,
                    "output_id": task_id,
                    "output_preview": result[:300],
                },
            )
        except Exception as exc:
            status = "error"
            error = _safe_error(exc)
        finally:
            finished_at = _now()
            try:
                db = await get_workspace_db()
                await db.execute(
                    "UPDATE ceo_autonomy_tasks SET status=?, result=?, error=?, finished_at=? "
                    "WHERE id=?",
                    (status, result[:4000], error[:500], finished_at, task_id),
                )
                await db.commit()
            except Exception as exc:
                logger.debug("CEO autonomy task persistence failed: %s", _safe_error(exc))
        return {
            "task_id": task_id,
            "workspace_id": workspace_id,
            "agent_type": agent_type,
            "status": status,
            "result": result[:4000],
            "error": error[:500],
        }

    async def status(self) -> dict[str, Any]:
        state = await _load_state()
        counts = {"pending_events": 0, "processing_events": 0, "running_tasks": 0, "pending_approvals": 0}
        try:
            db = await get_workspace_db()
            for label, query in (
                ("pending_events", "SELECT COUNT(*) FROM ceo_autonomy_events WHERE status='pending'"),
                ("processing_events", "SELECT COUNT(*) FROM ceo_autonomy_events WHERE status='processing'"),
                ("running_tasks", "SELECT COUNT(*) FROM ceo_autonomy_tasks WHERE status IN ('queued','running')"),
                ("pending_approvals", "SELECT COUNT(*) FROM ceo_autonomy_approvals WHERE status='pending'"),
            ):
                cursor = await db.execute(query)
                row = await cursor.fetchone()
                counts[label] = int(row[0]) if row else 0
        except Exception as exc:  # noqa: BLE001
            # Never swallow this. Reporting zeros because the query failed is
            # indistinguishable from a genuinely idle agency, which is how a
            # dead control plane kept reporting "ok, nothing to do".
            counts["error"] = _safe_error(exc)
            logger.warning("CEO autonomy status count query failed: %s", _safe_error(exc))
        state["counts"] = counts
        state["tick_sec"] = self._tick_sec
        state["max_events_per_tick"] = self._max_events
        state["max_workers"] = self._max_workers
        # Is the self-directed step actually firing? Without these, a CEO that
        # never thinks is indistinguishable from one that is waiting.
        state["think_interval_sec"] = float(os.getenv("AGENCY_CEO_THINK_INTERVAL_SEC", "300"))
        state["thinks_seeded"] = int(state.get("thinks_seeded", 0) or 0)
        state["last_think_at"] = float(state.get("last_think_at", 0.0) or 0.0)
        state["last_think_action"] = str(state.get("last_think_action", "") or "")
        state["last_think_rationale"] = str(state.get("last_think_rationale", "") or "")
        try:
            from admin.config import settings as _settings

            thinker_ok = bool(
                (_settings.WORKSPACE_API_BASE or "").strip()
                and (_settings.WORKSPACE_API_KEY or "").strip()
            )
        except Exception:  # noqa: BLE001
            thinker_ok = False
        state["thinker_configured"] = thinker_ok
        # An agency that is being rate limited and one that is merely idle look
        # identical from the outside. Surface the breaker so /status says which.
        try:
            state["llm_guards"] = llm_guard_snapshot()
        except Exception as exc:  # noqa: BLE001
            # Never let a status field crash the status call itself.
            state["llm_guards"] = {"error": _safe_error(exc)}
            logger.warning("LLM guard snapshot failed: %s", _safe_error(exc))
        return _safe_value(state)


_autonomy: CEOAutonomy | None = None


def get_autonomy() -> CEOAutonomy:
    global _autonomy
    if _autonomy is None:
        _autonomy = CEOAutonomy()
    return _autonomy
