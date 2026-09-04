"""Reasoning Logger — Content Agent ke har step ka reasoning store karta hai.

Yeh file har visual job ke reasoning chain ko log karti hai.
Debugging, learning, aur quality tracking ke liye use hoti hai.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import logging

logger = logging.getLogger(__name__)

# Storage path
_REASONING_DIR = Path(__file__).parent.parent.parent / "data" / "reasoning_logs"


class ReasoningLogger:
    """Logs each reasoning step for a visual job."""

    def __init__(self, job_id: str, workspace_id: str, domain: str = "content", client_name: str = ""):
        self.job_id = job_id
        self.workspace_id = workspace_id
        self.domain = domain
        self.client_name = client_name
        self.steps: dict[str, dict[str, Any]] = {}
        self.start_time = time.time()
        self.total_tokens = 0

    def log_step(
        self,
        step_name: str,
        reasoning: str,
        output: dict[str, Any] | None = None,
        tokens_used: int = 0,
        duration_ms: float = 0,
    ) -> None:
        """Ek step ka reasoning log karo."""
        self.steps[step_name] = {
            "timestamp": time.time(),
            "reasoning": reasoning,
            "output": output or {},
            "tokens_used": tokens_used,
            "duration_ms": round(duration_ms, 1),
        }
        self.total_tokens += tokens_used
        logger.info(
            "[ReasoningChain] job=%s step=%s tokens=%d duration=%.0fms",
            self.job_id, step_name, tokens_used, duration_ms,
        )

    def get_summary(self) -> dict[str, Any]:
        """Full reasoning chain ka summary."""
        return {
            "job_id": self.job_id,
            "workspace_id": self.workspace_id,
            "client_name": self.client_name,
            "domain": self.domain,
            "steps": self.steps,
            "total_tokens": self.total_tokens,
            "total_time_seconds": round(time.time() - self.start_time, 1),
            "step_count": len(self.steps),
        }

    def save(self) -> str:
        """Reasoning log ko file mein save karo."""
        _REASONING_DIR.mkdir(parents=True, exist_ok=True)
        summary = self.get_summary()
        filename = f"{self.job_id}.json"
        filepath = _REASONING_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        return str(filepath)

    @staticmethod
    def create_job_id() -> str:
        """Naya unique job ID banao."""
        return f"reason_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def load(job_id: str) -> dict[str, Any] | None:
        """Kisi job ka reasoning log load karo."""
        filepath = _REASONING_DIR / f"{job_id}.json"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    @staticmethod
    def get_recent(limit: int = 10) -> list[dict[str, Any]]:
        """Recent reasoning logs."""
        if not _REASONING_DIR.exists():
            return []
        files = sorted(_REASONING_DIR.glob("*.json"), reverse=True)[:limit]
        logs = []
        for fp in files:
            with open(fp, "r", encoding="utf-8") as f:
                logs.append(json.load(f))
        return logs
