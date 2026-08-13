"""Integration test: migration up+down for 0004_xref (issue #71).

Uses subprocess to run alembic in a separate process, avoiding event-loop conflicts
with the async test harness (alembic's sync connection tries to start its own loop)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db import dispose_engine, make_engine
from ontolib.repositories.xref.vocab import CLOSE_MATCH

pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


async def _evidence_column_exists(engine: object) -> bool:
    async with engine.connect() as conn:  # type: ignore[attr-defined]
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'concept_xref' AND column_name = 'evidence'"
            )
        )
        return bool(list(rows))


@pytest.mark.integration
async def test_generation_schema_constraints_and_indexes() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            columns = {
                row[0]
                for row in await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'concept_xref'"
                    )
                )
            }
            indexes = {
                row[0]
                for row in await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename = 'concept_xref'"
                    )
                )
            }
            generation_checks = {
                row[0]
                for row in await conn.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = 'xref_generation'::regclass"
                    )
                )
            }
            generation_unique_columns = {
                tuple(row[0])
                for row in await conn.execute(
                    text(
                        "SELECT ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH "
                        "ORDINALITY AS k(attnum, ord) JOIN pg_attribute a "
                        "ON a.attrelid=c.conrelid AND a.attnum=k.attnum "
                        "ORDER BY k.ord) FROM pg_constraint c WHERE "
                        "c.conrelid='xref_generation'::regclass "
                        "AND c.contype='u'"
                    )
                )
            }

        assert {
            "generation_id",
            "subject_system",
            "subject_version",
            "subject_id",
            "object_system",
            "object_version",
            "object_id",
        } <= columns
        assert {"idx_concept_xref_forward", "idx_concept_xref_reverse"} <= indexes
        assert any(
            "state" in check and "prepared" in check for check in generation_checks
        )
        assert any("content_sha256" in check for check in generation_checks)
        assert ("source", "content_sha256") not in generation_unique_columns

        async with engine.connect() as conn:
            icdo_tables = {
                row[0]
                for row in await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name IN "
                        "('icdo_generation','icdo_record','icdo_active_generation')"
                    )
                )
            }
            icdo_indexes = {
                row[0]
                for row in await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE tablename='icdo_record'"
                    )
                )
            }
        assert icdo_tables == {
            "icdo_generation",
            "icdo_record",
            "icdo_active_generation",
        }
        assert "idx_icdo_record_filters" in icdo_indexes

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO xref_generation "
                    "(id,source,content_sha256,source_metadata,graph_iri,state) VALUES "
                    "(:id,'uberon-cl',:content,CAST(:metadata AS jsonb),"
                    "'https://example.test/g','prepared')"
                ),
                {
                    "id": "a" * 64,
                    "content": "b" * 64,
                    "metadata": json.dumps(
                        {
                            "source": "uberon-cl",
                            "ncit_source_identity": "c" * 64,
                            "uberon_source_identity": "d" * 64,
                            "uberon_serving_identity": "e" * 64,
                        }
                    ),
                },
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO xref_active_generation (source,generation_id) "
                        "VALUES ('uberon-cl-promotion',:id)"
                    ),
                    {"id": "a" * 64},
                )

        for column, value in (
            ("predicate_id", "https://example.test/not-skos"),
            ("lifecycle_state", "invented"),
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO concept_xref "
                            "(generation_id,generation_source,subject_system,"
                            "subject_version,subject_id,predicate_id,object_system,"
                            "object_version,object_id,mapping_justification,confidence,"
                            "lifecycle_state,review_status,author) VALUES "
                            "(:generation,'uberon-cl','ncit','v','C1',:predicate,"
                            "'uberon-cl','v','U1','j',0.5,:lifecycle,'unreviewed','')"
                        ),
                        {
                            "generation": "a" * 64,
                            "predicate": value
                            if column == "predicate_id"
                            else CLOSE_MATCH,
                            "lifecycle": value
                            if column == "lifecycle_state"
                            else "proposed",
                        },
                    )

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO xref_generation "
                    "(id,source,content_sha256,source_metadata,graph_iri,state) VALUES "
                    "(:id,'uberon-cl-promotion',:content,CAST(:metadata AS jsonb),"
                    "'https://example.test/promotion','prepared')"
                ),
                {
                    "id": "f" * 64,
                    "content": "1" * 64,
                    "metadata": json.dumps(
                        {
                            "source": "uberon-cl-promotion",
                            "ncit_source_identity": "2" * 64,
                            "uberon_source_identity": "3" * 64,
                            "uberon_serving_identity": "4" * 64,
                        }
                    ),
                },
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO concept_xref "
                        "(generation_id,generation_source,subject_system,"
                        "subject_version,subject_id,predicate_id,object_system,"
                        "object_version,object_id,mapping_justification,confidence,"
                        "lifecycle_state,review_status,author) VALUES "
                        "(:generation,'uberon-cl-promotion','uberon-cl','v','U1',"
                        ":predicate,'ncit','v','C1','j',1,'validated','reviewed','')"
                    ),
                    {"generation": "f" * 64, "predicate": CLOSE_MATCH},
                )

        malformed_metadata = (
            None,
            7,
            [],
            {"source": 7},
            {
                "source": "uberon-cl",
                "ncit_source_identity": None,
                "uberon_source_identity": "d" * 64,
                "uberon_serving_identity": "e" * 64,
            },
            {
                "source": "uberon-cl",
                "ncit_source_identity": 7,
                "uberon_source_identity": "d" * 64,
                "uberon_serving_identity": "e" * 64,
            },
            {
                "source": "uberon-cl",
                "ncit_source_identity": "not-a-digest",
                "uberon_source_identity": "d" * 64,
                "uberon_serving_identity": "e" * 64,
            },
        )
        for index, metadata in enumerate(malformed_metadata):
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO xref_generation "
                            "(id,source,content_sha256,source_metadata,"
                            "graph_iri,state) "
                            "VALUES (:id,'uberon-cl',:content,CAST(:metadata AS jsonb),"
                            ":graph,'prepared')"
                        ),
                        {
                            "id": f"{index + 1:x}" * 64,
                            "content": "9" * 64,
                            "metadata": json.dumps(metadata),
                            "graph": f"https://example.test/malformed/{index}",
                        },
                    )
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_evidence_column_added_and_removed_by_0006(tmp_path: object) -> None:
    """The per-promotion ``evidence`` column exists after ``upgrade head`` and is gone
    after a downgrade past 0006 (#122, D36) — a schema fact, asked of Postgres."""
    engine = make_engine(get_settings().database_url)
    env = {**os.environ, "PYTHONPATH": "."}
    alembic = shutil.which("alembic") or "alembic"

    def _alembic(*args: str) -> None:
        subprocess.run(  # noqa: S603
            [alembic, *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=os.getcwd(),
        )

    try:
        assert await _evidence_column_exists(engine), (
            "evidence column missing at head — migration 0006 did not run"
        )
        _alembic("downgrade", "0005_search_weights")
        assert not await _evidence_column_exists(engine), (
            "evidence column survived the downgrade — 0006.downgrade is not an inverse"
        )
    finally:
        _alembic("upgrade", "head")
        await dispose_engine(engine)


@pytest.mark.integration
async def test_migration_up_and_down_roundtrip() -> None:
    engine = make_engine(get_settings().database_url)
    env = {**os.environ, "PYTHONPATH": "."}
    alembic = shutil.which("alembic") or "alembic"

    def _alembic(*args: str) -> None:
        subprocess.run(  # noqa: S603
            [alembic, *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=os.getcwd(),
        )

    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('xref_run', 'concept_xref')"
                )
            )
            assert {row[0] for row in rows} == {"xref_run", "concept_xref"}

        _alembic("downgrade", "0003_decomposition")

        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('xref_run', 'concept_xref')"
                )
            )
            tables = {row[0] for row in rows}
        assert "xref_run" not in tables
        assert "concept_xref" not in tables

        _alembic("upgrade", "head")

        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('xref_run', 'concept_xref')"
                )
            )
            tables = {row[0] for row in rows}
        assert "xref_run" in tables
        assert "concept_xref" in tables
    finally:
        _alembic("upgrade", "head")
        await dispose_engine(engine)
