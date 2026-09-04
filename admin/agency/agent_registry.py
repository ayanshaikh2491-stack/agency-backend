"""Dynamic Agent Registry — Munder-style user-added agents.

Built-in employees (sba, seo, website, content, ads, social, analytics) are
registered in workers.register_builtins(). This module adds PERSISTENT,
user-created agents on top of them: a user can add/remove agents at runtime
(name, role, system prompt, model, api-key ref, tools) and the CEO + workers
layer picks them up automatically.

Storage: the same workspace SQLite DB used by mandates.py, so no new infra.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from admin.persistence import get_workspace_db, row_to_dict

logger = logging.getLogger(__name__)

CREATE_CUSTOM_AGENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS custom_agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    api_key_ref TEXT NOT NULL DEFAULT '',
    tools TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL
)
"""

_ensured: bool = False


async def _ensure_table() -> None:
    global _ensured
    if _ensured:
        return
    db = await get_workspace_db()
    await db.execute(CREATE_CUSTOM_AGENTS_TABLE_SQL)
    await db.commit()
    _ensured = True


def _new_id() -> str:
    return "agent_" + uuid.uuid4().hex[:12]


async def create_agent(
    name: str,
    role: str,
    system_prompt: str = "",
    model: str = "",
    api_key_ref: str = "",
    tools: list[str] | None = None,
    created_by: str = "owner",
) -> dict[str, Any]:
    """Add a new custom agent. Returns the stored record (with id)."""
    name = (name or "").strip()
    role = (role or "").strip()
    if not name or not role:
        raise ValueError("name and role are required")
    await _ensure_table()
    agent_id = _new_id()
    created_at = datetime.now(timezone.utc).isoformat()
    db = await get_workspace_db()
    await db.execute(
        """
        INSERT INTO custom_agents
            (id, name, role, system_prompt, model, api_key_ref, tools, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            agent_id, name, role, system_prompt, model, api_key_ref,
            json.dumps(tools or []), created_by, created_at,
        ),
    )
    await db.commit()
    stored = await get_agent(agent_id)  # type: ignore[return-value]
    if stored:
        _mirror_agent_to_pb(stored)
    return stored


async def get_agent(agent_id: str) -> dict[str, Any] | None:
    await _ensure_table()
    db = await get_workspace_db()
    async with db.execute(
        "SELECT * FROM custom_agents WHERE id = ?", (agent_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return _deserialize(row) if row else None


async def list_agents() -> list[dict[str, Any]]:
    await _ensure_table()
    db = await get_workspace_db()
    async with db.execute("SELECT * FROM custom_agents ORDER BY created_at") as cursor:
        rows = await cursor.fetchall()
    return [_deserialize(row) for row in rows]


async def delete_agent(agent_id: str) -> bool:
    await _ensure_table()
    db = await get_workspace_db()
    result = await db.execute("DELETE FROM custom_agents WHERE id = ?", (agent_id,))
    await db.commit()
    deleted = result.rowcount > 0
    if deleted:
        _mirror_agent_to_pb({"id": agent_id}, delete=True)
    return deleted


def _deserialize(row: aiosqlite.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    try:
        data["tools"] = json.loads(data.get("tools") or "[]")
    except (ValueError, TypeError):
        data["tools"] = []
    return data


# ── PocketBase: durable source of truth for custom agents ────────────────────
_PB_SCHEMA = {
    "record_id": "text", "name": "text", "role": "text",
    "system_prompt": "text", "model": "text", "api_key_ref": "text",
    "tools": "text", "created_by": "text", "created_at": "text",
}
_ensured_pb = False


def _mirror_agent_to_pb(record: dict[str, Any], delete: bool = False) -> None:
    """Persist one custom agent to a JSON file AND PocketBase (best-effort).

    Files (data/store/custom_agents/<id>.json) always work; PocketBase is the
    networked source of truth when configured.
    """
    global _ensured_pb
    try:
        from admin.file_store import delete_record as fs_del
        from admin.file_store import save_record as fs_save

        if delete:
            fs_del("custom_agents", str(record.get("id", "")))
        else:
            fs_save("custom_agents", str(record.get("id", "")), record)
    except Exception as exc:  # noqa: BLE001
        logger.debug("custom_agent file mirror failed (non-fatal): %s", exc)
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        if not _ensured_pb:
            pb.ensure_collection("custom_agents", _PB_SCHEMA)
            _ensured_pb = True
        if delete:
            pb.delete_by_key("custom_agents", "record_id", record.get("id", ""))
            return
        pb.upsert_by_key("custom_agents", "record_id", dict(record))
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase custom_agent mirror failed (non-fatal): %s", exc)


async def sync_from_pocketbase() -> int:
    """Boot-time pull: PocketBase custom_agents -> local SQLite table.

    PocketBase wins as source of truth; data/store JSON files fill any gaps
    (e.g. PB unreachable). Registry reads (get_agent/list_agents) keep working
    unchanged offline. Returns how many agents were pulled. Best-effort.
    """
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
    except Exception:  # noqa: BLE001
        pb = None
    pulled = 0
    try:
        await _ensure_table()
        db = await get_workspace_db()

        async def upsert_row(rec: dict, key: str) -> None:
            """One INSERT for both PB rows (key=record_id) and file rows."""
            tools = rec.get("tools")
            tools = json.dumps(tools) if isinstance(tools, list) else (tools or "[]")
            await db.execute(
                """
                INSERT OR REPLACE INTO custom_agents
                    (id, name, role, system_prompt, model, api_key_ref,
                     tools, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    rec.get("name") or key,
                    rec.get("role") or "worker",
                    rec.get("system_prompt") or "",
                    rec.get("model") or "",
                    rec.get("api_key_ref") or "",
                    tools,
                    rec.get("created_by") or "owner",
                    rec.get("created_at")
                    or datetime.now(timezone.utc).isoformat(),
                ),
            )

        if pb and pb.is_configured():
            for row in pb.pull_all("custom_agents"):
                rid = str(row.get("record_id") or "")
                if not rid:
                    continue
                await upsert_row(row, rid)
                pulled += 1

        # File-store fallback: restore ids still unknown locally.
        from admin.file_store import load_all as fs_load_all

        cur = await db.execute("SELECT id FROM custom_agents")
        known = {r[0] for r in await cur.fetchall()}
        for rec in fs_load_all("custom_agents"):
            rid = str(rec.get("id") or "")
            if not rid or rid in known:
                continue
            await upsert_row(rec, rid)
            known.add(rid)
            pulled += 1
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("PocketBase/file custom_agents seed failed (non-fatal): %s", exc)
    return pulled
