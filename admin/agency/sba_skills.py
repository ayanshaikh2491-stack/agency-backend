"""SBA Agent Skills — SBA's OWN brain, loaded from its repo-local skill folder.

SBA is the sales/lead-gen agent. Its skills live in
admin/agency/sba_skills_repo/ (copied from the domain catalog + authored where
missing). This is repo-local so it deploys to AWS with the agent — it does NOT
depend on ~/.jcode/skills existing on the server.

Mechanism: detect by keyword -> load SKILL.md from sba_skills_repo/ -> inject as
context into SBA's model call. SBA thinks in its OWN sales domain, not as a
dumb router.
"""

from __future__ import annotations

import logging

from .agent_skill_loader import (
    detect_agent_skills,
    build_agent_skill_context,
    list_agent_skills,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "sba"

# ── SBA's own skill registry (its domain brain) ─────────────────────────────
SBA_SKILL_REGISTRY: list[dict] = [
    {
        "name": "cold-outreach",
        "keywords": [
            "cold outreach", "cold dm", "cold email", "prospect", "linkedin message",
            "cold message", "outbound", "lead generation", "dm", "direct message",
            "follow up", "follow-up", "sequence", "reply", "open rate",
        ],
        "description": "8 proven sales systems for cold outreach, DM, email prospecting",
    },
    {
        "name": "upwork-lead-gen",
        "keywords": [
            "upwork", "fiverr", "freelancer", "freelance", "gig", "job post",
            "project", "contract", "remote work", "hire", "proposal", "bid",
            "client", "open project", "rfp", "request for proposal",
            "freelance platform", "marketplace", "talent", "outsource",
        ],
        "description": "Find and win leads on Upwork, Fiverr, Freelancer — search jobs, submit proposals, convert to clients",
    },
    {
        "name": "linkedin-lead-gen",
        "keywords": [
            "linkedin", "linked in", "profile", "connection", "inmail",
            "sales navigator", "linkedin search", "linkedin prospecting",
            "linkedin outreach", "linkedin message", "linkedin sales",
        ],
        "description": "LinkedIn prospecting, profile research, connection requests, InMail outreach",
    },
    {
        "name": "web-lead-discovery",
        "keywords": [
            "chrome", "browser", "website", "web search", "google search",
            "find leads", "lead research", "company research", "industry research",
            "competitor", "directory", "listings", "yelp", "google maps",
            "crunchbase", "angellist", "producthunt", "g2", "capterra",
        ],
        "description": "Use browser to search for leads, research companies, find contact info on any platform",
    },
    {
        "name": "alex-hormozi-pitch",
        "keywords": [
            "offer", "pitch", "value proposition", "pricing", "guarantee",
            "grand slam", "irresistible offer", "value stack", "bonus",
            "scarcity", "urgency",
        ],
        "description": "Create irresistible offers using Alex Hormozi methodology",
    },
    {
        "name": "sales-enablement",
        "keywords": [
            "sales deck", "pitch deck", "one pager", "one-pager", "objection",
            "demo script", "proposal", "sales playbook", "buyer persona",
            "sales collateral", "talk track", "roi calculator", "case study brief",
        ],
        "description": "Sales collateral, pitch decks, objection handling, proposal templates",
    },
    {
        "name": "lead-qualification",
        "keywords": [
            "qualify", "qualification", "lead score", "bant", "champ",
            "meddic", "score", "red flag", "dq", "lead evaluation",
            "qualify lead", "prospect qualify", "is this lead good",
        ],
        "description": "CHAMP/BANT/MEDDIC lead qualification framework with scoring",
    },
    {
        "name": "meeting-companion",
        "keywords": [
            "meeting", "schedule", "call", "discovery", "demo",
            "follow up", "post-meeting", "pre-meeting", "meeting script",
            "handoff", "objection handling", "close", "next step",
        ],
        "description": "Full meeting lifecycle — scheduling, live companion, follow-up, handoff",
    },
]


def detect_skills(message: str, max_skills: int = 2) -> list[dict]:
    """Detect relevant SBA skills from a message (loaded from sba_skills_repo/)."""
    return detect_agent_skills(AGENT_NAME, message, SBA_SKILL_REGISTRY, max_skills=max_skills)


def build_skill_context(skills: list[dict]) -> str:
    """Build the SBA skill context block."""
    return build_agent_skill_context(skills)


def list_sba_skills() -> list[dict]:
    """List SBA's own skills (without loading content)."""
    return list_agent_skills(AGENT_NAME, SBA_SKILL_REGISTRY)
