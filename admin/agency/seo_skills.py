"""SEO Agent Skills — SEO's OWN brain, loaded from its repo-local skill folder.

SEO is the search/AI-visibility agent. Its skills live in
admin/agency/seo_skills_repo/ (copied from the domain catalog + authored where
missing: seo-technical, seo-geo). Repo-local so it deploys to AWS with the agent.

Covers SEO (ranking), technical audits, AEO (Answer Engine Optimization — ranking
inside AI answers like ChatGPT/Perplexity/Gemini), and GEO (Generative Engine
Optimization — being cited by AI search engines).
"""

from __future__ import annotations

import logging

from .agent_skill_loader import (
    detect_agent_skills,
    build_agent_skill_context,
    list_agent_skills,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "seo"

SEO_SKILL_REGISTRY: list[dict] = [
    {
        "name": "seo",
        "keywords": [
            "seo", "search engine optimization", "ranking", "serp",
            "backlink", "link building", "domain authority",
            "keyword research", "on-page seo", "off-page seo",
            "technical seo", "site audit", "crawl", "indexing",
            "meta tag", "title tag", "schema markup", "structured data",
            "core web vitals", "page speed", "mobile seo",
            "local seo", "google business profile",
            "content optimization", "internal linking",
        ],
        "description": "Full-stack SEO strategy -- technical audits, keyword research, on-page/off-page, local SEO",
    },
    {
        "name": "seo-technical",
        "keywords": [
            "technical seo", "technical audit", "crawl budget",
            "canonical", "hreflang", "render", "javascript seo",
            "site architecture", "xml sitemap", "robots",
            "structured data validation", "schema validation",
        ],
        "description": "Technical SEO deep-dive -- crawlability, render, canonicalization, site architecture",
    },
    {
        "name": "seo-aeo-best-practices",
        "keywords": [
            "answer engine optimization", "aeo", "ai overview",
            "chatgpt", "perplexity", "gemini", "ai search",
            "ai answer", "featured snippet", "people also ask",
            "knowledge graph", "knowledge panel", "entity seo",
            "schema for ai", "llm optimization", "ai mode",
            "answer box", "position zero",
        ],
        "description": "Answer Engine Optimization -- get the business cited/ranked inside AI answers (ChatGPT, Perplexity, Gemini, AI Overviews)",
    },
    {
        "name": "seo-geo",
        "keywords": [
            "generative engine optimization", "geo", "generative search",
            "ai citation", "cited by ai", "llm visibility",
            "ai search engine", "ai recommend", "ai mentions",
            "generative engine", "ai brand visibility",
            "chatgpt recommendation", "ai overview citation",
        ],
        "description": "Generative Engine Optimization -- be the source AI search engines cite and recommend",
    },
    {
        "name": "content-engine",
        "keywords": [
            "blog", "article", "content strategy", "content calendar",
            "repurpose", "content gap", "pillar content",
            "topic cluster", "content brief", "writer",
        ],
        "description": "Content strategy -- blog writing, repurposing, topic clusters, content briefs",
    },
    {
        "name": "seo-audit",
        "keywords": [
            "audit", "site audit", "technical audit", "seo audit",
            "broken link", "404", "redirect", "canonical",
            "sitemap", "robots.txt", "crawl error",
            "page speed",
        ],
        "description": "Technical SEO audit -- site crawl, broken links, redirects, sitemap analysis",
    },
]


def detect_skills(message: str, max_skills: int = 2) -> list[dict]:
    """Detect relevant SEO skills from a message (loaded from seo_skills_repo/)."""
    return detect_agent_skills(AGENT_NAME, message, SEO_SKILL_REGISTRY, max_skills=max_skills)


def build_skill_context(skills: list[dict]) -> str:
    """Build the SEO skill context block."""
    return build_agent_skill_context(skills)


def list_seo_skills() -> list[dict]:
    """List SEO's own skills (without loading content)."""
    return list_agent_skills(AGENT_NAME, SEO_SKILL_REGISTRY)
