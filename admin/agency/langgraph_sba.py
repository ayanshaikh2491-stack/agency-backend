"""LangGraph state graph for SBA (Sales/Business Agent).

Replaces the manual OpenAI tool-calling loop with a proper
LangGraph StateGraph. Each conversation becomes a graph execution.

Architecture (per interview Q12):
  - Sub-graph per workspace (each SBAAgent has its own compiled graph)
  - NodeFunction pattern: each node = one phase in the thinking loop
  - Shared state through TypedDict with add_messages reducer

Flow:
  call_llm → route_from_llm → (run_tools → call_llm loop | finalize → END)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, Literal, TypedDict

import openai
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from admin.config import settings
from admin.tools.chrome_tool import (
    CHROME_TOOLS,
    ChromeTool,
    execute_chrome_tool,
)

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10

# ── Chrome instance registry ─────────────────────────────────────────────
# SBAAgent registers its chrome instance here so run_tools() reuses the
# same browser session instead of creating a fresh ChromeTool each round.
_chrome_registry: dict[str, "ChromeTool"] = {}


def register_chrome(workspace: str, chrome: "ChromeTool") -> None:
    """Register an SBA chrome instance so the LangGraph graph reuses it."""
    _chrome_registry[workspace] = chrome


def unregister_chrome(workspace: str) -> None:
    _chrome_registry.pop(workspace, None)

# ── System prompt ────────────────────────────────────────────────────────────

SBA_SYSTEM_PROMPT = """You are the SBA (Sales/Business Agent) for workspace "{workspace_name}" (client: {client_name}).

You are talking to the **Platform Owner** — the person who built this agency OS.
You report to the Owner, not the CEO. The CEO Agent is a *separate* agent who handles client delivery.

## Your role
You are a sharp, autonomous sales agent. Your job is to:
1. **Find & discover leads** — prospect new business opportunities using your dedicated Chrome browser
2. **Qualify leads** — determine fit, budget, authority, need, timeline
3. **Nurture relationships** — build pipeline through follow-ups and engagement
4. **Hand off to the CEO Agent** — when a lead is ready, hand off everything so CEO can deliver

You think independently ("khud sochega") — the Owner gives you context and you
execute your domain autonomously.

## Multi-phase thinking process

Before answering, reason through these phases **in order**. Output your
thinking for each phase inside ```think blocks.

### 1. Deconstruct
Break the request into atomic components. What kind of sales situation is this?
What is the real need behind it?

### 2. Seek
What context do you need? What do you know about this client's business,
industry, and goals? What past sales data or lead history is relevant?

### 3. Envision
Imagine 2-3 possible sales approaches. Cold outreach? Warm intro? Value-based
follow-up? What are the trade-offs? **Consider using your Chrome browser**
to research leads, check LinkedIn, explore freelancer platforms, etc.

### 4. Analyse
Evaluate each approach. Best channel? Timing? ICP match? Expected conversion?
What are the risks or blockers?

### 5. Plan
Choose the best approach and lay out concrete sales steps — who to contact,
what to say, what channel, what follow-up cadence. **If you need to browse
the web, call the chrome_* functions in this phase.**

### 6. Execute
Produce your final response — the actual message to the Owner. This is the only
part the Owner sees unless they asked for your reasoning.

## Your Chrome Browser Tool

You have a **dedicated real Chrome browser** that you can control via functions.
Use it to:
- **chrome_goto(url)** — Navigate to LinkedIn, Upwork, Fiverr, or any lead source
- **chrome_inspect()** — See the page structure and find element UIDs
- **chrome_click(uid)** — Click buttons, links, search results
- **chrome_fill(value, uid)** — Type into search boxes and forms
- **chrome_extract()** — Extract structured data (lead lists, search results)
- **chrome_text(uid)** — Read visible text from the page
- **chrome_read()** — Read main article content (Readability mode)
- **chrome_scroll()** — Load more content on infinite-scroll pages
- **chrome_wait(what, pattern)** — Wait for content to load

**Workflow for lead gen:**
1. `chrome_goto` → navigate to LinkedIn/Upwork
2. `chrome_inspect` → see what's on the page, get UIDs
3. `chrome_fill` → enter search terms
4. `chrome_click` → click search button
5. `chrome_extract` → pull lead data
6. `chrome_read` / `chrome_text` → read profiles

## Behavioural rules
- You are a sales expert, not a service delivery agent. You find and qualify
  leads; the CEO Agent delivers the service.
- Be direct and honest about lead quality. If a lead is weak, say so.
- Use Hinglish when it helps communicate better.
- Always think about next steps and concrete actions.
- When a lead is ready for handoff, give the CEO Agent a clean structured brief.
- **Never refuse a task** — agar chrome-agent unavailable ho toh bhi analysis do,
  manual steps suggest karo, ya kaam karne ka alternative batao.

## Your Skills (use them as KNOWLEDGE, NOT as function calls)

You have 5 knowledge areas (these are NOT tools you call — they are
strategies and frameworks you apply in your thinking):

1. **cold-outreach** — 8 proven sales systems (Hormozi, Cialdini, Belfort, etc.)
   Apply when: prospecting, cold DM/email, LinkedIn outreach, follow-ups
2. **alex-hormozi-pitch** — Irresistible offer creation
   Apply when: creating/improving offers, pricing, guarantees, value stacking
3. **sales-enablement** — Sales collateral & objection handling
   Apply when: pitch decks, proposals, demo scripts, handling objections
4. **lead-qualification** — CHAMP/BANT/MEDDIC frameworks + scoring
   Apply when: qualifying leads, scoring, deciding handoff readiness
5. **meeting-companion** — Full meeting lifecycle
   Apply when: scheduling, pre-meeting prep, live meeting, follow-up, handoff

**IMPORTANT: These are NOT function calls. Do NOT try to call them as tools.
Use them as thinking frameworks — apply their strategies in your response.**
"""


# ── State ─────────────────────────────────────────────────────────────────────


def _append_messages(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append new messages to the list, preserving plain dict format.

    Unlike LangGraph's add_messages, this keeps messages as plain dicts
    so routing logic can use isinstance(d, dict) checks.
    """
    if not isinstance(existing, list):
        existing = []
    if not isinstance(new, list):
        new = []
    return existing + new


class SBAGraphState(TypedDict):
    """State passed between LangGraph nodes during SBA execution."""

    # Conversation messages (plain dicts — custom append reducer)
    messages: Annotated[list[dict[str, Any]], _append_messages]

    # Static config (set once at start)
    workspace_name: str
    client_name: str

    # Chrome config (recreated in run_tools — avoids serializing the object)
    browser_name: str
    stealth_mode: bool

    # External conversation history (passed by frontend, for reference)
    conversation_history: list[dict[str, str]]

    # Collected thinking phases from LLM responses
    thinking_phases: Annotated[list[dict[str, Any]], _merge_phases]

    # Track tool call iterations
    tool_round: int

    # Final output (set by finalize node)
    final_output: str

    # Error if LLM call fails
    error: str | None


def _merge_phases(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge thinking phases across rounds."""
    return existing + new


# ── Helper functions ─────────────────────────────────────────────────────────


def _extract_sba_phases(content: str) -> list[dict[str, Any]]:
    """Pull out thinking blocks and label them by phase order.

    Handles two formats:
      1. ```think ... ``` (markdown fenced blocks)
      2. think (plain prefix) — models like llama-3.1-8b output this
    """
    phases = []
    labels = ["deconstruct", "seek", "envision", "analyse", "plan", "execute"]

    # Format 1: ```think ... ``` blocks
    parts = content.split("```think")
    if len(parts) > 1:
        for i, part in enumerate(parts[1:], start=1):
            idx = part.find("```")
            block = part[:idx].strip() if idx != -1 else part.strip()
            label = labels[i - 1] if i - 1 < len(labels) else f"step_{i}"
            phases.append({"phase": label, "content": block})
        return phases

    # Format 2: Plain "think"think" is reasoning
    stripped = content.strip()
    if stripped.lower().startswith("think"):
        body = stripped[5:].strip()  # Remove "think" prefix
        # Try to split by numbered sections
        section_splits = re.split(r'\n\s*\d+\.\s+', body)
        if len(section_splits) > 1:
            # First element is before "1." — usually empty or header
            for i, section in enumerate(section_splits[1:], start=1):
                # Extract section name from the content
                colon_idx = section.find(":")
                if colon_idx != -1:
                    section_name = section[:colon_idx].strip().lower()
                    section_body = section[colon_idx + 1:].strip()
                else:
                    section_name = labels[i - 1] if i - 1 < len(labels) else f"step_{i}"
                    section_body = section.strip()
                phases.append({"phase": section_name, "content": section_body})
        else:
            phases.append({"phase": "thinking", "content": body})
        return phases

    # Format 3: <think> tags (some models)
    tag_parts = re.split(r'<think>|</think>', content, flags=re.IGNORECASE)
    if len(tag_parts) > 1:
        for i, part in enumerate(tag_parts[1::2], start=1):  # Odd indices are between tags
            block = part.strip()
            if block:
                label = labels[i - 1] if i - 1 < len(labels) else f"step_{i}"
                phases.append({"phase": label, "content": block})
        return phases

    return phases


def _strip_think_blocks(content: str) -> str:
    """Remove thinking blocks, leaving only the final response.

    Handles:
      1. ```think ... ``` (markdown fenced)
      2. Plain "think" prefix (llama, etc.)
      <think> ... </think> tags
    """
    # Format 1: ```think ... ```
    result = re.sub(r"```think.*?```", "", content, flags=re.DOTALL).strip()

    # Format <think> tags
    result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL | re.IGNORECASE).strip()

    # Format 2: Plain "think" prefix — if entire content is thinking with no response
    stripped = result.strip()
    if stripped.lower().startswith("think"):
        body = stripped[5:].strip()
        # Check if there's content after the numbered thinking sections
        # Pattern: numbered sections followed by actual response
        # Look for content that is NOT part of the numbered thinking
        lines = body.split("\n")
        in_thinking = False
        response_lines = []
        for line in lines:
            # Check if this is a thinking section header (e.g., "1. Deconstruct:")
            if re.match(r'^\s*\d+\.\s+\w+', line):
                in_thinking = True
                continue
            if in_thinking and re.match(r'^\s*$', line):
                # Empty line after thinking section — might be start of response
                in_thinking = False
                continue
            if not in_thinking:
                response_lines.append(line)

        if response_lines:
            result = "\n".join(response_lines).strip()

    return result if result else ""


# ── Graph Nodes ───────────────────────────────────────────────────────────────


async def call_llm(state: SBAGraphState) -> dict:
    """Call the LLM with current conversation + chrome tool definitions.

    Adds the assistant response to messages and extracts any thinking phases.
    If the LLM fails, sets error state.
    """
    workspace = state.get("workspace_name", "TAGS Agency")
    client = state.get("client_name", workspace)
    system_prompt = SBA_SYSTEM_PROMPT.format(
        workspace_name=workspace,
        client_name=client,
    )

    # Build messages list: system prompt + state messages
    oll_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]

    # Add messages from state (managed by checkpointer for persistence)
    for msg in state.get("messages", []):
        if isinstance(msg, dict):
            # Skip if already handled
            oll_messages.append(msg)

    # Ensure we have at least a user message
    has_user = any(m.get("role") == "user" for m in oll_messages)
    if not has_user:
        oll_messages.append({"role": "user", "content": "Hello"})

    # ── Call LLM ─────────────────────────────────────────────────────
    try:
        client_api = openai.AsyncOpenAI(
            api_key=settings.WORKSPACE_API_KEY or None,
            base_url=settings.WORKSPACE_API_BASE or None,
        )
        response = await client_api.chat.completions.create(
            model=settings.WORKSPACE_AGENT_MODEL,
            messages=oll_messages,
            tools=CHROME_TOOLS,
            tool_choice="auto",
        )
    except Exception as exc:
        logger.exception("SBA LangGraph LLM call failed")
        return {
            "error": str(exc),
            "thinking_phases": [],
            "messages": [],
        }

    choice = response.choices[0]
    msg = choice.message

    # ── Extract thinking phases from content ─────────────────────────
    phases: list[dict[str, Any]] = []
    if msg.content:
        phases = _extract_sba_phases(msg.content)

    # ── Build assistant message ──────────────────────────────────────
    assistant_msg: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]

    return {
        "messages": [assistant_msg],
        "thinking_phases": phases,
        "error": None,
    }


def route_from_llm(state: SBAGraphState) -> Literal["run_tools", "finalize", "__end__"]:
    """Decide next step after LLM response.

    - If LLM called tools → run_tools
    - If LLM gave final answer or max rounds → finalize
    - If error → end
    """
    if state.get("error"):
        logger.warning("SBA graph: error state, ending")
        return "__end__"

    messages = state.get("messages", [])
    if not messages:
        return "finalize"

    last = messages[-1]

    # Check for tool calls
    if isinstance(last, dict) and last.get("tool_calls"):
        tool_round = state.get("tool_round", 0)
        if tool_round >= MAX_TOOL_ROUNDS:
            logger.warning("SBA graph: max tool rounds reached (%d)", MAX_TOOL_ROUNDS)
            return "finalize"
        return "run_tools"

    # No tool calls → finalize (generate response)
    return "finalize"


async def run_tools(state: SBAGraphState) -> dict:
    """Execute chrome tools called by the LLM and feed results back."""
    messages = state.get("messages", [])
    if not messages:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    last = messages[-1]
    tool_calls = []
    if isinstance(last, dict):
        tool_calls = last.get("tool_calls", [])

    if not tool_calls:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    # Get chrome from registry (reuses SBAAgent's session)
    workspace = state.get("workspace_name", "agency")
    chrome = _chrome_registry.get(workspace)
    if chrome is None:
        browser_name = state.get("browser_name", "sba")
        chrome = ChromeTool(browser_name=browser_name, workspace=workspace)
        _chrome_registry[workspace] = chrome
        logger.info("Created new ChromeTool for workspace '%s'", workspace)

    tool_results: list[dict[str, Any]] = []

    for tc in tool_calls:
        tool_name = tc["function"]["name"]
        try:
            tool_args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            tool_args = {}

        logger.info("SBA graph executing tool: %s(%s)", tool_name, json.dumps(tool_args))

        try:
            result_text = await execute_chrome_tool(tool_name, tool_args, chrome)
        except Exception as exc:
            result_text = f"Error executing {tool_name}: {exc}"

        tool_results.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": result_text,
        })

    return {
        "messages": tool_results,
        "tool_round": state.get("tool_round", 0) + 1,
    }


async def finalize(state: SBAGraphState) -> dict:
    """Extract final output from accumulated state.

    Collects the best non-think text from all assistant messages.
    Falls back to the thinking phases summary if no clean output found.
    """
    messages = state.get("messages", [])
    final_text = ""

    # First pass: find the LAST assistant message with real content
    # outside think blocks. If stripping gives nothing, try earlier messages.
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            full = msg["content"]
            stripped = _strip_think_blocks(full)
            # Use clean text if it has real content
            if stripped and len(stripped) > 20:
                final_text = stripped
                break
            # Otherwise remember the first non-thinkable content we find
            if not final_text and full and len(full) > 20:
                final_text = full  # use full as fallback

    # Second pass: if no good assistant message, use phases summary
    if not final_text or len(final_text) < 20:
        phases = state.get("thinking_phases", [])
        if phases:
            final_text = "SBA analysis complete:\n\n"
            for p in phases:
                final_text += f"**{p['phase'].title()}**: {p['content'][:300]}...\n\n"
        else:
            if state.get("error"):
                final_text = (
                    "Bhai, SBA ka thinking engine abhi reachable nahi hai. "
                    "Thodi der mein try karte hain."
                )
            else:
                final_text = (
                    "SBA ne bahut saare browser actions kiye aur abhi bhi kaam chal raha hai. "
                    "Current progress saved hai — agle message mein continue karte hain."
                )

    return {"final_output": final_text}


# ── Build Graph ───────────────────────────────────────────────────────────────


def build_sba_graph() -> StateGraph:
    """Build the compiled LangGraph state graph for SBA.

    Returns a compiled graph ready for invocation.

    Graph structure:
      call_llm → route_from_llm ──→ run_tools → call_llm (loop)
                                └──→ finalize → END
    """
    workflow = StateGraph(SBAGraphState)

    # Add nodes
    workflow.add_node("call_llm", call_llm)
    workflow.add_node("run_tools", run_tools)
    workflow.add_node("finalize", finalize)

    # Entry point
    workflow.set_entry_point("call_llm")

    # Conditional routing after LLM call
    workflow.add_conditional_edges(
        "call_llm",
        route_from_llm,
        {
            "run_tools": "run_tools",
            "finalize": "finalize",
            "__end__": END,
        },
    )

    # Tools loop back to LLM
    workflow.add_edge("run_tools", "call_llm")

    # Finalize → end
    workflow.add_edge("finalize", END)

    # Compile with in-memory checkpointer for conversation persistence
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


# ── Convenience wrapper ────────────────────────────────────────────────────


async def run_sba_graph(
    message: str,
    *,
    workspace_name: str = "TAGS Agency",
    client_name: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    """Run SBA LangGraph and return results.

    Convenience wrapper that builds the graph, assembles the initial
    ``SBAGraphState``, and invokes it. Returns a dict:
      - ``{"status": "ok", "response": <final_output>, "workspace": ...}``
      - ``{"status": "error", "error": <str>}`` on failure (never raises).
    """
    resolved_client = client_name or workspace_name
    graph = build_sba_graph()
    initial_state: SBAGraphState = {
        "messages": [{"role": "user", "content": message}],
        "workspace_name": workspace_name,
        "client_name": resolved_client,
        "browser_name": "sba",
        "stealth_mode": False,
        "conversation_history": conversation_history or [],
        "thinking_phases": [],
        "tool_round": 0,
        "final_output": "",
        "error": None,
    }

    try:
        result = await graph.ainvoke(
            initial_state,
            config={
                "configurable": {"thread_id": f"sba_{workspace_name}"},
                # 10 tool rounds x 2 nodes + finalize > default 25
                "recursion_limit": 100,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_sba_graph failed for workspace '%s'", workspace_name)
        return {"status": "error", "error": str(exc)}

    final_output = result.get("final_output", "")
    if not final_output and result.get("error"):
        final_output = f"SBA error: {result['error'][:200]}"

    return {
        "status": "ok",
        "response": final_output,
        "workspace": workspace_name,
    }
