from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from admin.persistence import get_workspace_db, row_to_dict

DDL = """
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '{}',
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_plan (
    agent TEXT PRIMARY KEY,
    plan TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS agent_reflection (
    agent TEXT PRIMARY KEY,
    reflections TEXT NOT NULL DEFAULT '[]'
);
"""


async def init_memory_tables() -> None:
    """Create the agent memory, plan, and reflection tables."""
    db = await get_workspace_db()
    await db.executescript(DDL)
    await db.commit()


async def record_event(
    agent: str, kind: str, text: str, scope: dict | None = None
) -> None:
    """Insert a memory event for an agent (scope stored as JSON)."""
    db = await get_workspace_db()
    ts = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO agent_memory (agent, kind, text, scope, ts) "
        "VALUES (?, ?, ?, ?, ?)",
        (agent, kind, text, json.dumps(scope or {}), ts),
    )
    await db.commit()


async def set_plan(agent: str, plan: list[str]) -> None:
    """Upsert the agent's plan (stored as JSON)."""
    db = await get_workspace_db()
    await db.execute(
        "INSERT INTO agent_plan (agent, plan) VALUES (?, ?) "
        "ON CONFLICT(agent) DO UPDATE SET plan = excluded.plan",
        (agent, json.dumps(plan)),
    )
    await db.commit()


async def get_plan(agent: str) -> list[str]:
    """Return the agent's plan, or an empty list if none."""
    db = await get_workspace_db()
    async with db.execute(
        "SELECT plan FROM agent_plan WHERE agent = ?", (agent,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return []
    return json.loads(row["plan"])


async def add_reflection(agent: str, summary: str) -> None:
    """Append a reflection (cap last 20) and upsert."""
    db = await get_workspace_db()
    async with db.execute(
        "SELECT reflections FROM agent_reflection WHERE agent = ?", (agent,)
    ) as cur:
        row = await cur.fetchone()
    existing: list[str] = json.loads(row["reflections"]) if row else []
    existing.append(summary)
    reflections = existing[-20:]
    await db.execute(
        "INSERT INTO agent_reflection (agent, reflections) VALUES (?, ?) "
        "ON CONFLICT(agent) DO UPDATE SET reflections = excluded.reflections",
        (agent, json.dumps(reflections)),
    )
    await db.commit()


async def get_memory(agent: str) -> dict[str, Any]:
    """Return the agent's memory: stream (latest 200), plan, reflections."""
    db = await get_workspace_db()
    stream: list[dict[str, Any]] = []
    async with db.execute(
        "SELECT id, agent, kind, text, scope, ts FROM agent_memory "
        "WHERE agent = ? ORDER BY id DESC LIMIT 200",
        (agent,),
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        entry = row_to_dict(row)
        try:
            entry["scope"] = json.loads(entry["scope"])
        except (TypeError, ValueError):
            entry["scope"] = {}
        stream.append(entry)
    plan = await get_plan(agent)
    async with db.execute(
        "SELECT reflections FROM agent_reflection WHERE agent = ?", (agent,)
    ) as cur:
        rrow = await cur.fetchone()
    reflections = json.loads(rrow["reflections"]) if rrow else []
    return {"stream": stream, "plan": plan, "reflections": reflections}
