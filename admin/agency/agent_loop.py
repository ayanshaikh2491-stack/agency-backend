"""On-Demand Agency Agent Loop — CEO-mandated task runner (no background loop).

This is NOT an autonomy engine that runs 24/7. Per the boss rule, there are NO
always-on agent loops. Instead, this module exposes agent_loop_tick() which the
CEO calls ON DEMAND (when a boss-scheduled mandate is active). It never
self-schedules, never spawns a `while True`, and leaves no daemon running.

What agent_loop_tick() does when the CEO invokes it:
  1. Runs all RUNNING mandates' standing tasks via workers.run_worker
     (SEO scan -> report -> workspace CEO -> agency CEO -> agency SEO monitor).
  2. Auto-fires SBA -> CEO handoffs for leads that booked a meeting but whose
     client workspace has not been created yet. This closes the loop: a lead
     says "yes" to the SBA agent, the CEO agent spins up the client workspace
     and registers every specialist agent.

Design notes:
  - The orchestrator + scheduler are fully built; run_due_tasks was only
    reachable through a manual API endpoint. We keep it that way — the CEO is
    the only thing that triggers a tick, so the server stays light.
  - Safety: bounded (one tick at a time), read-only audit, NEVER sends
    external email / books meetings itself (those stay owned by the SBA
    autopilot + explicit owner gates).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from admin.config import settings

logger = logging.getLogger("agency.agent_loop")

# Used only to bound a single on-demand tick (not a polling interval).
AGENT_LOOP_TICK_TIMEOUT_SECONDS: int = int(
    getattr(settings, "AGENCY_AGENT_LOOP_TICK_TIMEOUT_SECONDS", "120")
)


async def _auto_process_due_handoffs() -> dict[str, Any]:
    """Auto-fire SBA -> CEO handoffs for booked leads missing a workspace.

    A handoff is created by the SBA pipeline when a lead books a meeting.
    If the workspace was never provisioned (e.g. the manual endpoint was not
    called), this brings it to life autonomously — but only when the CEO
    triggers a tick.
    """
    from admin.agency.orchestrator import ceo_process_sba_handoff
    from admin.agency.sba_store import list_handoffs

    processed = []
    try:
        pending = [h for h in list_handoffs() if not h.get("workspace_id")]
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent-loop: list_handoffs failed: %s", exc)
        return {"checked": 0, "processed": [], "error": str(exc)}

    for handoff in pending:
        hid = handoff.get("id")
        if not hid:
            continue
        try:
            result = await ceo_process_sba_handoff(hid)
            processed.append({"handoff_id": hid, "result": result})
            logger.info("agent-loop: auto-processed SBA handoff %s", hid)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent-loop: handoff %s failed: %s", hid, exc)

    return {"checked": len(pending), "processed": processed}


async def _tick_once() -> dict[str, Any]:
    """CEO-mandate-driven tick: never self-schedules. Runs only active mandates."""
    from admin.agency import mandates as mandates_mod
    from admin.agency import workers as workers_mod
    handoffs = await _auto_process_due_handoffs()
    ran: list[str] = []
    for md in await mandates_mod.list_mandates():
        if md.get("status") != "running":
            continue
        worker = md["worker"]
        scope = md.get("scope") or {"kind": "agency", "workspace_id": "agency"}
        try:
            await workers_mod.run_worker(worker, md.get("standing_task", ""), {"scope": scope})
            ran.append(worker)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent-loop: worker %s failed: %s", worker, exc)
    return {"mandates_ran": ran, "handoffs": handoffs, "at": time.time()}


async def agent_loop_tick() -> dict[str, Any]:
    """Run one CEO-mandated tick. On-demand only — never called on a timer."""
    try:
        return await asyncio.wait_for(_tick_once(), timeout=AGENT_LOOP_TICK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("agent-loop: tick exceeded %ss", AGENT_LOOP_TICK_TIMEOUT_SECONDS)
        return {"error": "tick_timeout"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent-loop: tick crashed: %s", exc)
        return {"error": str(exc)}
