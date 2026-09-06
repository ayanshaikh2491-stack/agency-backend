"""Per-agent runtime registry.

One lightweight, cached object per (agent_type, workspace_id) that bundles:
  - a sandbox (E2B real / local fallback)
  - the workspace's real integrations (Composio)
  - a spend-policy envelope

Every workspace agent now receives this via ``route_to_agent`` and the SBA
flagship graph, so the whole agency runs with real tools while staying
offline-safe and key-gated.
"""

from __future__ import annotations

from typing import Any

from admin.runtime.integrations import WorkspaceIntegrations
from admin.runtime.sandbox import AgentSandbox, get_agent_sandbox
from admin.runtime.spend_policy import SpendPolicy

# (agent_type, workspace_id) -> AgentRuntime
_REGISTRY: dict[tuple[str, str], "AgentRuntime"] = {}


class AgentRuntime:
    """Everything an agent needs to self-execute with real tools."""

    def __init__(
        self,
        agent_type: str,
        workspace_id: str,
        *,
        sandbox: AgentSandbox | None = None,
        integrations: WorkspaceIntegrations | None = None,
        policy: SpendPolicy | None = None,
    ) -> None:
        self.agent_type = agent_type
        self.workspace_id = workspace_id
        self.sandbox = sandbox or get_agent_sandbox(agent_type, workspace_id)
        self.integrations = integrations or WorkspaceIntegrations(workspace_id)
        self.policy = policy or SpendPolicy()

    def status(self) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "workspace_id": self.workspace_id,
            "sandbox": {
                "backend": self.sandbox.backend_kind,
                "sandbox_id": self.sandbox.sandbox_id,
            },
            "integrations": self.integrations.as_dict_status(),
            "external_remaining": self.policy.remaining(),
        }


def get_agent_runtime(agent_type: str, workspace_id: str) -> AgentRuntime:
    """Cached per-agent runtime. Construction is always safe (never raises)."""
    key = (str(agent_type), str(workspace_id))
    rt = _REGISTRY.get(key)
    if rt is None:
        rt = AgentRuntime(agent_type, workspace_id)
        _REGISTRY[key] = rt
    return rt
