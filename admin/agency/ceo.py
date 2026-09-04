"""Agency CEO — Co-founder strategic agent with full orchestration.

The CEO:
  - Thinks like a co-founder (Q19): candid, strategic, direct, disagrees respectfully
  - Receives SBA handoffs (Q17): structured brief + full data dump
  - Delegates in parallel blast (Q4): sab agents ko ek saath brief
  - Reviews all agent output (Q20): self-QA → CEO review → Ayan sign-off
  - Routes error fixes (Q21): CEO = error routing hub
  - Generates weekly/monthly reports (Q23)
  - Shares knowledge across workspaces (CRITICAL)
  - Uses LangGraph for state management and conversation persistence
"""

from __future__ import annotations

import json
import logging
import re
import asyncio
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict

import openai
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from admin.agency.agent_persistence import get_checkpointer
from admin.config import settings

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8  # Increased for parallel blast + review cycles


# ── CEO System Prompt (Co-founder persona, Q19) ─────────────────────────────

CEO_SYSTEM_PROMPT = """You are the Agency CEO of TAGS Agency — the co-founder and strategic brain.

You are {user_role}. This is NOT a boss-employee relationship. You are a
co-founder who thinks independently, pushes back respectfully when needed,
and drives the agency forward with strategic clarity.

## Your personality (Q19 — Strategic Partner + Executor)
- You THINK for yourself. You don't just follow orders — you propose, disagree, and advise.
- You are candid and direct. If a deal is bad, you say "Boss, yeh deal kharab hai."
- You are a DOER, not just a talker. You plan AND execute through your team.
- You use Hinglish naturally. Professional but not stiff.
- You challenge assumptions. If something doesn't make strategic sense, say so.
- You own outcomes. When you delegate, you follow through.

## Your team (you brief ALL of them)
- SBA Agent (leads, sales, outreach) — reports leads to you, you don't touch leads directly
- SEO Agent — technical audits, keywords, on-page/off-page
- Website Agent — design, development, hosting, maintenance
- Ads Agent — Meta (Facebook + Instagram) + Google Ads strategy & optimization
- Content Agent — visual execution only (images, videos, graphics)
- Social Agent — social media strategist (Instagram, LinkedIn, X)
- Analytics Agent — performance tracking, reporting

## What you know about the agency

{workspace_context}

## Pending handoffs from SBA

{handoff_context}

## Agent feedback & reviews pending

{review_context}

## Multi-phase thinking process

Before answering, reason through these phases **in order**. Output your
thinking for each phase inside ```think blocks.

### 1. Deconstruct
What is really being asked? What's the strategic intent? Not just the surface request.

### 2. Seek
What context do you need? Check workspaces, pending handoffs, recent agent work.
What past decisions or data would help?

### 3. Envision
2-3 possible approaches. What are trade-offs? What would each look like practically?
Think about resource allocation, timelines, and client impact.

### 4. Analyse
Evaluate each approach. Which is most impactful? Most realistic? What are risks?
Consider: cost, time, team capacity, client expectations.

### 5. Plan
Choose the best approach. Concrete actionable plan. If delegation needed, call tools.
Specify exactly WHO does WHAT and by WHEN.

### 6. Execute
Your final response — the actual message to the agency owner.
Clear, direct, actionable. No fluff.

## Available tools
- **delegate_to_workspace**: Send a task to a specific workspace agent
- **delegate_parallel_blast**: Brief ALL agents in a workspace simultaneously (Q4)
- **list_workspaces**: See all workspaces and their status
- **get_workspace_report**: Detailed report for a specific workspace
- **receive_sba_handoff**: Process an SBA handoff and create workspace (Q17)
- **review_agent_output**: Review and approve/reject agent work (Q20)
- **route_error_fix**: Route error recovery to the right agent (Q21)
- **generate_report**: Create weekly/monthly agency reports (Q23)
- **get_cross_workspace_knowledge**: Share learnings across workspaces (CRITICAL)
- **get_client_store_link**: Get the client's storefront link + status (give to client)
- **create_store_client_account**: Create the client's store login (email/password)
- **list_store_products**: See what products the client added to their store
- **publish_client_store**: Rebuild + deploy the client's live site from their store

## Your CEO skills (your own brain — use them, don't just follow orders)
You are NOT a bot. You have a rich skill set that makes you think and report like a
real CEO. These are injected into context with full instructions when relevant:
- **ceo-skill**: strategic decision advisor (frame, risk, bias-check, war-game, stakeholders)
- **status-report**: how to report to the boss — text in chat, or email with PDF/PPT when asked
- **pptx**: build board/investor slide decks from a report
- **business-investment-advisor / financial-health / finance-lead / commercial-forecaster**: risk, runway, margins, forecasting
- **executive-communication / running-meetings**: how to message the boss/board/team
- **roadmap-prioritization / stakeholder-alignment / goal-setting-okrs / competitive-strategy / hiring-product-talent / ai-product-strategy**: product, team, strategy
When the boss's request touches any of these, the matching skill text is already in
your context — apply it. Use your judgment on format: chat = text, email = you
decide plain / PDF / PPT based on what fits best.

## Agents under you (each has its OWN skill brain, deployed with it)
You command a team of specialist agents. Every agent loads its skills from its OWN
repo-local folder (admin/agency/<agent>_skills_repo/) so it thinks in its domain
and works the same on AWS/server as locally. When the boss adds a NEW agent, the
register_agent helper gives it the same own-folder brain AND registers it here so
you know it exists. Current agents:
- **SBA** (sales/lead-gen, 8 skills): cold outreach, Upwork/LinkedIn/web lead-gen, Hormozi offers, sales enablement, lead qualification, meeting companion
- **SEO** (search/AI-visibility, 6 skills): SEO, technical SEO, AEO, GEO, content, audit
- **SOCIAL** (content/social, 6 skills): ad creative, social, content engine, post writer, brand voice, content calendar
- **WEBSITE** (design/frontend/deploy, 18 skills): frontend design, React/Next, UI systems, copy, domain, testing
Delegate by DOMAIN. You set the goal; each agent decides HOW using its own brain.
If a task needs an agent that does not exist yet, tell the boss to register it.

## Client website flow (STORE)
When a client asks about their website/store, or you need to hand the client their store:
1. Call **get_client_store_link** to get their store link + whether they have a login.
2. If they have no login, call **create_store_client_account** (email + password) and
   share the credentials with the client.
3. Tell the client: "Ye aapka store hai — is link pe login karke apne products add
   karo (name, price, photo), aur jab ready ho to Publish dabao. Website live ho jayegi."
4. If the client says products are ready / go live, call **publish_client_store**.

## Behavioural rules
- You are a co-founder, not a task-runner. Think STRATEGICALLY.
- When SBA hands off a client, YOU create the workspace and brief ALL agents.
- **Use the Industry info from handoff** to direct agents properly. Example: Real Estate client → SEO ko local SEO mode, Content ko property photos mode, Ads ko FB/Google Local mode.
- If something fails, YOU route the fix — don't wait, don't ask, just fix it.
- You review agent work before it goes to Ayan. You are the quality gate.
- Always think about what's best for the agency long-term.
- Keep responses clear and direct. Hinglish welcome.
- When delegating, give COMPLETE briefs — not half-baked instructions.
"""


# ── Tools definition for CEO ─────────────────────────────────────────────────

CEO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "delegate_to_workspace",
            "description": "Delegate a task to a specific agent in a workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID"},
                    "agent_type": {
                        "type": "string",
                        "enum": ["sba", "seo", "content", "website", "analytics", "ads", "social"],
                        "description": "Agent type to delegate to",
                    },
                    "task": {"type": "string", "description": "Clear task brief"},
                    "context": {"type": "string", "description": "Additional context"},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "normal", "high", "urgent"],
                        "description": "Task priority",
                    },
                },
                "required": ["workspace_id", "agent_type", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "heal_agent",
            "description": (
                "CEO self-healing: when an agent fails (tool crash, API/429 error, "
                "timeout, missing credential), the CEO detects it, classifies the "
                "error, retries/fixes the ORIGINAL task, and only escalates to the "
                "owner after N failed attempts. Keeps work flowing - no stall."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID"},
                    "agent_type": {
                        "type": "string",
                        "description": "Failed agent slug to heal (e.g. ads, seo, sba)",
                    },
                    "task": {
                        "type": "string",
                        "description": "The ORIGINAL task to re-run after fixing",
                    },
                    "context": {"type": "string", "description": "Original context"},
                    "error": {
                        "type": "string",
                        "description": "Captured error text from the failure",
                    },
                },
                "required": ["workspace_id", "agent_type", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_parallel_blast",
            "description": (
                "Brief ALL agents in a workspace simultaneously (Q4 — parallel blast). "
                "Use when a new client starts or a major campaign launches. "
                "CEO sends comprehensive brief to every agent at once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID"},
                    "client_brief": {
                        "type": "string",
                        "description": (
                            "Comprehensive client brief covering goals, brand, "
                            "target audience, budget, timeline, KPIs"
                        ),
                    },
                    "agents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific agents to brief (default: all)",
                    },
                    "campaign_name": {
                        "type": "string",
                        "description": "Campaign or project name",
                    },
                    "deadline": {
                        "type": "string",
                        "description": "Expected delivery deadline",
                    },
                },
                "required": ["workspace_id", "client_brief"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_multiagent",
            "description": (
                "Fan out a brief to MULTIPLE agents at once (built-in + any user-added "
                "custom agents) and collect every answer. Use for multi-agent analysis, "
                "brainstorms, or when the boss wants several perspectives in one shot. "
                "Agents run concurrently; each thinks (reasoning) then answers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "brief": {
                        "type": "string",
                        "description": "The shared brief/task given to every agent",
                    },
                    "agent_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Specific agent ids to run. Empty = run ALL available agents "
                            "(the 7 built-ins + every user-added custom agent)."
                        ),
                    },
                    "task_per_agent": {
                        "type": "object",
                        "description": "Optional per-agent task override: agent_id -> task string",
                        "additionalProperties": {"type": "string"},
                    },
                    "scope": {
                        "type": "object",
                        "description": "Delegation scope (kind/workspace_id). Default agency-wide.",
                    },
                },
                "required": ["brief"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspaces",
            "description": "List all client workspaces with status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workspace_report",
            "description": "Detailed report for a specific workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID"},
                },
                "required": ["workspace_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "receive_sba_handoff",
            "description": (
                "Process an SBA handoff (Q17). Creates workspace and briefs all agents. "
                "Receives structured brief + full data dump from SBA."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "handoff_id": {"type": "string", "description": "SBA handoff ID"},
                    "action": {
                        "type": "string",
                        "enum": ["accept_and_create_workspace", "review_only", "reject"],
                        "description": "What to do with the handoff",
                    },
                    "ceo_notes": {
                        "type": "string",
                        "description": "CEO's notes on the handoff",
                    },
                },
                "required": ["handoff_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_agent_output",
            "description": (
                "Review and approve/reject agent work (Q20). "
                "CEO reviews output before it goes to Ayan for final sign-off."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID"},
                    "agent_type": {"type": "string", "description": "Agent whose output to review"},
                    "output_id": {
                        "type": "string",
                        "description": "ID of the output/task being reviewed",
                    },
                    "verdict": {
                        "type": "string",
                        "enum": ["approved", "needs_revision", "rejected"],
                        "description": "Review verdict",
                    },
                    "feedback": {
                        "type": "string",
                        "description": "Detailed feedback for the agent",
                    },
                },
                "required": ["workspace_id", "agent_type", "verdict"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_error_fix",
            "description": (
                "Route error recovery to the right agent (Q21). "
                "CEO is the routing hub for all error recovery."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID"},
                    "error_type": {
                        "type": "string",
                        "enum": [
                            "seo_issue", "website_down", "ads_underperforming",
                            "content_quality", "social_engagement", "analytics_anomaly",
                            "client_complaint", "other",
                        ],
                        "description": "Type of error",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "description": "Error severity",
                    },
                    "description": {"type": "string", "description": "Error description"},
                    "route_to": {
                        "type": "string",
                        "description": "Specific agent to route to (auto-detect if empty)",
                    },
                },
                "required": ["workspace_id", "error_type", "severity", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": (
                "Create weekly/monthly agency reports (Q23). "
                "Aggregates data from all workspaces."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_type": {
                        "type": "string",
                        "enum": ["weekly", "monthly", "client_specific"],
                        "description": "Type of report",
                    },
                    "workspace_id": {
                        "type": "string",
                        "description": "Workspace ID (for client_specific reports)",
                    },
                    "period_start": {
                        "type": "string",
                        "description": "Report period start (YYYY-MM-DD)",
                    },
                    "period_end": {
                        "type": "string",
                        "description": "Report period end (YYYY-MM-DD)",
                    },
                },
                "required": ["report_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cross_workspace_knowledge",
            "description": (
                "Share learnings across workspaces (CRITICAL). "
                "Agency-level knowledge pool — successful strategies, patterns, "
                "client preferences shared across all workspaces."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get_all", "get_by_domain", "add_learning"],
                        "description": "What to do with knowledge",
                    },
                    "domain": {
                        "type": "string",
                        "description": "Agent domain filter (seo, ads, content, etc.)",
                    },
                    "learning": {
                        "type": "string",
                        "description": "New learning to add (for add_learning action)",
                    },
                    "source_workspace": {
                        "type": "string",
                        "description": "Workspace this learning came from",
                    },
                },
                "required": ["action"],
            },
        },
    },
]

# Store tools (client storefront link, client account, products, publish)
try:
    from admin.tools.store_tools import STORE_TOOLS as _STORE_TOOLS
    CEO_TOOLS = [*CEO_TOOLS, *_STORE_TOOLS]
except Exception:  # noqa: BLE001
    pass


# ── State ────────────────────────────────────────────────────────────────────


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


class CEOGraphState(TypedDict):
    messages: Annotated[list[dict[str, Any]], _append_messages]
    user_role: str
    workspace_context: str
    handoff_context: str
    review_context: str
    thinking_phases: Annotated[list[dict[str, Any]], _merge_phases]
    tool_round: int
    final_output: str
    error: str | None


# ── Helper functions ─────────────────────────────────────────────────────────


def _extract_phases(content: str) -> list[dict[str, Any]]:
    """Extract thinking phases from LLM output."""
    phases = []
    labels = ["deconstruct", "seek", "envision", "analyse", "plan", "execute"]

    # Format 1: ```think ... ```
    parts = content.split("```think")
    if len(parts) > 1:
        for i, part in enumerate(parts[1:], start=1):
            idx = part.find("```")
            block = part[:idx].strip() if idx != -1 else part.strip()
            label = labels[i - 1] if i - 1 < len(labels) else f"step_{i}"
            phases.append({"phase": label, "content": block})
        return phases

    # Format 2: Plain "think" prefix
    stripped = content.strip()
    if stripped.lower().startswith("think"):
        body = stripped[5:].strip()
        section_splits = re.split(r'\n\s*\d+\.\s+', body)
        if len(section_splits) > 1:
            for i, section in enumerate(section_splits[1:], start=1):
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

    # Format <think> tags
    tag_parts = re.split(r'<think>|</think>', content, flags=re.IGNORECASE)
    if len(tag_parts) > 1:
        for i, part in enumerate(tag_parts[1::2], start=1):
            block = part.strip()
            if block:
                label = labels[i - 1] if i - 1 < len(labels) else f"step_{i}"
                phases.append({"phase": label, "content": block})
        return phases

    return phases


def _strip_think_blocks(content: str) -> str:
    """Remove thinking blocks, leaving only the final response."""
    result = re.sub(r"```think.*?```", "", content, flags=re.DOTALL).strip()
    result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL | re.IGNORECASE).strip()

    stripped = result.strip()
    if stripped.lower().startswith("think"):
        body = stripped[5:].strip()
        lines = body.split("\n")
        in_thinking = False
        response_lines = []
        for line in lines:
            if re.match(r'^\s*\d+\.\s+\w+', line):
                in_thinking = True
                continue
            if in_thinking and re.match(r'^\s*$', line):
                in_thinking = False
                continue
            if not in_thinking:
                response_lines.append(line)
        if response_lines:
            result = "\n".join(response_lines).strip()

    return result if result else ""


def _build_workspace_context() -> str:
    """Build rich workspace context from ceo_data for CEO's awareness."""
    try:
        from admin.ceo_data import get_agency_overview
        overview = get_agency_overview()
    except ImportError:
        try:
            from admin.workspace.manager import list_workspaces
            workspaces = list_workspaces()
        except ImportError:
            return "No workspace data available yet."

        if not workspaces:
            return "No client workspaces exist yet. The agency is ready for its first client."

        lines = ["Current workspaces:"]
        for ws in workspaces:
            agents = ", ".join(ws.agents) if ws.agents else "none assigned"
            lines.append(
                f"  - {ws.name} (ID: {ws.id}, client: {ws.client_name or 'N/A'})\n"
                f"    Agents: {agents}\n"
                f"    Created: {ws.created_at.strftime('%Y-%m-%d')}"
            )
        return "\n".join(lines)

    s = overview["summary"]
    lines = [
        "=== AGENCY STATUS ===",
        f"Workspaces: {s['total_workspaces']}",
        f"Leads: {s['active_leads']} active, {s['closed_leads']} closed, {s['lost_leads']} lost",
        f"Pending reviews: {s['pending_reviews']}",
        f"Token health: {s['expired_tokens']} expired, {s['expiring_tokens']} expiring",
        "",
    ]

    # Alerts
    if overview["alerts"]:
        lines.append("ALERTS:")
        for a in overview["alerts"]:
            lines.append(f"  [{a['severity'].upper()}] {a['message']}")
        lines.append("")

    # Workspaces
    if overview["workspaces"]:
        lines.append("WORKSPACES:")
        for ws in overview["workspaces"]:
            lines.append(
                f"  - {ws['name']} (ID: {ws['id']}, client: {ws['client_name']})\n"
                f"    Agents: {', '.join(ws['agents']) if ws['agents'] else 'none'}"
            )
    else:
        lines.append("No workspaces exist yet. The agency is ready for its first client.")

    return "\n".join(lines)


def _build_handoff_context() -> str:
    """Build context from pending SBA handoffs."""
    try:
        from admin.agency.sba_store import list_handoffs
        handoffs = list_handoffs()
    except ImportError:
        return "No handoff data available."

    pending = [h for h in handoffs if not h.get("workspace_id")]
    if not pending:
        return "No pending handoffs from SBA."

    lines = [f"Pending SBA handoffs ({len(pending)}):"]
    for h in pending:
        brief = h.get("brief", {})
        lines.append(
            f"  - Handoff ID: {h['id']}\n"
            f"    Client: {brief.get('lead_name', 'N/A')} "
            f"({brief.get('business_name', 'N/A')})\n"
            f"    Industry: {brief.get('industry', 'unknown')}\n"
            f"    Score: {brief.get('score', 'N/A')}\n"
            f"    Needs: {', '.join(brief.get('client_needs', [])) or 'N/A'}\n"
            f"    Scope: {brief.get('agreed_scope', 'N/A')}\n"
            f"    Time: {h.get('handoff_time', 'N/A')}"
        )
    return "\n".join(lines)


def _build_review_context() -> str:
    """Build context of agent outputs pending CEO review."""
    try:
        from admin.workspace.manager import list_pending_reviews
        reviews = list_pending_reviews()
    except (ImportError, AttributeError):
        return "No pending reviews."

    if not reviews:
        return "No agent outputs pending your review."

    lines = [f"Pending reviews ({len(reviews)}):"]
    for r in reviews:
        lines.append(
            f"  - {r.get('agent_type', 'unknown')} output in workspace "
            f"{r.get('workspace_id', 'N/A')}\n"
            f"    Task: {r.get('task', 'N/A')}\n"
            f"    Output: {r.get('output_preview', 'N/A')[:200]}"
        )
    return "\n".join(lines)


# ── Graph Nodes ──────────────────────────────────────────────────────────────


async def call_llm(state: CEOGraphState) -> dict:
    """Call the LLM with CEO system prompt and tools."""
    system_prompt = CEO_SYSTEM_PROMPT.format(
        user_role=state.get("user_role", "the agency owner"),
        workspace_context=state.get("workspace_context", "No workspace data."),
        handoff_context=state.get("handoff_context", "No pending handoffs."),
        review_context=state.get("review_context", "No pending reviews."),
    )

    # ── Inject the CEO's OWN skills (decision brain + report voice) ──
    # Found via find-skills tooling, shipped locally under ceo_skills_repo/.
    # The CEO uses these with its tools/functions to think & report like a real CEO.
    try:
        from admin.agency.ceo_skills import detect_ceo_skills, build_ceo_skill_context
        boss_msg = ""
        for m in state.get("messages", []):
            if isinstance(m, dict) and m.get("role") == "user":
                boss_msg = m.get("content", "") or ""
                break
        ceo_skills = detect_ceo_skills(boss_msg or "report status decision")
        ceo_skill_block = build_ceo_skill_context(ceo_skills)
        if ceo_skill_block:
            system_prompt = system_prompt + "\n\n" + ceo_skill_block
    except Exception as exc:  # noqa: BLE001
        logger.warning("CEO skill injection skipped: %s", exc)

    oll_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]

    for msg in state.get("messages", []):
        if isinstance(msg, dict):
            oll_messages.append(msg)

    has_user = any(m.get("role") == "user" for m in oll_messages)
    if not has_user:
        oll_messages.append({"role": "user", "content": "Hello"})

    try:
        client_api = openai.AsyncOpenAI(
            api_key=settings.AGENCY_CEO_API_KEY or None,
            base_url=settings.AGENCY_CEO_API_BASE or None,
        )
        response = await client_api.chat.completions.create(
            model=settings.AGENCY_CEO_MODEL,
            messages=oll_messages,
            tools=CEO_TOOLS,
            tool_choice="auto",
        )
    except Exception as exc:
        logger.exception("CEO LLM call failed")
        return {
            "error": str(exc),
            "thinking_phases": [],
            "messages": [],
        }

    choice = response.choices[0]
    msg = choice.message

    phases: list[dict[str, Any]] = []
    if msg.content:
        phases = _extract_phases(msg.content)

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


def route_from_llm(state: CEOGraphState) -> Literal["run_tools", "finalize", "__end__"]:
    """Route based on LLM response: tool calls -> run_tools, else -> finalize."""
    if state.get("error"):
        return "__end__"

    messages = state.get("messages", [])
    if not messages:
        return "finalize"

    last = messages[-1]
    if isinstance(last, dict) and last.get("tool_calls"):
        tool_round = state.get("tool_round", 0)
        if tool_round >= MAX_TOOL_ROUNDS:
            return "finalize"
        return "run_tools"

    return "finalize"


async def run_tools(state: CEOGraphState) -> dict:
    """Execute CEO tools (workspace queries, delegation, handoffs, reviews, errors, reports)."""
    messages = state.get("messages", [])
    if not messages:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    last = messages[-1]
    tool_calls = last.get("tool_calls", []) if isinstance(last, dict) else []

    if not tool_calls:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    tool_results: list[dict[str, Any]] = []

    for tc in tool_calls:
        tool_name = tc["function"]["name"]
        try:
            tool_args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            tool_args = {}

        logger.info("CEO tool call: %s(%s)", tool_name, json.dumps(tool_args))

        try:
            result_text = await _execute_ceo_tool(tool_name, tool_args)
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


async def _execute_ceo_tool(name: str, args: dict) -> str:
    """Execute a CEO tool and return the result text."""

    if name == "list_workspaces":
        return _tool_list_workspaces()

    elif name == "get_workspace_report":
        return _tool_workspace_report(args.get("workspace_id", ""))

    elif name == "delegate_to_workspace":
        return await _tool_delegate(args)

    elif name == "delegate_parallel_blast":
        return await _tool_parallel_blast(args)

    elif name == "run_multiagent":
        return await _tool_run_multiagent(args)

    elif name == "receive_sba_handoff":
        return await _tool_receive_handoff(args)

    elif name == "review_agent_output":
        return await _tool_review_output(args)

    elif name == "route_error_fix":
        return await _tool_route_error(args)

    elif name == "heal_agent":
        return await _tool_heal_agent(args)

    elif name == "generate_report":
        return await _tool_generate_report(args)

    elif name == "run_sales":
        return await _tool_run_sales(args)

    elif name == "email_client":
        return await _tool_email_client(args)

    elif name == "get_cross_workspace_knowledge":
        return await _tool_cross_workspace_knowledge(args)

    elif name in {"get_client_store_link", "create_store_client_account",
                  "list_store_products", "publish_client_store"}:
        from admin.tools.store_tools import execute_store_tool
        return execute_store_tool(name, args)

    else:
        return f"Unknown tool: {name}"


# ── Tool implementations ─────────────────────────────────────────────────────


def _tool_list_workspaces() -> str:
    """List all workspaces with health, alerts, and activity."""
    try:
        from admin.ceo_data import get_agency_overview
        overview = get_agency_overview()
    except ImportError:
        # Fallback to old basic listing
        try:
            from admin.workspace.manager import list_workspaces
            workspaces = list_workspaces()
        except ImportError:
            return "No workspace data available."
        if not workspaces:
            return "No workspaces exist yet."
        result = []
        for ws in workspaces:
            result.append(
                f"- {ws.name} (ID: {ws.id}, client: {ws.client_name or 'N/A'}) "
                f"agents: {', '.join(ws.agents) if ws.agents else 'none'}"
            )
        return "\n".join(result)

    s = overview["summary"]
    alerts = overview["alerts"]

    lines = [
        "=== AGENCY OVERVIEW ===",
        f"Workspaces: {s['total_workspaces']}",
        f"Active Leads: {s['active_leads']} | Closed: {s['closed_leads']} | Lost: {s['lost_leads']}",
        f"Pending Handoffs: {s['pending_handoffs']} | Pending Reviews: {s['pending_reviews']}",
        f"Tokens: {s['total_tokens']} total | {s['expired_tokens']} expired | {s['expiring_tokens']} expiring",
        "",
    ]

    if alerts:
        lines.append(f"--- ALERTS ({len(alerts)}) ---")
        for a in alerts:
            lines.append(f"  [{a['severity'].upper()}] {a['message']}")
        lines.append("")

    lines.append("--- WORKSPACES ---")
    for ws in overview["workspaces"]:
        lines.append(
            f"  {ws['name']} (ID: {ws['id']}, client: {ws['client_name']})\n"
            f"    Agents: {', '.join(ws['agents']) if ws['agents'] else 'none'}\n"
            f"    Created: {ws['created_at'][:10]}"
        )

    return "\n".join(lines)


def _tool_workspace_report(ws_id: str, notify_owner: bool = False) -> str:
    """Get detailed workspace report with health score, agent status, and alerts."""
    try:
        from admin.ceo_data import get_workspace_health
        health = get_workspace_health(ws_id)
    except ImportError:
        # Fallback to old basic report
        try:
            from admin.workspace.manager import get_workspace
        except ImportError:
            return "Workspace manager not available."
        ws = get_workspace(ws_id)
        if not ws:
            return f"Workspace '{ws_id}' not found."
        return (
            f"Workspace: {ws.name}\n"
            f"Client: {ws.client_name or 'N/A'}\n"
            f"Description: {ws.description or 'None'}\n"
            f"Agents: {', '.join(ws.agents) if ws.agents else 'none'}\n"
            f"Created: {ws.created_at.isoformat()}"
        )

    if "error" in health:
        return health["error"]

    ws = health["workspace"]
    lines = [
        f"=== WORKSPACE REPORT: {ws['name']} ===",
        f"Client: {ws['client_name']}",
        f"Health: {health['health_score']}/100 ({health['health_label']})",
        f"Created: {ws['created_at'][:10]}",
        "",
    ]

    # Alerts
    if health["alerts"]:
        lines.append("--- ALERTS ---")
        for a in health["alerts"]:
            lines.append(f"  [{a['severity'].upper()}] {a['message']}")
        lines.append("")

    # Agent status
    if health["agent_status"]:
        lines.append("--- AGENT STATUS ---")
        for agent, status in health["agent_status"].items():
            lines.append(
                f"  {agent}: {status['status']} "
                f"(outputs: {status['total_outputs']}, "
                f"pending reviews: {status['pending_reviews']}, "
                f"last: {status['last_activity'][:10] if status['last_activity'] != 'never' else 'never'})"
            )
        lines.append("")

    # Token status
    if health["token_status"]:
        lines.append("--- TOKEN STATUS ---")
        for t in health["token_status"]:
            lines.append(f"  {t['platform']}: {t['status']} (expires: {t.get('expires_at', 'N/A')[:10]})")
        lines.append("")

    # Pending reviews
    if health["pending_reviews"]:
        lines.append(f"--- PENDING REVIEWS ({len(health['pending_reviews'])}) ---")
        for r in health["pending_reviews"][:5]:
            lines.append(f"  {r.get('agent_type', '?')} — {r.get('task', 'N/A')[:80]}")
        lines.append("")

    # Recent activity
    if health["recent_activity"]:
        lines.append("--- RECENT ACTIVITY ---")
        for a in health["recent_activity"][-5:]:
            lines.append(f"  [{a.get('agent_type', '?')}] {a.get('action', '?')} — {a.get('details', '')[:80]}")

    return "\n".join(lines)


async def _tool_run_sales(args: dict) -> str:
    """CEO sales move: run the SBA worker for a workspace (lead find + email).

    SBA is a CEO tool, not a background service. The boss asks the CEO to run
    sales; the CEO instantiates the SBA worker for that workspace with a
    QUEUED email client (emails go to the outbox, not live SMTP yet) and runs
    one pass. Everything is wrapped so a failure never takes the CEO down —
    the boss gets a clear status instead.
    """
    ws_id = args.get("workspace_id", "") or args.get("client", "")
    task = args.get("task", "Find leads and reach out to potential clients")

    try:
        from admin.workspace.manager import get_workspace
    except ImportError:
        return "Workspace manager not available."

    ws = get_workspace(ws_id) if ws_id else None
    workspace_name = ws.name if ws else (ws_id or "agency")

    try:
        from admin.tools.email_queue import QueuedEmailClient
        from admin.agency.sba_autopilot import SBAAutopilot
        from admin.agency import lifecycle as lc

        # Lifecycle gate: SBA only runs while CEO-mandated (STANDBY -> ACTIVE).
        lc.wake("sba", brief_id=f"run_sales:{workspace_name}")
        try:
            ap = SBAAutopilot(
                email_client=QueuedEmailClient(),
                workspace_name=workspace_name,
            )
            stats = await ap.run_once()
        finally:
            # Self-sleep back to STANDBY even on failure — no loop left running.
            lc.sleep("sba")
        leads = stats.get("new_leads", 0) if isinstance(stats, dict) else 0
        emails = stats.get("emails_sent", 0) if isinstance(stats, dict) else 0
        return (
            f"Sales pass complete for '{workspace_name}':\n"
            f"  New leads found: {leads}\n"
            f"  Emails queued: {emails}\n"
            f"  (Emails are queued, not yet sent live. Review /api/ceo/email/outbox.)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("CEO run_sales failed for %s", workspace_name)
        return (
            f"Bhai, SBA sales pass fail ho gaya '{workspace_name}' ke liye: {exc}. "
            "Leads jo mil gaye the wo save ho gaye hain, lekin email queue nahi "
            "hua. Baad mein retry karo ya mujhe bolo."
        )


async def _tool_email_client(args: dict) -> str:
    """Queue a client email via the outbox (CEO's email tool).

    Args: to_email, subject, body, workspace_id (optional).
    Returns a boss-readable confirmation. Failures are reported, never silent.
    """
    to_email = (args.get("to_email") or "").strip()
    subject = args.get("subject", "")
    body = args.get("body", "")
    ws_id = args.get("workspace_id", "")

    if not to_email:
        return "Email nahi bheja — to_email missing hai."

    try:
        from admin.tools.email_queue import queue_email
        msg_id = await queue_email(
            to_email=to_email,
            subject=subject,
            body=body,
            from_agent="ceo",
            workspace_id=ws_id,
        )
        return (
            f"Email queued to {to_email} (id {msg_id}).\n"
            f"Subject: {subject}\n"
            "Boss can review it at /api/ceo/email/outbox (not sent live yet)."
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("CEO email_client failed")
        return f"Bhai, email queue fail ho gaya {to_email} ke liye: {exc}"



async def _tool_delegate(args: dict) -> str:
    """Delegate task to a single agent in a workspace."""
    ws_id = args.get("workspace_id", "")
    agent_type = args.get("agent_type", "sba")
    task = args.get("task", "")
    context = args.get("context", "")
    priority = args.get("priority", "normal")

    try:
        from admin.workspace.manager import get_workspace
    except ImportError:
        return "Workspace manager not available."

    ws = get_workspace(ws_id)
    if not ws:
        return f"Workspace '{ws_id}' not found."

    # ── Dynamic / user-added agents (Munder-style) ──────────────────────────
    # If the requested agent is a custom agent from the registry, route it
    # through the workers layer (which already knows how to run it) instead of
    # the workspace's fixed agent list. This lets the CEO delegate to any
    # user-created agent on the fly.
    try:
        from admin.agency import agent_registry as reg

        custom = await reg.get_agent(agent_type)
    except Exception:  # noqa: BLE001
        custom = None

    if custom is not None:
        from admin.agency.workers import run_worker

        result = await run_worker(
            agent_type, task,
            {"scope": {"kind": "workspace", "workspace_id": ws_id}},
        )
        ok = result.get("ok", False)
        resp = (result.get("result") or {}).get("answer") or result.get("error") or str(result)
        if not ok:
            # CEO self-heals instead of just reporting the failure.
            from admin.agency.self_heal import heal_and_report

            return await heal_and_report(
                slug=agent_type, workspace_id=ws_id, task=task,
                context=context, error=result.get("error", ""),
            )
        return (
            f"Delegated to {custom['name']} (custom) in {ws.name}:\n"
            f"Task: {task}\n"
            f"Priority: {priority}\n"
            f"Status: done\n"
            f"Response: {str(resp)[:500]}"
        )

    if agent_type not in ws.agents:
        return f"Agent '{agent_type}' not in workspace '{ws_id}'. Available: {ws.agents}"

    from admin.workspace.manager import route_to_agent

    priority_tag = f"[PRIORITY: {priority.upper()}] " if priority != "normal" else ""
    full_message = (
        f"{priority_tag}[CEO DELEGATION] {task}\n\n"
        f"Context: {context}" if context else
        f"{priority_tag}[CEO DELEGATION] {task}"
    )

    # Part C — audit trail: brief the employee on the agent_bus.
    message_id = None
    try:
        from admin.agency.agent_bus import get_bus

        message_id = get_bus().brief(
            "ceo", agent_type, ws_id, task,
            objective=f"CEO delegation to {agent_type}",
            context=context, required_action="execute + respond", status="active",
        )
    except Exception:
        logger.warning("agent_bus brief failed for delegate %s", agent_type)

    try:
        response = await route_to_agent(
            workspace_id=ws_id,
            agent_type=agent_type,
            message=full_message,
        )

        # Built-in agents return plain strings; non-exception failures come
        # back as error-ish replies. Detect them here so the CEO heals
        # instead of reporting success (closes the built-in self-heal gap).
        from admin.agency.self_heal import looks_like_error

        if looks_like_error(str(response)):
            if message_id:
                try:
                    from admin.agency.agent_bus import get_bus

                    get_bus().respond(
                        message_id, result=str(response)[:4000],
                        status="error", errors=str(response)[:500],
                    )
                except Exception:
                    pass
            from admin.agency.self_heal import heal_and_report

            return await heal_and_report(
                slug=agent_type, workspace_id=ws_id, task=task,
                context=context, error=str(response)[:500],
            )

        # Record the employee's report on the bus (audit trail).
        if message_id:
            try:
                from admin.agency.agent_bus import get_bus

                get_bus().respond(
                    message_id, result=str(response)[:4000],
                    status="done", errors="",
                )
            except Exception:
                pass

        # Store the output for review (Q20)
        try:
            from admin.workspace.manager import store_agent_output
            store_agent_output(
                workspace_id=ws_id,
                agent_type=agent_type,
                task=task,
                output=response,
            )
        except (ImportError, AttributeError):
            pass

        # Log activity for CEO tracking
        try:
            from admin.ceo_data import log_activity
            log_activity(
                workspace_id=ws_id,
                agent_type=agent_type,
                action="delegation_complete",
                details=f"Task: {task[:100]}",
                metadata={"priority": priority, "response_len": len(response)},
            )
        except Exception:
            pass

        return (
            f"Delegated to {agent_type} in {ws.name}:\n"
            f"Task: {task}\n"
            f"Priority: {priority}\n"
            f"Response: {response[:500]}"
        )
    except Exception as exc:
        # CEO self-heals the failure instead of just reporting it.
        from admin.agency.self_heal import heal_and_report

        return await heal_and_report(
            slug=agent_type, workspace_id=ws_id, task=task,
            context=context, error=f"{type(exc).__name__}: {exc}",
        )


async def _tool_parallel_blast(args: dict) -> str:
    """Brief ALL agents in a workspace simultaneously (Q4)."""
    ws_id = args.get("workspace_id", "")
    client_brief = args.get("client_brief", "")
    campaign_name = args.get("campaign_name", "General")
    deadline = args.get("deadline", "TBD")
    specific_agents = args.get("agents")

    try:
        from admin.workspace.manager import get_workspace
    except ImportError:
        return "Workspace manager not available."

    ws = get_workspace(ws_id)
    if not ws:
        return f"Workspace '{ws_id}' not found."

    agents_to_brief = specific_agents or [a for a in ws.agents if a != "memory"]

    from admin.workspace.manager import route_to_agent

    results = []
    bus_ids: dict[str, str] = {}
    try:
        from admin.agency.agent_bus import get_bus as _get_bus
        _bus = _get_bus()
        for _a in agents_to_brief:
            if _a in ws.agents:
                bus_ids[_a] = _bus.brief(
                    "ceo", _a, ws_id, client_brief,
                    objective=f"Parallel blast: {campaign_name}",
                    context=client_brief, required_action="execute + respond", status="active",
                )
    except Exception:
        logger.warning("agent_bus brief failed for parallel blast %s", ws_id)

    for agent_type in agents_to_brief:
        if agent_type not in ws.agents:
            results.append(f"  SKIP {agent_type}: not in workspace")
            continue

        agent_brief = (
            f"[CEO PARALLEL BLAST — Campaign: {campaign_name}]\n\n"
            f"Client Brief:\n{client_brief}\n\n"
            f"Deadline: {deadline}\n\n"
            f"As the {agent_type.upper()} agent, analyze this brief and:\n"
            f"1. Identify what YOU need to do for this client\n"
            f"2. List your specific deliverables\n"
            f"3. Flag any dependencies on other agents\n"
            f"4. State your estimated timeline\n"
            f"5. Note any questions or concerns\n\n"
            f"Think independently. You are the expert in your domain."
        )

        try:
            response = await route_to_agent(
                workspace_id=ws_id,
                agent_type=agent_type,
                message=agent_brief,
            )
            results.append(f"  OK {agent_type}: {response[:200]}")

            # Part C — audit trail: employee reports back on the bus.
            _mid = bus_ids.get(agent_type)
            if _mid:
                try:
                    from admin.agency.agent_bus import get_bus as _get_bus2
                    _get_bus2().respond(_mid, result=str(response)[:4000], status="done", errors="")
                except Exception:
                    pass

            # Store output for review
            try:
                from admin.workspace.manager import store_agent_output
                store_agent_output(
                    workspace_id=ws_id,
                    agent_type=agent_type,
                    task=f"Parallel blast: {campaign_name}",
                    output=response,
                )
            except (ImportError, AttributeError):
                pass

        except Exception as exc:
            results.append(f"  FAIL {agent_type}: {exc}")
            _mid = bus_ids.get(agent_type)
            if _mid:
                try:
                    from admin.agency.agent_bus import get_bus as _get_bus3
                    _get_bus3().respond(_mid, result="", status="failed", errors=str(exc)[:2000])
                except Exception:
                    pass

    return (
        f"Parallel blast completed for {ws.name} — Campaign: {campaign_name}\n"
        f"Deadline: {deadline}\n"
        f"Agents briefed: {len(agents_to_brief)}\n\n"
        f"Results:\n" + "\n".join(results)
    )


# Built-in (fixed) employee roster. Custom/user-added agents are merged in at
# runtime from the agent registry so the CEO can fan out to ANY agent.
_BUILTIN_AGENT_IDS = ["sba", "seo", "website", "content", "ads", "social", "analytics"]


async def _all_agent_ids() -> list[str]:
    """Built-in 7 + every user-added custom agent from the registry."""
    ids = list(_BUILTIN_AGENT_IDS)
    try:
        from admin.agency import agent_registry as reg
        for ca in await reg.list_agents():
            cid = ca.get("id")
            if cid and cid not in ids:
                ids.append(cid)
    except Exception:  # noqa: BLE001
        logger.warning("Could not load custom agents for multi-agent fan-out")
    return ids


async def run_multiagent(
    brief: str,
    agent_ids: list[str] | None = None,
    task_per_agent: dict[str, str] | None = None,
    scope: dict | None = None,
) -> dict:
    """Multi-agent fan-out: run many agents concurrently, collect all answers.

    This is the core "multi-agent" primitive — the CEO (or any caller) can throw
    a brief at N agents at once. Each agent thinks (reasoning) then answers via
    its own runner (built-in employees OR user-added custom agents). Failures are
    isolated per-agent and reported with a Hindi status, never aborting the run.

    Returns a structured report: {"ran", "failed", "results": [...]}.
    """
    scope = scope or {"kind": "agency", "workspace_id": "agency"}
    task_map = task_per_agent or {}
    chosen = agent_ids if agent_ids else await _all_agent_ids()
    chosen = [a for a in chosen if a]

    if not chosen:
        return {"ran": 0, "failed": 0, "results": [], "note": "No agents available."}

    from admin.agency.workers import run_worker

    async def _run_one(aid: str) -> dict:
        task = (task_map.get(aid) or brief).strip() or brief
        try:
            res = await run_worker(aid, task, {"scope": scope})
        except Exception as exc:  # noqa: BLE001
            return {
                "agent_id": aid,
                "ok": False,
                "error": str(exc),
                "hindi_status": f"Bhai, {aid} agent kaam fail ho gaya: {exc}",
            }
        result = res.get("result") or {}
        answer = result.get("answer") or result.get("response") or res.get("error") or ""
        reasoning = result.get("reasoning", "")
        return {
            "agent_id": aid,
            "ok": bool(res.get("ok", False)),
            "answer": answer,
            "reasoning": reasoning,
            "hindi_status": res.get("hindi_status", ""),
        }

    results = await asyncio.gather(*(_run_one(a) for a in chosen))
    failed = sum(1 for r in results if not r.get("ok"))
    return {"ran": len(results), "failed": failed, "results": results}


async def _tool_run_multiagent(args: dict) -> str:
    """CEO tool wrapper for run_multiagent (used by the LangGraph CEO)."""
    report = await run_multiagent(
        brief=args.get("brief", ""),
        agent_ids=args.get("agent_ids") or None,
        task_per_agent=args.get("task_per_agent") or None,
        scope=args.get("scope") or None,
    )
    if report.get("ran") == 0:
        return "Multi-agent run: koi agents nahi chale. Pehle agents add karo ya list check karo."

    lines = [f"=== MULTI-AGENT RUN ({report['ran']} agents, {report['failed']} failed) ===\n"]
    scope = args.get("scope") or {}
    ws_id = scope.get("workspace_id", "") if isinstance(scope, dict) else ""
    task_per_agent = args.get("task_per_agent") or {}
    brief = args.get("brief", "")
    for r in report["results"]:
        status = "OK" if r.get("ok") else "FAIL"
        ans = (r.get("answer") or r.get("hindi_status") or "")[:400]
        if not r.get("ok") and ws_id:
            # CEO self-heals the failed agent instead of leaving it broken.
            try:
                from admin.agency.self_heal import heal_and_report

                heal = await heal_and_report(
                    slug=r["agent_id"],
                    workspace_id=ws_id,
                    task=task_per_agent.get(r["agent_id"], brief),
                    error=r.get("hindi_status") or r.get("answer") or "multiagent failure",
                )
                ans = f"HEALED: {heal[:300]}"
                status = "HEALED" if "✅" in heal else "FAIL"
            except Exception as hx:  # noqa: BLE001
                ans = f"heal error: {hx}"
        lines.append(f"[{status}] {r['agent_id']}: {ans}")
    return "\n".join(lines)


async def _tool_receive_handoff(args: dict) -> str:
    """Receive SBA handoff and optionally create workspace (Q17)."""
    handoff_id = args.get("handoff_id", "")
    action = args.get("action", "review_only")
    ceo_notes = args.get("ceo_notes", "")

    try:
        from admin.agency.sba_store import get_handoff
    except ImportError:
        return "SBA store not available."

    handoff = get_handoff(handoff_id)
    if not handoff:
        return f"Handoff '{handoff_id}' not found."

    brief = handoff.get("brief", {})
    full_dump = handoff.get("full_dump", {})

    if action == "reject":
        return (
            f"Handoff {handoff_id} rejected by CEO.\n"
            f"Client: {brief.get('lead_name', 'N/A')}\n"
            f"Notes: {ceo_notes}"
        )

    if action == "review_only":
        return (
            f"Handoff {handoff_id} — Review Only\n\n"
            f"=== STRUCTURED BRIEF ===\n"
            f"Client: {brief.get('lead_name', 'N/A')} ({brief.get('business_name', 'N/A')})\n"
            f"Email: {brief.get('email', 'N/A')}\n"
            f"Phone: {brief.get('phone', 'N/A')}\n"
            f"Industry: {brief.get('industry', 'unknown')}\n"
            f"Score: {brief.get('score', 'N/A')}\n"
            f"Source: {brief.get('source', 'N/A')}\n"
            f"Key Signals: {brief.get('key_signals', 'N/A')}\n"
            f"Client Needs: {', '.join(brief.get('client_needs', [])) or 'N/A'}\n"
            f"Agreed Scope: {brief.get('agreed_scope', 'N/A')}\n"
            f"Next Steps: {', '.join(brief.get('next_steps', [])) or 'N/A'}\n\n"
            f"=== FULL DATA DUMP ===\n"
            f"Meetings: {len(full_dump.get('meetings', []))}\n"
            f"All Notes: {len(full_dump.get('all_notes', []))}\n"
            f"Action Items: {len(full_dump.get('action_items', []))}\n"
            f"Lead Response History: {len(full_dump.get('lead_response_history', []))}\n\n"
            f"CEO Notes: {ceo_notes or 'None'}"
        )

    if action == "accept_and_create_workspace":
        # Create workspace
        try:
            from admin.workspace.manager import create_workspace
            from admin.api.models.schemas import WorkspaceCreate, ClientContext

            ws_payload = WorkspaceCreate(
                name=f"{brief.get('business_name', brief.get('lead_name', 'Client'))} Workspace",
                client_name=brief.get("lead_name", ""),
                client_context=ClientContext(
                    industry=brief.get("industry", ""),
                    description=brief.get("agreed_scope", ""),
                ),
                description=(
                    f"Client: {brief.get('lead_name')} ({brief.get('business_name')})\n"
                    f"Industry: {brief.get('industry', 'N/A')}\n"
                    f"Scope: {brief.get('agreed_scope', 'N/A')}\n"
                    f"Needs: {', '.join(brief.get('client_needs', []))}\n"
                    f"CEO Notes: {ceo_notes}"
                ),
            )
            ws = create_workspace(ws_payload)

            # Link workspace back to handoff
            try:
                from admin.agency.sba_store import mark_handoff_workspace_created
                await mark_handoff_workspace_created(handoff_id, ws.id)
            except (ImportError, AttributeError):
                pass

            # Add CEO notes to handoff
            handoff["ceo_notes"] = ceo_notes
            handoff["workspace_id"] = ws.id

            # Log activity
            try:
                from admin.ceo_data import log_activity
                log_activity(
                    workspace_id=ws.id,
                    agent_type="ceo",
                    action="handoff_accepted",
                    details=f"Handoff {handoff_id[:20]}... → Workspace {ws.name}",
                    metadata={"lead_name": brief.get("lead_name", ""), "ceo_notes": ceo_notes[:200]},
                )
            except Exception:
                pass

            return (
                f"Handoff ACCEPTED. Workspace created.\n\n"
                f"Workspace: {ws.name} (ID: {ws.id})\n"
                f"Client: {brief.get('lead_name', 'N/A')}\n"
                f"Handoff ID: {handoff_id}\n\n"
                f"Next: Use delegate_parallel_blast to brief all agents.\n"
                f"CEO Notes: {ceo_notes or 'None'}"
            )
        except Exception as exc:
            return f"Failed to create workspace from handoff: {exc}"

    return f"Unknown action: {action}"


async def _tool_review_output(args: dict) -> str:
    """Review agent output (Q20) — CEO review stage."""
    ws_id = args.get("workspace_id", "")
    agent_type = args.get("agent_type", "")
    output_id = args.get("output_id", "")
    verdict = args.get("verdict", "approved")
    feedback = args.get("feedback", "")

    # Store the review
    try:
        from admin.workspace.manager import store_review
        store_review(
            workspace_id=ws_id,
            agent_type=agent_type,
            output_id=output_id,
            verdict=verdict,
            feedback=feedback,
        )
    except (ImportError, AttributeError):
        pass

    # Log activity
    try:
        from admin.ceo_data import log_activity
        log_activity(
            workspace_id=ws_id,
            agent_type=agent_type,
            action=f"review_{verdict}",
            details=f"Output {output_id[:20]}... — {feedback[:100]}",
            metadata={"verdict": verdict},
        )
    except Exception:
        pass

    if verdict == "approved":
        return (
            f"APPROVED: {agent_type} output in workspace {ws_id}\n"
            f"Feedback: {feedback or 'No issues — good work.'}\n"
            f"Status: Ready for Ayan's final sign-off."
        )
    elif verdict == "needs_revision":
        return (
            f"NEEDS REVISION: {agent_type} output in workspace {ws_id}\n"
            f"Feedback: {feedback}\n"
            f"Action: Agent has been notified to revise."
        )
    else:
        return (
            f"REJECTED: {agent_type} output in workspace {ws_id}\n"
            f"Feedback: {feedback}\n"
            f"Action: Rework required. CEO to rebrief if needed."
        )


async def _tool_route_error(args: dict) -> str:
    """Route error fix to the right agent (Q21)."""
    ws_id = args.get("workspace_id", "")
    error_type = args.get("error_type", "other")
    severity = args.get("severity", "medium")
    description = args.get("description", "")
    route_to = args.get("route_to", "")

    # Auto-detect which agent to route to
    error_agent_map = {
        "seo_issue": "seo",
        "website_down": "website",
        "ads_underperforming": "ads",
        "content_quality": "content",
        "social_engagement": "social",
        "analytics_anomaly": "analytics",
    }
    target_agent = route_to or error_agent_map.get(error_type, "")

    # Store the error
    try:
        from admin.workspace.manager import store_error
        store_error(
            workspace_id=ws_id,
            error_type=error_type,
            severity=severity,
            description=description,
            routed_to=target_agent,
        )
    except (ImportError, AttributeError):
        pass

    if not target_agent:
        return (
            f"ERROR LOGGED (severity: {severity}):\n"
            f"Type: {error_type}\n"
            f"Description: {description}\n"
            f"No auto-route agent determined. CEO manual intervention needed."
        )

    # Route the fix
    try:
        from admin.workspace.manager import route_to_agent
        fix_brief = (
            f"[CEO ERROR ROUTING — Severity: {severity.upper()}]\n\n"
            f"Error Type: {error_type}\n"
            f"Description: {description}\n\n"
            f"Analyze this error and:\n"
            f"1. Identify root cause\n"
            f"2. Propose immediate fix\n"
            f"3. Suggest prevention for future\n"
            f"4. State timeline for fix"
        )

        response = await route_to_agent(
            workspace_id=ws_id,
            agent_type=target_agent,
            message=fix_brief,
        )

        return (
            f"ERROR ROUTED to {target_agent} (severity: {severity}):\n"
            f"Type: {error_type}\n"
            f"Description: {description}\n\n"
            f"Agent Response: {response[:500]}"
        )
    except Exception as exc:
        return (
            f"ERROR LOGGED but routing failed:\n"
            f"Type: {error_type}\n"
            f"Description: {description}\n"
            f"Route to: {target_agent}\n"
            f"Error: {exc}"
        )


async def _tool_heal_agent(args: dict) -> str:
    """CEO self-heal: detect a failed agent run, fix/retry, escalate if needed."""
    from admin.agency.self_heal import heal_and_report

    return await heal_and_report(
        slug=args.get("agent_type") or args.get("slug", ""),
        workspace_id=args.get("workspace_id", ""),
        task=args.get("task", ""),
        context=args.get("context", ""),
        error=args.get("error", ""),
    )


async def _tool_generate_report(args: dict) -> str:
    """Generate weekly/monthly agency report with real data."""
    report_type = args.get("report_type", "weekly")
    ws_id = args.get("workspace_id")
    period_start = args.get("period_start", "")
    period_end = args.get("period_end", "")

    now = datetime.now(timezone.utc)

    # Get real data from ceo_data
    try:
        from admin.ceo_data import get_agency_overview, get_workspace_health, get_alerts
        overview = get_agency_overview()
        alerts = get_alerts()
    except ImportError:
        overview = None
        alerts = []

    # Client-specific report
    if report_type == "client_specific" and ws_id:
        try:
            from admin.ceo_data import get_workspace_health
            health = get_workspace_health(ws_id)
        except ImportError:
            health = None

        if health and "workspace" in health:
            ws = health["workspace"]
            lines = [
                f"=== CLIENT REPORT: {ws['name']} ===",
                f"Period: {period_start or 'N/A'} to {period_end or 'N/A'}",
                f"Client: {ws['client_name']}",
                f"Health Score: {health['health_score']}/100 ({health['health_label']})",
                f"Agents: {', '.join(ws['agents']) if ws['agents'] else 'none'}",
                "",
                "--- AGENT STATUS ---",
            ]
            for agent, status in health.get("agent_status", {}).items():
                lines.append(
                    f"  {agent}: {status['status']} "
                    f"(outputs: {status['total_outputs']}, "
                    f"pending reviews: {status['pending_reviews']})"
                )
            lines.append("")

            if health.get("alerts"):
                lines.append("--- ALERTS ---")
                for a in health["alerts"]:
                    lines.append(f"  [{a['severity'].upper()}] {a['message']}")
                lines.append("")

            if health.get("token_status"):
                lines.append("--- TOKEN STATUS ---")
                for t in health["token_status"]:
                    lines.append(f"  {t['platform']}: {t['status']}")
                lines.append("")

            lines.append("--- RECOMMENDATIONS ---")
            if health["health_score"] < 60:
                lines.append("  - Workspace health is below 60 — immediate attention needed")
            if any(t["status"] == "expired" for t in health.get("token_status", [])):
                lines.append("  - Expired tokens blocking agent work — renew immediately")
            if health.get("pending_reviews"):
                lines.append(f"  - {len(health['pending_reviews'])} outputs pending CEO review")

            return "\n".join(lines)
        else:
            return f"Workspace '{ws_id}' not found or data unavailable."

    # Agency-wide report
    if overview:
        s = overview["summary"]
        report_lines = [
            f"=== TAGS AGENCY {'WEEKLY' if report_type == 'weekly' else 'MONTHLY'} REPORT ===",
            f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}",
            f"Period: {period_start or 'N/A'} to {period_end or 'N/A'}",
            "",
            "--- AGENCY OVERVIEW ---",
            f"Total Workspaces: {s['total_workspaces']}",
            f"Active Leads: {s['active_leads']} | Closed: {s['closed_leads']} | Lost: {s['lost_leads']}",
            f"Pending Handoffs: {s['pending_handoffs']}",
            f"Pending Reviews: {s['pending_reviews']}",
            f"Token Health: {s['total_tokens']} total, {s['expired_tokens']} expired, {s['expiring_tokens']} expiring",
            "",
        ]

        # Alerts
        if alerts:
            report_lines.append(f"--- ALERTS ({len(alerts)}) ---")
            for a in alerts:
                report_lines.append(f"  [{a['severity'].upper()}] {a['message']}")
            report_lines.append("")

        # Workspace details
        if overview["workspaces"]:
            report_lines.append("--- WORKSPACE DETAILS ---")
            for ws in overview["workspaces"]:
                report_lines.append(
                    f"  {ws['name']} (client: {ws['client_name']})\n"
                    f"    Agents: {', '.join(ws['agents']) if ws['agents'] else 'none'}\n"
                    f"    Created: {ws['created_at'][:10]}"
                )
            report_lines.append("")

        # Strategic notes
        report_lines.extend([
            "--- STRATEGIC NOTES ---",
            f"Agency is managing {s['total_workspaces']} client workspace(s)",
            f"Lead pipeline: {s['active_leads']} active leads in funnel",
            f"Token attention needed: {s['expired_tokens'] + s['expiring_tokens']} platform(s)",
            "",
            "--- NEXT STEPS ---",
            "CEO reviews pending outputs and provides feedback",
            "Renew any expired tokens to unblock agent work",
            "Follow up on pending handoffs from SBA",
        ])
    else:
        report_lines = [
            f"=== TAGS AGENCY REPORT ===",
            f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "Agency data layer not available. Using basic counts.",
            "",
            "--- NEXT STEPS ---",
            "CEO to manually review workspace status",
        ]

        # Fallback to basic data
        try:
            from admin.workspace.manager import list_workspaces
            workspaces = list_workspaces()
            report_lines.insert(3, f"Workspaces: {len(workspaces)}")
        except Exception:
            pass

    result = "\\n".join(report_lines)

    # Best-effort owner notification from the CEO's AgentMail inbox. Does not
    # change the returned report; failures are logged, never raised.
    if notify_owner:
        try:
            from admin.tools import agentmail_notify
            ws_name = ""
            try:
                ws_name = health.get("workspace", {}).get("name", ws_id)
            except Exception:
                ws_name = ws_id
            agentmail_notify.notify_owner(
                "ceo",
                f"CEO report: {ws_name}",
                result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CEO report AgentMail notify failed (non-fatal): %s", exc)

    return result


async def _tool_cross_workspace_knowledge(args: dict) -> str:
    """Cross-workspace knowledge sharing — persists to JSON file."""
    import os
    from pathlib import Path

    action = args.get("action", "get_all")
    domain = args.get("domain", "")
    learning = args.get("learning", "")
    source_ws = args.get("source_workspace", "")

    # JSON file persistence
    persist_dir = Path(os.getenv("TAGS_DATA_DIR", "data")) / "ceo_knowledge"
    persist_dir.mkdir(parents=True, exist_ok=True)
    persist_file = persist_dir / "cross_workspace_knowledge.json"

    # Load existing
    store = []
    if persist_file.exists():
        try:
            store = json.loads(persist_file.read_text(encoding="utf-8"))
        except Exception:
            store = []

    def _save():
        persist_file.write_text(json.dumps(store, indent=2, default=str), encoding="utf-8")

    if action == "add_learning":
        if not learning:
            return "No learning provided."

        entry = {
            "id": f"kl_{len(store) + 1}",
            "domain": domain or "general",
            "learning": learning,
            "source_workspace": source_ws or "agency",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        store.append(entry)
        _save()

        return (
            f"Learning added to agency knowledge pool:\n"
            f"Domain: {entry['domain']}\n"
            f"Source: {entry['source_workspace']}\n"
            f"Learning: {learning}\n"
            f"Total entries: {len(store)}"
        )

    elif action == "get_by_domain":
        if not domain:
            return "Domain filter required for get_by_domain."

        filtered = [e for e in store if e["domain"] == domain]
        if not filtered:
            return f"No learnings found for domain: {domain}"

        lines = [f"Knowledge for domain '{domain}' ({len(filtered)} entries):"]
        for e in filtered[-10:]:
            lines.append(
                f"  - [{e['source_workspace']}] {e['learning'][:200]}"
            )
        return "\n".join(lines)

    else:  # get_all
        if not store:
            return "No cross-workspace knowledge collected yet."

        lines = [f"Agency Knowledge Pool ({len(store)} entries, persisted to JSON):"]
        for e in store[-20:]:
            lines.append(
                f"  - [{e['domain']}] [{e['source_workspace']}] {e['learning'][:150]}"
            )
        return "\n".join(lines)


async def finalize(state: CEOGraphState) -> dict:
    """Extract final output from accumulated state."""
    messages = state.get("messages", [])
    final_text = ""

    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            full = msg["content"]
            stripped = _strip_think_blocks(full)
            if stripped and len(stripped) > 20:
                final_text = stripped
                break
            if not final_text and full and len(full) > 20:
                final_text = full

    if not final_text or len(final_text) < 20:
        phases = state.get("thinking_phases", [])
        if phases:
            final_text = "CEO analysis complete:\n\n"
            for p in phases:
                final_text += f"**{p['phase'].title()}**: {p['content'][:300]}...\n\n"
        else:
            if state.get("error"):
                final_text = (
                    "Bhai, CEO ka thinking engine abhi reachable nahi hai. "
                    f"Error: {str(state['error'])[:200]}"
                )
            else:
                final_text = "CEO analysis complete. Aap kya next step chahte hain?"

    return {"final_output": final_text}


# ── Build Graph ──────────────────────────────────────────────────────────────


def build_ceo_graph(checkpointer: Any = None) -> StateGraph:
    """Build the compiled LangGraph state graph for the Agency CEO.

    Graph structure:
      call_llm -> route_from_llm -> run_tools -> call_llm (loop)
                                        \\-> finalize -> END

    ``checkpointer`` defaults to ``get_checkpointer("Agency", "ceo")`` so the
    CEO gets the same Supabase-backed cross-session persistence as the other
    workspace agents (falls back to in-memory MemorySaver when Supabase is not
    configured).
    """
    workflow = StateGraph(CEOGraphState)

    workflow.add_node("call_llm", call_llm)
    workflow.add_node("run_tools", run_tools)
    workflow.add_node("finalize", finalize)

    workflow.set_entry_point("call_llm")

    workflow.add_conditional_edges(
        "call_llm",
        route_from_llm,
        {
            "run_tools": "run_tools",
            "finalize": "finalize",
            "__end__": END,
        },
    )

    workflow.add_edge("run_tools", "call_llm")
    workflow.add_edge("finalize", END)

    checkpointer = checkpointer or get_checkpointer("Agency", "ceo")
    return workflow.compile(checkpointer=checkpointer)


# ── CEO Agent Class ──────────────────────────────────────────────────────────


class AgencyCEO:
    """Agency CEO agent — co-founder strategic partner with full orchestration."""

    def __init__(self) -> None:
        self.graph = build_ceo_graph()
        self._thread_id = "ceo_agency"

    async def chat(
        self,
        message: str,
        *,
        user_role: str = "the agency owner",
        conversation_history: list[dict[str, str]] | None = None,
        conversation_id: str | None = None,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        """Chat with the CEO agent.

        ``conversation_id`` continues an existing thread (persisted via the
        checkpointer); when omitted a stable default session is used.

        Returns (response, conversation_id, thinking_phases).
        """
        thread_id = conversation_id or self._thread_id
        workspace_context = _build_workspace_context()
        handoff_context = _build_handoff_context()
        review_context = _build_review_context()

        initial_state = {
            "messages": [{"role": "user", "content": message}],
            "user_role": user_role,
            "workspace_context": workspace_context,
            "handoff_context": handoff_context,
            "review_context": review_context,
            "thinking_phases": [],
            "tool_round": 0,
            "final_output": "",
            "error": None,
        }

        try:
            result = await self.graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": thread_id}},
            )
        except Exception:
            logger.exception("CEO LangGraph execution failed")
            return (
                "Bhai, CEO ka thinking engine abhi issue mein hai. "
                "Thodi der mein try karte hain.",
                "",
                [],
            )

        final_output = result.get("final_output", "")
        thinking_phases = result.get("thinking_phases", [])

        if not final_output:
            if result.get("error"):
                final_output = (
                    "Bhai, CEO ka LLM reachable nahi hai. "
                    f"Error: {str(result['error'])[:200]}"
                )
            else:
                final_output = "CEO analysis complete. Aap kya next step chahte hain?"

        return final_output, thread_id, thinking_phases

    # ── Direct API methods (for route handlers) ─────────────────────────────

    async def receive_handoff(
        self, handoff_id: str, action: str = "review_only", ceo_notes: str = ""
    ) -> str:
        """Receive and process an SBA handoff."""
        return await _tool_receive_handoff({
            "handoff_id": handoff_id,
            "action": action,
            "ceo_notes": ceo_notes,
        })

    async def parallel_blast(
        self, workspace_id: str, client_brief: str,
        campaign_name: str = "General", deadline: str = "TBD",
    ) -> str:
        """Brief all agents in a workspace simultaneously."""
        return await _tool_parallel_blast({
            "workspace_id": workspace_id,
            "client_brief": client_brief,
            "campaign_name": campaign_name,
            "deadline": deadline,
        })

    async def run_multiagent(
        self,
        brief: str,
        agent_ids: list[str] | None = None,
        task_per_agent: dict[str, str] | None = None,
        scope: dict | None = None,
    ) -> dict:
        """Fan a brief out to multiple agents (built-in + custom) at once."""
        return await run_multiagent(
            brief=brief,
            agent_ids=agent_ids,
            task_per_agent=task_per_agent,
            scope=scope,
        )

    async def review_output(
        self, workspace_id: str, agent_type: str,
        verdict: str, feedback: str = "", output_id: str = "",
    ) -> str:
        """Review agent output."""
        return await _tool_review_output({
            "workspace_id": workspace_id,
            "agent_type": agent_type,
            "output_id": output_id,
            "verdict": verdict,
            "feedback": feedback,
        })

    async def route_error(
        self, workspace_id: str, error_type: str,
        severity: str, description: str, route_to: str = "",
    ) -> str:
        """Route error recovery."""
        return await _tool_route_error({
            "workspace_id": workspace_id,
            "error_type": error_type,
            "severity": severity,
            "description": description,
            "route_to": route_to,
        })

    async def generate_report(
        self, report_type: str, workspace_id: str | None = None,
        period_start: str = "", period_end: str = "",
    ) -> str:
        """Generate agency report."""
        return await _tool_generate_report({
            "report_type": report_type,
            "workspace_id": workspace_id,
            "period_start": period_start,
            "period_end": period_end,
        })

    async def cross_workspace_knowledge(
        self, action: str = "get_all", domain: str = "",
        learning: str = "", source_workspace: str = "",
    ) -> str:
        """Query or update cross-workspace knowledge."""
        return await _tool_cross_workspace_knowledge({
            "action": action,
            "domain": domain,
            "learning": learning,
            "source_workspace": source_workspace,
        })
