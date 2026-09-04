"""Shared SQLite persistence layer for Agency OS.

Provides async SQLite connections using aiosqlite, with table initialization
for workspaces, agent outputs, reviews, error logs, agent messages,
agent knowledge, and CEO activity log.
"""

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite

_db: aiosqlite.Connection | None = None
_lock: asyncio.Lock | None = None
_persistent_mode: bool = False  # True when a long-running app (FastAPI) owns the loop


def set_persistent_mode(value: bool) -> None:
    """Mark the current loop as long-running (FastAPI) vs script.

    In persistent mode, fire-and-forget writes keep the shared connection
    open across calls. In script mode, the connection is closed after each
    write so the non-daemon aiosqlite thread does not keep the interpreter
    alive at exit.
    """
    global _persistent_mode
    _persistent_mode = value


def in_persistent_mode() -> bool:
    return _persistent_mode


def _get_lock() -> asyncio.Lock:
    """Return (or create) the module-level lock.

    Created lazily so the lock binds to the correct event loop on first use,
    avoiding cross-event-loop issues when the module is cached across
    separate ``asyncio.run()`` invocations.
    """
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock

DB_PATH = Path(__file__).resolve().parent.parent / "tags_agency_workspace.db"

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    client_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    agents TEXT NOT NULL DEFAULT '[]',
    client_context TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_outputs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT '',
    output TEXT NOT NULL DEFAULT '',
    output_preview TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    output_id TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL,
    feedback TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS error_logs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    error_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    routed_to TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    from_agent TEXT NOT NULL DEFAULT '',
    to_agent TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    message_type TEXT NOT NULL DEFAULT 'brief',
    subject TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    responded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agent_knowledge (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    learning TEXT NOT NULL DEFAULT '',
    source_workspace TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ceo_activity_log (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    agent_type TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL
);

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


def row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """Convert an aiosqlite Row to a plain dict."""
    return dict(row)


def rows_to_list(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    """Convert a list of aiosqlite Rows to a list of dicts."""
    return [dict(row) for row in rows]


async def get_workspace_db() -> aiosqlite.Connection:
    """Return the shared async SQLite connection, creating it if needed.

    Falls back to ``:memory:`` if the target DB path cannot be opened for
    writing (e.g. read-only filesystem, permission error).
    """
    global _db
    if _db is not None:
        return _db

    async with _get_lock():
        if _db is not None:
            return _db

        try:
            db_path_str = str(DB_PATH)
            _db = await aiosqlite.connect(db_path_str)
        except (OSError, RuntimeError):
            _db = await aiosqlite.connect(":memory:")

        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _db.commit()
        return _db


async def init_persistence() -> None:
    """Initialise all database tables.

    Must be called once at startup before using the database.
    """
    db = await get_workspace_db()
    await db.executescript(CREATE_TABLES_SQL)
    await db.commit()


async def close_persistence() -> None:
    """Close the shared database connection, if open."""
    global _db, _lock
    async with _get_lock():
        if _db is not None:
            await _db.close()
            _db = None
        _lock = None  # next asyncio.run() binds a fresh lock to its loop
