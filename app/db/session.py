"""Async engine + session factory.

init_db() only CREATES missing tables (metadata.create_all). It never drops or
alters existing ones, so it is safe to run on every startup and can never lose
data.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.db.models import Base

# Additive-only column migrations for tables that already existed before a model
# gained new fields. ADD COLUMN IF NOT EXISTS never drops or alters data.
_ADDITIVE_MIGRATIONS = [
    "ALTER TABLE goals ADD COLUMN IF NOT EXISTS target_date DATE",
    "ALTER TABLE goals ADD COLUMN IF NOT EXISTS progress INTEGER",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked BOOLEAN DEFAULT FALSE",
    "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS playbook TEXT DEFAULT ''",
    "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS week_theme TEXT",
    "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS week_started DATE",
]

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.sqlalchemy_url,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def init_db() -> None:
    """Create any missing tables. Additive only — never drops anything."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _ADDITIVE_MIGRATIONS:
            await conn.execute(text(stmt))
    logger.info("Database ready (tables ensured + columns added, nothing dropped).")


async def dispose_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
