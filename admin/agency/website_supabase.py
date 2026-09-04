"""Website Agent Supabase bridge — saves builds, the 6 client documents,
and every workspace event into the self-hosted Supabase.

Uses the same REST-API pattern as `supabase_bridge.py` (urllib only, no
heavy dependencies). Reads SUPABASE_URL + SUPABASE_SERVICE_KEY from env
or the backend .env.

Every workspace owns its own Postgres schema `ws_<slug>` (provisioned by
`public.provision_workspace` in _migrate_workspace_schemas.sql), and
inside that schema the Website Agent owns three tables:

  ws_<workspace>.website_builds     — one row per client: status, stage, urls
  ws_<workspace>.website_docs       — the 6 client documents, versioned per doc_type
  ws_<workspace>.website_build_log  — chat / build / improve / deploy / error events

Rows are scoped to a client via `client_name`; the schema provides the
workspace isolation. The bridge selects its schema through the PostgREST
`Accept-Profile` / `Content-Profile` headers.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from admin.agency.workspace_provision import schema_for

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8050"

# The 6 client-specific documents the Website Agent builds
DOC_TYPES = [
    "business_brief",      # 1. Business Brief — industry, audience, goals
    "site_requirements",   # 2. Site Requirements — pages, features, site type
    "brand_colors",        # 3. Brand Colors — palette, logo, fonts
    "design_system",       # 4. Design System — typography, layout, style
    "content_plan",        # 5. Content Plan — sections, copy, media
    "tech_deploy_plan",    # 6. Tech + Deploy Plan — stack, domain, deploy
]


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    for p in (
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"),
        "/home/ubuntu/sba-backend/.env",
    ):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(key + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return default


def get_config() -> tuple[str, str] | None:
    """Gateway config: PocketBase first, Supabase names as legacy fallback.

    The PocketBase Supabase gateway (`deploy/pb_gateway.py`) speaks the
    PostgREST dialect, so the store/website bridges hit it through these
    vars. POCKETBASE_URL / POCKETBASE_SERVICE_KEY are the current names;
    SUPABASE_URL / SUPABASE_SERVICE_KEY still work for existing deploys.
    """
    url = _env("POCKETBASE_URL", _env("SUPABASE_URL", DEFAULT_URL))
    key = _env("POCKETBASE_SERVICE_KEY", _env("SUPABASE_SERVICE_KEY", ""))
    if not key:
        logger.warning("website_supabase: POCKETBASE_SERVICE_KEY missing, bridge disabled")
        return None
    return url.rstrip("/"), key


def _api(method: str, url: str, key: str, path: str, body: Any = None, timeout: int = 30, on_conflict: str = "", profile: str = "public"):
    """Call Supabase REST API. Returns parsed JSON or [] on empty.

    `profile` selects the PostgREST schema (workspace schema like
    `ws_agency`, or `public`). GET uses Accept-Profile, writes use
    Content-Profile.
    """
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Profile": profile,
        "Content-Profile": profile,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Prefer"] = "return=representation,resolution=merge-duplicates"
        if on_conflict:
            path = path + ("&" if "?" in path else "?") + "on_conflict=" + on_conflict
    req = urllib.request.Request(url + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else []


def _client_q(client: str) -> str:
    """Build client query params (workspace isolation comes from the schema)."""
    return "client_name=eq." + urllib.parse.quote(client)


# ── Website Docs (6 client documents) ───────────────────────────────────

def save_website_doc(
    workspace: str,
    client: str,
    doc_type: str,
    title: str = "",
    content: str = "",
    version: int | None = None,
) -> dict[str, Any] | None:
    """Upsert one of the 6 client documents (versioned per doc_type)."""
    if doc_type not in DOC_TYPES:
        logger.warning("website_supabase: unknown doc_type %s", doc_type)
        return None
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "POST",
            url,
            key,
            "/rest/v1/website_docs",
            {
                "client_name": client,
                "doc_type": doc_type,
                "title": title,
                "content": content,
                "version": version or 1,
                "updated_at": _now_iso(),
            },
            on_conflict="client_name,doc_type",
            profile=schema_for(workspace),
        )
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("website_supabase: save_website_doc failed: %s", e)
        return None


def get_website_docs(workspace: str, client: str) -> list[dict[str, Any]]:
    """Get all 6 docs for a client, newest version each."""
    cfg = get_config()
    if not cfg:
        return []
    url, key = cfg
    try:
        return _api(
            "GET", url, key,
            "/rest/v1/website_docs?select=*&" + _client_q(client),
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("website_supabase: get_website_docs failed: %s", e)
        return []


def get_website_doc(workspace: str, client: str, doc_type: str) -> dict[str, Any] | None:
    rows = get_website_docs(workspace, client)
    return next((r for r in rows if r.get("doc_type") == doc_type), None)


def delete_website_docs(workspace: str, client: str) -> bool:
    cfg = get_config()
    if not cfg:
        return False
    url, key = cfg
    try:
        _api("DELETE", url, key, "/rest/v1/website_docs?" + _client_q(client), profile=schema_for(workspace))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("website_supabase: delete_website_docs failed: %s", e)
        return False


# ── Website Builds ───────────────────────────────────────────────────────

def upsert_website_build(
    workspace: str,
    client: str,
    status: str | None = None,
    current_stage: str | None = None,
    site_url: str | None = None,
    repo_url: str | None = None,
    framework: str | None = None,
) -> dict[str, Any] | None:
    """Create or update the build row for (workspace, client)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    row: dict[str, Any] = {"client_name": client}
    if status is not None:
        row["status"] = status
    if current_stage is not None:
        row["current_stage"] = current_stage
    if site_url is not None:
        row["site_url"] = site_url
    if repo_url is not None:
        row["repo_url"] = repo_url
    if framework is not None:
        row["framework"] = framework
    row["updated_at"] = _now_iso()
    try:
        rows = _api(
            "POST",
            url,
            key,
            "/rest/v1/website_builds",
            row,
            on_conflict="client_name",
            profile=schema_for(workspace),
        )
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("website_supabase: upsert_website_build failed: %s", e)
        return None


def get_website_build(workspace: str, client: str) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/website_builds?select=*&" + _client_q(client),
            profile=schema_for(workspace),
        )
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("website_supabase: get_website_build failed: %s", e)
        return None


# ── Website Build Log ────────────────────────────────────────────────────

def log_website_event(
    workspace: str,
    client: str,
    event_type: str,
    message: str,
    actor: str = "website_agent",
) -> dict[str, Any] | None:
    """Append an event (chat, build_start, build_step, improve, deploy, error)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "POST",
            url,
            key,
            "/rest/v1/website_build_log",
            {
                "client_name": client,
                "event_type": event_type,
                "message": message,
                "actor": actor,
            },
            profile=schema_for(workspace),
        )
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("website_supabase: log_website_event failed: %s", e)
        return None


def get_website_logs(workspace: str, client: str, limit: int = 50) -> list[dict[str, Any]]:
    cfg = get_config()
    if not cfg:
        return []
    url, key = cfg
    try:
        return _api(
            "GET",
            url,
            key,
            "/rest/v1/website_build_log?select=*&"
            + _client_q(client)
            + "&order=created_at.desc&limit=" + str(limit),
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("website_supabase: get_website_logs failed: %s", e)
        return []


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
