"""agent_catalog — the ONE canonical agent registry and its derived views.

There used to be three hand-maintained agent lists that disagreed:

  1. admin/agency/agent_registry.json  — 6 (ceo, sba, seo, social, website, aws)
  2. admin/workspace/manager.py        — 9 (DEFAULT_AGENTS)
  3. admin/api/routes/multiagent.py    — 7 (BUILTIN_AGENT_IDS)
plus a 4th copy in admin/agency/ceo.py and a 5th map in manager.py, and a 6th
hardcoded list in the Cloudflare worker (src/workers/index.ts).

They drifted because each list was hardcoded next to the code that used it.
This module makes agent_registry.json the single source of truth and derives
every view from it, so an agent is added or retired in exactly one place.

Per-agent fields (the ``role``/``skills_folder``/``skill_count``/``core`` quartet
is the original register_agent() persistence contract and is unchanged):

    implemented     bool — a real, runnable agent exists for this id
    placeholder     bool — a register_agent() scaffold, NOT a real agent
    fanout          bool — registered in agency/workers.py as a fan-out worker
    workspace_agent bool — instantiated by workspace/manager.py per workspace
    host            str  — "render" | "cloudflare" | "both"
    module / class  str  — import path + class name for the real implementation
    capabilities    list — advertised to the Cloudflare worker /api/agents route

``implemented`` and ``fanout`` are deliberately different. ``analyzing`` and
``memory`` are real agents (admin/workspace/agents/*.py) but are NOT fan-out
workers: agency/workers.py routes every non-SBA employee through
``_run_real_agent``, which needs an ``admin/tools/<id>_tools.py`` exposing
``execute_<id>_tool``. Until those exist, listing them in the fan-out roster
would just produce "unknown worker" failures.

Keeping admin/agency/agent_registry.json a flat ``{id: entry}`` map (rather than
wrapping it in {"agents": ...}) is deliberate: register_agent() does
``_save_registry(reg)`` on the whole document, so any new envelope would be
destroyed on the next register_agent() call.

This module is stdlib-only on purpose. admin/agency/__init__.py goes out of its
way to stay cheap to import (see the lazy AgencyCEO/SBAAgent note there), so
nothing in the import chain of these constants may pull in an LLM client.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The canonical registry file. register_agent() reads and writes this same path.
AGENCY_DIR = Path(__file__).parent
AGENT_REGISTRY_FILE = AGENCY_DIR / "agent_registry.json"

VALID_HOSTS = ("render", "cloudflare", "both")

# Fields register_agent() owns. Everything else in an entry is curated metadata.
_PERSISTED_CORE_FIELDS = ("role", "skills_folder", "skill_count", "core")

# Defaults applied to a brand-new entry created by register_agent(). A freshly
# onboarded agent has a skills brain but no runnable implementation yet, so it
# starts as a placeholder that nothing fans out to until a human wires it up.
_NEW_AGENT_DEFAULTS: dict[str, Any] = {
    "implemented": False,
    "placeholder": True,
    "fanout": False,
    "workspace_agent": False,
    "host": "cloudflare",
    "module": None,
    "class": None,
    "capabilities": [],
}


# ── Load / save ─────────────────────────────────────────────────────────────


def load_registry() -> dict[str, dict]:
    """Read the canonical registry. Falls back to an empty map if unreadable.

    Never raises: a corrupt registry must not stop the API from booting.
    """
    if not AGENT_REGISTRY_FILE.is_file():
        return {}
    try:
        data = json.loads(AGENT_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("agent_registry.json unreadable; using empty catalog", exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def save_registry(reg: dict[str, dict]) -> None:
    """Persist the canonical registry back to disk (register_agent() writes here)."""
    AGENT_REGISTRY_FILE.write_text(
        json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def get_agent(agent_id: str) -> dict:
    """Return one registry entry, or an empty dict if unknown."""
    return _reg().get(agent_id, {})


# ── Module-level state, kept in sync in place so importers never go stale ──

#: Backward-compatible alias for the seeded registry dict. register_agent()
#: mutates this in place and calls refresh() so the derived lists below update
#: for everyone who imported them, not just for future importers.
AGENT_REGISTRY: dict[str, dict] = load_registry()


def _reg() -> dict[str, dict]:
    return AGENT_REGISTRY


# ── Derived views ───────────────────────────────────────────────────────────


def agent_ids() -> list[str]:
    """Every id in the registry, placeholders included, in file order."""
    return list(_reg().keys())


def _select(pred) -> list[str]:
    return [aid for aid, e in _reg().items() if pred(e)]


def implemented_ids() -> list[str]:
    """Real, runnable agents only — no placeholders."""
    return _select(lambda e: e.get("implemented", False) and not e.get("placeholder", False))


def placeholder_ids() -> list[str]:
    """register_agent() scaffolds that are not real agents (currently: aws)."""
    return _select(lambda e: bool(e.get("placeholder")) or not e.get("implemented", False))


def workspace_agent_ids() -> list[str]:
    """Agents every workspace is provisioned with (workspace/manager.py).

    This is the old hardcoded DEFAULT_AGENTS, now derived.
    """
    return _select(
        lambda e: e.get("implemented", False)
        and not e.get("placeholder", False)
        and e.get("workspace_agent", False)
    )


def fanout_ids() -> list[str]:
    """Ids the CEO may fan a brief out to (agency/workers.py + multiagent.py).

    This is the old hardcoded BUILTIN_AGENT_IDS, now derived. A subset of
    workspace_agent_ids(): analyzing/memory are real but have no worker
    dispatcher yet.
    """
    return _select(
        lambda e: e.get("implemented", False)
        and not e.get("placeholder", False)
        and e.get("fanout", False)
    )


def public_ids() -> list[str]:
    """Roster the CEO can see and advertise (implemented agents, incl. ceo)."""
    return implemented_ids()


def agents_for_host(host: str) -> list[str]:
    """Implemented agents that can run on ``host`` ("render"|"cloudflare"|"both")."""
    if host not in VALID_HOSTS:
        raise ValueError(f"host must be one of {VALID_HOSTS}, got {host!r}")
    if host == "both":
        return implemented_ids()
    return _select(
        lambda e: e.get("implemented", False)
        and not e.get("placeholder", False)
        and e.get("host") in (host, "both")
    )


def host_of(agent_id: str) -> str:
    """Deployment host for one agent ('both' if unknown — the safe default)."""
    return str(get_agent(agent_id).get("host") or "both")


def domain_agent_classes() -> dict[str, tuple[str, str]]:
    """{agent_id: (module_path, class_name)} for workspace/manager.py.

    Replaces the hardcoded ``_domain_agents`` map. SBA is absent by design —
    manager.py handles it on a separate code path above the domain-agent branch.
    """
    out: dict[str, tuple[str, str]] = {}
    for aid, e in _reg().items():
        if not (e.get("implemented") and not e.get("placeholder")):
            continue
        if not e.get("workspace_agent"):
            continue
        mod, cls = e.get("module"), e.get("class")
        if not mod or not cls or mod.endswith(".sba"):
            continue
        out[aid] = (mod, cls)
    return out


def capabilities_map() -> dict[str, list[str]]:
    """{agent_id: [capabilities]} for the Cloudflare worker mirror."""
    return {aid: list(e.get("capabilities") or []) for aid, e in _reg().items()}


# ── Module-level derived views ──────────────────────────────────────────────

#: Every workspace gets these agent types.
DEFAULT_AGENTS: list[str] = workspace_agent_ids()

#: The CEO's built-in fan-out roster.
BUILTIN_AGENT_IDS: list[str] = fanout_ids()

#: What the Cloudflare worker advertises at GET /api/agents.
PUBLIC_AGENT_IDS: list[str] = public_ids()


def refresh() -> None:
    """Recompute the derived lists IN PLACE after AGENT_REGISTRY changes.

    Mutating in place (rather than rebinding) is what keeps
    ``from admin.agency.agent_catalog import DEFAULT_AGENTS`` honest in modules
    that already did that import.
    """
    DEFAULT_AGENTS[:] = workspace_agent_ids()
    BUILTIN_AGENT_IDS[:] = fanout_ids()
    PUBLIC_AGENT_IDS[:] = public_ids()


__all__ = [
    "AGENT_REGISTRY",
    "AGENT_REGISTRY_FILE",
    "AGENCY_DIR",
    "BUILTIN_AGENT_IDS",
    "DEFAULT_AGENTS",
    "PUBLIC_AGENT_IDS",
    "VALID_HOSTS",
    "agent_ids",
    "agents_for_host",
    "capabilities_map",
    "domain_agent_classes",
    "fanout_ids",
    "get_agent",
    "host_of",
    "implemented_ids",
    "load_registry",
    "placeholder_ids",
    "public_ids",
    "refresh",
    "save_registry",
    "workspace_agent_ids",
]
