"""OAuth 3-legged connect flow for organic channels (P2).

Replaces manual token paste for the OAuth-capable channels:

- **linkedin** — LinkedIn OAuth2 (w_member_social)
- **twitter** — X API v2 OAuth2 with PKCE (tweet.write + offline.access)
- **pinterest** — Pinterest API v5 OAuth2 (boards:read, pins:read, pins:write)
- **reddit** — Reddit OAuth2 authorization code (identity, submit, edit, read)
- **gbp** — Google Business Profile OAuth2 (business.manage)

Flow: ``build_auth_url`` writes a pending-state file and returns the platform
authorize URL. The platform redirects to ``/api/social/organic/oauth/callback``,
``handle_callback`` exchanges the code for tokens, saves them via token_manager
(access_token + refresh_token + expires_at + platform user id/name), then 302s
back to the frontend. ``refresh_access_token`` / ``ensure_fresh_token`` keep
expiring tokens alive from stored refresh tokens.

App credentials (client_id / client_secret) resolve from env vars first
(``OAUTH_<CHANNEL>_CLIENT_ID`` / ``OAUTH_<CHANNEL>_CLIENT_SECRET``), then from
per-workspace app config stored via ``POST /api/social/organic/oauth/app``
(``admin/organic_data/<ws>/oauth_app/<channel>.json``). Secrets never leave the
backend; the browser only sees the authorize URL.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# Channels that support a real 3-legged OAuth flow.
OAUTH_CHANNELS = {"linkedin", "twitter", "pinterest", "reddit", "gbp"}

# Data root — same override as history/scheduler (admin/organic_data by default).
OAUTH_DATA_DIR = Path(
    os.environ.get("ORGANIC_DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "organic_data"))
)

PROVIDERS: dict[str, dict[str, Any]] = {
    "linkedin": {
        "authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        "scopes": ["w_member_social"],
        "pkce": False,
        "userinfo_url": "https://api.linkedin.com/v2/userinfo",
        "user_id_path": ["sub"],
        "user_name_path": ["name"],
        "refresh": True,
    },
    "twitter": {
        "authorize_url": "https://twitter.com/i/oauth2/authorize",
        "token_url": "https://api.twitter.com/2/oauth2/token",
        "scopes": ["tweet.write", "users.read", "offline.access"],
        "pkce": True,
        "userinfo_url": "https://api.twitter.com/2/users/me",
        "user_id_path": ["data", "id"],
        "user_name_path": ["data", "username"],
        "refresh": True,
    },
    "pinterest": {
        "authorize_url": "https://www.pinterest.com/oauth/",
        "token_url": "https://api.pinterest.com/v5/oauth/token",
        "scopes": ["boards:read", "pins:read", "pins:write"],
        "pkce": False,
        "userinfo_url": "https://api.pinterest.com/v5/user_account",
        "user_id_path": ["id"],
        "user_name_path": ["username"],
        "refresh": False,
    },
    "reddit": {
        "authorize_url": "https://www.reddit.com/api/v1/authorize",
        "token_url": "https://www.reddit.com/api/v1/access_token",
        "scopes": ["identity", "submit", "edit", "read"],
        "pkce": False,
        "userinfo_url": "https://oauth.reddit.com/api/v1/me",
        "user_id_path": ["id"],
        "user_name_path": ["name"],
        "refresh": True,
    },
    "gbp": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/business.manage"],
        "pkce": False,
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "user_id_path": ["id"],
        "user_name_path": ["name"],
        "refresh": True,
    },
}

# Channels whose auth header style for token exchange differs (Reddit, Twitter).
_BASIC_AUTH_CHANNELS = {"reddit", "twitter"}
# Channels whose token endpoint uses form params vs JSON body.
_JSON_BODY_CHANNELS: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_dir() -> Path:
    OAUTH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return OAUTH_DATA_DIR


def _pending_dir(workspace_id: str) -> Path:
    d = _data_dir() / workspace_id / "oauth_pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _app_config_dir(workspace_id: str) -> Path:
    d = _data_dir() / workspace_id / "oauth_app"
    d.mkdir(parents=True, exist_ok=True)
    return d


def oauth_supported(channel: str) -> bool:
    return channel in OAUTH_CHANNELS


def redirect_uri() -> str:
    # Read at call time so tests / env changes take effect without re-import.
    base = os.environ.get("OAUTH_REDIRECT_BASE", "https://agency-frontend-seven.vercel.app")
    return f"{base}/api/social/oauth/callback"


# ── App credentials ─────────────────────────────────────────────────────────

def get_app_credentials(workspace_id: str, channel: str) -> tuple[str, str] | None:
    """Return (client_id, client_secret) for a channel, or None if unset.

    Resolution order: env vars (OAUTH_<CHANNEL>_CLIENT_ID / _CLIENT_SECRET),
    then per-workspace app config stored in organic_data/<ws>/oauth_app.
    """
    env_key = channel.upper().replace(" ", "_")
    client_id = os.environ.get(f"OAUTH_{env_key}_CLIENT_ID", "").strip()
    client_secret = os.environ.get(f"OAUTH_{env_key}_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret

    f = _app_config_dir(workspace_id) / f"{channel}.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        cid = (data.get("client_id") or "").strip()
        csec = (data.get("client_secret") or "").strip()
        if cid and csec:
            return cid, csec
    return None


def save_app_config(workspace_id: str, channel: str, config: dict[str, Any]) -> dict[str, Any]:
    """Persist per-workspace OAuth app credentials (client_id/secret)."""
    if not oauth_supported(channel):
        return {"status": "error", "error": f"OAuth not supported for channel: {channel}"}
    client_id = (config.get("client_id") or "").strip()
    client_secret = (config.get("client_secret") or "").strip()
    if not (client_id and client_secret):
        return {"status": "error", "error": "client_id and client_secret are required."}
    f = _app_config_dir(workspace_id) / f"{channel}.json"
    f.write_text(json.dumps({"client_id": client_id, "client_secret": client_secret}, indent=2), encoding="utf-8")
    logger.info("Saved OAuth app config for %s/%s", workspace_id, channel)
    return {"status": "saved", "channel": channel, "workspace_id": workspace_id}


def app_config_status(workspace_id: str) -> dict[str, Any]:
    """Report which OAuth channels have app credentials configured."""
    out: dict[str, Any] = {"workspace_id": workspace_id, "channels": {}}
    for channel in sorted(OAUTH_CHANNELS):
        creds = get_app_credentials(workspace_id, channel)
        out["channels"][channel] = {
            "id": channel,
            "configured": creds is not None,
            "source": "env" if creds and _creds_from_env(channel) else ("workspace" if creds else None),
            "scopes": PROVIDERS[channel]["scopes"],
        }
    return out


def _creds_from_env(channel: str) -> bool:
    env_key = channel.upper().replace(" ", "_")
    return bool(os.environ.get(f"OAUTH_{env_key}_CLIENT_ID") and os.environ.get(f"OAUTH_{env_key}_CLIENT_SECRET"))


# ── PKCE ────────────────────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    """Generate (code_verifier, code_challenge) for PKCE flows (Twitter)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── Auth URL ────────────────────────────────────────────────────────────────

def build_auth_url(workspace_id: str, channel: str) -> dict[str, Any]:
    """Start an OAuth flow: persist state, return the platform authorize URL.

    Returns ``{"status": "ok", "auth_url", "state", "channel", "workspace_id"}``
    or an error dict when the channel is unsupported or app creds are missing.
    """
    if not oauth_supported(channel):
        return {"status": "error", "error": f"OAuth not supported for channel: {channel}"}
    provider = PROVIDERS[channel]
    creds = get_app_credentials(workspace_id, channel)
    if creds is None:
        return {
            "status": "error",
            "error": f"No OAuth app configured for {channel}. Set OAUTH_{channel.upper()}_CLIENT_ID/SECRET env vars or save per-workspace app config.",
        }
    client_id, _client_secret = creds

    state = uuid.uuid4().hex
    pkce_verifier = None
    pkce_challenge = None
    if provider.get("pkce"):
        pkce_verifier, pkce_challenge = _pkce_pair()

    # Persist the pending flow so the callback can validate state + workspace.
    pending = {
        "state": state,
        "channel": channel,
        "workspace_id": workspace_id,
        "pkce_verifier": pkce_verifier,
        "created_at": _now(),
    }
    _pending_dir(workspace_id).joinpath(f"{state}.json").write_text(
        json.dumps(pending, indent=2), encoding="utf-8"
    )

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(provider["scopes"]),
        "state": state,
    }
    if pkce_challenge:
        params["code_challenge"] = pkce_challenge
        params["code_challenge_method"] = "S256"

    sep = "&" if "?" in provider["authorize_url"] else "?"
    auth_url = f"{provider['authorize_url']}{sep}" + "&".join(f"{k}={_quote(str(v))}" for k, v in params.items())
    return {"status": "ok", "auth_url": auth_url, "state": state, "channel": channel, "workspace_id": workspace_id}


def _quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


def _pop_pending(state: str) -> dict[str, Any] | None:
    """Load + delete a pending flow file by state. Returns None if unknown."""
    for ws_dir in _data_dir().iterdir():
        if not ws_dir.is_dir():
            continue
        f = ws_dir / "oauth_pending" / f"{state}.json"
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            f.unlink(missing_ok=True)
            return data
    return None


# ── Token exchange ──────────────────────────────────────────────────────────

def _exchange_code(
    channel: str, code: str, client_id: str, client_secret: str, code_verifier: str | None
) -> dict[str, Any]:
    """Exchange an authorization code for tokens. Raises on provider error."""
    provider = PROVIDERS[channel]
    data: dict[str, Any] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
    }
    headers = {"User-Agent": "AgencyOrganicBot/1.0", "Accept": "application/json"}
    auth = None

    if channel in _BASIC_AUTH_CHANNELS:
        auth = (client_id, client_secret)
    else:
        data["client_id"] = client_id
        data["client_secret"] = client_secret

    if code_verifier:
        data["code_verifier"] = code_verifier

    if channel in _JSON_BODY_CHANNELS:
        headers["Content-Type"] = "application/json"
        resp = requests.post(provider["token_url"], json=data, headers=headers, auth=auth, timeout=30)
    else:
        resp = requests.post(provider["token_url"], data=data, headers=headers, auth=auth, timeout=30)

    if not resp.ok:
        raise RuntimeError(f"{channel} token exchange failed: HTTP {resp.status_code} {resp.text[:300]}")
    j = resp.json()
    access_token = j.get("access_token", "")
    if not access_token:
        raise RuntimeError(f"{channel} token exchange returned no access_token: {json.dumps(j)[:300]}")
    return j


def _extract_userinfo(channel: str, token: str) -> tuple[str, str]:
    """Fetch platform user id + display name. Returns ("", "") if unavailable."""
    provider = PROVIDERS.get(channel)
    url = provider.get("userinfo_url") if provider else None
    if not url:
        return "", ""
    try:
        headers = {"Authorization": f"Bearer {token}", "User-Agent": "AgencyOrganicBot/1.0"}
        resp = requests.get(url, headers=headers, timeout=20)
        if not resp.ok:
            logger.warning("%s userinfo failed: HTTP %s", channel, resp.status_code)
            return "", ""
        j = resp.json()
        uid = j
        for part in provider.get("user_id_path", []):
            if isinstance(uid, dict):
                uid = uid.get(part, "")
        name = j
        for part in provider.get("user_name_path", []):
            if isinstance(name, dict):
                name = name.get(part, "")
        return str(uid or ""), str(name or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s userinfo error: %s", channel, exc)
        return "", ""


def handle_callback(channel: str, state: str, code: str = "", error: str = "") -> dict[str, Any]:
    """Complete an OAuth flow from the platform callback.

    Validates the pending state, exchanges the code, saves the token, and
    returns a dict with ``redirect`` (frontend URL) or a plain error result.
    """
    if error:
        return {"status": "error", "error": f"OAuth denied: {error}", "redirect": _result_url(channel, False, error)}
    pending = _pop_pending(state)
    if pending is None:
        return {"status": "error", "error": "OAuth state unknown or expired.", "redirect": _result_url(channel, False, "state")}
    if channel != pending.get("channel"):
        return {"status": "error", "error": "OAuth channel mismatch.", "redirect": _result_url(channel, False, "mismatch")}
    workspace_id = pending.get("workspace_id", "default")
    if not code:
        return {"status": "error", "error": "OAuth callback missing code.", "redirect": _result_url(channel, False, "code")}

    creds = get_app_credentials(workspace_id, channel)
    if creds is None:
        return {"status": "error", "error": f"No OAuth app configured for {channel} at callback time.", "redirect": _result_url(channel, False, "app")}
    client_id, client_secret = creds

    try:
        j = _exchange_code(channel, code, client_id, client_secret, pending.get("pkce_verifier"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("OAuth exchange failed for %s", channel)
        return {"status": "error", "error": str(exc)[:300], "redirect": _result_url(channel, False, "exchange")}

    access_token = j.get("access_token", "")
    refresh_token = j.get("refresh_token", "")
    expires_in = j.get("expires_in")
    expires_at = ""
    if expires_in:
        try:
            expires_at = datetime.now(timezone.utc).timestamp() + float(expires_in)
            expires_at = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            expires_at = ""

    uid, uname = _extract_userinfo(channel, access_token)

    from admin.token_manager import save_token
    save_token(
        workspace_id=workspace_id,
        platform=channel,
        access_token=access_token,
        refresh_token=refresh_token,
        platform_user_id=uid,
        platform_username=uname,
        expires_at=expires_at,
        token_type="oauth",
        scopes=(j.get("scope") or " ").split(),
    )
    logger.info("OAuth connected: %s/%s (user=%s)", workspace_id, channel, uname or uid)
    return {
        "status": "connected",
        "channel": channel,
        "workspace_id": workspace_id,
        "user": uname or uid,
        "redirect": _result_url(channel, True, "", user=uname or uid),
    }


def _result_url(channel: str, success: bool, error: str = "", user: str = "") -> str:
    base = os.environ.get("OAUTH_FRONTEND_URL", "https://agency-frontend-seven.vercel.app/admin/social")
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}oauth={channel}&success={'1' if success else '0'}"
    if error:
        url += f"&error={error}"
    if user:
        url += f"&user={quote(user, safe='')}"
    return url


# ── Token refresh ───────────────────────────────────────────────────────────

def _token_file(workspace_id: str, channel: str) -> Path:
    d = Path(os.environ.get("TAGS_TOKENS_DIR", "data/social_tokens")) / workspace_id
    return d / f"{channel}.json"


def refresh_access_token(workspace_id: str, channel: str) -> dict[str, Any]:
    """Refresh an expiring/expired OAuth token using its stored refresh_token.

    Returns ``{"status": "refreshed" | "ok" | "error", ...}``.
    """
    if not oauth_supported(channel):
        return {"status": "error", "error": f"OAuth not supported for channel: {channel}"}
    provider = PROVIDERS[channel]
    if not provider.get("refresh"):
        return {"status": "ok", "error": f"{channel} tokens are long-lived; no refresh flow."}

    f = _token_file(workspace_id, channel)
    if not f.exists():
        return {"status": "error", "error": f"No token stored for {channel}."}
    try:
        token_data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "error": f"Token file unreadable for {channel}."}

    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        return {"status": "error", "error": f"No refresh_token stored for {channel}."}

    creds = get_app_credentials(workspace_id, channel)
    if creds is None:
        return {"status": "error", "error": f"No OAuth app configured for {channel}; cannot refresh."}
    client_id, client_secret = creds

    data: dict[str, Any] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    headers = {"User-Agent": "AgencyOrganicBot/1.0", "Accept": "application/json"}
    auth = None
    if channel in _BASIC_AUTH_CHANNELS:
        auth = (client_id, client_secret)
    else:
        data["client_id"] = client_id
        data["client_secret"] = client_secret

    try:
        if channel in _JSON_BODY_CHANNELS:
            headers["Content-Type"] = "application/json"
            resp = requests.post(provider["token_url"], json=data, headers=headers, auth=auth, timeout=30)
        else:
            resp = requests.post(provider["token_url"], data=data, headers=headers, auth=auth, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{channel} refresh request failed: {exc}"}

    if not resp.ok:
        return {"status": "error", "error": f"{channel} refresh failed: HTTP {resp.status_code} {resp.text[:300]}"}
    j = resp.json()
    new_access = j.get("access_token", "")
    if not new_access:
        return {"status": "error", "error": f"{channel} refresh returned no access_token."}

    new_refresh = j.get("refresh_token") or refresh_token
    expires_in = j.get("expires_in")
    expires_at = ""
    if expires_in:
        try:
            expires_at = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + float(expires_in), tz=timezone.utc
            ).isoformat()
        except (TypeError, ValueError, OverflowError):
            expires_at = ""

    token_data["access_token"] = new_access
    token_data["refresh_token"] = new_refresh
    token_data["expires_at"] = expires_at or token_data.get("expires_at", "")
    token_data["status"] = "active"
    token_data["last_used_at"] = _now()
    token_data["updated_at"] = _now()
    f.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    logger.info("Refreshed OAuth token for %s/%s", workspace_id, channel)
    return {"status": "refreshed", "channel": channel, "workspace_id": workspace_id}


def ensure_fresh_token(workspace_id: str, channel: str) -> dict[str, Any]:
    """Refresh a token if it is expired or expiring soon (<= 1h). No-op if fine."""
    f = _token_file(workspace_id, channel)
    if not f.exists():
        return {"status": "ok", "reason": "no_token"}
    try:
        token_data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "error": f"Token file unreadable for {channel}."}

    if not token_data.get("refresh_token"):
        return {"status": "ok", "reason": "no_refresh"}
    expires_at = token_data.get("expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            # Refresh if expired or under 1 hour left.
            if (exp - now).total_seconds() > 3600:
                return {"status": "ok", "reason": "fresh"}
        except ValueError:
            return {"status": "ok", "reason": "no_expiry"}
    return refresh_access_token(workspace_id, channel)
