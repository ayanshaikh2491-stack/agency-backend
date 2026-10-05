"""Regression tests for 429 handling on the workspace LLM call paths.

``_call_with_retry`` caught every exception in one bucket and retried it after
1s then 2s. An HTTP 429 landed in that bucket, so a throttled upstream was
re-hit almost immediately, which extends the throttle instead of riding it
out. The safe/generic routers also had no guard at all, so N agents could
enter the gateway in the same instant.

A 429 must now open the breaker, a call made while it is open must never
reach the upstream, and the refusal must be reported as a failure rather than
shaped like an analysis.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin import llm_throttle as throttle  # noqa: E402
from admin.api.models.schemas import WorkspaceOut  # noqa: E402
from admin.workspace import manager  # noqa: E402


class _RateLimited(Exception):
    """Stand-in for openai.RateLimitError: an error carrying a 429 status."""

    def __init__(self, message: str = "rate limited") -> None:
        super().__init__(message)
        self.status_code = 429
        self.response = SimpleNamespace(headers={})


def _reset(monkeypatch) -> None:
    """Pristine breaker plus a breaker window that will not expire mid-test."""
    for name, value in (
        ("_circuit_open_until", 0.0),
        ("_circuit_strikes", 0),
        ("_circuit_opens", 0),
        ("_circuit_backoff", throttle.CIRCUIT_BASE_SEC),
        ("_rate_limits", 0),
        ("_last_rate_limit_at", 0.0),
        ("_last_rate_limit_error", ""),
        ("_refused_locally", 0),
        ("_in_flight", 0),
        ("_peak_in_flight", 0),
        ("_slots", None),
        ("_slots_loop", None),
        ("_slots_size", 0),
    ):
        monkeypatch.setattr(throttle, name, value)


def _install_workspace(monkeypatch):
    ws = WorkspaceOut(
        id="ws_test",
        name="Acme",
        client_name="Acme Inc",
        description="",
        created_at=datetime.now(timezone.utc),
        agents=["analyzing"],
    )
    monkeypatch.setitem(manager._workspaces, "ws_test", ws.model_dump())
    return ws


def _patch_openai(monkeypatch, create):
    client = SimpleNamespace()
    client.chat = SimpleNamespace()
    client.chat.completions = SimpleNamespace(create=create)
    monkeypatch.setattr(__import__("openai"), "AsyncOpenAI", lambda **_: client)


def test_429_is_not_retried_immediately(monkeypatch):
    """A throttled agent gets one attempt, not three back-to-back re-hits."""
    _reset(monkeypatch)
    attempts: list[str] = []

    class ThrottledAgent:
        async def chat(self, message):
            attempts.append(message)
            raise _RateLimited("free tier exhausted")

    with pytest.raises(Exception) as excinfo:
        asyncio.run(manager._call_with_retry(ThrottledAgent(), "analyse", max_retries=2))

    assert len(attempts) == 1, (
        "a 429 must not be retried inside the same call; it opened the breaker instead"
    )
    # The failure is surfaced, and the breaker is now open as a result.
    assert throttle.circuit_open() is True
    assert "circuit is open" in str(excinfo.value).lower()


def test_transient_non_429_errors_are_still_retried(monkeypatch):
    """Only 429 changes behaviour: a 500 keeps the existing retry schedule."""
    _reset(monkeypatch)
    attempts: list[str] = []

    class FlakyAgent:
        async def chat(self, message):
            attempts.append(message)
            raise RuntimeError("upstream exploded")

    with pytest.raises(RuntimeError):
        asyncio.run(manager._call_with_retry(FlakyAgent(), "analyse", max_retries=2))

    assert len(attempts) == 3, "max_retries=2 means three attempts in total"
    assert throttle.circuit_open() is False, "a 500 must not open the breaker"


def test_a_429_then_a_recovery_still_produces_an_answer(monkeypatch):
    """When the breaker does not trip the retry can still succeed."""
    _reset(monkeypatch)
    monkeypatch.setattr(throttle, "CIRCUIT_THRESHOLD", 5)
    attempts: list[str] = []

    class FlakyAgent:
        async def chat(self, message):
            attempts.append(message)
            if len(attempts) == 1:
                raise _RateLimited("slow down")
            return "Revenue rose 12% quarter on quarter.", []

    result = asyncio.run(manager._call_with_retry(FlakyAgent(), "analyse", max_retries=2))

    assert result == "Revenue rose 12% quarter on quarter."
    assert len(attempts) == 2


def test_safe_router_refuses_locally_while_the_circuit_is_open(monkeypatch):
    """An open circuit must cost zero upstream calls, not one rejected call."""
    _reset(monkeypatch)
    _install_workspace(monkeypatch)
    upstream_calls: list[str] = []

    async def _create(**kwargs):
        upstream_calls.append(kwargs.get("messages") or [])
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="never reached", tool_calls=None),
                finish_reason="stop",
            )],
        )

    _patch_openai(monkeypatch, _create)
    throttle.record_rate_limit(_RateLimited())

    result = asyncio.run(
        manager._route_to_agent_safe("ws_test", "analyzing", "Analyse traffic."))

    assert upstream_calls == [], (
        "the call must be refused locally; nothing should reach the gateway"
    )
    assert result.startswith("ERROR:"), (
        f"a refused call must not look like an analysis: {result!r}"
    )
    assert "blocked" in result


def test_safe_router_registers_a_429_and_reports_the_real_error(monkeypatch):
    _reset(monkeypatch)
    _install_workspace(monkeypatch)
    calls: list[str] = []

    async def _create(**kwargs):
        calls.append("call")
        raise _RateLimited("free tier exhausted")

    _patch_openai(monkeypatch, _create)

    result = asyncio.run(
        manager._route_to_agent_safe("ws_test", "analyzing", "Analyse traffic."))

    assert len(calls) == 1, "a 429 is not retried by the safe router"
    assert result.startswith("ERROR:"), result
    # The upstream's own words must survive into the operator-facing report
    # rather than being flattened into a generic failure.
    assert "free tier exhausted" in result, (
        f"the real upstream error must survive into the report: {result!r}"
    )
    assert throttle.circuit_open() is True


def test_safe_router_still_returns_a_real_answer_when_healthy(monkeypatch):
    """The guard must not break the normal path."""
    _reset(monkeypatch)
    _install_workspace(monkeypatch)
    calls: list[str] = []

    async def _create(**kwargs):
        calls.append("call")
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="Traffic fell 22% week on week.",
                                       tool_calls=None),
                finish_reason="stop",
            )],
        )

    _patch_openai(monkeypatch, _create)

    result = asyncio.run(
        manager._route_to_agent_safe("ws_test", "analyzing", "Analyse traffic."))

    assert result == "Traffic fell 22% week on week."
    assert len(calls) == 1


def test_a_429_spent_through_the_sdk_and_the_retry_helper_opens_one_incident(
        monkeypatch):
    """The patched SDK reports the 429, then the retry helper sees it too."""
    _reset(monkeypatch)

    # Simulate install() having already recorded the 429 on the way up.
    throttle.record_rate_limit(_RateLimited("free tier exhausted"))
    before = throttle.snapshot()["circuit"]["opens"]

    throttle.record_rate_limit(_RateLimited("free tier exhausted"))

    assert throttle.snapshot()["circuit"]["opens"] == before, (
        "one throttling incident must not escalate the backoff twice"
    )
    assert throttle.snapshot()["circuit"]["next_backoff_sec"] == throttle.CIRCUIT_BASE_SEC * 2


def test_a_healthy_call_keeps_the_concurrency_slot_balanced(monkeypatch):
    _reset(monkeypatch)
    _install_workspace(monkeypatch)

    async def _create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="OK", tool_calls=None),
                finish_reason="stop",
            )],
        )

    _patch_openai(monkeypatch, _create)
    asyncio.run(manager._route_to_agent_safe("ws_test", "analyzing", "probe"))

    concurrency = throttle.snapshot()["concurrency"]
    assert concurrency["in_flight"] == 0, "the slot must be released after the call"
    assert concurrency["peak_in_flight"] == 1
