"""SEO Data Store — in-memory + optional DB persistence.

Stores audits, keyword research, reports, and tracked keywords per workspace.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── In-memory stores ─────────────────────────────────────────────────────────
_audits: dict[str, dict[str, Any]] = {}
_reports: dict[str, dict[str, Any]] = {}
_tracked_keywords: dict[str, list[dict]] = {}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# AUDITS
# ═══════════════════════════════════════════════════════════════════════════════


def save_audit(workspace_id: str, url: str, audit_data: dict[str, Any]) -> dict[str, Any]:
    """Save a site audit result."""
    aid = _new_id()
    now = _now_str()
    record = {
        "id": aid,
        "workspace_id": workspace_id,
        "url": url,
        "data": audit_data,
        "pages_crawled": audit_data.get("pages_crawled", 0),
        "issues_count": len(audit_data.get("issues", [])),
        "summary": audit_data.get("summary", {}),
        "created_at": now,
    }
    _audits[aid] = record
    return record


def get_audit(audit_id: str) -> dict[str, Any] | None:
    return _audits.get(audit_id)


def list_audits(workspace_id: str | None = None) -> list[dict[str, Any]]:
    audits = list(_audits.values())
    if workspace_id:
        audits = [a for a in audits if a["workspace_id"] == workspace_id]
    return sorted(audits, key=lambda a: a["created_at"], reverse=True)


def delete_audit(audit_id: str) -> bool:
    return _audits.pop(audit_id, None) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# TRACKED KEYWORDS
# ═══════════════════════════════════════════════════════════════════════════════


def add_tracked_keyword(
    workspace_id: str,
    keyword: str,
    target_url: str = "",
    search_volume: str = "",
    difficulty: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Add a keyword to track for a workspace."""
    kid = _new_id()
    now = _now_str()
    entry = {
        "id": kid,
        "workspace_id": workspace_id,
        "keyword": keyword,
        "target_url": target_url,
        "search_volume": search_volume,
        "difficulty": difficulty,
        "notes": notes,
        "rank_history": [],
        "created_at": now,
        "updated_at": now,
    }
    if workspace_id not in _tracked_keywords:
        _tracked_keywords[workspace_id] = []
    _tracked_keywords[workspace_id].append(entry)
    return entry


def get_tracked_keywords(workspace_id: str) -> list[dict[str, Any]]:
    return _tracked_keywords.get(workspace_id, [])


def update_tracked_keyword(
    workspace_id: str,
    keyword_id: str,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    for kw in _tracked_keywords.get(workspace_id, []):
        if kw["id"] == keyword_id:
            kw.update(updates)
            kw["updated_at"] = _now_str()
            return kw
    return None


def remove_tracked_keyword(workspace_id: str, keyword_id: str) -> bool:
    kws = _tracked_keywords.get(workspace_id, [])
    for i, kw in enumerate(kws):
        if kw["id"] == keyword_id:
            kws.pop(i)
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTS
# ═══════════════════════════════════════════════════════════════════════════════


def save_report(
    workspace_id: str,
    report_type: str,
    title: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    """Save an SEO report."""
    rid = _new_id()
    now = _now_str()
    record = {
        "id": rid,
        "workspace_id": workspace_id,
        "report_type": report_type,
        "title": title,
        "content": content,
        "created_at": now,
    }
    _reports[rid] = record
    return record


def get_report(report_id: str) -> dict[str, Any] | None:
    return _reports.get(report_id)


def list_reports(
    workspace_id: str | None = None,
    report_type: str | None = None,
) -> list[dict[str, Any]]:
    reports = list(_reports.values())
    if workspace_id:
        reports = [r for r in reports if r["workspace_id"] == workspace_id]
    if report_type:
        reports = [r for r in reports if r["report_type"] == report_type]
    return sorted(reports, key=lambda r: r["created_at"], reverse=True)


def delete_report(report_id: str) -> bool:
    return _reports.pop(report_id, None) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# KEYWORD RESEARCH CACHE
# ═══════════════════════════════════════════════════════════════════════════════

_keyword_cache: dict[str, dict[str, Any]] = {}


def cache_keyword_research(seed: str, data: dict[str, Any]) -> None:
    _keyword_cache[seed.lower()] = data


def get_cached_keyword_research(seed: str) -> dict[str, Any] | None:
    return _keyword_cache.get(seed.lower())
