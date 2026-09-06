"""Real third-party integrations via Composio.

Composio (https://composio.dev) is the agency's real integration layer:
100+ SaaS apps (Gmail, Google Calendar, Slack, HubSpot, Google Sheets,
Notion, Linear, ...) exposed to every agent as callable tools. This module
wraps the Composio 1.x ``Composio`` client and exposes a simple, offline-safe
surface that every workspace agent can use.

KEY-GATED / OFFLINE-SAFE:
  - With ``COMPOSIO_API_KEY`` set, ``list_tools`` / ``execute`` call the real
    Composio backend.
  - Without it, ``available`` is ``False`` and every method returns a clean
    ``{"status": "unavailable", ...}`` dict instead of raising, so an agent
    can degrade gracefully (e.g. fall back to the existing SBA SMTP email).

A workspace-scoped ``user_id`` (the workspace id) is threaded through so each
workspace's connections stay scoped to that workspace — consistent with the
per-workspace isolation used elsewhere (store, email, sandbox).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from admin.config import settings

logger = logging.getLogger(__name__)

# Default app slugs the agency actually uses today. Extend as needed; each
# workspace can still request any slug via list_tools/execute.
DEFAULT_APP_SLUGS = [
    "gmail",
    "googlesheets",
    "googledocs",
    "googledrive",
    "googlecalendar",
    "slack",
    "notion",
    "hubspot",
]


class _Unavailable:
    """Stand-in returned when Composio is not configured."""

    available = False

    def list_tools(self, *a, **k):
        return []

    def execute(self, *a, **k):
        return {"status": "unavailable", "error": "COMPOSIO_API_KEY not configured"}


class WorkspaceIntegrations:
    """Composio wrapper bound to one workspace (user_id = workspace id)."""

    def __init__(self, workspace_id: str, user_id: str | None = None) -> None:
        self.workspace_id = workspace_id
        # Composio scopes connections per "user_id"; use the workspace id so a
        # client's Gmail/Sheets never mixes with another client's.
        self.user_id = user_id or workspace_id
        self.available = bool(
            getattr(settings, "COMPOSIO_API_KEY", "") or os.environ.get("COMPOSIO_API_KEY", "")
        )
        self._client: Any = None
        if self.available:
            try:
                from composio import Composio

                self._client = Composio()
                logger.info("WorkspaceIntegrations[%s] -> Composio LIVE", workspace_id)
            except Exception as exc:
                logger.warning("Composio init failed for %s: %s", workspace_id, exc)
                self.available = False
                self._client = None
        else:
            logger.info("WorkspaceIntegrations[%s] -> unavailable (no key)", workspace_id)

    # ── Tool discovery ─────────────────────────────────────────────────────

    def list_tool_slugs(self, apps: list[str] | None = None) -> list[str]:
        """Return real Composio tool slugs for the given apps (workspace-scoped)."""
        if not self.available or self._client is None:
            return []
        try:
            collection = self._client.tools.get(
                user_id=self.user_id,
                toolkits=apps or DEFAULT_APP_SLUGS,
            )
            slugs = [getattr(t, "slug", None) for t in collection]
            return [s for s in slugs if s]
        except Exception as exc:
            logger.warning("Composio list_tool_slugs failed: %s", exc)
            return []

    # ── Execution ──────────────────────────────────────────────────────────

    def execute(
        self,
        tool_slug: str,
        arguments: dict[str, Any],
        connected_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute one real Composio tool for this workspace.

        Returns a normalized dict with ``status`` (``ok`` / ``error`` /
        ``unavailable``) so callers need never branch on availability.
        """
        if not self.available or self._client is None:
            return {
                "status": "unavailable",
                "error": "COMPOSIO_API_KEY not configured",
                "tool": tool_slug,
            }
        try:
            resp = self._client.tools.execute(
                slug=tool_slug,
                arguments=arguments or {},
                connected_account_id=connected_account_id,
                user_id=self.user_id,
            )
            data = getattr(resp, "data", None)
            if isinstance(data, str):
                return {"status": "ok", "tool": tool_slug, "data": data}
            return {"status": "ok", "tool": tool_slug, "data": data}
        except Exception as exc:
            logger.warning("Composio execute(%s) failed: %s", tool_slug, exc)
            return {"status": "error", "tool": tool_slug, "error": str(exc)[:300]}

    def as_dict_status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "default_apps": DEFAULT_APP_SLUGS,
        }
