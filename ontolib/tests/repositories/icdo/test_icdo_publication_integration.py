from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.icdo.models import CanonicalDataset, IcdoRecord, SourceShape
from ontolib.repositories.icdo.store import IcdoRepository, publish_dataset

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


def _dataset(label: str) -> CanonicalDataset:
    return CanonicalDataset(
        edition="4.0",
        axis="topography",
        records=(IcdoRecord(code="C34", level="category", preferred=label),),
        source_shape=SourceShape(
            sheet_names=("Topography",),
            headers=("ICDO4",),
            merged_ranges=(),
            trailing_blank_rows=0,
        ),
        source_sha256=("a" if label == "LUNG" else "b") * 64,
    )


def _morphology32() -> CanonicalDataset:
    return CanonicalDataset(
        edition="3.2",
        axis="morphology",
        records=tuple(
            IcdoRecord(
                code=code,
                level="morphology",
                base_morphology=code[:4],
                behaviour=code[-1],
                preferred=f"Term {code}",
            )
            for code in ("9680/3", "9751/1", "9751/3")
        ),
        source_shape=SourceShape(
            sheet_names=("Morphology",),
            headers=("ICDO3.2",),
            merged_ranges=(),
            trailing_blank_rows=0,
        ),
        source_sha256="c" * 64,
    )


@pytest.mark.integration
async def test_active_morphology32_codes_resolve_in_one_indexed_generation_join() -> (
    None
):
    engine = make_engine(get_settings().database_url)
    sessions = make_sessionmaker(engine)
    try:
        manifest = await publish_dataset(
            sessions,
            _morphology32(),
            publisher_url="https://example.test/icdo32",
            published_at=datetime.now(UTC),
        )
        resolution = await IcdoRepository(sessions).resolve_active_morphology32_codes(
            {"9680/3", "9751/1", "9999/9"}
        )
        assert resolution.generation_id == manifest.generation_id
        assert resolution.serving_sha256 == manifest.serving_sha256
        assert resolution.resolved_codes == {"9680/3", "9751/1"}

        async with engine.connect() as connection:
            await connection.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                str(row[0])
                for row in (
                    await connection.execute(
                        text(
                            "EXPLAIN SELECT r.code FROM icdo_active_generation a "
                            "JOIN icdo_record r ON r.generation_id=a.generation_id "
                            "AND r.code=ANY(:codes) WHERE a.edition='3.2' "
                            "AND a.axis='morphology'"
                        ),
                        {"codes": ["9680/3", "9751/1", "9999/9"]},
                    )
                ).all()
            )
        assert "idx_icdo_record_filters" in plan
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_concurrent_publications_activate_one_complete_immutable_generation() -> (
    None
):
    engine = make_engine(get_settings().database_url)
    sessions = make_sessionmaker(engine)
    try:
        first, second = await asyncio.gather(
            publish_dataset(
                sessions,
                _dataset("LUNG"),
                publisher_url="https://example.test",
                published_at=datetime.now(UTC),
            ),
            publish_dataset(
                sessions,
                _dataset("BRONCHUS"),
                publisher_url="https://example.test",
                published_at=datetime.now(UTC),
            ),
        )
        repository = IcdoRepository(sessions)
        detail = await repository.detail("4.0", "topography", "C34")
        assert detail is not None
        assert detail["preferred"] in {"LUNG", "BRONCHUS"}
        async with engine.connect() as connection:
            generations = await connection.scalar(
                text(
                    "SELECT count(*) FROM icdo_generation "
                    "WHERE edition='4.0' AND axis='topography'"
                )
            )
            records = await connection.scalar(
                text(
                    "SELECT count(*) FROM icdo_record "
                    "WHERE generation_id IN (:first,:second)"
                ),
                {"first": first.generation_id, "second": second.generation_id},
            )
            active = await connection.scalar(
                text(
                    "SELECT count(*) FROM icdo_active_generation "
                    "WHERE edition='4.0' AND axis='topography'"
                )
            )
        assert (generations, records, active) == (2, 2, 1)
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_cloned_icdo_schema_preserves_columns_constraints_and_indexes() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE SCHEMA icdo_clone"))
            for table in ("icdo_generation", "icdo_record", "icdo_active_generation"):
                await connection.execute(
                    text(
                        f"CREATE TABLE icdo_clone.{table} "
                        f"(LIKE public.{table} INCLUDING ALL)"
                    )
                )
            columns = (
                await connection.execute(
                    text(
                        "SELECT table_schema, table_name, column_name, data_type, "
                        "is_nullable FROM information_schema.columns WHERE "
                        "table_schema IN ('public','icdo_clone') AND "
                        "table_name LIKE 'icdo_%' ORDER BY table_schema, "
                        "table_name, ordinal_position"
                    )
                )
            ).all()
            constraints = (
                await connection.execute(
                    text(
                        "SELECT n.nspname, c.relname, con.contype, "
                        "pg_get_constraintdef(con.oid) FROM pg_constraint con "
                        "JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n "
                        "ON n.oid=c.relnamespace WHERE n.nspname IN "
                        "('public','icdo_clone') AND c.relname LIKE 'icdo_%' "
                        "AND con.contype <> 'f' ORDER BY n.nspname, c.relname, "
                        "con.contype, pg_get_constraintdef(con.oid)"
                    )
                )
            ).all()
            indexes = (
                await connection.execute(
                    text(
                        "SELECT schemaname, tablename, regexp_replace(indexdef, "
                        "'INDEX [^ ]+ ON (public|icdo_clone)\\.', 'INDEX ON ') "
                        "FROM pg_indexes WHERE schemaname IN "
                        "('public','icdo_clone') AND tablename LIKE 'icdo_%' "
                        "ORDER BY schemaname, tablename, 3"
                    )
                )
            ).all()
        public = [row[1:] for row in columns if row[0] == "public"]
        clone = [row[1:] for row in columns if row[0] == "icdo_clone"]
        assert clone == public
        assert [row[1:] for row in constraints if row[0] == "icdo_clone"] == [
            row[1:] for row in constraints if row[0] == "public"
        ]
        assert [row[1:] for row in indexes if row[0] == "icdo_clone"] == [
            row[1:] for row in indexes if row[0] == "public"
        ]
    finally:
        await dispose_engine(engine)
