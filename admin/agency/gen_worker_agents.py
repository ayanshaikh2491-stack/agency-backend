"""Generate src/workers/agent_catalog.ts from admin/agency/agent_registry.json.

WHY A GENERATED MIRROR EXISTS
-----------------------------
The Cloudflare Worker (src/workers/index.ts) cannot import a Python module —
Workers bundles only the TS/JS graph reachable from its entrypoint, and a JSON
file is not fetchable at runtime without a separate asset upload. So the
canonical Python registry cannot be read directly from the edge.

The honest options were (a) keep a hand-maintained TS list that drifts — which
is exactly the bug being fixed — or (b) generate this small TS mirror from the
canonical registry and fail CI when it goes stale. This is (b).

WHAT IS GENERATED
-----------------
Only the agent catalog that GET /api/agents serves. The file is committed, not
built at deploy time, so `wrangler deploy` needs no extra step.

REGENERATE AFTER EDITING admin/agency/agent_registry.json:

    python -m admin.agency.gen_worker_agents          # write the file
    python -m admin.agency.gen_worker_agents --check  # exit 1 if stale (CI)

`--check` is the one that matters. Wire it into CI/pre-commit so a registry
edit that forgets to regenerate cannot ship:

    "check:agent-catalog": "python -m admin.agency.gen_worker_agents --check"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agent_catalog

# admin/agency/gen_worker_agents.py -> repo root -> src/workers/
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "src" / "workers" / "agent_catalog.ts"

# Agents the Worker itself can execute. The Worker has no Chromium, no shell and
# no long-lived process, so anything assigned host="render" is catalogued (the
# Worker can still route a task for it to the Render worker via the agent_tasks
# queue) but is not advertised as locally executable.
_LOCAL_HOSTS = ("cloudflare",)

_HEADER = """\
// ─────────────────────────────────────────────────────────────────────────────
// GENERATED FILE — DO NOT EDIT BY HAND.
// Source of truth: admin/agency/agent_registry.json
// Regenerate:      python -m admin.agency.gen_worker_agents
// Verify (CI):     python -m admin.agency.gen_worker_agents --check
//
// A Cloudflare Worker cannot import the Python registry, so this mirror exists.
// Run the --check command above whenever the registry changes, or the edge
// catalog silently goes stale — which is the drift this file was created to end.
// ─────────────────────────────────────────────────────────────────────────────

export type AgentHost = 'render' | 'cloudflare' | 'both'

export interface AgentEntry {
  id: string
  capabilities: string[]
  host: AgentHost
  /** True when this agent can execute inside the Worker runtime itself. */
  local: boolean
}

export const AGENT_CATALOG: AgentEntry[] = %s

export const AGENT_IDS: string[] = AGENT_CATALOG.map((a) => a.id)

/** Ids the Worker can run without bouncing the task to the Render host. */
export const LOCAL_AGENT_IDS: string[] = AGENT_CATALOG.filter((a) => a.local).map((a) => a.id)

/** Ids that must be handed to a Render worker (no Chromium/shell available). */
export const RENDER_AGENT_IDS: string[] = AGENT_CATALOG.filter((a) => !a.local).map((a) => a.id)
"""


def build_entries() -> list[dict]:
    """Catalog rows for every IMPLEMENTED agent, in canonical registry order.

    Placeholders (e.g. the `aws` scaffold) are excluded: they are not agents and
    must never be advertised or routed to.
    """
    reg = agent_catalog.AGENT_REGISTRY
    rows: list[dict] = []
    for aid in agent_catalog.implemented_ids():
        entry = reg.get(aid) or {}
        host = entry.get("host") or "both"
        rows.append({
            "id": aid,
            "capabilities": list(entry.get("capabilities") or []),
            "host": host,
            "local": host in _LOCAL_HOSTS,
        })
    return rows


def render() -> str:
    body = json.dumps(build_entries(), indent=2, ensure_ascii=False)
    # json.dumps gives double-quoted keys; this is valid TS object literal
    # syntax, so the output can be embedded as-is.
    return _HEADER % body + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the generated file is out of date (for CI)",
    )
    args = ap.parse_args(argv)

    wanted = render()

    if args.check:
        if not OUT_PATH.is_file():
            print(f"FAIL: {OUT_PATH} is missing — run: python -m admin.agency.gen_worker_agents")
            return 1
        current = OUT_PATH.read_text(encoding="utf-8")
        if current != wanted:
            print(
                f"FAIL: {OUT_PATH} is out of date with "
                f"admin/agency/agent_registry.json\n"
                f"      run: python -m admin.agency.gen_worker_agents"
            )
            return 1
        print(f"OK: {OUT_PATH} matches admin/agency/agent_registry.json")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(wanted, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(build_entries())} agents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
