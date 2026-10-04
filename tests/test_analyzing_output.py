"""Regression tests for the analyzing agent reporting failures as successes.

The malformed tool call reached the agent as ``choice.message.content``, with
no structured ``tool_calls`` entry. The agent used to hand that markup to
``final_output``, and the finalize node used to invent "Analysis complete."
when nothing came back at all. Both turned a failure into a stored result.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.workspace.agents import analyzing  # noqa: E402

MALFORMED_TOOL_CALL = "<function_call><list_directory></list_directory>"


def _fake_client(content):
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=None),
            finish_reason="stop",
        )],
    )
    client = SimpleNamespace()
    client.chat = SimpleNamespace()
    client.chat.completions = SimpleNamespace(create=lambda **_: response)
    return client


def test_malformed_tool_call_is_an_error_not_an_output(monkeypatch):
    monkeypatch.setattr(analyzing, "_get_llm_client", lambda: _fake_client(MALFORMED_TOOL_CALL))

    result = asyncio.run(analyzing.analyzing_call_llm({
        "messages": [{"role": "user", "content": "Analyse last week's traffic."}],
        "workspace_name": "Acme",
        "client_name": "Acme Inc",
    }))

    assert result.get("final_output") == "", "markup leaked into the agent output"
    assert result.get("error"), "a malformed tool call must surface as an error"

    finalized = analyzing.analyzing_finalize({**result})
    output = finalized["final_output"]
    assert "Analysis complete." not in output
    assert "error" in output.lower()


def test_real_answer_still_becomes_the_output(monkeypatch):
    answer = "Traffic fell 22% week over week; cut the Instagram cadence first."
    monkeypatch.setattr(analyzing, "_get_llm_client", lambda: _fake_client(answer))

    result = asyncio.run(analyzing.analyzing_call_llm({
        "messages": [{"role": "user", "content": "Analyse last week's traffic."}],
        "workspace_name": "Acme",
        "client_name": "Acme Inc",
    }))

    assert result["final_output"] == answer
    assert result["error"] is None


def test_finalize_never_invents_a_success():
    output = analyzing.analyzing_finalize({
        "messages": [], "final_output": "", "error": None,
    })["final_output"]

    assert output != "Analysis complete."
    assert "no analysis" in output.lower()


def test_finalize_reports_a_real_error():
    output = analyzing.analyzing_finalize({
        "messages": [], "final_output": "",
        "error": "LLM call failed: connection reset",
    })["final_output"]

    assert "LLM call failed" in output