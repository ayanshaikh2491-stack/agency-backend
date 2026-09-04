"""Base models and validation for organic channel posting."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CHANNEL_TYPE_API = "api"
CHANNEL_TYPE_BROWSER = "browser"


@dataclass
class PostResult:
    """Standard result returned by every organic channel module."""

    status: str = "published"  # published | queued | error | config_missing
    channel: str = ""
    post_id: str = ""
    post_url: str = ""
    error: str = ""
    published_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "channel": self.channel,
            "post_id": self.post_id,
            "post_url": self.post_url,
            "error": self.error,
            "published_at": self.published_at,
        }


def validate_payload(meta: dict, payload: dict) -> list[str]:
    """Return list of missing required fields from payload against channel meta."""
    missing = []
    for field_name in meta.get("required_fields", []):
        value = payload.get(field_name)
        if value is None or str(value).strip() == "":
            missing.append(field_name)
    return missing
