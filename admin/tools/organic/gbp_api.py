"""Google Business Profile local post creation.

Payload fields consumed: ``summary`` (required), ``location_name``
(required unless set in channel config), ``link``, ``topic_type``
(default ``STANDARD``). The plan brief also listed offer/event fields, but
the v4 localPosts API consumes only these four, so that is the supported
surface. ``location_name`` is the resource name, e.g.
``accounts/123/locations/456`` (set it in the payload or channel config).
"""
from __future__ import annotations

import logging

import requests

from admin.tools.organic.base import CHANNEL_TYPE_API, PostResult
from admin.tools.organic.config import get_channel_config
from admin.token_manager import get_active_token

logger = logging.getLogger(__name__)

CHANNEL_META = {
    "id": "gbp",
    "name": "Google Business Profile",
    "type": CHANNEL_TYPE_API,
    "auth": "token",
    "capabilities": ["post"],
    "required_fields": ["summary", "location_name"],
    "description": "Create a local business post (offer/event/update). location_name required (payload or channel config).",
}


def post(workspace_id: str, payload: dict) -> PostResult:
    token_data = get_active_token(workspace_id, "gbp")
    if not token_data:
        return PostResult(status="config_missing", channel="gbp", error="No GBP token.")

    # Interface compat: admin.token_manager.get_active_token returns a bare
    # access-token string (or None), while tests mock it as a dict. Normalize
    # to the dict shape used below so real string tokens don't crash.
    if isinstance(token_data, str):
        token_data = {"access_token": token_data}

    token = token_data.get("access_token", "")
    cfg = get_channel_config(workspace_id, "gbp")
    location_name = payload.get("location_name") or cfg.get("location_name", "")
    if not token:
        return PostResult(status="config_missing", channel="gbp", error="Empty GBP token.")
    if not location_name:
        return PostResult(status="config_missing", channel="gbp", error="No location_name. Set it in channel config.")

    body = {
        "summary": payload["summary"],
        "callToAction": {"actionType": "LEARN_MORE", "url": payload.get("link", "")} if payload.get("link") else None,
        "topicType": payload.get("topic_type", "STANDARD"),
    }
    body = {k: v for k, v in body.items() if v is not None}

    try:
        resp = requests.post(
            f"https://mybusiness.googleapis.com/v4/{location_name}/localPosts",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if resp.ok:
            post_name = resp.json().get("name", "")
            return PostResult(channel="gbp", post_id=post_name)
        return PostResult(status="error", channel="gbp", error=resp.text[:300])
    except Exception as e:
        return PostResult(status="error", channel="gbp", error=str(e)[:300])
