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

NOTE: a registered agent is NOT automatically runnable. register_agent() builds
a skill brain and a registry entry; the agent still has to be given a real
implementation before the CEO can fan work out to it. New entries therefore
land with implemented=False / placeholder=True / fanout=False. Flip those in
admin/agency/agent_registry.json once the agent class and (for fan-out) its
admin/tools/<name>_tools.py dispatcher actually exist.

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
from . import agent_catalog

logger = logging.getLogger(__name__)

# ── Master registry of every agent the CEO/orchestrator knows about ─────────
# The canonical store is admin/agency/agent_registry.json, read/written by
# agent_catalog. This module keeps the same public names it always had:
# AGENT_REGISTRY_FILE, AGENT_REGISTRY, _load_registry(), _save_registry(),
# register_agent(), list_agents().
#
# Every other agent list in the repo (workspace/manager.DEFAULT_AGENTS,
# api/routes/multiagent.BUILTIN_AGENT_IDS, the Cloudflare worker's /api/agents)
# is now DERIVED from this file — see agent_catalog for the derivation rules.
# Add or retire an agent by editing agent_registry.json, not by editing one of
# the consumers.
AGENT_REGISTRY_FILE = agent_catalog.AGENT_REGISTRY_FILE

#: Live view of the canonical registry. Kept as a module-level dict that
#: register_agent() mutates so `from register_agent import AGENT_REGISTRY`
#: keeps working exactly as before.
AGENT_REGISTRY: dict[str, dict] = agent_catalog.AGENT_REGISTRY


def _load_registry() -> dict:
    """Read the canonical registry, falling back to the in-memory copy."""
    reg = agent_catalog.load_registry()
    if not reg:
        return dict(AGENT_REGISTRY)
    return reg


def _save_registry(reg: dict) -> None:
    agent_catalog.save_registry(reg)


def register_agent(
    name: str,
    role: str,
    skills: list[str],
    source_dir: str | None = None,
    keywords: dict[str, list[str]] | None = None,
    core: bool = False,
    *,
    host: str | None = None,
    implemented: bool | None = None,
    fanout: bool | None = None,
) -> dict:
    """Onboard a new agent with its own repo-local skill brain.

    Returns the registry entry for the new agent.

    The persisted file keeps its original flat ``{id: entry}`` shape, so an
    existing entry is MERGED rather than replaced: re-registering a real agent
    never wipes the curated `host` / `capabilities` / `module` metadata that
    agent_catalog's derived views depend on. Pass `host=` / `implemented=` /
    `fanout=` to override those explicitly; anything left as None keeps its
    current value (or the placeholder defaults, for a brand-new id).
    """
    name = name.lower().strip()
    if host is not None and host not in agent_catalog.VALID_HOSTS:
        raise ValueError(
            f"host must be one of {agent_catalog.VALID_HOSTS}, got {host!r}"
        )
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

    # Register in the CEO/orchestrator registry (persisted). Merge into any
    # existing entry so curated metadata survives a re-registration.
    reg = _load_registry()
    existing = dict(reg.get(name) or {})
    entry = dict(agent_catalog._NEW_AGENT_DEFAULTS)
    entry.update(existing)
    entry.update({
        "role": role,
        "skills_folder": f"{name}_skills_repo",
        "skill_count": len(skills),
        "core": core,
    })
    # A brand-new scaffold is a placeholder; if it is now being given a real
    # implementation, that flips the marker and the entry stops being a stub.
    if implemented is None:
        implemented = bool(entry.get("implemented") or entry.get("module"))
    entry["implemented"] = implemented
    entry["placeholder"] = not implemented
    if host is not None:
        entry["host"] = host
    if fanout is not None:
        entry["fanout"] = fanout and implemented
    elif not implemented:
        entry["fanout"] = False

    reg[name] = entry
    _save_registry(reg)
    # Refresh the in-process copy and every derived view IN PLACE so importers
    # of AGENT_REGISTRY / DEFAULT_AGENTS / BUILTIN_AGENT_IDS see the new agent
    # without needing a restart.
    AGENT_REGISTRY.clear()
    AGENT_REGISTRY.update(reg)
    agent_catalog.refresh()

    logger.info("Registered agent '%s' with %d skills (folder: %s)", name, len(skills), repo_dir)
    return entry


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

from .agent_skill_loader import (
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
    """Return the full agent registry (CEO/orchestrator view).

    This is the canonical roster: it now includes every real workspace agent
    (``analyzing`` and ``memory`` used to be missing here, so the CEO could not
    see or delegate to them). Placeholder scaffolds are included too but carry
    ``"placeholder": true`` / ``"implemented": false`` — filter with
    ``agent_catalog.implemented_ids()`` if you need only real agents.
    """
    return _load_registry()
