"""register_agent — onboard a NEW agency agent with its OWN skill brain.

When the boss (or CEO) adds a new agent (e.g. an "AWS agent", a "Finance agent"),
this helper gives it the SAME first-class treatment as the core agents:

1. Creates admin/agency/<name>_skills_repo/ (its own brain folder, repo-local so
   it deploys to AWS with the agent).
2. Copies the listed skills into that folder (from a source dir or authors stubs).
3. Generates admin/agency/<name>_skills.py that uses agent_skill_loader (so the
   agent loads from its OWN folder, not ~/.jcode).
4. Registers it in AGENT_REGISTRY so the CEO (and the orchestrator) know the agent
   exists, what it does, and what skills it has.

Result: the NEW agent thinks in its own domain (like CEO/AWS/etc.), AND the CEO
knows about it — exactly the "everyone should know their own brain + the CEO
knows why" requirement.

Usage (from code or a one-off script):
    from register_agent import register_agent
    register_agent(
        name="aws",
        role="AWS cloud infra & deployment agent",
        skills=["aws-cdk", "aws-serverless", "aws-security"],  # copied if on disk
        source_dir=None,  # optional path to copy skills from
    )
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

from .agent_skill_loader import AGENCY_DIR

logger = logging.getLogger(__name__)

# ── Master registry of every agent the CEO/orchestrator knows about ─────────
# Core agents are seeded here; register_agent() appends new ones at runtime and
# persists them to AGENT_REGISTRY_FILE so restarts remember them.
AGENT_REGISTRY_FILE = AGENCY_DIR / "agent_registry.json"

AGENT_REGISTRY: dict[str, dict] = {
    "ceo": {
        "role": "Co-founder & strategic brain — orchestrates, delegates, reports to boss",
        "skills_folder": "ceo_skills_repo",
        "skill_count": 15,
        "core": True,
    },
    "sba": {
        "role": "Sales / lead-gen agent",
        "skills_folder": "sba_skills_repo",
        "skill_count": 8,
        "core": True,
    },
    "seo": {
        "role": "Search & AI-visibility agent (SEO/AEO/GEO)",
        "skills_folder": "seo_skills_repo",
        "skill_count": 6,
        "core": True,
    },
    "social": {
        "role": "Content & social-media agent",
        "skills_folder": "social_skills_repo",
        "skill_count": 6,
        "core": True,
    },
    "website": {
        "role": "Design / frontend / deploy agent",
        "skills_folder": "website_skills_repo",
        "skill_count": 18,
        "core": True,
    },
}


def _load_registry() -> dict:
    if AGENT_REGISTRY_FILE.is_file():
        try:
            import json
            return json.loads(AGENT_REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return dict(AGENT_REGISTRY)
    return dict(AGENT_REGISTRY)


def _save_registry(reg: dict) -> None:
    import json
    AGENT_REGISTRY_FILE.write_text(
        json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def register_agent(
    name: str,
    role: str,
    skills: list[str],
    source_dir: str | None = None,
    keywords: dict[str, list[str]] | None = None,
    core: bool = False,
) -> dict:
    """Onboard a new agent with its own repo-local skill brain.

    Returns the registry entry for the new agent.
    """
    name = name.lower().strip()
    repo_dir = AGENCY_DIR / f"{name}_skills_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Copy provided skills into the agent's own folder
    copied = []
    if source_dir:
        src = Path(source_dir)
        for s in skills:
            sdir = src / s
            if sdir.is_dir():
                dst = repo_dir / s
                if dst.is_dir():
                    shutil.rmtree(dst)
                shutil.copytree(sdir, dst)
                copied.append(s)
            else:
                logger.warning("skill not found in source: %s", s)

    # Author a stub SKILL.md for any skill we couldn't copy (so detection still works)
    for s in skills:
        if s not in copied:
            stub = repo_dir / s / "SKILL.md"
            if not stub.exists():
                stub.parent.mkdir(parents=True, exist_ok=True)
                stub.write_text(
                    f"# {s}\n\n"
                    f"Skill for the {name} agent. Describe the workflow, triggers, "
                    f"and guardrails here so the agent has its own domain brain.\n",
                    encoding="utf-8",
                )
                copied.append(s)

    # Generate the *_skills.py loader that uses agent_skill_loader (own folder)
    _generate_skills_module(name, role, skills, keywords or {})

    # Register in the CEO/orchestrator registry (persisted)
    reg = _load_registry()
    reg[name] = {
        "role": role,
        "skills_folder": f"{name}_skills_repo",
        "skill_count": len(skills),
        "core": core,
    }
    _save_registry(reg)
    AGENT_REGISTRY[name] = reg[name]

    logger.info("Registered agent '%s' with %d skills (folder: %s)", name, len(skills), repo_dir)
    return reg[name]


def _generate_skills_module(name: str, role: str, skills: list[str], keywords: dict) -> None:
    """Generate admin/agency/<name>_skills.py using the shared loader."""
    reg_items = []
    for s in skills:
        kws = keywords.get(s, [s])
        kws_str = ",\n            ".join(f'"{k}"' for k in kws)
        reg_items.append(
            f'    {{\n'
            f'        "name": "{s}",\n'
            f'        "keywords": [\n            {kws_str},\n'
            f'        ],\n'
            f'        "description": "{s} skill for the {name} agent",\n'
            f'    }},'
        )
    reg_block = "\n".join(reg_items)

    module = f'''"""={name.upper()} Agent Skills — {name}'s OWN brain, loaded from its repo-local folder.

{role}. Its skills live in admin/agency/{name}_skills_repo/ (repo-local, deploys to
AWS with the agent). Detect by keyword -> load from own folder -> inject as context.
Generated by register_agent(); edit the registry below to tune keywords.
"""

from __future__ import annotations

import logging

from agent_skill_loader import (
    detect_agent_skills,
    build_agent_skill_context,
    list_agent_skills,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "{name}"

{name.upper()}_SKILL_REGISTRY: list[dict] = [
{reg_block}
]


def detect_skills(message: str, max_skills: int = 2) -> list[dict]:
    """Detect relevant {name} skills from a message (loaded from {name}_skills_repo/)."""
    return detect_agent_skills(AGENT_NAME, message, {name.upper()}_SKILL_REGISTRY, max_skills=max_skills)


def build_skill_context(skills: list[dict]) -> str:
    """Build the {name} skill context block."""
    return build_agent_skill_context(skills)


def list_{name}_skills() -> list[dict]:
    """List {name}'s own skills (without loading content)."""
    return list_agent_skills(AGENT_NAME, {name.upper()}_SKILL_REGISTRY)
'''
    out = AGENCY_DIR / f"{name}_skills.py"
    out.write_text(module, encoding="utf-8")


def list_agents() -> dict:
    """Return the full agent registry (CEO/orchestrator view)."""
    return _load_registry()
