"""The autonomy loop reported failed work as success.

Found by auditing a live run: 100 tasks, 92 carrying an error string, 91 of them
marked `done`. The agency looked productive and had produced nothing. These
tests pin the three fixes so the loop cannot quietly go back to lying.

  1. orchestration status must reflect its sub-agents
  2. the thinker must be shown what it already tried
  3. a task must not be `done` when every sub-agent failed
"""
import asyncio
import inspect

import pytest

from admin.agency import ceo_autonomy as ca


class _FakeDB:
    """Minimal async DB stand-in recording what was written."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.executed = []

    async def fetchall(self, sql, params=()):
        return self._rows

    async def fetchone(self, sql, params=()):
        return self._rows[0] if self._rows else None

    async def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return None

    async def commit(self):
        return None


@pytest.fixture
def autonomy():
    inst = ca.CEOAutonomy.__new__(ca.CEOAutonomy)
    # __new__ skips __init__, so the timeout the real constructor sets is absent.
    # Without it asyncio.wait_for(coro, timeout=None) behaves differently from
    # the running system.
    inst._agent_timeout = 30.0
    return inst


def _patch_routing(monkeypatch, per_agent):
    """Replace route_to_agent so each agent returns a canned string.

    The production code wraps it in asyncio.wait_for(timeout=...). That must be
    left alone: stubbing wait_for out would stop awaiting the coroutine and the
    test would pass for the wrong reason. The stub is fast enough that the real
    timeout never fires.
    """
    import admin.workspace.manager as manager

    async def route(workspace_id, agent_type, task, safe_only=True):
        await asyncio.sleep(0)
        return per_agent[agent_type]

    monkeypatch.setattr(manager, "route_to_agent", route, raising=False)


def _patch_persistence(monkeypatch, emit=None):
    """Silence the event/log writers so the tests assert only on the merge."""
    async def fake_emit(*a, **k):
        return None

    monkeypatch.setattr(ca, "emit_event", emit or fake_emit, raising=False)
    monkeypatch.setattr(ca, "get_workspace_db", lambda: None, raising=False)


def test_all_sub_agents_failing_is_an_error(autonomy, monkeypatch):
    """The exact production failure: three agents, three timeouts, one 'done'."""
    _patch_routing(monkeypatch, {
        "sba": "ERROR: SBA safe analysis failed: LLM call exceeded 25s",
        "content": "ERROR: CONTENT safe analysis failed: LLM call exceeded 25s",
        "website": "ERROR: WEBSITE safe analysis failed: LLM call exceeded 25s",
    })

    _patch_persistence(monkeypatch)

    result = asyncio.run(autonomy._run_orchestration(
        "ws_agency", ["sba", "content", "website"], "internal_analysis",
        "research a niche for TAGS Agency"))

    assert result["status"] == "error", (
        "every sub-agent failed; this must not be recorded as done"
    )
    assert "all 3 sub-agents failed" in result["error"]["orchestration"]


def test_partial_failure_is_degraded_not_error(autonomy, monkeypatch):
    """One agent dying does not erase the work the other two actually did."""
    _patch_routing(monkeypatch, {
        "sba": "Found 5 clinics in Pune with retainer above 20000/mo.",
        "content": "ERROR: CONTENT safe analysis failed: LLM call exceeded 25s",
        "website": "Hero: 'You pay for leads. You do not get patients.'",
    })

    _patch_persistence(monkeypatch)

    result = asyncio.run(autonomy._run_orchestration(
        "ws_agency", ["sba", "content", "website"], "internal_analysis",
        "research a niche"))

    assert result["status"] == "degraded"
    assert result["result"]["results"]["sba"].startswith("Found 5 clinics")
    assert result["result"]["results"]["website"].startswith("Hero:")


def test_all_success_is_done(autonomy, monkeypatch):
    """The happy path must not be broken by the new status logic."""
    _patch_routing(monkeypatch, {"sba": "5 clinics found.", "content": "Hook written."})

    _patch_persistence(monkeypatch)

    result = asyncio.run(autonomy._run_orchestration(
        "ws_agency", ["sba", "content"], "internal_analysis", "research a niche"))

    assert result["status"] == "done"
    assert result["error"] == {}


def test_thinker_is_shown_its_own_history(monkeypatch):
    """The loop must be able to see that it already tried this."""
    src = inspect.getsource(ca.CEOAutonomy._think)
    assert "_recent_decision_summary()" in src, (
        "the thinker prompt must include what the CEO already tried, or it "
        "will repeat a failed action forever"
    )
    assert "WHAT YOU ALREADY TRIED RECENTLY" in src
    assert "do NOT repeat it" in src


def test_history_helper_flags_real_work(monkeypatch):
    """observe/review_required are bookkeeping and must not read as attempts."""
    import admin.persistence as persistence

    rows = [
        {"action": "bootstrap_prospecting", "rationale": "No leads in pipeline",
         "created_at": "2026-10-07T08:23:11.252545+00:00"},
        {"action": "observe", "rationale": "no urgent internal work",
         "created_at": "2026-10-07T08:23:11.252545+00:00"},
    ]
    monkeypatch.setattr(persistence, "get_db", lambda: _FakeDB(rows), raising=False)

    out = asyncio.run(ca._recent_decision_summary())

    assert len(out) == 2
    by_action = {o["action"]: o for o in out}
    assert by_action["bootstrap_prospecting"]["was_real_work"] is True
    assert by_action["observe"]["was_real_work"] is False


def test_history_failure_does_not_break_the_thinker(monkeypatch):
    """A dead DB must not stop the CEO deciding anything at all."""
    import admin.persistence as persistence

    def boom():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(persistence, "get_db", boom, raising=False)
    assert asyncio.run(ca._recent_decision_summary()) == []
