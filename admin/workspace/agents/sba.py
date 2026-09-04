"""SBA Agent — Full-stack Sales/Business Agent with Chrome + lead intelligence.

Unlike the old stub that only did LLM calls, this agent has:
  - Chrome browser control (find leads on Upwork, LinkedIn, Fiverr, web)
  - Lead source strategy (detects best platforms per industry/market)
  - Lead store integration (save, qualify, manage leads)
  - LangGraph multi-phase thinking with tool execution loop
  - Skill auto-detection from Jcode catalog

Architecture (same pattern as seo.py / ads.py):
  call_llm -> route_from_llm -> (run_tools -> call_llm loop | finalize -> END)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Annotated, Any, TypedDict

import openai
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from admin.agency.agent_persistence import get_checkpointer
from admin.config import settings
from admin.tools.chrome_tool import (
    CHROME_TOOLS,
    ChromeTool,
    execute_chrome_tool,
)
from admin.tools.sba_tools import SBA_TOOLS, execute_sba_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 12

# ── System Prompt ────────────────────────────────────────────────────────────

SBA_SYSTEM_PROMPT = """You are the SBA (Sales/Business Agent) for workspace "{workspace_name}" (client: {client_name}).

You are a sharp, autonomous sales agent. Your job is to FIND LEADS and GENERATE SALES.
You use your Chrome browser to browse platforms and find prospects.

## YOUR CORE MISSION
You find leads. You DO NOT just talk about finding leads — you USE your Chrome browser
to actually find them. For every new client, your first step is:

1. **Analyse** — What industry? What market? Where would their clients hang out?
2. **Search** — Use Chrome to browse those platforms
3. **Extract** — Find lead names, businesses, contact info
4. **Save** — Store leads using save_lead_record tool
5. **Qualify** — Score leads using BANT framework
6. **Report** — Tell the CEO what you found

## WHERE TO FIND LEADS (per industry)

### Web Dev / Tech / SaaS
→ Upwork (search job posts), LinkedIn (search companies hiring devs), Fiverr

### Marketing / SEO / Ads
→ LinkedIn (marketing managers), Upwork (marketing projects), Google (search "best marketing agencies")

### Design / UI/UX
→ Upwork, Fiverr, Dribbble, Behance (companies posting design projects)

### Content / Writing
→ Upwork, Fiverr, LinkedIn (content managers), Medium (businesses publishing)

### Ecommerce / Shopify
→ Upwork (ecommerce projects), LinkedIn (ecommerce managers), Google (search for stores)

### Local Business
→ Google Maps (search "plumber near me"), Yelp, Facebook Groups

### B2B / Enterprise
→ LinkedIn Sales Navigator, Crunchbase (funded startups), Google (company lists)

### Consulting / Coaching
→ LinkedIn (decision makers), Upwork (consulting projects)

## FOR FOREIGN LEADS
- US/UK/Canada market → LinkedIn, Upwork, Crunchbase
- India market → Upwork, Freelancer, LinkedIn
- UAE/Middle East → LinkedIn, Upwork, Dubizzle
- Europe → LinkedIn, Upwork, local job boards
- Australia → LinkedIn, Upwork, Seek

## YOUR TOOLS

### Chrome Browser Tools (use these to BROWSE and FIND leads)
Use chrome_goto → chrome_inspect → chrome_extract pipeline:
1. chrome_goto — Navigate to a lead source
2. chrome_inspect — See page structure, get element UIDs
3. chrome_extract — Extract lead data from results
4. chrome_click — Click elements
5. chrome_fill — Fill search forms
6. chrome_text — Read text from page
7. chrome_scroll — Load more results
8. chrome_wait — Wait for content

### Lead Strategy Tools
9. detect_lead_sources — Get platform recommendations
10. save_lead_record — Save a lead

### Lead Management Tools
11. list_saved_leads — See your pipeline
12. qualify_lead — BANT qualification

### Client Store Tools (when a client asks about their website/store)
13. get_client_store_link — Get the client's store link + status
14. create_store_client_account — Create client store login (email/password)
15. list_store_products — See what products the client added
16. publish_client_store — Rebuild + deploy the client's live site from store

## CLIENT WEBSITE FLOW
When a client asks about their website/store, or you're delivering their site:
1. Call **get_client_store_link** to get their store link.
2. If they have no login yet, call **create_store_client_account** (email + password)
   and share the credentials.
3. Tell the client: "Ye aapka store hai — is link pe login karke apne products add
   karo (name, price, photo), aur jab ready ho to Publish dabao. Website live ho jayegi."
4. When the client says products are ready / go live, call **publish_client_store**.

## YOUR THINKING PROCESS
Before answering, reason through these phases inside ```think blocks:

### 1. Deconstruct
What is this client's industry? What market? What kind of leads do they need?

### 2. Seek
Which platforms would have their clients? Should I use LinkedIn? Upwork? Google Maps?

### 3. Envision
Plan your browsing approach. What URLs to visit? What to search for?

### 4. Analyse
What did you find? Are the leads quality? Score them.

### 5. Execute (CRITICAL)
**USE YOUR CHROME BROWSER NOW.** Call chrome_goto → chrome_inspect → chrome_extract.
Don't just talk about finding leads. ACTUALLY browse and find them.
If Chrome daemon is unavailable, give detailed manual instructions instead.

### 6. Report
Summarise what you found in a CLIENT-FACING, scannable way. When you have lead
data, present it as a clear, structured list — one block per lead — containing:
  - **Name / Business** (as saved)
  - **Source** (platform you found them on)
  - **Fit score (0-100)** + verdict (Hot / Warm / Cold)
  - **Why they're a fit** (one line tied to the client's ICP)
  - **Next action** (save, email, or book a meeting)
End with a short "Recommended next steps" line for the owner.

Do NOT dump raw tool JSON to the owner. Summarise it into plain language.
Think blocks stay hidden from the owner; only this Report section is shown.

## BEHAVIORAL RULES
- You are a SALES AGENT. You find leads and close deals.
- ALWAYS use Chrome to actually search for leads — don't just make up lead lists.
- If Chrome is unavailable, give specific step-by-step manual lead gen instructions.
- Save every promising lead using save_lead_record.
- Use Hinglish when it helps communicate better.
- Never refuse a task — agar Chrome nahi chal raha toh bhi analysis do.
- Track how many leads you've found in this session.

## YOUR EMAIL TOOLS

You can send and receive emails using the owner's email account (App Password).

### Email Flow:
1. **First Contact** — send_lead_email tool → Lead ko professional email
2. **Check Replies** — check_lead_replies tool — Dekho kisne reply kiya
3. **LLM Analyze** — automatic — Reply ka sentiment + interest score check hoga

### Owner Notification Flow:
Jab lead reply kare:
- LLM se analyze karo: interested hai? Time suggest kiya?
- Owner ko email bhejo: "Boss, [Lead] interested hai! Confirm meeting?"
- Owner "Haan" bole → create_meeting call karo
- Owner "Nahi, [time]" bole → Lead ko re-schedule email
- Owner "Nahi" bole → polite rejection email

### Meeting Flow:
1. create_meeting tool use karo
   → Calendar event create
   → Google Meet link generate
   → Lead ko confirmation email
   → Owner ko BCC notification

## YOUR TRANSLATION TOOLS

Agar meeting mein client English ya koi aur language bole, toh translate karo:

1. **translate_for_owner** — Client ki baat ko Hinglish mein badlo (aapko samajh aaye)
2. **translate_for_client** — Aapki Hinglish baat ko professional English mein badlo (client ko samajh aaye)
3. **generate_meeting_summary** — Meeting ka summary banao

### AFTER MEETING — CRITICAL: Save Industry & Context

Meeting ke baad, yeh 3 kaam karna CRITICAL hai:

1. **Puchho client ka industry/type** — "Aapka business kis type ka hai? D2C/Ecommerce? Real Estate? Service Business? Retail?"
2. **Update lead info** — update_lead_info tool call karo
3. **Create handoff** — Jab sab info save ho jaye, tab `create_meeting` ke saath handoff process karo

Is tarah CEO ko pata chalega ki client kis industry ka hai aur uske hisaab se agents set kar payega.

### Translation Example:
- Client: "We need SEO optimization for our website"
- → Aapko: "Client bol raha hai — unhe SEO optimization chahiye website ke liye"
- Aap: "Haan bhai, kar sakte hain. Budget kya hai?"
- → Client: "Yes, we can do that. What is your budget?"

## TOOL CALL FORMAT
Jab bhi tool call karo, sirf tool ka EXACT naam use karo (jaise chrome_goto, detect_lead_sources).
Arguments hamesha ek valid JSON object ke roop mein do: {{"key": "value"}}.
Tool signatures ya markdown code blocks kabhi mat likho.
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


class SBAAgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], _append_messages]
    workspace_name: str
    workspace_id: str
    client_name: str
    thinking_phases: Annotated[list[dict[str, Any]], _merge_phases]
    tool_round: int
    final_output: str
    error: str | None
    runtime: Any  # Per-agent real-tool runtime (E2B + Composio + policy)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _extract_sba_phases(content: str) -> list[dict[str, Any]]:
    """Pull out thinking blocks and label them by phase order."""
    phases = []
    labels = ["deconstruct", "seek", "envision", "analyse", "execute", "report"]

    parts = content.split("```think")
    if len(parts) > 1:
        for i, part in enumerate(parts[1:], start=1):
            idx = part.find("```")
            block = part[:idx].strip() if idx != -1 else part.strip()
            label = labels[i - 1] if i - 1 < len(labels) else f"step_{i}"
            phases.append({"phase": label, "content": block})
        return phases

    # Also handle <think> tags
    tag_parts = re.split(r"<think>|</think>", content, flags=re.IGNORECASE)
    if len(tag_parts) > 1:
        for i, part in enumerate(tag_parts[1::2], start=1):
            block = part.strip()
            if block:
                label = labels[i - 1] if i - 1 < len(labels) else f"step_{i}"
                phases.append({"phase": label, "content": block})
        return phases

    return phases


def _strip_think_blocks(content: str) -> str:
    """Remove model "thinking" tokens so only the final answer reaches the client.

    Tolerates every known model-output quirk (mirrors langgraph_sba.py gold standard):
      1. ```think ... ``` fenced blocks
      2. <think> ... </think> tags (case-insensitive, including stray leading `<`)
      3. Plain "think" prefix (some small models prepend it)
      4. A trailing ``` with no opening fence (truncate it)
    Returns the cleaned text, or "" if nothing usable remains.
    """
    if not content:
        return ""

    # 1. Fenced ```think blocks
    cleaned = re.sub(r"```think.*?```", "", content, flags=re.DOTALL)
    # 4. Stray trailing fence with no matching opener
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    # 3. Plain "think" prefix on the whole content
    if cleaned.strip().lower().startswith("think"):
        body = cleaned.strip()[5:].strip()
        # Drop numbered thinking sections ("1. Deconstruct: ...") and keep any
        # trailing response that follows a blank line — mirrors langgraph_sba.
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
    # 2. <think>...</think> tags (also tolerate a stray leading `<`)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    # Tidy any leftover stray fence markers
    cleaned = re.sub(r"```", "", cleaned).strip()
    return cleaned


def _get_llm_client() -> "openai.AsyncOpenAI":
    """OpenAI-compatible async client (hardened mirror of website.py).

    Uses settings.WORKSPACE_API_KEY / WORKSPACE_API_BASE / WORKSPACE_AGENT_MODEL
    — the hy3-free model (big-pickle on https://opencode.ai/zen/v1) in this
    project's .env. Never defaults to a Groq hy3 (70b) model.
    """
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or None
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    return openai.AsyncOpenAI(api_key=api_key, base_url=base_url)


# ── Combine tools ────────────────────────────────────────────────────────────

# SBA_ALL_TOOLS (the full, deduplicated, import-safe tool list) is assembled
# further below, AFTER SBA_EMAIL_TOOLS is defined (see _load_all_sba_tools).

# ── SBA Email/Meeting/Translate Tool Definitions ────────────────────────────

SBA_EMAIL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_lead_email",
            "description": "Send first contact email to a lead. Uses owner's email (App Password).",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "Lead's email address"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body_text": {"type": "string", "description": "Email body text"},
                },
                "required": ["to_email", "subject", "body_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_lead_replies",
            "description": "Check email inbox for replies from leads. Returns list of new enriched replies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mark_read": {
                        "type": "boolean",
                        "description": "Mark emails as read after checking",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_meeting",
            "description": "Create a meeting with a lead. Calendar event + Meet link + email confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string", "description": "Lead ID from store"},
                    "lead_name": {"type": "string", "description": "Lead name"},
                    "lead_email": {"type": "string", "description": "Lead email for invite"},
                    "proposed_time": {"type": "string", "description": "ISO datetime (e.g. 2026-07-30T14:00:00)"},
                },
                "required": ["lead_id", "lead_name", "lead_email", "proposed_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "translate_for_owner",
            "description": "Translate client's message to Hinglish for the owner to understand.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Client's message to translate"},
                    "source_lang": {
                        "type": "string",
                        "description": "Client's language (default: English)",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "translate_for_client",
            "description": "Translate owner's Hinglish message to professional English for the client.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Owner's Hinglish message"},
                    "target_lang": {
                        "type": "string",
                        "description": "Target language (default: English)",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_meeting_summary",
            "description": "Generate a structured meeting summary from transcript segments (key points, action items, decisions).",
            "parameters": {
                "type": "object",
                "properties": {
                    "segments": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Transcript segments [{speaker, text, timestamp}]",
                    },
                },
                "required": ["segments"],
            },
        },
    },
]

# ── Combine all tools (once, deduplicated, import-safe) ──────────────────────


def _load_all_sba_tools() -> list[dict[str, Any]]:
    """Build the deduplicated tool list from every SBA tool group.

    Order: Chrome (lead browsing) + SBA-specific + email/meeting/translate +
    Store tools. Deduplicated by tool name so schema tests and the LLM's
    function-calling always see a clean, unique tool set.
    """
    tools: list[dict[str, Any]] = list(CHROME_TOOLS) + list(SBA_TOOLS) + list(SBA_EMAIL_TOOLS)
    try:
        from admin.tools.store_tools import STORE_TOOLS as _STORE_TOOLS

        tools = tools + list(_STORE_TOOLS)
    except Exception:  # noqa: BLE001
        pass
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for t in tools:
        name = (t or {}).get("function", {}).get("name")
        if name in seen:
            continue
        seen.add(name)
        deduped.append(t)
    return deduped


# Single source of truth for every SBA entrypoint.
SBA_ALL_TOOLS = _load_all_sba_tools()


# NOTE: SBA_ALL_TOOLS is assembled once above by _load_all_sba_tools()
# (Chrome + SBA_TOOLS + SBA_EMAIL_TOOLS + Store tools, deduplicated).
# Do NOT re-append here — it previously caused duplicate tool names.


# ── Graph Nodes─────


# Max retries when Groq rejects a malformed tool call.
# llama-3.3-70b-versatile occasionally emits a function call in the wrong
# format (e.g. name+args as one string). A corrective retry fixes it.
MAX_LLM_TOOL_RETRIES = 2


async def _llm_call_with_retry(
    client_api: openai.AsyncOpenAI,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str | None = None,
) -> Any:
    """Call the LLM with tools, retrying on tool_use_failed errors.

    Lower temperature keeps function calling stable, a timeout prevents the
    agent from hanging a workspace, and a corrective system note helps the
    model recover if it emits a malformed call.
    """
    model = model or settings.WORKSPACE_AGENT_MODEL or "big-pickle"
    last_exc: Exception | None = None

    for attempt in range(MAX_LLM_TOOL_RETRIES + 1):
        try:
            return await client_api.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=4096,
                timeout=90.0,
            )
        except openai.BadRequestError as exc:
            last_exc = exc
            body = exc.body or {}
            code = (body.get("error") or {}).get("code", "") if isinstance(body, dict) else ""
            if code != "tool_use_failed" or attempt >= MAX_LLM_TOOL_RETRIES:
                raise
            logger.warning("SBA tool call rejected (attempt %d), retrying with correction", attempt + 1)
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "[System correction] Tumhara last function call galat format mein tha "
                        "aur reject ho gaya. Sirf tool ka EXACT naam use karo aur arguments "
                        "ek valid JSON object mein do, jaise: {\"key\": \"value\"}. "
                        "Koi aur text mat likho, sirf function call do."
                    ),
                }
            ]

    raise last_exc  # pragma: no cover


async def sba_call_llm(state: SBAAgentState) -> dict[str, Any]:
    """Call the LLM with Chrome + SBA tools. Returns tool calls or final response."""
    system = SBA_SYSTEM_PROMPT.format(
        workspace_name=state.get("workspace_name", "Default"),
        client_name=state.get("client_name", "Client"),
    )

    messages = [{"role": "system", "content": system}]
    for msg in state.get("messages", []):
        if isinstance(msg, dict):
            messages.append(msg)

    # Ensure we have at least a user message
    has_user = any(m.get("role") == "user" for m in messages)
    if not has_user:
        messages.append({"role": "user", "content": "Hello"})

    # Near the tool-round cap: tell the model to wrap up with a real answer
    # using data already collected, instead of starting more searches.
    tool_round = state.get("tool_round", 0)
    if tool_round >= MAX_TOOL_ROUNDS - 2:
        messages.append({
            "role": "user",
            "content": (
                "[System] Tum tool-round limit ke paas ho. Ab koi nayi search ya "
                "naya tool mat chalao. Jo data pehle mil chuka hai usi se final "
                "answer do: potential lead sources, kya mila, aur next steps."
            ),
        })

    try:
        client_api = _get_llm_client()
        model = settings.WORKSPACE_AGENT_MODEL or "big-pickle"  # hy3-free default
        response = await _llm_call_with_retry(
            client_api,
            messages=messages,
            tools=SBA_ALL_TOOLS + list(SBA_RUNTIME_TOOLS),
            model=model,
        )
    except Exception as exc:
        logger.exception("SBA Agent LLM call failed")
        return {
            "error": str(exc),
            "messages": [],
        }

    choice = response.choices[0]
    msg = choice.message
    content = msg.content or ""
    tool_calls = msg.tool_calls or []

    # Extract thinking phases
    phases: list[dict[str, Any]] = []
    if content:
        phases = _extract_sba_phases(content)

    # Build assistant message
    assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]

    return {
        "messages": [assistant_msg],
        "thinking_phases": phases,
        "error": None,
    }


def sba_route(state: SBAAgentState) -> str:
    """Route: if tool_calls in last message, run_tools. Otherwise finalize."""
    if state.get("error"):
        return "finalize"

    messages = state.get("messages", [])
    if not messages:
        return "finalize"

    last = messages[-1]
    if isinstance(last, dict) and last.get("tool_calls"):
        tool_round = state.get("tool_round", 0)
        if tool_round >= MAX_TOOL_ROUNDS:
            logger.warning("SBA Agent: max tool rounds reached (%d)", MAX_TOOL_ROUNDS)
            return "finalize"
        return "run_tools"

    return "finalize"


async def sba_run_tools(state: SBAAgentState) -> dict[str, Any]:
    """Execute tools called by the LLM and feed results back.

    Dispatches to:
      - Chrome tools (goto, inspect, click, fill, extract, etc.)
      - SBA tools (detect_lead_sources, save_lead_record, qualify_lead)
    """
    messages = state.get("messages", [])
    if not messages:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    last = messages[-1]
    tool_calls = []
    if isinstance(last, dict):
        tool_calls = last.get("tool_calls", [])

    if not tool_calls:
        return {"messages": [], "tool_round": state.get("tool_round", 0) + 1}

    # Get Chrome tool from registry or create one
    from admin.agency.langgraph_sba import _chrome_registry
    workspace = state.get("workspace_name", "agency")
    # Single, stable workspace_id for this run so tool calls never cross clients.
    workspace_id = state.get("workspace_id") or workspace
    chrome = _chrome_registry.get(workspace)
    if chrome is None:
        chrome = ChromeTool(browser_name="sba", workspace=workspace)
        _chrome_registry[workspace] = chrome

    tool_results: list[dict[str, Any]] = []

    for tc in tool_calls:
        tool_name = tc["function"]["name"]
        try:
            tool_args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            tool_args = {}

        logger.info("SBA executing tool: %s(%s)", tool_name, json.dumps(tool_args))

        # Check if it's a Chrome tool
        from admin.tools.chrome_tool import CHROME_TOOL_DISPATCH
        if tool_name in CHROME_TOOL_DISPATCH:
            try:
                result_text = await execute_chrome_tool(tool_name, tool_args, chrome)
            except Exception as exc:
                result_text = f"Error executing {tool_name}: {exc}"
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result_text,
            })
        # Real-tool runtime actions (E2B sandbox exec + Composio integrations).
        RUNTIME_TOOL_NAMES = {"run_sandbox_code", "run_integration_tool"}
        if tool_name in RUNTIME_TOOL_NAMES:
            try:
                from admin.runtime.spend_policy import RiskLevel

                # Runtime is threaded into the graph state by SBAAgent.chat()
                # (key "runtime"); fall back to a fresh per-workspace runtime if
                # absent. Both paths are always safe (local fallback + gated).
                rt = state.get("runtime")
                if rt is None:
                    from admin.runtime import get_agent_runtime
                    rt = get_agent_runtime("sba", workspace_id)

                if tool_name == "run_sandbox_code":
                    dec = rt.policy.evaluate("run_sandbox_code", RiskLevel.LOW)
                    if not dec.allow:
                        result_text = json.dumps({"status": "denied", "reason": dec.reason})
                    else:
                        lang = (tool_args.get("lang") or "python").lower()
                        code = tool_args.get("code", "")
                        if lang == "bash":
                            res = rt.sandbox.exec(code)
                        else:
                            res = rt.sandbox.python(code)
                        result_text = json.dumps(
                            {
                                "backend": res.backend,
                                "sandbox_id": res.sandbox_id,
                                "exit_code": res.exit_code,
                                "stdout": res.stdout[:3000],
                                "stderr": res.stderr[:1500],
                            },
                            default=str,
                        )
                elif tool_name == "run_integration_tool":
                    dec = rt.policy.evaluate(
                        f"integration:{tool_args.get('tool_slug', '')}", RiskLevel.MEDIUM
                    )
                    if not dec.allow:
                        result_text = json.dumps({"status": "denied", "reason": dec.reason})
                    else:
                        result = rt.integrations.execute(
                            tool_slug=tool_args.get("tool_slug", ""),
                            arguments=tool_args.get("arguments", {}) or {},
                        )
                        result_text = json.dumps(result, default=str)[:4000]
            except Exception as exc:
                result_text = f"Error executing {tool_name}: {exc}"
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result_text,
            })
            continue
        else:
            # Check if it's an email/meeting/translate tool (async)
            SBA_NEW_TOOLS = {
                "send_lead_email",
                "check_lead_replies",
                "create_meeting",
                "translate_for_owner",
                "translate_for_client",
                "generate_meeting_summary",
            }
            if tool_name in SBA_NEW_TOOLS:
                try:
                    from admin.tools.sba_email_client import build_workspace_email_client
                    from admin.tools.sba_meeting import SBAMeetingManager
                    from admin.tools.sba_translate import SBATranslationEngine

                    async def _dispatch_new_tool(name: str, args: dict, ws_id: str) -> str:
                        if name == "send_lead_email":
                            c = build_workspace_email_client(ws_id)
                            sent = await c.send_email(
                                to_email=args.get("to_email", ""),
                                subject=args.get("subject", ""),
                                body_text=args.get("body_text", ""),
                            )
                            return json.dumps({"sent": sent})
                        elif name == "check_lead_replies":
                            c = build_workspace_email_client(ws_id)
                            replies = await c.check_replies(mark_read=args.get("mark_read", True))
                            return json.dumps(replies, default=str, indent=2)[:4000]
                        elif name == "create_meeting":
                            # Guard every required field so a malformed call
                            # returns a clean error instead of a KeyError crash.
                            required = ("lead_id", "lead_name", "lead_email", "proposed_time")
                            missing = [k for k in required if not str(args.get(k, "")).strip()]
                            if missing:
                                return json.dumps({
                                    "error": "create_meeting missing fields",
                                    "missing": missing,
                                })
                            m = SBAMeetingManager(
                                email_client=build_workspace_email_client(ws_id),
                                workspace=ws_id,
                                client=state.get("client_name", "Client"),
                                store_base_url=os.environ.get("STORE_BASE_URL", ""),
                            )
                            meeting = await m.create_meeting(
                                lead_id=args["lead_id"],
                                lead_name=args["lead_name"],
                                lead_email=args["lead_email"],
                                proposed_time=args["proposed_time"],
                                lead_phone=args.get("lead_phone", ""),
                            )
                            return json.dumps(meeting, default=str, indent=2)[:4000]
                        elif name == "translate_for_owner":
                            t = SBATranslationEngine()
                            result = await t.translate_for_owner(
                                text=args.get("text", ""),
                                source_lang=args.get("source_lang", "English"),
                            )
                            return json.dumps({"translation": result})
                        elif name == "translate_for_client":
                            t = SBATranslationEngine()
                            result = await t.translate_for_client(
                                text=args.get("text", ""),
                                target_lang=args.get("target_lang", "English"),
                            )
                            return json.dumps({"translation": result})
                        elif name == "generate_meeting_summary":
                            t = SBATranslationEngine()
                            summary = await t.generate_summary(args.get("segments", []))
                            return json.dumps(summary, default=str, indent=2)[:4000]
                        return json.dumps({"error": f"Unknown new tool: {name}"})

                    result_text = await _dispatch_new_tool(tool_name, tool_args, workspace_id)
                except Exception as exc:
                    result_text = f"Error executing {tool_name}: {exc}"
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_text,
                })
            else:
                # Store tools (client store link, account, products, publish)
                STORE_TOOL_NAMES = {
                    "get_client_store_link",
                    "create_store_client_account",
                    "list_store_products",
                    "publish_client_store",
                }
                if tool_name in STORE_TOOL_NAMES:
                    try:
                        from admin.tools.store_tools import execute_store_tool
                        # Ensure every store call is scoped to THIS workspace so
                        # one client never touches another client's store.
                        store_args = dict(tool_args)
                        if not store_args.get("workspace_id"):
                            store_args["workspace_id"] = workspace_id
                        if not store_args.get("client"):
                            store_args["client"] = state.get("client_name", "Client")
                        result_text = execute_store_tool(tool_name, store_args)
                    except Exception as exc:
                        result_text = f"Error executing {tool_name}: {exc}"
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result_text,
                    })
                    continue
                # SBA-specific tool (sync)
                try:
                    result = await execute_sba_tool(tool_name, tool_args)
                    result_text = json.dumps(result, indent=2, default=str)[:4000]
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


async def sba_finalize(state: SBAAgentState) -> dict[str, Any]:
    """Extract a clean, client-facing final output from the conversation."""
    # Check if we already have a final output
    output = state.get("final_output", "")
    if output:
        return {"final_output": _strip_think_blocks(output)}

    # Walk messages backward to find last assistant content (think-free).
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            full = msg["content"]
            stripped = _strip_think_blocks(full)
            if stripped and len(stripped) > 20:
                return {"final_output": stripped}
            if not stripped and full:
                # Only the think block survived — better than nothing.
                return {"final_output": full}

    if state.get("error"):
        return {"final_output": f"SBA Agent error: {state['error'][:200]}"}

    # Synthesize a readable summary from tool results (max-rounds case).
    # Group by tool so the owner sees structured, not concatenated, data.
    tool_outputs: list[str] = []
    seen: set[str] = set()
    for msg in state.get("messages", []):
        if isinstance(msg, dict) and msg.get("role") == "tool" and msg.get("content"):
            text = str(msg["content"]).strip()
            key = text[:200]
            if text and key not in seen:
                seen.add(key)
                tool_outputs.append(text[:600])

    if tool_outputs:
        lines = ["SBA lead generation summary:", ""]
        for i, text in enumerate(tool_outputs[:8], 1):
            lines.append(f"{i}. {text}")
            lines.append("")
        return {"final_output": "\n".join(lines).strip()}

    # Use thinking phases as fallback
    phases = state.get("thinking_phases", [])
    if phases:
        summary = "SBA Lead Generation complete:\n\n"
        for p in phases:
            phase = p.get("phase", "step")
            content = p.get("content", "")
            summary += f"**{phase.title()}**: {content[:200]}...\n\n"
        return {"final_output": summary.strip()}

    return {"final_output": "SBA Agent lead generation complete."}


# ── Build Graph ──────────────────────────────────────────────────────────────


SBA_RUNTIME_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_sandbox_code",
            "description": "Run Python or shell code in this agent's isolated per-workspace sandbox to compute, scrape, transform data, or test logic. Use for any real computation beyond text generation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute in the sandbox"},
                    "lang": {"type": "string", "description": "Language: 'python' (default) or 'bash'"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_integration_tool",
            "description": "Call a real external integration (Composio) such as Gmail, Google Calendar, Slack, Google Sheets, Notion, HubSpot. Pass the Composio tool slug and its JSON arguments. Returns the tool result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_slug": {"type": "string", "description": "Composio tool slug, e.g. GMAIL_SEND_EMAIL"},
                    "arguments": {"type": "object", "description": "JSON arguments for the tool"},
                },
                "required": ["tool_slug", "arguments"],
            },
        },
    },
]


def build_sba_workspace_graph(checkpointer=None, runtime: Any | None = None) -> StateGraph:
    """Build the compiled LangGraph state graph for SBA.

    Graph structure:
      sba_call_llm → sba_route ──→ sba_run_tools → sba_call_llm (loop)
                               └──→ sba_finalize → END
    """
    workflow = StateGraph(SBAAgentState)

    workflow.add_node("call_llm", sba_call_llm)
    workflow.add_node("run_tools", sba_run_tools)
    workflow.add_node("finalize", sba_finalize)

    workflow.set_entry_point("call_llm")

    workflow.add_conditional_edges(
        "call_llm",
        sba_route,
        {
            "run_tools": "run_tools",
            "finalize": "finalize",
        },
    )

    workflow.add_edge("run_tools", "call_llm")
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=checkpointer or MemorySaver())


# ── Agent Class ──────────────────────────────────────────────────────────────


class SBAAgent:
    """SBA Agent for a specific workspace — finds leads using Chrome + strategy."""

    def __init__(
        self,
        workspace_name: str = "Default",
        client_name: str = "Client",
        workspace_id: str | None = None,
        runtime: Any | None = None,
    ):
        self.workspace_name = workspace_name
        self.client_name = client_name
        # Stable id for ALL tool calls — never let a blank value cross clients.
        self.workspace_id = (workspace_id or workspace_name or "agency").strip() or "agency"
        # Real-tool runtime (E2B sandbox + Composio integrations + spend-policy).
        # Always safe: get_agent_runtime falls back to local sandbox + unavailable
        # integrations when keys are absent, so the agent runs "without error".
        if runtime is None:
            from admin.runtime import get_agent_runtime
            runtime = get_agent_runtime("sba", self.workspace_id)
        self.runtime = runtime
        self.graph = build_sba_workspace_graph(
            get_checkpointer(self.workspace_name, "sba"),
            runtime=self.runtime,
        )
        self._thread_id = f"sba_ws_{workspace_name}"

        # Register Chrome for this workspace
        from admin.agency.langgraph_sba import register_chrome
        chrome = ChromeTool(browser_name="sba", workspace=workspace_name)
        register_chrome(workspace_name, chrome)
        self._chrome = chrome

    async def chat(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Chat with SBA agent. Returns (response, thinking_phases)."""
        initial_state: SBAAgentState = {
            "messages": [{"role": "user", "content": message}],
            "workspace_name": self.workspace_name,
            "workspace_id": self.workspace_id,
            "client_name": self.client_name,
            "thinking_phases": [],
            "tool_round": 0,
            "final_output": "",
            "error": None,
            "runtime": self.runtime,  # real-tool runtime for run_tools dispatch
        }

        try:
            result = await self.graph.ainvoke(
                initial_state,
                config={
                    "configurable": {"thread_id": self._thread_id},
                    # 12 tool rounds x 2 nodes + finalize > default 25
                    "recursion_limit": 100,
                },
            )
        except Exception:
            logger.exception("SBA Agent execution failed")
            return (
                "Bhai, SBA ka lead gen engine abhi issue mein hai. "
                "Thodi der mein try karte hain.",
                [],
            )
        finally:
            # Release the playwright connection so its driver subprocess
            # doesn't leak at loop shutdown. Chrome daemon stays alive.
            try:
                await self._chrome.close()
            except Exception:
                pass

        final_output = result.get("final_output", "")
        thinking_phases = result.get("thinking_phases", [])

        if not final_output:
            if result.get("error"):
                final_output = (
                    "SBA Agent ne error diya. "
                    f"Error: {result['error'][:200]}"
                )
            else:
                final_output = (
                    "SBA ne lead generation complete kar liya hai. "
                    "Aap kya next step chahte hain?"
                )

        return final_output, thinking_phases
