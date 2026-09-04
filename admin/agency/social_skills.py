"""Social Agent Skills — Social's OWN brain, loaded from its repo-local folder.

Social is the content/social-media agent. Its skills live in
admin/agency/social_skills_repo/ (copied from the domain catalog + authored where
missing: post-writer-sms). Repo-local so it deploys to AWS with the agent.

Mirrors the other agents' pattern: detect by keyword -> load from own folder ->
inject as context so the Social agent applies real marketing/copywriting frameworks.
"""

from __future__ import annotations

import logging

from .agent_skill_loader import (
    detect_agent_skills,
    build_agent_skill_context,
    list_agent_skills,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "social"

SOCIAL_SKILL_REGISTRY: list[dict] = [
    {
        "name": "ad-creative",
        "keywords": [
            "ad copy", "ad creative", "headline", "rsa", "facebook ad",
            "google ad", "ad variations", "hook writing", "creative strategy",
        ],
        "description": "Generate and iterate paid ad creative (headlines, descriptions, primary text)",
    },
    {
        "name": "social",
        "keywords": [
            "linkedin post", "twitter thread", "instagram", "social media",
            "content calendar", "viral", "what should i post", "reel", "carousel",
            "caption", "post ideas", "social strategy", "hashtag",
        ],
        "description": "Social media content creation, scheduling, and strategy",
    },
    {
        "name": "content-engine",
        "keywords": [
            "content system", "content plan", "repurpose", "multi-platform",
            "content pipeline", "newsletter", "youtube script",
        ],
        "description": "Platform-native content systems and repurposing",
    },
    {
        "name": "post-writer-sms",
        "keywords": [
            "write a post", "post for me", "draft post", "engagement post",
            "cta post", "announcement post",
        ],
        "description": "Write platform-native social posts",
    },
    {
        "name": "brand-voice",
        "keywords": [
            "brand voice", "tone of voice", "writing style", "voice profile",
            "consistent voice", "copy style",
        ],
        "description": "Build a source-derived writing style profile",
    },
    {
        "name": "content-calendar-sms",
        "keywords": [
            "content calendar", "posting schedule", "when to post",
            "content cadence", "weekly plan", "monthly content plan",
        ],
        "description": "Plan social media posting schedules and calendars",
    },
]


def detect_skills(message: str, max_skills: int = 2) -> list[dict]:
    """Detect relevant Social skills from a message (loaded from social_skills_repo/)."""
    return detect_agent_skills(AGENT_NAME, message, SOCIAL_SKILL_REGISTRY, max_skills=max_skills)


def build_skill_context(skills: list[dict]) -> str:
    """Build the Social skill context block."""
    return build_agent_skill_context(skills)


def list_social_skills() -> list[dict]:
    """List Social's own skills (without loading content)."""
    return list_agent_skills(AGENT_NAME, SOCIAL_SKILL_REGISTRY)
