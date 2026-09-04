"""CEO Agent Skills — the CEO's OWN reasoning, reporting & leadership skills.

The CEO is the most powerful agent in the agency, so it gets the RICHEST skill
set (15 skills) — on par with the worker agents (SBA:8, SEO:6, Social:6,
Website:18). These are its OWN brain/voice, discovered locally from
`ceo_skills_repo/`, NOT copied from the ~/.jcode domain catalog.

Found via the find-skills discovery workflow (web search of the skills.sh
ecosystem + cloned open skill repos):
  - ceo-skill                 : world-class Chief-of-Staff decision advisor
  - status-report             : CEO work-report voice (text / email / PDF / PPT)
  - pptx                      : build PowerPoint decks (board/investor reports)
  - risk-analysis style       : business-investment-advisor, financial-health
  - finance                   : finance-lead, commercial-forecaster
  - exec-comms                : executive-communication, running-meetings
  - product/team             : roadmap-prioritization, stakeholder-alignment,
                               goal-setting-okrs, competitive-strategy,
                               hiring-product-talent, ai-product-strategy

The mechanism mirrors the worker agents' `*_skills.py` (keyword detection ->
context block), but sources from the local CEO folder and is injected into the
CEO system prompt alongside its tools/functions (see ceo.py::call_llm).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── CEO skills live in this folder (CEO's own role skills) ───────────────────
CEO_SKILLS_DIR = Path(__file__).parent / "ceo_skills_repo"

# ── CEO skill registry (its own brain) ──────────────────────────────────────
# name must match a subfolder under CEO_SKILLS_DIR/ that contains SKILL.md.
CEO_SKILL_REGISTRY: list[dict] = [
    {
        "name": "ceo-skill",
        "keywords": [
            "decision", "strategy", "strategic", "should we", "trade-off",
            "risk", "stakeholder", "board", "investor", "prioritize",
            "resource", "allocation", "okr", "kpi", "crisis", "war game",
            "red team", "blind spot", "bias", "business call", "competitive",
            "soch", "plan", "evaluate", "which agent", "delegate who", "what to do",
        ],
        "description": "Real-CEO strategic decision advisor: framing, risk, bias-check, war-gaming, stakeholder mapping",
    },
    {
        "name": "status-report",
        "keywords": [
            "report", "status", "update", "digest", "summary", "kya hua",
            "kya chal raha", "progress", "health", "weekly", "monthly",
            "tell me", "inform", "brief me", "what happened", "results",
            "email report", "send report", "pdf", "ppt", "deck",
        ],
        "description": "CEO work-report voice: text / email / PDF / PPT; 🟢🟡🔴 health, actionable next-steps",
    },
    {
        "name": "pptx",
        "keywords": [
            "ppt", "pptx", "slide", "deck", "presentation", "powerpoint",
            "board deck", "investor deck", "pitch deck",
        ],
        "description": "Build/edit PowerPoint decks — turn a report into a board/investor slide deck",
    },
    {
        "name": "business-investment-advisor",
        "keywords": [
            "investment", "invest", "acquisition", "merger", "ma", "deal",
            "risk assessment", "due diligence", "portfolio", "capital",
            "valuation", "financial risk",
        ],
        "description": "Business investment & risk advisor — evaluate deals, M&A, capital allocation",
    },
    {
        "name": "financial-health",
        "keywords": [
            "financial health", "cash flow", "burn", "runway", "profit",
            "revenue", "margin", "balance sheet", "financial state",
        ],
        "description": "Financial health check — runway, margins, cash position",
    },
    {
        "name": "finance-lead",
        "keywords": [
            "finance", "budget", "accounting", "forecast", "financial plan",
            "cost", "pricing model", "unit economics",
        ],
        "description": "Finance leadership — budgeting, unit economics, financial planning",
    },
    {
        "name": "commercial-forecaster",
        "keywords": [
            "forecast", "projection", "predict", "estimate revenue",
            "sales target", "quarterly plan", "growth model",
        ],
        "description": "Commercial forecasting — revenue/projection modeling",
    },
    {
        "name": "executive-communication",
        "keywords": [
            "communicate", "message", "announce", "email boss", "write to",
            "exec comms", "narrative", "story", "internal comms",
        ],
        "description": "Executive communication — clear messaging to boss, board, team",
    },
    {
        "name": "running-meetings",
        "keywords": [
            "meeting", "agenda", "standup", "sync", "facilitate", "1:1",
            "run a meeting",
        ],
        "description": "Run effective meetings — agendas, facilitation, follow-through",
    },
    {
        "name": "roadmap-prioritization",
        "keywords": [
            "roadmap", "prioritize", "priority", "sequence", "what next",
            "backlog", "scope", "initiative order",
        ],
        "description": "Roadmap & prioritization — sequence initiatives by impact",
    },
    {
        "name": "stakeholder-alignment",
        "keywords": [
            "stakeholder", "alignment", "buy-in", "alignment", "manage boss",
            "manage board", "political", "resistance",
        ],
        "description": "Stakeholder alignment — map interests, build coalitions",
    },
    {
        "name": "goal-setting-okrs",
        "keywords": [
            "okr", "goal", "objective", "kpi set", "target set", "quarterly goal",
        ],
        "description": "Goal setting — OKRs/KPIs, measurable targets",
    },
    {
        "name": "competitive-strategy",
        "keywords": [
            "competitor", "competitive", "market position", "differentiate",
            "moat", "war game", "market share",
        ],
        "description": "Competitive strategy — positioning, moat, market play",
    },
    {
        "name": "hiring-product-talent",
        "keywords": [
            "hire", "hiring", "recruit", "team", "talent", "role", "headcount",
            "interview", "onboard",
        ],
        "description": "Hiring & team building — roles, talent, org design",
    },
    {
        "name": "ai-product-strategy",
        "keywords": [
            "product", "feature", "ai strategy", "product strategy", "build what",
            "product direction", "vision",
        ],
        "description": "AI product strategy — what to build, product direction",
    },
]

MAX_SKILL_CONTENT_CHARS = 6000   # CEO model context is larger; keep generous
MAX_TOTAL_SKILL_CHARS = 16000   # allow several skills at once


def _load_skill_content(skill_name: str) -> str | None:
    """Read SKILL.md from the CEO skills folder."""
    skill_path = CEO_SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.is_file():
        logger.warning("CEO skill not found: %s (%s)", skill_name, skill_path)
        return None
    try:
        return skill_path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.error("Error reading CEO skill %s: %s", skill_name, e)
        return None


def detect_ceo_skills(message: str, max_skills: int = 4) -> list[dict]:
    """Detect relevant CEO skills from the boss's message.

    Returns matched skills with loaded content. At most `max_skills` returned
    (CEO can draw on several at once — it's the strategic brain).
    """
    msg_lower = message.lower()
    matched: list[dict] = []
    seen: set[str] = set()

    for skill in CEO_SKILL_REGISTRY:
        for kw in skill["keywords"]:
            if kw in msg_lower:
                if skill["name"] in seen:
                    break
                content = _load_skill_content(skill["name"])
                if content:
                    matched.append({
                        "name": skill["name"],
                        "description": skill["description"],
                        "content": content,
                    })
                    seen.add(skill["name"])
                break
        if len(matched) >= max_skills:
            break

    return matched


def build_ceo_skill_context(skills: list[dict]) -> str:
    """Build the CEO skill context block from matched skills."""
    if not skills:
        return ""

    blocks: list[str] = []
    total_chars = 0
    for s in skills:
        content = s["content"]
        if len(content) > MAX_SKILL_CONTENT_CHARS:
            content = content[:MAX_SKILL_CONTENT_CHARS] + "\n...[truncated]"
        if total_chars + len(content) > MAX_TOTAL_SKILL_CHARS:
            break
        total_chars += len(content)
        blocks.append(
            f"### CEO Skill: {s['name']}\n"
            f"{s['description']}\n\n"
            f"{content}"
        )

    if not blocks:
        return ""

    return (
        "── YOUR CEO SKILLS (use these to think & report like a real CEO) ──\n"
        "Tu ek asli CEO hai. In skills ko apne tools/functions ke saath use kar:\n\n"
        + "\n\n".join(blocks)
        + "\n──────────────────────────────────────────"
    )


def list_ceo_skills() -> list[dict]:
    """List the CEO's own skills (without loading content)."""
    return [
        {"name": s["name"], "description": s["description"], "keywords": s["keywords"]}
        for s in CEO_SKILL_REGISTRY
    ]
