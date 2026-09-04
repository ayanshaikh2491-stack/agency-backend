"""SBA Proactive Monitor — on-demand pipeline snapshot (no background loop).

Per the boss rule, there is NO 24/7 SBA monitoring loop. SBA runs only when the
CEO wakes it (via Lifecycle.wake -> SBAWorkspaceRunner.run_mandated). This
module provides an ON-DEMAND status snapshot the CEO can pull when it needs to
report pipeline health — no polling, no background task, server stays light.

Checks (computed fresh each call):
  1. Stale leads needing follow-up
  2. Hot leads ready for CEO attention
  3. Pipeline health + pending CEO handoffs
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

LEAD_STALE_DAYS = 3  # Alert if lead untouched for 3+ days


class SBAMonitor:
    """On-demand SBA pipeline snapshot. No background task, no loop."""

    def __init__(self) -> None:
        self._last_status: dict[str, Any] = {}

    async def get_pipeline_status(self) -> dict[str, Any]:
        """Build and return a fresh SBA pipeline snapshot (called on demand)."""
        status = await self._build_status()
        self._last_status = status
        return status

    async def _build_status(self) -> dict[str, Any]:
        """Build current SBA pipeline status snapshot."""
        from admin.agency.sba_store import list_handoffs, list_leads

        all_leads = list_leads()
        handoffs = [h for h in list_handoffs() if not h.get("workspace_id")]

        now = datetime.now(timezone.utc)
        alerts: list[dict[str, Any]] = []
        stale_leads: list[dict[str, Any]] = []
        hot_leads: list[dict[str, Any]] = []

        for lead in all_leads:
            updated = lead.get("updated_at", "")
            status = lead.get("status", "new")

            try:
                updated_dt = datetime.fromisoformat(updated) if updated else now
            except (ValueError, TypeError):
                updated_dt = now

            days_since_update = (now - updated_dt).days if updated_dt else 0

            if status in ("new", "contacted") and days_since_update >= LEAD_STALE_DAYS:
                stale_leads.append({
                    "id": lead["id"],
                    "name": lead.get("name", "Unknown"),
                    "business": lead.get("business_name", ""),
                    "status": status,
                    "last_contact": updated,
                    "days_stale": days_since_update,
                })
                if days_since_update >= 7:
                    alerts.append({
                        "severity": "warning",
                        "message": f"Lead '{lead.get('name', 'Unknown')}' stale for {days_since_update} days — needs follow-up",
                    })

            if lead.get("score", 0) >= 80 and status not in ("closed", "lost"):
                hot_leads.append({
                    "id": lead["id"],
                    "name": lead.get("name", "Unknown"),
                    "business": lead.get("business_name", ""),
                    "score": lead.get("score", 0),
                    "status": status,
                })

        pipeline_counts: dict[str, int] = {}
        for s in ["new", "contacted", "meeting", "proposal", "negotiation", "closed", "lost"]:
            pipeline_counts[s] = len([l for l in all_leads if l["status"] == s])

        if handoffs:
            alerts.append({
                "severity": "info",
                "message": f"{len(handoffs)} pending handoff(s) waiting for CEO action",
            })

        if hot_leads:
            alerts.append({
                "severity": "info",
                "message": f"{len(hot_leads)} hot lead(s) ready for CEO attention",
            })

        return {
            "timestamp": now.isoformat(),
            "pipeline": pipeline_counts,
            "total_leads": len(all_leads),
            "hot_leads": hot_leads,
            "stale_leads": stale_leads,
            "pending_handoffs": len(handoffs),
            "alerts": alerts,
        }


# Module-level singleton
_monitor: SBAMonitor | None = None


def get_monitor() -> SBAMonitor:
    global _monitor
    if _monitor is None:
        _monitor = SBAMonitor()
    return _monitor
