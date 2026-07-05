"""Integration test: the Alembic migration reproduces the embedding schema.

Creates a throwaway database, runs ``alembic upgrade head`` against it, and asserts the
resulting schema matches what the semantic-similarity endpoints require (pgvector
tables + HNSW cosine indexes). Skipped when Postgres is unreachable.
"""

import asyncio
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from backend.config import get_settings

_TEMP_DB = "ontoprism_migtest"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Turn a ``postgresql+asyncpg://…`` URL into a plain asyncpg DSN."""
    return sqlalchemy_url.replace("+asyncpg", "")


def _swap_db(url: str, db_name: str) -> str:
    return f"{url.rsplit('/', 1)[0]}/{db_name}"


async def _pg_reachable(admin_dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(admin_dsn, timeout=2)
    except (OSError, asyncpg.PostgresError):
        return False
    await conn.close()
    return True


async def _recreate_db(admin_dsn: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {_TEMP_DB}")
        await conn.execute(f"CREATE DATABASE {_TEMP_DB}")
    finally:
        await conn.close()


async def _drop_db(admin_dsn: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {_TEMP_DB}")
    finally:
        await conn.close()


async def _introspect(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        return {
            "version": await conn.fetchval("SELECT version_num FROM alembic_version"),
            "has_vector_ext": await conn.fetchval(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
            ),
            "embedding_type": await conn.fetchval(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name = 'ncit_concepts' AND column_name = 'embedding'"
            ),
            "tables": await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('ncit_concepts', 'cde_repository')"
            ),
            "hnsw_indexes": await conn.fetchval(
                "SELECT count(*) FROM pg_indexes "
                "WHERE indexname IN "
                "('idx_ncit_concepts_hnsw', 'idx_cde_repository_hnsw')"
            ),
        }
    finally:
        await conn.close()


@pytest.mark.integration
def test_migration_reproduces_embedding_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = get_settings().database_url
    admin_dsn = _asyncpg_dsn(base_url)
    if not asyncio.run(_pg_reachable(admin_dsn)):
        pytest.skip("Postgres not reachable")

    temp_sa_url = _swap_db(base_url, _TEMP_DB)
    temp_dsn = _swap_db(admin_dsn, _TEMP_DB)

    asyncio.run(_recreate_db(admin_dsn))
    try:
        monkeypatch.setenv("DATABASE_URL", temp_sa_url)
        get_settings.cache_clear()  # env.py reads the temp URL via settings
        cfg = Config(str(_REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
        command.upgrade(cfg, "head")

        schema = asyncio.run(_introspect(temp_dsn))
    finally:
        get_settings.cache_clear()
        asyncio.run(_drop_db(admin_dsn))

    assert schema["version"] == "0001_embedding_tables"
    assert schema["has_vector_ext"] == 1
    assert schema["embedding_type"] == "vector"
    assert schema["tables"] == 2  # both embedding tables created
    assert schema["hnsw_indexes"] == 2  # both HNSW cosine indexes created
