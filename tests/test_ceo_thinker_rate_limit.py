"""Regression tests for the CEO thinker under upstream rate limiting.

``CEOAutonomy._think`` builds its own httpx POST, so it never went through the
openai guard at all, and its repair retry fired a SECOND request immediately
when the first one came back HTTP 429. On a free tier that is two calls for
one decision, back to back, at the exact moment the gateway is refusing.

The thinker must register the 429, spend only the single call, and skip
entirely while the breaker is open. No network is touched here: httpx.post is
replaced with a recorder.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin import llm_throttle as throttle  # noqa: E402
from admin.agency import ceo_autonomy as ca  # noqa: E402


def _reset(monkeypatch) -> None:
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


def _configure_thinker(monkeypatch):
    """Give the thinker a base URL and key so it does not bail out early."""
    settings = SimpleNamespace(
        WORKSPACE_API_BASE="http://127.0.0.1:3001/v1",
        WORKSPACE_API_KEY="test-key",
        WORKSPACE_AGENT_MODEL="auto",
    )
    monkeypatch.setattr("admin.config.settings", settings, raising=False)
    monkeypatch.setitem(sys.modules, "admin.config.settings", settings)
    return settings


def _patch_httpx(monkeypatch, responses: list[int]) -> list[dict]:
    """Replace httpx.post with a recorder returning the given status codes."""
    import httpx

    seen: list[dict] = []

    def _post(url, headers=None, json=None, timeout=None):
        seen.append({"url": url, "json": json})
        status = responses[min(len(seen) - 1, len(responses) - 1)]
        body = {"choices": [{"message": {"content": "{}"}}]} if status == 200 else {}
        return SimpleNamespace(
            status_code=status,
            headers={},
            json=lambda: body,
        )

    monkeypatch.setattr(httpx, "post", _post)
    return seen


def _autonomy() -> ca.CEOAutonomy:
    return ca.CEOAutonomy()


def test_thinker_spends_one_call_on_a_429_not_two(monkeypatch):
    _reset(monkeypatch)
    _configure_thinker(monkeypatch)
    seen = _patch_httpx(monkeypatch, [429])

    result = asyncio.run(_autonomy()._think({"summary": {"total_leads": 0}}))

    assert len(seen) == 1, (
        "a 429 must not trigger the JSON repair retry; that was the second "
        "immediate call into a throttled upstream"
    )
    assert result["action"] == "observe"
    assert "429" in result["rationale"], result
    assert throttle.circuit_open() is True, "the 429 must have opened the breaker"


def test_thinker_skips_entirely_while_the_circuit_is_open(monkeypatch):
    _reset(monkeypatch)
    _configure_thinker(monkeypatch)
    seen = _patch_httpx(monkeypatch, [200])

    throttle.record_rate_limit(RuntimeError("HTTP 429 from gateway"))

    result = asyncio.run(_autonomy()._think({"summary": {"total_leads": 0}}))

    assert seen == [], "an open circuit must cost zero upstream calls"
    assert result["action"] == "observe"
    assert "rate limiting" in result["rationale"].lower(), result
    assert result["rationale"] != "", "the skip must say why, not look like a decision"


def test_a_plain_unusable_answer_still_gets_its_repair_retry(monkeypatch):
    """The 429 fix must not disable the legitimate JSON repair path."""
    _reset(monkeypatch)
    _configure_thinker(monkeypatch)
    seen = _patch_httpx(monkeypatch, [200])

    decision = asyncio.run(_autonomy()._think({"summary": {"total_leads": 0}}))

    assert len(seen) == 2, "a non-JSON answer is a real fault worth one repair call"
    assert decision["action"] == "observe"


def test_thinker_holds_a_concurrency_slot_and_releases_it(monkeypatch):
    _reset(monkeypatch)
    _configure_thinker(monkeypatch)
    _patch_httpx(monkeypatch, [429])

    asyncio.run(_autonomy()._think({"summary": {"total_leads": 0}}))

    concurrency = throttle.snapshot()["concurrency"]
    assert concurrency["in_flight"] == 0, "the slot must be released after the call"
    assert concurrency["peak_in_flight"] == 1


def test_status_surfaces_the_breaker_state(monkeypatch):
    """An idle agency and a throttled one must not look identical."""
    _reset(monkeypatch)

    class _Cursor:
        async def fetchone(self):
            return None

    class _Db:
        async def execute(self, *_a, **_k):
            return _Cursor()

        async def commit(self):
            return None

    async def _fake_db():
        return _Db()

    monkeypatch.setattr(ca, "get_workspace_db", _fake_db)
    throttle.record_rate_limit(RuntimeError("HTTP 429 from gateway"))

    state = asyncio.run(_autonomy().status())

    assert "llm_guards" in state, state.keys()
    assert state["llm_guards"]["circuit"]["state"] == "open"
    assert state["llm_guards"]["circuit"]["calls_refused_locally"] == 0
    assert state["llm_guards"]["concurrency"]["limit"] == throttle.MAX_CONCURRENCY


def test_status_survives_a_broken_guard_snapshot(monkeypatch):
    """A status field must never be able to break the status call."""
    _reset(monkeypatch)

    class _Cursor:
        async def fetchone(self):
            return None

    class _Db:
        async def execute(self, *_a, **_k):
            return _Cursor()

        async def commit(self):
            return None

    async def _fake_db():
        return _Db()

    def _boom():
        raise RuntimeError("snapshot exploded")

    monkeypatch.setattr(ca, "get_workspace_db", _fake_db)
    monkeypatch.setattr(ca, "llm_guard_snapshot", _boom)

    state = asyncio.run(_autonomy().status())

    assert state["llm_guards"]["error"], "the failure is reported, not hidden"
    assert "snapshot exploded" in state["llm_guards"]["error"]
