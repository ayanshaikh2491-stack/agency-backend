"""CEO Data Layer — Central data aggregator for Agency CEO.

Pulls data from all system modules and provides a unified view:
  workspaces, agent outputs, reviews, errors
  - token_manager → token health, expiry alerts
  - sba_store → leads, handoffs, meetings
  - agent_activity → who did what when

CEO uses this to make data-driven decisions.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from admin.persistence import get_workspace_db, rows_to_list

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _uuid_hex(n: int = 8) -> str:
    import uuid
    return uuid.uuid4().hex[:n]


# ═══════════════════════════════════════════════════════════════════════════════
# AGENCY OVERVIEW — High-level view of entire agency
# ═══════════════════════════════════════════════════════════════════════════════

def get_agency_overview() -> dict[str, Any]:
    """Get complete agency overview — everything CEO needs to know."""
    now = _now()

    # Workspaces
    workspaces = _get_workspaces()

    # Leads & Handoffs
    leads = _get_leads()
    handoffs = _get_handoffs()

    # Token health
    token_health = _get_all_token_health()

    # Pending reviews
    pending_reviews = _get_pending_reviews()

    # Activity summary
    activity = _get_recent_activity(limit=50)

    # Compute summary stats
    active_leads = len([l for l in leads if l.get("status") not in ("closed", "lost")])
    closed_leads = len([l for l in leads if l.get("status") == "closed"])
    lost_leads = len([l for l in leads if l.get("status") == "lost"])
    pending_handoffs = len([h for h in handoffs if not h.get("workspace_id")])

    total_tokens = len(token_health)
    expired_tokens = len([t for t in token_health if t["status"] == "expired"])
    expiring_tokens = len([t for t in token_health if t["status"] == "expiring_soon"])

    # Alerts
    alerts = []
    if expired_tokens > 0:
        alerts.append({
            "severity": "critical",
            "type": "token_expired",
            "message": f"{expired_tokens} token(s) expired — client work may be blocked",
        })
    if expiring_tokens > 0:
        alerts.append({
            "severity": "warning",
            "type": "token_expiring",
            "message": f"{expiring_tokens} token(s) expiring within 10 days",
        })
    if pending_handoffs > 0:
        alerts.append({
            "severity": "info",
            "type": "pending_handoff",
            "message": f"{pending_handoffs} handoff(s) from SBA awaiting review",
        })
    if pending_reviews:
        alerts.append({
            "severity": "info",
            "type": "pending_review",
            "message": f"{len(pending_reviews)} agent output(s) awaiting CEO review",
        })

    return {
        "generated_at": _now_iso(),
        "summary": {
            "total_workspaces": len(workspaces),
            "total_leads": len(leads),
            "active_leads": active_leads,
            "closed_leads": closed_leads,
            "lost_leads": lost_leads,
            "pending_handoffs": pending_handoffs,
            "pending_reviews": len(pending_reviews),
            "total_tokens": total_tokens,
            "expired_tokens": expired_tokens,
            "expiring_tokens": expiring_tokens,
        },
        "alerts": alerts,
        "alert_count": len(alerts),
        "workspaces": workspaces,
        "leads_summary": leads[:10],  # last 10
        "token_health": token_health,
        "recent_activity": activity[:10],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSPACE HEALTH — Per-client deep health
# ═══════════════════════════════════════════════════════════════════════════════

def get_workspace_health(workspace_id: str) -> dict[str, Any]:
    """Get full health report for a specific workspace."""
    ws = _get_workspace_by_id(workspace_id)
    if not ws:
        return {"error": f"Workspace '{workspace_id}' not found"}

    # Token status for this workspace
    tokens = _get_workspace_tokens(workspace_id)

    # Agent outputs for this workspace
    outputs = _get_workspace_outputs(workspace_id)

    # Reviews for this workspace
    reviews = _get_workspace_reviews(workspace_id)

    # Errors for this workspace
    errors = _get_workspace_errors(workspace_id)

    # Activity for this workspace
    activity = _get_workspace_activity(workspace_id)

    # Compute health score (0-100)
    health_score = _compute_health_score(tokens, outputs, reviews, errors, activity)

    # Agent status summary
    agent_status = {}
    for agent in ws.get("agents", []):
        agent_outputs = [o for o in outputs if o.get("agent_type") == agent]
        agent_reviews = [r for r in reviews if r.get("agent_type") == agent]
        last_output = agent_outputs[-1] if agent_outputs else None

        agent_status[agent] = {
            "total_outputs": len(agent_outputs),
            "pending_reviews": len([r for r in agent_reviews if r.get("status") == "pending"]),
            "last_activity": last_output.get("created_at", "never") if last_output else "never",
            "status": "active" if last_output else "idle",
        }

    return {
        "workspace": ws,
        "health_score": health_score,
        "health_label": _health_label(health_score),
        "agent_status": agent_status,
        "token_status": tokens,
        "recent_outputs": outputs[-5:] if outputs else [],
        "pending_reviews": [r for r in reviews if r.get("status") == "pending"],
        "recent_errors": errors[-5:] if errors else [],
        "recent_activity": activity[-10:] if activity else [],
        "alerts": _workspace_alerts(tokens, outputs, reviews, errors),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ALERTS — Things that need CEO attention
# ═══════════════════════════════════════════════════════════════════════════════

def get_alerts() -> list[dict[str, Any]]:
    """Get all alerts that need CEO attention."""
    alerts = []

    # Token alerts
    token_health = _get_all_token_health()
    for t in token_health:
        if t["status"] == "expired":
            alerts.append({
                "severity": "critical",
                "type": "token_expired",
                "workspace_id": t.get("workspace_id", ""),
                "platform": t.get("platform", ""),
                "message": f"Token expired for {t.get('platform', '?')} in workspace '{t.get('workspace_id', '?')}'",
                "action_needed": "Ask owner for new token",
            })
        elif t["status"] == "expiring_soon":
            alerts.append({
                "severity": "warning",
                "type": "token_expiring",
                "workspace_id": t.get("workspace_id", ""),
                "platform": t.get("platform", ""),
                "days_left": t.get("days_left", 0),
                "message": f"Token expiring in {t.get('days_left', 0)} days for {t.get('platform', '?')}",
                "action_needed": "Renew before expiry",
            })

    # Pending handoffs
    handoffs = _get_handoffs()
    pending = [h for h in handoffs if not h.get("workspace_id")]
    for h in pending:
        brief = h.get("brief", {})
        alerts.append({
            "severity": "info",
            "type": "pending_handoff",
            "handoff_id": h.get("id", ""),
            "client": brief.get("lead_name", "Unknown"),
            "message": f"Handoff from SBA: {brief.get('lead_name', 'Unknown')} ({brief.get('business_name', '')})",
            "action_needed": "Review and accept/reject handoff",
        })

    # Pending reviews
    reviews = _get_pending_reviews()
    for r in reviews:
        alerts.append({
            "severity": "info",
            "type": "pending_review",
            "workspace_id": r.get("workspace_id", ""),
            "agent_type": r.get("agent_type", ""),
            "message": f"{r.get('agent_type', '?')} output pending review in '{r.get('workspace_id', '?')}'",
            "action_needed": "Review and approve/reject",
        })

    # Sort by severity
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a.get("severity", "info"), 3))

    return alerts


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS — Pull data from system modules
# ═══════════════════════════════════════════════════════════════════════════════

def _get_workspaces() -> list[dict[str, Any]]:
    """Get all workspaces as dicts."""
    try:
        from admin.workspace.manager import list_workspaces
        workspaces = list_workspaces()
        return [
            {
                "id": ws.id,
                "name": ws.name,
                "client_name": ws.client_name or "N/A",
                "description": ws.description or "",
                "agents": ws.agents or [],
                "created_at": ws.created_at.isoformat() if hasattr(ws.created_at, "isoformat") else str(ws.created_at),
            }
            for ws in workspaces
        ]
    except Exception as e:
        logger.debug("Failed to load workspaces: %s", e)
        return []


def _get_workspace_by_id(workspace_id: str) -> dict[str, Any] | None:
    """Get a single workspace by ID."""
    try:
        from admin.workspace.manager import get_workspace
        ws = get_workspace(workspace_id)
        if not ws:
            return None
        return {
            "id": ws.id,
            "name": ws.name,
            "client_name": ws.client_name or "N/A",
            "description": ws.description or "",
            "agents": ws.agents or [],
            "created_at": ws.created_at.isoformat() if hasattr(ws.created_at, "isoformat") else str(ws.created_at),
        }
    except Exception as e:
        logger.debug("Failed to load workspace %s: %s", workspace_id, e)
        return None


def _get_leads() -> list[dict[str, Any]]:
    """Get all leads from SBA store."""
    try:
        from admin.agency.sba_store import list_leads
        return list_leads()
    except Exception as e:
        logger.debug("Failed to load leads: %s", e)
        return []


def _get_handoffs() -> list[dict[str, Any]]:
    """Get all handoffs from SBA store."""
    try:
        from admin.agency.sba_store import list_handoffs
        return list_handoffs()
    except Exception as e:
        logger.debug("Failed to load handoffs: %s", e)
        return []


def _get_all_token_health() -> list[dict[str, Any]]:
    """Get token health for ALL workspaces."""
    try:
        from admin.token_manager import check_all_tokens_health
        health = check_all_tokens_health()
        # Flatten workspace reports into per-token entries
        tokens = []
        for ws_report in health.get("workspace_reports", []):
            ws_id = ws_report.get("workspace_id", "")
            for token_file in _list_token_files(ws_id):
                tokens.append({
                    "workspace_id": ws_id,
                    "platform": token_file.get("platform", "unknown"),
                    "status": token_file.get("status", "unknown"),
                    "expires_at": token_file.get("expires_at", ""),
                    "days_left": token_file.get("days_left", -1),
                })
        return tokens
    except Exception as e:
        logger.debug("Failed to load token health: %s", e)
        return []


def _list_token_files(workspace_id: str) -> list[dict[str, Any]]:
    """List token files for a workspace."""
    try:
        from admin.token_manager import _workspace_dir
        import json as _json
        d = _workspace_dir(workspace_id)
        tokens = []
        for f in d.glob("*.json"):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
                # Check expiry
                expires_at = data.get("expires_at", "")
                status = data.get("status", "active")
                days_left = -1
                if expires_at:
                    try:
                        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                        days_left = (_now() - exp).days
                        if days_left < 0:
                            status = "expired"
                        elif days_left <= 10:
                            status = "expiring_soon"
                    except ValueError:
                        pass
                tokens.append({
                    "platform": data.get("platform", f.stem),
                    "status": status,
                    "expires_at": expires_at,
                    "days_left": days_left,
                    "token_type": data.get("token_type", ""),
                })
            except Exception:
                continue
        return tokens
    except Exception as e:
        logger.debug("Failed to list token files for %s: %s", workspace_id, e)
        return []


def _get_workspace_tokens(workspace_id: str) -> list[dict[str, Any]]:
    """Get token status for a specific workspace."""
    tokens = _list_token_files(workspace_id)
    for t in tokens:
        t["workspace_id"] = workspace_id
    return tokens


def _get_workspace_outputs(workspace_id: str) -> list[dict[str, Any]]:
    """Get agent outputs for a workspace."""
    try:
        from admin.workspace.manager import _agent_outputs
        return [o for o in _agent_outputs if o.get("workspace_id") == workspace_id]
    except Exception:
        return []


def _get_workspace_reviews(workspace_id: str) -> list[dict[str, Any]]:
    """Get reviews for a workspace."""
    try:
        from admin.workspace.manager import _pending_reviews, _completed_reviews
        pending = [{"status": "pending", **r} for r in _pending_reviews if r.get("workspace_id") == workspace_id]
        completed = [{"status": "completed", **r} for r in _completed_reviews if r.get("workspace_id") == workspace_id]
        return pending + completed
    except Exception:
        return []


def _get_pending_reviews() -> list[dict[str, Any]]:
    """Get all pending reviews across all workspaces."""
    try:
        from admin.workspace.manager import _pending_reviews
        return list(_pending_reviews)
    except Exception:
        return []


def _get_workspace_errors(workspace_id: str) -> list[dict[str, Any]]:
    """Get errors for a workspace."""
    try:
        from admin.workspace.manager import _error_logs
        return [e for e in _error_logs if e.get("workspace_id") == workspace_id]
    except Exception:
        return []


def _get_workspace_activity(workspace_id: str) -> list[dict[str, Any]]:
    """Get recent activity for a workspace from SQLite, falling back to JSON."""
    try:
        import asyncio
        from admin.persistence import get_workspace_db, rows_to_list

        async def _read():
            try:
                db = await get_workspace_db()
                cursor = await db.execute(
                    "SELECT * FROM ceo_activity_log WHERE workspace_id=? ORDER BY timestamp DESC LIMIT 20",
                    (workspace_id,),
                )
                rows = await cursor.fetchall()
                entries = rows_to_list(rows)
                for e in entries:
                    if isinstance(e.get("metadata"), str):
                        try:
                            e["metadata"] = json.loads(e["metadata"])
                        except (TypeError, json.JSONDecodeError):
                            e["metadata"] = {}
                return entries
            except Exception:
                return []

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                entries = asyncio.run_coroutine_threadsafe(_read(), loop).result(timeout=2)
                if entries:
                    return entries
            else:
                entries = loop.run_until_complete(_read())
                if entries:
                    return entries
        except (RuntimeError, Exception):
            pass
    except Exception:
        pass

    # Fallback: JSON file
    try:
        from pathlib import Path
        import os
        activity_file = Path(os.getenv("TAGS_DATA_DIR", "data")) / "ceo_activity" / f"{workspace_id}.json"
        if not activity_file.exists():
            return []
        data = json.loads(activity_file.read_text(encoding="utf-8"))
        return data.get("entries", [])[-20:]
    except Exception:
        return []


def _get_recent_activity(limit: int = 50) -> list[dict[str, Any]]:
    """Get recent activity across all workspaces from SQLite."""
    try:
        import asyncio
        from admin.persistence import get_workspace_db, rows_to_list

        async def _read():
            try:
                db = await get_workspace_db()
                cursor = await db.execute(
                    "SELECT * FROM ceo_activity_log ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
                rows = await cursor.fetchall()
                entries = rows_to_list(rows)
                for e in entries:
                    if isinstance(e.get("metadata"), str):
                        try:
                            e["metadata"] = json.loads(e["metadata"])
                        except (TypeError, json.JSONDecodeError):
                            e["metadata"] = {}
                return entries
            except Exception:
                return []

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                entries = asyncio.run_coroutine_threadsafe(_read(), loop).result(timeout=2)
                if entries:
                    return entries
            else:
                entries = loop.run_until_complete(_read())
                if entries:
                    return entries
        except (RuntimeError, Exception):
            pass
    except Exception:
        pass

    # Fallback: read from JSON files
    try:
        from pathlib import Path
        import os
        activity_dir = Path(os.getenv("TAGS_DATA_DIR", "data")) / "ceo_activity"
        if not activity_dir.exists():
            return []
        all_entries = []
        for f in activity_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ws_id = f.stem
                for entry in data.get("entries", []):
                    entry["workspace_id"] = ws_id
                all_entries.extend(data.get("entries", []))
            except Exception:
                continue
        all_entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return all_entries[:limit]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVITY LOGGING — Record what agents do
# ═══════════════════════════════════════════════════════════════════════════════

def log_activity(
    workspace_id: str,
    agent_type: str,
    action: str,
    details: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an agent activity for CEO tracking.

    Called by workspace manager when agents produce output, get reviewed, etc.
    Dual-writes to SQLite (primary) and JSON file (legacy fallback).
    """
    now_iso = _now_iso()
    entry_id = f"act_{_uuid_hex(8)}"

    # Write to SQLite (fire-and-forget)
    async def _write_sqlite():
        try:
            db = await get_workspace_db()
            await db.execute(
                "INSERT INTO ceo_activity_log "
                "(id, workspace_id, agent_type, action, details, metadata, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry_id, workspace_id, agent_type, action, details,
                 json.dumps(metadata or {}), now_iso),
            )
            await db.commit()
        except Exception as e:
            logger.debug("SQLite activity write failed: %s", e)
    asyncio.create_task(_write_sqlite())

    # Legacy JSON file write
    try:
        from pathlib import Path
        import os
        activity_dir = Path(os.getenv("TAGS_DATA_DIR", "data")) / "ceo_activity"
        activity_dir.mkdir(parents=True, exist_ok=True)
        activity_file = activity_dir / f"{workspace_id}.json"

        data = {}
        if activity_file.exists():
            try:
                data = json.loads(activity_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        entries = data.get("entries", [])
        entries.append({
            "timestamp": now_iso,
            "agent_type": agent_type,
            "action": action,
            "details": details,
            "metadata": metadata or {},
        })

        data["entries"] = entries[-100:]
        activity_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to log activity to file: %s", e)


def get_recent_activity(limit: int = 50) -> list[dict[str, Any]]:
    """Public: get recent activity across all workspaces, newest first."""
    return _get_recent_activity(limit=limit)


def get_workspace_activity(workspace_id: str) -> list[dict[str, Any]]:
    """Public: get recent activity for a specific workspace."""
    return _get_workspace_activity(workspace_id)


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH SCORING

def _compute_health_score(
    tokens: list,
    outputs: list,
    reviews: list,
    errors: list,
    activity: list,
) -> int:
    """Compute workspace health score 0-100.

    Factors:
    - Token health (30 points)
    - Agent activity (30 points)
    - Review status (20 points)
    - Error rate (20 points)
    """
    score = 100

    # Token health (-30 max)
    if tokens:
        expired = len([t for t in tokens if t.get("status") == "expired"])
        expiring = len([t for t in tokens if t.get("status") == "expiring_soon"])
        score -= expired * 15
        score -= expiring * 5

    # Agent activity (-30 max)
    if not activity:
        score -= 15  # No activity at all
    elif activity:
        last = activity[-1]
        try:
            last_ts = datetime.fromisoformat(last.get("timestamp", "").replace("Z", "+00:00"))
            days_since = (_now() - last_ts).days
            if days_since > 7:
                score -= 20
            elif days_since > 3:
                score -= 10
            elif days_since > 1:
                score -= 5
        except (ValueError, TypeError):
            pass

    # Pending reviews (-20 max)
    pending = len([r for r in reviews if r.get("status") == "pending"])
    if pending > 5:
        score -= 20
    elif pending > 2:
        score -= 10
    elif pending > 0:
        score -= 5

    # Errors (-20 max)
    if errors:
        critical = len([e for e in errors if e.get("severity") == "critical"])
        score -= critical * 10
        score -= min(len(errors) * 2, 10)

    return max(0, min(100, score))


def _health_label(score: int) -> str:
    """Convert health score to label."""
    if score >= 80:
        return "Healthy"
    elif score >= 60:
        return "Needs Attention"
    elif score >= 40:
        return "Warning"
    else:
        return "Critical"


def _workspace_alerts(
    tokens: list,
    outputs: list,
    reviews: list,
    errors: list,
) -> list[dict[str, Any]]:
    """Generate alerts for a specific workspace."""
    alerts = []

    for t in tokens:
        if t.get("status") == "expired":
            alerts.append({
                "severity": "critical",
                "message": f"{t.get('platform', '?')} token expired",
            })
        elif t.get("status") == "expiring_soon":
            alerts.append({
                "severity": "warning",
                "message": f"{t.get('platform', '?')} token expiring in {t.get('days_left', '?')} days",
            })

    pending = len([r for r in reviews if r.get("status") == "pending"])
    if pending > 0:
        alerts.append({
            "severity": "info",
            "message": f"{pending} output(s) pending review",
        })

    if errors:
        recent_errors = errors[-3:]
        for e in recent_errors:
            alerts.append({
                "severity": e.get("severity", "info"),
                "message": f"Recent error: {e.get('error_type', '?')} — {e.get('description', '')[:100]}",
            })

    return alerts
