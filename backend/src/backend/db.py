"""Async SQLAlchemy engine + session factory for ontoprism's PostgreSQL (pgvector).

Holds the concept/CDE embeddings (768-dim) used for semantic "similar items" search.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(database_url: str) -> AsyncEngine:
    """Create the async engine (pool pre-ping so a recycled connection is detected)."""
    return create_async_engine(database_url, pool_pre_ping=True, future=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the async session factory."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def dispose_engine(engine: AsyncEngine) -> None:
    """Dispose the engine's connection pool on shutdown."""
    await engine.dispose()
