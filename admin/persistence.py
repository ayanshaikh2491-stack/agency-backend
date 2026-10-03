"""Shared SQLite persistence layer for Agency OS.

Provides async SQLite connections using aiosqlite, with table initialization
for workspaces, agent outputs, reviews, error logs, agent messages,
agent knowledge, agent tasks, and the CEO autonomy control plane.

This module does NOT silently downgrade to an in-memory database when the
configured file cannot be opened. It raises unless AGENCY_ALLOW_MEMORY_DB is
explicitly set. See get_workspace_db() for why.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger("admin.persistence")

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

DB_PATH = Path(
    os.getenv("AGENCY_WORKSPACE_DB_PATH", "").strip()
    or str(Path(__file__).resolve().parent.parent / "tags_agency_workspace.db")
)

# Loud warning if we are on a path that a PaaS will throw away. Render, HF
# Spaces and most container hosts use an ephemeral disk, so every redeploy
# wipes anything written here — including the whole ceo_autonomy_* control
# plane. Point AGENCY_WORKSPACE_DB_PATH at durable storage to avoid this.
if not os.getenv("AGENCY_WORKSPACE_DB_PATH", "").strip():
    logger.info(
        "Workspace DB is the default local path (%s). If this process runs on "
        "a host with an ephemeral disk, all workspace and autonomy state will "
        "be lost on every deploy. Set AGENCY_WORKSPACE_DB_PATH to durable "
        "storage, or accept and document the loss.", DB_PATH,
    )

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

CREATE TABLE IF NOT EXISTS ceo_autonomy_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ceo_autonomy_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'system',
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    processed_at TEXT,
    error TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ceo_autonomy_events_pending
    ON ceo_autonomy_events(status, created_at);

CREATE TABLE IF NOT EXISTS ceo_autonomy_decisions (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ceo_autonomy_decisions_workspace
    ON ceo_autonomy_decisions(workspace_id, created_at);

CREATE TABLE IF NOT EXISTS ceo_autonomy_tasks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    agent_type TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ceo_autonomy_tasks_status
    ON ceo_autonomy_tasks(status, created_at);

CREATE TABLE IF NOT EXISTS ceo_autonomy_approvals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL,
    description TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT NOT NULL DEFAULT '',
    decision_reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ceo_autonomy_approvals_status
    ON ceo_autonomy_approvals(status, created_at);

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

CREATE TABLE IF NOT EXISTS service_deliveries (
    id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    client_name TEXT NOT NULL DEFAULT '',
    service_type TEXT NOT NULL DEFAULT 'website_seo_content',
    status TEXT NOT NULL DEFAULT 'queued',
    website_status TEXT NOT NULL DEFAULT 'pending',
    seo_status TEXT NOT NULL DEFAULT 'pending',
    content_status TEXT NOT NULL DEFAULT 'pending',
    output_dir TEXT NOT NULL DEFAULT '',
    report_path TEXT NOT NULL DEFAULT '',
    website_result TEXT NOT NULL DEFAULT '{}',
    seo_result TEXT NOT NULL DEFAULT '{}',
    content_result TEXT NOT NULL DEFAULT '{}',
    offer_value REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    followup_status TEXT NOT NULL DEFAULT 'ready',
    followup_due_at TEXT,
    followup_subject TEXT NOT NULL DEFAULT '',
    followup_body TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    agent_type TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    safe_only INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'queued',
    result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status, created_at);
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
        except (OSError, RuntimeError) as exc:
            # HISTORY OF A DATA-LOSS BUG (do not reintroduce): this silently
            # fell back to ":memory:". On a read-only filesystem, a full disk,
            # or a permission error -- exactly what an ephemeral container hits
            # -- the entire Agency OS swapped to a RAM database. init_persistence()
            # then ran CREATE TABLE against RAM and SUCCEEDED, so boot completed
            # cleanly and every workspace, agent output and the whole
            # ceo_autonomy_* control plane silently evaporated on restart.
            # There was no logger in this module at all, so it was invisible.
            #
            # Now an in-memory database requires an explicit opt-in, which is
            # only for tests. Production gets a loud failure at boot.
            if os.environ.get("AGENCY_ALLOW_MEMORY_DB", "").strip() in ("1", "true", "yes"):
                logger.warning(
                    "AGENCY_ALLOW_MEMORY_DB is set — using an IN-MEMORY database. "
                    "All data is discarded on exit. Never do this outside tests."
                )
                _db = await aiosqlite.connect(":memory:")
                in_memory = True
            else:
                raise RuntimeError(
                    f"Cannot open workspace database at {DB_PATH}: {exc}. "
                    "Refusing to fall back to an in-memory database, which would "
                    "silently discard every workspace and autonomy record on "
                    "restart. Fix the path/permissions, or set "
                    "AGENCY_ALLOW_MEMORY_DB=1 only for tests."
                ) from exc
        else:
            in_memory = False

        _db.row_factory = aiosqlite.Row
        if not in_memory:
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
