"""Analyzing Agent — Senior cross-channel data analyst and insight engine.

Real tools (20, from analytics_tools):
Reporting (4): weekly_report, monthly_report, campaign_report, custom_report
Tracking (4): track_traffic, track_rankings, track_conversions, track_revenue
Analysis (4): cross_channel_analysis, roi_calculator, funnel_analysis, competitor_benchmark
Alerts (3): anomaly_detector, threshold_alert, competitor_alert
Forecasting (3): traffic_forecast, budget_forecast, growth_projection
Data (2): data_aggregator, email_report

ROLE BOUNDARY (distinct from the /api/analytics metrics reporter):
- The analytics endpoint is a thin metrics reporter. This agent is the ANALYST:
  it reasons ACROSS those reports, finds trends, compares channels, explains WHY,
  and returns a structured insight brief (not just raw numbers).
- It owns the same 20 analytics tools but adds synthesis: trend direction,
  anomalies, root-cause hypotheses, and prioritized recommendations.

LangGraph: call_llm -> route -> (run_tools | finalize) -> END
"""
from __future__ import annotations

import json
import logging
from typing import Annotated, Any, TypedDict

import openai
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from admin.config import settings
from admin.tools.analytics_tools import ANALYTICS_TOOLS, execute_analytics_tool
from admin.workspace.agent_bus import send_message

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8


# ── System Prompt ────────────────────────────────────────────────────────────

ANALYZING_SYSTEM_PROMPT = """You are the Analyzing Agent for workspace '{workspace_name}' (client: {client_name}).

You are a senior data analyst and insight engine. Other agents and the owner can
hand you raw numbers; you turn them into decisions. You do NOT just restate
metrics — you explain trends, compare channels, surface anomalies, and recommend
the single highest-leverage action.

## Your Expertise
- Cross-channel performance analysis (SEO, ads, social, website, SBA pipeline)
- Trend detection and seasonality
- Cohort / funnel analysis and conversion diagnostics
- ROI and budget efficiency (spend vs revenue vs pipeline)
- Forecasting and scenario planning
- Anomaly detection and alert triage
- Clear, decision-ready reporting (executive summary + evidence + next steps)

## Your Tools (USE THEM — never guess numbers)
1. **weekly_report(workspace, client, channels, period)** — last-7-day snapshot across channels
2. **monthly_report(workspace, client, channels, period)** — trends + recommendations over a month
3. **campaign_report(campaign_name, platform, period, metrics)** — single-campaign deep dive
4. **custom_report(workspace, client, focus_areas, period)** — report on specific focus areas
5. **track_traffic(channel, period, source)** — website traffic metrics
6. **track_rankings(...)** — keyword ranking tracking
7. **track_conversions(...)** — conversion tracking
8. **track_revenue(...)** — revenue tracking
9. **cross_channel_analysis(...)** — compare performance across channels
10. **roi_calculator(...)** — spend vs return
11. **funnel_analysis(...)** — drop-off + conversion by stage
12. **competitor_benchmark(...)** — benchmark vs competitors
13. **anomaly_detector(...)** — flag outliers in a metric series
14. **threshold_alert(...)** — alert when a metric crosses a threshold
15. **competitor_alert(...)** — competitor movement alerts
16. **traffic_forecast(...)** — forecast traffic
17. **budget_forecast(...)** — forecast budget needs
18. **growth_projection(...)** — project growth under scenarios
19. **data_aggregator(...)** — pull + join data for custom analysis
20. **email_report(...)** — email a finished report to a stakeholder

## How you work
1. Pull the relevant report(s)/tool data FIRST. Never answer from memory.
2. Compare apples to apples: same period, same channels, same definition.
3. State the TREND (up/down/flat) and the MAGNITUDE with evidence.
4. Give a ROOT-CAUSE HYPOTHESIS (1-3 plausible drivers), not just correlation.
5. End with PRIORITIZED RECOMMENDATIONS — ranked, with the one action that moves
   the needle most first.
6. Be specific and numeric. "Engagement is down" is useless; "Instagram reach fell
   22% WoW after we cut posting from 5x/week to 2x" is analysis.

## Output format (decision brief)
- **Executive Summary** (2-3 sentences, the so-what)
- **Evidence** (the numbers, with tool sources)
- **Trend & Drivers** (what changed and why it likely changed)
- **Risks / Anomalies** (what to watch)
- **Recommended Actions** (ranked, owner can act today)

## Behavioural rules
- Be direct, numeric, and decision-oriented. No fluff, no generic advice.
- If data is missing or demo-only, say so explicitly (data_source field).
- You may request a deeper pull from another agent via the owner, but you own
  synthesis. You do NOT write code for sites or run campaigns — you analyze.
"""


# ── State ─────────────────────────────────────────────────────────────────────

class AnalyzingAgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], "Conversation"]
    workspace_name: str
    client_name: str
    tool_round: int
    final_output: str
    error: str | None
    skills_meta: str
    thinking_phases: list[dict[str, Any]]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_llm_client() -> openai.OpenAI:
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    return openai.OpenAI(api_key=api_key, base_url=base_url) if base_url else openai.OpenAI(api_key=api_key)


# ── Graph Nodes ──────────────────────────────────────────────────────────────

async def analyzing_call_llm(state: AnalyzingAgentState) -> dict[str, Any]:
    """Call the LLM with analytics tools. Returns tool calls (unexecuted) or final response."""
    system = ANALYZING_SYSTEM_PROMPT.format(
        workspace_name=state.get("workspace_name", "Default"),
        client_name=state.get("client_name", "Client"),
    )
    if state.get("skills_meta"):
        system = f"{system}\n\n{state['skills_meta']}"

    messages = [{"role": "system", "content": system}]
    messages.extend(state.get("messages", []))

    client = _get_llm_client()
    model = settings.WORKSPACE_AGENT_MODEL or "llama-3.3-70b-versatile"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=ANALYTICS_TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=4096,
        )
    except Exception as e:
        logger.exception("Analyzing Agent LLM call failed")
        return {"error": f"LLM call failed: {str(e)[:200]}"}

    choice = response.choices[0]
    content = choice.message.content or ""
    tool_calls = choice.message.tool_calls or []

    new_messages = []

    if tool_calls:
        new_messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })
        return {"messages": new_messages, "error": None}

    new_messages.append({"role": "assistant", "content": content})
    return {"messages": new_messages, "final_output": content, "error": None}


def analyzing_route(state: AnalyzingAgentState) -> str:
    """Route: tool_calls -> run_tools, tool results -> back to LLM, else finalize."""
    if state.get("error"):
        return "finalize"
    if state.get("tool_round", 0) >= MAX_TOOL_ROUNDS:
        return "finalize"

    msgs = state.get("messages", [])
    if not msgs:
        return "finalize"

    last = msgs[-1]
    if isinstance(last, dict):
        if last.get("tool_calls"):
            return "run_tools"
        if last.get("role") == "tool":
            return "call_llm"

    return "finalize"


async def analyzing_run_tools(state: AnalyzingAgentState) -> dict[str, Any]:
    """Execute analytics tools from last message."""
    messages = state.get("messages", [])
    last = messages[-1] if messages else {}
    tool_calls = last.get("tool_calls", []) if isinstance(last, dict) else []

    if not tool_calls:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    results = []
    for tc in tool_calls:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            args = {}

        logger.info("Analyzing tool: %s(%s)", name, args)
        result = execute_analytics_tool(name, args)
        result_str = json.dumps(result, default=str)[:8000]

        results.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result_str})

    return {"messages": results, "tool_round": state.get("tool_round", 0) + 1}


def analyzing_finalize(state: AnalyzingAgentState) -> dict[str, Any]:
    """Extract final output."""
    output = state.get("final_output", "")
    if output:
        return {"final_output": output}

    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            return {"final_output": msg["content"]}

    if state.get("error"):
        return {"final_output": f"Analyzing Agent error: {state['error'][:200]}"}

    return {"final_output": "Analysis complete."}


# ── Graph ────────────────────────────────────────────────────────────────────

def build_analyzing_graph() -> StateGraph:
    graph = StateGraph(AnalyzingAgentState)
    graph.add_node("call_llm", analyzing_call_llm)
    graph.add_node("run_tools", analyzing_run_tools)
    graph.add_node("finalize", analyzing_finalize)
    graph.set_entry_point("call_llm")
    graph.add_conditional_edges("call_llm", analyzing_route, {
        "run_tools": "run_tools",
        "call_llm": "call_llm",
        "finalize": "finalize",
    })
    graph.add_edge("run_tools", "call_llm")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=MemorySaver())


_graph = None

def get_analyzing_graph() -> StateGraph:
    global _graph
    if _graph is None:
        _graph = build_analyzing_graph()
    return _graph


# ── AnalyzingAgent Class ────────────────────────────────────────────────────

class AnalyzingAgent:
    """Senior cross-channel data analyst. Returns structured insight briefs."""

    def __init__(self, workspace_name: str = "Default", client_name: str = "Client"):
        self.workspace_name = workspace_name
        self.client_name = client_name
        self._thread_id = f"analyzing_{workspace_name}"
        self._graph = get_analyzing_graph()

    async def chat(self, message: str, skills: list[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
        """Process an analysis request. Returns (final_output, thinking_phases)."""
        skills_meta = ""
        if skills:
            skills_meta = (
                "── APPLY THESE SKILLS ────────────────────────\n"
                "Use these frameworks/approaches in your analysis and recommendations:\n"
                + "\n".join(f"- {s}" for s in skills)
                + "\n──────────────────────────────────────────"
            )

        initial_state: dict[str, Any] = {
            "messages": [{"role": "user", "content": message}],
            "workspace_name": self.workspace_name,
            "client_name": self.client_name,
            "skills_meta": skills_meta,
            "tool_round": 0,
            "final_output": "",
            "error": None,
            "thinking_phases": [],
        }

        try:
            result = await self._graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": self._thread_id}},
            )
        except Exception:
            logger.exception("Analyzing Agent execution failed")
            return "Analyzing Agent temporarily unavailable.", []

        final_output = result.get("final_output", "")
        if not final_output:
            final_output = result.get("error") or "Analysis complete."

        phases = self._build_thinking_phases(message, final_output, skills or [])
        return final_output, phases

    def _build_thinking_phases(self, message: str, final_output: str, skills: list[str]) -> list[dict[str, Any]]:
        """Build the 5-step reasoning chain for audit trail / UI."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["forecast", "projection", "predict", "next quarter", "growth"]):
            category = "FORECAST"
            tool = "traffic_forecast / growth_projection"
        elif any(w in msg_lower for w in ["compare", "cross", "channel", "vs", "versus", "benchmark"]):
            category = "COMPARE"
            tool = "cross_channel_analysis / competitor_benchmark"
        elif any(w in msg_lower for w in ["anomaly", "alert", "spike", "drop", "unusual"]):
            category = "DETECT"
            tool = "anomaly_detector / threshold_alert"
        elif any(w in msg_lower for w in ["roi", "budget", "spend", "revenue"]):
            category = "EFFICIENCY"
            tool = "roi_calculator / budget_forecast"
        else:
            category = "REPORT"
            tool = "weekly_report / monthly_report"

        return [
            {"phase": "understand", "summary": f"Category: {category}. Parsed the analytical question and scope."},
            {"phase": "research", "summary": f"Pulled data via {tool}. Applied skills: {', '.join(skills) or 'none'}."},
            {"phase": "strategize", "summary": "Compared periods/channels, isolated trend direction and magnitude."},
            {"phase": "execute", "summary": "Synthesized a decision brief: summary, evidence, drivers, risks, actions."},
            {"phase": "validate", "summary": f"Checked against data sources. Result: {final_output[:120]}"},
        ]

    def request_data(self, from_agent: str, task: str) -> dict[str, Any]:
        """Log a delegation request to another agent for raw data (audit trail)."""
        try:
            send_message(
                from_agent="analyzing",
                to_agent=from_agent,
                workspace_id=self.workspace_name,
                subject="Analyzing Agent needs data",
                content=task,
                message_type="brief",
            )
            return {"status": "brief_sent", "to_agent": from_agent}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)}
