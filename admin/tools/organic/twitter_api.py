# admin/tools/organic/twitter_api.py
"""X / Twitter posting via API v2."""
from __future__ import annotations

import logging

import requests

from admin.tools.organic.base import CHANNEL_TYPE_API, PostResult
from admin.token_manager import get_active_token

logger = logging.getLogger(__name__)

CHANNEL_META = {
    "id": "twitter",
    "name": "X / Twitter",
    "type": CHANNEL_TYPE_API,
    "auth": "token",
    "capabilities": ["post", "reply"],
    "required_fields": ["text"],
    "description": "Post tweet or reply via X API v2.",
}

TWITTER_API_URL = "https://api.twitter.com/2/tweets"


def post(workspace_id: str, payload: dict) -> PostResult:
    token_data = get_active_token(workspace_id, "twitter")
    if not token_data:
        return PostResult(status="config_missing", channel="twitter", error="No X/Twitter token.")

    # Interface compat: admin.token_manager.get_active_token returns a bare
    # access-token string (or None), while tests mock it as a dict. Normalize
    # to the dict shape used below so real string tokens don't crash.
    if isinstance(token_data, str):
        token_data = {"access_token": token_data}

    token = token_data.get("access_token", "")
    if not token:
        return PostResult(status="config_missing", channel="twitter", error="Empty X/Twitter token.")

    text = payload.get("text", "")
    if not text:
        return PostResult(status="error", channel="twitter", error="Missing required field: text.")

    body = {"text": text}
    if payload.get("reply_to"):
        body["reply"] = {"in_reply_to_tweet_id": str(payload["reply_to"])}

    try:
        resp = requests.post(
            TWITTER_API_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if resp.ok:
            tweet_id = resp.json().get("data", {}).get("id", "")
            return PostResult(channel="twitter", post_id=tweet_id, post_url=f"https://x.com/i/status/{tweet_id}" if tweet_id else "")
        return PostResult(status="error", channel="twitter", error=resp.text[:300])
    except Exception as e:
        return PostResult(status="error", channel="twitter", error=str(e)[:300])
