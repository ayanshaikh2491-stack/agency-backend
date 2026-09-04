"""Per-workspace organic post history (append-only JSONL log).

Every organic post — manual or scheduled — is recorded here so the frontend
can show a real proof-of-work timeline and stats instead of sample data.

Files live in admin/organic_data/<workspace_id>/history.jsonl
Override the root with the ORGANIC_DATA_DIR env var (used by tests).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ORGANIC_DATA_DIR = Path(
    os.environ.get("ORGANIC_DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "organic_data"))
)

# Sensitive payload keys that must never be persisted to history.
_SENSITIVE_KEYS = {"password", "client_secret", "access_token", "bot_token", "token", "secret"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_file(workspace_id: str) -> Path:
    d = ORGANIC_DATA_DIR / workspace_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "history.jsonl"


def _sanitize_payload(payload: dict | None) -> dict:
    out: dict = {}
    for k, v in (payload or {}).items():
        if k.lower() in _SENSITIVE_KEYS:
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = [str(x) for x in v[:20]]
        else:
            out[k] = str(v)[:500]
    return out


def record_post(
    workspace_id: str,
    channel: str,
    result: dict[str, Any],
    payload: dict | None = None,
    scheduled_for: str | None = None,
) -> str:
    """Append one post event to the workspace history log. Returns history_id.

    Fail-open: a disk error must never break posting, so this logs and returns
    a synthetic id instead of raising.
    """
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now(),
        "channel": channel,
        "status": result.get("status", "unknown"),
        "post_id": result.get("post_id", ""),
        "post_url": result.get("post_url", ""),
        "error": result.get("error", "")[:500],
        "scheduled_for": scheduled_for or "",
        "payload": _sanitize_payload(payload),
    }
    try:
        with open(_history_file(workspace_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        logger.warning("history write failed for %s/%s: %s", workspace_id, channel, exc)
    _mirror_to_pb(workspace_id, channel, entry)
    return entry["id"]


# ── PocketBase mirror (best-effort, never fatal) ───────────────────────────

def _mirror_to_pb(workspace_id: str, channel: str, entry: dict) -> None:
    """Mirror one organic history entry to PocketBase so the boss can see it.

    Stable dedupe key ``record_id`` = ``<workspace>:<channel>:<sha1[:12]>``.
    All fields are flattened to JSON-safe strings. Best-effort: failures are
    logged at debug level and never break the main history path.
    """
    try:
        entry_json = json.dumps(entry, default=str, sort_keys=True)
        record_id = (
            f"{workspace_id}:{channel}:"
            f"{hashlib.sha1(entry_json.encode('utf-8')).hexdigest()[:12]}"
        )
        payload: dict[str, Any] = {
            "record_id": record_id,
            "workspace_id": workspace_id,
            "channel": channel,
            "history_id": entry.get("id", ""),
            "ts": entry.get("ts", ""),
            "status": entry.get("status", ""),
            "post_id": entry.get("post_id", ""),
            "post_url": entry.get("post_url", ""),
            "error": entry.get("error", ""),
            "scheduled_for": entry.get("scheduled_for", ""),
            "payload": json.dumps(entry.get("payload", {}), default=str),
            "appended_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("organic_history mirror payload build failed (non-fatal): %s", exc)
        return
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        pb.upsert_by_key("organic_history", "record_id", payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase mirror (organic_history) failed (non-fatal): %s", exc)


def list_posts(workspace_id: str, channel: str | None = None, limit: int = 200) -> list[dict]:
    """Return post history, newest first. Optionally filter by channel."""
    f = _history_file(workspace_id)
    if not f.exists():
        return []
    entries: list[dict] = []
    try:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    if channel:
        entries = [e for e in entries if e.get("channel") == channel]
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return entries[: max(1, limit)]


def history_stats(workspace_id: str) -> dict[str, Any]:
    """Per-channel / per-status counts plus totals for the workspace."""
    entries = list_posts(workspace_id, limit=10000)
    by_channel: dict[str, int] = {}
    by_status: dict[str, int] = {}
    published = 0
    for e in entries:
        ch = e.get("channel", "?")
        st = e.get("status", "?")
        by_channel[ch] = by_channel.get(ch, 0) + 1
        by_status[st] = by_status.get(st, 0) + 1
        if st == "published":
            published += 1
    return {
        "total": len(entries),
        "published": published,
        "by_channel": by_channel,
        "by_status": by_status,
    }
