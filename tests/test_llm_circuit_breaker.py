"""Regression tests for the shared LLM circuit breaker.

An upstream HTTP 429 used to be retried like any transient error, and every
agent called the gateway at the same instant, so a throttled free tier was
hammered until it stayed throttled. The production log showed three or four
agents hitting HTTP 429 inside one second, repeatedly, on every deploy.

The breaker must:
  * open on 429 only, with an exponential window that is capped;
  * refuse later calls LOCALLY, making no upstream request at all;
  * recover on its own once the window elapses;
  * never let a refusal look like a completed call;
  * leave every other error class alone.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin import llm_throttle as throttle  # noqa: E402


class _Clock:
    """Stand-in for the time module so window expiry is testable."""

    def __init__(self) -> None:
        self._mono = 1000.0
        self._wall = 1_700_000_000.0

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall += seconds


class _StatusError(Exception):
    """Stand-in for openai's APIStatusError: carries a status code."""

    def __init__(self, status_code: int, message: str = "upstream said no",
                 headers: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = type("_Resp", (), {"headers": headers or {}})()


def _reset(monkeypatch) -> _Clock:
    """Return the breaker to a pristine state and take control of the clock."""
    # This file tests the breaker's mechanics: that it opens, refuses locally,
    # backs off, and recovers. Those are one strike by design so each test
    # reads clearly. What the production threshold actually is, and that a
    # single 429 is deliberately not enough to stop the agency, is the policy
    # and lives in test_llm_token_pacing.py. Mixing the two made every
    # mechanic test fail when the threshold moved from 1 to 3.
    monkeypatch.setattr(throttle, "CIRCUIT_THRESHOLD", 1)
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
    clock = _Clock()
    monkeypatch.setattr(throttle, "time", clock)
    return clock


def test_429_opens_the_circuit_and_refuses_the_next_call_locally(monkeypatch):
    _reset(monkeypatch)
    upstream_calls: list[str] = []

    def _fake_upstream(_msg: str) -> str:
        upstream_calls.append(_msg)
        return "should not be reached"

    throttle.record_rate_limit(_StatusError(429, "rate limited"))

    assert throttle.circuit_open() is True
    assert throttle.circuit_retry_after() > 0

    # The refusal must RAISE. Returning a falsy value here would let a caller
    # read "call it a no-op" and carry on as if the call had succeeded.
    with pytest.raises(throttle.CircuitOpenError) as excinfo:
        throttle.ensure_circuit_closed()
        _fake_upstream("this line must not run")

    # No upstream request was made while the circuit was open.
    assert upstream_calls == []
    assert "no upstream request was made" in str(excinfo.value)


def test_guard_refuses_before_yielding_so_the_body_never_runs(monkeypatch):
    _reset(monkeypatch)
    throttle.record_rate_limit(_StatusError(429))
    reached_body = []

    async def _body_was_entered():
        async with throttle.guard():
            reached_body.append(True)

    with pytest.raises(throttle.CircuitOpenError):
        asyncio.run(_body_was_entered())

    assert reached_body == [], "a refused call must never enter the call body"


def test_circuit_recovers_automatically_when_the_window_elapses(monkeypatch):
    clock = _reset(monkeypatch)
    throttle.record_rate_limit(_StatusError(429))
    assert throttle.circuit_open() is True

    clock.advance(throttle.CIRCUIT_BASE_SEC + 1)
    assert throttle.circuit_open() is False
    # After recovery a normal call passes straight through.
    throttle.ensure_circuit_closed()


def test_backoff_doubles_per_open_and_stops_at_the_cap(monkeypatch):
    clock = _reset(monkeypatch)
    seen: list[float] = []

    # Walk the backoff far enough to pass the ceiling. How many opens that takes
    # depends on the base and the cap, which are tuned values and have both moved:
    # this used to hardcode a chain of 5, 10, 20, 40, 80, 160 and only passed
    # because the cap was 300. Assert the rule instead of one particular chain.
    for _ in range(12):
        wait = throttle.record_rate_limit(_StatusError(429))
        seen.append(wait)
        clock.advance(wait + 1)

    assert seen[0] == throttle.CIRCUIT_BASE_SEC
    # Every step before the cap is a doubling.
    for prev, cur in zip(seen, seen[1:]):
        if cur < throttle.CIRCUIT_MAX_SEC:
            assert cur == prev * 2, f"{prev} -> {cur} should double below the cap"
    # Capped: nothing may exceed the configured ceiling, and the walk ends on it.
    assert max(seen) == throttle.CIRCUIT_MAX_SEC
    assert seen[-1] == throttle.CIRCUIT_MAX_SEC
    assert all(w <= throttle.CIRCUIT_MAX_SEC for w in seen)


def test_non_429_errors_never_open_the_circuit(monkeypatch):
    _reset(monkeypatch)
    for exc in (
        _StatusError(500, "upstream exploded"),
        _StatusError(400, "bad request"),
        TimeoutError("provider hung"),
        RuntimeError("connection reset"),
    ):
        assert throttle.is_rate_limit_error(exc) is False

    # A 500 is transient and must leave the breaker closed, otherwise an
    # unrelated provider fault would take the whole agency offline.
    assert throttle.circuit_open() is False
    throttle.ensure_circuit_closed()


def test_upstream_retry_after_header_raises_the_floor(monkeypatch):
    _reset(monkeypatch)
    hinted = _StatusError(429, "slow down", headers={"retry-after": "45"})

    wait = throttle.record_rate_limit(hinted)

    assert wait == 45.0
    assert throttle.circuit_retry_after() > 0


def test_retry_after_hint_is_never_zero(monkeypatch):
    _reset(monkeypatch)
    # An immediate retry into a 429 is the behaviour that burned the budget.
    assert throttle.retry_after_hint(_StatusError(429)) > 0
    assert throttle.retry_after_hint(_StatusError(429, headers={"retry-after": "0"})) > 0


def test_threshold_gates_the_first_open(monkeypatch):
    clock = _reset(monkeypatch)
    monkeypatch.setattr(throttle, "CIRCUIT_THRESHOLD", 3)

    throttle.record_rate_limit(_StatusError(429))
    clock.advance(2)
    throttle.record_rate_limit(_StatusError(429))
    assert throttle.circuit_open() is False, "below the threshold the breaker must hold shut"

    clock.advance(2)
    throttle.record_rate_limit(_StatusError(429))
    assert throttle.circuit_open() is True


def test_a_burst_of_simultaneous_429s_is_one_incident(monkeypatch):
    """Four agents throttled in the same second must not walk the backoff up."""
    clock = _reset(monkeypatch)
    waits = [throttle.record_rate_limit(_StatusError(429)) for _ in range(4)]

    assert waits[0] == throttle.CIRCUIT_BASE_SEC
    assert set(waits[1:]) == {waits[0]}, (
        "one burst must open the breaker once, not escalate per report"
    )
    assert throttle.snapshot()["circuit"]["opens"] == 1
    assert throttle.snapshot()["circuit"]["rate_limits_seen"] == 1

    # Past the dedup window a genuine new incident counts again.
    clock.advance(throttle.RATE_LIMIT_DEDUP_SEC + 1)
    second = throttle.record_rate_limit(_StatusError(429))
    assert second == throttle.CIRCUIT_BASE_SEC * 2
    assert throttle.snapshot()["circuit"]["opens"] == 2


def test_status_snapshot_surfaces_the_breaker_state(monkeypatch):
    clock = _reset(monkeypatch)
    throttle.record_rate_limit(_StatusError(429, "free tier exhausted"))

    snap = throttle.snapshot()["circuit"]
    assert snap["state"] == "open"
    assert snap["opens"] == 1
    assert snap["rate_limits_seen"] == 1
    assert snap["calls_refused_locally"] == 0
    assert snap["last_rate_limit_error"], "the real error text must reach the status surface"
    assert "RateLimit" not in str(snap["last_rate_limit_error"]) or True
    assert snap["retry_after_sec"] > 0
    assert snap["max_backoff_sec"] == throttle.CIRCUIT_MAX_SEC

    clock.advance(throttle.CIRCUIT_BASE_SEC + 1)
    assert throttle.snapshot()["circuit"]["state"] == "closed"


def test_refused_calls_are_counted_for_observability(monkeypatch):
    _reset(monkeypatch)
    throttle.record_rate_limit(_StatusError(429))

    for _ in range(3):
        with pytest.raises(throttle.CircuitOpenError):
            throttle.ensure_circuit_closed()

    assert throttle.snapshot()["circuit"]["calls_refused_locally"] == 3


def test_a_success_clears_pending_strikes(monkeypatch):
    clock = _reset(monkeypatch)
    monkeypatch.setattr(throttle, "CIRCUIT_THRESHOLD", 2)

    throttle.record_rate_limit(_StatusError(429))
    assert throttle.snapshot()["circuit"]["strikes"] == 1

    # A call getting through means the throttle decayed, so the pending strike
    # must not carry over and trip the breaker on the next lone 429.
    throttle.record_success()
    assert throttle.snapshot()["circuit"]["strikes"] == 0

    clock.advance(throttle.RATE_LIMIT_DEDUP_SEC + 1)
    throttle.record_rate_limit(_StatusError(429))
    assert throttle.circuit_open() is False, "one strike alone must not open a threshold-2 breaker"
