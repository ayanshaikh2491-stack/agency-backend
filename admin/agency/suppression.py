"""Addresses that must never be emailed again.

Taken from Colossus (github.com/vitorfs/colossus), which keeps a hard-bounce and
unsubscribe suppression list and refuses to mail anything on it. Two things are
learned from that design and implemented here.

Suppression is permanent. A recipient who asked to stop hearing from the agency
should not have to ask twice, so there is no un-suppress path exposed anywhere.

Reads never touch the database. The sender checks this list once per run rather
than once per message, and a list of a few hundred strings costs nothing to hold
in memory for the length of a campaign.

Bounces and unsubscribes share one list on purpose. Colossus treats a hard
bounce as a suppression for the same reason: continuing to write to an address
that has already refused mail is what turns a sending problem into a provider
block.
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_suppressed: set[str] = set()
_loaded = False


def _path() -> str:
    return os.environ.get("SBA_SUPPRESSION_FILE", "./suppression_list.txt")


def _load() -> None:
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        try:
            with open(_path(), encoding="utf-8") as f:
                for line in f:
                    addr = line.strip().lower()
                    if addr and "@" in addr:
                        _suppressed.add(addr)
        except FileNotFoundError:
            pass
        _loaded = True


def suppress(email: str, reason: str = "") -> bool:
    """Record an address as permanently unreachable. Never raises."""
    addr = (email or "").strip().lower()
    if not addr or "@" not in addr:
        return False
    try:
        _load()
        with _lock:
            _suppressed.add(addr)
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(f"{addr}\t{reason or 'unspecified'}\n")
        return True
    except Exception:
        logger.warning("could not suppress %s: %s", addr, reason)
        return False


def is_suppressed(email: str) -> bool:
    _load()
    return (email or "").strip().lower() in _suppressed


def filtered(emails: list[str]) -> list[str]:
    """Drop suppressed addresses. Used once per campaign run."""
    _load()
    return [e for e in emails if not is_suppressed(e)]


def count() -> int:
    _load()
    return len(_suppressed)


def reload() -> int:
    """Re-read from disk. For the sender, at the start of each run."""
    global _loaded
    with _lock:
        _suppressed.clear()
        _loaded = False
    _load()
    return count()