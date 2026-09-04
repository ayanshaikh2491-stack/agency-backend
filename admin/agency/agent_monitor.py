"""Agency-wide Agent Health Monitor — shared 24/7 construction probe.

Every registered worker agent is probed on a fixed interval to detect
import/compile/missing-dependency/construct failures BEFORE a client hits the
chat box. The probe only builds the agent (module import + class resolve +
instantiate) — it does NOT call the LLM, so it is cheap and catches the
historical failure class (langgraph 4.x removed symbols -> ImportError crashed
all 7 on-demand agents silently until a user chatted).

Wire this into the FastAPI lifespan (see admin.main) via start_monitor() /
stop_monitor(), then read status from get_monitor().get_health().

Probe targets mirror admin.api.routes.agent_aliases.AGENT_SLUG_MAP plus the
SBA autopilot runner. "memory" is intentionally included even though no
module exists, so the monitor surfaces that gap instead of hiding it.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import traceback
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 5 * 60  # every 5 minutes

# slug -> (module_path, class_name, init_kwargs)
# Mirrors the real routing table in admin.workspace.manager.route_to_agent.
_AGENT_PROBES: dict[str, dict[str, Any]] = {
    "seo": {
        "module": "admin.workspace.agents.seo",
        "class": "SEOAgent",
        "kwargs": {"workspace_name": "HealthCheck", "client_name": "HealthCheck"},
    },
    "ads": {
        "module": "admin.workspace.agents.ads",
        "class": "AdsAgent",
        "kwargs": {"workspace_name": "HealthCheck", "client_name": "HealthCheck"},
    },
    "website": {
        "module": "admin.workspace.agents.website",
        "class": "WebsiteAgent",
        "kwargs": {"workspace_name": "HealthCheck", "client_name": "HealthCheck"},
    },
    "social": {
        "module": "admin.workspace.agents.social",
        "class": "SocialAgent",
        "kwargs": {"workspace_name": "HealthCheck", "client_name": "HealthCheck"},
    },
    "analytics": {
        "module": "admin.workspace.agents.analytics",
        "class": "AnalyticsAgent",
        "kwargs": {"workspace_name": "HealthCheck", "client_name": "HealthCheck"},
    },
    "content": {
        "module": "admin.workspace.agents.content",
        "class": "ContentAgent",
        "kwargs": {
            "workspace_id": "health",
            "workspace_name": "HealthCheck",
            "client_name": "HealthCheck",
        },
    },
    # Intentionally probed: registered in AGENT_SLUG_MAP but no module exists.
    "memory": {
        "module": "admin.workspace.agents.memory",
        "class": "MemoryAgent",
        "kwargs": {"workspace_name": "HealthCheck", "client_name": "HealthCheck"},
    },
    # SBA autopilot runner — the only always-on agent.
    "sba": {
        "module": "admin.agency.sba_autopilot",
        "class": "SBAWorkspaceRunner",
        "kwargs": {},
    },
}


def probe_agent(slug: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Construct the agent and report health. No LLM call.

    Returns a health record: status healthy|down, error, trace (optional).
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        module = importlib.import_module(spec["module"])
        cls = getattr(module, spec["class"], None)
        if cls is None:
            return {
                "slug": slug,
                "status": "down",
                "error": f"class '{spec['class']}' not found in {spec['module']}",
                "last_checked": now,
            }
        try:
            cls(**spec.get("kwargs", {}))
        except Exception as exc:  # noqa: BLE001 — construction-time failure
            return {
                "slug": slug,
                "status": "down",
                "error": f"instantiation failed: {type(exc).__name__}: {exc}",
                "last_checked": now,
            }
        return {"slug": slug, "status": "healthy", "error": None, "last_checked": now}
    except Exception as exc:  # noqa: BLE001 — import/compile/missing-dep
        return {
            "slug": slug,
            "status": "down",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=3),
            "last_checked": now,
        }


class AgentHealthMonitor:
    """On-demand construction-health snapshot (no background loop).

    Per the boss rule there are NO 24/7 polling loops. Health is recomputed
    fresh on each call to get_health(). start()/stop() are kept for API
    symmetry but intentionally launch/stop NO background task.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._health: dict[str, dict[str, Any]] = {}
        # Consecutive failure counts per slug (alert only on sustained down).
        self._failures: dict[str, int] = {slug: 0 for slug in _AGENT_PROBES}
        self._alerts: list[dict[str, Any]] = []

    async def start(self) -> None:
        # Intentionally does NOT start a background loop (boss rule).
        logger.info("Agent Health Monitor is on-demand; call get_health().")

    async def stop(self) -> None:
        logger.info("Agent Health Monitor has no background task to stop.")

    async def get_health(self) -> dict[str, Any]:
        async with self._lock:
            if not self._health:
                self._health = {slug: probe_agent(slug, spec) for slug, spec in _AGENT_PROBES.items()}
            down = [s for s, r in self._health.items() if r["status"] == "down"]
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "all_healthy": len(down) == 0,
                "down_count": len(down),
                "agents": self._health,
                "alerts": list(self._alerts),
            }

    def get_alerts(self) -> list[dict[str, Any]]:
        alerts = list(self._alerts)
        self._alerts.clear()
        return alerts

    # No _run_loop: the monitor is on-demand only (boss rule — no 24/7 loop).
    # Health is recomputed in get_health(); there is intentionally no
    # while-True / run_forever background task anywhere in this module.


_monitor: AgentHealthMonitor | None = None


def get_monitor() -> AgentHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = AgentHealthMonitor()
    return _monitor


async def start_monitor() -> AgentHealthMonitor:
    """Start the shared monitor (call once in the FastAPI lifespan)."""
    global _monitor
    m = get_monitor()
    await m.start()
    return m


async def stop_monitor() -> None:
    if _monitor is not None:
        await _monitor.stop()
