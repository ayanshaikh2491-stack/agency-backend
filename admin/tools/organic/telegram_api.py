# admin/tools/organic/telegram_api.py
"""Telegram posting via Bot API."""
from __future__ import annotations

import logging

import requests

from admin.tools.organic.base import CHANNEL_TYPE_API, PostResult
from admin.tools.organic.config import get_channel_config
from admin.token_manager import get_active_token

logger = logging.getLogger(__name__)

CHANNEL_META = {
    "id": "telegram",
    "name": "Telegram",
    "type": CHANNEL_TYPE_API,
    "auth": "token",
    "capabilities": ["post"],
    "required_fields": ["text"],
    "description": "Send message/photo to a bot channel or group.",
}


def post(workspace_id: str, payload: dict) -> PostResult:
    token_data = get_active_token(workspace_id, "telegram")
    # token_manager.get_active_token returns a bare token string (or None),
    # while tests mock a dict. Normalize so both shapes work.
    if isinstance(token_data, str):
        token_data = {"access_token": token_data}
    bot_token = (token_data or {}).get("access_token", "")
    cfg = get_channel_config(workspace_id, "telegram")
    chat_id = cfg.get("chat_id", "")

    if not bot_token:
        return PostResult(status="config_missing", channel="telegram", error="No Telegram bot token.")
    if not chat_id:
        return PostResult(status="config_missing", channel="telegram", error="No chat_id in config.")

    text = payload.get("text", "")

    try:
        base = f"https://api.telegram.org/bot{bot_token}"
        if payload.get("photo_url"):
            resp = requests.post(
                f"{base}/sendPhoto",
                data={"chat_id": chat_id, "caption": text},
                files={"photo": requests.get(payload["photo_url"], timeout=30).content},
                timeout=60,
            )
        else:
            resp = requests.post(
                f"{base}/sendMessage",
                data={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
                timeout=30,
            )
        if resp.ok:
            j = resp.json()
            message_id = str(j.get("result", {}).get("message_id", ""))
            # Public t.me/c/ URLs only work for supergroups (-100...). Strip the
            # -100 prefix explicitly so only the marker is removed, never a digit.
            public_chat = chat_id[4:] if chat_id.startswith("-100") and chat_id[4:].isdigit() else chat_id
            return PostResult(channel="telegram", post_id=message_id, post_url=f"https://t.me/c/{public_chat}/{message_id}" if message_id else "")
        return PostResult(status="error", channel="telegram", error=resp.text[:300])
    except Exception as e:
        return PostResult(status="error", channel="telegram", error=str(e)[:300])
