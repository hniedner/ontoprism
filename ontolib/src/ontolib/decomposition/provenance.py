"""Provenance persistence for decomposition runs (design section 4.5)."""

from __future__ import annotations

import datetime
import json as _json
import logging
from typing import TYPE_CHECKING, cast

from sqlalchemy import Result, text

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ontolib.decomposition.models import Constituent

from ontolib.decomposition.provenance_models import MintedConcept, RunSummary

_logger = logging.getLogger(__name__)


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

    async def processed_codes(self, run_id: str) -> set[str]:
        """Concept codes that already have persisted constituents for *run_id*.

        Used by ``--resume`` to skip re-processing (design §9). A concept that
        decomposed to zero constituents leaves no row here, so it is reprocessed on
        resume — safe (idempotent upserts) but not exhaustive; see run.py.
        """
        async with self._sf() as s:
            result = await s.execute(
                text(
                    "SELECT DISTINCT concept_code FROM decomp_constituent "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            return set(result.scalars().all())

    async def run_version(self, run_id: str) -> str | None:
        """The ``ncit_version`` pinned for *run_id*, or ``None`` if it has no manifest.

        Lets ``--resume`` refuse to continue a run against a different NCIt build
        than the one it started with (design §9/§13 — roles are version-pinned).
        """
        async with self._sf() as s:
            result = await s.execute(
                text("SELECT ncit_version FROM decomp_run WHERE id = :run_id"),
                {"run_id": run_id},
            )
            return result.scalar()

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        sql = text(
            "SELECT id, branch, status, ncit_version, started_at, finished_at, metrics "
            "FROM decomp_run ORDER BY started_at DESC LIMIT :limit OFFSET :offset"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"limit": limit, "offset": offset})
            return [self._row_to_run(r) for r in result.mappings().all()]

    async def get_run(self, run_id: str) -> RunSummary | None:
        sql = text(
            "SELECT id, branch, status, ncit_version, started_at, finished_at, metrics "
            "FROM decomp_run WHERE id = :run_id"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"run_id": run_id})
            row = result.mappings().first()
            return self._row_to_run(row) if row is not None else None

    async def list_minted_concepts(
        self,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[MintedConcept]:
        sql = text(
            "SELECT id, run_id, axis, label, source_signal, status FROM minted_concept "
            "WHERE (:run_id IS NULL OR run_id = :run_id) "
            "AND (:status IS NULL OR status = :status) "
            "ORDER BY id LIMIT :limit OFFSET :offset"
        )
        async with self._sf() as s:
            result = await s.execute(
                sql,
                {
                    "run_id": run_id,
                    "status": status,
                    "limit": limit,
                    "offset": offset,
                },
            )
            return [MintedConcept(**dict(r)) for r in result.mappings().all()]

    @staticmethod
    def _row_to_run(row: RowMapping) -> RunSummary:
        raw = row["metrics"]
        if isinstance(raw, str):
            try:
                m = _json.loads(raw)
            except _json.JSONDecodeError:
                _logger.warning(
                    "Corrupt metrics JSON in decomp_run %s", row.get("id", "?")
                )
                m = {}
        else:
            m = raw or {}
        return RunSummary(
            id=row["id"],
            branch=row["branch"],
            status=row["status"],
            ncit_version=row["ncit_version"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            total_in_scope=m.get("total_in_scope"),
            decomposed=m.get("decomposed"),
            residual=m.get("residual"),
            minted_count=m.get("minted_count"),
            pct_decomposed=m.get("pct_decomposed"),
            roundtrip_fidelity=m.get("roundtrip_fidelity"),
        )

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
        """Record a minted concept proposal. Returns rows affected.

        Insert-or-ignore, deliberately never insert-or-*update*: a rerun re-mints the
        same deterministic id (``minting.MintedConcept``) with ``status="proposed"``
        by default, and the engine must never clobber a curator's prior
        approve/reject decision on that row. Approval is a governance step outside
        the engine (design §7.2) — it updates the row directly, not via this method.
        """
        result: Result
        async with self._sf() as s:
            result = await s.execute(
                text(
                    "INSERT INTO minted_concept (id, run_id, axis, "
                    "label, source_signal, status) "
                    "VALUES (:id, :run_id, :axis, :label, :source_signal, :status) "
                    "ON CONFLICT (id) DO NOTHING"
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
