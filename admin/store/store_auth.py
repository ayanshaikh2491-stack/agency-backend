"""Client Store Auth — workspace-scoped client accounts + signed tokens.

Accounts live in `{schema}__store_accounts` (PocketBase via the gateway).
Passwords are hashed with a per-account salt (sha256, no heavy deps).
Login issues a short-lived HMAC-signed token; every store mutation verifies
the token and binds the caller to one workspace (schema isolation + row
scope), so a client can only touch their own store.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from typing import Any

from admin.agency.website_supabase import _api, get_config
from admin.agency.workspace_provision import schema_for

logger = logging.getLogger(__name__)

ACCOUNTS_TABLE = "store_accounts"

TOKEN_TTL = 60 * 60 * 24 * 7  # 7 days


def _secret() -> str:
    """HMAC secret: env override, else stable local fallback."""
    return os.environ.get("STORE_TOKEN_SECRET", "tags-agency-store-sig-v1")


def _client_q(client: str) -> str:
    return "client_name=eq." + urllib.parse.quote(client)


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + "::" + password).encode()).hexdigest()


def _new_salt() -> str:
    return hashlib.sha256(os.urandom(16)).hexdigest()[:16]


# ── Accounts ────────────────────────────────────────────────────────────────

def create_account(workspace: str, client: str, email: str, password: str, name: str = "") -> dict[str, Any] | None:
    """Create a client account for (workspace, client). Returns account or None."""
    cfg = get_config()
    if not cfg:
        return None
    email = (email or "").strip().lower()
    if "@" not in email or len(password or "") < 4:
        return None
    url, key = cfg
    salt = _new_salt()
    try:
        rows = _api(
            "POST", url, key,
            "/rest/v1/" + ACCOUNTS_TABLE,
            {
                "client_name": client,
                "email": email,
                "name": (name or "").strip() or email.split("@")[0],
                "password_hash": _hash_password(password, salt),
                "salt": salt,
                "active": True,
            },
            profile=schema_for(workspace),
        )
        return _account_out(rows[0]) if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("store_auth: create_account failed: %s", e)
        return None


def find_account(workspace: str, client: str, email: str) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    email = (email or "").strip().lower()
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + ACCOUNTS_TABLE + "?select=*&" + _client_q(client),
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store_auth: find_account failed: %s", e)
        return None
    for r in rows:
        if (r.get("email") or "").strip().lower() == email:
            return dict(r)
    return None


def verify_login(workspace: str, client: str, email: str, password: str) -> dict[str, Any] | None:
    """Check email/password against the client account. Returns account or None."""
    account = find_account(workspace, client, email)
    if not account or not account.get("active", True):
        return None
    expected = _hash_password(password or "", account.get("salt") or "")
    if not hmac.compare_digest(expected, account.get("password_hash") or ""):
        return None
    return _account_out(account)


def _account_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": row.get("email") or "",
        "name": row.get("name") or "",
        "client": row.get("client_name") or "",
        "active": bool(row.get("active", True)),
    }


# ── Tokens ──────────────────────────────────────────────────────────────────

def issue_token(workspace: str, client: str, email: str, name: str = "") -> str:
    payload = {
        "ws": workspace,
        "client": client,
        "email": email,
        "name": name,
        "exp": int(time.time()) + TOKEN_TTL,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(
        hmac.new(_secret().encode(), raw.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return raw + "." + sig


def verify_token(token: str) -> dict[str, Any] | None:
    """Verify an HMAC token. Returns payload or None if invalid/expired."""
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    expected = base64.urlsafe_b64encode(
        hmac.new(_secret().encode(), raw.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:  # noqa: BLE001
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def token_workspace(token: str, expected_workspace: str | None = None) -> dict[str, Any] | None:
    """Verify token and (optionally) pin it to a workspace."""
    payload = verify_token(token)
    if not payload:
        return None
    if expected_workspace and payload.get("ws") != expected_workspace:
        return None
    return payload


# ── Sales stats (SBA pipeline for this workspace) ───────────────────────────

def sales_stats(workspace: str, client: str) -> dict[str, Any]:
    """Count SBA pipeline rows scoped to this workspace/client.

    Prefers the live pipeline store (PocketBase via gateway). Rows are
    matched on workspace_name / category / client_name when present.
    """
    stats = {"leads": 0, "new": 0, "contacted": 0, "hot": 0, "meetings": 0, "source": "none"}
    try:
        from admin.agency import sba_pipeline
        leads = sba_pipeline.load_leads_preferred()
    except Exception as e:  # noqa: BLE001
        logger.warning("store_auth: sales_stats load failed: %s", e)
        return {**stats, "source": "error"}
    if not leads:
        return {**stats, "source": "empty"}

    ws_norm = (workspace or "").strip().lower()
    client_norm = (client or "").strip().lower()

    def matches(l: dict) -> bool:
        if not ws_norm and not client_norm:
            return True
        return any(
            ws_norm and ws_norm in str(l.get(k) or "").lower()
            for k in ("workspace_name", "workspace_id", "category", "source")
        ) or any(
            client_norm and client_norm in str(l.get(k) or "").lower()
            for k in ("client_name", "business_name", "name")
        )

    scoped = [l for l in leads if matches(l)]
    stats["leads"] = len(scoped)
    for l in scoped:
        status = (l.get("status") or "new").lower()
        if status in ("new", "candidate", "good"):
            stats["new"] += 1
        elif status in ("contacted", "email_sent", "replied"):
            stats["contacted"] += 1
        if status in ("hot", "interested", "yes"):
            stats["hot"] += 1
        if status in ("meeting", "meeting_scheduled", "booked"):
            stats["meetings"] += 1
    stats["source"] = "pipeline"
    return stats
