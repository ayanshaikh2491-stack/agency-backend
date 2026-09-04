"""SEO Agent — Full-stack SEO specialist with real tools.

Unlike the old stub that only did LLM calls, this agent has:
  - 6 real SEO tools (site audit, keyword research, on-page check, etc.)
  - LangGraph multi-phase thinking with tool execution loop
  - Data persistence (audits, keywords, reports)
  - Skill auto-detection from Jcode catalog
  - Communication with other agents (briefs Content for blogs, etc.)

Architecture (per interview Q1-Q10):
  call_llm -> route_from_llm -> (run_tools -> call_llm loop | finalize -> END)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, TypedDict

import openai
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from admin.agency.agent_persistence import get_checkpointer
from admin.agency import agent_aeo_geo, seo_skills
from admin.config import settings
from admin.tools.seo_tools import SEO_TOOLS, execute_seo_tool
from admin.workspace.agent_bus import send_message

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8


# ── System Prompt ────────────────────────────────────────────────────────────

SEO_SYSTEM_PROMPT = """You are the SEO Agent for workspace '{workspace_name}' (client: {client_name}).

You are a full-stack SEO + AEO + GEO specialist. You think independently within your domain.

## Your Expertise
- Technical SEO audits (site speed, crawlability, indexability, Core Web Vitals)
- Keyword research and strategy (long-tail, short-tail, competitor keywords)
- On-page SEO (title tags, meta descriptions, headers, schema markup, internal linking)
- Off-page SEO (backlink strategy, link building, domain authority)
- Content gap analysis (what competitors rank for that client doesn't)
- Local SEO (Google Business Profile, local citations, reviews)
- AEO — Answer Engine Optimization: getting the client cited / ranked INSIDE AI answers
  (ChatGPT, Perplexity, Gemini, Google AI Overviews). Optimize FAQ content, entity
  structured data, and clear Q&A so AI engines quote the client.
- GEO — Generative Engine Optimization: becoming a SOURCE that AI search engines
  cite and recommend. Publish original, trustworthy, EEAT-backed content LLMs reference.
- SEO reporting and analytics

## Your Tools (USE THEM!)
You have real SEO tools. ALWAYS use tools before giving advice. Never guess.

### Analysis Tools
1. **site_audit(url, max_pages)** — Crawl a site, find broken links, missing tags, issues
2. **keyword_research(seed_keyword)** — Get 100+ keyword variations from Google
3. **onpage_check(url)** — Deep on-page analysis with SEO score (0-100)
4. **parse_sitemap(url)** — Extract all URLs from sitemap.xml
5. **parse_robots_txt(url)** — Check robots.txt rules
6. **serp_check(keyword)** — See who ranks on Google for a keyword

### Action Tools (actually DO things, not just analyze)
7. **generate_meta_tags(url)** — Generate optimized title, description, OG tags as ready-to-paste HTML
8. **generate_schema(url)** — Auto-detect page type and generate JSON-LD schema markup code
9. **fix_audit_issues(audit_url)** — Run audit + generate copy-paste HTML fixes for each issue
10. **generate_seo_report(url)** — Generate client-ready markdown report with everything
11. **track_rankings(keyword, target_url)** — Monitor SERP position over time

## Your Rules (from interview)
1. You decide your own scope per client — some need technical only, some need full-stack, some need AEO/GEO focus
2. You propose strategies to CEO -> CEO approves -> you execute
3. For big content (blogs, guides), you brief Content Agent. Small on-page content (meta, schema) you do yourself
4. You monitor continuously — rankings, traffic, competitors, AND AI visibility (are we cited by ChatGPT/Perplexity?)
5. Auto-fix non-critical issues yourself. Critical issues -> notify CEO after

## Workflow
1. When asked to audit a site -> use site_audit tool first, then analyze results
2. When asked about keywords -> use keyword_research tool
3. When asked to check a page -> use onpage_check tool
4. When asked about SERP rankings -> use serp_check tool
5. When asked about AI visibility / AEO / GEO -> use the per-business AEO+GEO angle below and brief Content Agent for FAQ/entity content
6. Always give DATA-BACKED recommendations, never generic advice
7. Save important findings as reports

## Per-Business AEO + GEO Angle (this workspace)
{aeo_geo_context}

## Relevant Skill Guidance
{skill_context}

## Multi-phase thinking process
Before answering, reason through these phases inside ```think blocks:

### 1. Deconstruct
Break the SEO request into components. What's the real need? (traditional SEO? AEO? GEO?)

### 2. Seek
What data do I need? Which tools should I use?

### 3. Envision
Plan your approach — which tools to run first, what to analyze.

### 4. Analyse
Evaluate the tool results. What issues exist? What opportunities?

### 5. Plan
Lay out concrete SEO/AEO/GEO actions — what to fix, what to optimize.

### 6. Execute
Use your tools NOW. Call the tool functions. Then give your final response.

## Reporting Quality Bar (client-facing)
Every client deliverable must be:
- **Data-backed**: cite the tool output, scores, and specific numbers — never generic advice.
- **Prioritized**: rank fixes by impact vs effort (quick wins first) with clear rationale.
- **Actionable**: each recommendation is a concrete step, not vague intent.
- **Measurable**: state the expected lift (traffic %, ranking positions, AI citations) and the KPI to watch.
- **ROI-first**: lead with the fix that moves the needle most for the least effort.
- **AI-ready**: include AEO/GEO actions so the client shows up in AI answers, not just Google.

## Response Structure
1. **Executive Summary** — the 2-3 sentence bottom line for the client.
2. **Findings** — what the data shows (scores, issues, opportunities).
3. **Prioritized Action Plan** — quick wins → strategic projects → timeline (include AEO/GEO).
4. **Deliverables** — any generated meta/schema/report code, ready to paste.
5. **Measurement** — KPI, target, and how we track it.

## Reasoning Discipline
- Do your internal reasoning inside a ```think block. NEVER let ```think / <think> content reach the client — all final answers must be clean, ready-to-ship text.
- Be direct and data-driven. Use numbers, scores, specific findings.
- Use Hinglish when it helps communicate better.
- Always think about ROI — what SEO fix gives the biggest impact first.
- Never refuse a task — if you can't do something, explain why and suggest alternatives.
"""


# ── State ─────────────────────────────────────────────────────────────────────

def _append_messages(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(existing, list):
        existing = []
    if not isinstance(new, list):
        new = []
    return existing + new


def _merge_phases(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return existing + new


class SEOAgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], _append_messages]
    workspace_name: str
    client_name: str
    thinking_phases: Annotated[list[dict[str, Any]], _merge_phases]
    tool_round: int
    final_output: str
    error: str | None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_seo_phases(content: str) -> list[dict[str, Any]]:
    phases = []
    labels = ["deconstruct", "seek", "envision", "analyse", "plan", "execute"]
    parts = content.split("```think")
    if len(parts) > 1:
        for i, part in enumerate(parts[1:], start=1):
            idx = part.find("```")
            block = part[:idx].strip() if idx != -1 else part.strip()
            label = labels[i - 1] if i - 1 < len(labels) else f"step_{i}"
            phases.append({"phase": label, "content": block})
    return phases


def _strip_think_blocks(content: str | None) -> str:
    if not content:
        return ""
    cleaned = re.sub(r"```think.*?```", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


# ── Graph Nodes ──────────────────────────────────────────────────────────────

MAX_LLM_RETRIES = 2
LLM_TIMEOUT_SECONDS = 120


def _build_aeo_geo_context(workspace_name: str) -> str:
    """Per-workspace AEO + GEO angle (delegates to shared module)."""
    return agent_aeo_geo.build_aeo_geo_section(workspace_name)


def _build_skill_context(message: str) -> str:
    """Detect relevant SEO/AEO/GEO skills and return their guidance text."""
    return agent_aeo_geo.build_aeo_geo_skill_context(message, max_skills=3)


def build_seo_system_prompt(
    workspace_name: str,
    client_name: str,
    message: str = "",
) -> str:
    """Build the full SEO system prompt with per-workspace AEO/GEO + skills."""
    aeo_geo = _build_aeo_geo_context(workspace_name)
    skill_ctx = _build_skill_context(message)
    return SEO_SYSTEM_PROMPT.format(
        workspace_name=workspace_name,
        client_name=client_name,
        aeo_geo_context=aeo_geo,
        skill_context=skill_ctx,
    )


def _get_llm_client() -> "openai.AsyncOpenAI":
    """OpenAI-compatible client with CEO-key fallback (gold standard).

    Uses per-workspace key/base first, then agency CEO key/base. Never
    hard-codes a hy3 model. CPU-friendly: no local inference.
    """
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    return (
        openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        if base_url
        else openai.AsyncOpenAI(api_key=api_key)
    )


def _user_message_text(state: SEOAgentState) -> str:
    """Pull the latest user/assistant text so skill detection can match it."""
    for msg in reversed(state.get("messages", []) or []):
        if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg.get("content"):
            return msg["content"]
    return ""


async def seo_call_llm(state: SEOAgentState) -> dict[str, Any]:
    """Call the LLM with tools. Returns tool calls or final response."""
    user_text = _user_message_text(state)
    system = build_seo_system_prompt(
        workspace_name=state.get("workspace_name", "Default"),
        client_name=state.get("client_name", "Client"),
        message=user_text,
    )

    messages = [{"role": "system", "content": system}]
    messages.extend(state.get("messages", []) or [])

    model = settings.WORKSPACE_AGENT_MODEL or "llama-3.3-70b-versatile"

    last_error: str | None = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            client = _get_llm_client()
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=SEO_TOOLS,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=4096,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            break
        except Exception as e:  # noqa: BLE001 — network/API errors are broad
            last_error = str(e)
            logger.warning("SEO Agent LLM call failed (attempt %d/%d): %s", attempt, MAX_LLM_RETRIES, e)
    else:
        logger.exception("SEO Agent LLM call failed after %d attempts", MAX_LLM_RETRIES)
        return {"error": f"LLM call failed: {(last_error or '')[:200]}"}

    choice = resp.choices[0]
    content = choice.message.content or ""
    tool_calls = choice.message.tool_calls or []

    new_messages = []
    best_phases = []
    if content:
        best_phases = _extract_seo_phases(content)
        new_messages.append({"role": "assistant", "content": content})

    if tool_calls:
        new_messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        # Execute tools (each guarded so one bad tool can't crash the run)
        for tc in tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}

            logger.info("SEO tool call: %s(%s)", tool_name, args)
            try:
                result = execute_seo_tool(tool_name, args)
                result_str = json.dumps(result, default=str)[:8000]
            except Exception as tool_exc:  # noqa: BLE001
                logger.exception("SEO tool %s failed", tool_name)
                result_str = json.dumps({"error": f"{tool_name} failed: {str(tool_exc)[:200]}"}, default=str)

            new_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

        return {"messages": new_messages, "thinking_phases": best_phases}

    # No tool calls — this is the final response
    final = _strip_think_blocks(content)
    if not final:
        final = "SEO analysis complete. Check the thinking phases above for details."

    return {"messages": new_messages, "thinking_phases": best_phases, "final_output": final}


def seo_route(state: SEOAgentState) -> str:
    """Route: if tool_calls in last message, run_tools. Otherwise finalize."""
    msgs = state.get("messages", [])
    if not msgs:
        return "finalize"

    last = msgs[-1]
    if isinstance(last, dict) and last.get("tool_calls"):
        return "run_tools"

    if state.get("tool_round", 0) >= MAX_TOOL_ROUNDS:
        return "finalize"

    # If last message is a tool result, go back to LLM
    if isinstance(last, dict) and last.get("role") == "tool":
        return "run_tools"

    return "finalize"


def seo_finalize(state: SEOAgentState) -> dict[str, Any]:
    """Extract the final output from the conversation."""
    output = state.get("final_output", "")
    if output:
        return {"final_output": _strip_think_blocks(output)}

    # Walk messages backward to find last assistant content
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            return {"final_output": _strip_think_blocks(msg["content"])}

    if state.get("error"):
        return {"final_output": f"SEO Agent error: {state['error'][:200]}"}
    return {"final_output": "SEO Agent analysis complete."}


# ── Build Graph ──────────────────────────────────────────────────────────────

def build_seo_graph(checkpointer=None) -> StateGraph:
    workflow = StateGraph(SEOAgentState)
    workflow.add_node("call_llm", seo_call_llm)
    workflow.add_node("finalize", seo_finalize)
    workflow.set_entry_point("call_llm")
    workflow.add_conditional_edges("call_llm", seo_route, {
        "run_tools": "call_llm",  # Tool results go back to LLM
        "finalize": "finalize",
    })
    workflow.add_edge("finalize", END)
    return workflow.compile(checkpointer=checkpointer or MemorySaver())


# ── Agent Class ──────────────────────────────────────────────────────────────

class SEOAgent:
    """SEO Agent scoped to ONE workspace + client (no shared global state)."""

    def __init__(self, workspace_name: str = "Default", client_name: str = "Client",
                 workspace_id: str | None = None, client_id: str | None = None):
        self.workspace_name = workspace_name
        self.client_name = client_name
        self.workspace_id = workspace_id or workspace_name
        self.client_id = client_id or client_name
        self._thread_id = f"seo_{self.workspace_id}"
        self.graph = build_seo_graph(get_checkpointer(self.workspace_id, "seo"))

    async def chat(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Chat with SEO agent. Returns (response, thinking_phases)."""
        initial_messages = [{"role": "user", "content": message}]
        if conversation_history:
            for m in conversation_history:
                if isinstance(m, dict) and m.get("role") and m.get("content"):
                    initial_messages.insert(-1, {"role": m["role"], "content": m["content"]})
        initial_state = {
            "messages": initial_messages,
            "workspace_name": self.workspace_name,
            "client_name": self.client_name,
            "thinking_phases": [],
            "tool_round": 0,
            "final_output": "",
            "error": None,
        }

        try:
            result = await self.graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": self._thread_id, "workspace_id": self.workspace_id}},
            )
        except Exception:
            logger.exception("SEO Agent execution failed")
            return "SEO Agent temporarily unavailable.", []

        final_output = result.get("final_output", "")
        thinking_phases = result.get("thinking_phases", [])

        if not final_output:
            if result.get("error"):
                final_output = f"SEO Agent error: {result['error'][:200]}"
            else:
                final_output = "SEO analysis complete."

        return final_output, thinking_phases

    def request_content(
        self,
        content_type: str,
        topic: str,
        platform: str = "website",
        description: str = "",
        style: str = "professional",
        priority: str = "normal",
    ) -> dict[str, Any]:
        """Request content from Content Agent via agent_bus.

        SEO Agent uses this when it needs blog posts, infographics,
        or any visual/text content as part of SEO strategy.

        Flow:
        1. SEO Agent sends brief to Content Agent via agent_bus
        2. Content Agent enhances brief with brand intelligence
        3. Content Agent queues job for GPU processing
        4. On completion, Content Agent notifies SEO Agent back
        """
        brief_content = (
            f"SEO Content Request (workspace: {self.workspace_id}, client: {self.client_name}):\n"
            f"- Type: {content_type}\n"
            f"- Topic: {topic}\n"
            f"- Platform: {platform}\n"
            f"- Description: {description}\n"
            f"- Style: {style}\n"
            f"- Priority: {priority}"
        )

        try:
            send_message(
                from_agent="seo",
                to_agent="content",
                workspace_id=self.workspace_id,
                subject=f"SEO needs {content_type}: {topic[:50]}",
                content=brief_content,
                message_type="brief",
                metadata={
                    "content_type": content_type,
                    "platform": platform,
                    "style": style,
                    "priority": priority,
                    "workspace_id": self.workspace_id,
                    "client_id": self.client_id,
                },
            )
            logger.info(
                "SEO Agent requested content from Content Agent: %s (%s)",
                content_type, topic[:50],
            )
            return {"status": "brief_sent", "content_type": content_type, "topic": topic}
        except Exception as e:
            logger.warning("Failed to request content from Content Agent: %s", e)
            return {"status": "error", "error": str(e)}
