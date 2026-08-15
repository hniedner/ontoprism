from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.icdo.models import CanonicalDataset, IcdoRecord, SourceShape
from ontolib.repositories.icdo.store import (
    CertificationExpectation,
    IcdoRepository,
    publish_dataset,
)

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
async def test_exact_unpublished_generation_is_unavailable() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        repository = IcdoRepository(make_sessionmaker(engine))

        assert (
            await repository.dataset("4.0", "topography", generation_id="f" * 64)
            is None
        )
    finally:
        await dispose_engine(engine)


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
            {"9680/3", "9751/1", "9999/9"},
            CertificationExpectation(
                source_sha256=manifest.source_sha256,
                edition="3.2",
                axis="morphology",
                row_count=manifest.row_count,
                serving_sha256=manifest.serving_sha256,
            ),
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
async def test_certified_generation_remains_bound_after_pointer_switch() -> None:
    engine = make_engine(get_settings().database_url)
    sessions = make_sessionmaker(engine)
    try:
        first = await publish_dataset(
            sessions,
            _dataset("LUNG"),
            publisher_url="https://example.test",
            published_at=datetime.now(UTC),
        )
        repository = IcdoRepository(sessions)
        certified = await repository.certified_metadata(
            "4.0",
            "topography",
            CertificationExpectation(
                source_sha256=first.source_sha256,
                edition="4.0",
                axis="topography",
                row_count=first.row_count,
                serving_sha256=first.serving_sha256,
            ),
        )
        assert certified is not None
        await publish_dataset(
            sessions,
            _dataset("BRONCHUS"),
            publisher_url="https://example.test",
            published_at=datetime.now(UTC),
        )

        detail = await repository.detail(
            "4.0", "topography", "C34", generation_id=certified.generation_id
        )
        assert detail is not None
        assert detail["preferred"] == "LUNG"
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_search_refuses_denormalized_column_corruption() -> None:
    engine = make_engine(get_settings().database_url)
    sessions = make_sessionmaker(engine)
    try:
        manifest = await publish_dataset(
            sessions,
            _dataset("LUNG"),
            publisher_url="https://example.test",
            published_at=datetime.now(UTC),
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE icdo_record SET search_text='corrupt' "
                    "WHERE generation_id=:id"
                ),
                {"id": manifest.generation_id},
            )
        repository = IcdoRepository(sessions)
        result = await repository.search(
            "4.0",
            "topography",
            query="corrupt",
            limit=10,
            offset=0,
            generation_id=manifest.generation_id,
        )
        assert result["total"] == 0
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("code", "C35", "ck_icdo_record_code_payload"),
        ("level", "leaf", "ck_icdo_record_level_payload"),
        ("behaviour", "9", "ck_icdo_record_behaviour_payload"),
    ],
)
async def test_relational_icdo_columns_cannot_drift_from_payload(
    column: str, value: str, constraint: str
) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        manifest = await publish_dataset(
            make_sessionmaker(engine),
            _dataset("LUNG"),
            publisher_url="https://example.test",
            published_at=datetime.now(UTC),
        )
        with pytest.raises(IntegrityError, match=constraint):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"UPDATE icdo_record SET {column}=:value "  # noqa: S608
                        "WHERE generation_id=:generation"
                    ),
                    {"value": value, "generation": manifest.generation_id},
                )
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("payload", "constraint"),
    [
        ("null", "ck_icdo_record_code_payload"),
        ("[]", "ck_icdo_record_code_payload"),
        ('{"preferred":"LUNG"}', "ck_icdo_record_code_payload"),
    ],
)
async def test_icdo_record_payload_requires_an_identity_object(
    payload: str, constraint: str
) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        manifest = await publish_dataset(
            make_sessionmaker(engine),
            _dataset("LUNG"),
            publisher_url="https://example.test",
            published_at=datetime.now(UTC),
        )
        with pytest.raises(IntegrityError, match=constraint):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE icdo_record SET payload=CAST(:payload AS jsonb) "
                        "WHERE generation_id=:generation"
                    ),
                    {"payload": payload, "generation": manifest.generation_id},
                )
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_postgres_search_filters_paginates_and_excludes_inactive() -> None:
    engine = make_engine(get_settings().database_url)
    sessions = make_sessionmaker(engine)
    try:
        source_shape = SourceShape(
            sheet_names=("Morphology",),
            headers=("ICDO4",),
            merged_ranges=(),
            trailing_blank_rows=0,
        )
        await publish_dataset(
            sessions,
            CanonicalDataset(
                edition="4.0",
                axis="morphology",
                records=(
                    IcdoRecord(
                        code="89999/3",
                        level="morphology",
                        base_morphology="8999",
                        specificity="9",
                        behaviour="3",
                        preferred="Inactive generation only",
                    ),
                ),
                source_shape=source_shape,
                source_sha256="d" * 64,
            ),
            publisher_url="https://example.test/icdo4",
            published_at=datetime.now(UTC),
        )
        await publish_dataset(
            sessions,
            CanonicalDataset(
                edition="4.0",
                axis="morphology",
                records=(
                    IcdoRecord(
                        code="80000/0",
                        level="morphology",
                        base_morphology="8000",
                        specificity="0",
                        behaviour="0",
                        preferred="Neoplasm",
                    ),
                    IcdoRecord(
                        code="8001A/3",
                        level="morphology",
                        base_morphology="8001",
                        specificity="A",
                        behaviour="3",
                        preferred="Malignant tumor",
                        synonyms=("Cancer alpha",),
                    ),
                    IcdoRecord(
                        code="8010B/3",
                        level="morphology",
                        base_morphology="8010",
                        specificity="B",
                        behaviour="3",
                        preferred="Carcinoma",
                        related=("Epithelial malignancy",),
                    ),
                ),
                source_shape=source_shape,
                source_sha256="e" * 64,
            ),
            publisher_url="https://example.test/icdo4",
            published_at=datetime.now(UTC),
        )
        repository = IcdoRepository(sessions)

        listed = await repository.search(
            "4.0", "morphology", query="", limit=1, offset=1
        )
        assert listed == {
            "edition": "4.0",
            "axis": "morphology",
            "query": "",
            "total": 3,
            "limit": 1,
            "offset": 1,
            "hits": [
                {
                    "code": "8001A/3",
                    "level": "morphology",
                    "parent_code": None,
                    "base_morphology": "8001",
                    "specificity": "A",
                    "behaviour": "3",
                    "preferred": "Malignant tumor",
                    "synonyms": ["Cancer alpha"],
                    "related": [],
                    "notes": [],
                    "code_references": [],
                    "see_also": [],
                    "see_notes": [],
                    "includes": [],
                    "excludes": [],
                    "other_text": [],
                }
            ],
        }
        text_hits = await repository.search(
            "4.0", "morphology", query="EPITHELIAL", limit=10, offset=0
        )
        code_hits = await repository.search(
            "4.0", "morphology", query="8001a", limit=10, offset=0
        )
        filtered = await repository.search(
            "4.0",
            "morphology",
            query="",
            behaviour="3",
            level="morphology",
            limit=10,
            offset=0,
        )
        inactive_hits = await repository.search(
            "4.0", "morphology", query="inactive", limit=10, offset=0
        )

        assert text_hits["total"] == 1
        assert [row["code"] for row in text_hits["hits"]] == ["8010B/3"]
        assert code_hits["total"] == 1
        assert [row["code"] for row in code_hits["hits"]] == ["8001A/3"]
        assert filtered["total"] == 2
        assert [row["code"] for row in filtered["hits"]] == [
            "8001A/3",
            "8010B/3",
        ]
        assert inactive_hits["total"] == 0
        assert inactive_hits["hits"] == []
    finally:
        await dispose_engine(engine)
