"""Postgres persistence for xref runs and SSSOM mapping records."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import Result, text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ontolib.repositories.xref.models import SSSOMRecord


class XrefStore:
    """Persistence for xref run manifests and concept_xref mapping rows."""

    def __init__(self, sf: async_sessionmaker[AsyncSession]) -> None:
        self._sf = sf

    async def upsert_run(
        self,
        run_id: str,
        source: str,
        ncit_version: str,
        source_version: str,
        status: str = "running",
    ) -> int:
        now = datetime.datetime.now(datetime.UTC)
        async with self._sf() as s:
            result: Result = await s.execute(
                text(
                    "INSERT INTO xref_run "
                    "(id, source, status, ncit_version, source_version, started_at) "
                    "VALUES (:id, :source, :status, :ncit_version, "
                    ":source_version, :started_at) "
                    "ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status"
                ),
                {
                    "id": run_id,
                    "source": source,
                    "status": status,
                    "ncit_version": ncit_version,
                    "source_version": source_version,
                    "started_at": now,
                },
            )
            await s.commit()
            return cast("int", result.rowcount)  # type: ignore[attr-defined]

    async def upsert_records(self, run_id: str, records: list[SSSOMRecord]) -> int:
        if not records:
            return 0
        rows = [
            {
                "run_id": run_id,
                "subject_id": r.subject_id,
                "predicate_id": r.predicate_id,
                "object_id": r.object_id,
                "mapping_justification": r.mapping_justification,
                "confidence": r.confidence,
                "subject_source_version": r.subject_source_version,
                "object_source_version": r.object_source_version,
                "lifecycle_state": r.lifecycle_state,
                "review_status": r.review_status,
                "author": r.author,
            }
            for r in records
        ]
        async with self._sf() as s:
            result: Result = await s.execute(
                text(
                    "INSERT INTO concept_xref "
                    "(run_id, subject_id, predicate_id, object_id, "
                    "mapping_justification, "
                    "confidence, subject_source_version, object_source_version, "
                    "lifecycle_state, review_status, author) "
                    "VALUES (:run_id, :subject_id, :predicate_id, :object_id, "
                    ":mapping_justification, :confidence, :subject_source_version, "
                    ":object_source_version, :lifecycle_state, "
                    ":review_status, :author) "
                    "ON CONFLICT (run_id, subject_id, predicate_id, object_id) "
                    "DO NOTHING"
                ),
                rows,
            )
            await s.commit()
            if result.rowcount and result.rowcount >= 0:  # type: ignore[attr-defined]
                return cast("int", result.rowcount)  # type: ignore[attr-defined]
            return len(rows)

    async def records_for_run(self, run_id: str) -> list[dict]:
        async with self._sf() as s:
            result = await s.execute(
                text(
                    "SELECT subject_id, predicate_id, object_id, confidence "
                    "FROM concept_xref WHERE run_id = :run_id ORDER BY subject_id"
                ),
                {"run_id": run_id},
            )
            return [dict(row) for row in result.mappings().all()]
