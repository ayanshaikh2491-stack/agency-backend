"""Shared persistence layer for Agency OS (the CEO's brain and memory).

Three backends, one connection surface:

  1. Cloudflare D1, used when the AGENCY_WORKSPACE_DB_D1_* variables are set
     and Turso is not. This is the production store. The backend service runs
     on Render's FREE plan, which has an EPHEMERAL disk: every redeploy deletes
     the filesystem. While the store was a local file, a fresh boot logged
     "Loaded from DB: 0 workspaces, 0 outputs, 0 reviews, 0 errors" and the CEO
     lost all memory on every single deploy.
  2. Turso / libSQL, used when TURSO_DATABASE_URL is set. Kept because the
     configuration and the driver path already exist, but the hosted server
     answers HTTP 401 for valid credentials, so it is not currently usable.
  3. Local SQLite via aiosqlite, used when neither remote is configured. Zero
     configuration local dev, but NOT durable on an ephemeral disk.

Every existing caller keeps working unchanged: `db = await get_workspace_db()`
then `db.execute(...)`, `db.executescript(...)`, `db.commit()`, `db.close()`.

SQL PORTABILITY
---------------
libSQL and D1 are both SQLite, so SQLite statement syntax parses on every
backend (INSERT OR REPLACE, INSERT OR IGNORE, CREATE TABLE IF NOT EXISTS,
sqlite_master introspection, positional "?" placeholders). The real traps are
the ones that are NOT syntax, and each is handled explicitly here:

  * Python bool. aiosqlite binds True as 1 because bool is an int subclass;
    the Rust libSQL binding rejects bool outright, and the D1 JSON encoder
    would send `true` where an INTEGER flag is expected. _normalise() coerces.
  * PRAGMAs. journal_mode=WAL is a local-file concept and is not applied on
    either remote path. foreign_keys is enforced by the server instead.
  * executescript(). aiosqlite has it, neither remote path does. See
    split_sql_script().
  * Transactions. commit() is a real COMMIT locally and a no-op on both remote
    paths, which apply each statement atomically server-side.

D1 SPECIFICS
------------
D1 is reached over the Cloudflare REST API, not a SQLite driver:

    POST https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{db}/query
    Authorization: Bearer {token}
    body: {"sql": "<one statement>", "params": [...]}

Three consequences are load-bearing and are handled in _D1Backend:

  * ONE STATEMENT PER REQUEST. executescript() therefore splits the script and
    issues the statements in order via split_sql_script(). This is also why
    init_persistence() pays N round trips once at boot, where the local file
    paid zero.
  * NO TRANSACTION SPANNING REQUESTS. There is no server-side session to hold
    open, so commit() is a no-op and multi-statement atomicity does not exist.
    Any caller that reads, decides and writes has a per-statement window.
  * EVERY STATEMENT IS AN HTTPS ROUND TRIP. That is the dominant cost. See the
    LATENCY note on _D1Backend.executemany.

This module does NOT silently downgrade. A configured-but-unreachable remote
raises at boot rather than quietly reopening a local file that the next deploy
will delete. See _open_turso() and _open_d1().
"""

import asyncio
import base64
import datetime as _dt
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite
import httpx

from admin.config import settings

logger = logging.getLogger("admin.persistence")

BACKEND_TURSO = "turso"
BACKEND_SQLITE = "sqlite"
BACKEND_D1 = "d1"

# Cloudflare D1 REST endpoint. Kept as a constant so a future regional or
# custom-domain proxy only has to change one line, and so the error messages
# and backend_info() cannot disagree about where requests go.
D1_API_BASE = "https://api.cloudflare.com/client/v4"

# Default per-request wall clock for a D1 call. D1 answers in well under a
# second from most regions, so anything above this is a wedged edge, not slow
# SQL. A timeout RAISES (see _D1Error); it never returns an empty result that a
# caller would read as "no rows".
D1_TIMEOUT_SECONDS = 15.0

# Bounded retry for HTTP 429 only. 0 by default: a rate limit that is retried
# silently is the failure mode this codebase keeps getting bitten by, so the
# first 429 raises with Cloudflare's own error text. Raise it deliberately on
# Render if the boot DDL script trips the free-tier rate limit.
D1_MAX_RETRIES = 0
D1_RETRY_BASE_SECONDS = 0.5

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
# plane. The fix is a remote store (see resolve_backend): set the
# AGENCY_WORKSPACE_DB_D1_* variables for Cloudflare D1, or TURSO_DATABASE_URL
# for libSQL, or point AGENCY_WORKSPACE_DB_PATH at durable storage.
if not settings.WORKSPACE_DB_SQLITE_PATH:
    logger.info(
        "Workspace DB is the default local path (%s). If this process runs on "
        "a host with an ephemeral disk, all workspace and autonomy state will "
        "be lost on every deploy. Set the AGENCY_WORKSPACE_DB_D1_* variables "
        "to use Cloudflare D1, or TURSO_DATABASE_URL for Turso/libSQL, or "
        "AGENCY_WORKSPACE_DB_PATH to point at durable storage.", DB_PATH,
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


def _setting(name: str, default: str = "") -> str:
    """Resolve a config value from admin.config.settings, else the environment.

    Same contract as admin/llm_throttle._setting: the central settings module
    is the documented home for configuration, and the environment default keeps
    a value configurable RIGHT NOW without editing that module. D1 lives here
    rather than in admin/config/settings.py because other work is in flight in
    that file; when it is moved, this function picks the new attribute up with
    no code change.
    """
    value = getattr(settings, name, None)
    if value is None or value == "":
        value = os.getenv(name, default)
    return str(value or "").strip()


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


# ── Cloudflare D1 configuration ─────────────────────────────────────────────
#
# Four variables, all optional individually and all required together:
#
#   AGENCY_WORKSPACE_DB_D1_TOKEN        Cloudflare API token (secret)
#   AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID   account id
#   AGENCY_WORKSPACE_DB_D1_DATABASE_ID  D1 database id
#   AGENCY_WORKSPACE_DB_D1_URL          optional, the account and database id
#                                       combined as one URL
#
# D1_CONFIG_URLS maps the query endpoint shape. {"account": .., "db": ..} is
# present iff BOTH ids are set. The account id is a UUID and the database id is
# a 32-character hex string, so neither can be confused for a path segment
# belonging to the other.
_D1_CONFIG_URLS = "/accounts/{account}/d1/database/{db}/query"


def parse_d1_url(url: str) -> dict[str, str]:
    """Pull (account, database) out of a D1 query URL. {} when unparseable.

    Accepted, in order of how the Cloudflare dashboard and CLI present them:
      https://api.cloudflare.com/client/v4/accounts/<acct>/d1/database/<db>/query
      .../accounts/<acct>/d1/database/<db>            (no /query suffix)
      <acct>/<db>
      <acct>:<db>

    The bare and colon forms exist because that is how the ids are displayed
    side by side, and copying them is the obvious mistake. Parsing them here
    turns a silent misconfiguration into a loud one at boot.

    TRAP: the marker test is `"/accounts/" IN url`, not `startswith`. The
    documented shape starts with a slash, but what an operator pastes starts
    with "https://", so a startswith test never matches a real URL and every
    full URL silently falls through to the bare-id branches and returns {}.
    """
    raw = (url or "").strip()
    if not raw:
        return {}

    marker = "/accounts/"
    if marker in raw:
        tail = raw.split(marker, 1)[1]
        account, sep, tail = tail.partition("/")
        if not sep:
            return {}
        # The leading slash was already consumed by the partition above, so
        # this must match "d1/database/" and NOT "/d1/database/". Matching the
        # slash-prefixed form is a second, quieter way to return {} for every
        # real URL.
        _d1, sep, tail = tail.partition("d1/database/")
        if not sep:
            return {}
        # The database id is one path segment, so an optional trailing
        # "/query" (or anything else after it) is not part of it.
        database = tail.split("/")[0]
        if account and database:
            return {"account": account, "db": database}
        return {}

    parts = [p for p in raw.rstrip("/").split("/") if p]
    if len(parts) == 2 and "://" not in raw and ":" not in parts[0]:
        return {"account": parts[0], "db": parts[1]}
    # Colon form only when there is no path at all. Without that guard
    # "https://api.cloudflare.com/" parses as account="https".
    if "/" not in raw and raw.count(":") == 1:
        account, _sep, database = raw.partition(":")
        if account and database:
            return {"account": account, "db": database}
    return {}


def d1_config() -> dict[str, str]:
    """Resolve the D1 connection settings. Every value is "" when unset.

    The separate id variables win over AGENCY_WORKSPACE_DB_D1_URL, so a URL
    that is left in place after the ids are filled in cannot silently redirect
    the store to a different database.
    """
    account = _setting("AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID")
    database = _setting("AGENCY_WORKSPACE_DB_D1_DATABASE_ID")
    from_url = parse_d1_url(_setting("AGENCY_WORKSPACE_DB_D1_URL"))
    return {
        "token": _setting("AGENCY_WORKSPACE_DB_D1_TOKEN"),
        "account": account or from_url.get("account", ""),
        "database": database or from_url.get("db", ""),
    }


def d1_endpoint(cfg: dict[str, str]) -> str:
    """The D1 query URL for a resolved config, or "" when it is incomplete."""
    account = cfg.get("account", "")
    database = cfg.get("database", "")
    if not account or not database:
        return ""
    return D1_API_BASE + _D1_CONFIG_URLS.format(account=account, db=database)


def d1_configured() -> bool:
    """True when all three D1 settings (token, account, database) are present."""
    cfg = d1_config()
    return bool(cfg["token"] and cfg["account"] and cfg["database"])


def d1_active() -> bool:
    """True when the workspace store resolves to Cloudflare D1.

    Advisory only, same contract as turso_active(): a half-configured D1
    reports False rather than raising, so a status page can ask without
    handling boot failures. resolve_backend() is the strict version.
    """
    try:
        return resolve_backend() == BACKEND_D1
    except RuntimeError:
        return False


def durable_active() -> bool:
    """True when the live store survives a redeploy (a remote backend).

    False means the CEO's memory is in a local file that the next Render
    deploy deletes. Deliberately never raises, for the same reason as the two
    helpers above.
    """
    try:
        return resolve_backend() in (BACKEND_D1, BACKEND_TURSO)
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

    Raises instead of degrading. A half-configured remote must fail at boot:
    quietly reopening a local file would look healthy and then hand the CEO an
    empty database on the next redeploy.

    "auto" precedence is Turso, then D1, then the local file. Turso keeps its
    existing precedence so a deployment that only has TURSO_* set does not
    change behaviour underneath the operator, but that means a Render service
    carrying BOTH sets still boots against Turso (and dies on its HTTP 401)
    until TURSO_DATABASE_URL is deleted. The warning below says so. Pin
    AGENCY_WORKSPACE_DB_BACKEND=d1 to make D1 win regardless.
    """
    url, token = turso_config()
    mode = settings.WORKSPACE_DB_BACKEND
    d1 = d1_config()

    if mode == BACKEND_D1:
        _require_d1_config(d1)
        return BACKEND_D1

    if mode == BACKEND_TURSO:
        if not url:
            raise RuntimeError(
                "AGENCY_WORKSPACE_DB_BACKEND=turso but TURSO_DATABASE_URL is "
                "not set. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in "
                "Render, point the store at D1 with "
                "AGENCY_WORKSPACE_DB_BACKEND=d1, or unset "
                "AGENCY_WORKSPACE_DB_BACKEND to use local SQLite."
            )
        return BACKEND_TURSO

    if mode == BACKEND_SQLITE:
        if url:
            logger.warning(
                "AGENCY_WORKSPACE_DB_BACKEND=sqlite is pinning the LOCAL file "
                "%s even though TURSO_DATABASE_URL is set. Writes here are "
                "lost on every redeploy on an ephemeral disk.", DB_PATH,
            )
        if d1_configured():
            logger.warning(
                "AGENCY_WORKSPACE_DB_BACKEND=sqlite is pinning the LOCAL file "
                "%s even though Cloudflare D1 is configured. Writes here are "
                "lost on every redeploy on an ephemeral disk.", DB_PATH,
            )
        return BACKEND_SQLITE

    if mode != "auto":
        raise RuntimeError(
            f"Unknown AGENCY_WORKSPACE_DB_BACKEND={mode!r}. "
            "Use 'auto', 'd1', 'turso' or 'sqlite'."
        )

    if not url:
        if _d1_any_set(d1):
            _require_d1_config(d1)
        return BACKEND_D1 if d1_configured() else BACKEND_SQLITE

    if d1_configured():
        logger.warning(
            "Both Turso and Cloudflare D1 are configured, and 'auto' resolves "
            "to Turso because TURSO_DATABASE_URL is set. Delete "
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN to move the store to D1, "
            "or set AGENCY_WORKSPACE_DB_BACKEND=d1 to force it."
        )
    if not token and url.startswith(_REMOTE_SCHEMES):
        raise RuntimeError(
            f"TURSO_DATABASE_URL={redact_url(url)} is a remote libSQL address "
            "but TURSO_AUTH_TOKEN is empty. Turso rejects unauthenticated "
            "remote connections. Refusing to fall back to a local SQLite "
            "file, which the next deploy would delete."
        )
    return BACKEND_TURSO


def _d1_any_set(cfg: dict[str, str]) -> bool:
    """True when at least one D1 variable is set, complete or not.

    This is what separates "D1 is not configured, use the local file" from
    "D1 is half configured, which is a boot failure". The second case matters:
    a token alone is enough to look configured in the Render dashboard while
    the store quietly stays on a file the next deploy deletes.
    """
    return bool(cfg.get("token") or cfg.get("account") or cfg.get("database"))


def _require_d1_config(cfg: dict[str, str]) -> None:
    """Raise naming every missing D1 variable, or return quietly.

    A partial D1 configuration is the exact failure this module refuses to
    paper over: the token alone is enough to look configured in the Render
    dashboard while the store silently stays on the local file, which the next
    deploy deletes.
    """
    missing = [
        name
        for name, value in (
            ("AGENCY_WORKSPACE_DB_D1_TOKEN", cfg.get("token", "")),
            ("AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID", cfg.get("account", "")),
            ("AGENCY_WORKSPACE_DB_D1_DATABASE_ID", cfg.get("database", "")),
        )
        if not value
    ]
    if not missing:
        return

    logger.error(
        "Cloudflare D1 is selected but not fully configured. Missing: %s. "
        "Set all three in Render (AGENCY_WORKSPACE_DB_D1_URL may supply the "
        "account and database id instead of the last two).", ", ".join(missing),
    )
    raise RuntimeError(
        f"Cloudflare D1 is selected but these are unset: {', '.join(missing)}. "
        "Set AGENCY_WORKSPACE_DB_D1_TOKEN, AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID "
        "and AGENCY_WORKSPACE_DB_D1_DATABASE_ID, or supply the account and "
        "database id through AGENCY_WORKSPACE_DB_D1_URL. Refusing to fall "
        f"back to a local SQLite file at {DB_PATH}: on an ephemeral disk "
        "(Render FREE) that file is deleted on the next deploy, so the CEO "
        "would silently restart from an empty database."
    )


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

    `kind` and `durable` are the two fields to read from outside. A healthy D1
    deployment answers {"kind": "d1", "durable": true}. The token is never
    included, only whether one was set.
    """
    url, token = turso_config()
    d1 = d1_config()
    durable = _backend_kind in (BACKEND_D1, BACKEND_TURSO)
    info: dict[str, Any] = {
        "kind": _backend_kind,
        "requested": settings.WORKSPACE_DB_BACKEND,
        "turso_configured": bool(url),
        "turso_auth_token_set": bool(token),
        "turso_url": redact_url(url),
        "d1_configured": d1_configured(),
        "d1_token_set": bool(d1["token"]),
        "d1_account_id": d1["account"],
        "d1_database_id": d1["database"],
        "d1_url": d1_endpoint(d1),
        "durable": durable,
        "ready": _db is not None,
    }
    if _backend_kind == BACKEND_TURSO:
        info["target"] = redact_url(build_libsql_url(url, token)).split("?")[0]
        info["transactions"] = "per-statement, server-side"
    elif _backend_kind == BACKEND_D1:
        info["target"] = d1_endpoint(d1) or "(d1 not fully configured)"
        # Stated out loud because a caller that reads then writes gets a
        # per-statement window here, not the all-or-nothing commit it gets on
        # the local file. See _D1Backend.commit().
        info["transactions"] = "per-statement, no cross-request transaction"
    else:
        info["target"] = str(DB_PATH)
        info["transactions"] = "client-side, real COMMIT"
        info["ephemeral_warning"] = (
            "Workspace store is a local SQLite file. On an ephemeral disk "
            "(Render FREE) every redeploy deletes it, and the CEO starts from "
            "an empty database. Set the AGENCY_WORKSPACE_DB_D1_* variables "
            "(with TURSO_DATABASE_URL unset) to fix this."
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
    """A result row from a remote backend (Turso/libSQL or Cloudflare D1).

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


class _D1Error(RuntimeError):
    """A Cloudflare D1 call failed. Carries the real Cloudflare error text.

    RuntimeError because that is what the rest of this module raises for a
    backend that cannot be opened, and callers already handle that. `status` is
    the HTTP status when there was one (429 for a rate limit, 401 for a bad
    token, 5xx for a Cloudflare edge problem) and None when the request never
    produced a response, which is how a refused connection or a timeout is
    reported. Both are real failures and both raise; neither is converted into
    an empty result set.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _d1_rowcount(result: Any) -> int:
    """Rows changed by one D1 statement, from its meta block.

    D1 reports "changes" for writes and "rows_read"/"rows_written" alongside
    it. rows_read is the honest count for a SELECT that read every row it
    scanned, so preferring changes and falling back to rows_read keeps
    rowcount meaningful on both a write and a read, which aiosqlite gives for
    free. Anything unrecognised is 0, never an exception: a missing meta block
    must not turn a successful statement into a failure.
    """
    meta = result.get("meta") if isinstance(result, dict) else None
    if not isinstance(meta, dict):
        return 0
    for key in ("changes", "rows_read"):
        value = meta.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return 0


def _d1_lastrowid(result: Any) -> Any:
    """The rowid of the last row inserted by one D1 statement, or None.

    D1 returns it as meta.last_row_id. The codebase does not rely on it
    (every table here has a TEXT primary key), so anything missing returns
    None rather than a fabricated 0 that would look like a real rowid.
    """
    meta = result.get("meta") if isinstance(result, dict) else None
    if isinstance(meta, dict):
        value = meta.get("last_row_id")
        if value is not None:
            return value
    return None


def _d1_columns(rows: list[Any]) -> list[str]:
    """Column names for one D1 result block, taken from its first row.

    D1 does not return a separate column list the way sqlite3.Cursor does.
    Instead every row is a JSON object keyed by column name, so the names are
    there: they are the first row's keys. TRAP: the ENVELOPE must not be
    mistaken for a row. The block is {"results": [...], "meta": {...}}, and
    reading its keys as columns would produce a row of ("results", "meta") for
    every result set, all values None. That is why this takes the ROW list and
    never the block.

    A row-less result has no columns, which is exactly what an INSERT or a DDL
    statement looks like and is what aiosqlite's description is not. Nothing in
    this codebase reads column names off an empty result, because there are no
    rows to read them from.
    """
    if rows and isinstance(rows[0], dict):
        return [str(k) for k in rows[0].keys()]
    return []


def _make_d1_client(timeout: float) -> Any:
    """Create the HTTP client _D1Backend talks through. THIS IS THE HTTP SEAM.

    A module-level function on purpose: it is the one place that touches httpx,
    so a test substitutes the transport by replacing this, rather than by
    patching sys.modules or reaching inside the class. Everything above it is
    transport-independent, which is the same split the Turso path gets for free
    from its SQLAlchemy dialect.
    """
    import httpx

    return httpx.AsyncClient(timeout=timeout)


class _D1Cursor:
    """Cursor facade over one already-fetched D1 result block.

    Same contract as _TursoCursor and aiosqlite.Cursor for everything the
    callers use: fetchone/fetchall/fetchmany, rowcount, lastrowid, close.
    There is no server-side cursor to close, because the HTTP response already
    carried the whole result set.
    """

    arraysize = 1

    def __init__(
        self,
        rows: list[Any] | None = None,
        rowcount: int = 0,
        lastrowid: Any = None,
    ) -> None:
        self._rowcount = rowcount
        self._lastrowid = lastrowid
        # Column names come from the rows, never from the response envelope.
        # See _d1_columns(): reading {"results", "meta"} as columns would hand
        # every caller a row of two Nones.
        columns = _d1_columns(rows or [])
        self._rows: list[_Row] = [
            _Row(columns, tuple(row.get(c) for c in columns)) for row in (rows or [])
        ]

    def rows(self) -> list[_Row]:
        """Every row, synchronously.

        D1 returned the whole result set inside the HTTP response, so nothing
        here needs to await. The async fetch* methods below exist only to match
        the aiosqlite surface the callers are already written against.
        """
        return self._rows

    async def fetchall(self) -> list[_Row]:
        return self._rows

    async def fetchone(self) -> _Row | None:
        return self._rows[0] if self._rows else None

    async def fetchmany(self, size: int | None = None) -> list[_Row]:
        return self._rows[: self.arraysize if size is None else size]

    @property
    def rowcount(self) -> int:
        return self._rowcount

    @property
    def lastrowid(self) -> Any:
        return self._lastrowid

    async def close(self) -> None:
        # The rows arrived with the HTTP response, so there is nothing to release.
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


class _D1Backend:
    """Cloudflare D1 over the REST API, one HTTPS request per statement.

    D1 speaks the same SQLite dialect as the other two backends, so every
    statement in this codebase runs unchanged, including positional "?"
    placeholders and sqlite_master introspection. What it does NOT speak is
    libSQL, so this uses HTTP rather than the sqlalchemy-libsql driver, and the
    three differences below are the whole reason this class exists rather than
    a configuration flag.

    ONE STATEMENT PER REQUEST
        D1's /query endpoint takes a single {"sql", "params"} object. A
        multi-statement script therefore has to be split (executescript, via
        the shared split_sql_script) and issued one request per statement, in
        order. init_persistence() pays that once at boot, and the CREATE
        TABLES script is a few dozen statements, so a cold D1 boot is slower
        than a cold local-file boot. It happens once, and it is far better
        than the alternative this module used to have.

    NO CROSS-REQUEST TRANSACTION
        There is no server-side session to BEGIN in and no client-side journal,
        so commit() below is a deliberate no-op. Concretely: a caller that
        reads a row, decides something and writes the row back has a window
        between the two requests in which another request, or another Render
        instance, can change that row. Single statements are still atomic,
        because D1 applies each one as its own transaction. Multi-statement
        atomicity simply does not exist here, and nothing in this class
        pretends otherwise.

    LATENCY
        Every statement is a full HTTPS round trip, roughly 100-300ms from a
        Render region. That is the whole cost model, and it makes the number of
        execute calls the thing to watch. A loop that issues N statements costs
        N round trips; there is no client-side batching to hide it and no
        cursor to page through. Call sites that sweep every workspace or every
        agent are the ones that get noticeably slower. Nothing in this layer
        hides or papers over that, and the hot spots are documented rather
        than worked around, because fixing them means changing the callers.

    Errors are never swallowed. A refused connection, a timeout, an HTTP 429
    rate limit and a SQL error all raise _D1Error carrying Cloudflare's own
    text. A failure never turns into an empty result set, because an empty
    result set is indistinguishable from "no rows" to every caller above.
    """

    def __init__(
        self,
        endpoint: str,
        token: str,
        client: Any = None,
        timeout: float = D1_TIMEOUT_SECONDS,
        max_retries: int = D1_MAX_RETRIES,
    ) -> None:
        self._endpoint = endpoint
        self._token = token
        self._client = client
        self._timeout = timeout
        self._max_retries = max(0, int(max_retries))
        self._closed = False

    # ── Transport ────────────────────────────────────────────────────────────

    def _json_safe(self, parameters: Sequence[Any]) -> list[Any]:
        """Coerce bound parameters into something the JSON body can carry.

        normalise_params() has already turned bool into int. A D1 body is JSON,
        so the remaining hazards are the stdlib types that have no JSON form
        and would otherwise raise a TypeError from inside the encoder, far from
        the statement that caused it: date and datetime become ISO strings,
        bytes become UTF-8, and anything else falls back to str(). The values
        in this codebase are strings, ints and None, so this is a guard rail
        rather than a routine conversion, and it never silently drops a value.
        """
        safe: list[Any] = []
        for value in parameters:
            if value is None or isinstance(value, (str, int, float, bool)):
                safe.append(value)
            elif isinstance(value, (bytes, bytearray, memoryview)):
                safe.append(bytes(value).decode("utf-8", "replace"))
            elif isinstance(value, _dt.datetime):
                safe.append(value.isoformat())
            elif isinstance(value, _dt.date):
                safe.append(value.isoformat())
            else:
                safe.append(str(value))
        return safe

    async def _post(self, body: dict[str, Any]) -> Any:
        """POST one statement to D1 and return the first result block.

        The returned value is the element of the response's "results" list that
        matches this request, which for the single-statement body is always the
        first one. The transport itself comes from _make_d1_client(); nothing
        above this line knows that HTTP exists.
        """
        if self._closed:
            raise _D1Error(
                "Cloudflare D1 connection is closed. This is a use-after-close "
                "in the caller, not a D1 outage."
            )
        payload = json.dumps(body)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        client = self._client
        if client is None:
            client = _make_d1_client(self._timeout)
            self._client = client
        try:
            response = await client.post(
                self._endpoint, content=payload, headers=headers,
            )
        except Exception as exc:  # noqa: BLE001
            # httpx raises a family of transport errors (timeout, connect,
            # read, proxy) that do not share a useful base class short of
            # Exception. Every one of them means the same thing to a caller:
            # the store did not answer, so this statement did not run. Raise
            # with the real text rather than letting it look like "no rows".
            raise _D1Error(
                f"Cloudflare D1 request to {redact_url(self._endpoint)} failed "
                f"for {_short_sql(str(body.get('sql', '')))}: "
                f"{type(exc).__name__}: {exc}. The statement did NOT run. No "
                "local fallback exists: a D1 write that fails here is lost, "
                "and a D1 read that fails here must not be read as empty.",
                status=None,
            ) from exc
        return _d1_result_block(response, self._endpoint, str(body.get("sql", "")))

    async def _post_with_retry(self, body: dict[str, Any]) -> Any:
        """_post, retrying HTTP 429 up to self._max_retries times.

        Off by default. Cloudflare rate-limits the free D1 tier and the boot
        DDL script is a burst, so the knob exists, but the first 429 raising
        with Cloudflare's own text is the default: a rate limit that is retried
        quietly is how a throttled store turns into a silently degraded one.
        Every attempt is logged, so a run that only passed on the last retry is
        visible in the boot log.
        """
        attempt = 0
        while True:
            try:
                return await self._post(body)
            except _D1Error as exc:
                if exc.status != 429 or attempt >= self._max_retries:
                    raise
                delay = D1_RETRY_BASE_SECONDS * (2 ** attempt)
                attempt += 1
                logger.warning(
                    "Cloudflare D1 rate limited (HTTP 429) on %s; retry %d of "
                    "%d in %.1fs. %s",
                    _short_sql(body.get("sql", "")), attempt, self._max_retries,
                    delay, exc,
                )
                await asyncio.sleep(delay)

    # ── Backend surface ──────────────────────────────────────────────────────

    async def execute(self, sql: str, parameters: Sequence[Any]) -> _D1Cursor:
        body = {"sql": sql, "params": self._json_safe(list(parameters or ()))}
        block = await self._post_with_retry(body)
        return _D1Cursor(
            rows=list(block.get("results") or []),
            rowcount=_d1_rowcount(block),
            lastrowid=_d1_lastrowid(block),
        )

    async def executemany(self, sql: str, seq_of_parameters: Sequence[Any]) -> Any:
        """One request per parameter set, in order, summing the per-row counts.

        The requests are issued sequentially rather than with asyncio.gather.
        That is deliberate and it is the "batch efficiently" part of the design:
        each request reuses the pooled AsyncClient, so there is no connection
        setup per row, and the parameters are JSON-encoded once per row with no
        re-parsing of the SQL. What it deliberately does NOT do is fire the rows
        concurrently. D1 applies each request as its own transaction, so two
        concurrent writes to the same table can collide, and a collision
        surfacing as a random _D1Error halfway through a batch would be far
        worse than a slower, ordered batch. The returned cursor reports the sum
        of every per-row result's meta.changes, and the last row's lastrowid,
        so rowcount stays meaningful instead of reporting only the final row.
        """
        cursor: _D1Cursor | None = None
        changes = 0
        for params in seq_of_parameters:
            body = {"sql": sql, "params": self._json_safe(list(params or ()))}
            block = await self._post_with_retry(body)
            changes += _d1_rowcount(block)
            cursor = _D1Cursor(
                rows=list(block.get("results") or []),
                rowcount=_d1_rowcount(block),
                lastrowid=_d1_lastrowid(block),
            )
        if cursor is None:
            # Zero rows is not an error, but it must not be None either: the
            # Turso and local paths both return a cursor here.
            return _D1Cursor(rowcount=0)
        return _D1Cursor(
            rows=cursor.rows(),
            rowcount=changes,
            lastrowid=cursor.lastrowid,
        )

    async def executescript(self, sql: str) -> Any:
        """Split the script and issue each statement as its own request.

        D1's /query endpoint takes one statement, and the shared
        split_sql_script() already handles the quoting and comment cases that a
        naive split on ";" would break. Statements are issued in order because
        DDL here is order-dependent (CREATE TABLE then CREATE INDEX on it).
        """
        statements = split_sql_script(sql)
        cursor: _D1Cursor | None = None
        changes = 0
        for statement in statements:
            cursor = await self.execute(statement, ())
            changes += cursor.rowcount
        if cursor is None:
            return _D1Cursor(rowcount=0)
        return _D1Cursor(
            rows=cursor.rows(),
            rowcount=changes,
            lastrowid=cursor.lastrowid,
        )

    async def commit(self) -> None:
        """NO-OP. D1 has no transaction to commit. Read this before relying on it.

        There is no server-side session to BEGIN in and no client-side journal,
        so there is nothing to commit. Every statement was already applied, and
        applied atomically, when its request returned.

        THE CONSEQUENCE, which callers must not forget: `await db.commit()` after
        a read-then-write sequence does NOT make that sequence atomic on D1. It
        is atomic on the local file, and the difference is invisible in the
        code. The write-after-read call sites that depend on it are listed in
        the D1 handover notes; the correct fix there is a single UPDATE with a
        WHERE guard on the value that was read, not a transaction wrapper,
        because this API cannot provide one.

        Nothing is logged on every call, because callers call it constantly and
        the noise would hide the boot log. backend_info() reports it instead, as
        transactions="per-statement, no cross-request transaction".
        """
        return None

    async def close(self) -> None:
        self._closed = True
        client, self._client = self._client, None
        if client is not None and hasattr(client, "aclose"):
            await client.aclose()


def _short_sql(sql: str, limit: int = 120) -> str:
    """One-line, length-capped SQL for an error message. Never a parameter."""
    text = " ".join(str(sql or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _d1_error_text(data: Any) -> str:
    """Flatten Cloudflare's {"errors": [{"code", "message"}]} into one string.

    Falls back to the raw body when the payload is not the documented shape,
    so an unexpected response still puts SOMETHING real in the exception rather
    than a generic "request failed".
    """
    errors = data.get("errors") if isinstance(data, dict) else None
    if errors is None:
        messages = data.get("messages") if isinstance(data, dict) else None
        errors = messages
    if isinstance(errors, list) and errors:
        parts: list[str] = []
        for item in errors:
            if isinstance(item, dict):
                code = item.get("code")
                message = item.get("message") or item.get("error")
                parts.append(f"{code}: {message}" if code else str(message))
            else:
                parts.append(str(item))
        return " | ".join(parts)
    if isinstance(errors, dict):
        return str(errors)
    return ""


def _d1_result_block(response: Any, endpoint: str, sql: str = "") -> dict[str, Any]:
    """Turn an httpx response into the D1 result block, or raise _D1Error.

    Four ways this raises, none of them quietly:
      * a non-2xx status, including 401 (bad token) and 429 (rate limited),
        carrying Cloudflare's own error text;
      * a 200 whose body has "success": false, which is how D1 reports a SQL
        error such as "no such table" while the transport itself succeeded;
      * a 200 whose body is not JSON at all, e.g. a captive portal or a proxy
        error page, which must not be mistaken for a successful empty query;
      * a 200 whose "result" is missing or empty, which would otherwise become
        a cursor with zero rows and read downstream as "no rows matched".

    Every message names the statement, because a D1 error with no SQL attached
    is not actionable and D1 gives no request id of its own to quote here.
    """
    status = getattr(response, "status_code", 0)
    try:
        data = json.loads(response.text)
    except ValueError:
        data = None

    where = f" while running {_short_sql(sql)!r}" if sql else ""

    if status < 200 or status >= 300:
        detail = _d1_error_text(data) or (response.text or "").strip()
        raise _D1Error(
            f"Cloudflare D1 returned HTTP {status} for {redact_url(endpoint)}"
            f"{where}: {_short_sql(detail) or '<empty body>'}",
            status=status,
        )

    if not isinstance(data, dict):
        raise _D1Error(
            f"Cloudflare D1 returned HTTP {status} for {redact_url(endpoint)}"
            f"{where} with a non-object body "
            f"({_short_sql(response.text, 200) or '<empty>'}). Expected the "
            "documented {success, result, errors} envelope; a proxy or "
            "captive portal answered instead of Cloudflare.",
            status=status,
        )

    if data.get("success") is False:
        raise _D1Error(
            f"Cloudflare D1 reported failure for {redact_url(endpoint)}{where}: "
            f"{_d1_error_text(data) or 'no error detail returned'}",
            status=status,
        )

    # D1's query envelope uses the singular key "result", not "results":
    #   {"success": true, "result": [{"results": [...], "success": true,
    #     "meta": {...}}], "errors": [], "messages": []}
    # Verified live against api.cloudflare.com/client/v4 on 2026-10-05. Only
    # the inner list is "results". Reading the outer key as "results" yields
    # None on every single call, so accept the documented singular first and
    # keep the plural as a tolerated alias rather than trusting either blindly.
    results = data.get("result")
    if not isinstance(results, list):
        results = data.get("results")
    if not isinstance(results, list) or not results:
        raise _D1Error(
            f"Cloudflare D1 returned no result block for "
            f"{redact_url(endpoint)}{where}. "
            f"envelope keys={sorted(data.keys())} "
            f"errors={_d1_error_text(data) or '[]'} messages="
            f"{_short_sql(json.dumps(data.get('messages') or []), 200)}",
            status=status,
        )

    block = results[0]
    if not isinstance(block, dict):
        raise _D1Error(
            f"Cloudflare D1 returned a malformed result block for "
            f"{redact_url(endpoint)}{where}: {_short_sql(json.dumps(block), 200)}",
            status=status,
        )
    return block


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

    @property
    def is_d1(self) -> bool:
        return self.kind == BACKEND_D1

    @property
    def is_remote(self) -> bool:
        """True for either remote store, i.e. anything that survives a redeploy."""
        return self.kind in (BACKEND_D1, BACKEND_TURSO)

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


async def _open_d1(cfg: dict[str, str] | None = None) -> _D1Backend:
    """Open the Cloudflare D1 store, or raise with the real error.

    There is deliberately no fallback to a local file, for exactly the reason
    _open_turso() has none. On Render's free plan the local file is deleted on
    the next deploy, so "D1 is configured but unreachable" has to stop the boot
    loudly. Losing the store visibly is the correct failure; silently diverging
    onto a file that will vanish is the bug this module exists to remove.

    The `SELECT 1` probe forces one HTTPS round trip NOW. Without it, a wrong
    token or a wrong database id would not surface until the first write, long
    after boot reported success, and the first symptom would be the CEO quietly
    having no memory rather than a visible crash.
    """
    cfg = cfg or d1_config()
    _require_d1_config(cfg)
    endpoint = d1_endpoint(cfg)
    logger.info("Workspace store: Cloudflare D1 via %s", redact_url(endpoint))

    try:
        import httpx  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Cloudflare D1 is configured but httpx is not installed. Add "
            "`httpx>=0.27.0` to requirements.txt."
        ) from exc

    timeout = _d1_float("AGENCY_WORKSPACE_DB_D1_TIMEOUT_SEC", D1_TIMEOUT_SECONDS)
    retries = _d1_int("AGENCY_WORKSPACE_DB_D1_MAX_RETRIES", D1_MAX_RETRIES)
    # The client is built here, not lazily inside _post, so the boot probe and
    # every later statement share one connection pool and a failed boot has a
    # single thing to close. _make_d1_client() is the HTTP seam.
    client = _make_d1_client(timeout)
    backend = _D1Backend(
        endpoint, cfg["token"], client=client, timeout=timeout, max_retries=retries,
    )

    # Boot probe with retries: network blips between Render and Cloudflare are
    # common. Retry up to 3 times with exponential backoff (1s, 2s, 4s).
    probe_retries = 3
    probe_base_delay = 1.0
    for attempt in range(probe_retries):
        try:
            await backend.execute("SELECT 1", ())
            break
        except _D1Error as exc:
            if attempt == probe_retries - 1:
                try:
                    await backend.close()
                except Exception as close_exc:  # noqa: BLE001
                    logger.warning(
                        "Could not close the D1 client after a failed boot probe: %s",
                        close_exc,
                    )
                raise RuntimeError(
                    f"Cannot reach Cloudflare D1 at {redact_url(endpoint)} after "
                    f"{probe_retries} attempts: {exc}. Refusing "
                    f"to fall back to a local SQLite file at {DB_PATH}: on an "
                    "ephemeral disk (Render FREE) that file is deleted on the next "
                    "deploy, so the CEO would silently restart from an empty database. "
                    "Check AGENCY_WORKSPACE_DB_D1_TOKEN (it needs the D1 Edit "
                    "permission on the account), AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID and "
                    "AGENCY_WORKSPACE_DB_D1_DATABASE_ID, or unset them deliberately to "
                    "run against local SQLite."
                ) from exc
            else:
                delay = probe_base_delay * (2 ** attempt)
                logger.warning(
                    "D1 boot probe failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, probe_retries, delay, exc
                )
                await asyncio.sleep(delay)

    return backend


def _d1_float(name: str, default: float) -> float:
    """Read a float knob, logging and ignoring a value it cannot parse.

    An unparseable timeout must not become a silent default, because the
    operator would then believe they configured something they did not.
    """
    raw = _setting(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not a number; using the default %s.", name, raw, default,
        )
        return default
    if value <= 0:
        logger.warning(
            "%s=%r must be greater than 0; using the default %s.", name, raw, default,
        )
        return default
    return value


def _d1_int(name: str, default: int) -> int:
    """Read a non-negative int knob, logging and ignoring an unusable value."""
    raw = _setting(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not an integer; using the default %s.", name, raw, default,
        )
        return default
    if value < 0:
        logger.warning(
            "%s=%r must not be negative; using the default %s.", name, raw, default,
        )
        return default
    return value


async def get_workspace_db() -> _WorkspaceDB:
    """Return the shared workspace connection, creating it if needed.

    Opens Cloudflare D1 or Turso/libSQL when either is configured, and the
    local SQLite file otherwise. Raises rather than degrading: see
    _open_d1(), _open_turso() and _open_local().
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
        elif kind == BACKEND_D1:
            backend = await _open_d1()
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

    On D1 this is the slowest boot in the system: CREATE_TABLES_SQL is a few
    dozen statements and D1 runs one per request, so it costs one round trip
    each. That is a one-off per process start and it is paid before the app
    serves traffic, which is the right place to pay it. If it ever becomes a
    problem the fix is to stop running DDL on every boot, not to skip the
    statements.
    """
    # Restore from KV backup if available (ephemeral disk protection)
    if _kv_configured():
        await _kv_download(DB_PATH)

    db = await get_workspace_db()
    await db.executescript(CREATE_TABLES_SQL)
    await db.commit()
    if db.is_turso:
        url, _token = turso_config()
        logger.info(
            "Workspace store is Turso/libSQL (%s). Tables are ready and this "
            "store survives redeploys.", redact_url(url),
        )
    elif db.is_d1:
        logger.info(
            "Workspace store is Cloudflare D1 (%s). Tables are ready and this "
            "store survives redeploys. D1 applies each statement on its own, "
            "so there is no multi-statement transaction: await db.commit() is "
            "a no-op here and a read-then-write has a window between its two "
            "requests.", d1_endpoint(d1_config()),
        )
    else:
        logger.info(
            "Workspace store is local SQLite (%s). Set the "
            "AGENCY_WORKSPACE_DB_D1_* variables, with TURSO_DATABASE_URL unset, "
            "before deploying to an ephemeral disk.", DB_PATH,
        )


# ── Cloudflare KV Backup/Restore ──────────────────────────────────────────────

_KV_NAMESPACE_ID = "5b16c98175e44680be0cf35f1be65e8f"
_KV_ACCOUNT_ID = "44f94d3a0d718f3192a26fe49401bdd9"
_KV_API_BASE = "https://api.cloudflare.com/client/v4"

_KV_KEY = "workspace_backup.db"  # single key storing the entire SQLite file


def _kv_configured() -> bool:
    """True when KV credentials are available via env."""
    return bool(os.getenv("CF_KV_TOKEN"))


def _kv_headers() -> dict[str, str]:
    tok = os.getenv("CF_KV_TOKEN")
    if not tok:
        raise RuntimeError("CF_KV_TOKEN not set")
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


async def _kv_upload(local_path: str) -> None:
    """Upload the local SQLite file to Cloudflare KV."""
    if not _kv_configured():
        return
    # Force WAL checkpoint so the main .db file has all changes
    try:
        import aiosqlite
        async with aiosqlite.connect(local_path) as conn:
            await conn.execute("PRAGMA wal_checkpoint(FULL)")
    except Exception:
        pass  # best effort
    data = open(local_path, "rb").read()
    import base64
    b64 = base64.b64encode(data).decode()
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{_KV_ACCOUNT_ID}/storage/kv/namespaces/{_KV_NAMESPACE_ID}/values/{_KV_KEY}"
    )
    headers = _kv_headers()
    body = json.dumps({"value": b64, "metadata": {"size": len(data)}}).encode()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.put(url, headers=headers, content=body)
        if r.status_code >= 300:
            logger.warning("KV backup upload failed: %s %s", r.status_code, r.text[:200])


async def _kv_download(local_path: str) -> bool:
    """Download the SQLite file from Cloudflare KV to local_path.
    Returns True if downloaded, False if not found or not configured.
    Short timeout (3s) to never block health checks.
    """
    if not _kv_configured():
        return False
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{_KV_ACCOUNT_ID}/storage/kv/namespaces/{_KV_NAMESPACE_ID}/values/{_KV_KEY}"
    )
    headers = _kv_headers()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url, headers=headers)
        if r.status_code == 404:
            return False
        if r.status_code >= 300:
            logger.warning("KV backup download failed: %s %s", r.status_code, r.text[:200])
            return False
        d = r.json()
        b64 = d.get("value") or d.get("result", {}).get("value")
        if not b64:
            return False
        import base64
        data = base64.b64decode(b64)
        with open(local_path, "wb") as f:
            f.write(data)
        logger.info("Restored workspace DB from KV (%d bytes)", len(data))
        return True
    except Exception as exc:
        logger.warning("KV download error (non-fatal): %s", exc)
        return False


# ── Close persistence with KV backup ──────────────────────────────────────────

async def close_persistence() -> None:
    """Close the shared database connection, if open, and backup to KV."""
    global _db, _lock, _backend_kind
    async with _get_lock():
        if _db is not None:
            await _db.close()
            _db = None
        _backend_kind = "uninitialised"
        _lock = None  # next asyncio.run() binds a fresh lock to its loop
    # Backup to KV after closing (ephemeral disk protection)
    if _kv_configured():
        await _kv_upload(DB_PATH)


# ── Sync escape hatch ───────────────────────────────────────────────────────


def execute_sync(statements: list[tuple[str, Sequence[Any]]]) -> list[list[Any]]:
    """Run raw (sql, params) statements on the workspace store from sync code.

    The AgentMail durable sender is a synchronous tool surface, and it used to
    open its own stdlib sqlite3 handle on DB_PATH. Under Turso that handle
    would point at a local file that the next deploy deletes, so the outbox
    would silently diverge from the real store. Routing it through here keeps
    one store.

    On a remote store (D1 or Turso) each statement runs on a throwaway event
    loop with its own connection, because the shared connection belongs to the
    caller's loop and reusing it from another loop corrupts both. On D1 that
    also means one HTTPS round trip per statement, including a `SELECT 1` boot
    probe, so execute_sync() is the most expensive call in the sync tool
    surface. The `await db.commit()` below is a no-op on D1: it exists for the
    local file and for Turso, and it is not evidence that these statements
    were applied atomically.

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

    async def _run_remote() -> list[list[Any]]:
        opened = await (_open_d1() if kind == BACKEND_D1 else _open_turso(*turso_config()))
        db = _WorkspaceDB(opened, kind)
        try:
            rows: list[list[Any]] = []
            for sql, params in statements:
                cursor = await db.execute(sql, params)
                rows.append(await cursor.fetchall())
            await db.commit()
            return rows
        finally:
            await db.close()

    return asyncio.run(_run_remote())
