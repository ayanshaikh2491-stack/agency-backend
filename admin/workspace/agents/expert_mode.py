"""Expert-mode wrapper: routed agents work like real domain humans.

Two additions around every route_to_agent call (single chokepoint, all
built-in agents covered without touching any agent file):

1. BRIEF  - inject client facts + this agent's own past memories +
   recent outputs into the task message, so the agent starts already
   knowing the account (a human specialist reviews their notes first).
2. REVIEW - one senior-reviewer pass over the draft: did it actually do
   THIS task (vs generic advice), is it client-specific, is it
   actionable? Returns the improved deliverable plus honest WORK NOTES.
   Fails soft: any error returns the original draft unchanged.

Kill switch: AGENCY_EXPERT_MODE=0 disables both (raw behaviour).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# One-line human persona per built-in domain.
PERSONAS: dict[str, str] = {
    "seo": "senior SEO & AEO strategist",
    "ads": "performance-ads lead (Google/Meta)",
    "content": "editorial content head",
    "website": "web strategist / dev lead",
    "social": "social media manager",
    "analytics": "analytics & attribution analyst",
    "analyzing": "research & insights analyst",
    "memory": "agency knowledge manager",
    "sba": "senior business-development associate",
}
_DEFAULT_PERSONA = "domain specialist"


def _persona(agent_type: str) -> str:
    return PERSONAS.get(agent_type, _DEFAULT_PERSONA)


async def build_brief(workspace_id: str, agent_type: str) -> str:
    """Account notes for this (workspace, agent): client facts, own past
    memories, recent outputs. Empty string when nothing is known."""
    parts: list[str] = []

    from admin.workspace.manager import get_workspace

    ws = get_workspace(workspace_id)
    if not ws:
        return ""
    ctx = ws.client_context or {}
    facts = {
        "client": ws.client_name,
        "workspace": ws.name,
        "industry": ctx.get("industry", ""),
        "audience": ctx.get("target_audience", ""),
        "goals": ctx.get("goals", ""),
    }
    fact_line = ", ".join(f"{k}={v}" for k, v in facts.items() if v)
    if fact_line:
        parts.append(f"[CLIENT FACTS] {fact_line}")

    try:
        from admin.file_store import load_all

        mems = [
            r for r in load_all("agent_memory").values()
            if r.get("workspace") == workspace_id and r.get("agent") == agent_type
        ]
        if mems:
            lines = [
                f"- {r.get('memory_key')}: {str(r.get('value'))[:200]}"
                for r in sorted(mems, key=lambda r: str(r.get("updated_at") or ""))[-5:]
            ]
            parts.append("[YOUR PAST MEMORY]\n" + "\n".join(lines))

        outs = [
            r for r in load_all("agent_outputs").values()
            if r.get("workspace_id") == workspace_id
            and r.get("agent_type") == agent_type
        ]
        outs.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        if outs:
            lines = [f"- {str(r.get('task'))[:120]}" for r in outs[:3]]
            parts.append("[YOUR RECENT DELIVERABLES - build on, don't repeat]\n" + "\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.debug("expert brief store read failed", exc_info=True)

    return "\n\n".join(parts)


async def review(agent_type: str, task_message: str, brief: str, draft: str) -> str:
    """One senior-reviewer pass. Returns polished text or original draft."""
    from admin.agency.self_heal import looks_like_error
    from admin.config import settings
    import openai

    if not draft or looks_like_error(draft):
        return draft

    client = openai.AsyncOpenAI(
        api_key=settings.WORKSPACE_API_KEY or None,
        base_url=settings.WORKSPACE_API_BASE or None,
    )
    system = (
        f"You are the SENIOR LEAD of the {_persona(agent_type)} team at a "
        "digital agency. A specialist on your team produced the DRAFT below "
        "for the CEO. Your job is quality gate + light fix, not a rewrite:\n"
        "1) Does the draft do EXACTLY what was asked (not generic advice)?\n"
        "2) Is it specific to THIS client using the BRIEF facts given?\n"
        "3) Is it actionable - concrete steps/names/numbers where the "
        "request implies them?\n"
        "Rules: never invent data you don't have. If an input is genuinely "
        "missing, add a short 'NEED FROM CEO:' line instead of guessing. "
        "If the draft is already good, keep it nearly intact.\n"
        "Output format: the final deliverable text, then a separator line "
        "'---', then 'WORK NOTES:' with 2-4 bullet lines stating what you "
        "checked/fixed."
    )
    user = f"[TASK]\n{task_message}\n\n"
    if brief:
        user += f"[BRIEF]\n{brief}\n\n"
    user += f"[DRAFT]\n{draft}"

    try:
        resp = await client.chat.completions.create(
            model=settings.WORKSPACE_AGENT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception:  # noqa: BLE001
        logger.warning("expert review LLM unavailable - keeping raw draft")
        return draft
    out = (resp.choices[0].message.content or "").strip()
    return out or draft
