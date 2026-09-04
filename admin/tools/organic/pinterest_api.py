"""Pinterest pin creation via REST API."""
from __future__ import annotations

import logging

import requests

from admin.tools.organic.base import CHANNEL_TYPE_API, PostResult
from admin.token_manager import get_active_token

logger = logging.getLogger(__name__)

CHANNEL_META = {
    "id": "pinterest",
    "name": "Pinterest",
    "type": CHANNEL_TYPE_API,
    "auth": "token",
    "capabilities": ["post"],
    "required_fields": ["title", "image_url", "board_id"],
    "description": "Create a pin on a board.",
}


def post(workspace_id: str, payload: dict) -> PostResult:
    token_data = get_active_token(workspace_id, "pinterest")
    if not token_data:
        return PostResult(status="config_missing", channel="pinterest", error="No Pinterest token.")

    # Interface compat: admin.token_manager.get_active_token returns a bare
    # access-token string (or None), while tests mock it as a dict. Normalize
    # to the dict shape used below so real string tokens don't crash.
    if isinstance(token_data, str):
        token_data = {"access_token": token_data}

    token = token_data.get("access_token", "")
    if not token:
        return PostResult(status="config_missing", channel="pinterest", error="Empty Pinterest token.")

    body = {
        "board_id": payload["board_id"],
        "media_source": {"source_type": "image_url", "url": payload["image_url"]},
        "title": payload.get("title", ""),
        "description": payload.get("description", payload.get("title", "")),
        "link": payload.get("link", ""),
    }

    try:
        resp = requests.post(
            "https://api-sandbox.pinterest.com/v5/pins",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if resp.ok:
            pin_id = resp.json().get("id", "")
            return PostResult(channel="pinterest", post_id=pin_id, post_url=f"https://www.pinterest.com/pin/{pin_id}" if pin_id else "")
        return PostResult(status="error", channel="pinterest", error=resp.text[:300])
    except Exception as e:
        return PostResult(status="error", channel="pinterest", error=str(e)[:300])
