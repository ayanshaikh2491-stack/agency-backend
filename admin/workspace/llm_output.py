"""Shared guard for turning an LLM chat response into an agent's output.

Every agent under ``admin/workspace/agents/`` resolves its key/base/model the
same way and then hands the model's text straight back to the caller as "the
agent's output". That is fine right up until the model does not answer. The
production failure this module exists for looked like this::

    <function_call><list_directory></list_directory>

A tool-call envelope with no arguments and no payload. The router stored that
markup as the answer, the CEO autonomy task was marked ``done``, and the
workspace recorded an empty result. A failure wearing a success costume.

``require_agent_output`` is the single place that decides whether a response
carries a usable answer. It raises :class:`AgentOutputError` when the response
is blank, when the model asked for a tool this path cannot run, or when the
text is still tool-call markup. Callers log the error and surface a visible
failure. It never returns an empty string and it never returns markup.

Detection is intentionally conservative about ordinary markup: only tag names
whose segments are tool words (``tool``, ``function``, ``invoke``, ...) are
flagged, so real report output containing ``<table>`` or ``<div>`` passes.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

__all__ = [
    "AgentOutputError",
    "MAX_OUTPUT_REPAIRS",
    "TOOL_CALL_REPAIR_HINT",
    "find_tool_call_markup",
    "require_agent_output",
    "require_output_text",
    "unusable_output_reason",
]

# How many corrective retries a caller may make after an output rejection.
# One is enough to recover a model that emitted a tool call by mistake and
# cheap enough to fit inside the edge request budget. This mirrors the
# existing MAX_LLM_TOOL_RETRIES convention in agents/sba.py.
MAX_OUTPUT_REPAIRS = 1

# Appended to the conversation when a response had to be rejected, so the model
# has a chance to produce a real result instead of failing the task.
TOOL_CALL_REPAIR_HINT = (
    "[System correction] Aapke pichle response mein koi tool call ya uska "
    "markup aa gaya tha, isliye koi answer nahi mila. Ab sirf plain analysis "
    "likho, koi function call, XML tag, ya tool name mat likho. Task ka jawab "
    "seedha text mein do."
)

# How much of the offending response to keep in logs and error messages.
_SNIPPET_LIMIT = 240

# A tag name is split on these separators and each piece is compared against
# _TOOL_TAG_SEGMENTS, so <list_directory> and <table> are never flagged.
_TOOL_TAG_SEGMENTS = frozenset({
    "tool",
    "tools",
    "tooluse",
    "toolcall",
    "toolcalls",
    "toolresult",
    "function",
    "functions",
    "functioncall",
    "functioncalls",
    "functionuse",
    "invoke",
    "recipient",
})

_TAG_NAME_RE = re.compile(r"<\s*/?\s*([A-Za-z_][A-Za-z0-9_.:\-]*)")
_SPECIAL_TOKEN_RE = re.compile(r"<\|\s*/?\s*([A-Za-z_][A-Za-z0-9_.\-]*)\s*\|>")
_SEGMENT_SPLIT_RE = re.compile(r"[._:\-\s]+")


class AgentOutputError(RuntimeError):
    """An agent produced no usable output. Always log this, never swallow it."""


def _is_tool_tag(tag_name: str) -> bool:
    """True when a tag name reads as a tool/function-call envelope."""
    segments = {s for s in _SEGMENT_SPLIT_RE.split(tag_name.lower()) if s}
    return bool(segments & _TOOL_TAG_SEGMENTS)


def _snippet(text: str, start: int) -> str:
    """Flatten and trim the offending text for a log line or an error message."""
    chunk = text[start:start + _SNIPPET_LIMIT].strip()
    flat = " ".join(chunk.split())
    return flat[:_SNIPPET_LIMIT]


def find_tool_call_markup(text: str | None) -> str | None:
    """Return the offending snippet when ``text`` is a tool call, else None."""
    if not text:
        return None
    for match in _TAG_NAME_RE.finditer(text):
        if _is_tool_tag(match.group(1)):
            return _snippet(text, match.start())
    for match in _SPECIAL_TOKEN_RE.finditer(text):
        if _is_tool_tag(match.group(1)):
            return _snippet(text, match.start())
    return None


def _tool_names(message: object) -> list[str]:
    """Best-effort names of the tool calls a message asked for."""
    names: list[str] = []
    for call in getattr(message, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        name = getattr(fn, "name", None) or getattr(call, "name", None)
        if name:
            names.append(str(name))
    return names


def require_agent_output(response: object, *, context: str) -> str:
    """Return the model's answer for ``response``, or raise AgentOutputError.

    ``response`` is the object returned by ``chat.completions.create`` and
    ``context`` names the call site so the log line points at the right agent.
    """
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise AgentOutputError(f"{context}: LLM response carried no choices")

    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        raise AgentOutputError(f"{context}: LLM response carried no message")

    finish_reason = getattr(choice, "finish_reason", None)

    tool_names = _tool_names(message)
    if tool_names:
        # These paths are tool-free, so a requested tool is a tool that will
        # never run. Recording it as the answer records nothing at all.
        raise AgentOutputError(
            f"{context}: model requested tool call(s) {tool_names} that this "
            f"path cannot execute, so there is no output "
            f"(finish_reason={finish_reason!r})"
        )

    content = getattr(message, "content", None)
    text = content if isinstance(content, str) else ("" if content is None else str(content))

    if not text.strip():
        raise AgentOutputError(
            f"{context}: model returned empty content "
            f"(finish_reason={finish_reason!r})"
        )

    markup = find_tool_call_markup(text)
    if markup is not None:
        raise AgentOutputError(
            f"{context}: model answered with tool-call markup instead of text: "
            f"{markup!r}"
        )

    return text.strip()


def require_output_text(text: str | None, *, context: str) -> str:
    """Validate text an agent already produced. Raises instead of returning ''."""
    value = text if isinstance(text, str) else ("" if text is None else str(text))
    if not value.strip():
        raise AgentOutputError(f"{context}: agent produced empty output")
    markup = find_tool_call_markup(value)
    if markup is not None:
        raise AgentOutputError(
            f"{context}: agent returned tool-call markup instead of a result: "
            f"{markup!r}"
        )
    return value.strip()


def unusable_output_reason(text: str | None, *, context: str) -> str | None:
    """Return why ``text`` must not be recorded as a successful result.

    Returns None when the text is a usable result. Callers that persist agent
    output use this to choose between a ``done`` and an ``error`` status, so a
    failed run is never stored as a completed one. ``ERROR:`` is the marker the
    routers in this module already return for a failure.
    """
    value = text if isinstance(text, str) else ("" if text is None else str(text))
    if not value.strip():
        return f"{context} returned an empty result"
    if value.lstrip().startswith("ERROR:"):
        return f"{context} returned no usable output: {value[:200]!r}"
    markup = find_tool_call_markup(value)
    if markup is not None:
        return f"{context} returned tool-call markup instead of an output: {markup!r}"
    return None