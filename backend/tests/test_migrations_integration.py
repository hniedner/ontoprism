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


async def _decomposition_lifecycle_facts(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        tables = {
            row["table_name"]
            for row in await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('decomp_work_item', "
                "'decomp_minted_proposal')"
            )
        }
        run_columns = {
            row["column_name"]: row["data_type"]
            for row in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'decomp_run'"
            )
        }
        constituent_columns = {
            row["column_name"]: row["data_type"]
            for row in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'decomp_constituent'"
            )
        }
        constraints = [
            row["definition"]
            for row in await conn.fetch(
                "SELECT pg_get_constraintdef(oid) AS definition "
                "FROM pg_constraint WHERE conrelid IN "
                "('decomp_run'::regclass, 'decomp_work_item'::regclass)"
            )
        ]
        trigger = await conn.fetchval(
            "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgrelid = 'decomp_run'::regclass "
            "AND tgname = 'decomp_run_identity_immutable'"
        )
        proposal_pk = await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'decomp_minted_proposal'::regclass "
            "AND contype = 'p'"
        )
        return {
            "tables": tables,
            "run_columns": run_columns,
            "constituent_columns": constituent_columns,
            "constraints": constraints,
            "trigger": trigger,
            "proposal_pk": proposal_pk,
        }
    finally:
        await conn.close()


async def _decomposition_lifecycle_is_absent(dsn: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        table_count = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('decomp_work_item', 'decomp_minted_proposal')"
        )
        column_count = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'decomp_run' "
            "AND column_name IN ('source_identity', 'fingerprint', "
            "'fingerprint_sha256', 'emitted_at', 'error_type', 'error_message')"
        )
        return table_count == 0 and column_count == 0
    finally:
        await conn.close()


async def _complete_definition_schema_facts(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        definition_columns = {
            row["column_name"]: row["data_type"]
            for row in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'decomp_definition_fact'"
            )
        }
        constituent_source_type = await conn.fetchval(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'decomp_constituent' "
            "AND column_name = 'source_definition_ids'"
        )
        definition_constraints = [
            row["definition"]
            for row in await conn.fetch(
                "SELECT pg_get_constraintdef(oid) AS definition "
                "FROM pg_constraint "
                "WHERE conrelid = to_regclass('decomp_definition_fact')"
            )
        ]
        return {
            "definition_columns": definition_columns,
            "constituent_source_type": constituent_source_type,
            "definition_constraints": definition_constraints,
        }
    finally:
        await conn.close()


async def _complete_definition_schema_is_absent(dsn: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        table = await conn.fetchval("SELECT to_regclass('decomp_definition_fact')")
        column = await conn.fetchval(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'decomp_constituent' "
            "AND column_name = 'source_definition_ids'"
        )
        return table is None and column is None
    finally:
        await conn.close()


async def _nested_definition_group_schema_facts(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        group_columns = {
            row["column_name"]: row["data_type"]
            for row in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'decomp_definition_group'"
            )
        }
        edge_columns = {
            row["column_name"]: row["data_type"]
            for row in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'decomp_definition_group_edge'"
            )
        }
        fact_constraints = [
            row["definition"]
            for row in await conn.fetch(
                "SELECT pg_get_constraintdef(oid) AS definition "
                "FROM pg_constraint "
                "WHERE conrelid = to_regclass('decomp_definition_fact')"
            )
        ]
        return {
            "group_columns": group_columns,
            "edge_columns": edge_columns,
            "fact_constraints": fact_constraints,
        }
    finally:
        await conn.close()


async def _nested_definition_group_schema_is_absent(dsn: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return (
            await conn.fetchval("SELECT to_regclass('decomp_definition_group')") is None
            and await conn.fetchval(
                "SELECT to_regclass('decomp_definition_group_edge')"
            )
            is None
        )
    finally:
        await conn.close()


async def _seed_historical_decomposition_outcomes(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO decomp_run "
            "(id, branch, status, ncit_version, started_at, finished_at, "
            "source_identity, fingerprint, fingerprint_sha256, emitted_at, "
            "publication_state) VALUES "
            "('historical-outcomes', 'neoplasm', 'complete', '26.07d', now(), "
            "now(), repeat('a', 64), '{}'::jsonb, repeat('b', 64), now(), 'legacy')"
        )
        await conn.executemany(
            "INSERT INTO decomp_work_item "
            "(run_id, concept_code, ordinal, state, attempt_count, semantic_type, "
            "is_decomposed, is_residual, constituent_count, minted_count, "
            "completed_at) VALUES "
            "('historical-outcomes', $1, $2, 'complete', 1, $3, $4, $5, $6, 0, "
            "now())",
            [
                ("C1", 0, "Neoplastic Process", True, False, 1),
                ("C2", 1, "Neoplastic Process", False, True, 0),
                ("C3", 2, None, False, False, 0),
            ],
        )
    finally:
        await conn.close()


async def _decomposition_outcome_schema_facts(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        columns = {
            row["column_name"]: row["data_type"]
            for row in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'decomp_work_item' "
                "AND column_name IN ('outcome', 'semantic_types')"
            )
        }
        constraints = [
            row["definition"]
            for row in await conn.fetch(
                "SELECT pg_get_constraintdef(oid) AS definition "
                "FROM pg_constraint "
                "WHERE conrelid = 'decomp_work_item'::regclass AND contype = 'c'"
            )
        ]
        rows = [
            dict(row)
            for row in await conn.fetch(
                "SELECT concept_code, outcome, semantic_types::text AS semantic_types "
                "FROM decomp_work_item WHERE run_id = 'historical-outcomes' "
                "ORDER BY ordinal"
            )
        ]
        invalid_rejected = False
        transaction = conn.transaction()
        await transaction.start()
        try:
            await conn.execute(
                "UPDATE decomp_work_item SET outcome = 'semantic-excluded' "
                "WHERE run_id = 'historical-outcomes' AND concept_code = 'C1'"
            )
        except asyncpg.CheckViolationError:
            invalid_rejected = True
        finally:
            await transaction.rollback()
        return {
            "columns": columns,
            "constraints": constraints,
            "rows": rows,
            "invalid_rejected": invalid_rejected,
        }
    finally:
        await conn.close()


async def _decomposition_outcome_schema_is_absent(dsn: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return (
            await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'decomp_work_item' "
                "AND column_name IN ('outcome', 'semantic_types')"
            )
            == 0
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

    assert revision == "0014_definition_presence"
    assert legacy_rows == 1
    assert publication_tables == 2


@pytest.mark.integration
@pytest.mark.mutating_integration
@pytest.mark.usefixtures("isolated_postgres_settings")
def test_decomposition_run_lifecycle_migration_roundtrip() -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))

    async def seed_legacy_running_run() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO decomp_run "
                "(id, branch, status, ncit_version, started_at) "
                "VALUES ('legacy-running', 'neoplasm', 'running', '26.02d', now())"
            )
        finally:
            await conn.close()

    async def legacy_run_facts() -> tuple[str, str | None, str, int]:
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT status, error_type, publication_state "
                "FROM decomp_run WHERE id = 'legacy-running'"
            )
            work_items = await conn.fetchval(
                "SELECT count(*) FROM decomp_work_item WHERE run_id = 'legacy-running'"
            )
            return (
                row["status"],
                row["error_type"],
                row["publication_state"],
                work_items,
            )
        finally:
            await conn.close()

    try:
        command.downgrade(cfg, "0007_embedding_publication")
        absent_after_down = asyncio.run(_decomposition_lifecycle_is_absent(dsn))
        asyncio.run(seed_legacy_running_run())
        command.upgrade(cfg, "head")
        facts = asyncio.run(_decomposition_lifecycle_facts(dsn))
        legacy_facts = asyncio.run(legacy_run_facts())
    finally:
        command.upgrade(cfg, "head")

    assert absent_after_down is True
    assert facts["tables"] == {"decomp_work_item", "decomp_minted_proposal"}
    assert {
        "source_identity": "text",
        "fingerprint": "jsonb",
        "fingerprint_sha256": "text",
        "emitted_at": "timestamp with time zone",
        "error_type": "text",
        "error_message": "text",
        "publication_state": "text",
        "publication_attempt_count": "integer",
        "representation_identity": "text",
        "publication_artifact_path": "text",
        "publication_built_at": "timestamp with time zone",
        "publication_started_at": "timestamp with time zone",
        "publication_finished_at": "timestamp with time zone",
        "publication_error_type": "text",
        "publication_error_message": "text",
    }.items() <= facts["run_columns"].items()
    assert {
        "needs_review": "boolean",
        "relationship_group": "text",
        "source_role": "text",
    }.items() <= facts["constituent_columns"].items()
    constraints = " ".join(facts["constraints"])
    assert "running" in constraints
    assert "failed" in constraints
    assert "complete" in constraints
    assert "pending" in constraints
    assert facts["trigger"] is not None
    assert facts["proposal_pk"] == ("PRIMARY KEY (run_id, concept_code, proposal_id)")
    assert legacy_facts == (
        "failed",
        "LegacyRun",
        "pending",
        0,
    )


@pytest.mark.integration
@pytest.mark.mutating_integration
@pytest.mark.usefixtures("isolated_postgres_settings")
def test_complete_definition_migration_roundtrip() -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    try:
        command.downgrade(cfg, "0008_decomposition_run_lifecycle")
        absent_after_down = asyncio.run(_complete_definition_schema_is_absent(dsn))
        command.upgrade(cfg, "head")
        facts = asyncio.run(_complete_definition_schema_facts(dsn))
    finally:
        command.upgrade(cfg, "head")

    assert absent_after_down is True
    assert facts["definition_columns"] == {
        "run_id": "text",
        "concept_code": "text",
        "fact_id": "text",
        "anchor_code": "text",
        "group_id": "text",
        "depth": "integer",
        "fact_kind": "text",
        "genus_code": "text",
        "is_defined": "boolean",
        "role_code": "text",
        "filler_code": "text",
    }
    assert facts["constituent_source_type"] == "jsonb"
    constraints = " ".join(facts["definition_constraints"])
    assert "PRIMARY KEY (run_id, concept_code, fact_id)" in constraints
    assert "fact_kind" in constraints
    assert "genus" in constraints
    assert "restriction" in constraints


@pytest.mark.integration
@pytest.mark.mutating_integration
@pytest.mark.usefixtures("isolated_postgres_settings")
def test_nested_definition_group_migration_roundtrip() -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    try:
        command.downgrade(cfg, "0011_decomposition_publication")
        absent_after_down = asyncio.run(_nested_definition_group_schema_is_absent(dsn))
        command.upgrade(cfg, "head")
        facts = asyncio.run(_nested_definition_group_schema_facts(dsn))
    finally:
        command.upgrade(cfg, "head")

    assert absent_after_down is True
    assert facts["group_columns"] == {
        "run_id": "text",
        "concept_code": "text",
        "group_id": "text",
        "anchor_code": "text",
        "depth": "integer",
        "is_root": "boolean",
    }
    assert facts["edge_columns"] == {
        "run_id": "text",
        "concept_code": "text",
        "parent_group_id": "text",
        "child_group_id": "text",
    }
    assert any(
        "FOREIGN KEY (run_id, concept_code, group_id)" in constraint
        and "decomp_definition_group" in constraint
        for constraint in facts["fact_constraints"]
    )


@pytest.mark.integration
@pytest.mark.mutating_integration
@pytest.mark.usefixtures("isolated_postgres_settings")
def test_outcome_migration_backfills_unknown_and_rejects_invalid_shape() -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    try:
        command.downgrade(cfg, "0012_nested_definition_groups")
        absent_after_down = asyncio.run(_decomposition_outcome_schema_is_absent(dsn))
        asyncio.run(_seed_historical_decomposition_outcomes(dsn))
        command.upgrade(cfg, "head")
        facts = asyncio.run(_decomposition_outcome_schema_facts(dsn))
    finally:
        command.upgrade(cfg, "head")

    assert absent_after_down is True
    assert facts["columns"] == {"outcome": "text", "semantic_types": "jsonb"}
    assert facts["rows"] == [
        {
            "concept_code": "C1",
            "outcome": "decomposed",
            "semantic_types": '["Neoplastic Process"]',
        },
        {
            "concept_code": "C2",
            "outcome": "residual",
            "semantic_types": '["Neoplastic Process"]',
        },
        {"concept_code": "C3", "outcome": "unknown", "semantic_types": "[]"},
    ]
    assert facts["invalid_rejected"] is True
    constraints = " ".join(facts["constraints"])
    assert "semantic-excluded" in constraints
    assert "atomic-no-op" in constraints
    assert "unknown" in constraints


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
