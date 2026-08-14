"""Immutable generation publication and active ICD-O repository reads."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import text

from ontolib.repositories.icdo.ingest import canonical_bytes
from ontolib.repositories.icdo.models import CanonicalDataset, IcdoRecord, SourceShape

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class IcdoManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    edition: Literal["3.2", "4.0"]
    axis: Literal["morphology", "topography"]
    publisher_url: str
    source_sha256: str
    archive_sha256: str | None
    annex_sha256: str | None
    reader_identity: str
    serving_sha256: str
    row_count: int
    term_counts: dict[str, int]
    published_at: AwareDatetime


class CertificationExpectation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    source_sha256: str
    edition: Literal["3.2", "4.0"]
    axis: Literal["morphology", "topography"]
    row_count: int
    serving_sha256: str


class IcdoCertificationError(ValueError):
    """Active ICD-O data differs from its configured certified identity."""


class IcdoRepositoryUnavailableError(RuntimeError):
    """The required active ICD-O dataset is absent or structurally invalid."""


class IcdoCodeResolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    serving_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_codes: set[str]


def certify_dataset(
    manifest: IcdoManifest,
    dataset: CanonicalDataset,
    expected: CertificationExpectation,
) -> IcdoManifest:
    recomputed = dataset_manifest(
        dataset,
        publisher_url=manifest.publisher_url,
        published_at=manifest.published_at,
    )
    persisted = {
        "row_count": manifest.row_count,
        "serving_sha256": manifest.serving_sha256,
        "generation_id": manifest.generation_id,
    }
    exact = {
        "row_count": recomputed.row_count,
        "serving_sha256": recomputed.serving_sha256,
        "generation_id": recomputed.generation_id,
    }
    for field, value in exact.items():
        if persisted[field] != value:
            raise IcdoCertificationError(f"{field} drift")
    observed: dict[str, object] = {
        "source_sha256": manifest.source_sha256,
        "edition": manifest.edition,
        "axis": manifest.axis,
        "row_count": len(dataset.records),
        "serving_sha256": canonical_sha256(dataset),
    }
    for field, value in expected.model_dump().items():
        if observed[field] != value:
            raise IcdoCertificationError(f"{field} drift")
    return manifest


def canonical_sha256(dataset: CanonicalDataset) -> str:
    return hashlib.sha256(canonical_bytes(dataset)).hexdigest()


def dataset_manifest(
    dataset: CanonicalDataset, *, publisher_url: str, published_at: datetime
) -> IcdoManifest:
    serving = canonical_sha256(dataset)
    identity_payload = json.dumps(
        {
            "edition": dataset.edition,
            "axis": dataset.axis,
            "publisher_url": publisher_url,
            "source_sha256": dataset.source_sha256,
            "archive_sha256": dataset.archive_sha256,
            "annex_sha256": dataset.annex_sha256,
            "reader_identity": "ontolib.icdo/1;xlrd=2.0.2;openpyxl=3.1.5",
            "serving_sha256": serving,
            "row_count": len(dataset.records),
            "term_counts": dataset.term_counts,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return IcdoManifest(
        generation_id=hashlib.sha256(identity_payload).hexdigest(),
        edition=dataset.edition,
        axis=dataset.axis,
        publisher_url=publisher_url,
        source_sha256=dataset.source_sha256,
        archive_sha256=dataset.archive_sha256,
        annex_sha256=dataset.annex_sha256,
        reader_identity="ontolib.icdo/1;xlrd=2.0.2;openpyxl=3.1.5",
        serving_sha256=serving,
        row_count=len(dataset.records),
        term_counts=dataset.term_counts,
        published_at=published_at,
    )


class IcdoRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def metadata(self, edition: str, axis: str) -> IcdoManifest | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT g.manifest FROM icdo_active_generation a "
                        "JOIN icdo_generation g ON g.id=a.generation_id "
                        "WHERE a.edition=:edition AND a.axis=:axis"
                    ),
                    {"edition": edition, "axis": axis},
                )
            ).scalar_one_or_none()
        return (
            IcdoManifest.model_validate_json(json.dumps(row))
            if row is not None
            else None
        )

    async def _generation_dataset(
        self, edition: str, axis: str, generation_id: str | None = None
    ) -> tuple[IcdoManifest, CanonicalDataset] | None:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT g.manifest, r.payload FROM icdo_generation g "
                        "JOIN icdo_record r ON r.edition=g.edition AND r.axis=g.axis "
                        "AND r.generation_id=g.id "
                        "WHERE g.edition=:edition AND g.axis=:axis AND g.id=COALESCE("
                        ":generation, (SELECT generation_id FROM "
                        "icdo_active_generation "
                        "WHERE edition=:edition AND axis=:axis)) ORDER BY r.code"
                    ),
                    {"edition": edition, "axis": axis, "generation": generation_id},
                )
            ).all()
        if not rows:
            return None
        manifest = IcdoManifest.model_validate_json(json.dumps(rows[0][0]))
        dataset = CanonicalDataset(
            edition=manifest.edition,
            axis=manifest.axis,
            records=tuple(
                IcdoRecord.model_validate_json(json.dumps(payload))
                for _, payload in rows
            ),
            source_shape=SourceShape(
                sheet_names=(), headers=(), merged_ranges=(), trailing_blank_rows=0
            ),
            source_sha256=manifest.source_sha256,
            archive_sha256=manifest.archive_sha256,
            annex_sha256=manifest.annex_sha256,
        )
        return manifest, dataset

    async def dataset(
        self, edition: str, axis: str, generation_id: str | None = None
    ) -> CanonicalDataset | None:
        bound = await self._generation_dataset(edition, axis, generation_id)
        return bound[1] if bound is not None else None

    async def certified_metadata(
        self, edition: str, axis: str, expected: CertificationExpectation
    ) -> IcdoManifest | None:
        bound = await self._generation_dataset(edition, axis)
        if bound is None:
            return None
        manifest, dataset = bound
        return certify_dataset(manifest, dataset, expected)

    async def search(
        self,
        edition: str,
        axis: str,
        *,
        query: str,
        limit: int,
        offset: int,
        behaviour: str | None = None,
        level: str | None = None,
        generation_id: str | None = None,
    ) -> dict[str, object]:
        pattern = f"%{query.lower()}%"
        params: dict[str, object] = {
            "edition": edition,
            "axis": axis,
            "pattern": pattern,
            "limit": limit,
            "offset": offset,
            "has_query": bool(query),
            "behaviour": behaviour,
            "level": level,
            "generation": generation_id,
        }
        joins = " FROM icdo_record r "
        where = (
            "WHERE r.edition=:edition AND r.axis=:axis "
            "AND r.generation_id=COALESCE(:generation, (SELECT generation_id "
            "FROM icdo_active_generation WHERE edition=:edition AND axis=:axis)) "
            "AND (NOT :has_query OR lower(r.payload->>'code') LIKE :pattern OR "
            "lower(concat_ws(' ', r.payload->>'preferred', r.payload->'synonyms', "
            "r.payload->'related')) LIKE :pattern) "
            "AND (CAST(:behaviour AS text) IS NULL OR "
            "r.payload->>'behaviour'=:behaviour) "
            "AND (CAST(:level AS text) IS NULL OR r.payload->>'level'=:level)"
        )
        async with self._sessions() as session:
            total = (
                await session.execute(
                    text("SELECT count(*)" + joins + where),
                    params,
                )
            ).scalar_one()
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT r.payload"
                            + joins
                            + where
                            + " ORDER BY r.code LIMIT :limit OFFSET :offset"
                        ),
                        params,
                    )
                )
                .scalars()
                .all()
            )
        return {
            "edition": edition,
            "axis": axis,
            "query": query,
            "total": total,
            "limit": limit,
            "offset": offset,
            "hits": [
                IcdoRecord.model_validate_json(json.dumps(row)).model_dump(mode="json")
                for row in rows
            ],
        }

    async def detail(
        self, edition: str, axis: str, code: str, generation_id: str | None = None
    ) -> dict[str, object] | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT r.payload FROM icdo_record r WHERE r.edition=:edition "
                        "AND r.axis=:axis AND r.generation_id=COALESCE(:generation, "
                        "(SELECT generation_id FROM icdo_active_generation WHERE "
                        "edition=:edition AND axis=:axis)) AND r.code=:code"
                    ),
                    {
                        "edition": edition,
                        "axis": axis,
                        "code": code,
                        "generation": generation_id,
                    },
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return IcdoRecord.model_validate_json(json.dumps(row)).model_dump(mode="json")

    async def resolve_active_morphology32_codes(
        self, codes: set[str], expected: CertificationExpectation
    ) -> IcdoCodeResolution:
        """Resolve a code set against exactly one active ICD-O-3.2 generation."""
        bound = await self._generation_dataset("3.2", "morphology")
        if bound is None:
            raise IcdoRepositoryUnavailableError(
                "active ICD-O-3.2 morphology generation is unavailable"
            )
        manifest, dataset = bound
        certify_dataset(manifest, dataset, expected)
        return IcdoCodeResolution(
            generation_id=manifest.generation_id,
            serving_sha256=manifest.serving_sha256,
            resolved_codes={row.code for row in dataset.records if row.code in codes},
        )


async def publish_dataset(
    sessions: async_sessionmaker[AsyncSession],
    dataset: CanonicalDataset,
    *,
    publisher_url: str,
    published_at: datetime,
) -> IcdoManifest:
    manifest = dataset_manifest(
        dataset, publisher_url=publisher_url, published_at=published_at
    )
    async with sessions.begin() as session:
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('icdo:' || :edition || ':' || :axis))"
            ),
            {"edition": dataset.edition, "axis": dataset.axis},
        )
        await session.execute(
            text(
                "INSERT INTO icdo_generation (id, edition, axis, manifest) "
                "VALUES (:id,:edition,:axis,:manifest) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": manifest.generation_id,
                "edition": dataset.edition,
                "axis": dataset.axis,
                "manifest": manifest.model_dump_json(),
            },
        )
        for record in dataset.records:
            payload = record.model_dump(mode="json")
            search_text = " ".join(
                filter(None, [record.preferred, *record.synonyms, *record.related])
            )
            await session.execute(
                text(
                    "INSERT INTO icdo_record "
                    "(edition, axis, generation_id, code, level, behaviour, "
                    "search_text, payload) VALUES "
                    "(:edition,:axis,:generation,:code,:level,:behaviour,"
                    ":search,:payload) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "generation": manifest.generation_id,
                    "edition": dataset.edition,
                    "axis": dataset.axis,
                    "code": record.code,
                    "level": record.level,
                    "behaviour": record.behaviour,
                    "search": search_text,
                    "payload": json.dumps(payload),
                },
            )
        await session.execute(
            text(
                "INSERT INTO icdo_active_generation "
                "(edition,axis,generation_id,activated_at) "
                "VALUES (:edition,:axis,:generation,:activated) "
                "ON CONFLICT (edition,axis) DO UPDATE SET "
                "generation_id=EXCLUDED.generation_id, "
                "activated_at=EXCLUDED.activated_at"
            ),
            {
                "edition": dataset.edition,
                "axis": dataset.axis,
                "generation": manifest.generation_id,
                "activated": published_at,
            },
        )
    return manifest
