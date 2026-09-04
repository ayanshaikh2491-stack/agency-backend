"""Central router for organic channel posting."""
from __future__ import annotations

import importlib
import logging
from typing import Any

from admin.tools.organic.base import PostResult, validate_payload
from admin.tools.organic.registry import get_channel

logger = logging.getLogger(__name__)

CHANNEL_MODULES: dict[str, str] = {
    "reddit": "admin.tools.organic.reddit_api",
    "linkedin": "admin.tools.organic.linkedin_api",
    "twitter": "admin.tools.organic.twitter_api",
    "pinterest": "admin.tools.organic.pinterest_api",
    "telegram": "admin.tools.organic.telegram_api",
    "gbp": "admin.tools.organic.gbp_api",
    "facebook": "admin.tools.organic.facebook_browser",
}


def _load_module(channel_id: str):
    module_path = CHANNEL_MODULES.get(channel_id)
    if not module_path:
        return None
    try:
        return importlib.import_module(module_path)
    except ImportError as e:
        logger.error("Failed to import %s: %s", module_path, e)
        return None


def post(channel_id: str, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Route a post to the right channel module and return a standard result dict."""
    meta = get_channel(channel_id)
    if meta is None:
        return PostResult(status="error", channel=channel_id, error=f"Unknown channel: {channel_id}").to_dict()

    missing = validate_payload(meta, payload)
    if missing:
        return PostResult(
            status="error", channel=channel_id,
            error=f"Missing required fields: {', '.join(missing)}",
        ).to_dict()

    module = _load_module(channel_id)
    if module is None:
        return PostResult(status="error", channel=channel_id, error=f"Module not available for channel: {channel_id}").to_dict()

    # OAuth channels: refresh an expiring token before dispatching. Non-fatal —
    # the channel module still reports config_missing if no usable token exists.
    try:
        from admin.tools.organic.oauth import ensure_fresh_token, oauth_supported
        if oauth_supported(channel_id):
            ensure_fresh_token(workspace_id, channel_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("oauth pre-refresh skipped for %s: %s", channel_id, exc)

    try:
        result = module.post(workspace_id, payload)
        if isinstance(result, PostResult):
            return result.to_dict()
        return result
    except Exception as e:
        logger.exception("organic post failed on %s", channel_id)
        return PostResult(status="error", channel=channel_id, error=str(e)[:300]).to_dict()
