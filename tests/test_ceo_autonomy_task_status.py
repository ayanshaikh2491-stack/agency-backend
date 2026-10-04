"""Regression test for how CEO autonomy records an unusable agent result.

``CEOAutonomy._run_task`` used to set ``status = "done"`` for anything that came
back from ``route_to_agent``, including an empty string. That is how a dispatch
which produced nothing was persisted as a completed task with an empty result.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.agency import ceo_autonomy as ca  # noqa: E402

MALFORMED_TOOL_CALL = "<function_call><list_directory></list_directory>"


class _FakeCursor:
    async def fetchone(self):
        return None


class _FakeDb:
    async def execute(self, *_args, **_kwargs):
        return _FakeCursor()

    async def commit(self):
        return None


def _stub_environment(monkeypatch, route_result):
    async def fake_db():
        return _FakeDb()

    async def fake_emit(*_args, **_kwargs):
        return None

    async def fake_route(workspace_id, agent_type, message, *, safe_only=False):
        if isinstance(route_result, Exception):
            raise route_result
        return route_result

    monkeypatch.setattr(ca, "get_workspace_db", fake_db)
    monkeypatch.setattr(ca, "emit_event", fake_emit)

    import admin.agency.agent_bus as agent_bus
    import admin.workspace.manager as manager

    monkeypatch.setattr(
        agent_bus, "get_bus", lambda: SimpleNamespace(
            brief=lambda *a, **k: "", respond=lambda *a, **k: None,
        ),
    )
    monkeypatch.setattr(manager, "route_to_agent", fake_route)


def _autonomy():
    autonomy = object.__new__(ca.CEOAutonomy)
    autonomy._agent_timeout = 5.0
    return autonomy


def _run(route_result):
    return asyncio.wait_for(
        _autonomy()._run_task("ws_test", "analyzing", "internal_analysis", "Analyse traffic."),
        timeout=20,
    )


def test_empty_result_is_an_error_not_a_done_task(monkeypatch):
    _stub_environment(monkeypatch, "")
    out = asyncio.run(_run(""))
    assert out["status"] == "error", out
    assert "empty" in out["error"].lower(), out


def test_explicit_router_failure_is_an_error_not_a_done_task(monkeypatch):
    _stub_environment(monkeypatch, "ERROR: ANALYZING safe analysis failed: bad model output")
    out = asyncio.run(_run("ERROR: ANALYZING safe analysis failed: bad model output"))
    assert out["status"] == "error", out
    assert "no usable output" in out["error"], out


def test_malformed_markup_is_an_error_not_a_done_task(monkeypatch):
    # The exact pre-fix router behaviour: markup returned as if it were output.
    _stub_environment(monkeypatch, MALFORMED_TOOL_CALL)
    out = asyncio.run(_run(MALFORMED_TOOL_CALL))
    assert out["status"] == "error", out


def test_a_real_answer_is_still_done(monkeypatch):
    _stub_environment(monkeypatch, "Traffic fell 22% week over week.")
    out = asyncio.run(_run("Traffic fell 22% week over week."))
    assert out["status"] == "done", out
    assert out["result"] == "Traffic fell 22% week over week."