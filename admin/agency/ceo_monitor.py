"""CEO Proactive Monitor — on-demand agency health snapshot (no background loop).

Per the boss rule, there is NO 24/7 monitoring loop. The CEO is a 24/7 listener
(via HTTP /api/ceo/chat), but it does NOT run a polling task. Instead, health is
computed ON DEMAND: the CEO calls get_agency_status() when it needs to report or
decide. That keeps the server light — only the FastAPI process is always on.

Checks (computed fresh each call):
  1. Token health -> expiring/expired
  2. Pending reviews -> count needing CEO attention
  3. SBA handoffs -> pending handoffs waiting for CEO
  4. Workspace activity -> inactive workspaces
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class CEOMonitor:
    """On-demand agency health snapshot. No background task, no loop."""

    def __init__(self) -> None:
        self._last_status: dict[str, Any] = {}

    async def get_agency_status(self) -> dict[str, Any]:
        """Build and return a fresh agency status snapshot (called on demand)."""
        status = await self._build_status()
        self._last_status = status
        return status

    async def _build_status(self) -> dict[str, Any]:
        """Build current agency status snapshot."""
        from admin.ceo_data import get_agency_overview
        overview = get_agency_overview()

        from admin.workspace.manager import list_pending_reviews
        pending_reviews = list_pending_reviews()

        try:
            from admin.agency.sba_store import list_handoffs
            handoffs = [h for h in list_handoffs() if not h.get("workspace_id")]
        except ImportError:
            handoffs = []

        alerts = list(overview.get("alerts", []))

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workspace_count": overview["summary"].get("total_workspaces", 0),
            "pending_reviews": len(pending_reviews),
            "pending_handoffs": len(handoffs),
            "alerts": alerts,
            "token_health": {
                "total": overview["summary"].get("total_tokens", 0),
                "expired": overview["summary"].get("expired_tokens", 0),
                "expiring": overview["summary"].get("expiring_tokens", 0),
            },
            "leads": {
                "active": overview["summary"].get("active_leads", 0),
                "closed": overview["summary"].get("closed_leads", 0),
            },
        }


# Module-level singleton
_monitor: CEOMonitor | None = None


def get_monitor() -> CEOMonitor:
    global _monitor
    if _monitor is None:
        _monitor = CEOMonitor()
    return _monitor
