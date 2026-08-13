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
        return IcdoManifest.model_validate(row) if row is not None else None

    async def dataset(self, edition: str, axis: str) -> CanonicalDataset | None:
        manifest = await self.metadata(edition, axis)
        if manifest is None:
            return None
        async with self._sessions() as session:
            payloads = (
                (
                    await session.execute(
                        text(
                            "SELECT r.payload FROM icdo_active_generation a "
                            "JOIN icdo_record r ON r.generation_id=a.generation_id "
                            "WHERE a.edition=:edition AND a.axis=:axis ORDER BY r.code"
                        ),
                        {"edition": edition, "axis": axis},
                    )
                )
                .scalars()
                .all()
            )
        return CanonicalDataset(
            edition=manifest.edition,
            axis=manifest.axis,
            records=tuple(IcdoRecord.model_validate(payload) for payload in payloads),
            source_shape=SourceShape(
                sheet_names=(), headers=(), merged_ranges=(), trailing_blank_rows=0
            ),
            source_sha256=manifest.source_sha256,
            archive_sha256=manifest.archive_sha256,
            annex_sha256=manifest.annex_sha256,
        )

    async def certified_metadata(
        self, edition: str, axis: str, expected: CertificationExpectation
    ) -> IcdoManifest | None:
        manifest = await self.metadata(edition, axis)
        dataset = await self.dataset(edition, axis)
        if manifest is None or dataset is None:
            return None
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
        }
        joins = (
            " FROM icdo_active_generation a JOIN icdo_record r "
            "ON r.generation_id=a.generation_id "
        )
        where = (
            "WHERE a.edition=:edition AND a.axis=:axis "
            "AND (NOT :has_query OR lower(r.code) LIKE :pattern "
            "OR lower(r.search_text) LIKE :pattern) "
            "AND (:behaviour IS NULL OR r.behaviour=:behaviour) "
            "AND (:level IS NULL OR r.level=:level)"
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
            "hits": rows,
        }

    async def detail(
        self, edition: str, axis: str, code: str
    ) -> dict[str, object] | None:
        async with self._sessions() as session:
            return (
                await session.execute(
                    text(
                        "SELECT r.payload FROM icdo_active_generation a "
                        "JOIN icdo_record r ON r.generation_id=a.generation_id "
                        "WHERE a.edition=:edition AND a.axis=:axis AND r.code=:code"
                    ),
                    {"edition": edition, "axis": axis, "code": code},
                )
            ).scalar_one_or_none()

    async def resolve_active_morphology32_codes(
        self, codes: set[str]
    ) -> IcdoCodeResolution:
        """Resolve a code set against exactly one active ICD-O-3.2 generation."""
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT a.generation_id, g.manifest->>'serving_sha256' "
                            "AS serving_sha256, r.code FROM icdo_active_generation a "
                            "JOIN icdo_generation g ON g.id=a.generation_id "
                            "LEFT JOIN icdo_record r ON "
                            "r.generation_id=a.generation_id "
                            "AND r.code=ANY(:codes) WHERE a.edition='3.2' "
                            "AND a.axis='morphology' ORDER BY r.code"
                        ),
                        {"codes": sorted(codes)},
                    )
                )
                .mappings()
                .all()
            )
        if not rows:
            raise IcdoRepositoryUnavailableError(
                "active ICD-O-3.2 morphology generation is unavailable"
            )
        generation_id = rows[0]["generation_id"]
        serving_sha256 = rows[0]["serving_sha256"]
        if not isinstance(generation_id, str) or not isinstance(serving_sha256, str):
            raise IcdoRepositoryUnavailableError(
                "active ICD-O-3.2 morphology generation identity is invalid"
            )
        return IcdoCodeResolution(
            generation_id=generation_id,
            serving_sha256=serving_sha256,
            resolved_codes={
                str(row["code"]) for row in rows if row["code"] is not None
            },
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
                    "(generation_id, code, level, behaviour, search_text, payload) "
                    "VALUES (:generation,:code,:level,:behaviour,:search,:payload) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "generation": manifest.generation_id,
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
