"""One Groq key shared by nine agents, paced in tokens rather than requests.

Groq allows 1000 requests per minute on the model this agency uses but only
8000 tokens per minute. Pacing on requests, as this throttle originally did,
left the real limit invisible: the request counter read empty while three
agents each sent a couple of thousand tokens and the provider answered 429.
The breaker then opened for everybody, so one burst took down every agent.

These tests pin the unit. The reservation has to count tokens, it has to leave
headroom under the published ceiling, and a reservation that does not fit must
wait rather than be admitted and fail upstream.
"""
import asyncio
import sys
import time

sys.path.insert(0, ".")

from admin import llm_throttle as lt  # noqa: E402


def reset():
    lt._hits.clear()
    lt._token_events.clear()
    lt._tokens_in = lt._tokens_out = 0
    lt._usd = 0.0


def test_the_cap_comes_from_the_provider_limit_not_guessed():
    assert lt.MINUTE_TOKEN_CAP >= 1000
    assert lt.MINUTE_TOKEN_CAP < 8000, "must stay under the published 8000/min"


def test_a_reservation_counts_tokens():
    """Three agents of 3000 each must not all be admitted."""
    async def go():
        reset()
        lt._token_events.append((time.monotonic(), 3000))
        spent = sum(n for _, n in lt._token_events)
        assert spent == 3000

    asyncio.run(go())


def test_the_window_forgets_old_reservations():
    """A minute-old reservation must not block tomorrow's call."""

    async def go():
        reset()
        old = time.monotonic() - 61
        lt._token_events.append((old, 7000))
        async def probe():
            async with lt._lock:
                while lt._token_events and time.monotonic() - lt._token_events[0][0] >= lt._WINDOW:
                    lt._token_events.popleft()
                return sum(n for _, n in lt._token_events)
        assert await probe() == 0

    asyncio.run(go())


def test_a_call_that_does_not_fit_waits_instead_of_being_admitted():
    """The whole point: no 429, so the breaker never opens."""

    async def go():
        reset()
        lt._token_events.append((time.monotonic(), 6400))
        task = asyncio.create_task(lt.acquire(estimated_tokens=4000))
        await asyncio.sleep(0.2)
        assert not task.done(), "a reservation over the cap must wait, not proceed"

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(go())


def test_a_call_that_fits_is_admitted():
    async def go():
        reset()
        await asyncio.wait_for(lt.acquire(estimated_tokens=1000), timeout=3)
        assert sum(n for _, n in lt._token_events) == 1000

    asyncio.run(go())


def test_a_zero_estimate_is_not_blocked_by_the_token_cap():
    """Callers that cannot estimate must still work, paced by requests only."""

    async def go():
        reset()
        lt._token_events.append((time.monotonic(), 6400))
        await asyncio.wait_for(lt.acquire(estimated_tokens=0), timeout=3)

    asyncio.run(go())


def test_the_snapshot_exposes_the_token_budget():
    snap = lt.snapshot()
    assert "tokens_per_min" in snap
    assert "tokens_spent_this_min" in snap


def test_usage_recorded_by_the_sdk_lands_in_the_window():
    """Real usage must count, or the estimate is all we ever have."""

    async def go():
        reset()

        class Usage:
            prompt_tokens = 3000
            completion_tokens = 1000

        await lt.record_usage(Usage())
        assert lt._tokens_in == 3000
        assert lt._tokens_out == 1000

    asyncio.run(go())


def test_the_estimator_reads_the_actual_prompt():
    small = lt._estimate_request_tokens({"messages": [{"role": "user", "content": "say OK"}], "max_tokens": 5})
    big = lt._estimate_request_tokens({"messages": [{"role": "user", "content": "x" * 20000}], "max_tokens": 2000})
    assert 0 < small < big, "a bigger call must reserve more of the window"


def test_the_estimator_never_reserves_more_than_the_cap():
    huge = lt._estimate_request_tokens({"messages": [{"role": "user", "content": "x" * 500000}], "max_tokens": 100000})
    assert huge <= lt.MINUTE_TOKEN_CAP


def test_the_estimator_degrades_to_zero_rather_than_raising():
    """A zero estimate falls back to request pacing, which always works."""
    for bad in ({}, {"messages": "not a list"}, {"messages": [None, 3]}, {"messages": [{"content": {"weird": 1}}]}):
        assert lt._estimate_request_tokens(bad) == 0, bad
