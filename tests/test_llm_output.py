"""Regression tests for the malformed-tool-call-as-empty-output bug.

Production symptom: the model answered a CEO autonomy dispatch with a tool
call envelope and no arguments, for example::

    <function_call><list_directory></list_directory>

The router stored that markup as the agent's output and the task was recorded
as done. These tests pin the new contract: a tool call, or nothing at all, is
a visible failure and never a stored output.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.workspace.llm_output import (  # noqa: E402
    AgentOutputError,
    find_tool_call_markup,
    require_agent_output,
    require_output_text,
    unusable_output_reason,
)

MALFORMED_TOOL_CALL = "<function_call><list_directory></list_directory>"


def _message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(content=None, tool_calls=None, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=_message(content, tool_calls),
                                 finish_reason=finish_reason)],
    )


# ── Detection ───────────────────────────────────────────────────────────────

def test_malformed_tool_call_markup_is_detected():
    assert find_tool_call_markup(MALFORMED_TOOL_CALL) is not None


def test_json_bodied_tool_call_markup_is_detected():
    raw = '<tool_call>{"name": "list_directory", "arguments": {}}</tool_call>'
    assert find_tool_call_markup(raw) is not None


def test_special_token_tool_call_is_detected():
    assert find_tool_call_markup('<|tool_call|>{"name": "list_directory"}') is not None


def test_plain_analysis_is_not_flagged():
    assert find_tool_call_markup("Revenue is up 22% week over week across all channels.") is None


def test_ordinary_markup_in_a_report_is_not_flagged():
    assert find_tool_call_markup("Evidence: <table><tr><td>22%</td></tr></table>") is None


# ── require_agent_output: response objects ──────────────────────────────────

def test_malformed_tool_call_response_raises():
    try:
        require_agent_output(_response(MALFORMED_TOOL_CALL), context="analyzing")
    except AgentOutputError as exc:
        assert "analyzing" in str(exc)
    else:
        raise AssertionError("malformed tool call was accepted as agent output")


def test_empty_content_response_raises():
    try:
        require_agent_output(_response(""), context="analyzing")
    except AgentOutputError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty content was accepted as agent output")


def test_none_content_response_raises():
    try:
        require_agent_output(_response(None), context="analyzing")
    except AgentOutputError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("None content was accepted as agent output")


def test_unexecuted_tool_call_response_raises():
    call = SimpleNamespace(
        id="call_1", type="function",
        function=SimpleNamespace(name="list_directory", arguments=""),
    )
    try:
        require_agent_output(_response(None, [call]), context="analyzing")
    except AgentOutputError as exc:
        assert "list_directory" in str(exc)
    else:
        raise AssertionError("a tool call with no output was accepted")


def test_no_choices_raises():
    try:
        require_agent_output(SimpleNamespace(choices=[]), context="analyzing")
    except AgentOutputError:
        pass
    else:
        raise AssertionError("empty choices were accepted")


def test_real_answer_is_returned_stripped():
    out = require_agent_output(_response("  Traffic fell 22% WoW.  "), context="analyzing")
    assert out == "Traffic fell 22% WoW."


# ── require_output_text: text an agent already produced ─────────────────────

def test_agent_returned_markup_raises():
    try:
        require_output_text(MALFORMED_TOOL_CALL, context="AnalyzingAgent")
    except AgentOutputError as exc:
        assert "AnalyzingAgent" in str(exc)
    else:
        raise AssertionError("agent markup was accepted as a result")


def test_agent_returned_blank_raises():
    for blank in ("", "   ", None):
        try:
            require_output_text(blank, context="AnalyzingAgent")
        except AgentOutputError:
            continue
        raise AssertionError(f"blank output {blank!r} was accepted")


def test_agent_error_text_passes_through():
    # Agents in this codebase report failures as text. Rejecting those would
    # turn a reported failure into a silent retry loop.
    text = "Analyzing Agent error: model returned a tool call instead of an analysis"
    assert require_output_text(text, context="AnalyzingAgent") == text


# ── unusable_output_reason: done vs error when persisting ───────────────────

def test_reason_flags_empty_result():
    assert "empty" in unusable_output_reason("", context="analyzing agent").lower()


def test_reason_flags_router_error_marker():
    reason = unusable_output_reason(
        "ERROR: ANALYZING safe analysis failed: bad model output",
        context="analyzing agent",
    )
    assert reason and "no usable output" in reason


def test_reason_flags_malformed_markup():
    reason = unusable_output_reason(MALFORMED_TOOL_CALL, context="analyzing agent")
    assert reason and "tool-call markup" in reason


def test_reason_is_none_for_a_real_result():
    assert unusable_output_reason(
        "Traffic fell 22% week over week.", context="analyzing agent") is None