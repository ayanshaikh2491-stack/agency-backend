"""Shared persistence layer for Agency OS (the CEO's brain and memory).

Two backends, one connection surface:

  1. Turso / libSQL, used when TURSO_DATABASE_URL is set. This is the
     production store. The backend service runs on Render's FREE plan, which
     has an EPHEMERAL disk: every redeploy deletes the filesystem. While the
     store was a local file, a fresh boot logged "Loaded from DB: 0
     workspaces, 0 outputs, 0 reviews, 0 errors" and the CEO lost all memory
     on every single deploy.
  2. Local SQLite via aiosqlite, used when Turso is not configured. Zero
     configuration local dev, but NOT durable on an ephemeral disk.

Every existing caller keeps working unchanged: `db = await get_workspace_db()`
then `db.execute(...)`, `db.executescript(...)`, `db.commit()`, `db.close()`.

SQL PORTABILITY
---------------
libSQL is a SQLite fork, so SQLite statement syntax parses on both backends
(INSERT OR REPLACE, INSERT OR IGNORE, CREATE TABLE IF NOT EXISTS). The real
traps are the ones that are NOT syntax, and each is handled explicitly here:

  * Python bool. aiosqlite binds True as 1 because bool is an int subclass;
    the Rust libSQL binding rejects bool outright. _normalise() coerces.
  * PRAGMAs. journal_mode=WAL is a local-file concept and is not applied on
    the remote path. foreign_keys is enforced by libSQL server-side instead.
  * executescript(). aiosqlite has it, the libSQL path does not. See
    split_sql_script().
  * Transactions. commit() is a real COMMIT locally and a no-op on Turso,
    which applies each statement atomically server-side.

This module does NOT silently downgrade. A configured-but-unreachable Turso
raises at boot rather than quietly reopening a local file that the next deploy
will delete. See _open_turso().
"""

import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

from admin.config import settings

logger = logging.getLogger("admin.persistence")

BACKEND_TURSO = "turso"
BACKEND_SQLITE = "sqlite"

_db: "_WorkspaceDB | None" = None
_lock: asyncio.Lock | None = None
_persistent_mode: bool = False  # True when a long-running app (FastAPI) owns the loop
_backend_kind: str = "uninitialised"  # what get_workspace_db() last opened


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
    settings.WORKSPACE_DB_SQLITE_PATH
    or str(Path(__file__).resolve().parent.parent / "tags_agency_workspace.db")
)

# Loud warning if we are on a path that a PaaS will throw away. Render, HF
# Spaces and most container hosts use an ephemeral disk, so every redeploy
# wipes everything written here, including the whole ceo_autonomy_* control
# plane. The fix is Turso (see resolve_backend), not a better local path.
if not settings.WORKSPACE_DB_SQLITE_PATH:
    logger.info(
        "Workspace DB is the default local path (%s). If this process runs on "
        "a host with an ephemeral disk, all workspace and autonomy state will "
        "be lost on every deploy. Set TURSO_DATABASE_URL to use Turso/libSQL, "
        "or AGENCY_WORKSPACE_DB_PATH to point at durable storage.", DB_PATH,
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


def row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a result row (aiosqlite.Row or _Row) to a plain dict."""
    return dict(row)


def rows_to_list(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert a list of result rows to a list of dicts."""
    return [dict(row) for row in rows]


# ── Backend selection ───────────────────────────────────────────────────────

_REMOTE_SCHEMES = ("libsql://", "libsqls://", "http://", "https://", "ws://", "wss://")


def turso_config() -> tuple[str, str]:
    """Return (TURSO_DATABASE_URL, TURSO_AUTH_TOKEN), stripped. "" when unset."""
    return (
        (settings.TURSO_DATABASE_URL or "").strip(),
        (settings.TURSO_AUTH_TOKEN or "").strip(),
    )


def turso_active() -> bool:
    """True when the workspace store resolves to Turso/libSQL.

    Advisory only. A misconfigured Turso (for example a remote URL with no
    auth token) reports False here rather than raising, so callers that just
    want to know which store is live can ask without handling boot failures.
    resolve_backend() is the strict version.
    """
    try:
        return resolve_backend() == BACKEND_TURSO
    except RuntimeError:
        return False


def redact_url(url: str) -> str:
    """Strip any credentials from a URL so it is safe to log."""
    if not url:
        return ""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    authority, slash, tail = rest.partition("/")
    if "@" in authority:
        authority = "***@" + authority.rsplit("@", 1)[1]
    return f"{scheme}://{authority}{slash}{tail}"


def resolve_backend() -> str:
    """Decide which backend the workspace store uses, or raise.

    Raises instead of degrading. A half-configured Turso deployment must fail
    at boot: quietly reopening a local file would look healthy and then hand
    the CEO an empty database on the next redeploy.
    """
    url, token = turso_config()
    mode = settings.WORKSPACE_DB_BACKEND

    if mode == BACKEND_TURSO:
        if not url:
            raise RuntimeError(
                "AGENCY_WORKSPACE_DB_BACKEND=turso but TURSO_DATABASE_URL is "
                "not set. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in "
                "Render, or unset AGENCY_WORKSPACE_DB_BACKEND to use local "
                "SQLite."
            )
        return BACKEND_TURSO

    if mode == BACKEND_SQLITE:
        if url:
            logger.warning(
                "AGENCY_WORKSPACE_DB_BACKEND=sqlite is pinning the LOCAL file "
                "%s even though TURSO_DATABASE_URL is set. Writes here are "
                "lost on every redeploy on an ephemeral disk.", DB_PATH,
            )
        return BACKEND_SQLITE

    if mode != "auto":
        raise RuntimeError(
            f"Unknown AGENCY_WORKSPACE_DB_BACKEND={mode!r}. "
            "Use 'auto', 'turso' or 'sqlite'."
        )

    if not url:
        return BACKEND_SQLITE
    if not token and url.startswith(_REMOTE_SCHEMES):
        raise RuntimeError(
            f"TURSO_DATABASE_URL={redact_url(url)} is a remote libSQL address "
            "but TURSO_AUTH_TOKEN is empty. Turso rejects unauthenticated "
            "remote connections. Refusing to fall back to a local SQLite "
            "file, which the next deploy would delete."
        )
    return BACKEND_TURSO


def build_libsql_url(url: str, token: str) -> str:
    """Build the sqlalchemy-libsql dialect URL for a Turso database.

    The host has to sit in the SQLAlchemy authority. Building it the other way
    round, "sqlite+libsql://libsql://my-db.turso.io?authToken=...", is what
    admin/config/settings.py used to produce, and
    sqlalchemy.engine.make_url rejects that string outright with
    "invalid literal for int() with base 10: ''". admin/database.py builds its
    engine from that URL at module scope, so the old shape took the whole
    backend down the moment TURSO_* was set. That is very likely why setting
    those variables "never worked" and stayed empty.

    Accepted inputs: libsql://host, libsqls://host, https://host, a bare host,
    and file:/path for an embedded local replica.

    AGENCY_WORKSPACE_DB_URL overrides the result entirely for anyone whose
    driver expects a different shape. The effective URL is logged at boot with
    the token redacted, so a mismatch shows up immediately instead of as a
    mysterious connection failure later.
    """
    if settings.WORKSPACE_DB_URL:
        return settings.WORKSPACE_DB_URL

    raw = (url or "").strip()
    if not raw:
        raise RuntimeError("TURSO_DATABASE_URL is empty")

    if raw.startswith("file:"):
        # Embedded local replica: sqlite+libsql:///<path>. No auth token.
        target = raw[len("file:"):]
        if not target.startswith("/"):
            target = "/" + target
    else:
        scheme, sep, rest = raw.partition("://")
        host = rest if sep else raw
        # Drop any path and any userinfo; only the host is dialable here.
        target = host.split("/", 1)[0].rsplit("@", 1)[-1]
        if sep and scheme.lower() not in {
            "libsql", "libsqls", "http", "https", "ws", "wss", "sqlite", "sqlite+libsql",
        }:
            logger.warning(
                "TURSO_DATABASE_URL uses an unexpected scheme %r; dialling the "
                "host only.", scheme,
            )

    if not target:
        raise RuntimeError(f"TURSO_DATABASE_URL={redact_url(raw)} has no host")

    dialect = f"sqlite+libsql://{target}"
    if not token:
        return dialect
    joiner = "&" if "?" in dialect else "?"
    return f"{dialect}{joiner}authToken={token}"


def backend_info() -> dict[str, Any]:
    """Describe the workspace store, for /api/status and operator triage.

    Safe to call before init_persistence(): `ready` is False and `kind` is
    "uninitialised", so an operator sees "configured but never connected"
    rather than a bare 500.
    """
    url, token = turso_config()
    durable = _backend_kind == BACKEND_TURSO
    info: dict[str, Any] = {
        "kind": _backend_kind,
        "requested": settings.WORKSPACE_DB_BACKEND,
        "turso_configured": bool(url),
        "turso_auth_token_set": bool(token),
        "turso_url": redact_url(url),
        "durable": durable,
        "ready": _db is not None,
    }
    if _backend_kind == BACKEND_TURSO:
        info["target"] = redact_url(build_libsql_url(url, token)).split("?")[0]
    else:
        info["target"] = str(DB_PATH)
        info["ephemeral_warning"] = (
            "Workspace store is a local SQLite file. On an ephemeral disk "
            "(Render FREE) every redeploy deletes it, and the CEO starts from "
            "an empty database. Set TURSO_DATABASE_URL to fix this."
        )
    return info


# ── Portability helpers ─────────────────────────────────────────────────────


def _normalise_value(value: Any) -> Any:
    """Coerce one bound parameter to a type both backends accept.

    TRAP: bool is a subclass of int, so aiosqlite happily binds True as 1 and
    a bool column writes correctly on local SQLite. The Rust libSQL binding
    rejects bool outright, so the exact same statement would raise on Turso.
    The columns here are INTEGER flags (reviewed, resolved, read, responded,
    safe_only), so bool -> int is the correct translation, not a guess.
    """
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def normalise_params(parameters: Any) -> list[Any] | dict[str, Any]:
    """Normalise a parameter sequence (or mapping) for either backend."""
    if parameters is None:
        return []
    if isinstance(parameters, dict):
        return {k: _normalise_value(v) for k, v in parameters.items()}
    if isinstance(parameters, (list, tuple)):
        return [_normalise_value(p) for p in parameters]
    return [_normalise_value(parameters)]


def split_sql_script(script: str) -> list[str]:
    """Split a multi-statement DDL script into individual statements.

    TRAP: aiosqlite has executescript(); the libSQL path does not, so the
    script has to be split by hand. Splitting naively on ";" would break on
    any string literal containing a semicolon, so quoting and comments are
    tracked here.
    """
    statements: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    length = len(script)
    while i < length:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < length else ""
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                if nxt == quote:  # doubled quote is an escaped quote
                    buf.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-":
            newline = script.find("\n", i)
            i = length if newline == -1 else newline + 1
            continue
        if ch == "/" and nxt == "*":
            end = script.find("*/", i + 2)
            i = length if end == -1 else end + 2
            continue
        if ch == ";":
            statements.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf)
    if tail.strip():
        statements.append(tail)
    return [s for s in (part.strip() for part in statements) if s]


# ── Result rows and cursors ─────────────────────────────────────────────────


class _Row(Sequence[Any]):
    """A result row from the Turso backend.

    Mirrors the sqlite3.Row surface the rest of the codebase relies on:
    positional access, column-name access, iteration, and `dict(row)` (which
    works because dict() uses the keys() + __getitem__ mapping protocol).
    """

    __slots__ = ("_columns", "_values")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = list(columns)
        self._values = list(values)

    def keys(self) -> list[str]:
        return list(self._columns)

    def values(self) -> list[Any]:
        return list(self._values)

    def items(self) -> list[tuple[str, Any]]:
        return list(zip(self._columns, self._values))

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._columns.index(key)]
        except ValueError:
            raise KeyError(key) from None

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key: Any) -> bool:
        return key in self._columns

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, _Row):
            return self._values == other._values
        if isinstance(other, (tuple, list)):
            return self._values == list(other)
        return NotImplemented

    def __repr__(self) -> str:
        return f"_Row({dict(zip(self._columns, self._values))!r})"


class _TursoCursor:
    """Cursor facade over a SQLAlchemy CursorResult from the libSQL dialect."""

    arraysize = 1

    def __init__(self, result: Any) -> None:
        self._result = result
        self._rows: list[_Row] | None = None

    def _materialise(self) -> list[_Row]:
        if self._rows is None:
            keys = [str(k) for k in (self._result.keys() or [])]
            self._rows = [_Row(keys, tuple(row)) for row in self._result.fetchall()]
        return self._rows

    async def fetchall(self) -> list[_Row]:
        return self._materialise()

    async def fetchone(self) -> _Row | None:
        rows = self._materialise()
        return rows[0] if rows else None

    async def fetchmany(self, size: int | None = None) -> list[_Row]:
        rows = self._materialise()
        return rows[: self.arraysize if size is None else size]

    @property
    def rowcount(self) -> int:
        return self._result.rowcount

    @property
    def lastrowid(self) -> Any:
        return getattr(self._result, "lastrowid", None)

    async def close(self) -> None:
        # Rows are already fully materialised, so there is nothing to release.
        return None


class _Statement:
    """A pending statement: awaitable AND async context manager.

    aiosqlite hands back an object usable in either shape, and call sites use
    both:
        cursor = await db.execute(sql, params)
        async with db.execute(sql, params) as cursor: ...
    """

    __slots__ = ("_coro", "_cursor")

    def __init__(self, coro: Any) -> None:
        self._coro = coro
        self._cursor: Any = None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self) -> Any:
        self._cursor = await self._coro
        return self._cursor

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._cursor is not None:
            await self._cursor.close()
            self._cursor = None
        return False


# ── Backends ────────────────────────────────────────────────────────────────


class _LocalBackend:
    """Local SQLite file through aiosqlite. Zero-config dev behaviour.

    Delegates straight through, so aiosqlite.Row and aiosqlite.Cursor are what
    callers see. That keeps local dev byte-for-byte identical to the pre-Turso
    code path.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def execute(self, sql: str, parameters: Sequence[Any]) -> Any:
        return await self._conn.execute(sql, parameters)

    async def executemany(self, sql: str, seq_of_parameters: Sequence[Any]) -> Any:
        return await self._conn.executemany(sql, seq_of_parameters)

    async def executescript(self, sql: str) -> Any:
        return await self._conn.executescript(sql)

    async def commit(self) -> None:
        await self._conn.commit()

    async def close(self) -> None:
        await self._conn.close()


class _TursoBackend:
    """Remote Turso/libSQL store via the sqlalchemy-libsql async dialect.

    Uses exec_driver_sql so the raw SQLite statements in this codebase keep
    working with their positional "?" placeholders. SQLAlchemy's text() would
    require rewriting every one of them to :name style.
    """

    def __init__(self, engine: Any, conn: Any) -> None:
        self._engine = engine
        self._conn = conn

    async def execute(self, sql: str, parameters: Sequence[Any]) -> _TursoCursor:
        args = tuple(parameters) if parameters else None
        result = await self._conn.exec_driver_sql(sql, args)
        return _TursoCursor(result)

    async def executemany(self, sql: str, seq_of_parameters: Sequence[Any]) -> Any:
        # exec_driver_sql takes one parameter set, so this loops. libSQL
        # applies each statement atomically anyway; see commit().
        cursor = None
        for params in seq_of_parameters:
            cursor = await self.execute(sql, params)
        return cursor

    async def executescript(self, sql: str) -> Any:
        cursor = None
        for statement in split_sql_script(sql):
            cursor = await self.execute(statement, ())
        return cursor

    async def commit(self) -> None:
        """Commit, if the driver has a transaction open.

        TRAP: libSQL over HTTP has no client-visible transaction to commit.
        Each statement is applied atomically server-side as it arrives, so
        there is nothing to do here and this is a no-op for Turso while
        remaining a real COMMIT for local SQLite. The consequence is that
        multi-statement atomicity (the autonomy claim read-then-write, for
        one) is per-statement on Turso.
        """
        if self._conn.in_transaction():
            await self._conn.commit()

    async def close(self) -> None:
        await self._conn.close()
        await self._engine.dispose()


class _WorkspaceDB:
    """Backend-agnostic handle returned by get_workspace_db().

    Deliberately shaped like an aiosqlite.Connection so that every existing
    caller keeps working with no change.
    """

    def __init__(self, backend: Any, kind: str) -> None:
        self._backend = backend
        self.kind = kind
        self.row_factory: Any = None  # accepted for aiosqlite compatibility

    @property
    def is_turso(self) -> bool:
        return self.kind == BACKEND_TURSO

    def execute(self, sql: str, parameters: Any = ()) -> _Statement:
        return _Statement(self._backend.execute(sql, normalise_params(parameters)))

    def executemany(self, sql: str, seq_of_parameters: Sequence[Any]) -> _Statement:
        rows = [normalise_params(p) for p in seq_of_parameters]
        return _Statement(self._backend.executemany(sql, rows))

    def executescript(self, sql: str) -> _Statement:
        return _Statement(self._backend.executescript(sql))

    async def commit(self) -> None:
        await self._backend.commit()

    async def close(self) -> None:
        await self._backend.close()


# ── Connection lifecycle ────────────────────────────────────────────────────


async def _open_local() -> _LocalBackend:
    """Open the local SQLite file, or raise.

    This module does NOT silently downgrade to an in-memory database when the
    configured file cannot be opened for writing. See the guard below.
    """
    try:
        conn = await aiosqlite.connect(str(DB_PATH))
        in_memory = False
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        # HISTORY OF A DATA-LOSS BUG (do not reintroduce): this silently fell
        # back to ":memory:". On a read-only filesystem, a full disk, or a
        # permission error -- exactly what an ephemeral container hits -- the
        # entire Agency OS swapped to a RAM database. init_persistence() then
        # ran CREATE TABLE against RAM and SUCCEEDED, so boot completed cleanly
        # and every workspace, agent output and the whole ceo_autonomy_*
        # control plane silently evaporated on restart.
        #
        # sqlite3.Error is caught as well as OSError because sqlite3 reports
        # "unable to open database file" as sqlite3.OperationalError, which is
        # neither. Without it this guard is unreachable for the exact case it
        # was written for, and the traceback escapes instead.
        #
        # Now an in-memory database requires an explicit opt-in, which is only
        # for tests. Production gets a loud failure at boot.
        if os.environ.get("AGENCY_ALLOW_MEMORY_DB", "").strip() in ("1", "true", "yes"):
            logger.warning(
                "AGENCY_ALLOW_MEMORY_DB is set, using an IN-MEMORY database. "
                "All data is discarded on exit. Never do this outside tests."
            )
            conn = await aiosqlite.connect(":memory:")
            in_memory = True
        else:
            raise RuntimeError(
                f"Cannot open workspace database at {DB_PATH}: {exc}. "
                "Refusing to fall back to an in-memory database, which would "
                "silently discard every workspace and autonomy record on "
                "restart. Fix the path/permissions, or set "
                "AGENCY_ALLOW_MEMORY_DB=1 only for tests."
            ) from exc

    conn.row_factory = aiosqlite.Row
    if not in_memory:
        # TRAP: journal_mode=WAL is a local-file concept. libSQL is remote and
        # this pragma is simply not applied there, so it stays local-only.
        await conn.execute("PRAGMA journal_mode=WAL")
    # libSQL enforces foreign keys server-side, so this pragma is also
    # local-only in effect.
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.commit()
    return _LocalBackend(conn)


async def _open_turso(url: str, token: str) -> _TursoBackend:
    """Open the remote store, or raise with the real error.

    There is deliberately no fallback to a local file. On Render's free plan a
    local file is deleted on the next deploy, so "Turso is configured but
    unreachable" has to stop the boot loudly. Losing the store visibly is the
    correct failure; silently diverging onto a file that will vanish is the
    bug being removed.
    """
    try:
        import sqlalchemy_libsql  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "TURSO_DATABASE_URL is set but the libSQL dialect is not "
            "installed. Add `sqlalchemy-libsql>=0.1.0` to requirements.txt."
        ) from exc

    dialect_url = build_libsql_url(url, token)
    logger.info("Workspace store: Turso/libSQL via %s", redact_url(dialect_url))

    engine = None
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(dialect_url, echo=False)
        conn = await engine.connect()
        # Force one round trip now so an unreachable host fails HERE, at boot,
        # rather than later on the first write with a half-open connection.
        # CursorResult.fetchall() is synchronous; only the driver call is not.
        result = await conn.exec_driver_sql("SELECT 1")
        result.fetchall()
    except Exception as exc:
        if engine is not None:
            await engine.dispose()
        raise RuntimeError(
            f"Cannot reach Turso at {redact_url(dialect_url)}: {exc}. Refusing "
            f"to fall back to a local SQLite file at {DB_PATH}: on an ephemeral "
            "disk (Render FREE) that file is deleted on the next deploy, so "
            "the CEO would silently restart from an empty database. Fix "
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN, or unset them "
            "deliberately to run against local SQLite."
        ) from exc

    return _TursoBackend(engine, conn)


async def get_workspace_db() -> _WorkspaceDB:
    """Return the shared workspace connection, creating it if needed.

    Opens Turso/libSQL when it is configured, and the local SQLite file
    otherwise. Raises rather than degrading: see _open_turso() and _open_local().
    """
    global _db, _backend_kind
    if _db is not None:
        return _db

    async with _get_lock():
        if _db is not None:
            return _db

        kind = resolve_backend()
        if kind == BACKEND_TURSO:
            backend = await _open_turso(*turso_config())
        else:
            backend = await _open_local()
        _backend_kind = kind
        _db = _WorkspaceDB(backend, kind)
        return _db


async def init_persistence() -> None:
    """Initialise all database tables.

    Must be called once at startup before using the database. Logs which
    store went live, so the backend is visible in the boot log and not only in
    the code.
    """
    db = await get_workspace_db()
    await db.executescript(CREATE_TABLES_SQL)
    await db.commit()
    if db.is_turso:
        url, _token = turso_config()
        logger.info(
            "Workspace store is Turso/libSQL (%s). Tables are ready and this "
            "store survives redeploys.", redact_url(url),
        )
    else:
        logger.info(
            "Workspace store is local SQLite (%s). Set TURSO_DATABASE_URL and "
            "TURSO_AUTH_TOKEN before deploying to an ephemeral disk.", DB_PATH,
        )


async def close_persistence() -> None:
    """Close the shared database connection, if open."""
    global _db, _lock, _backend_kind
    async with _get_lock():
        if _db is not None:
            await _db.close()
            _db = None
        _backend_kind = "uninitialised"
        _lock = None  # next asyncio.run() binds a fresh lock to its loop


# ── Sync escape hatch ───────────────────────────────────────────────────────


def execute_sync(statements: list[tuple[str, Sequence[Any]]]) -> list[list[Any]]:
    """Run raw (sql, params) statements on the workspace store from sync code.

    The AgentMail durable sender is a synchronous tool surface, and it used to
    open its own stdlib sqlite3 handle on DB_PATH. Under Turso that handle
    would point at a local file that the next deploy deletes, so the outbox
    would silently diverge from the real store. Routing it through here keeps
    one store.

    On Turso each statement runs on a throwaway event loop with its own
    connection, because the shared connection belongs to the caller's loop and
    reusing it from another loop corrupts both.

    Returns one list of rows per statement.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "execute_sync() was called from inside a running event loop. "
            "Use `await get_workspace_db()` instead."
        )

    kind = resolve_backend()
    if kind == BACKEND_SQLITE:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            out: list[list[Any]] = []
            for sql, params in statements:
                cursor = conn.execute(sql, normalise_params(params))
                out.append(cursor.fetchall())
            conn.commit()
            return out
        finally:
            conn.close()

    async def _run_turso() -> list[list[Any]]:
        db = _WorkspaceDB(await _open_turso(*turso_config()), kind)
        try:
            rows: list[list[Any]] = []
            for sql, params in statements:
                cursor = await db.execute(sql, params)
                rows.append(await cursor.fetchall())
            await db.commit()
            return rows
        finally:
            await db.close()

    return asyncio.run(_run_turso())
