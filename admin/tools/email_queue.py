"""Durable outbound email helpers backed by AgentMail and SQLite.

The outbox is an audit/delivery record, not a fake sender. Agent-owned paths
call :func:`send_agentmail_email`, which writes ``sending`` before the HTTPS
request and finishes with ``sent`` or ``failed``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from admin.persistence import get_workspace_db

logger = logging.getLogger(__name__)

_EMAIL_OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS email_outbox (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    from_agent TEXT NOT NULL DEFAULT 'ceo',
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT
);
"""


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\x00", "")[:500]
    return re.sub(
        r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,}]+",
        r"\1=[REDACTED]",
        text,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _idempotency_key(actor: str, to_email: str, subject: str, body: str,
                     workspace_id: str = "") -> str:
    """Stable key for retries; content changes intentionally create a new key."""
    payload = "\x1f".join((actor, to_email, subject, body, workspace_id))
    return "em_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _normalize_outbox_key(key: str) -> str:
    """Keep explicit and generated keys in the existing ``em_`` namespace."""
    return key if key.startswith("em_") else "em_" + key


def send(
    actor: str,
    to_email: str,
    subject: str,
    body_text: str,
    html: str | None = None,
    reply_to: str | None = None,
    idempotency_key: str | None = None,
    bcc: list[str] | None = None,
) -> dict[str, Any]:
    """Small synchronous boundary used by the async durable sender and tests."""
    from admin.tools.agentmail_client import send_with_result

    return send_with_result(
        actor,
        to_email,
        subject,
        body_text,
        html=html,
        reply_to=reply_to,
        idempotency_key=idempotency_key,
        bcc=bcc,
    )


class AgentMailEmailClient:
    """Small compatibility adapter for callers expecting an async email client."""

    def __init__(self, actor: str = "ceo", owner_email: str | None = None,
                 workspace_id: str = "") -> None:
        from admin.tools.agentmail_client import AgentMailEmailClient as _Client

        self._client = _Client(actor, owner_email=owner_email, workspace_id=workspace_id)

    @property
    def enabled(self) -> bool:
        return self._client.enabled

    async def send_email(self, to_email: str, subject: str, body_text: str,
                         cc_owner: bool = True) -> bool:
        return await self._client.send_email(to_email, subject, body_text, cc_owner=cc_owner)

    async def check_replies(self, mark_read: bool = True) -> list[dict[str, Any]]:
        return await self._client.check_replies(mark_read=mark_read)


# Kept as a compatibility alias for old integrations. It is live AgentMail,
# never queue-only and never SMTP.
QueuedEmailClient = AgentMailEmailClient


async def queue_email(
    to_email: str,
    subject: str,
    body: str,
    from_agent: str = "ceo",
    workspace_id: str = "",
    status: str = "pending",
    idempotency_key: str | None = None,
) -> str:
    """Insert an outbox row. New agent paths should prefer ``send_agentmail_email``."""
    from admin.persistence import get_workspace_db

    msg_id = idempotency_key or f"em_{uuid.uuid4().hex[:32]}"
    db = await get_workspace_db()
    await db.execute(
        "INSERT INTO email_outbox "
        "(id, workspace_id, from_agent, to_email, subject, body, status, error, created_at, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, NULL)",
        (msg_id, workspace_id, from_agent, to_email, subject, body, status, _now()),
    )
    await db.commit()
    return msg_id


async def list_outbox(
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List outbound emails (newest first)."""
    from admin.persistence import get_workspace_db, row_to_dict

    db = await get_workspace_db()
    clauses: list[str] = []
    params: list[Any] = []
    if workspace_id:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cursor = await db.execute(
        f"SELECT * FROM email_outbox{where} ORDER BY created_at DESC LIMIT ?",
        (*params, limit),
    )
    rows = await cursor.fetchall()
    return [row_to_dict(r) for r in rows]


async def mark_outbox(
    msg_id: str,
    status: str,
    error: str = "",
    sent_at: str | None = None,
) -> bool:
    """Update an outbox row's status (sending / sent / failed)."""
    from admin.persistence import get_workspace_db

    db = await get_workspace_db()
    await db.execute(
        "UPDATE email_outbox SET status=?, error=?, sent_at=? WHERE id=?",
        (status, error, sent_at or (_now() if status == "sent" else None), msg_id),
    )
    await db.commit()
    return True


async def send_agentmail_email(
    actor: str,
    to_email: str,
    subject: str,
    body_text: str,
    workspace_id: str = "",
    cc_owner: bool = False,
    owner_email: str | None = None,
    idempotency_key: str | None = None,
    html: str | None = None,
) -> bool:
    """Send live through the actor's AgentMail inbox and persist the result."""
    from admin.persistence import get_workspace_db

    if not to_email or "@" not in to_email:
        return False
    key = _normalize_outbox_key(
        idempotency_key or _idempotency_key(actor, to_email, subject, body_text, workspace_id)
    )
    db = await get_workspace_db()
    # Reuse a completed delivery. A failed/sending row is retried with the same
    # AgentMail idempotency key, so an API retry cannot create a second message.
    cursor = await db.execute("SELECT status FROM email_outbox WHERE id=?", (key,))
    existing = await cursor.fetchone()
    if existing and existing["status"] == "sent":
        return True
    if not existing:
        bcc = [owner_email] if cc_owner and owner_email else None
        await db.execute(
            "INSERT INTO email_outbox "
            "(id, workspace_id, from_agent, to_email, subject, body, status, error, created_at, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'sending', '', ?, NULL)",
            (key, workspace_id, actor, to_email, subject, body_text, _now()),
        )
        await db.commit()
    else:
        await db.execute("UPDATE email_outbox SET status='sending', error='' WHERE id=?", (key,))
        await db.commit()

    bcc = [owner_email] if cc_owner and owner_email else None
    try:
        result = await asyncio.to_thread(
            send,
            actor,
            to_email,
            subject,
            body_text,
            html,
            None,
            key,
            bcc,
        )
        if isinstance(result, bool):
            result = {"ok": result}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:500]}
    if result.get("ok"):
        await mark_outbox(key, "sent")
        return True
    await mark_outbox(key, "failed", str(result.get("error") or "AgentMail send failed")[:500])
    return False


def send_agentmail_email_sync(
    actor: str,
    to_email: str,
    subject: str,
    body_text: str,
    workspace_id: str = "",
    cc_owner: bool = False,
    owner_email: str | None = None,
    idempotency_key: str | None = None,
    html: str | None = None,
) -> bool:
    """Synchronous durable sender for sync agent tool surfaces."""
    from admin.persistence import execute_sync

    if not to_email or "@" not in to_email:
        return False

    key = _normalize_outbox_key(
        idempotency_key or _idempotency_key(actor, to_email, subject, body_text, workspace_id)
    )
    bcc = [owner_email] if cc_owner and owner_email else None

    # The outbox is written from sync code, but the store it belongs to may be
    # remote Turso, which has no sync handle. execute_sync() routes these
    # statements through whichever backend is live. Opening a private
    # sqlite3.connect(DB_PATH) here instead would write the outbox to a local
    # file that the next deploy deletes while the rest of the outbox lives in
    # Turso, which is silent divergence, not a fallback.
    try:
        found = execute_sync([
            (_EMAIL_OUTBOX_SCHEMA, ()),
            ("SELECT status FROM email_outbox WHERE id=?", (key,)),
        ])
        row = found[1][0] if found[1] else None
        if row and row[0] == "sent":
            return True
        if row is None:
            execute_sync([(
                "INSERT OR IGNORE INTO email_outbox "
                "(id, workspace_id, from_agent, to_email, subject, body, status, error, created_at, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'sending', '', ?, NULL)",
                (key, workspace_id, actor, to_email, subject, body_text, _now()),
            )])
        else:
            execute_sync([(
                "UPDATE email_outbox SET status='sending', error='' WHERE id=?",
                (key,),
            )])
    except Exception as exc:
        logger.warning("AgentMail outbox persistence failed: %s", _safe_error(exc))
        return False

    try:
        result = send(
            actor,
            to_email,
            subject,
            body_text,
            html=html,
            idempotency_key=key,
            bcc=bcc,
        )
        if isinstance(result, bool):
            result = {"ok": result}
    except Exception as exc:
        result = {"ok": False, "error": _safe_error(exc)}

    status = "sent" if result.get("ok") else "failed"
    error = "" if status == "sent" else str(result.get("error") or "AgentMail send failed")[:500]
    sent_at = _now() if status == "sent" else None
    persisted = False
    try:
        execute_sync([(
            "UPDATE email_outbox SET status=?, error=?, sent_at=? WHERE id=?",
            (status, error, sent_at, key),
        )])
        persisted = True
    except Exception as exc:
        logger.warning("AgentMail outbox completion persistence failed: %s", _safe_error(exc))
    return status == "sent" and persisted
