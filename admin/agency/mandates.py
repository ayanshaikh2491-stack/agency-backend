from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from admin.persistence import get_workspace_db, row_to_dict

CREATE_MANDATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mandates (
    worker TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    standing_task TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '{}',
    last_result TEXT,
    updated_at TEXT NOT NULL
)
"""


async def init_mandates_table() -> None:
    db = await get_workspace_db()
    await db.execute(CREATE_MANDATES_TABLE_SQL)
    await db.commit()


# Guard so the table is created on first use even if init_mandates_table()
# has not been called explicitly (e.g. tests or ad-hoc call paths).
_ensured: bool = False


async def _ensure_table() -> None:
    global _ensured
    if _ensured:
        return
    db = await get_workspace_db()
    await db.execute(CREATE_MANDATES_TABLE_SQL)
    await db.commit()
    _ensured = True


async def set_mandate(
    worker: str,
    status: str,
    standing_task: str,
    scope: dict[str, Any],
    last_result: str | None = None,
) -> dict[str, Any]:
    updated_at = datetime.now(timezone.utc).isoformat()
    scope_text = json.dumps(scope)
    await _ensure_table()
    db = await get_workspace_db()
    await db.execute(
        """
        INSERT INTO mandates (worker, status, standing_task, scope, last_result, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(worker) DO UPDATE SET
            status = excluded.status,
            standing_task = excluded.standing_task,
            scope = excluded.scope,
            last_result = excluded.last_result,
            updated_at = excluded.updated_at
        """,
        (worker, status, standing_task, scope_text, last_result, updated_at),
    )
    await db.commit()
    return await get_mandate(worker)  # type: ignore[return-value]


async def get_mandate(worker: str) -> dict[str, Any] | None:
    await _ensure_table()
    db = await get_workspace_db()
    async with db.execute(
        "SELECT * FROM mandates WHERE worker = ?", (worker,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _deserialize(row)


async def list_mandates() -> list[dict[str, Any]]:
    await _ensure_table()
    db = await get_workspace_db()
    async with db.execute("SELECT * FROM mandates ORDER BY worker") as cursor:
        rows = await cursor.fetchall()
    return [_deserialize(row) for row in rows]


async def clear_mandate(worker: str) -> bool:
    await _ensure_table()
    db = await get_workspace_db()
    result = await db.execute("DELETE FROM mandates WHERE worker = ?", (worker,))
    await db.commit()
    return result.rowcount > 0


def _deserialize(row: aiosqlite.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["scope"] = json.loads(data.get("scope") or "{}")
    return data
