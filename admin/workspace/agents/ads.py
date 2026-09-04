"""Ads Agent — Meta (Facebook + Instagram) + Google Ads specialist.

Domain (from interview Q1-Q5):
- Full ownership: strategy → campaign → optimization → reporting
- Meta (Facebook + Instagram) primary, Google Ads secondary
- Auto-optimization loop (budget shift, creative rotation, bid adjustment)
- Multi-layer error recovery
- Prospecting + retargeting hybrid
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
from admin.agency import agent_aeo_geo
from admin.config import settings
from admin.tools.ads_tools import ADS_TOOLS, execute_ads_tool
from admin.workspace.agent_bus import send_message

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MAX_LLM_RETRIES = 2
LLM_TIMEOUT_SECONDS = 120


def _strip_think_blocks(content: str) -> str:
    """Remove model ```think / <think> reasoning blocks from client-facing text.

    Mirrors the gold-standard _strip_think_blocks in sba.py / website.py so
    thinking traces never leak into ads copy or strategy output.
    """
    cleaned = re.sub(r"```think.*?```", "", content or "", flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _get_llm_client() -> "openai.AsyncOpenAI":
    """OpenAI-compatible client with CEO-key fallback (gold standard).

    Uses the per-workspace key/base first, then the agency CEO key/base, and
    never hard-codes a hy3 model. CPU-friendly: no local inference.
    """
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    return (
        openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        if base_url
        else openai.AsyncOpenAI(api_key=api_key)
    )


ADS_SYSTEM_PROMPT = """You are the Ads Agent for workspace '{workspace_name}' (client: {client_name}).

You are a performance marketing specialist focused on paid advertising.

## Your Expertise
- Meta Ads (Facebook + Instagram): campaign strategy, audience targeting, creative direction, budget optimization
- Google Ads: search, display, shopping, performance max campaigns
- ROAS/ROI driven optimization
- Prospecting + retargeting hybrid strategies
- Multi-platform budget allocation

## Your Rules (from interview)
1. You own EVERYTHING — ad copy, creative briefs, budget allocation, optimization
2. For visual creatives, brief Content Agent. You write the copy and strategy.
3. You auto-optimize based on metrics — don't wait for anyone
4. Error recovery: minor fixes first → aggressive optimization → pause → report CEO
5. You think about both prospecting AND retargeting simultaneously
6. You measure success by ROAS/ROI targets per client
7. CEO can override your strategy anytime

## AI Visibility — AEO + GEO (paid + AI search)
{aeo_geo_context}
When you write ad copy or brief creatives, also make the brand AI-answer-ready:
- Write ad copy + creative briefs that reinforce the SAME business name, city, and USP
  the SEO/Website agents use (entity consistency across paid + organic + AI answers)
- For Google Ads, consider how the brand appears in AI Overviews / Performance Max
- For Meta, keep the brand entity recognizable so AI search cites the right business
NOTE: keyword research stays with SEO Agent, but your copy must stay entity-consistent.

## Your 20 Tools
### Strategy
- campaign_strategy: Create full campaign strategy with 3 phases
- audience_research: Research target audiences by industry/product/platform
- budget_planner: Allocate budget across prospecting/retargeting/testing
- competitor_ads: Analyze competitor ad strategies
- platform_selection: Recommend platforms per client

### Content
- ad_copy_generator: Generate ad copy with hook formulas
- creative_brief: Create detailed creative brief for Content Agent
- ad_variations: Create A/B test variants
- landing_page_strategy: Plan landing page and tracking
- ad_hashtag_tags: Generate hashtags and UTM tags

### Targeting
- audience_builder: Build audiences with interests/behaviors
- lookalike_audience: Create LAL from converters
- retargeting_setup: Full funnel retargeting
- exclusion_list: Build exclusion audiences

### Optimization
- performance_analyzer: Analyze metrics and detect issues
- auto_optimize: Rule-based auto-optimization
- ab_test_setup: Configure A/B tests

### Reporting
- campaign_report: Generate comprehensive campaign report
- roas_calculator: Calculate ROAS with gap analysis
- creative_score: Score creative effectiveness (0-100)

## Your Jcode Skills (use when needed)
- ads: Full paid ads playbook (Meta Andromeda era, retargeting frameworks)
- copywriting: PAS/BAB frameworks, hook formulas for ad copy
- analytics: Conversion tracking, GA4, pixel setup
- landing-page-copywriter: Landing page copy that converts
- marketing-council: Multi-expert marketing consultation

## Key Playbooks You Know
- Meta Andromeda (2026+): Statics > video, broad targeting + specific creative, identity-trigger keywords
- 4-Component Retargeting: Objection-handling + proof carousel + other-offers + value-first audit
- Headline Mirror Trick: 20-40 headline variants → mirror winner on landing page → 15-20% lift
- Zombie Campaigns: Resurrect dead variants in separate ad sets
- Net Cash > ROAS: Scale until break-even ceiling

## Metrics You Track
- ROAS, ROI, CTR, CPC, CPA, CPM
- Impression Share, Frequency, Reach
- Conversion Rate, Cost per Conversion
- Campaign-level and ad-set-level performance

## What you know about this workspace

{workspace_context}

## Ad Copy Quality Bar (client-facing)
When you write ad copy or a brief, every deliverable must be:
- **Platform-native**: hook-first, scannable, native to Meta/Google style — no corporate fluff.
- **Framework-driven**: apply PAS (Problem-Agitate-Solution), BAB (Before-After-Bridge), or a proven hook formula (curiosity, objection, identity, social proof).
- **Specific & measurable**: name the audience, the offer, the CTA, and the metric target (CPA/ROAS).
- **Varied**: always ship 3+ variants with a clear A/B hypothesis per variant.
- **Compliant**: no banned claims, no missing disclaimers, platform policy-safe.

## Response Structure (when delivering strategy or copy)
1. **Situation** — what's true for this client right now (goal, budget, audience).
2. **Strategy** — the 1-2 punch that maximizes ROAS (prospecting + retargeting).
3. **Deliverables** — concrete copy/brief/plan, each with a hypothesis.
4. **Measurement** — the KPI, the target, and the kill/pivot threshold.

## Reasoning Discipline
- Do your internal reasoning inside a ```think block. NEVER let ```think / <think> content reach the client — all final copy must be clean, ready-to-ship text.
- Be direct and data-driven. Use numbers, scores, specific findings. Never give generic advice.

## Thinking Process
1. What's the advertising goal?
2. What budget/platforms are available?
3. What's the target audience?
4. What approach maximizes ROAS?
5. What's my specific recommendation (with variants + measurement)?
"""


class AdsAgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], lambda e, n: e + n]
    workspace_name: str
    client_name: str
    workspace_context: str
    tool_round: int
    final_output: str
    error: str | None


async def ads_call_llm(state: AdsAgentState) -> dict:
    system_prompt = ADS_SYSTEM_PROMPT.format(
        workspace_name=state.get("workspace_name", "Unknown"),
        client_name=state.get("client_name", "Unknown"),
        workspace_context=state.get("workspace_context", "No data yet."),
        aeo_geo_context=agent_aeo_geo.build_aeo_geo_section(state.get("workspace_name", "Default")),
    )
    messages = [{"role": "system", "content": system_prompt}]
    for msg in state.get("messages", []):
        if isinstance(msg, dict):
            messages.append(msg)
    if not any(m.get("role") == "user" for m in messages):
        messages.append({"role": "user", "content": "Hello"})

    model = settings.WORKSPACE_AGENT_MODEL or "llama-3.3-70b-versatile"

    last_error: str | None = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            client_api = _get_llm_client()
            response = await client_api.chat.completions.create(
                model=model,
                messages=messages,
                tools=ADS_TOOLS,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=4096,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            break
        except Exception as exc:  # noqa: BLE001 — network/API errors are broad
            last_error = str(exc)
            logger.warning("Ads Agent LLM call failed (attempt %d/%d): %s", attempt, MAX_LLM_RETRIES, exc)
    else:
        logger.exception("Ads Agent LLM call failed after %d attempts", MAX_LLM_RETRIES)
        return {"error": last_error, "messages": [], "tool_round": state.get("tool_round", 0)}

    msg = response.choices[0].message
    assistant_msg: dict[str, Any] = {"role": "assistant", "content": _strip_think_blocks(msg.content) or ""}
    if msg.tool_calls:
        assistant_msg["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return {"messages": [assistant_msg], "error": None}


def ads_route(state: AdsAgentState) -> str:
    if state.get("error"):
        return "finalize"
    last = state.get("messages", [{}])[-1]
    if isinstance(last, dict) and last.get("tool_calls"):
        return "run_tools" if state.get("tool_round", 0) < MAX_TOOL_ROUNDS else "finalize"
    return "finalize"


async def ads_run_tools(state: AdsAgentState) -> dict:
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

        # Use real tool executor from ads_tools.py
        tool_result = execute_ads_tool(name, args)
        result_text = json.dumps(tool_result, indent=2, default=str)

        results.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result_text})
    return {"messages": results, "tool_round": state.get("tool_round", 0) + 1}


async def ads_finalize(state: AdsAgentState) -> dict:
    for msg in reversed(state.get("messages", []) or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            return {"final_output": _strip_think_blocks(msg["content"])}
    if state.get("error"):
        return {"final_output": f"Ads Agent error: {state['error'][:200]}"}
    return {"final_output": "Ads Agent analysis complete."}


def build_ads_graph(checkpointer=None) -> StateGraph:
    workflow = StateGraph(AdsAgentState)
    workflow.add_node("call_llm", ads_call_llm)
    workflow.add_node("run_tools", ads_run_tools)
    workflow.add_node("finalize", ads_finalize)
    workflow.set_entry_point("call_llm")
    workflow.add_conditional_edges("call_llm", ads_route, {"run_tools": "run_tools", "finalize": "finalize"})
    workflow.add_edge("run_tools", "call_llm")
    workflow.add_edge("finalize", END)
    return workflow.compile(checkpointer=checkpointer or MemorySaver())


class AdsAgent:
    """Ads Agent scoped to ONE workspace + client (no shared global state).

    `workspace_id`/`client_id` are threaded everywhere so multiple workspaces
    and clients never cross-contaminate checkpointers, thread IDs, or bus messages.
    """

    def __init__(self, workspace_name: str = "Default", client_name: str = "Client",
                 workspace_id: str | None = None, client_id: str | None = None):
        self.workspace_name = workspace_name
        self.client_name = client_name
        self.workspace_id = workspace_id or workspace_name
        self.client_id = client_id or client_name
        self._thread_id = f"ads_{self.workspace_id}"
        self.graph = build_ads_graph(get_checkpointer(self.workspace_id, "ads"))

    async def chat(self, message: str, conversation_history: list[dict[str, str]] | None = None) -> tuple[str, str]:
        workspace_context = f"Workspace: {self.workspace_name} ({self.workspace_id}), Client: {self.client_name}"
        initial_messages = [{"role": "user", "content": message}]
        if conversation_history:
            for m in conversation_history:
                if isinstance(m, dict) and m.get("role") and m.get("content"):
                    initial_messages.insert(-1, {"role": m["role"], "content": m["content"]})
        initial_state = {
            "messages": initial_messages,
            "workspace_name": self.workspace_name,
            "client_name": self.client_name,
            "workspace_context": workspace_context,
            "tool_round": 0, "final_output": "", "error": None,
        }
        try:
            result = await self.graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": self._thread_id, "workspace_id": self.workspace_id}},
            )
        except Exception:
            logger.exception("Ads Agent execution failed")
            return "Ads Agent temporarily unavailable.", self._thread_id
        return result.get("final_output", "Ads Agent analysis complete."), self._thread_id

    def request_content(
        self,
        content_type: str,
        topic: str,
        platform: str = "facebook",
        description: str = "",
        style: str = "bold",
        priority: str = "normal",
        quantity: int = 1,
        objective: str = "lead_generation",
        target_audience: dict[str, Any] | None = None,
        emotional_hook: str = "fear",
        cta: str = "sign_up",
        key_message: str = "",
        competitor_context: str = "",
        constraints: str = "",
        copy_text: str = "",
    ) -> dict[str, Any]:
        """Request ad creatives from Content Agent with DEEP brief.

        Ads Agent samajhta hai ki ad creative sirf image nahi hai —
        yeh CONVERSION ka tool hai. Isliye brief mein sab kuch deta hai:
        - Kya banana hai (content_type)
        - Kahan dikhega (platform)
        - Kisko dikhana hai (target_audience)
        - Kya feel karna hai (emotional_hook)
        - Kya karna hai (cta)
        - Kyun behtar hai (competitor_context)
        """
        from admin.workspace.agents.brief_builder import build_domain_brief, brief_to_text

        brief = build_domain_brief(
            domain="ads",
            content_type=content_type,
            topic=topic,
            platform=platform,
            description=description,
            style=style,
            priority=priority,
            quantity=quantity,
            objective=objective,
            target_audience=target_audience,
            emotional_hook=emotional_hook,
            cta=cta,
            key_message=key_message,
            competitor_context=competitor_context,
            constraints=constraints,
            copy_text=copy_text,
        )

        brief_content = brief_to_text(brief)

        try:
            send_message(
                from_agent="ads",
                to_agent="content",
                workspace_id=self.workspace_id,
                subject=f"Ads needs {content_type}: {topic[:50]}",
                content=brief_content,
                message_type="brief",
                metadata={**brief, "workspace_id": self.workspace_id, "client_id": self.client_id},
            )
            logger.info(
                "Ads Agent requested content: %s (%s) x%d | objective=%s, hook=%s",
                content_type, topic[:50], quantity, objective, emotional_hook,
            )
            return {"status": "brief_sent", "brief": brief}
        except Exception as e:
            logger.warning("Failed to request content: %s", e)
            return {"status": "error", "error": str(e)}
