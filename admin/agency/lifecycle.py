"""LifecycleState — the gate that keeps agents LIGHT and 24/7-loop-free.

Per the boss's hard rule (docs/specs/2026-08-25-ceo-gated-on-demand-design.md):

  Agents are SMART. CEO runs 24/7 as a listener. Agents sleep by default and
  wake ONLY on CEO command. When a task is done an agent self-sleeps (STANDBY)
  even without an explicit stop. The server stays light. No 24/7 always-on
  agent loops.

This module is the one place that enforces that rule:

  STANDBY  (default)  -> no process/loop, cheap
  ACTIVE              -> CEO dispatched a brief; worker running run_once
  COOLDOWN            -> just finished; flushing; about to STANDBY
  DISABLED            -> owner turned the agent off

Rules enforced (not advisory):
  1. Default = STANDBY at boot. Nothing spawns a loop.
  2. Wake = CEO-only (via Lifecycle.wake, called from CEO tools).
  3. Self-sleep: executor returns -> COOLDOWN -> STANDBY. No daemon left.
  4. No `while True` / run_forever anywhere reaches a worker directly.
  5. Boss can force wake/sleep via API; state persisted in the registry.

The CEO is the ONLY thing that may call Lifecycle.wake. That is the
intelligence-vs-mechanism split: CEO (LLM) decides who works; Lifecycle (code)
enforces sleep.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json

logger = logging.getLogger(__name__)


class LifecycleState(str, Enum):
    STANDBY = "standby"
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


@dataclass
class AgentRuntime:
    slug: str
    state: LifecycleState = LifecycleState.STANDBY
    last_wake: float | None = None
    last_sleep: float | None = None
    current_brief_id: str | None = None
    last_error: str | None = None


# In-memory runtime table (single-writer: only Lifecycle mutates it).
_RUNTIMES: dict[str, AgentRuntime] = {}

# Persisted override file (boss/CEO forced states survive restart).
_STATE_FILE = Path(__file__).parent / "lifecycle_state.json"


def _load_overrides() -> dict:
    if _STATE_FILE.is_file():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_overrides(overrides: dict) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not persist lifecycle overrides: %s", e)
    # External PocketBase mirror (best-effort; never breaks local runs).
    _mirror_lifecycle_to_pb(overrides)


def _mirror_lifecycle_to_pb(overrides: dict) -> None:
    """Mirror CEO lifecycle/error state to external PocketBase (best-effort)."""
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        pb.ensure_collection("ceo_lifecycle", {"slug": "text", "state": "text"})
        for slug, state in overrides.items():
            # Match on `slug` — PB's 15-char ids can't hold 'lc_<slug>'.
            pb.upsert_by_key("ceo_lifecycle", "slug", {"slug": slug, "state": state})
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase lifecycle mirror failed (non-fatal): %s", exc)


def register(slug: str, disabled: bool = False) -> AgentRuntime:
    """Register an agent at boot. Default state = STANDBY (or DISABLED if set)."""
    overrides = _load_overrides()
    forced = overrides.get(slug)
    rt = _RUNTIMES.get(slug)
    if rt is None:
        rt = AgentRuntime(slug=slug)
        _RUNTIMES[slug] = rt
    if forced in (LifecycleState.DISABLED.value, "disabled"):
        rt.state = LifecycleState.DISABLED
    elif disabled:
        rt.state = LifecycleState.DISABLED
    else:
        # Default is always STANDBY unless explicitly forced active (never auto).
        if rt.state not in (LifecycleState.DISABLED,):
            rt.state = LifecycleState.STANDBY
    return rt


def get(slug: str) -> AgentRuntime:
    """Get (or lazily register) an agent's runtime."""
    rt = _RUNTIMES.get(slug)
    if rt is None:
        rt = register(slug)
    return rt


def all_runtimes() -> list[AgentRuntime]:
    return list(_RUNTIMES.values())


def can_wake(slug: str) -> bool:
    rt = get(slug)
    return rt.state in (LifecycleState.STANDBY, LifecycleState.COOLDOWN)


def wake(slug: str, brief_id: str | None = None) -> AgentRuntime:
    """CEO-only: put an agent into ACTIVE. Raises if not in a wakeable state.

    This is the ONLY entry point that flips an agent to ACTIVE. It must be
    called from a CEO tool (e.g. _tool_delegate / _tool_run_sales), never from a
    scheduler or monitor.
    """
    rt = get(slug)
    if rt.state == LifecycleState.DISABLED:
        raise RuntimeError(f"Agent '{slug}' is DISABLED; cannot wake.")
    if not can_wake(slug):
        raise RuntimeError(
            f"Agent '{slug}' is {rt.state.value}; only STANDBY/COOLDOWN can wake."
        )
    rt.state = LifecycleState.ACTIVE
    rt.last_wake = time.time()
    rt.current_brief_id = brief_id
    rt.last_error = None
    logger.info("Lifecycle.wake(%s) -> ACTIVE (brief=%s)", slug, brief_id)
    return rt


def sleep(slug: str) -> AgentRuntime:
    """Self-sleep: ACTIVE -> COOLDOWN -> STANDBY. No loop left running."""
    rt = get(slug)
    rt.state = LifecycleState.COOLDOWN
    rt.current_brief_id = None
    rt.last_sleep = time.time()
    # COOLDOWN is instantaneous here; immediately to STANDBY (no daemon).
    rt.state = LifecycleState.STANDBY
    logger.info("Lifecycle.sleep(%s) -> STANDBY", slug)
    return rt


def mark_error(slug: str, error: str) -> AgentRuntime:
    """Record an error but still return to STANDBY (self-sleep even on failure)."""
    rt = get(slug)
    rt.last_error = error
    return sleep(slug)


def force_wake(slug: str, brief_id: str | None = None) -> AgentRuntime:
    """Boss/API manual override — forces ACTIVE even from COOLDOWN."""
    rt = get(slug)
    if rt.state == LifecycleState.DISABLED:
        # Allow re-enabling via explicit force if not globally disabled.
        pass
    rt.state = LifecycleState.ACTIVE
    rt.last_wake = time.time()
    rt.current_brief_id = brief_id
    _persist_override(slug, LifecycleState.ACTIVE.value)
    return rt


def force_sleep(slug: str) -> AgentRuntime:
    """Boss/API manual override — forces STANDBY."""
    rt = get(slug)
    rt.state = LifecycleState.STANDBY
    rt.current_brief_id = None
    rt.last_sleep = time.time()
    _persist_override(slug, LifecycleState.STANDBY.value)
    return rt


def disable(slug: str) -> AgentRuntime:
    rt = get(slug)
    rt.state = LifecycleState.DISABLED
    rt.current_brief_id = None
    _persist_override(slug, LifecycleState.DISABLED.value)
    return rt


def enable(slug: str) -> AgentRuntime:
    rt = get(slug)
    rt.state = LifecycleState.STANDBY
    _persist_override(slug, LifecycleState.STANDBY.value)
    return rt


def _persist_override(slug: str, state_value: str) -> None:
    overrides = _load_overrides()
    overrides[slug] = state_value
    _save_overrides(overrides)


def snapshot() -> list[dict]:
    """Light, on-demand introspection (for GET /api/ceo/state). No polling."""
    return [
        {
            "slug": rt.slug,
            "state": rt.state.value,
            "last_wake": rt.last_wake,
            "last_sleep": rt.last_sleep,
            "current_brief_id": rt.current_brief_id,
            "last_error": rt.last_error,
        }
        for rt in all_runtimes()
    ]
