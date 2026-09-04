"""Shared AEO/GEO context builder for all workspace agents.

Every agent (SEO, Social, Content, Website) should tailor its work to the
client business's Answer Engine Optimization (AEO) and Generative Engine
Optimization (GEO) angles. This module centralizes:
  - per-workspace AEO/GEO angle extraction (from sba_biztypes classification)
  - skill-aware AEO/GEO guidance injection (from seo_skills)

Keeping it in one place means the three content agents (social, content,
website) stay in sync with SEO's AI-visibility strategy without duplication.
"""
from __future__ import annotations

import logging
from typing import Any

from admin.agency import seo_skills, sba_biztypes

logger = logging.getLogger(__name__)


def get_workspace_aeo_geo(workspace_name: str) -> dict[str, Any]:
    """Return the per-business AEO/GEO angle block for a workspace.

    Never raises: returns safe generic angles on any error.
    """
    try:
        cfg = sba_biztypes.classify_business(workspace_name)
    except Exception:  # noqa: BLE001 - never block an agent on classify errors
        cfg = {}
    return {
        "category": cfg.get("category") or "local business",
        "aeo_angle": cfg.get("aeo_angle") or (
            "Optimize for AI answers about your business -- FAQ + entity "
            "structured data so ChatGPT/Perplexity cite you"
        ),
        "geo_angle": cfg.get("geo_angle") or (
            "Become a citation source for AI search -- original, trustworthy "
            "content LLMs reference"
        ),
    }


def build_aeo_geo_section(workspace_name: str) -> str:
    """A formatted prompt section describing THIS workspace's AEO/GEO angles."""
    info = get_workspace_aeo_geo(workspace_name)
    return (
        f"Business category: {info['category']}\n"
        f"- AEO angle (rank inside AI answers like ChatGPT/Perplexity/Gemini): "
        f"{info['aeo_angle']}\n"
        f"- GEO angle (become a source AI search engines cite): "
        f"{info['geo_angle']}\n"
        "Apply these angles when you create content, captions, visuals, or site "
        "structure so the client shows up in AI answers, not just Google."
    )


def build_aeo_geo_skill_context(message: str, max_skills: int = 2) -> str:
    """Detect AEO/GEO-relevant skills and return their guidance text.

    Loads real SKILL.md content (aeo, geo, seo) from the skills dirs.
    Returns '' when nothing matches or on any error.
    """
    try:
        skills = seo_skills.detect_skills(message, max_skills=max_skills)
        ctx = seo_skills.build_skill_context(skills)
        return ctx
    except Exception:  # noqa: BLE001 - never block an agent on skill errors
        return ""


def build_aeo_geo_prompt(workspace_name: str, message: str = "") -> str:
    """Full AEO/GEO prompt block: per-workspace angle + relevant skill guidance."""
    section = build_aeo_geo_section(workspace_name)
    skill_ctx = build_aeo_geo_skill_context(message)
    block = (
        "## AI Visibility — AEO + GEO (this workspace)\n"
        + section
    )
    if skill_ctx:
        block += "\n\n" + skill_ctx
    return block
