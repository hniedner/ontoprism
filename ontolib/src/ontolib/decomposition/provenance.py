"""Provenance persistence for decomposition runs (design section 4.5)."""

from __future__ import annotations

import datetime
import json as _json
from typing import TYPE_CHECKING, cast

from sqlalchemy import Result, text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ontolib.decomposition.models import Constituent


class ProvenanceStore:
    """Persistence for decomposition run manifests and constituents."""

    def __init__(self, sf: async_sessionmaker[AsyncSession]) -> None:
        self._sf = sf

    async def upsert_run(
        self,
        run_id: str,
        branch: str,
        ncit_version: str,
        status: str = "running",
    ) -> int:
        """Create or update a decomp_run row. Returns rows affected."""
        now = datetime.datetime.now(datetime.UTC)
        result: Result
        async with self._sf() as s:
            result = await s.execute(
                text(
                    "INSERT INTO decomp_run "
                    "(id, branch, status, ncit_version, started_at) "
                    "VALUES (:id, :branch, :status, :ncit_version, :started_at) "
                    "ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status"
                ),
                {
                    "id": run_id,
                    "branch": branch,
                    "status": status,
                    "ncit_version": ncit_version,
                    "started_at": now,
                },
            )
            await s.commit()
            return cast("int", result.rowcount)  # type: ignore[attr-defined]

    async def upsert_constituents(
        self,
        run_id: str,
        concept_code: str,
        constituents: list[Constituent],
    ) -> int:
        """Batch-upsert constituents. Returns count persisted."""
        if not constituents:
            return 0
        rows = [
            {
                "run_id": run_id,
                "concept_code": concept_code,
                "axis": c.axis,
                "filler_code": c.filler_code,
                "axis_source": c.axis_source,
                "most_specific": c.most_specific,
            }
            for c in constituents
        ]
        result: Result
        async with self._sf() as s:
            result = await s.execute(
                text(
                    "INSERT INTO decomp_constituent "
                    "(run_id, concept_code, axis, filler_code, "
                    "axis_source, most_specific) "
                    "VALUES (:run_id, :concept_code, :axis, :filler_code, "
                    ":axis_source, :most_specific) "
                    "ON CONFLICT (run_id, concept_code, axis, filler_code) DO NOTHING"
                ),
                rows,
            )
            await s.commit()
            return cast("int", result.rowcount)  # type: ignore[attr-defined]

    async def finish_run(
        self,
        run_id: str,
        metrics: dict | None = None,
    ) -> bool:
        """Mark a run complete. Returns True if updated."""
        result: Result
        async with self._sf() as s:
            now = datetime.datetime.now(datetime.UTC)
            result = await s.execute(
                text(
                    "UPDATE decomp_run SET status='complete', "
                    "finished_at=:finished_at, metrics=:metrics WHERE id=:id"
                ),
                {
                    "id": run_id,
                    "finished_at": now,
                    "metrics": _json.dumps(metrics) if metrics else None,
                },
            )
            await s.commit()
            return bool(cast("int", result.rowcount))  # type: ignore[attr-defined]

    async def upsert_minted_concept(
        self,
        run_id: str,
        id: str = "",
        axis: str = "",
        label: str = "",
        source_signal: str = "",
        status: str = "proposed",
    ) -> int:
        """Record a minted concept proposal. Returns rows affected."""
        result: Result
        async with self._sf() as s:
            result = await s.execute(
                text(
                    "INSERT INTO minted_concept (id, run_id, axis, "
                    "label, source_signal, status) "
                    "VALUES (:id, :run_id, :axis, :label, :source_signal, :status) "
                    "ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status"
                ),
                {
                    "id": id,
                    "run_id": run_id,
                    "axis": axis,
                    "label": label,
                    "source_signal": source_signal,
                    "status": status,
                },
            )
            await s.commit()
            return cast("int", result.rowcount)  # type: ignore[attr-defined]
