"""Regression tests for the CEO autonomy dispatch path in workspace.manager.

``route_to_agent(..., safe_only=True)`` is what the CEO autonomy loop calls at
admin/agency/ceo_autonomy.py:1309. It used to return
``resp.choices[0].message.content or "No response generated."``, so a model
answer made of a tool call and nothing else was handed back as if it were the
agent's analysis and the task was recorded as done.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openai  # noqa: E402

from admin.api.models.schemas import WorkspaceOut  # noqa: E402
from admin.workspace import manager  # noqa: E402

MALFORMED_TOOL_CALL = "<function_call><list_directory></list_directory>"


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


def _patch_openai(monkeypatch, contents):
    """Make every AsyncOpenAI call return the given contents in order."""
    calls: list[list[dict]] = []

    async def _create(**kwargs):
        calls.append(kwargs.get("messages") or [])
        content = contents[min(len(calls) - 1, len(contents) - 1)]
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason="stop",
            )],
        )

    client = SimpleNamespace()
    client.chat = SimpleNamespace()
    client.chat.completions = SimpleNamespace(create=_create)
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **_: client)
    return calls


def test_malformed_tool_call_is_not_returned_as_the_analysis(monkeypatch):
    _install_workspace(monkeypatch)
    calls = _patch_openai(monkeypatch, [MALFORMED_TOOL_CALL])

    result = asyncio.run(manager._route_to_agent_safe("ws_test", "analyzing", "Analyse traffic."))

    # The markup may appear inside the failure reason (it is what the operator
    # needs in the stored error), but it must never be handed back as the
    # agent's analysis.
    assert result.strip() != MALFORMED_TOOL_CALL.strip()
    assert result.startswith("ERROR:"), result
    assert "tool-call markup" in result, result
    assert len(calls) == 2, "the model should get one corrective retry"


def test_empty_content_is_not_returned_as_the_analysis(monkeypatch):
    _install_workspace(monkeypatch)
    _patch_openai(monkeypatch, [""])

    result = asyncio.run(manager._route_to_agent_safe("ws_test", "analyzing", "Analyse traffic."))

    assert result.strip() != ""
    assert result.startswith("ERROR:"), result


def test_repair_retry_can_still_produce_a_real_result(monkeypatch):
    _install_workspace(monkeypatch)
    _patch_openai(
        monkeypatch,
        [MALFORMED_TOOL_CALL, "Traffic fell 22% week over week across all channels."],
    )

    result = asyncio.run(manager._route_to_agent_safe("ws_test", "analyzing", "Analyse traffic."))

    assert result == "Traffic fell 22% week over week across all channels."


def test_healthy_answer_is_returned_unchanged(monkeypatch):
    _install_workspace(monkeypatch)
    calls = _patch_openai(monkeypatch, ["OK"])

    result = asyncio.run(manager._route_to_agent_safe("ws_test", "analyzing", "Health probe."))

    assert result == "OK"
    assert len(calls) == 1, "a healthy answer must not trigger a retry"


def test_call_with_retry_rejects_an_agent_that_returns_markup():
    class BrokenAgent:
        async def chat(self, message):
            return MALFORMED_TOOL_CALL, []

    try:
        asyncio.run(manager._call_with_retry(BrokenAgent(), "analyse", max_retries=0))
    except manager.AgentOutputError as exc:
        assert "BrokenAgent" in str(exc)
    else:
        raise AssertionError("an agent returning markup was accepted as a result")


def test_call_with_retry_rejects_an_empty_agent_result():
    class EmptyAgent:
        async def chat(self, message):
            return "", []

    try:
        asyncio.run(manager._call_with_retry(EmptyAgent(), "analyse", max_retries=0))
    except manager.AgentOutputError as exc:
        assert "EmptyAgent" in str(exc)
    else:
        raise AssertionError("an empty agent result was accepted")