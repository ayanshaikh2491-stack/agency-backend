"""Employee workers — real agents the CEO (Michael) delegates to.

Each worker is a company "employee": it receives a brief from the CEO, does REAL work via its
domain tool module, reports back, and on failure returns a boss-readable Hindi status so the
CEO can route the fix (never silent).

The CEO orchestrates through `admin/agency/ceo.py` (LangGraph + tools) and `ceo_controller`.
This module is the *bridge* between the CEO's delegation and the agents' real tool modules.

Every agent already has a detailed, real implementation:
  - sba      -> admin.agency.sba_autopilot.SBAAutopilot (lead -> email -> meeting)
  - seo      -> admin.tools.seo_tools.execute_seo_tool
  - website  -> admin.tools.website_tools.execute_website_tool
  - content  -> admin.tools.content_tools.execute_content_tool
  - ads      -> admin.tools.ads_tools.execute_ads_tool
  - social   -> admin.tools.social_tools.execute_social_tool
  - analytics-> admin.tools.analytics_tools.execute_analytics_tool

Sub-agent (colleague) delegation: `run_colleague_task` lets one employee brief another directly
and fast without bouncing through the CEO.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from admin.workspace.manager import append_agent_activity_log, update_agent_activity

logger = logging.getLogger(__name__)

WorkerFn = Callable[[str, dict], Awaitable[dict]]

WORKERS: dict[str, dict] = {}

# Map each employee to its real tool dispatcher (single consistent entry point).
_TOOL_MODULES: dict[str, str] = {
    "seo": "admin.tools.seo_tools",
    "website": "admin.tools.website_tools",
    "content": "admin.tools.content_tools",
    "ads": "admin.tools.ads_tools",
    "social": "admin.tools.social_tools",
    "analytics": "admin.tools.analytics_tools",
}

# Sensible default tool per agent when a brief has no specific hint.
_DEFAULT_TOOL: dict[str, tuple[str, dict[str, Any]]] = {
    "seo": ("site_audit", {"url": "https://example.com"}),
    "website": ("analyze_website", {"url": "https://example.com"}),
    "content": ("generate_content_brief", {"topic": "agency"}),
    "ads": ("campaign_strategy", {"product": "client"}),
    "social": ("content_calendar", {"weeks": 4}),
    "analytics": ("weekly_report", {}),
}

# ── Agent brains (Q: "har agent apne hisab se soche") ─────────────────────
# Each employee gets an LLM thinking layer: it UNDERSTANDS the CEO brief,
# picks the best tool+args itself (no keyword matching), and interprets the
# raw tool output in its expert voice with anomalies + a concrete next step.
# If the LLM is unreachable, everything falls back to the deterministic
# _parse_brief keyword path — the loop never breaks.

_AGENT_BRAINS: dict[str, dict[str, str]] = {
    "seo": {
        "role": "SEO strategist",
        "voice": (
            "You think in rankings, search intent, and local visibility. For local "
            "small businesses (dentists/salons/HVAC in Austin/Dallas TX), local "
            "search visibility IS the revenue lever."
        ),
    },
    "website": {
        "role": "Website engineer",
        "voice": (
            "You think in speed, structure, trust signals, and conversion. A local "
            "business site must load fast, look credible, and make calling dead simple."
        ),
    },
    "content": {
        "role": "Content strategist",
        "voice": (
            "You think in hooks, audience pain, and message-market fit. Content must "
            "sound like the client's best customer talking, not a brochure."
        ),
    },
    "ads": {
        "role": "Media buyer",
        "voice": (
            "You think in CAC, ROAS, targeting, and budget efficiency. Every dollar "
            "must trace to a lead; vague reach without tracking is a fail."
        ),
    },
    "social": {
        "role": "Social growth strategist",
        "voice": (
            "You think in algorithms, cadence, and engagement. Consistent, native, "
            "platform-appropriate beats polished-but-corporate."
        ),
    },
    "analytics": {
        "role": "Data analyst",
        "voice": (
            "You think in trends, anomalies, and attribution. Numbers tell the story; "
            "your job is to say WHAT changed, WHY it matters, and what to do next."
        ),
    },
}

# Tool catalog per agent: name -> one-line purpose (for the planning brain).
_AGENT_CATALOGS: dict[str, dict[str, str]] = {
    "seo": {
        "site_audit": "Full on-page/technical audit of a URL (meta, headings, images, links)",
        "keyword_research": "Keyword ideas + intent for a seed keyword",
        "onpage_check": "Single-page onpage SEO check for a URL",
        "parse_sitemap": "Parse a site's sitemap.xml",
        "parse_robots_txt": "Parse a site's robots.txt",
        "serp_check": "SERP look at who ranks for a keyword",
        "generate_meta_tags": "Generate meta tags for a URL",
        "generate_schema": "Generate JSON-LD schema for a URL",
        "fix_audit_issues": "Fix earlier audit issues",
        "generate_seo_report": "SEO report for a URL",
        "track_rankings": "Track a keyword's ranking vs a target URL",
    },
    "website": {
        "analyze_website": "Full site analysis (perf, seo, tech, a11y) for a URL",
        "build_site": "Build a site for a business",
        "design_planner": "Plan a site's design",
        "generate_code": "Generate site code",
        "publish_site": "Publish a built site",
        "deploy_vercel": "Deploy to Vercel",
        "connect_domain": "Connect a domain",
        "check_domain": "Domain info check",
        "domain_status": "Domain status check",
        "tech_stack_advisor": "Recommend a tech stack",
        "competitor_sites": "Analyze competitor sites",
        "screenshot_site": "Screenshot a URL",
        "check_performance": "Performance check for a URL",
        "check_accessibility": "Accessibility check for a URL",
        "check_links": "Broken-link check for a URL",
        "check_ssl": "SSL check for a domain",
        "check_uptime": "Uptime check for a URL",
        "responsive_check": "Responsive/mobile check for a URL",
        "security_check": "Security headers check for a URL",
        "update_store_site": "Update stored site",
    },
    "content": {
        "generate_content_brief": "Content brief for a topic",
        "generate_blog_post": "Full blog post for a topic",
        "generate_content_calendar": "Content calendar",
        "generate_ad_copy": "Ad copy variants",
        "rewrite_content": "Rewrite/improve given content",
        "analyze_content_gaps": "Content gap analysis",
        "analyze_readability": "Readability analysis of content",
        "optimize_meta_descriptions": "Meta description variants",
        "repurpose_for_social": "Repurpose content for social platforms",
        "get_social_image_specs": "Image specs per social platform",
        "search_images": "Search stock images for content",
    },
    "ads": {
        "campaign_strategy": "Campaign strategy for a product/business",
        "audience_research": "Audience research for a business",
        "budget_planner": "Budget planning across platforms",
        "competitor_ads": "Competitor ad analysis",
        "platform_selection": "Which platforms to run ads on",
        "ad_copy_generator": "Generate ad copy",
        "creative_brief": "Creative brief for ads",
        "ad_variations": "Ad variations for testing",
        "landing_page_strategy": "LP strategy for a campaign",
        "ad_hashtag_tags": "Hashtags/tags for ads",
        "audience_builder": "Build target audience",
        "lookalike_audience": "Lookalike audience setup",
        "retargeting_setup": "Retargeting setup",
        "exclusion_list": "Exclusion list",
        "performance_analyzer": "Analyze campaign performance",
        "auto_optimize": "Auto-optimize a campaign",
        "ab_test_setup": "A/B test setup",
        "campaign_report": "Campaign report",
        "roas_calculator": "ROAS calculation",
        "creative_score": "Score ad creative",
    },
    "social": {
        "content_calendar": "Social content calendar",
        "hashtag_research": "Hashtag research for a niche",
        "posting_schedule": "Best posting times",
        "competitor_analysis": "Competitor social analysis",
        "trend_research": "Trend research for a niche",
        "engagement_strategy": "Engagement strategy",
        "platform_strategy": "Platform strategy",
        "content_gap_analysis": "Social content gap analysis",
        "audience_analysis": "Audience analysis",
        "growth_tactics": "Growth tactics for a handle",
        "generate_caption": "Generate a caption",
        "repurpose_content": "Repurpose content cross-platform",
        "dm_outreach": "DM outreach plan",
        "influencer_research": "Influencer research",
        "analytics_report": "Social analytics report",
        "create_post": "Create a post",
        "schedule_post": "Schedule a post",
        "post_now": "Post immediately",
        "organic_post": "Organic post via connected channels",
        "organic_channels": "List organic channels",
        "social_accounts": "Connected social accounts",
        "content_queue": "Content queue status",
        "post_analytics": "Post-level analytics",
    },
    "analytics": {
        "weekly_report": "Weekly workspace report",
        "monthly_report": "Monthly workspace report",
        "campaign_report": "Campaign performance report",
        "custom_report": "Custom report",
        "track_traffic": "Traffic tracking",
        "track_rankings": "Ranking tracking",
        "track_conversions": "Conversion tracking",
        "track_revenue": "Revenue tracking",
        "cross_channel_analysis": "Cross-channel analysis",
        "roi_calculator": "ROI calculation",
        "funnel_analysis": "Funnel analysis",
        "competitor_benchmark": "Competitor benchmarking",
        "anomaly_detector": "Detect anomalies in metrics",
        "threshold_alert": "Threshold alert setup",
        "competitor_alert": "Competitor alert setup",
        "traffic_forecast": "Traffic forecast",
        "budget_forecast": "Budget forecast",
        "growth_projection": "Growth projection",
        "data_aggregator": "Aggregate data sources",
        "email_report": "Email a report",
    },
}


async def _llm_json(messages: list[dict], temperature: float = 0.3) -> str | None:
    """One LLM chat call via the workspace default model. Returns content or
    None on any failure (callers fall back deterministically)."""
    try:
        import openai

        client = openai.AsyncOpenAI(
            api_key=_resolve_api_key(""),
            base_url=_resolve_api_base(""),
        )
        resp = await client.chat.completions.create(
            model=_workspace_agent_model(),
            messages=messages,
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent-brain LLM call failed: %s", exc)
        return None


async def _think_plan(agent_type: str, brief: str) -> tuple[str, dict[str, Any]] | None:
    """Agent's own planning pass: understand the brief, pick tool+args.

    Returns (tool_name, args-without-workspace) or None to fall back to
    keyword parsing. The agent — not keywords — decides what to run.
    """
    brain = _AGENT_BRAINS.get(agent_type)
    catalog = _AGENT_CATALOGS.get(agent_type)
    if not brain or not catalog:
        return None
    tools_lines = "\n".join(f"- {name}: {desc}" for name, desc in catalog.items())
    system = (
        f"You are the {brain['role']} of TAGS Agency, an AI marketing agency serving "
        f"local small businesses (dentists, salons, HVAC etc. in Austin/Dallas TX). "
        f"{brain['voice']} You are an autonomous professional: understand what the "
        "CEO actually needs from the brief, then choose the single best tool.\n\n"
        f"Available tools:\n{tools_lines}\n\n"
        "Reply with ONLY a JSON object, no other text:\n"
        '{"tool": "<exact tool name from the list>", '
        '"args": {<only the args this tool needs, extracted from the brief>}, '
        '"why": "<one short line: what the CEO really needs>"}'
    )
    raw = await _llm_json(
        [{"role": "system", "content": system}, {"role": "user", "content": brief}],
        temperature=0.2,
    )
    if not raw:
        return None
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        obj = json.loads(m.group(0) if m else raw)
        tool = str(obj.get("tool") or "").strip()
        if tool not in catalog:
            return None
        args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
        return tool, args
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent-brain plan parse failed (%s): %s", agent_type, exc)
        return None


async def _interpret_result(
    agent_type: str, brief: str, tool: str, result: Any
) -> str | None:
    """Agent's own interpretation pass: read the raw tool output and speak as
    the domain expert — what the data says, one anomaly, one next step."""
    brain = _AGENT_BRAINS.get(agent_type)
    if not brain:
        return None
    try:
        raw_json = json.dumps(result, default=str)[:3000]
    except Exception:  # noqa: BLE001
        raw_json = str(result)[:3000]
    system = (
        f"You are the {brain['role']} of TAGS Agency. {brain['voice']} "
        "You just ran a tool and got raw output. Interpret it like the expert "
        "you are — the CEO reads your words, not the JSON.\n\n"
        "Rules:\n"
        "- Max 4 short lines. Direct, no fluff. Hinglish ok.\n"
        "- Say WHAT the data shows, ONE notable thing/anomaly, and ONE concrete "
        "next step you recommend.\n"
        "- ONLY what is in the data. No invented numbers, no fake confidence. "
        "TAGS is a new small agency — never fabricate clients or results.\n"
        "- Reply in plain text, no JSON."
    )
    user = (
        f"CEO brief: {brief[:400]}\n\nTool ran: {tool}\n\nRaw output:\n{raw_json}"
    )
    return await _llm_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.4,
    )


def _hindi_status(agent: str, exc: Exception) -> str:
    msg = str(exc).strip()
    if msg.lower().startswith("bhai"):
        return f"{msg} CEO ko bhej diya hai — wo fix route karega."
    return (
        f"Bhai, {agent.upper()} employee kaam fail ho gaya: {exc}. "
        "CEO ko bhej diya hai — wo fix route karega."
    )


def _extract_url(text: str) -> str | None:
    m = re.search(r"https?://[^\s)]+", text)
    return m.group(0).rstrip(".,)") if m else None


def _extract_phrase(text: str, key: str) -> str | None:
    m = re.search(rf"{key}\s+([^\n]+)", text, re.IGNORECASE)
    return m.group(1).strip().strip("'\"") if m else None


def _parse_brief(task: str, ctx: dict) -> tuple[str, dict[str, Any]]:
    """Turn a CEO brief into (tool_name, args) for the employee's tool dispatcher.

    Heuristic but deterministic: explicit `tool: <name> | json-args` first, then
    keyword hints, then a per-agent default so a bare brief still does real work.
    """
    text = (task or "").strip()
    scope = ctx.get("scope", {}) or {}
    ws = scope.get("workspace_id", "agency")
    args: dict[str, Any] = {"workspace_id": ws, "__brief": text}

    # Explicit "tool: <name> | json-args" form from the CEO.
    if text.lower().startswith("tool:"):
        body = text[5:].strip()
        name, _, rest = body.partition("|")
        name = name.strip()
        if rest.strip():
            try:
                args.update(json.loads(rest.strip()))
            except (ValueError, TypeError):
                pass
        return name, args

    low = text.lower()
    if "audit" in low:
        return "site_audit", {**args, "url": _extract_url(text) or "https://example.com"}
    if "keyword" in low:
        return "keyword_research", {**args, "seed_keyword": _extract_phrase(text, "for") or "marketing"}
    if "blog" in low or "article" in low or "post" in low:
        return "generate_blog_post", {**args, "topic": _extract_phrase(text, "about") or text[:80]}
    if "ad" in low or "campaign" in low:
        return "campaign_strategy", {**args, "product": _extract_phrase(text, "for") or text[:80]}
    if "report" in low or "analytics" in low:
        return "weekly_report", args
    return "run_default", args


async def _run_real_agent(task: str, ctx: dict, agent_type: str | None = None) -> dict:
    """Thinking worker: understand brief -> choose tool -> run -> interpret.

    `agent_type` is passed by the registered wrapper so this single function can
    serve every employee (WorkerFn signature is (task, ctx)).

    The agent's own LLM brain does the choosing now (Q: "har agent apne
    hisab se soche"). If the brain is unavailable, we fall back to the
    deterministic keyword parse — work never stops.
    """
    if not agent_type:
        raise ValueError("agent_type required for _run_real_agent")
    module_name = _TOOL_MODULES.get(agent_type)
    if not module_name:
        raise ValueError(f"no tool module for agent '{agent_type}'")

    scope = ctx.get("scope", {}) or {}
    ws = scope.get("workspace_id", "agency")

    # Pass 1 — the agent's planning brain picks the tool (or None -> fallback).
    planned = await _think_plan(agent_type, task)
    if planned:
        tool_name, plan_args = planned
        args: dict[str, Any] = {"workspace_id": ws, "__brief": task, **plan_args}
        chosen_by = "brain"
    else:
        tool_name, args = _parse_brief(task, ctx)
        if tool_name == "run_default":
            tool_name, extra = _DEFAULT_TOOL.get(agent_type, ("site_audit", {}))
            args.update(extra)
        chosen_by = "keyword-fallback"

    mod = importlib.import_module(module_name)
    dispatcher = getattr(mod, f"execute_{agent_type}_tool", None)
    if dispatcher is None:
        raise ValueError(f"{module_name} has no execute_{agent_type}_tool")

    # Some dispatchers are coroutines; most are sync. Run sync ones off the loop.
    if asyncio.iscoroutinefunction(dispatcher):
        result = await dispatcher(tool_name, args)
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: dispatcher(tool_name, args))

    # Pass 2 — the agent interprets its own raw output in its expert voice.
    interpretation = await _interpret_result(agent_type, task, tool_name, result)

    return {
        "agent": agent_type,
        "tool": tool_name,
        "chosen_by": chosen_by,
        "result": result,
        "interpretation": interpretation,
    }


# Natural-language cues (Hinglish/English) that mean "SBA, DO the real
# lead->email->meeting pipeline now" — not just a status heartbeat.
# The CEO often says "clients lao", "leads nikaal", "meeting book kar", so we
# match intent words, not only the literal "run pipeline".
_PIPELINE_TRIGGERS = (
    "pipeline", "run once", "run sba", "send", "leads", "lead nikal", "lead lao",
    "client", "clients", "lao", "nikaal", "nikalo", "meeting", "book", "prospect",
    "outreach", "reach out", "contact", "email kar", "email bhej", "follow up",
    "dhund", "dhoond", "find", "generate", "campaign",
)


# Lightweight SBA tool keywords — when the CEO asks for ONE specific SBA
# action (not the whole lead->email->meeting sweep), run just that tool via
# sba_tools.execute_sba_tool instead of the heavy run_once browser+SMTP pass.
# Keeps the SBA worker fast and targeted while still doing real work.
_LIGHT_TOOL_KEYWORDS = {
    "qualify": "qualify_lead",
    "detect lead": "detect_lead_sources",
    "lead source": "detect_lead_sources",
    "save lead": "save_lead_record",
    "list lead": "list_saved_leads",
    "find email": "find_lead_email",
    "update lead": "update_lead_info",
    "lead info": "update_lead_info",
}


async def _run_sba(task: str, ctx: dict) -> dict:
    """SBA employee worker.

    Three modes, chosen by what the CEO actually asked:

    1. LIGHTWEIGHT TOOL — CEO named a specific SBA tool ("qualify this lead",
       "detect lead sources", "find email"). We run JUST that tool via
       sba_tools.execute_sba_tool. Fast, targeted, real work, no browser/SMTP.

    2. HEARTBEAT — a pure status/health question ("what's your status",
       "kya haal"). Returns last-known stats, no work at all. Keeps the
       orchestrator snappy during broad fan-outs.

    3. REAL PIPELINE — CEO asked for outreach work in natural language
       ("clients lao", "leads nikaal", "run the SBA pipeline", "send emails",
       "tool: sba_run_once"). Runs the full lead->email->meeting pass.

    This split means the SBA agent does real work without always spinning up
    the heavy browser+SMTP sweep, and it only does the full pipeline when the
    boss actually wants leads/clients/emails.
    """
    from admin.agency.sba_autopilot import SBAAutopilot

    low = (task or "").lower().strip()
    explicit_tool = low.startswith("tool:")

    # Mode 1: a specific lightweight tool was named.
    light_tool = None
    for kw, tool in _LIGHT_TOOL_KEYWORDS.items():
        if kw in low:
            light_tool = tool
            break

    # Mode 2: pure status/heartbeat — no work.
    if any(s in low for s in ("status", "kya haal", "what's up", "how are", "report only")):
        ap = SBAAutopilot()
        status = ap.status()
        return {
            "agent": "sba",
            "tool": "sba_autopilot.status",
            "result": {
                "status": status,
                "note": "Lightweight heartbeat (no work). 'clients lao' / 'leads nikaal' bolo to SBA real pipeline chalaayega.",
            },
        }

    # Mode 1 wins when a specific tool is named (even if pipeline words appear).
    if light_tool:
        from admin.tools.sba_tools import execute_sba_tool

        args = dict(ctx.get("scope", {}) or {})
        args.update({"task": task})
        result = execute_sba_tool(light_tool, args)
        return {
            "agent": "sba",
            "tool": f"sba_tools.{light_tool}",
            "result": {"result": result},
        }

    # Mode 3: natural-language outreach intent → full real pipeline.
    wants_pipeline = explicit_tool or any(t in low for t in _PIPELINE_TRIGGERS)
    if wants_pipeline:
        ap = SBAAutopilot()
        stats = await ap.run_once()
        new_leads = stats.get("new_leads_found", 0) if isinstance(stats, dict) else 0
        sent = stats.get("emails_sent", 0) if isinstance(stats, dict) else 0
        meetings = stats.get("meetings_scheduled", 0) if isinstance(stats, dict) else 0
        return {
            "agent": "sba",
            "tool": "sba_autopilot.run_once",
            "result": {
                "stats": stats,
                "summary": (
                    f"SBA ne real pipeline chalaya: {new_leads} naye leads, "
                    f"{sent} emails bheje, {meetings} meetings book hue."
                ),
            },
        }

    # Fallback: nothing matched — give a heartbeat so the fan-out stays snappy.
    ap = SBAAutopilot()
    status = ap.status()
    return {
        "agent": "sba",
        "tool": "sba_autopilot.status",
        "result": {
            "status": status,
            "note": "Lightweight heartbeat (no sends). 'clients lao' / 'leads nikaal' bolo to SBA real pipeline chalaayega.",
        },
    }


async def _run_custom_agent(task: str, ctx: dict, agent_type: str | None = None) -> dict:
    """Generic runner for user-added (dynamic) agents from the registry.

    The agent thinks (reasoning) then answers using its own system_prompt +
    model + api-key ref. Falls back to the workspace default model if the
    agent has none configured. Never crashes the loop on a missing key — it
    reports a clear Hindi status instead.
    """
    if not agent_type:
        raise ValueError("agent_type required for _run_custom_agent")
    from admin.agency import agent_registry as reg

    agent = await reg.get_agent(agent_type)
    if agent is None:
        raise ValueError(f"custom agent '{agent_type}' not found in registry")

    prompt = agent.get("system_prompt") or f"You are {agent['name']}, a {agent['role']} agent."
    model = agent.get("model") or _workspace_agent_model()
    api_key = _resolve_api_key(agent.get("api_key_ref") or "")
    if not api_key:
        raise RuntimeError(
            f"Bhai, '{agent['name']}' agent ka API key set nahi hai. "
            "Key add karo ya free-demo model lagao — tab tak ye agent kaam nahi karega."
        )

    import openai

    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=_resolve_api_base(agent.get("api_key_ref") or ""),
    )

    # Step 1: internal, hidden reasoning pass. The model reasons about the
    # task using the agent's identity/role before we ask for the answer.
    reason_system = (
        f"You are {agent['name']}, a {agent.get('role', 'custom')} agent. "
        "Think carefully about the user task before acting. In your reply, "
        "output a concise `REASON:` block explaining what the task needs and "
        "why, followed by a `PLAN:` block listing the concrete steps you will "
        "take to produce the best answer. Do not give the final answer here."
    )
    try:
        reason_resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": reason_system},
                {"role": "user", "content": task},
            ],
            temperature=0.5,
        )
        reasoning = (reason_resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("custom agent %s reasoning call failed: %s", agent_type, exc)
        raise RuntimeError(
            f"Bhai, '{agent['name']}' agent ka LLM call fail ho gaya: {exc}. "
            "Model/api-key/base-url check karo."
        )

    # Step 2: final answer pass. Combines the agent system_prompt with the
    # model's own REASON/PLAN so its answer stays grounded in its reasoning.
    answer_system = (
        f"{prompt}\n\nUse the reasoning below (from your own prior thinking) as "
        "context and produce ONLY the final user-facing answer for the task. "
        "Do not repeat the reasoning.\n\n--- REASONING ---\n" + reasoning
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": answer_system},
                {"role": "user", "content": task},
            ],
            temperature=0.7,
        )
        answer = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("custom agent %s LLM call failed: %s", agent_type, exc)
        raise RuntimeError(
            f"Bhai, '{agent['name']}' agent ka LLM call fail ho gaya: {exc}. "
            "Model/api-key/base-url check karo."
        )
    return {
        "agent": agent_type,
        "tool": "custom_agent.chat",
        "result": {"answer": answer, "reasoning": reasoning},
    }


def _workspace_agent_model() -> str:
    try:
        from admin.config import settings

        return settings.WORKSPACE_AGENT_MODEL or "llama-3.3-70b-versatile"
    except Exception:  # noqa: BLE001
        return "llama-3.3-70b-versatile"


def _resolve_api_key(ref: str) -> str:
    """Resolve an api_key_ref name to its env value, or use the ref directly.

    If the ref is a known env var name it is read from the environment; an
    explicit key string is returned as-is. Empty -> '' (caller reports error).
    """
    if not ref:
        # Default workspace key (works with Groq/OpenAI/OpenRouter via .env).
        try:
            from admin.config import settings

            return settings.WORKSPACE_API_KEY or os.getenv("AGENCY_CEO_API_KEY", "")
        except Exception:  # noqa: BLE001
            return os.getenv("AGENCY_CEO_API_KEY", "")
    return os.getenv(ref, ref)


def _resolve_api_base(ref: str) -> str:
    if not ref:
        try:
            from admin.config import settings

            return settings.WORKSPACE_API_BASE or ""
        except Exception:  # noqa: BLE001
            return ""
    base = os.getenv(ref + "_BASE", "")
    return base


def register_worker(worker_type: str, label: str, kind: str, fn: WorkerFn) -> None:
    WORKERS[worker_type] = {
        "type": worker_type,
        "label": label,
        "kind": kind,
        "runnable": fn,
    }


async def run_worker(worker: str, task: str, ctx: dict, message_id: str | None = None) -> dict:
    """Run an employee worker. Wraps errors with Hindi status; records to agent_bus.

    `message_id` (when provided) links this run to the CEO's `bus.brief` so the
    employee can `respond()` with its report (audit trail, Part C).
    """
    meta = WORKERS.get(worker)
    if meta is None:
        return {"ok": False, "error": f"unknown worker: {worker}",
                "hindi_status": f"Bhai, '{worker}' naam ka employee nahi hai."}

    scope = ctx.get("scope", {"kind": "agency", "workspace_id": "agency"})
    ws = scope.get("workspace_id", "agency")

    # Part C — audit trail. If the CEO didn't pre-brief (no message_id), create a
    # self-brief so EVERY worker run is auditable on the bus (never silent).
    if message_id is None:
        try:
            from admin.agency.agent_bus import get_bus

            message_id = get_bus().brief(
                "ceo", worker, ws, task[:200],
                objective="direct CEO delegation",
                required_action="execute + respond",
                status="active",
            )
        except Exception:
            logger.warning("agent_bus brief failed for direct task %s", worker)
            message_id = None

    update_agent_activity(ws, worker, "working", task[:80])
    append_agent_activity_log(ws, worker, "task", f"CEO task: {task[:200]}")

    try:
        result: dict[str, Any] = await meta["runnable"](task, ctx)
        ok = True
        err = None
        hindi = None
    except Exception as exc:  # noqa: BLE001 - surface any worker failure
        update_agent_activity(ws, worker, "error")
        logger.exception("Employee %s failed on task: %s", worker, task[:120])
        result = {}
        ok = False
        err = str(exc)
        hindi = _hindi_status(worker, exc)
        append_agent_activity_log(ws, worker, "error", f"{worker} failed: {err[:200]}")

    update_agent_activity(ws, worker, "idle")
    append_agent_activity_log(ws, worker, "status", f"done: {task[:200]}")

    # Part C — audit trail. Respond to the CEO's brief if one exists.
    if message_id:
        try:
            from admin.agency.agent_bus import get_bus

            get_bus().respond(
                message_id,
                result=json.dumps(result, default=str)[:4000],
                status="done" if ok else "failed",
                errors=err or "",
            )
        except Exception:  # bus is additive; never block the worker on it
            logger.warning("agent_bus respond failed for %s", message_id)

    payload: dict[str, Any] = {"ok": ok, "agent": worker, "result": result}
    if not ok:
        payload["error"] = err
        payload["hindi_status"] = hindi
    return payload


async def run_colleague_task(
    sender: str, receiver: str, workspace: str, task: str, context: str = ""
) -> dict:
    """Sub-agent delegation (Part D): one employee briefs a colleague directly.

    Records a brief on the agent_bus, runs the colleague, and responds — all
    without bouncing through the CEO. Speeds up cross-agent work (e.g. SEO asks
    Content for a blog post).
    """
    mid: str | None = None
    try:
        from admin.agency.agent_bus import get_bus

        mid = get_bus().brief(
            sender, receiver, workspace, task,
            objective=f"colleague task from {sender}",
            context=context,
            required_action="execute + respond",
            status="active",
        )
    except Exception:
        logger.warning("agent_bus brief failed for colleague task %s->%s", sender, receiver)

    return await run_worker(
        receiver,
        task,
        {"scope": {"kind": "workspace", "workspace_id": workspace}},
        message_id=mid,
    )


def list_workers() -> list[dict]:
    return [{"type": m["type"], "label": m["label"], "kind": m["kind"]} for m in WORKERS.values()]


async def register_builtins() -> None:
    """Register every employee as a real, wired worker (Part A)."""

    def _make_real(agent_type: str):
        async def _fn(task: str, ctx: dict) -> dict:
            return await _run_real_agent(task, ctx, agent_type=agent_type)
        return _fn

    register_worker("sba", "SBA — Lead→Email→Meeting", "sales", _run_sba)
    register_worker("seo", "SEO — Audit, Keywords, On-page", "growth", _make_real("seo"))
    register_worker("website", "Website — Build, Publish, Deploy", "build", _make_real("website"))
    register_worker("content", "Content — Blog, Copy, Calendar", "creative", _make_real("content"))
    register_worker("ads", "Ads — Strategy, Copy, Optimization", "growth", _make_real("ads"))
    register_worker("social", "Social — Posts, Calendar, Listening", "creative", _make_real("social"))
    register_worker("analytics", "Analytics — Reports, ROI, Forecasts", "insight", _make_real("analytics"))

    # ── Dynamic / user-added agents (Munder-style) ───────────────────────────
    # Load every custom agent from the persistent registry and register it as a
    # worker too, so the CEO can delegate to user-created agents on the fly.
    try:
        from admin.agency import agent_registry as reg

        for ca in await reg.list_agents():
            ca_id = ca["id"]
            register_worker(
                ca_id,
                f"{ca['name']} — {ca['role']} (custom)",
                "custom",
                lambda task, ctx, _id=ca_id: _run_custom_agent(task, ctx, agent_type=_id),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("custom agent registration failed: %s", exc)
