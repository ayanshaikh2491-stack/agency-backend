"""Async SQLAlchemy engine, session, and Base for PostgreSQL persistence.

Currently configured but gracefully falls back to in-memory if DB is unavailable.
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from admin.config import settings

logger = logging.getLogger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────
# DATABASE_URL may be a PG url or a fallback SQLite for local dev
DATABASE_URL = settings.DATABASE_URL

# For local dev without PG, use SQLite
_using_fallback = False
if "postgresql" in DATABASE_URL:
    try:
        engine = create_async_engine(DATABASE_URL, echo=False, pool_size=5, max_overflow=10)
        # Quick connectivity check
        import asyncio
        # We'll lazy-check on first use
    except Exception:
        logger.warning("PostgreSQL not available, falling back to SQLite")
        _using_fallback = True

if _using_fallback or "sqlite" in DATABASE_URL or "postgresql" not in DATABASE_URL:
    _fallback_url = "sqlite+aiosqlite:///./tags_agency.db"
    try:
        import aiosqlite  # noqa: F401
        # NullPool: every session opens its own connection. aiosqlite workers are
        # loop-bound, so pooled connections break when sync code calls
        # asyncio.run() multiple times (each run creates a fresh loop).
        from sqlalchemy.pool import NullPool
        engine = create_async_engine(_fallback_url, echo=False, poolclass=NullPool)
        logger.info("Using SQLite fallback: %s", _fallback_url)
    except ImportError:
        logger.warning("aiosqlite not installed — DB persistence disabled")
        engine = None  # type: ignore[assignment]

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
    """Create all tables. Safe to call on every startup."""
    if engine is None:
        logger.warning("No database engine — skipping init_db()")
        return
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created / verified")
    except Exception as exc:
        logger.error("init_db() failed: %s", exc)


async def close_db() -> None:
    """Dispose the engine."""
    if engine:
        await engine.dispose()
