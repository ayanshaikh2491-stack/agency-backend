"""Memory Agent — Workspace-wide key/value memory keeper.

Domain (from interview):
- Remember client preferences, learned facts, and per-agent state across runs
- Recall anything the workspace has previously stored
- Surface memory held by other agents (sba, seo, content, ...) in the workspace

This agent is the conversational wrapper around the existing durable memory
infra in admin.agency.agent_persistence. It does NOT introduce a new storage
layer — it reuses the per-workspace, per-agent key/value store that the other
agents already write to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Annotated, Any, TypedDict

import openai
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from admin.agency.agent_persistence import get_checkpointer
from admin.config import settings
from admin.tools.memory_tools import MEMORY_TOOLS, execute_memory_tool
from admin.workspace.agent_bus import send_message

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
# hy3-free default model (OpenCode Zen). Falls back to configured WORKSPACE_AGENT_MODEL.
DEFAULT_MEMORY_MODEL = settings.WORKSPACE_AGENT_MODEL or "big-pickle"
LLM_TIMEOUT_SECONDS = 60.0
LLM_MAX_RETRIES = 2


def _get_llm_client() -> openai.AsyncOpenAI:
    """Build an OpenAI-compatible AsyncOpenAI client (hy3-free, no global state)."""
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    if base_url:
        return openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
    return openai.AsyncOpenAI(api_key=api_key)


def _strip_think_blocks(content: str) -> str:
    """Remove model ``<think>...</think>`` / ```think``` output reliably."""
    if not content:
        return ""
    cleaned = re.sub(r"```think.*?```", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


async def _call_llm_with_retry(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> Any:
    """Call the LLM with timeout + retry. Returns the raw response object."""
    model = DEFAULT_MEMORY_MODEL
    last_exc: Exception | None = None
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            client = _get_llm_client()
            return await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=4096,
                ),
                timeout=LLM_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "Memory LLM call attempt %d/%d failed: %s",
                attempt, LLM_MAX_RETRIES, exc,
            )
            await asyncio.sleep(min(1.5 * attempt, 5.0))
    if last_exc:
        raise last_exc
    raise RuntimeError("Memory LLM call failed for unknown reasons")


MEMORY_SYSTEM_PROMPT = """You are the Memory Agent for workspace '{workspace_name}' (client: {client_name}).

You are the workspace's long-term memory keeper. Other agents store their
learnings, state, and client preferences in per-agent memory; you help the
CEO and the team read, write, and forget that memory.

## Your Expertise
- Remembering explicit instructions ("remember that this client hates em-dashes")
- Recalling stored facts, preferences, and prior decisions
- Surfacing what other agents (sba, seo, content, website, ads, social, analytics)
  have learned in this workspace
- Keeping the workspace's institutional knowledge tidy and queryable

## Your Rules (from interview)
1. When the user asks you to "remember" something, SAVE it under a clear key.
2. When the user asks "what do we know about X", LIST or GET the relevant memory.
3. Prefer recall_others to pull context from sibling agents before answering
   knowledge questions about the client or past work.
4. Never invent memories — only report what is actually stored. If nothing is
   stored, say so plainly.
5. You are the source of truth for durable workspace facts — be concise and exact.

## Your 5 Tools
- save_memory: store a key/value memory for this workspace
- get_memory: retrieve one memory value by key
- list_memory: list all keys you hold for this workspace
- delete_memory: forget one memory (or all, if key omitted)
- recall_others: read memory held by another agent in this workspace

## What you know about this workspace

{workspace_context}

## Thinking Process
1. Does the user want to store, retrieve, or forget?
2. Which key/target agent is involved?
3. Call the matching tool and report the real result (no fabrication).
"""


class MemoryAgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], lambda e, n: e + n]
    workspace_name: str
    client_name: str
    workspace_context: str
    tool_round: int
    final_output: str
    error: str | None


async def memory_call_llm(state: MemoryAgentState) -> dict:
    """Call LLM with Memory agent system prompt (retry + timeout hardened)."""
    system_prompt = MEMORY_SYSTEM_PROMPT.format(
        workspace_name=state.get("workspace_name", "Unknown"),
        client_name=state.get("client_name", "Unknown"),
        workspace_context=state.get("workspace_context", "No data yet."),
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in state.get("messages", []):
        if isinstance(msg, dict):
            messages.append(msg)

    if not any(m.get("role") == "user" for m in messages):
        messages.append({"role": "user", "content": "Hello"})

    try:
        response = await _call_llm_with_retry(messages, MEMORY_TOOLS)
    except Exception as exc:
        logger.exception("Memory Agent LLM call failed")
        return {
            "error": f"LLM call failed: {str(exc)[:200]}",
            "messages": [],
            "tool_round": state.get("tool_round", 0),
        }

    msg = response.choices[0].message

    assistant_msg: dict[str, Any] = {
        "role": "assistant",
        "content": _strip_think_blocks(msg.content or ""),
    }
    if msg.tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]

    return {"messages": [assistant_msg], "error": None}


def memory_route(state: MemoryAgentState) -> str:
    if state.get("error"):
        return "finalize"
    messages = state.get("messages", [])
    if not messages:
        return "finalize"
    last = messages[-1]
    if isinstance(last, dict) and last.get("tool_calls"):
        if state.get("tool_round", 0) >= MAX_TOOL_ROUNDS:
            return "finalize"
        return "run_tools"
    return "finalize"


async def memory_run_tools(state: MemoryAgentState) -> dict:
    """Execute Memory tools using the real tool executor."""
    messages = state.get("messages", [])
    if not messages:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    last = messages[-1]
    tool_calls = last.get("tool_calls", []) if isinstance(last, dict) else []
    if not tool_calls:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    # Scope tool calls to the workspace so no client's memory leaks to another.
    ws = state.get("workspace_name", "Default")

    results = []
    for tc in tool_calls:
        name = tc.get("function", {}).get("name", "")
        try:
            args = json.loads(tc.get("function", {}).get("arguments", "{}"))
        except (json.JSONDecodeError, KeyError, TypeError):
            args = {}

        if not isinstance(args, dict):
            args = {}

        # Always bind the workspace so tool handlers know the scope.
        args.setdefault("workspace", ws)

        try:
            tool_result = execute_memory_tool(name, args)
        except Exception as exc:  # noqa: BLE001 — one bad tool must not kill the run
            logger.exception("Memory tool %s failed", name)
            tool_result = {"status": "error", "error": f"tool {name} failed: {str(exc)[:200]}"}

        result_text = json.dumps(tool_result, indent=2, default=str)
        results.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": result_text,
        })

    return {"messages": results, "tool_round": state.get("tool_round", 0) + 1}


async def memory_finalize(state: MemoryAgentState) -> dict:
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            final = _strip_think_blocks(msg["content"])
            if final:
                return {"final_output": final}

    if state.get("error"):
        return {"final_output": f"Memory Agent error: {state['error'][:200]}"}
    return {"final_output": "Memory Agent done."}


def build_memory_graph(checkpointer=None) -> StateGraph:
    workflow = StateGraph(MemoryAgentState)
    workflow.add_node("call_llm", memory_call_llm)
    workflow.add_node("run_tools", memory_run_tools)
    workflow.add_node("finalize", memory_finalize)
    workflow.set_entry_point("call_llm")
    workflow.add_conditional_edges("call_llm", memory_route, {
        "run_tools": "run_tools", "finalize": "finalize",
    })
    workflow.add_edge("run_tools", "call_llm")
    workflow.add_edge("finalize", END)
    return workflow.compile(checkpointer=checkpointer or MemorySaver())


class MemoryAgent:
    """Memory Agent for a specific workspace."""

    def __init__(self, workspace_name: str = "Default", client_name: str = "Client"):
        self.workspace_name = workspace_name
        self.client_name = client_name
        self.graph = build_memory_graph(get_checkpointer(self.workspace_name, "memory"))
        self._thread_id = f"memory_{workspace_name}"

    async def chat(self, message: str) -> tuple[str, str]:
        """Chat with Memory agent. Returns (response, thread_id)."""
        # Build a reliable workspace context by reading what is actually stored.
        workspace_context = f"Workspace: {self.workspace_name}, Client: {self.client_name}"
        try:
            from admin.tools.memory_tools import execute_memory_tool

            listing = execute_memory_tool("list_memory", {"workspace": self.workspace_name})
            items = listing.get("items", {}) if isinstance(listing, dict) else {}
            if isinstance(items, dict) and items:
                keys = ", ".join(sorted(items.keys()))
                workspace_context += f"\nStored memory keys: {keys}"
        except Exception as exc:  # noqa: BLE001 — context is best-effort only
            logger.warning("Memory context lookup failed: %s", exc)

        initial_state = {
            "messages": [{"role": "user", "content": message}],
            "workspace_name": self.workspace_name,
            "client_name": self.client_name,
            "workspace_context": workspace_context,
            "tool_round": 0,
            "final_output": "",
            "error": None,
        }

        try:
            result = await self.graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": self._thread_id}},
            )
        except Exception:
            logger.exception("Memory Agent execution failed")
            return "Memory Agent temporarily unavailable.", self._thread_id

        return result.get("final_output", "Memory Agent done."), self._thread_id

    def report_to_ceo(self, content: str = "") -> dict[str, Any]:
        """Send a memory summary/alert to CEO via agent_bus."""
        try:
            send_message(
                from_agent="memory",
                to_agent="ceo",
                workspace_id=self.workspace_name,
                subject=f"Memory update: {self.client_name}",
                content=content,
                message_type="report",
            )
            return {"status": "sent", "to": "ceo"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
