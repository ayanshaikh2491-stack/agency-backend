# admin/tools/organic/linkedin_api.py
"""LinkedIn posting via REST API (UGC posts)."""
from __future__ import annotations

import logging

import requests

from admin.tools.organic.base import CHANNEL_TYPE_API, PostResult
from admin.token_manager import get_active_token

logger = logging.getLogger(__name__)

CHANNEL_META = {
    "id": "linkedin",
    "name": "LinkedIn",
    "type": CHANNEL_TYPE_API,
    "auth": "token",
    "capabilities": ["post"],
    "required_fields": ["text"],
    "description": "Share text post to profile or company page.",
}


def post(workspace_id: str, payload: dict) -> PostResult:
    token_data = get_active_token(workspace_id, "linkedin")
    if not token_data:
        return PostResult(status="config_missing", channel="linkedin", error="No LinkedIn token.")

    # Interface compat: admin.token_manager.get_active_token returns a bare
    # access-token string (or None), while the test mocks it as a dict. Normalize
    # to the dict shape used below so real string tokens don't crash. A bare
    # string carries no platform_user_id, so author_urn falls back to the
    # payload's person_urn; if absent it becomes config_missing "No author URN".
    if isinstance(token_data, str):
        token_data = {"access_token": token_data}

    token = token_data.get("access_token", "")
    author_urn = payload.get("person_urn") or token_data.get("platform_user_id", "")
    # Normalize: accept a bare person id, a partial urn (person:xxx), or a full
    # urn (urn:li:person:xxx) without doubling the prefix in the body below.
    if author_urn.startswith("urn:li:person:"):
        author_urn = author_urn[len("urn:li:person:"):]
    elif author_urn.startswith("person:"):
        author_urn = author_urn[len("person:"):]
    text = payload.get("text", "")
    if not token:
        return PostResult(status="config_missing", channel="linkedin", error="Empty LinkedIn token.")
    if not author_urn:
        return PostResult(status="config_missing", channel="linkedin", error="No author URN. Connect with LinkedIn and store platform_user_id.")
    if not text:
        return PostResult(status="error", channel="linkedin", error="Missing required field: text.")

    body = {
        "author": f"urn:li:person:{author_urn}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    try:
        resp = requests.post(
            "https://api.linkedin.com/v2/ugcPosts",
            headers={"Authorization": f"Bearer {token}", "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if resp.ok:
            post_id = resp.json().get("id", "")
            return PostResult(channel="linkedin", post_id=post_id)
        return PostResult(status="error", channel="linkedin", error=resp.text[:300])
    except Exception as e:
        return PostResult(status="error", channel="linkedin", error=str(e)[:300])
