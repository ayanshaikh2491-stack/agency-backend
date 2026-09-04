"""Per-workspace channel configuration store (subreddits, chat IDs, group URLs).

Files live in admin/organic_config/<workspace_id>/<channel>.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ORGANIC_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "organic_config"


def _channel_dir(workspace_id: str) -> Path:
    d = ORGANIC_CONFIG_DIR / workspace_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_channel_config(workspace_id: str, channel: str) -> dict:
    f = _channel_dir(workspace_id) / f"{channel}.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_channel_config(workspace_id: str, channel: str, data: dict) -> dict:
    f = _channel_dir(workspace_id) / f"{channel}.json"
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Saved %s config for workspace %s", channel, workspace_id)
    return {"status": "saved", "channel": channel, "workspace_id": workspace_id}


def list_channel_configs(workspace_id: str) -> dict:
    d = _channel_dir(workspace_id)
    out = {}
    for f in sorted(d.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return out
