"""Regression tests for the boot burst stagger in the 24/7 worker.

Every loop used to be handed to ``asyncio.gather`` with no offset, so all six
started inside the same event-loop iteration. The CEO autonomy tick, the CEO
scheduler and the two queue drainers then reached the LLM gateway together, and
the production log showed three or four agents hitting HTTP 429 inside one
second, repeated on every deploy.

The work is still scheduled, only spread. The offsets must be deterministic so
a restart reproduces the same order, and disabling the stagger must return the
old instant-start behaviour for anyone who wants it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.workers import main as worker  # noqa: E402

WORKER_NAMES = [
    "ceo_autonomy",
    "scheduler",
    "service_delivery",
    "sba_autopilot",
    "agent_task_queue",
    "handoff_queue",
]


def test_offsets_are_deterministic(monkeypatch):
    """A restart must reproduce the same schedule, so no random() jitter."""
    monkeypatch.setattr(worker, "BOOT_STAGGER_SEC", 2.5)
    first = [worker.boot_offset(n, i) for i, n in enumerate(WORKER_NAMES)]
    second = [worker.boot_offset(n, i) for i, n in enumerate(WORKER_NAMES)]
    assert first == second


def test_boot_burst_is_spread_not_simultaneous(monkeypatch):
    monkeypatch.setattr(worker, "BOOT_STAGGER_SEC", 2.5)
    offsets = [worker.boot_offset(n, i) for i, n in enumerate(WORKER_NAMES)]

    assert all(o > 0 for o in offsets), offsets
    # The whole point: no two workers start at the same instant.
    assert len(set(offsets)) == len(offsets), offsets
    assert offsets == sorted(offsets), "the stagger should preserve the listed order"
    assert offsets[-1] >= 2.5 * (len(WORKER_NAMES) - 1)


def test_stagger_can_be_disabled(monkeypatch):
    monkeypatch.setattr(worker, "BOOT_STAGGER_SEC", 0.0)
    offsets = [worker.boot_offset(n, i) for i, n in enumerate(WORKER_NAMES)]
    assert offsets == [0.0] * len(WORKER_NAMES)


def test_stagger_length_is_configurable(monkeypatch):
    monkeypatch.setattr(worker, "BOOT_STAGGER_SEC", 0.5)
    small = worker.boot_offset("scheduler", 1)
    monkeypatch.setattr(worker, "BOOT_STAGGER_SEC", 10.0)
    large = worker.boot_offset("scheduler", 1)
    assert large > small


def test_supervise_delays_the_start_by_the_offset(monkeypatch):
    """The offset is honoured before start(), and start is never dropped."""
    started_at: list[float] = []

    async def _run():
        begin = asyncio.get_running_loop().time()

        async def _start():
            started_at.append(asyncio.get_running_loop().time() - begin)
            await asyncio.Event().wait()  # a real 24/7 loop never returns

        task = asyncio.create_task(
            worker._supervise("loop", _start, start_delay=0.15))
        await asyncio.sleep(0.02)
        assert started_at == [], "the loop must not start before its offset elapses"
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())

    assert started_at, "the staggered worker must still be started"
    assert started_at[0] >= 0.15, started_at


def test_staggered_workers_do_not_all_start_together(monkeypatch):
    """The production shape: six loops, staggered starts, never one instant."""
    monkeypatch.setattr(worker, "BOOT_STAGGER_SEC", 0.05)
    begin_times: dict[str, float] = {}

    async def _run():
        begin = asyncio.get_running_loop().time()

        def _make(name: str):
            async def _start():
                begin_times[name] = asyncio.get_running_loop().time() - begin
                await asyncio.Event().wait()
            return _start

        tasks = [
            asyncio.create_task(worker._supervise(
                name, _make(name), start_delay=worker.boot_offset(name, i)))
            for i, name in enumerate(WORKER_NAMES)
        ]
        await asyncio.sleep(0.6)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(_run())

    assert len(begin_times) == len(WORKER_NAMES), begin_times
    assert len(set(begin_times.values())) == len(WORKER_NAMES), (
        f"workers still started in lockstep: {begin_times}"
    )
