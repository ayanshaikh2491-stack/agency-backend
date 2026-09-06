"""TAGS Agency OS — Real-tool runtime.

Per-agent, per-workspace isolation layer that gives every agency agent:
  - a real E2B sandbox (code/command execution) when E2B_API_KEY is set,
    with a safe local subprocess fallback when it is not,
  - real third-party integrations via Composio (Gmail, Calendar, Slack,
    HubSpot, Sheets, Notion, ...) when COMPOSIO_API_KEY is set,
  - a treasury/spend-policy envelope (borrowed from the automaton design
    pattern) that caps and gates every external action so an agent can
    never silently run the wrong real tool.

Everything here is KEY-GATED and OFFLINE-SAFE: importing this package, and
calling any method, never raises when keys are absent. With no keys the
sandbox falls back to a local subprocess and integrations report
"unavailable" instead of crashing. That is what lets the whole agency run
"without error" tonight with the real client arriving tomorrow.
"""

from __future__ import annotations

from admin.runtime.integrations import WorkspaceIntegrations
from admin.runtime.registry import AgentRuntime, get_agent_runtime
from admin.runtime.sandbox import AgentSandbox
from admin.runtime.spend_policy import SpendPolicy

__all__ = [
    "AgentSandbox",
    "WorkspaceIntegrations",
    "SpendPolicy",
    "AgentRuntime",
    "get_agent_runtime",
]
