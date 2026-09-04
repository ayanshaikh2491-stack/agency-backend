"""Queued email client — stores outbound emails instead of sending via SMTP.

Used by the CEO's email tool so the agency can queue client emails now and
flip to a real SMTP sender later without touching any agent code. The
interface mirrors ``admin.tools.sba_email_client.SBAEmailClient.send_email``
exactly, so it is a drop-in for ``SBAAutopilot(email_client=...)``.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueuedEmailClient:
    """Email client that queues messages to the SQLite outbox (no SMTP).

    Implements the same ``send_email(to, subject, body, cc_owner)`` surface as
    ``SBAEmailClient`` so it can be injected anywhere an email client is
    expected. Returns ``True`` when the message is queued.
    """

    def __init__(self, email: str | None = None, name: str | None = None, **_: Any) -> None:
        # ``email``/``name`` accepted for interface parity; the queued client
        # does not authenticate. Keep the displayed sender for the record.
        self.email = (email or "").strip()
        self.name = (name or "CEO").strip()
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return True

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        cc_owner: bool = True,
    ) -> bool:
        """Queue an outbound email instead of sending it.

        Returns True if successfully queued. Never raises.
        """
        try:
            await queue_email(
                to_email=to_email,
                subject=subject,
                body=body_text,
                from_agent=self.name or "ceo",
                workspace_id="",
            )
            logger.info("Email QUEUED to %s: %s", to_email, subject)
            return True
        except Exception:
            logger.exception("Failed to queue email to %s", to_email)
            return False

    async def check_replies(self, mark_read: bool = True) -> list[dict[str, Any]]:
        # Queued mode has no inbox to poll.
        return []


async def queue_email(
    to_email: str,
    subject: str,
    body: str,
    from_agent: str = "ceo",
    workspace_id: str = "",
    status: str = "pending",
) -> str:
    """Insert an email into the outbox table. Returns the new id."""
    from admin.persistence import get_workspace_db

    msg_id = f"em_{uuid.uuid4().hex[:12]}"
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
    """List queued/emitted emails (newest first)."""
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
    """Update an outbox row's status (e.g. sent / failed)."""
    from admin.persistence import get_workspace_db

    db = await get_workspace_db()
    await db.execute(
        "UPDATE email_outbox SET status=?, error=?, sent_at=? WHERE id=?",
        (status, error, sent_at or (_now() if status == "sent" else None), msg_id),
    )
    await db.commit()
    return True
