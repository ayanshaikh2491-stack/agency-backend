"""Channel registry — single source of truth for what each channel can do."""
from __future__ import annotations

from admin.tools.organic.base import CHANNEL_TYPE_API, CHANNEL_TYPE_BROWSER

CHANNELS: dict[str, dict] = {
    "reddit": {
        "id": "reddit",
        "name": "Reddit",
        "type": CHANNEL_TYPE_API,
        "auth": "token",
        "capabilities": ["post", "comment"],
        "required_fields": ["subreddit", "title", "body"],
        "description": "Post to subreddits and comment on threads (PRAW-style via requests OAuth2).",
    },
    "linkedin": {
        "id": "linkedin",
        "name": "LinkedIn",
        "type": CHANNEL_TYPE_API,
        "auth": "token",
        "capabilities": ["post"],
        "required_fields": ["text"],
        "description": "Share text/URL post to profile or company page.",
    },
    "twitter": {
        "id": "twitter",
        "name": "X / Twitter",
        "type": CHANNEL_TYPE_API,
        "auth": "token",
        "capabilities": ["post", "reply"],
        "required_fields": ["text"],
        "description": "Post tweet or reply via X API v2.",
    },
    "pinterest": {
        "id": "pinterest",
        "name": "Pinterest",
        "type": CHANNEL_TYPE_API,
        "auth": "token",
        "capabilities": ["post"],
        "required_fields": ["title", "image_url", "board_id"],
        "description": "Create a pin on a board.",
    },
    "telegram": {
        "id": "telegram",
        "name": "Telegram",
        "type": CHANNEL_TYPE_API,
        "auth": "token",
        "capabilities": ["post"],
        "required_fields": ["text"],
        "description": "Send message/photo to a bot channel or group.",
    },
    "gbp": {
        "id": "gbp",
        "name": "Google Business Profile",
        "type": CHANNEL_TYPE_API,
        "auth": "token",
        "capabilities": ["post"],
        "required_fields": ["summary"],
        "description": "Create a local business post (offer/event/update).",
    },
    "facebook": {
        "id": "facebook",
        "name": "Facebook",
        "type": CHANNEL_TYPE_BROWSER,
        "auth": "browser_session",
        "capabilities": ["groups", "marketplace"],
        "required_fields": ["target"],  # group URL or marketplace
        "description": "Post to Facebook Groups and Marketplace listings via browser automation.",
    },
}


def get_channel(channel_id: str) -> dict | None:
    return CHANNELS.get(channel_id)


def list_channels() -> list[dict]:
    return [dict(v) for v in CHANNELS.values()]
