"""Background task runner: agent work without the 60s edge limit.

Render's edge kills any HTTP request at ~60s, but real agent work
(draft + tools + review) often needs 2-5 minutes. This module lets any
caller hand a task to a background coroutine and poll for the result:

    task_id = await start_task("sba", ws_id, "find 10 dentist leads")
    ...
    state = get_task(task_id)   # {"status": "running"|"done"|"error", ...}

Tasks live in memory (fine on ephemeral Render disks; finished results
are also mirrored to the agent_outputs store for audit). A hard 10-minute
cap bounds runaway tasks. No event-loop blocking: everything is async.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

logger = logging.getLogger(__name__)

# task_id -> state dict. Never serialize live objects out of it; copy fields.
_TASKS: dict[str, dict] = {}
_LOCK = asyncio.Lock()
# Keep strong refs so asyncio doesn't GC running coroutines.
_HANDLES: dict[str, asyncio.Task] = {}

HARD_CAP_SEC = 600.0  # 10 min
# Render free tier = 512MB RAM. Background tasks are lightweight (the LLM
# runs OUTSIDE the server; we only hold strings), but bound them anyway:
# never more than MAX_CONCURRENT running, finished states pruned so the
# in-memory registry can never grow unbounded across a long uptime.
MAX_CONCURRENT = 3
KEEP_FINISHED = 30  # keep the newest 30 finished task states


async def start_task(
    agent_type: str,
    workspace_id: str,
    message: str,
    *,
    source: str = "api",
) -> str:
    """Spawn a background agent task; returns its id immediately.

    Guard-rails for the 512MB box: at most MAX_CONCURRENT tasks run at
    once; extra requests get a polite 'queue full' instead of piling RAM.
    """
    async with _LOCK:
        running = sum(
            1 for s in _TASKS.values() if s["status"] in ("queued", "running")
        )
        if running >= MAX_CONCURRENT:
            return "BUSY"  # caller translates this to a clean message
        task_id = uuid.uuid4().hex[:12]
        state = {
            "task_id": task_id,
            "agent_type": agent_type,
            "workspace_id": workspace_id,
            "message": message[:500],
            "source": source,
            "status": "queued",
            "result": "",
            "error": "",
            "started_at": time.time(),
            "finished_at": None,
        }
        _TASKS[task_id] = state
        _HANDLES[task_id] = asyncio.create_task(_run(state))
    logger.info("bg task %s -> %s (ws=%s src=%s)", task_id, agent_type, workspace_id, source)
    return task_id


async def _run(state: dict) -> None:
    """Coroutine body: raw agent call with a hard 10-min cap."""
    tid = state["task_id"]
    state["status"] = "running"
    t0 = time.time()
    try:
        from admin.workspace.manager import _route_to_agent_raw

        result = await asyncio.wait_for(
            _route_to_agent_raw(
                state["workspace_id"], state["agent_type"], state["message"]
            ),
            timeout=HARD_CAP_SEC,
        )
        state["result"] = str(result)
        state["status"] = "done"
    except asyncio.TimeoutError:
        state["status"] = "error"
        state["error"] = f"task exceeded {HARD_CAP_SEC:.0f}s hard cap"
    except Exception as exc:  # noqa: BLE001
        state["status"] = "error"
        state["error"] = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        state["finished_at"] = time.time()
        state["elapsed_sec"] = round(state["finished_at"] - t0, 1)
        # Audit trail: finished outputs land in the same store the CEO reads.
        try:
            from admin.workspace.manager import store_agent_output

            store_agent_output(
                workspace_id=state["workspace_id"],
                agent_type=state["agent_type"],
                task=state["message"],
                output=state["result"] or state["error"],
            )
        except Exception:  # noqa: BLE001
            logger.debug("bg task %s: store_agent_output failed", tid, exc_info=True)
        # Activity log so the floor/status surfaces see background work.
        try:
            from admin.ceo_data import log_activity

            log_activity(
                workspace_id=state["workspace_id"],
                agent_type=state["agent_type"],
                action=f"bg_task_{state['status']}",
                details=f"[{tid}] {state['message'][:80]}",
                metadata={"task_id": tid, "elapsed": state.get("elapsed_sec")},
            )
        except Exception:  # noqa: BLE001
            pass
        _HANDLES.pop(tid, None)
        # RAM guard: keep only the newest KEEP_FINISHED finished states.
        if len(_TASKS) > KEEP_FINISHED + MAX_CONCURRENT:
            finished = sorted(
                (s for s in _TASKS.values() if s["status"] in ("done", "error")),
                key=lambda s: s["started_at"],
            )
            for old in finished[: len(_TASKS) - KEEP_FINISHED - MAX_CONCURRENT]:
                _TASKS.pop(old["task_id"], None)
        logger.info("bg task %s finished: %s in %ss", tid, state["status"], state.get("elapsed_sec"))


def get_task(task_id: str) -> dict | None:
    """Public copy of one task state (small owned dict, no live refs)."""
    st = _TASKS.get(task_id)
    if not st:
        return None
    return {
        "task_id": st["task_id"],
        "agent_type": st["agent_type"],
        "workspace_id": st["workspace_id"],
        "message": st["message"],
        "source": st["source"],
        "status": st["status"],
        "result": st["result"][:2000] if st["result"] else "",
        "error": st["error"],
        "started_at": st["started_at"],
        "finished_at": st["finished_at"],
        "elapsed_sec": st.get("elapsed_sec"),
    }


def list_tasks(limit: int = 20, pending_only: bool = False) -> list[dict]:
    """Newest-first task list (bounded, copied fields only)."""
    items = sorted(_TASKS.values(), key=lambda s: s["started_at"], reverse=True)
    if pending_only:
        items = [s for s in items if s["status"] in ("queued", "running")]
    return [
        {
            "task_id": s["task_id"],
            "agent_type": s["agent_type"],
            "message": s["message"][:120],
            "status": s["status"],
            "elapsed_sec": s.get("elapsed_sec"),
        }
        for s in items[: max(1, min(limit, 100))]
    ]
