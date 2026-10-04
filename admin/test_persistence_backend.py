"""Tests for the Turso/libSQL <-> local SQLite workspace store switch.

Both modes are covered:

  * TURSO_DATABASE_URL unset  -> local aiosqlite file, zero configuration.
  * TURSO_DATABASE_URL set    -> Turso/libSQL.

The Turso path never touches the network. sqlalchemy_libsql is not installed
in the test environment and the engine it drives is replaced with a fake that
executes the SQL against a real in-memory sqlite3 database. That is deliberate:
it proves the statements in this codebase actually run on the libSQL path
(same SQLite dialect, no executescript, no PRAGMA, bools normalised) without
making a single outbound call.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import types
from pathlib import Path

import pytest

import admin.persistence as persistence


# ── Fakes for the libSQL driver boundary ────────────────────────────────────


class _FakeResult:
    """Stands in for a SQLAlchemy CursorResult."""

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._keys = [d[0] for d in cursor.description] if cursor.description else []
        self._rows = cursor.fetchall() if cursor.description else []
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid

    def keys(self) -> list[str]:
        return list(self._keys)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class _FakeConnection:
    """Stands in for AsyncConnection on the sqlalchemy-libsql dialect.

    Runs every statement against a real in-memory SQLite database, so a
    statement that only works on one backend fails here.
    """

    def __init__(self) -> None:
        self._sqlite = sqlite3.connect(":memory:")
        self.statements: list[tuple[str, object]] = []
        self.committed = 0
        self.closed = False
        self._in_transaction = False

    async def exec_driver_sql(self, sql: str, parameters=None):
        self.statements.append((sql, parameters))
        cursor = self._sqlite.execute(sql, parameters if parameters is not None else ())
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self._in_transaction = True
        return _FakeResult(cursor)

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def commit(self) -> None:
        self._sqlite.commit()
        self.committed += 1
        self._in_transaction = False

    async def close(self) -> None:
        self.closed = True


class _FakeEngine:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn
        self.disposed = False
        self.url = ""

    async def connect(self) -> _FakeConnection:
        return self.conn

    async def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def turso(monkeypatch):
    """Point the persistence layer at Turso, with the driver mocked out."""
    monkeypatch.setattr(persistence.settings, "TURSO_DATABASE_URL", "libsql://test-db.turso.io")
    monkeypatch.setattr(persistence.settings, "TURSO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "auto")
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_URL", "")

    # The real dialect package is absent, and _open_turso checks for it.
    monkeypatch.setitem(sys.modules, "sqlalchemy_libsql", types.ModuleType("sqlalchemy_libsql"))

    conn = _FakeConnection()
    engine = _FakeEngine(conn)
    engine.url = "stub"

    created: list[str] = []

    def _create_async_engine(url, **kwargs):
        created.append(url)
        engine.url = url
        return engine

    import sqlalchemy.ext.asyncio as sa_asyncio

    monkeypatch.setattr(sa_asyncio, "create_async_engine", _create_async_engine)
    return types.SimpleNamespace(
        connection=conn, engine=engine, urls=created,
        statement_count=lambda: len(conn.statements),
    )


@pytest.fixture
def scratch():
    """A writable scratch directory next to this test module.

    Deliberately not tmp_path: these tests must run wherever the process is
    allowed to write, and the default system temp directory is not always
    that. Removed again on teardown.
    """
    import shutil
    import uuid

    path = Path(__file__).resolve().parent / "_pytest_scratch" / uuid.uuid4().hex[:12]
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def local(monkeypatch, scratch):
    """Point the persistence layer at a local SQLite file, Turso unset."""
    monkeypatch.setattr(persistence.settings, "TURSO_DATABASE_URL", "")
    monkeypatch.setattr(persistence.settings, "TURSO_AUTH_TOKEN", "")
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "auto")
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_URL", "")
    monkeypatch.delenv("AGENCY_ALLOW_MEMORY_DB", raising=False)
    db_path = scratch / "local.db"
    monkeypatch.setattr(persistence, "DB_PATH", db_path)
    return db_path


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Close and drop the shared connection so tests cannot leak into each other."""
    yield
    db = persistence._db
    persistence._db = None
    persistence._lock = None
    persistence._backend_kind = "uninitialised"
    if db is not None:
        try:
            # aiosqlite runs a non-daemon worker thread per connection, so a
            # connection left open here hangs the interpreter at exit.
            asyncio.run(db.close())
        except Exception as exc:  # noqa: BLE001
            print(f"warning: teardown could not close the workspace db: {exc}")


# ── Backend selection ───────────────────────────────────────────────────────


def test_resolves_sqlite_when_url_unset(local):
    assert persistence.resolve_backend() == persistence.BACKEND_SQLITE
    assert persistence.turso_active() is False


def test_resolves_turso_when_url_set(turso):
    assert persistence.resolve_backend() == persistence.BACKEND_TURSO
    assert persistence.turso_active() is True


def test_remote_url_without_token_raises(local, monkeypatch):
    """A half-configured Turso must not quietly use the local file."""
    monkeypatch.setattr(persistence.settings, "TURSO_DATABASE_URL", "libsql://db.turso.io")
    monkeypatch.setattr(persistence.settings, "TURSO_AUTH_TOKEN", "")
    with pytest.raises(RuntimeError, match="TURSO_AUTH_TOKEN is empty"):
        persistence.resolve_backend()
    # Advisory helper must agree rather than claiming Turso is live.
    assert persistence.turso_active() is False


def test_turso_mode_without_url_raises(local, monkeypatch):
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "turso")
    with pytest.raises(RuntimeError, match="TURSO_DATABASE_URL is not set"):
        persistence.resolve_backend()


def test_unknown_mode_raises(local, monkeypatch):
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "postgres")
    with pytest.raises(RuntimeError, match="Unknown AGENCY_WORKSPACE_DB_BACKEND"):
        persistence.resolve_backend()


def test_sqlite_mode_warns_but_works_with_turso_set(turso, monkeypatch, caplog):
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "sqlite")
    with caplog.at_level("WARNING"):
        assert persistence.resolve_backend() == persistence.BACKEND_SQLITE
    assert "pinning the LOCAL file" in caplog.text


def test_build_libsql_url_puts_host_in_the_authority(monkeypatch):
    """The host must land in the authority, or SQLAlchemy cannot parse it.

    "sqlite+libsql://libsql://host?authToken=..." (TURSO_DATABASE_URL carrying
    its own scheme) makes make_url raise
    "invalid literal for int() with base 10: ''". admin/database.py builds its
    engine from that at module scope, which is very likely why TURSO_* was
    never actually set on Render.
    """
    from sqlalchemy.engine import make_url

    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_URL", "")
    url = persistence.build_libsql_url("libsql://my-db.turso.io", "tok")
    parsed = make_url(url)  # raises on the old shape
    assert parsed.drivername == "sqlite+libsql"
    assert parsed.host == "my-db.turso.io"
    assert parsed.query["authToken"] == "tok"


def test_build_libsql_url_matches_the_settings_convention(monkeypatch):
    """admin/database.py builds from settings.DATABASE_URL, so agree exactly."""
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_URL", "")
    built = persistence.build_libsql_url("libsql://my-db.turso.io", "tok")
    assert built == "sqlite+libsql://my-db.turso.io?authToken=tok"


def test_settings_database_url_is_parseable(monkeypatch):
    """settings.DATABASE_URL is what admin/database.py hands to create_async_engine.

    Reloaded with Turso set so the real code path is exercised, not a copy of
    it. The old form raised inside make_url, taking the backend down at import.
    """
    import importlib

    from sqlalchemy.engine import make_url

    from admin.config import settings as settings_module

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://my-db.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
    monkeypatch.delenv("RENDER_POSTGRES_URL", raising=False)
    try:
        reloaded = importlib.reload(settings_module)
        parsed = make_url(reloaded.DATABASE_URL)  # raises on the old shape
        assert parsed.drivername == "sqlite+libsql"
        assert parsed.host == "my-db.turso.io"
        assert parsed.query["authToken"] == "tok"
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)
    assert settings_module.DATABASE_URL.startswith("sqlite+")


def test_build_libsql_url_handles_a_local_replica(monkeypatch):
    """A file: target must parse as the database component, not the host."""
    from sqlalchemy.engine import make_url

    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_URL", "")
    parsed = make_url(persistence.build_libsql_url("file:/tmp/replica.db", ""))
    assert parsed.host is None
    # SQLAlchemy consumes one leading slash of a /// path form.
    assert parsed.database.endswith("tmp/replica.db")


def test_build_libsql_url_honours_explicit_override(monkeypatch):
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_URL", "sqlite+libsql://pinned")
    assert persistence.build_libsql_url("libsql://x.turso.io", "tok") == "sqlite+libsql://pinned"


def test_redact_url_strips_credentials():
    assert persistence.redact_url("libsql://user:pw@db.turso.io") == "libsql://***@db.turso.io"
    assert persistence.redact_url("") == ""


# ── Local mode (URL unset) ──────────────────────────────────────────────────


def test_local_mode_round_trip(local):
    async def run():
        await persistence.init_persistence()
        db = await persistence.get_workspace_db()
        assert db.kind == persistence.BACKEND_SQLITE
        assert db.is_turso is False

        await db.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
            ("ws1", "Acme", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

        async with db.execute("SELECT id, name FROM workspaces WHERE id=?", ("ws1",)) as cur:
            row = await cur.fetchone()
        assert persistence.row_to_dict(row) == {"id": "ws1", "name": "Acme"}
        assert row[0] == "ws1"
        await persistence.close_persistence()

    asyncio.run(run())


def test_local_mode_reports_itself_as_non_durable(local):
    async def run():
        await persistence.init_persistence()
        info = persistence.backend_info()
        await persistence.close_persistence()
        return info

    info = asyncio.run(run())
    assert info["kind"] == "sqlite"
    assert info["durable"] is False
    assert info["turso_configured"] is False
    assert "ephemeral disk" in info["ephemeral_warning"]


def test_unopenable_path_raises_instead_of_silently_using_ram(local, scratch, monkeypatch):
    """The AGENCY_ALLOW_MEMORY_DB guard must actually be reachable.

    sqlite3 reports an unopenable file as OperationalError, which is neither
    OSError nor RuntimeError. Catching only those let the real failure escape
    the guard the comment claims is there.
    """
    monkeypatch.setattr(persistence, "DB_PATH", scratch)  # a directory
    with pytest.raises(RuntimeError, match="Cannot open workspace database"):
        asyncio.run(persistence.get_workspace_db())


def test_memory_db_still_requires_explicit_opt_in(local, scratch, monkeypatch):
    monkeypatch.setattr(persistence, "DB_PATH", scratch)
    monkeypatch.setenv("AGENCY_ALLOW_MEMORY_DB", "1")

    async def run():
        db = await persistence.get_workspace_db()
        await persistence.init_persistence()
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workspaces'"
        ) as cur:
            rows = await cur.fetchall()
        await persistence.close_persistence()
        return [r[0] for r in rows]

    assert asyncio.run(run()) == ["workspaces"]


# ── Turso mode (URL set) ────────────────────────────────────────────────────


def test_turso_mode_uses_the_libsql_dialect(turso):
    async def run():
        db = await persistence.get_workspace_db()
        assert db.kind == persistence.BACKEND_TURSO
        assert db.is_turso is True
        await persistence.close_persistence()

    asyncio.run(run())
    assert turso.urls, "create_async_engine was never called"
    assert turso.urls[0].startswith("sqlite+libsql://")
    assert "authToken=test-token" in turso.urls[0]


def test_turso_mode_round_trip_and_row_shapes(turso):
    """The libSQL path must return rows the rest of the codebase can read."""

    async def run():
        await persistence.init_persistence()
        db = await persistence.get_workspace_db()

        await db.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
            ("ws1", "Acme", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

        async with db.execute("SELECT id, name FROM workspaces WHERE id=?", ("ws1",)) as cur:
            row = await cur.fetchone()
        assert persistence.row_to_dict(row) == {"id": "ws1", "name": "Acme"}
        assert persistence.rows_to_list([row]) == [{"id": "ws1", "name": "Acme"}]
        assert row[0] == "ws1"
        assert row["name"] == "Acme"
        assert len(row) == 2
        assert "id" in row

        async with db.execute("SELECT id FROM workspaces") as cur:
            assert len(await cur.fetchall()) == 1
        await persistence.close_persistence()

    asyncio.run(run())


def test_turso_mode_normalises_bool_parameters(turso):
    """A bool binds fine on aiosqlite and is rejected by the Rust binding."""

    async def run():
        await persistence.init_persistence()
        db = await persistence.get_workspace_db()
        await db.execute(
            "INSERT INTO agent_outputs "
            "(id, workspace_id, agent_type, timestamp, reviewed) VALUES (?, ?, ?, ?, ?)",
            ("o1", "ws1", "seo", "2026-01-01T00:00:00Z", True),
        )
        await db.commit()
        async with db.execute(
            "SELECT reviewed FROM agent_outputs WHERE id=?", ("o1",)
        ) as cur:
            row = await cur.fetchone()
        await persistence.close_persistence()
        return row["reviewed"]

    assert asyncio.run(run()) == 1


def test_turso_mode_runs_the_full_ddl_script(turso):
    """executescript does not exist on the libSQL path; the splitter does."""

    async def run():
        await persistence.init_persistence()
        db = await persistence.get_workspace_db()
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
            ("workspaces", "ceo_autonomy_state", "email_outbox"),
        ) as cur:
            found = sorted(r[0] for r in await cur.fetchall())
        await persistence.close_persistence()
        return found

    assert asyncio.run(run()) == ["ceo_autonomy_state", "email_outbox", "workspaces"]


def test_turso_mode_applies_no_pragmas(turso):
    """journal_mode=WAL and foreign_keys are local-file concepts only."""
    asyncio.run(persistence.init_persistence())
    assert not [s for s, _ in turso.connection.statements if "PRAGMA" in s.upper()]


def test_turso_mode_reports_itself_as_durable(turso):
    async def run():
        await persistence.init_persistence()
        info = persistence.backend_info()
        await persistence.close_persistence()
        return info

    info = asyncio.run(run())
    assert info["kind"] == "turso"
    assert info["durable"] is True
    assert info["turso_configured"] is True
    assert info["turso_url"] == "libsql://test-db.turso.io"
    assert "ephemeral_warning" not in info


def test_unreachable_turso_raises_and_does_not_fall_back(local, monkeypatch):
    """Losing the store visibly beats silently diverging onto a doomed file."""
    monkeypatch.setattr(persistence.settings, "TURSO_DATABASE_URL", "libsql://down.turso.io")
    monkeypatch.setattr(persistence.settings, "TURSO_AUTH_TOKEN", "tok")
    monkeypatch.setitem(sys.modules, "sqlalchemy_libsql", types.ModuleType("sqlalchemy_libsql"))
    monkeypatch.setattr(
        persistence,
        "_open_turso",
        persistence._open_turso,  # keep the real one, it is what raises
    )

    import sqlalchemy.ext.asyncio as sa_asyncio

    def _boom(url, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(sa_asyncio, "create_async_engine", _boom)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(persistence.get_workspace_db())

    assert "Refusing to fall back" in str(excinfo.value)
    assert "down.turso.io" in str(excinfo.value)
    assert persistence._db is None
    assert not local.exists(), "a local DB file was created despite Turso being live"


def test_missing_libsql_driver_names_the_package(local, monkeypatch):
    monkeypatch.setattr(persistence.settings, "TURSO_DATABASE_URL", "libsql://db.turso.io")
    monkeypatch.setattr(persistence.settings, "TURSO_AUTH_TOKEN", "tok")
    monkeypatch.setitem(sys.modules, "sqlalchemy_libsql", None)

    with pytest.raises(RuntimeError, match="sqlalchemy-libsql"):
        asyncio.run(persistence.get_workspace_db())


def test_turso_mode_close_disposes_the_engine(turso):
    async def run():
        await persistence.get_workspace_db()
        await persistence.close_persistence()

    asyncio.run(run())
    assert turso.engine.disposed is True
    assert turso.connection.closed is True
    assert persistence._db is None


# ── Portability helpers ─────────────────────────────────────────────────────


def test_split_sql_script_keeps_semicolons_inside_literals():
    script = (
        "CREATE TABLE t (a TEXT NOT NULL DEFAULT 'x;y');\n"
        "-- a line comment ; with a semicolon\n"
        "CREATE INDEX i ON t(a);"
    )
    statements = persistence.split_sql_script(script)
    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE t")
    assert "'x;y'" in statements[0]


def test_split_sql_script_handles_doubled_quotes():
    statements = persistence.split_sql_script("INSERT INTO t VALUES ('it''s; fine');")
    assert len(statements) == 1
    assert statements[0] == "INSERT INTO t VALUES ('it''s; fine')"


def test_split_sql_script_strips_block_comments():
    statements = persistence.split_sql_script(
        "/* CREATE TABLE gone(a); */\nCREATE TABLE t (a TEXT);"
    )
    assert len(statements) == 1
    assert statements[0].startswith("CREATE TABLE t")


def test_normalise_params_converts_bools_only():
    assert persistence.normalise_params((True, False, 1, "s", None)) == [1, 0, 1, "s", None]
    assert persistence.normalise_params(None) == []
    assert persistence.normalise_params(()) == []
    assert persistence.normalise_params({"a": True}) == {"a": 1}


def test_execute_sync_refuses_to_run_inside_a_loop(local):
    async def run():
        with pytest.raises(RuntimeError, match="running event loop"):
            persistence.execute_sync([("SELECT 1", ())])

    asyncio.run(run())


def test_execute_sync_uses_the_local_file(local):
    # execute_sync opens its own handle, so this needs no seeded connection.
    rows = persistence.execute_sync([
        ("CREATE TABLE IF NOT EXISTS _t (id TEXT)", ()),
        ("INSERT OR REPLACE INTO _t (id) VALUES (?)", ("a",)),
        ("SELECT id FROM _t ORDER BY id", ()),
    ])
    assert [dict(r) for r in rows[2]] == [{"id": "a"}]
    assert local.exists()