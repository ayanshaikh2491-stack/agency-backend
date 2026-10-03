"""Async SQLAlchemy engine, session, and Base.

Supports PostgreSQL (asyncpg), Turso/libSQL (sqlalchemy-libsql), and local
SQLite for development.

This module does NOT degrade to a fallback database when the configured one is
unusable. It raises at import time. See the comment above _build_engine() for
why silent fallback was removed.
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
import sqlalchemy

from admin.config import settings

logger = logging.getLogger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────
# DATABASE_URL may be postgres, Turso/libSQL, or a local SQLite file for dev.
#
# HISTORY OF A DATA-LOSSING BUG (do not reintroduce):
#   The old check was `if "sqlite" in DATABASE_URL ...: use local SQLite`.
#   settings.py builds Turso URLs as "sqlite+libsql://...", which CONTAINS the
#   substring "sqlite" -- so a fully-configured Turso deployment silently fell
#   through to `sqlite+aiosqlite:///./tags_agency.db`. On Render that file lives
#   on ephemeral disk and is wiped on every restart, so the whole backend wrote
#   leads into the void while reporting "OK". Same bug class as the documented
#   sba_pipeline.py failure ("silently ran with all-zero stats for months").
#
#   Rule now: match the DIALECT exactly, and raise on anything unrecognised.
#   A wrong DB must fail loudly at boot, never degrade to a local file.
DATABASE_URL = settings.DATABASE_URL


def _build_engine(url: str):
    """Create an async engine for `url`. Returns (engine, kind).

    Raises on an unknown dialect instead of silently degrading to SQLite.
    """
    if not url:
        raise ValueError("DATABASE_URL is empty")

    # PostgreSQL. Normalise the several schemes people paste in.
    if url.startswith(("postgres://", "postgresql://", "postgresql+")):
        _pg = url
        if _pg.startswith("postgres://"):
            _pg = "postgresql://" + _pg[len("postgres://"):]
        # asyncpg is our async driver; strip any sync driver that got pasted in.
        _pg = _pg.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _pg = _pg.replace("postgresql://", "postgresql+asyncpg://")
        return (
            create_async_engine(_pg, echo=False, pool_size=5, max_overflow=10),
            "postgresql",
        )

    # Turso / libSQL. MUST be checked before the plain-sqlite branch, because
    # the libSQL dialect string literally starts with "sqlite+".
    if url.startswith("sqlite+libsql://"):
        try:
            import sqlalchemy_libsql  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "DATABASE_URL points at libSQL/Turso but the dialect is not "
                "installed. Add `sqlalchemy-libsql>=0.1.0` to requirements.txt."
            ) from exc
        return create_async_engine(url, echo=False), "libsql"

    # Local SQLite (dev default, or an explicit sqlite+aiosqlite:// URL)
    if url.startswith("sqlite"):
        try:
            import aiosqlite  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "DATABASE_URL points at SQLite but aiosqlite is not installed."
            ) from exc
        # NullPool: every session opens its own connection. aiosqlite workers are
        # loop-bound, so pooled connections break when sync code calls
        # asyncio.run() multiple times (each run creates a fresh loop).
        from sqlalchemy.pool import NullPool
        return create_async_engine(url, echo=False, poolclass=NullPool), "sqlite"

    raise ValueError(
        f"Unrecognised DATABASE_URL dialect {url.split(':', 1)[0]!r}. Refusing "
        "to fall back to a local SQLite file -- that silently loses data on "
        "ephemeral disks. Fix the URL, or add the dialect here."
    )


try:
    engine, DB_KIND = _build_engine(DATABASE_URL)
except Exception as exc:  # noqa: BLE001
    # Fail loud. Previously this path fell back to a local file, and the backend
    # reported healthy while writing every row to scratch disk.
    logger.error("FATAL: could not initialise database engine from %r: %s",
                 DATABASE_URL, exc)
    raise

if DB_KIND == "sqlite":
    logger.warning(
        "Using local SQLite (%s) -- data will NOT survive a redeploy. Set "
        "TURSO_DATABASE_URL + TURSO_AUTH_TOKEN, or DATABASE_URL, for production.",
        DATABASE_URL,
    )
else:
    logger.info("Database engine ready: %s", DB_KIND)

# ── Session factory ───────────────────────────────────────────────────────
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None
if engine:
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session."""
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not configured")
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables. Safe to call on every startup.

    Raises if the schema cannot be created. Schema creation is a hard boot
    dependency: if the tables do not exist, every later query fails at request
    time while /api/health still reports ceo_ready=true. Callers that genuinely
    want to continue without a schema must catch this themselves and say so.
    """
    if engine is None:
        raise RuntimeError(
            "init_db() called with no database engine — the backend would boot "
            "and then fail every query at request time."
        )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created / verified")
    except Exception as exc:
        logger.error("init_db() failed: %s", exc)
        raise
    # ── Lightweight column migration ────────────────────────────────────────
    # create_all only creates MISSING TABLES — existing tables never gain new
    # columns. Add columns added after initial deploy here (idempotent).
    try:
        _COLS = {
            "leads": [
                ("city", "VARCHAR(128) DEFAULT ''"),
                ("state", "VARCHAR(64) DEFAULT ''"),
                ("website", "VARCHAR(512) DEFAULT ''"),
            ],
        }
        async with engine.begin() as conn:
            for table, cols in _COLS.items():
                existing = set()
                try:
                    result = await conn.execute(
                        sqlalchemy.text(f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'")  # noqa: E501
                    )
                    existing = {r[0] for r in result}
                except Exception:  # noqa: BLE001
                    # SQLite path
                    try:
                        result = await conn.execute(sqlalchemy.text(f"PRAGMA table_info({table})"))
                        existing = {r[1] for r in result}
                    except Exception:  # noqa: BLE001
                        continue
                for col, decl in cols:
                    if col not in existing:
                        await conn.execute(
                            sqlalchemy.text(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                        )
                        logger.info("migrated: %s.%s added", table, col)
    except Exception as exc:  # noqa: BLE001
        logger.warning("column migration skipped: %s", exc)


async def close_db() -> None:
    """Dispose the engine."""
    if engine:
        await engine.dispose()
