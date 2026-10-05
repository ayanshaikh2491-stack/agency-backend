"""Regression tests for the LLM concurrency ceiling.

Ten agents and the CEO thinker all reach the same free-tier gateway. Nothing
capped how many could be in flight at once, which is what earns the HTTP 429 in
the first place. ``guard()`` must admit at most MAX_CONCURRENCY calls at a time
and must fail loudly rather than queue forever.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin import llm_throttle as throttle  # noqa: E402


def _reset(monkeypatch, *, max_concurrency: int = 3) -> None:
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
        ("MAX_CONCURRENCY", max_concurrency),
    ):
        monkeypatch.setattr(throttle, name, value)


def test_no_more_than_the_limit_are_in_flight_at_once(monkeypatch):
    _reset(monkeypatch, max_concurrency=3)
    in_flight = 0
    peak = 0

    async def _one_call():
        nonlocal in_flight, peak
        async with throttle.guard():
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    async def _run():
        # Ten agents, the shape of the production burst.
        await asyncio.gather(*[_one_call() for _ in range(10)])

    asyncio.run(_run())

    assert peak <= 3, f"{peak} calls were in flight at once, ceiling is 3"
    assert peak == 3, "the ceiling should actually be used, not merely respected"
    assert throttle.snapshot()["concurrency"]["peak_in_flight"] == 3


def test_the_limit_is_configurable(monkeypatch):
    _reset(monkeypatch, max_concurrency=1)
    peak = 0
    in_flight = 0

    async def _one_call():
        nonlocal peak, in_flight
        async with throttle.guard():
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    async def _run():
        await asyncio.gather(*[_one_call() for _ in range(5)])

    asyncio.run(_run())

    assert peak == 1, "MAX_CONCURRENCY=1 must serialise every call"
    assert throttle.snapshot()["concurrency"]["limit"] == 1


def test_a_saturated_queue_raises_instead_of_waiting_forever(monkeypatch):
    _reset(monkeypatch, max_concurrency=1)
    monkeypatch.setattr(throttle, "CIRCUIT_QUEUE_TIMEOUT", 0.05)

    async def _run():
        # The Event must be bound to the loop that uses it.
        released = asyncio.Event()

        async def _holder():
            async with throttle.guard():
                await released.wait()

        async def _blocked():
            async with throttle.guard():  # pragma: no cover - must never run
                return "should not run"

        holder = asyncio.create_task(_holder())
        await asyncio.sleep(0.01)  # let the holder take the only slot
        try:
            await _blocked()
        finally:
            released.set()
            await holder

    with pytest.raises(throttle.ConcurrencyTimeoutError) as excinfo:
        asyncio.run(_run())

    # A refusal must be explicit, and it must not be mistaken for a result.
    assert "refused" in str(excinfo.value)
    assert isinstance(excinfo.value, throttle.LLMGuardError)


def test_slots_are_released_when_a_call_explodes(monkeypatch):
    _reset(monkeypatch, max_concurrency=1)

    async def _boom():
        async with throttle.guard():
            raise RuntimeError("upstream exploded mid-call")

    async def _run():
        with pytest.raises(RuntimeError):
            await _boom()
        # If the slot leaked, this second call would hang until the queue
        # timeout instead of completing immediately.
        async with throttle.guard():
            return "ok"

    assert asyncio.run(_run()) == "ok"
    assert throttle.snapshot()["concurrency"]["in_flight"] == 0
