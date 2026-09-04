"""File-based mirror of key Agency OS state (belt-and-suspenders with PocketBase).

Boss rule: everything saved in PocketBase AND in plain JSON files under
``data/store/<collection>/<record_id>.json`` so state stays human-readable
and survives even when PocketBase is unreachable.

- Always-on: writes happen whether or not POCKETBASE_URL is configured.
- Best-effort: a file failure is logged and never breaks the caller.
- Filenames are sanitised to [A-Za-z0-9._-] so any id is safe on disk.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_STORE = Path(os.getenv("TAGS_DATA_DIR", "data")) / "store"
_lock = threading.Lock()
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _path(collection: str, record_id: str) -> Path:
    safe_coll = _SAFE.sub("_", collection) or "misc"
    safe_rid = _SAFE.sub("_", str(record_id)) or "unnamed"
    return _STORE / safe_coll / f"{safe_rid}.json"


def save_record(collection: str, record_id: str, record: dict[str, Any]) -> bool:
    """Atomically write one record as pretty JSON. Returns success."""
    try:
        path = _path(collection, record_id)
        payload = json.dumps(
            record,
            default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o),
            indent=2,
            ensure_ascii=False,
        )
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)  # atomic on same filesystem
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("file_store.save_record(%s/%s) failed: %s",
                     collection, record_id, exc)
        return False


def delete_record(collection: str, record_id: str) -> bool:
    """Remove one record file (missing file counts as success)."""
    try:
        _path(collection, record_id).unlink(missing_ok=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("file_store.delete_record(%s/%s) failed: %s",
                     collection, record_id, exc)
        return False


def load_all(collection: str) -> list[dict[str, Any]]:
    """Load every record dict from a collection folder (best-effort)."""
    out: list[dict[str, Any]] = []
    try:
        folder = _STORE / (_SAFE.sub("_", collection) or "misc")
        if not folder.is_dir():
            return out
        for f in sorted(folder.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    out.append(data)
            except (ValueError, OSError):
                continue
    except Exception as exc:  # noqa: BLE001
        logger.debug("file_store.load_all(%s) failed: %s", collection, exc)
    return out
