"""Channel credential + setup helpers for the organic engine.

Every organic channel can be connected by a client through a guided flow:

- **token channels** (linkedin, twitter, pinterest, gbp): client pastes the
  OAuth access token → saved via token_manager (admin/tokens/<ws>/<channel>.json).
- **reddit**: script-app creds (client_id, client_secret, username, password)
  stored as token (client_id → platform_user_id, username → platform_username)
  plus channel config for client_secret/password.
- **telegram**: bot_token verified via Bot API getMe, then saved as token;
  chat_id goes to channel config.
- **facebook**: either a Chrome profile_dir path, or client email/password →
  ChromeTool browser login persists the session in a per-workspace profile dir.

channel_setup_status(workspace_id) reports per-channel state so the UI can
show "connect me" badges and missing fields.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

from admin.tools.organic.base import CHANNEL_TYPE_API, CHANNEL_TYPE_BROWSER
from admin.tools.organic.config import get_channel_config, save_channel_config
from admin.tools.organic.facebook_browser import _new_chrome
from admin.tools.organic.registry import get_channel
from admin.token_manager import get_active_token, save_token

logger = logging.getLogger(__name__)

# Channels that store their credentials as a plain access token.
TOKEN_CHANNELS = ["linkedin", "twitter", "pinterest", "gbp"]
# Channel whose token needs extra config fields alongside.
TOKEN_WITH_CONFIG = {"gbp": ["location_name"]}

# Required credential fields per channel, in the order the UI shows them.
CREDENTIAL_FIELDS: dict[str, list[dict]] = {
    "linkedin": [
        {"name": "access_token", "label": "Access token", "type": "password"},
        {"name": "platform_user_id", "label": "Author URN (optional, e.g. abc123)", "type": "text", "optional": True},
    ],
    "twitter": [
        {"name": "access_token", "label": "Bearer / OAuth2 token", "type": "password"},
    ],
    "pinterest": [
        {"name": "access_token", "label": "Access token", "type": "password"},
    ],
    "gbp": [
        {"name": "access_token", "label": "Access token", "type": "password"},
        {"name": "location_name", "label": "Location name (accounts/.../locations/...)", "type": "text"},
    ],
    "reddit": [
        {"name": "client_id", "label": "Client ID", "type": "text"},
        {"name": "client_secret", "label": "Client secret", "type": "password"},
        {"name": "username", "label": "Reddit username", "type": "text"},
        {"name": "password", "label": "Reddit password", "type": "password"},
        {"name": "subreddits", "label": "Default subreddits (comma separated)", "type": "text", "optional": True},
    ],
    "telegram": [
        {"name": "bot_token", "label": "Bot token (from @BotFather)", "type": "password"},
        {"name": "chat_id", "label": "Chat/group ID (optional)", "type": "text", "optional": True},
    ],
    "facebook": [
        {"name": "profile_dir", "label": "Chrome profile path (optional)", "type": "text", "optional": True},
        {"name": "email", "label": "Facebook email (for browser login)", "type": "text", "optional": True},
        {"name": "password", "label": "Facebook password (for browser login)", "type": "password", "optional": True},
        {"name": "default_groups", "label": "Default group URLs (comma separated)", "type": "text", "optional": True},
        {"name": "default_images", "label": "Default image URLs (comma separated)", "type": "text", "optional": True},
    ],
}


def _fb_default_profile_dir(workspace_id: str) -> str:
    return os.path.expanduser(f"~/.sba-fb-profile-{workspace_id}")


def _run_async(coro) -> dict[str, Any]:
    """Run an async browser coroutine from sync context (see facebook_browser)."""
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def _runner() -> None:
        box["result"] = asyncio.run(coro)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    return box["result"]


# ── Status ─────────────────────────────────────────────────────────────────

def channel_setup_status(workspace_id: str) -> dict[str, Any]:
    """Report connect state for every organic channel in this workspace."""
    from admin.tools.organic.oauth import oauth_supported
    from admin.tools.organic.registry import list_channels

    out: dict[str, Any] = {"workspace_id": workspace_id, "channels": {}}
    for ch in list_channels():
        cid = ch["id"]
        state = _channel_state(workspace_id, cid)
        out["channels"][cid] = {
            "id": cid,
            "name": ch["name"],
            "type": ch["type"],
            "auth": ch["auth"],
            "oauth": oauth_supported(cid),
            "connected": state["connected"],
            "missing": state["missing"],
            "fields": CREDENTIAL_FIELDS.get(cid, []),
            "instructions": _instructions(cid),
        }
    out["connected_count"] = sum(1 for c in out["channels"].values() if c["connected"])
    out["total"] = len(out["channels"])
    return out


def _channel_state(workspace_id: str, channel_id: str) -> dict[str, Any]:
    cfg = get_channel_config(workspace_id, channel_id)
    meta = get_channel(channel_id)
    missing: list[str] = []

    if channel_id in TOKEN_CHANNELS:
        token = get_active_token(workspace_id, channel_id)
        if not token or not token.get("access_token"):
            missing.append("access_token")
        if channel_id == "gbp" and not cfg.get("location_name"):
            missing.append("location_name")
    elif channel_id == "reddit":
        token = get_active_token(workspace_id, channel_id)
        if not token:
            missing.append("client_id")
        if not cfg.get("client_secret"):
            missing.append("client_secret")
        if not cfg.get("password"):
            missing.append("password")
        if not (token or {}).get("platform_username") and not cfg.get("username"):
            missing.append("username")
    elif channel_id == "telegram":
        token = get_active_token(workspace_id, channel_id)
        if not token or not token.get("access_token"):
            missing.append("bot_token")
        if not cfg.get("chat_id"):
            missing.append("chat_id")
    elif channel_id == "facebook":
        if not cfg.get("profile_dir"):
            missing.append("profile_dir")

    connected = (meta or {}).get("type") == CHANNEL_TYPE_API and not missing
    if (meta or {}).get("type") == CHANNEL_TYPE_BROWSER:
        connected = not missing
    return {"connected": connected, "missing": missing}


def _instructions(channel_id: str) -> list[str]:
    text = {
        "linkedin": [
            "Go to LinkedIn Developer portal > your app > Auth > Access tokens.",
            "Generate an OAuth2 user token (w_r_member_profile scope).",
            "Paste the token below. Optional: author URN for company-page posting.",
        ],
        "twitter": [
            "Create an app in the X developer portal.",
            "Generate an OAuth2 token with tweet.write scope.",
            "Paste the bearer/user token below.",
        ],
        "pinterest": [
            "Create an app at developers.pinterest.com.",
            "Authorize and copy the access token (boards:read, pins:write).",
            "Paste the token below.",
        ],
        "gbp": [
            "Enable Google Business Profile API and create credentials.",
            "Authorize the business account and copy the access token.",
            "Paste token + the location resource name (accounts/.../locations/...).",
        ],
        "reddit": [
            "Create a script app at reddit.com/prefs/apps.",
            "Copy Client ID (under the app name) + secret.",
            "Use your Reddit username/password. Paste all four below.",
        ],
        "telegram": [
            "Message @BotFather on Telegram → /newbot → get your bot token.",
            "Paste the bot token. Add the bot to your group/channel and get its chat ID.",
            "Optionally paste the chat ID; otherwise posts need it in the payload.",
        ],
        "facebook": [
            "Option A: paste a Chrome profile path that is already logged into Facebook.",
            "Option B: give Facebook email + password once — we log in and save the session in a per-workspace profile (never stored in plain text).",
            "Add default group URLs (comma separated) so posts without a target use them.",
        ],
    }
    return text.get(channel_id, ["Connect this channel."])


# ── Save credentials ───────────────────────────────────────────────────────

def save_channel_credentials(workspace_id: str, channel: str, data: dict[str, Any]) -> dict[str, Any]:
    """Save client credentials for one organic channel.

    data keys follow CREDENTIAL_FIELDS. Returns a status dict. For facebook
    email/password mode this triggers a real browser login (may take ~15s).
    """
    if channel not in CREDENTIAL_FIELDS:
        return {"status": "error", "channel": channel, "error": f"Unknown channel: {channel}"}

    if channel in TOKEN_CHANNELS:
        return _save_token_channel(workspace_id, channel, data)
    if channel == "reddit":
        return _save_reddit(workspace_id, data)
    if channel == "telegram":
        return _save_telegram(workspace_id, data)
    if channel == "facebook":
        return _save_facebook(workspace_id, data)
    return {"status": "error", "channel": channel, "error": f"Unsupported channel: {channel}"}


def _save_token_channel(workspace_id: str, channel: str, data: dict[str, Any]) -> dict[str, Any]:
    token = (data.get("access_token") or "").strip()
    if not token:
        return {"status": "error", "channel": channel, "error": "access_token is required."}
    save_token(
        workspace_id=workspace_id,
        platform=channel,
        access_token=token,
        platform_user_id=(data.get("platform_user_id") or "").strip(),
        platform_username=(data.get("platform_username") or "").strip(),
    )
    cfg = {}
    if channel == "gbp" and data.get("location_name"):
        cfg["location_name"] = data["location_name"].strip()
    if cfg:
        save_channel_config(workspace_id, channel, cfg)
    return {"status": "connected", "channel": channel, "mode": "token"}


def _save_reddit(workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in ("client_id", "client_secret", "username", "password") if not data.get(k)]
    if missing:
        return {"status": "error", "channel": "reddit", "error": f"Missing: {', '.join(missing)}."}
    save_token(
        workspace_id=workspace_id,
        platform="reddit",
        access_token=data["client_secret"],  # used by reddit_api for OAuth auth
        platform_user_id=data["client_id"],
        platform_username=data["username"],
    )
    cfg = {"client_secret": data["client_secret"], "username": data["username"], "password": data["password"]}
    if data.get("subreddits"):
        cfg["subreddits"] = [s.strip().lstrip("r/") for s in str(data["subreddits"]).split(",") if s.strip()]
    save_channel_config(workspace_id, "reddit", cfg)
    return {"status": "connected", "channel": "reddit", "mode": "script_app"}


def _save_telegram(workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    bot_token = (data.get("bot_token") or "").strip()
    if not bot_token:
        return {"status": "error", "channel": "telegram", "error": "bot_token is required."}

    try:
        resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=15)
        if not resp.ok:
            return {"status": "error", "channel": "telegram", "error": f"Bot token rejected by Telegram (HTTP {resp.status_code})."}
        bot_info = resp.json().get("result", {})
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "channel": "telegram", "error": f"Telegram verify failed: {exc}"}

    save_token(
        workspace_id=workspace_id,
        platform="telegram",
        access_token=bot_token,
        platform_username=bot_info.get("username", ""),
        platform_user_id=str(bot_info.get("id", "")),
    )
    cfg = {}
    if data.get("chat_id"):
        cfg["chat_id"] = str(data["chat_id"]).strip()
    if cfg:
        save_channel_config(workspace_id, "telegram", cfg)
    return {"status": "connected", "channel": "telegram", "mode": "bot", "bot": bot_info.get("username", "")}


def _save_facebook(workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    profile_dir = (data.get("profile_dir") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if profile_dir and not os.path.exists(profile_dir):
        try:
            os.makedirs(profile_dir, exist_ok=True)
        except OSError as exc:
            return {"status": "error", "channel": "facebook", "error": f"Cannot create profile_dir: {exc}"}

    if not profile_dir:
        if not (email and password):
            return {"status": "error", "channel": "facebook",
                    "error": "Provide profile_dir or Facebook email + password."}
        profile_dir = _fb_default_profile_dir(workspace_id)
        os.makedirs(profile_dir, exist_ok=True)

        chrome = _new_chrome(profile_dir)
        try:
            result = _run_async(chrome.facebook_login(email, password))
        finally:
            _run_async(chrome.close())
        if "error" in result:
            return {"status": "error", "channel": "facebook", "error": result["error"]}

    cfg: dict[str, Any] = {"profile_dir": profile_dir}
    if data.get("default_groups"):
        cfg["default_groups"] = [g.strip() for g in str(data["default_groups"]).split(",") if g.strip()]
    if data.get("default_images"):
        cfg["default_images"] = [u.strip() for u in str(data["default_images"]).split(",") if u.strip()]
    save_channel_config(workspace_id, "facebook", cfg)
    return {"status": "connected", "channel": "facebook", "mode": "browser", "profile_dir": profile_dir}
