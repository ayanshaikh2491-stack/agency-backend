"""Shared skill-loading helper for ALL agency agents (CEO + workers + future).

Every agent now has its OWN skill folder INSIDE the repo
(admin/agency/<agent>_skills_repo/). This is deliberately repo-local (not
~/.jcode/skills) so the skills deploy with the agent to AWS/server — they are
NOT on the local home dir there, so loading from ~/.jcode would silently break
in production (empty skill context = "dumb" agent).

Each agent's *_skills.py builds its registry (name + keywords + description)
and calls load_agent_skill / detect_agent_skills from here. New agents added via
register_agent() get their own folder + a generated *_skills.py automatically, so
the CEO AND the new agent both know the agent's brain.

Pattern mirrors the worker agents' original detect->context mechanism, but the
source is now local + portable.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# All agent skill folders live here, alongside the *_skills.py files.
AGENCY_DIR = Path(__file__).parent  # admin/agency


def agent_skills_dir(agent_name: str) -> Path:
    """Return the repo-local skill folder for an agent.

    agent_name 'sba' -> admin/agency/sba_skills_repo/
    """
    return AGENCY_DIR / f"{agent_name}_skills_repo"


def load_agent_skill(agent_name: str, skill_name: str, max_chars: int = 2000) -> str | None:
    """Load SKILL.md content for a skill owned by `agent_name`.

    Returns the trimmed SKILL.md text, or None if missing.
    """
    skill_path = agent_skills_dir(agent_name) / skill_name / "SKILL.md"
    if not skill_path.is_file():
        logger.warning("[%s] skill not found: %s (%s)", agent_name, skill_name, skill_path)
        return None
    try:
        return skill_path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except OSError as e:
        logger.error("[%s] error reading skill %s: %s", agent_name, skill_name, e)
        return None


def detect_agent_skills(
    agent_name: str,
    message: str,
    registry: list[dict],
    max_skills: int = 2,
    max_total_chars: int = 4000,
) -> list[dict]:
    """Detect relevant skills for `agent_name` from `message` using `registry`.

    `registry` is the agent's own list of {name, keywords, description}. Returns
    matched skills with loaded `content`. Mirrors the original worker logic but
    loads from the agent's OWN repo-local folder.
    """
    msg_lower = message.lower()
    matched: list[dict] = []
    total = 0
    for skill in registry:
        if any(kw in msg_lower for kw in skill.get("keywords", [])):
            content = load_agent_skill(agent_name, skill["name"])
            if content:
                matched.append({
                    "name": skill["name"],
                    "description": skill.get("description", ""),
                    "content": content,
                })
                total += len(content)
            if len(matched) >= max_skills or total >= max_total_chars:
                break
    return matched


def build_agent_skill_context(skills: list[dict], max_total_chars: int = 4000) -> str:
    """Build the injected skill context block from matched skills."""
    if not skills:
        return ""
    parts: list[str] = []
    total = 0
    for s in skills:
        content = s.get("content", "") or s.get("description", "")
        if not content:
            continue
        block = f"### {s['name']}\n{content}"
        if total + len(block) > max_total_chars:
            block = block[: max(0, max_total_chars - total)]
        parts.append(block)
        total += len(block)
        if total >= max_total_chars:
            break
    return "\n\n".join(parts)


def list_agent_skills(agent_name: str, registry: list[dict]) -> list[dict]:
    """List an agent's skills (no content load)."""
    return [
        {"name": s["name"], "description": s.get("description", ""),
         "keywords": s.get("keywords", [])}
        for s in registry
    ]
