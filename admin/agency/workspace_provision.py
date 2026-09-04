"""Workspace provisioning for the workspace-scoped Supabase layout.

Each workspace owns a Postgres schema ``ws_<slug>`` containing one table
per agent (leads/clients for SBA, website_builds/website_docs/
website_build_log for the Website Agent, more as agents are added).

To create a new workspace:

    from admin.agency.workspace_provision import provision_workspace
    ok = provision_workspace("Acme Co")          # -> ws_acme_co schema + all agent tables

The schema is created server-side by the ``public.provision_workspace``
SQL function via the Supabase RPC endpoint, so the backend only needs the
service key (no direct postgres access).
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Keep in sync with SQL `public.workspace_slug` (migration
# _migrate_workspace_schemas.sql).
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """'My Workspace!' -> 'my_workspace'; falls back to 'default'."""
    s = SLUG_RE.sub("_", (name or "").lower()).strip("_")
    s = re.sub(r"^[0-9_]+", "", s)
    return s or "default"


def schema_for(workspace: str) -> str:
    """Postgres schema name for a workspace, e.g. 'ws_my_workspace'.

    Idempotent: if the workspace id is already schema-style (``ws_<slug>``,
    e.g. the seeded ``ws_agency`` / ``ws_default``), it is returned as-is
    instead of double-prefixing to ``ws_ws_agency``.
    """
    s = slugify(workspace)
    if s.startswith("ws_"):
        return s
    return "ws_" + s


def provision_workspace(ws_name: str, url: str | None = None, key: str | None = None) -> bool:
    """Create the workspace schema + all agent tables via Supabase RPC.

    Returns True on success. Safe to call multiple times (idempotent).
    """
    from admin.agency.website_supabase import get_config

    cfg = get_config()
    if cfg is None:
        logger.warning("workspace_provision: bridge disabled (no SUPABASE_SERVICE_KEY)")
        return False
    base, svc_key = (url or cfg[0]).rstrip("/"), key or cfg[1]
    body = json.dumps({"ws_name": ws_name}).encode("utf-8")
    req = urllib.request.Request(
        base + "/rest/v1/rpc/provision_workspace",
        data=body,
        headers={
            "apikey": svc_key,
            "Authorization": "Bearer " + svc_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        logger.info("workspace_provision: provisioned %s -> %s", ws_name, schema_for(ws_name))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("workspace_provision: failed for %s: %s", ws_name, e)
        return False
