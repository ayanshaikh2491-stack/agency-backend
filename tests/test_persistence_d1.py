"""Tests for the Cloudflare D1 workspace store, and for the local default.

Three backends now share one aiosqlite-shaped surface. This module covers the
new D1 path and re-asserts that the local path is still the zero-config
default, because the failure this file exists to prevent is D1 being configured
and quietly not being used (or being used and quietly not working).

The D1 path never touches the network. The HTTP transport is replaced at
persistence._make_d1_client(), the one place that touches httpx, and the fake
speaks the real Cloudflare envelope:

    {"success": true, "results": [{"results": [...], "meta": {...}}], ...}

and runs each statement against a real in-memory sqlite3 database, one
statement per request, exactly like the real API. That is deliberate: it proves
the statements in this codebase run on D1 (same SQLite dialect, no
executescript, no PRAGMA, positional "?" placeholders, bools normalised) and it
proves the one-statement-per-request splitting, with no outbound call.

Covered, in the order the bugs arrive:
  * backend selection: d1 mode, auto preference, and every missing variable;
  * configured-but-unreachable raising instead of falling back to a local file;
  * executescript splitting the DDL script into one request per statement;
  * the row surfaces the ~25 consumer modules rely on: row[0], row["col"],
    dict(row), fetchone/fetchall/fetchmany, rowcount, lastrowid;
  * bool normalisation through normalise_params onto the JSON body;
  * commit() being an honest no-op rather than a fake transaction.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin.persistence as persistence  # noqa: E402

ACCOUNT_ID = "acct0000000000000000000000000000"
DATABASE_ID = "db00000000000000000000000000000000"
D1_TOKEN = "cf-test-token"

EXPECTED_ENDPOINT = (
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
    f"/d1/database/{DATABASE_ID}/query"
)

_D1_VARS = (
    "AGENCY_WORKSPACE_DB_D1_TOKEN",
    "AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID",
    "AGENCY_WORKSPACE_DB_D1_DATABASE_ID",
    "AGENCY_WORKSPACE_DB_D1_URL",
    "AGENCY_WORKSPACE_DB_D1_TIMEOUT_SEC",
    "AGENCY_WORKSPACE_DB_D1_MAX_RETRIES",
)


# ── Fakes for the Cloudflare HTTP boundary ───────────────────────────────────


class _FakeResponse:
    """Stands in for an httpx.Response."""

    def __init__(self, status_code: int, body: object = None, text: str | None = None) -> None:
        self.status_code = status_code
        self.text = json.dumps(body) if text is None else text


class _FakeD1Client:
    """A Cloudflare D1 /query endpoint backed by real in-memory SQLite.

    One POST is one statement, which is the constraint under test. Every call is
    recorded so a test can assert on the request shape and count, and a
    statement that only works on one backend fails here rather than in
    production.
    """

    def __init__(self) -> None:
        self._sqlite = sqlite3.connect(":memory:")
        self.requests: list[dict] = []
        self.endpoints: list[str] = []
        self.headers: list[dict] = []
        self.closed = False
        self.fail_with: Exception | None = None
        self.status_override: int | None = None
        self.body_override: object | None = None
        self.text_override: str | None = None

    def _envelope(self, sql: str, params: list) -> _FakeResponse:
        cursor = self._sqlite.execute(sql, params)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall() if columns else []
        self._sqlite.commit()
        block = {
            "results": [dict(zip(columns, row)) for row in rows] if columns else [],
            "meta": {
                "changes": 0 if columns else cursor.rowcount,
                "rows_read": len(rows),
                "rows_written": 0 if columns else cursor.rowcount,
                "last_row_id": None if columns else cursor.lastrowid,
                "duration": 0.01,
            },
        }
        return _FakeResponse(
            200, {"success": True, "results": [block], "errors": [], "messages": []}
        )

    async def post(self, url, content=None, headers=None):
        self.endpoints.append(url)
        self.headers.append(dict(headers or {}))
        if isinstance(content, (str, bytes)):
            body = json.loads(content)
        else:
            body = dict(content or {})
        self.requests.append(body)
        if self.fail_with is not None:
            raise self.fail_with
        if self.status_override is not None:
            return _FakeResponse(
                self.status_override,
                {
                    "success": False,
                    "results": [],
                    "errors": [{"code": 1000, "message": "simulated cloudflare failure"}],
                },
            )
        if self.text_override is not None:
            return _FakeResponse(200, text=self.text_override)
        if self.body_override is not None:
            return _FakeResponse(200, self.body_override)
        return self._envelope(body["sql"], list(body.get("params") or []))

    async def aclose(self) -> None:
        self.closed = True

    # ── helpers for seeding and inspecting the fake database ──
    def seed(self, sql: str, params=()) -> None:
        """Prepare a table without going through the HTTP layer."""
        self._sqlite.execute(sql, params)
        self._sqlite.commit()

    def rows(self, sql: str) -> list[tuple]:
        return self._sqlite.execute(sql).fetchall()

    @property
    def sql(self) -> list[str]:
        return [r["sql"] for r in self.requests]

    def count_of(self, fragment: str) -> int:
        return sum(1 for s in self.sql if fragment in s)


@pytest.fixture
def d1_client():
    return _FakeD1Client()


@pytest.fixture
def clean_env(monkeypatch):
    """No D1, no Turso, backend=auto. The zero-configuration starting point."""
    for name in _D1_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(persistence.settings, "TURSO_DATABASE_URL", "")
    monkeypatch.setattr(persistence.settings, "TURSO_AUTH_TOKEN", "")
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "auto")
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_URL", "")
    monkeypatch.delenv("AGENCY_ALLOW_MEMORY_DB", raising=False)
    return monkeypatch


@pytest.fixture
def scratch():
    """A writable scratch directory next to this test module.

    Deliberately not tmp_path: the default system temp directory is not always
    writable for this process, and a PermissionError while creating a fixture
    directory is reported as an ERROR against every test that uses it. Mirrors
    the fixture admin/test_persistence_backend.py already uses successfully.
    """
    path = Path(__file__).resolve().parent / "_pytest_scratch" / uuid.uuid4().hex[:12]
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def d1(clean_env, d1_client, monkeypatch):
    """A complete D1 configuration whose HTTP transport is the fake client."""
    _set_d1(monkeypatch)
    monkeypatch.setattr(persistence, "_make_d1_client", lambda timeout: d1_client)
    return d1_client


@pytest.fixture
def local(clean_env, scratch, monkeypatch):
    """The zero-config default, pointed at a writable local SQLite file."""
    db_path = scratch / "local.db"
    monkeypatch.setattr(persistence, "DB_PATH", db_path)
    return db_path


def _set_d1(monkeypatch, values=None):
    """Set or clear the three D1 id variables from a {var: value} mapping."""
    values = values or {
        "AGENCY_WORKSPACE_DB_D1_TOKEN": D1_TOKEN,
        "AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID": ACCOUNT_ID,
        "AGENCY_WORKSPACE_DB_D1_DATABASE_ID": DATABASE_ID,
    }
    for name, value in values.items():
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Drop the shared connection so tests cannot leak into each other."""
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


# ── Configuration and backend selection ─────────────────────────────────────


def test_d1_config_reads_the_three_variables(clean_env, monkeypatch):
    _set_d1(monkeypatch)
    assert persistence.d1_config() == {
        "token": D1_TOKEN,
        "account": ACCOUNT_ID,
        "database": DATABASE_ID,
    }
    assert persistence.d1_configured() is True
    assert persistence.d1_endpoint(persistence.d1_config()) == EXPECTED_ENDPOINT


def test_auto_selects_d1_when_configured_and_turso_is_not(d1):
    assert persistence.resolve_backend() == persistence.BACKEND_D1
    assert persistence.d1_active() is True
    assert persistence.turso_active() is False
    assert persistence.durable_active() is True


def test_d1_mode_is_honoured_even_with_turso_set(d1, monkeypatch):
    """AGENCY_WORKSPACE_DB_BACKEND=d1 wins over a leftover Turso config."""
    monkeypatch.setattr(persistence.settings, "TURSO_DATABASE_URL", "libsql://x.turso.io")
    monkeypatch.setattr(persistence.settings, "TURSO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "d1")
    assert persistence.resolve_backend() == persistence.BACKEND_D1


def test_auto_prefers_turso_and_says_so_when_both_are_set(d1, monkeypatch, caplog):
    """Turso keeps precedence; the operator is told, not left to guess."""
    monkeypatch.setattr(persistence.settings, "TURSO_DATABASE_URL", "libsql://x.turso.io")
    monkeypatch.setattr(persistence.settings, "TURSO_AUTH_TOKEN", "tok")
    with caplog.at_level("WARNING"):
        assert persistence.resolve_backend() == persistence.BACKEND_TURSO
    assert "Delete" in caplog.text and "D1" in caplog.text


def test_local_is_still_the_default_when_nothing_is_configured(local):
    assert persistence.resolve_backend() == persistence.BACKEND_SQLITE
    assert persistence.d1_active() is False
    assert persistence.durable_active() is False


def test_d1_url_can_supply_the_account_and_database_id(clean_env, monkeypatch):
    """One variable instead of two, in the shape the dashboard shows."""
    monkeypatch.setenv(
        "AGENCY_WORKSPACE_DB_D1_URL",
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
        f"/d1/database/{DATABASE_ID}/query",
    )
    cfg = persistence.d1_config()
    assert cfg["account"] == ACCOUNT_ID
    assert cfg["database"] == DATABASE_ID
    assert persistence.d1_endpoint(cfg) == EXPECTED_ENDPOINT


def test_explicit_ids_win_over_a_stale_d1_url(clean_env, monkeypatch):
    """A leftover URL must not silently redirect the store."""
    _set_d1(monkeypatch)
    monkeypatch.setenv(
        "AGENCY_WORKSPACE_DB_D1_URL",
        "https://api.cloudflare.com/client/v4/accounts/other/d1/database/other/query",
    )
    cfg = persistence.d1_config()
    assert cfg["account"] == ACCOUNT_ID
    assert cfg["database"] == DATABASE_ID


@pytest.mark.parametrize(
    "url",
    [
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
        f"/d1/database/{DATABASE_ID}/query",
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
        f"/d1/database/{DATABASE_ID}",
        f"{ACCOUNT_ID}/{DATABASE_ID}",
        f"{ACCOUNT_ID}:{DATABASE_ID}",
    ],
)
def test_parse_d1_url_accepts_the_shapes_people_copy(url):
    assert persistence.parse_d1_url(url) == {"account": ACCOUNT_ID, "db": DATABASE_ID}


@pytest.mark.parametrize(
    "url", ["", "nonsense", "https://api.cloudflare.com/", f"{ACCOUNT_ID}/"]
)
def test_parse_d1_url_rejects_junk(url):
    assert persistence.parse_d1_url(url) == {}


# ── Missing configuration is loud, never a silent local file ────────────────


@pytest.mark.parametrize("missing", list(_D1_VARS[:3]))
def test_missing_d1_variable_raises_and_names_itself(clean_env, monkeypatch, missing):
    values = {
        "AGENCY_WORKSPACE_DB_D1_TOKEN": D1_TOKEN,
        "AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID": ACCOUNT_ID,
        "AGENCY_WORKSPACE_DB_D1_DATABASE_ID": DATABASE_ID,
    }
    values[missing] = ""
    _set_d1(monkeypatch, values)

    with pytest.raises(RuntimeError) as excinfo:
        persistence.resolve_backend()

    message = str(excinfo.value)
    assert missing in message, message
    assert "Refusing to fall back" in message, message
    # The advisory helpers must not claim a live D1 over a broken config.
    assert persistence.d1_active() is False
    assert persistence.durable_active() is False


def test_d1_mode_without_any_config_raises(local, monkeypatch):
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "d1")
    with pytest.raises(RuntimeError, match="AGENCY_WORKSPACE_DB_D1_TOKEN"):
        persistence.resolve_backend()


def test_half_configured_d1_still_raises_under_auto(clean_env, monkeypatch):
    """A token alone must not look like an unconfigured dev box."""
    _set_d1(monkeypatch, {"AGENCY_WORKSPACE_DB_D1_TOKEN": D1_TOKEN})
    with pytest.raises(RuntimeError, match="AGENCY_WORKSPACE_DB_D1_ACCOUNT_ID"):
        persistence.resolve_backend()


def test_unknown_mode_mentions_every_valid_choice(local, monkeypatch):
    monkeypatch.setattr(persistence.settings, "WORKSPACE_DB_BACKEND", "postgres")
    with pytest.raises(RuntimeError) as excinfo:
        persistence.resolve_backend()
    message = str(excinfo.value)
    for kind in ("auto", "d1", "turso", "sqlite"):
        assert kind in message, message


# ── Failure behaviour matches Turso: raise, never fall back ─────────────────


def test_unreachable_d1_raises_and_does_not_fall_back(d1, local):
    """The exact bug class this layer exists to prevent.

    A configured store that cannot be reached must stop the boot. Opening the
    local file instead would look healthy and then hand the CEO an empty
    database on the next deploy, because Render FREE deletes the disk.
    """
    d1.fail_with = ConnectionRefusedError("connection refused")

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(persistence.get_workspace_db())

    message = str(excinfo.value)
    assert "Cannot reach Cloudflare D1" in message, message
    assert "Refusing to fall back" in message, message
    assert "connection refused" in message, message
    assert persistence._db is None
    assert not local.exists(), "a local DB file was created despite D1 being live"


def test_a_401_from_cloudflare_surfaces_the_real_reason(d1):
    """A wrong token is an auth failure, not an outage, and must say so."""
    d1.status_override = 401

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(persistence.get_workspace_db())

    message = str(excinfo.value)
    assert "HTTP 401" in message, message
    assert "simulated cloudflare failure" in message, message
    assert "D1 Edit" in message, message


def test_a_timeout_is_never_read_as_an_empty_result(d1):
    """A hung edge must raise. Returning zero rows would read as 'no rows'."""
    d1.fail_with = TimeoutError("read timeout after 15.0s")

    async def run():
        db = await persistence.get_workspace_db()
        return await db.execute("SELECT 1", ())

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(run())
    assert "The statement did NOT run" in str(excinfo.value)


def test_rate_limit_surfaces_429_with_clouds_flares_own_text(d1):
    """HTTP 429 is raised by default, not retried into a silent throttle."""
    d1.status_override = 429

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(persistence.get_workspace_db())
    assert "HTTP 429" in str(excinfo.value)


def test_rate_limit_retry_is_opt_in_and_then_surfaces(d1, monkeypatch):
    monkeypatch.setenv("AGENCY_WORKSPACE_DB_D1_MAX_RETRIES", "2")
    monkeypatch.setattr(persistence, "D1_RETRY_BASE_SECONDS", 0.0)
    d1.status_override = 429

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(persistence.get_workspace_db())
    assert "HTTP 429" in str(excinfo.value)
    # 1 boot probe + 2 retries, so every attempt really happened.
    assert len(d1.requests) == 3


def test_sql_error_inside_a_200_response_still_raises(d1):
    """D1 reports 'no such table' as success:false with HTTP 200.

    Treating that as a result set with zero rows is the silent-failure bug.
    """
    d1.body_override = {
        "success": False,
        "results": [],
        "errors": [{"code": 0, "message": "no such table: workspaces"}],
        "messages": [],
    }

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(persistence.get_workspace_db())
    assert "no such table: workspaces" in str(excinfo.value)


def test_a_non_json_200_is_not_a_successful_empty_query(d1):
    """A captive portal or proxy error page must not become 'no rows'."""
    d1.text_override = "<html><body>502 Bad Gateway</body></html>"

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(persistence.get_workspace_db())
    assert "502 Bad Gateway" in str(excinfo.value)


def test_a_200_with_no_result_block_is_not_an_empty_result(d1):
    """A success envelope with nothing in results must not read as 'no rows'."""
    d1.body_override = {"success": True, "results": [], "errors": [], "messages": []}

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(persistence.get_workspace_db())
    assert "no result block" in str(excinfo.value)


# ── The D1 path works, and works with the same call surface ─────────────────


def test_d1_mode_round_trip_and_row_shapes(d1):
    """Every row surface the ~25 consumer modules rely on must be identical."""

    async def run():
        await persistence.init_persistence()
        db = await persistence.get_workspace_db()
        assert db.kind == persistence.BACKEND_D1
        assert db.is_d1 is True
        assert db.is_turso is False
        assert db.is_remote is True

        await db.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
            ("ws1", "Acme", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

        async with db.execute(
            "SELECT id, name FROM workspaces WHERE id=?", ("ws1",)
        ) as cur:
            row = await cur.fetchone()

        assert row[0] == "ws1"
        assert row["id"] == "ws1"
        assert row["name"] == "Acme"
        assert dict(row) == {"id": "ws1", "name": "Acme"}
        assert persistence.row_to_dict(row) == {"id": "ws1", "name": "Acme"}
        assert persistence.rows_to_list([row]) == [{"id": "ws1", "name": "Acme"}]
        assert len(row) == 2
        assert "id" in row
        assert list(row) == ["ws1", "Acme"]

        async with db.execute("SELECT id FROM workspaces") as cur:
            assert len(await cur.fetchall()) == 1
        await persistence.close_persistence()
        return row

    asyncio.run(run())


def test_d1_awaited_execute_and_context_manager_agree(d1):
    """`cursor = await db.execute(...)` and `async with db.execute(...)` both work."""

    async def run():
        await persistence.init_persistence()
        db = await persistence.get_workspace_db()
        cursor = await db.execute("SELECT 1 AS one")
        awaited = await cursor.fetchone()
        await cursor.close()

        async with db.execute("SELECT 1 AS one") as cur:
            ctx = await cur.fetchone()
        await persistence.close_persistence()
        return awaited, ctx

    awaited, ctx = asyncio.run(run())
    assert awaited[0] == 1 and ctx["one"] == 1


def test_d1_fetchmany_rowcount_and_lastrowid(d1):
    async def run():
        await persistence.init_persistence()
        db = await persistence.get_workspace_db()
        await db.execute("CREATE TABLE IF NOT EXISTS _probe (t TEXT, at TEXT)", ())
        await db.executemany(
            "INSERT INTO _probe (t, at) VALUES (?, ?)",
            [("a", "2026-10-05"), ("b", "2026-10-05")],
        )
        cursor = await db.execute("SELECT t, at FROM _probe ORDER BY t")
        first = await cursor.fetchmany(1)
        rest = await cursor.fetchall()
        inserted = await db.execute(
            "INSERT INTO _probe (t, at) VALUES (?, ?)", ("c", "2026-10-05")
        )
        await persistence.close_persistence()
        return first, rest, inserted.rowcount, inserted.lastrowid

    first, rest, rowcount, lastrowid = asyncio.run(run())
    assert [r[0] for r in first] == ["a"]
    assert [r["t"] for r in rest] == ["a", "b"]
    assert rowcount == 1, "rowcount must report the write, not zero"
    assert lastrowid is not None, "lastrowid must be D1's last_row_id, not a fake 0"


def test_d1_rejects_use_after_close(d1):
    """A closed store is a bug in the caller and must be visible."""

    async def run():
        db = await persistence.get_workspace_db()
        await db.close()
        return await db.execute("SELECT 1", ())

    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(run())


def test_d1_close_releases_the_http_client(d1):
    async def run():
        db = await persistence.get_workspace_db()
        await db.close()

    asyncio.run(run())
    assert d1.closed is True


# ── One statement per request ───────────────────────────────────────────────


def test_executescript_splits_the_ddl_into_one_request_per_statement(d1):
    """D1's /query takes one statement, so the script has to be split."""
    asyncio.run(persistence.init_persistence())
    expected = len(persistence.split_sql_script(persistence.CREATE_TABLES_SQL))
    assert len(d1.requests) == expected + 1  # +1 for the boot SELECT 1 probe
    for statement in d1.sql:
        assert statement.count("CREATE TABLE") <= 1, statement


def test_executescript_issues_statements_in_order(d1):
    """DDL is order-dependent: the index needs its table to exist first."""
    asyncio.run(persistence.init_persistence())
    table = next(
        i
        for i, s in enumerate(d1.sql)
        if s.startswith("CREATE TABLE IF NOT EXISTS agent_tasks")
    )
    index = next(i for i, s in enumerate(d1.sql) if "idx_agent_tasks_status" in s)
    assert table < index
    # The split really happened: no single request carried two CREATE TABLEs.
    assert not any(s.count("CREATE TABLE") > 1 for s in d1.sql)


def test_executescript_keeps_semicolons_inside_literals(d1):
    d1.seed("CREATE TABLE _lit (v TEXT)")
    script = (
        "INSERT INTO _lit (v) VALUES ('x;y');\n"
        "-- a comment ; with a semicolon\n"
        "INSERT INTO _lit (v) VALUES ('second');"
    )

    async def run():
        db = await persistence.get_workspace_db()
        await db.executescript(script)
        await persistence.close_persistence()

    asyncio.run(run())
    assert len(d1.sql) == 3  # boot probe + 2 statements, not 4+
    # 'x;y' was not cut in half by the splitter.
    assert [r[0] for r in d1.rows("SELECT v FROM _lit ORDER BY rowid")] == [
        "x;y",
        "second",
    ]


def test_every_request_carries_one_statement_and_its_params(d1):
    asyncio.run(persistence.init_persistence())
    assert all(isinstance(r["sql"], str) and r["sql"].strip() for r in d1.requests)
    assert all(isinstance(r.get("params"), list) for r in d1.requests)
    assert all(r["params"] == [] for r in d1.requests), "DDL carries no parameters"
    # The DDL is a burst, so the free-tier rate limit is a real boot risk, and
    # the retry is opt-in for exactly that reason.
    assert len(d1.requests) > 10, len(d1.requests)


def test_executemany_issues_one_request_per_row_and_sums_the_changes(d1):
    """Batched over the pooled client, ordered, with a truthful rowcount."""
    d1.seed("CREATE TABLE _many (t TEXT)")

    async def run():
        db = await persistence.get_workspace_db()
        cursor = await db.executemany(
            "INSERT INTO _many (t) VALUES (?)", [("a",), ("b",), ("c",)]
        )
        await db.commit()
        stored = await db.execute("SELECT COUNT(*) AS n FROM _many")
        count = await stored.fetchone()
        await persistence.close_persistence()
        return cursor, count

    cursor, count = asyncio.run(run())
    assert d1.count_of("INSERT INTO _many (t) VALUES (?)") == 3
    assert cursor.rowcount == 3, "rowcount must sum every row, not report the last"
    assert count["n"] == 3


def test_executemany_with_no_rows_still_returns_a_cursor(d1):
    """The Turso and local paths both return a cursor here; D1 must too."""

    async def run():
        db = await persistence.get_workspace_db()
        cursor = await db.executemany("SELECT 1", [])
        await persistence.close_persistence()
        return cursor

    cursor = asyncio.run(run())
    assert cursor.rowcount == 0
    assert cursor.rows() == []
    assert asyncio.run(cursor.fetchall()) == []


# ── Parameter marshalling ───────────────────────────────────────────────────


def test_bools_are_normalised_on_the_wire(d1):
    """D1's body is JSON; a raw True would not reliably store as an INTEGER."""
    d1.seed(
        "CREATE TABLE _flags (id TEXT PRIMARY KEY, reviewed INTEGER, responded INTEGER)"
    )

    async def run():
        db = await persistence.get_workspace_db()
        await db.execute(
            "INSERT INTO _flags (id, reviewed, responded) VALUES (?, ?, ?)",
            ("o1", True, False),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT reviewed, responded FROM _flags WHERE id=?", ("o1",)
        )
        row = await cursor.fetchone()
        await persistence.close_persistence()
        return row

    row = asyncio.run(run())
    insert = next(r for r in d1.requests if "INSERT INTO _flags" in r["sql"])
    assert insert["params"] == ["o1", 1, 0]
    assert row["reviewed"] == 1
    assert row["responded"] == 0


def test_normalise_params_is_unchanged_and_shared_by_d1():
    """D1 reuses the existing helper rather than growing a second one."""
    assert persistence.normalise_params((True, False, 1, "s", None)) == [1, 0, 1, "s", None]
    assert persistence.normalise_params(None) == []
    assert persistence.normalise_params(()) == []
    assert persistence.normalise_params({"a": True}) == {"a": 1}


def test_d1_json_encodes_awkward_but_legal_values(d1):
    """bytes and date must not raise from inside the JSON encoder."""
    import datetime as dt

    d1.seed("CREATE TABLE _blob (id TEXT PRIMARY KEY, payload TEXT)")

    async def run():
        db = await persistence.get_workspace_db()
        await db.execute(
            "INSERT INTO _blob (id, payload) VALUES (?, ?)", ("b1", b"bytes-are-fine")
        )
        await db.execute(
            "INSERT INTO _blob (id, payload) VALUES (?, ?)", ("d1", dt.date(2026, 10, 5))
        )
        await db.commit()
        cursor = await db.execute("SELECT id, payload FROM _blob ORDER BY id")
        rows = await cursor.fetchall()
        await persistence.close_persistence()
        return {r["id"]: r["payload"] for r in rows}

    rows = asyncio.run(run())
    assert rows["b1"] == "bytes-are-fine"
    assert rows["d1"] == "2026-10-05"


def test_d1_sends_the_bearer_token_and_json_content_type(d1):
    asyncio.run(persistence.init_persistence())
    header = d1.headers[0]
    assert header["Authorization"] == f"Bearer {D1_TOKEN}"
    assert header["Content-Type"] == "application/json"
    assert d1.endpoints[0] == EXPECTED_ENDPOINT


def test_a_bad_timeout_setting_falls_back_and_says_so(monkeypatch, caplog):
    """An unparseable knob must not become a silent default."""
    monkeypatch.setenv("AGENCY_WORKSPACE_DB_D1_TIMEOUT_SEC", "soon")
    with caplog.at_level("WARNING"):
        assert persistence._d1_float("AGENCY_WORKSPACE_DB_D1_TIMEOUT_SEC", 15.0) == 15.0
    assert "is not a number" in caplog.text


# ── commit() is an honest no-op ─────────────────────────────────────────────


def test_commit_is_a_no_op_that_makes_no_request(d1):
    """It must not pretend to be a transaction, and must not hide that fact."""
    asyncio.run(persistence.init_persistence())
    before = len(d1.requests)

    async def run():
        db = await persistence.get_workspace_db()
        await db.execute("CREATE TABLE IF NOT EXISTS _c (id TEXT)", ())
        await db.commit()
        await db.commit()
        return len(d1.requests)

    assert asyncio.run(run()) == before + 1, "commit() must not issue a statement"

    doc = persistence._D1Backend.commit.__doc__ or ""
    assert "NO-OP" in doc
    assert "does NOT make that sequence atomic" in doc


def test_d1_backend_info_states_there_is_no_transaction(d1):
    """The honest description has to be readable from outside the process."""
    from admin.api.routes.extra import _storage_info

    async def run():
        await persistence.init_persistence()
        info = persistence.backend_info()
        await persistence.close_persistence()
        return info

    info = asyncio.run(run())
    assert info["kind"] == "d1"
    assert info["durable"] is True
    assert info["d1_configured"] is True
    assert info["d1_token_set"] is True
    assert info["d1_account_id"] == ACCOUNT_ID
    assert info["d1_database_id"] == DATABASE_ID
    assert info["target"] == EXPECTED_ENDPOINT
    assert info["transactions"] == "per-statement, no cross-request transaction"
    assert "ephemeral_warning" not in info
    assert D1_TOKEN not in json.dumps(info), "the token must never be reported"

    # The status endpoint helper must report the same shape once closed.
    assert _storage_info()["kind"] == "uninitialised"


# ── Local mode is untouched ─────────────────────────────────────────────────


def test_local_mode_round_trip(local):
    async def run():
        await persistence.init_persistence()
        db = await persistence.get_workspace_db()
        assert db.kind == persistence.BACKEND_SQLITE
        assert db.is_d1 is False
        assert db.is_remote is False

        await db.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
            ("ws1", "Acme", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

        async with db.execute(
            "SELECT id, name FROM workspaces WHERE id=?", ("ws1",)
        ) as cur:
            row = await cur.fetchone()
        await persistence.close_persistence()
        return row

    row = asyncio.run(run())
    assert row[0] == "ws1"
    assert row["name"] == "Acme"
    assert dict(row) == {"id": "ws1", "name": "Acme"}


def test_local_mode_still_reports_itself_as_non_durable(local):
    async def run():
        await persistence.init_persistence()
        info = persistence.backend_info()
        await persistence.close_persistence()
        return info

    info = asyncio.run(run())
    assert info["kind"] == "sqlite"
    assert info["durable"] is False
    assert info["d1_configured"] is False
    assert info["transactions"] == "client-side, real COMMIT"
    assert "ephemeral disk" in info["ephemeral_warning"]


def test_execute_sync_routes_through_d1(d1):
    """The AgentMail outbox is synchronous code; it must reach the same store."""
    rows = persistence.execute_sync([
        ("CREATE TABLE IF NOT EXISTS _outbox (id TEXT)", ()),
        ("INSERT OR REPLACE INTO _outbox (id) VALUES (?)", ("m1",)),
        ("SELECT id FROM _outbox ORDER BY id", ()),
    ])

    assert [dict(r) for r in rows[2]] == [{"id": "m1"}]
    # One round trip for the boot probe, then one per statement.
    assert len(d1.requests) == 4, d1.sql


def test_execute_sync_refuses_to_run_inside_a_loop(local):
    async def run():
        with pytest.raises(RuntimeError, match="running event loop"):
            persistence.execute_sync([("SELECT 1", ())])

    asyncio.run(run())
