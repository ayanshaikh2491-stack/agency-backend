"""Structured multi-agent communication bus (governance doc #24).

The Agency OS routes agents through `manager.route_to_agent`, but until now
there was NO structured message log between agents (prev STATE.md falsely
claimed `agent_bus.py` existed — VERIFIED FALSE, file was absent).

This module provides a SQLite-backed bus so one agent can message another with
clear structure (doc #24): sender, receiver, workspace, task, objective,
context, required_action, result, status, errors, metadata.

Design:
- Self-contained: stdlib `sqlite3`, its own db file (`agent_bus.db` next to
  the existing `tags_agency.db`), created lazily. No new hard dependency.
- Workspace isolation (doc #25): every row carries `workspace`, and all query
  helpers are workspace-scoped by default.
- Persistent + loads on startup (doc #21): messages survive process restart.
- Low-risk / additive: nothing imports this yet; CEO delegation can opt-in to
  log via `bus.record(...)` without changing routing behavior.

API:
  - `get_bus()` -> AgentBus singleton (creates tables on first use).
  - `bus.brief(sender, receiver, workspace, task, objective="", context="",
        required_action="", metadata=None)` -> message_id
  - `bus.respond(message_id, result="", status="done", errors="")` -> updates row
  - `bus.parallel_blast(sender, workspace, receivers, task, objective="",
        context="")` -> [message_id,...]
  - `bus.share_knowledge(sender, workspace, topic, payload)` -> message_id
        (receiver="broadcast", stored in agent_knowledge too)
  - `bus.inbox(agent, workspace)` -> list of pending/active messages
  - `bus.thread(message_id)` -> full message dict
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Dedicated db file (additive; does not touch existing SQLite/Postgres stores).
_BUS_DB = os.environ.get(
    "AGENT_BUS_DB",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent_bus.db"),
)

_VALID_STATUSES = {"pending", "active", "done", "failed", "escalated"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (dict, list, str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


@dataclass
class AgentMessage:
    """A structured message between two agents (or a broadcast)."""

    id: str
    sender: str
    receiver: str
    workspace: str
    task: str
    objective: str = ""
    context: str = ""
    required_action: str = ""
    result: str = ""
    status: str = "pending"
    errors: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "receiver": self.receiver,
            "workspace": self.workspace,
            "task": self.task,
            "objective": self.objective,
            "context": self.context,
            "required_action": self.required_action,
            "result": self.result,
            "status": self.status,
            "errors": self.errors,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AgentBus:
    """SQLite-backed structured message bus (doc #24).

    Threadsafe via a single connection-per-bus + lock. Tables:
      agent_messages  — structured inter-agent messages
      agent_knowledge — shared_knowledge broadcasts (topic/payload)
    """

    def __init__(self, db_path: str = _BUS_DB) -> None:
        self._db_path = db_path
        # RLock: respond() acquires the lock and then calls thread(), which also
        # acquires it. A plain Lock would deadlock on re-entry from the same thread.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Avoid Windows/lock races when multiple connections touch the db:
        # wait (ms) instead of immediately raising "database is locked".
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._init_schema()

    # ── schema ──────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    task TEXT NOT NULL,
                    objective TEXT NOT NULL DEFAULT '',
                    context TEXT NOT NULL DEFAULT '',
                    required_action TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    errors TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_ws ON agent_messages(workspace)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_recv ON agent_messages(receiver, workspace)"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_know_ws ON agent_knowledge(workspace, topic)"
            )
            self._conn.commit()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _new_id(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + os.urandom(3).hex()

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> AgentMessage:
        try:
            meta = json.loads(row["metadata"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        return AgentMessage(
            id=row["id"],
            sender=row["sender"],
            receiver=row["receiver"],
            workspace=row["workspace"],
            task=row["task"],
            objective=row["objective"] or "",
            context=row["context"] or "",
            required_action=row["required_action"] or "",
            result=row["result"] or "",
            status=row["status"] or "pending",
            errors=row["errors"] or "",
            metadata=meta if isinstance(meta, dict) else {},
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    # ── public API ────────────────────────────────────────────────────────────

    def brief(
        self,
        sender: str,
        receiver: str,
        workspace: str,
        task: str,
        objective: str = "",
        context: str = "",
        required_action: str = "",
        metadata: Optional[dict[str, Any]] = None,
        status: str = "pending",
    ) -> str:
        """Send a structured brief from `sender` to `receiver` (doc #24)."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Valid: {sorted(_VALID_STATUSES)}")
        mid = self._new_id()
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_messages
                (id, sender, receiver, workspace, task, objective, context,
                 required_action, result, status, errors, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    sender,
                    receiver,
                    workspace,
                    task,
                    objective,
                    context,
                    required_action,
                    "",
                    status,
                    "",
                    json.dumps(metadata or {}, default=_json_default),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        logger.info("bus.brief %s -> %s [%s]: %s", sender, receiver, workspace, task[:60])
        _msg = self.thread(mid)
        _mirror_message(_msg.to_dict() if _msg else {})
        return mid

    def respond(
        self,
        message_id: str,
        result: str = "",
        status: str = "done",
        errors: str = "",
    ) -> Optional[AgentMessage]:
        """Receiver reports back (doc #24: result/status/errors)."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Valid: {sorted(_VALID_STATUSES)}")
        now = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM agent_messages WHERE id = ?", (message_id,)
            )
            if cur.fetchone() is None:
                logger.warning("bus.respond: unknown message_id %s", message_id)
                return None
            self._conn.execute(
                """
                UPDATE agent_messages
                SET result = ?, status = ?, errors = ?, updated_at = ?
                WHERE id = ?
                """,
                (result, status, errors, now, message_id),
            )
            self._conn.commit()
        msg = self.thread(message_id)
        _mirror_message(msg.to_dict() if msg else {})
        return msg

    def parallel_blast(
        self,
        sender: str,
        workspace: str,
        receivers: list[str],
        task: str,
        objective: str = "",
        context: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        """Brief multiple receivers at once (doc #24 parallel work)."""
        ids: list[str] = []
        for r in receivers:
            ids.append(
                self.brief(
                    sender, r, workspace, task, objective, context,
                    required_action="execute + respond", metadata=metadata,
                )
            )
        return ids

    def share_knowledge(
        self,
        sender: str,
        workspace: str,
        topic: str,
        payload: Any,
    ) -> str:
        """Broadcast knowledge to all agents in a workspace (doc #24)."""
        now = _now_iso()
        blob = json.dumps(payload, default=_json_default)
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_knowledge (sender, workspace, topic, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sender, workspace, topic, blob, now),
            )
            self._conn.commit()
        # Also store as a broadcast-style message so it shows in thread views.
        return self.brief(
            sender,
            "broadcast",
            workspace,
            f"knowledge:{topic}",
            objective=f"Shared knowledge on '{topic}'",
            context=blob[:2000],
            status="done",
            metadata={"kind": "knowledge", "topic": topic},
        )

    def inbox(self, agent: str, workspace: str, statuses: Optional[list[str]] = None) -> list[AgentMessage]:
        """Pending/active messages addressed to `agent` in `workspace` (doc #25)."""
        with self._lock:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                cur = self._conn.execute(
                    f"SELECT * FROM agent_messages WHERE receiver = ? AND workspace = ? "
                    f"AND status IN ({placeholders}) ORDER BY created_at ASC",
                    (agent, workspace, *statuses),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM agent_messages WHERE receiver = ? AND workspace = ? "
                    "ORDER BY created_at ASC",
                    (agent, workspace),
                )
            return [self._row_to_message(r) for r in cur.fetchall()]

    def thread(self, message_id: str) -> Optional[AgentMessage]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM agent_messages WHERE id = ?", (message_id,)
            )
            row = cur.fetchone()
            return self._row_to_message(row) if row else None

    def recent(self, workspace: str, limit: int = 50) -> list[AgentMessage]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM agent_messages WHERE workspace = ? ORDER BY created_at DESC LIMIT ?",
                (workspace, limit),
            )
            return [self._row_to_message(r) for r in cur.fetchall()]

    def knowledge(self, workspace: str, topic: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            if topic:
                cur = self._conn.execute(
                    "SELECT * FROM agent_knowledge WHERE workspace = ? AND topic = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (workspace, topic, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM agent_knowledge WHERE workspace = ? ORDER BY id DESC LIMIT ?",
                    (workspace, limit),
                )
            return [
                {
                    "id": r["id"],
                    "sender": r["sender"],
                    "workspace": r["workspace"],
                    "topic": r["topic"],
                    "payload": r["payload"],
                    "created_at": r["created_at"],
                }
                for r in cur.fetchall()
            ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ── durable mirroring (PocketBase + JSON files) ────────────────────────────

def _mirror_message(record: dict[str, Any]) -> None:
    """Mirror an agent bus message to a JSON file AND PocketBase.

    PocketBase is the durable source of truth; the JSON file under
    ``data/store/agent_messages`` is the boss-readable backup that works even
    when POCKETBASE_URL is unset. Both calls are best-effort and NEVER raise,
    so a mirror failure can never break the main bus path.

    The row dict is flattened to JSON-safe strings (nested dicts/lists are
    serialised, datetimes become ISO strings). Our own id is kept under the
    ``message_id`` key because PocketBase record ids are 15-char only.
    """
    try:
        payload: dict[str, Any] = {}
        for key in (
            "id", "sender", "receiver", "workspace", "task", "objective",
            "context", "required_action", "result", "status", "errors",
            "metadata", "created_at", "updated_at",
        ):
            val = record.get(key, "")
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif isinstance(val, (dict, list)):
                val = json.dumps(val, default=_json_default)
            elif val is None:
                val = ""
            else:
                val = str(val)
            payload[key] = val
        # Keep our id under "message_id" (PB ids are 15-char only).
        payload["message_id"] = payload.pop("id", "")
        # Never send our internal "id" to PB (would clash with its record id).
        payload.pop("id", None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent_messages mirror payload build failed (non-fatal): %s", exc)
        return
    try:
        from admin.file_store import save_record as _fs_save
        _fs_save("agent_messages", payload["message_id"], payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("file mirror (agent_messages) failed (non-fatal): %s", exc)
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        pb.upsert_by_key("agent_messages", "message_id", payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase mirror (agent_messages) failed (non-fatal): %s", exc)


_bus_singleton: Optional[AgentBus] = None
_bus_lock = threading.Lock()


def get_bus(db_path: Optional[str] = None) -> AgentBus:
    """Return the process-wide AgentBus, creating tables on first use.

    Pass `db_path` to override the default location (used by tests so the
    real `agent_bus.db` is never opened in CI / during test collection).
    """
    global _bus_singleton
    if _bus_singleton is None:
        with _bus_lock:
            if _bus_singleton is None:
                _bus_singleton = AgentBus(db_path) if db_path else AgentBus()
    return _bus_singleton
