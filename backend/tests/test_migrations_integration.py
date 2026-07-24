"""Integration tests for the Alembic embedding-schema migration.

Verifies the migration (a) produces the exact pgvector schema the similarity endpoints
need, (b) round-trips (upgrade→downgrade), and (c) matches the configured DB. The
mutating round-trip requires
disposable Postgres; the separately marked full-store parity contract skips when its
configured database or migrated embedding tables are unavailable.
"""

import asyncio
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from backend.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Turn a ``postgresql+asyncpg://…`` URL into a plain asyncpg DSN."""
    return sqlalchemy_url.replace("+asyncpg", "")


async def _pg_reachable(admin_dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(admin_dsn, timeout=2)
    except (OSError, asyncpg.PostgresError):
        return False
    await conn.close()
    return True


_EMBEDDING_TABLES = ("ncit_concepts", "cde_repository")


async def _table_facts(conn: asyncpg.Connection, table: str) -> dict[str, Any]:
    # Parameterized + join (not ::regclass) so a missing table returns None, not raises.
    return {
        "embedding_type": await conn.fetchval(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname = $1 AND a.attname = 'embedding'",
            table,
        ),
        "metadata_type": await conn.fetchval(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = 'metadata'",
            table,
        ),
        "hnsw_indexdef": await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE indexname = $1",
            f"idx_{table}_hnsw",
        ),
    }


async def _schema_facts(dsn: str) -> dict[str, Any]:
    """Schema facts the similarity endpoints depend on (no alembic assumptions).

    Introspects *both* embedding tables so a divergence in either is caught.
    """
    conn = await asyncpg.connect(dsn)
    try:
        return {
            "has_vector_ext": await conn.fetchval(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
            ),
            "tables": await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('ncit_concepts', 'cde_repository', "
                "'embedding_corpus_manifest', 'embedding_corpus_staging')"
            ),
            "per_table": {
                table: await _table_facts(conn, table) for table in _EMBEDDING_TABLES
            },
            "manifest_columns": {
                row["column_name"]: row["data_type"]
                for row in await conn.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'embedding_corpus_manifest'"
                )
            },
            "staging_primary_key": await conn.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'embedding_corpus_staging'::regclass "
                "AND contype = 'p'"
            ),
            "active_index": await conn.fetchval(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_embedding_corpus_active'"
            ),
            "manifest_checks": [
                row["definition"]
                for row in await conn.fetch(
                    "SELECT pg_get_constraintdef(oid) AS definition "
                    "FROM pg_constraint WHERE conrelid = "
                    "'embedding_corpus_manifest'::regclass AND contype = 'c'"
                )
            ],
        }
    finally:
        await conn.close()


async def _table_count(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('ncit_concepts', 'cde_repository', "
            "'embedding_corpus_manifest', 'embedding_corpus_staging')"
        )
    finally:
        await conn.close()


def _assert_embedding_schema(facts: dict[str, Any]) -> None:
    assert facts["has_vector_ext"] == 1
    assert facts["tables"] == 4
    for table in _EMBEDDING_TABLES:
        t = facts["per_table"][table]
        assert t["embedding_type"] == "vector(768)", table  # dim matters for similarity
        assert t["metadata_type"] == "jsonb", table
        # HNSW cosine opclass — an L2 opclass would silently return wrong neighbors.
        indexdef = t["hnsw_indexdef"] or ""
        assert "hnsw" in indexdef, table
        assert "vector_cosine_ops" in indexdef, table
    assert facts["manifest_columns"] == {
        "build_id": "uuid",
        "corpus": "text",
        "state": "text",
        "is_active": "boolean",
        "source_version": "text",
        "source_hash": "text",
        "model_id": "text",
        "model_revision": "text",
        "vector_dimension": "integer",
        "expected_row_count": "integer",
        "actual_row_count": "integer",
        "code_commit": "text",
        "required_doc_ids": "ARRAY",
        "error_message": "text",
        "created_at": "timestamp with time zone",
        "completed_at": "timestamp with time zone",
    }
    assert facts["staging_primary_key"] == "PRIMARY KEY (build_id, doc_id)"
    active_index = facts["active_index"] or ""
    assert "UNIQUE" in active_index
    assert "WHERE is_active" in active_index
    checks = " ".join(facts["manifest_checks"])
    assert "cardinality(required_doc_ids) > 0" in checks
    assert "state = 'building'" in checks
    assert "state = 'failed'" in checks
    assert "state = 'complete'" in checks


@pytest.mark.integration
@pytest.mark.mutating_integration
@pytest.mark.usefixtures("isolated_postgres_settings")
def test_migration_upgrade_downgrade_roundtrip() -> None:
    base_url = get_settings().database_url
    dsn = _asyncpg_dsn(base_url)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    try:
        command.downgrade(cfg, "base")
        after_down = asyncio.run(_table_count(dsn))
        command.upgrade(cfg, "head")
        facts = asyncio.run(_schema_facts(dsn))
    finally:
        command.upgrade(cfg, "head")

    assert after_down == 0  # downgrade drops both embedding tables
    _assert_embedding_schema(facts)


@pytest.mark.integration
@pytest.mark.mutating_integration
@pytest.mark.usefixtures("isolated_postgres_settings")
def test_legacy_embedding_tables_stamp_predecessor_then_upgrade() -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0001_embedding_tables")

    async def seed_legacy() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO ncit_concepts (doc_id, embedding, metadata) "
                "VALUES ('LEGACY', $1::vector, '{}'::jsonb)",
                "[" + ",".join(["0.5"] * 768) + "]",
            )
            await conn.execute("DROP TABLE alembic_version")
        finally:
            await conn.close()

    async def legacy_facts() -> tuple[str | None, int, int]:
        conn = await asyncpg.connect(dsn)
        try:
            return (
                await conn.fetchval("SELECT version_num FROM alembic_version"),
                await conn.fetchval(
                    "SELECT count(*) FROM ncit_concepts WHERE doc_id = 'LEGACY'"
                ),
                await conn.fetchval(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name "
                    "IN ('embedding_corpus_manifest','embedding_corpus_staging')"
                ),
            )
        finally:
            await conn.close()

    try:
        asyncio.run(seed_legacy())
        command.stamp(cfg, "0001_embedding_tables")
        command.upgrade(cfg, "head")
        revision, legacy_rows, publication_tables = asyncio.run(legacy_facts())
    finally:
        command.upgrade(cfg, "head")

    assert revision == "0007_embedding_publication"
    assert legacy_rows == 1
    assert publication_tables == 2


@pytest.mark.integration
@pytest.mark.full_store
def test_migration_matches_cloned_db_schema() -> None:
    # Parity: the live/cloned DB (created by pg_dump) must match what the migration
    # produces — otherwise `migrate-stamp` would mark a mismatched clone as migrated.
    dsn = _asyncpg_dsn(get_settings().database_url)
    if not asyncio.run(_pg_reachable(dsn)):
        pytest.skip("Postgres not reachable")
    facts = asyncio.run(_schema_facts(dsn))
    if not facts["tables"]:
        pytest.skip("embedding tables not present in the configured DB")
    _assert_embedding_schema(facts)
