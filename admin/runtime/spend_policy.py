"""Treasury / spend-policy envelope for external agent actions.

Borrowed from the Conway automaton reference design's treasury/spend-policy
pattern: before any agent performs an EXTERNAL, side-effecting action (sending
email, posting to a real integration, spinning up a paid sandbox), the action
must pass through a policy gate. This keeps real tools safe — an agent can
self-execute, but only within owner-defined caps and an allowlist.

OFFLINE-SAFE: the policy always returns a decision; with no config it uses
sensible defaults (allow low-risk, cap blind external sends). It never raises.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from admin.config import settings


class RiskLevel(str, Enum):
    LOW = "low"        # local-only / read / sandbox
    MEDIUM = "medium"  # single external write (one email, one post)
    HIGH = "high"      # bulk external sends, payments, deletions


# Per-risk default decision when the action is not explicitly allow/deny.
DEFAULT_ALLOW = {
    RiskLevel.LOW: True,
    RiskLevel.MEDIUM: True,
    RiskLevel.HIGH: False,  # owner must opt in (mirrors SBA follow-up gate)
}


@dataclass
class PolicyConfig:
    """Owner-tunable envelope. Read from settings but never required."""

    deny: set[str] = field(default_factory=set)            # exact action names blocked
    allow: set[str] = field(default_factory=set)           # exact action names allowed
    allow_risk: set[RiskLevel] = field(
        default_factory=lambda: {RiskLevel.LOW, RiskLevel.MEDIUM}
    )
    max_external_per_window: int = 25                      # cap external writes/window
    window_seconds: int = 3600

    @classmethod
    def from_settings(cls) -> "PolicyConfig":
        cfg = cls()
        # Optional knobs; safe defaults if absent.
        try:
            cfg.max_external_per_window = int(
                getattr(settings, "AGENT_MAX_EXTERNAL_PER_HOUR", 25)
            )
        except Exception:
            pass
        if str(getattr(settings, "AGENT_ALLOW_HIGH_RISK", "false")).lower() in ("1", "true", "yes"):
            cfg.allow_risk.add(RiskLevel.HIGH)
        return cfg


@dataclass
class Decision:
    allow: bool
    reason: str
    risk: RiskLevel


class SpendPolicy:
    """Windowed cap + allow/deny envelope for agent actions."""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig.from_settings()
        self._external_count = 0
        self._window_start = time.time()

    def _reset_if_expired(self) -> None:
        if time.time() - self._window_start >= self.config.window_seconds:
            self._external_count = 0
            self._window_start = time.time()

    def evaluate(self, action: str, risk: RiskLevel = RiskLevel.MEDIUM) -> Decision:
        """Decide whether ``action`` may run right now."""
        if action in self.config.deny:
            return Decision(False, f"action '{action}' is explicitly denied", risk)
        if action in self.config.allow:
            self._external_count += 1
            return Decision(True, f"action '{action}' explicitly allowed", risk)

        if risk not in self.config.allow_risk:
            return Decision(False, f"risk '{risk.value}' not in allowed set", risk)

        self._reset_if_expired()
        if risk in (RiskLevel.MEDIUM, RiskLevel.HIGH):
            if self._external_count >= self.config.max_external_per_window:
                return Decision(
                    False,
                    f"external cap {self.config.max_external_per_window}/"
                    f"{self.config.window_seconds}s reached",
                    risk,
                )
            self._external_count += 1
        return Decision(True, "within policy", risk)

    def remaining(self) -> int:
        self._reset_if_expired()
        return max(0, self.config.max_external_per_window - self._external_count)
