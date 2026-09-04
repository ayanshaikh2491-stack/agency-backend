"""Social Agent — Organic Social Media Strategist + Executor with 21 tools.

Organic-only (no paid ads). Creates posts, schedules, and publishes via SocialClaw.

21 tools:
1. content_calendar — Generate content calendar
2. hashtag_research — Find relevant hashtags
3. posting_schedule — Best times to post
4. competitor_analysis — Analyze competitor social presence
5. trend_research — Find trending topics
6. engagement_strategy — Plan community management
7. platform_strategy — Platform-specific strategy
8. content_gap_analysis — What competitors post that you don't
9. audience_analysis — Target audience insights
10. growth_tactics — Follower acquisition plan
11. generate_caption — Generate post captions
12. repurpose_content — Adapt content for multiple platforms
13. dm_outreach — DM templates + outreach strategy
14. influencer_research — Find organic influencers
15. analytics_report — Organic performance report
16. create_post — Create complete post (caption + hashtags + media plan)
17. schedule_post — Schedule post via SocialClaw
18. post_now — Publish immediately via SocialClaw
19. social_accounts — Manage connected accounts
20. content_queue — View scheduled posts
21. post_analytics — Track post performance

Interview Q1-Q3:
- STRATEGIST only, not executor (creates strategy, not posts)
- Platforms: Instagram + LinkedIn + X + TikTok + Facebook
- Organic-first, no paid ads
- Briefs Content Agent for visual content

LangGraph: call_llm -> route -> (run_tools | finalize) -> END
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
from admin.agency.social_skills import detect_skills, build_skill_context
from admin.config import settings
from admin.tools.social_tools import SOCIAL_TOOLS, execute_social_tool
from admin.workspace.agent_bus import send_message

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
MAX_LLM_RETRIES = 2
LLM_TIMEOUT_SECONDS = 120


# ── System Prompt ────────────────────────────────────────────────────────────

SOCIAL_SYSTEM_PROMPT = """You are the Social Media Agent for workspace '{workspace_name}' (client: {client_name}).

You are an ORGANIC Social Media STRATEGIST + EXECUTOR. You create strategy, write captions, schedule posts, and publish via SocialClaw. You do NOT use paid ads — everything is organic growth.

## Your Role
- STRATEGIST — you plan content calendars, engagement plans, growth tactics
- TEXT CREATOR — you write captions, hashtags, CTAs, engagement copy
- PUBLISHER — you schedule and publish posts via SocialClaw
- BRIEF GIVER — you send detailed visual briefs to Content Agent
- You do NOT create visual content — Content Agent does that

## Your Platforms
Instagram | LinkedIn | X/Twitter | TikTok | Facebook
(You decide which platforms per client based on industry/goals)

## Your Skills (Use These Knowledge Bases!)

### Social Content Skill
- Hook formulas: Curiosity, Story, Value, Contrarian
- Content pillars: 3-5 per brand (Industry insights 30%, Behind-the-scenes 25%, Educational 25%, Personal 15%, Promotional 5%)
- Platform frequency: LinkedIn 3-5x/week, Twitter 3-10x/day, Instagram 1-2 posts + Stories daily, TikTok 1-4x/day
- Repurposing: Blog -> LinkedIn post + Twitter thread + Instagram carousel

### Content Strategy Skill
- Searchable vs Shareable: Every piece must be one or both
- Content pillars: Product-led, Audience-led, Search-led, Competitor-led
- Keyword research by buyer stage: Awareness -> Consideration -> Decision

### Content Calendar Skill
- Balanced calendar: No pillar >40%, no platform >3 days without post
- Batching: Weekly planning (30min) + Platform batch (90min) + Review (30min)
- 20-30% flexible slots for reactive content

### Content Repurposer Skill
- Extract 3-7 standalone insights from any content
- Platform-native writing: Twitter (punchy, <280), LinkedIn (conversational, 3-5 paragraphs), Threads (casual)
- Content atoms: Quotable moments, story arcs, tactical tips, controversial takes

### Social Publisher Skill
- Best posting times: TikTok (7am, 12pm, 7pm), Instagram (11am-1pm, 7-9pm), LinkedIn (8-10am Tue-Thu)
- Caption adaptation: Platform-specific tone and length

## Your Tools (21 TOOLS — USE THEM!)\n\n### Strategy Tools (1-10)\n1. **content_calendar(platform, duration, niche, brand_tone)** — Generate content calendar\n2. **posting_schedule(platform, timezone_offset, audience)** — Best times to post\n3. **platform_strategy(industry, goals, budget)** — Which platforms to prioritize\n\n### Research Tools\n4. **hashtag_research(niche, platform, count)** — Find relevant hashtags by tier\n5. **trend_research(niche, platform)** — Trending topics and viral formats\n6. **competitor_analysis(competitors, platform, niche)** — Analyze competitor presence\n7. **content_gap_analysis(your_content, competitor_content, niche)** — Find gaps\n\n### Growth Tools\n8. **engagement_strategy(platform, goals, audience_size)** — Community management plan\n9. **audience_analysis(industry, platform, location)** — Target audience insights\n10. **growth_tactics(current_followers, platform, niche, budget)** — Follower acquisition (organic only)\n\n### Content Tools\n11. **generate_caption(topic, platform, tone, audience, include_cta)** — Generate post captions\n12. **repurpose_content(original_content, source_platform, target_platforms, topic)** — Adapt for multiple platforms\n\n### Outreach Tools\n13. **dm_outreach(purpose, platform, target_audience, tone)** — DM templates + strategy\n14. **influencer_research(niche, platform, budget, count)** — Find organic influencers\n\n### Analytics\n15. **analytics_report(platform, metrics, period)** — Organic performance report\n\n### Execution Tools (ACTUALLY POST!)\n16. **create_post(platform, topic, content_type, tone, caption, hashtags, media_url, cta)** — Create complete post\n17. **schedule_post(platform, caption, scheduled_at, media_url, hashtags, account_id)** — Schedule post via SocialClaw\n18. **post_now(platform, caption, media_url, hashtags, account_id)** — Publish immediately via SocialClaw\n19. **social_accounts(action, provider)** — List or connect social accounts\n20. **content_queue(platform, status)** — View scheduled posts queue\n21. **post_analytics(platform, post_id, period)** — Track post performance\n\n## Caption Writing Rules (USE HOOKS!)\n\n### Hook Formulas (First line determines if anyone reads)\n- Curiosity: \"I was wrong about [common belief].\"\n- Story: \"Last week, [unexpected thing] happened.\"\n- Value: \"How to [desirable outcome] (without [common pain]):\"\n- Contrarian: \"Unpopular opinion: [bold statement]\"\n\n### Caption Structure\n1. HOOK (first line — must stop the scroll)\n2. VALUE (2-5 paragraphs — deliver on the hook promise)\n3. CTA (what should they do? comment, share, save, visit link)\n4. HASHTAGS (mix of high/medium/low volume)\n\n### Platform-Specific Caption Rules\n- Instagram: 2200 chars max, 30 hashtags max, first 125 chars visible\n- LinkedIn: 3000 chars max, no links in body (first comment), 3-5 paragraphs\n- Twitter: 280 chars per tweet, threads for longer content\n- TikTok: Short, punchy, emoji-friendly, 100-150 chars\n\n## Briefing Content Agent\nWhen you need visual content, provide a DETAILED brief:\n- Post type (carousel, reel, story, single image)\n- Topic and description\n- Mood and style (bold, minimal, professional, fun)\n- Target audience\n- Platform and dimensions\n- Text overlay or CTA\n- Brand colors to use\n- Why this content works\n\n## Your Rules\n1. ORGANIC ONLY — no paid ads, no sponsored content\n2. Always use HOOKS in captions — first line must stop the scroll\n3. Use tools before giving advice — research first, then recommend\n4. Content pillars: 3-5 per brand, balanced distribution\n5. Brief Content Agent with DETAIL — vague briefs = bad visuals\n6. Platform-native content — don't copy-paste across platforms\n7. 20-30% flexible slots in calendar for reactive content\n8. CEO can override your strategy anytime\n\n## Behavioural rules\n- Be strategic and data-driven. Give specific numbers, schedules, tactics.\n- Think about ROI — every strategy should have measurable goals.\n- Consider the client's industry, audience, and resources.\n- Start with what's achievable, then scale.\n- Never refuse a task — if you can't do something, explain why and suggest alternatives.\n- ALWAYS mention this is organic strategy — no paid ads.\n\n## AI Visibility — AEO + GEO (organic + AI search)\n{aeo_geo_context}\nBeyond organic growth, make the brand AI-answer-ready:\n- Write FAQ-style + question-format captions (so AI can quote the brand's voice)\n- Keep business name + city + USP consistent across every caption/hashtag (entity clarity)\n- Produce original, trustworthy posts AI search engines can cite (GEO)\n- Align with SEO Agent's angle for this business (same keywords, same entity)\n"""


# ── State ─────────────────────────────────────────────────────────────────────

class SocialAgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], "Conversation"]
    workspace_name: str
    client_name: str
    tool_round: int
    final_output: str
    error: str | None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_llm_client() -> openai.OpenAI:
    """OpenAI-compatible client with CEO-key fallback (gold standard).

    Uses per-workspace key/base first, then agency CEO key/base. Never
    hard-codes a hy3 model. CPU-friendly: no local inference.
    """
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    return openai.OpenAI(api_key=api_key, base_url=base_url) if base_url else openai.OpenAI(api_key=api_key)


def _strip_think_blocks(content: str | None) -> str:
    """Remove model "thinking" tokens so only the final answer reaches the client.

    Tolerates every known model-output quirk (mirrors sba.py gold standard):
      1. ```think ... ``` fenced blocks
      2. <think> ... </think> tags (case-insensitive, including stray leading `<`)
      3. Plain "think" prefix (some small models prepend it)
      4. A trailing ``` with no opening fence (truncate it)
    Returns the cleaned text, or "" if nothing usable remains.
    """
    if not content:
        return ""
    cleaned = re.sub(r"```think.*?```", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    if cleaned.strip().lower().startswith("think"):
        body = cleaned.strip()[5:].strip()
        lines = body.split("\n")
        response_lines: list[str] = []
        in_thinking = False
        for line in lines:
            if re.match(r"^\s*\d+\.\s+\w", line):
                in_thinking = True
                continue
            if in_thinking and re.match(r"^\s*$", line):
                in_thinking = False
                continue
            if not in_thinking:
                response_lines.append(line)
        cleaned = "\n".join(response_lines).strip() or body
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"```", "", cleaned).strip()
    return cleaned


# ── Graph Nodes ──────────────────────────────────────────────────────────────

async def social_call_llm(state: SocialAgentState) -> dict[str, Any]:
    """Call the LLM with tools."""
    system = SOCIAL_SYSTEM_PROMPT.format(
        workspace_name=state.get("workspace_name", "Default"),
        client_name=state.get("client_name", "Client"),
        aeo_geo_context=agent_aeo_geo.build_aeo_geo_section(state.get("workspace_name", "Default")),
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(state.get("messages", []))

    # Superpower skill context injection
    if messages and messages[-1].get("role") == "user":
        skills = detect_skills(messages[-1]["content"])
        skill_ctx = build_skill_context(skills)
        if skill_ctx:
            messages[-1]["content"] += (
                "\n\n## Relevant Skills (use these frameworks)\n" + skill_ctx
            )
        # Light AEO/GEO nudge: keep brand entity consistent so AI recognizes it
        aeo_geo = agent_aeo_geo.build_aeo_geo_section(
            state.get("workspace_name", "Default")
        )
        messages[-1]["content"] += (
            "\n\n## AI Visibility (light)\n" + aeo_geo +
            "\nApply: keep business name + city + USP consistent in captions/hashtags "
            "so AI search engines recognize the brand entity (supports GEO)."
        )

    client = _get_llm_client()
    model = settings.WORKSPACE_AGENT_MODEL or "llama-3.3-70b-versatile"

    last_error: str | None = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            client = _get_llm_client()
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=SOCIAL_TOOLS,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=4096,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            break
        except Exception as e:  # noqa: BLE001 — network/API errors are broad
            last_error = str(e)
            logger.warning("Social Agent LLM call failed (attempt %d/%d): %s", attempt, MAX_LLM_RETRIES, e)
    else:
        logger.exception("Social Agent LLM call failed after %d attempts", MAX_LLM_RETRIES)
        return {"error": f"LLM call failed: {(last_error or '')[:200]}"}

    choice = response.choices[0]
    content = choice.message.content or ""
    tool_calls = choice.message.tool_calls or []

    # Tool calls: emit the assistant message with tool_calls ONLY.
    # Execution is delegated to social_run_tools -> the graph re-enters call_llm
    # after tool results, so we must NOT execute tools here (avoids double execution).
    if tool_calls:
        return {
            "messages": [{
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            }],
            "error": None,
        }


def social_route(state: SocialAgentState) -> str:
    """Route: tool_calls -> run_tools, tool results -> call_llm, else finalize."""
    if state.get("error"):
        return "finalize"
    if state.get("tool_round", 0) >= MAX_TOOL_ROUNDS:
        return "finalize"

    msgs = state.get("messages", [])
    if not msgs:
        return "finalize"

    last = msgs[-1]
    if isinstance(last, dict) and last.get("tool_calls"):
        return "run_tools"

    # Tool results -> re-enter LLM with fresh tool decisions
    if isinstance(last, dict) and last.get("role") == "tool":
        return "call_llm"

    return "finalize"


async def social_run_tools(state: SocialAgentState) -> dict[str, Any]:
    """Execute tools from last message (each guarded so one bad tool can't crash the run)."""
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

        logger.info("Social tool: %s(%s)", name, args)
        try:
            result = execute_social_tool(name, args)
            result_str = json.dumps(result, default=str)[:8000]
        except Exception as tool_exc:  # noqa: BLE001
            logger.exception("Social tool %s failed", name)
            result_str = json.dumps({"error": f"{name} failed: {str(tool_exc)[:200]}"}, default=str)

        results.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result_str})

    return {"messages": results, "tool_round": state.get("tool_round", 0) + 1}


def social_finalize(state: SocialAgentState) -> dict[str, Any]:
    """Extract final output (think-block stripped)."""
    output = state.get("final_output", "")
    if output:
        return {"final_output": _strip_think_blocks(output)}

    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            return {"final_output": _strip_think_blocks(msg["content"])}

    if state.get("error"):
        return {"final_output": f"Social Agent error: {state['error'][:200]}"}

    return {"final_output": "Social Agent analysis complete."}


# ── Graph ────────────────────────────────────────────────────────────────────

def build_social_graph(checkpointer=None) -> StateGraph:
    graph = StateGraph(SocialAgentState)
    graph.add_node("call_llm", social_call_llm)
    graph.add_node("run_tools", social_run_tools)
    graph.add_node("finalize", social_finalize)
    graph.set_entry_point("call_llm")
    graph.add_conditional_edges("call_llm", social_route, {
        "run_tools": "run_tools",
        "call_llm": "call_llm",
        "finalize": "finalize",
    })
    graph.add_edge("run_tools", "call_llm")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


_graph = None

def get_social_graph() -> StateGraph:
    global _graph
    if _graph is None:
        _graph = build_social_graph()
    return _graph


# ── SocialAgent Class ──────────────────────────────────────────────────────

class SocialAgent:
    """Social Media Strategist with real tools. Scoped to ONE workspace + client."""

    def __init__(self, workspace_name: str = "Default", client_name: str = "Client",
                 workspace_id: str | None = None):
        self.workspace_name = workspace_name
        self.client_name = client_name
        self.workspace_id = workspace_id or workspace_name
        self._thread_id = f"social_{self.workspace_id}"
        self._graph = build_social_graph(get_checkpointer(self.workspace_id, "social"))

    async def chat(self, message: str) -> tuple[str, str]:
        """Process a social media request."""
        initial_state: dict[str, Any] = {
            "messages": [{"role": "user", "content": message}],
            "workspace_name": self.workspace_name,
            "client_name": self.client_name,
            "tool_round": 0,
            "final_output": "",
            "error": None,
        }

        try:
            result = await self._graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": self._thread_id}},
            )
            return result.get("final_output", "Social Agent analysis complete."), self._thread_id
        except Exception:
            logger.exception("Social Agent execution failed")
            return "Social Agent temporarily unavailable.", self._thread_id

    def request_content(
        self,
        content_type: str,
        topic: str,
        platform: str = "instagram",
        description: str = "",
        style: str = "bold",
        priority: str = "normal",
        quantity: int = 1,
        objective: str = "engagement",
        target_audience: dict[str, Any] | None = None,
        emotional_hook: str = "curiosity",
        cta: str = "learn_more",
        key_message: str = "",
        competitor_context: str = "",
        constraints: str = "",
        copy_text: str = "",
    ) -> dict[str, Any]:
        """Request social media visuals from Content Agent with DEEP brief.

        Social Agent samajhta hai ki social content ENGAGEMENT ka tool hai —
        scroll stop karna hai, like karna hai, share karna hai.
        Isliye brief mein sab context deta hai.
        """
        from admin.workspace.agents.brief_builder import build_domain_brief, brief_to_text

        brief = build_domain_brief(
            domain="social",
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
                from_agent="social",
                to_agent="content",
                workspace_id=self.workspace_id,
                subject=f"Social needs {content_type}: {topic[:50]}",
                content=brief_content,
                message_type="brief",
                metadata=brief,
            )
            logger.info(
                "Social Agent requested content: %s (%s) x%d | objective=%s, hook=%s",
                content_type, topic[:50], quantity, objective, emotional_hook,
            )
            return {"status": "brief_sent", "brief": brief}
        except Exception as e:
            return {"status": "error", "error": str(e)}
