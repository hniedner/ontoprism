"""Postgres persistence for xref runs and SSSOM mapping records."""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Result, text

from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
            if result.rowcount is not None:  # type: ignore[attr-defined]
                if (
                    result.rowcount >= 0  # type: ignore[attr-defined]
                ):  # pragma: no cover
                    return cast("int", result.rowcount)  # type: ignore[attr-defined]  # pragma: no cover
                return len(rows)
            return 0  # pragma: no cover

    async def update_run_metrics(
        self, run_id: str, metrics: dict[str, Any], *, status: str = "completed"
    ) -> None:
        """Set ``finished_at``, *status*, and *metrics* on a run.

        *status* is a parameter, not a constant: a run whose reasoner never ran must not
        be recorded as ``completed`` just because it exited without an exception.

        ``metrics`` is a ``jsonb`` column and this is raw SQL, so the dict is
        serialized and cast explicitly — asyncpg will not adapt a bare dict.
        """
        now = datetime.datetime.now(datetime.UTC)
        async with self._sf() as s:
            await s.execute(
                text(
                    "UPDATE xref_run SET "
                    "  finished_at = :now, status = :status, "
                    "  metrics = CAST(:metrics AS jsonb) "
                    "WHERE id = :run_id"
                ),
                {
                    "run_id": run_id,
                    "now": now,
                    "status": status,
                    "metrics": json.dumps(metrics),
                },
            )
            await s.commit()

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

    async def mapping_strength_by_subject(self) -> dict[str, set[tuple[str, str]]]:
        """Return all ``(predicate_id, lifecycle_state)`` per subject across every run.

        Because rows from multiple runs coalesce in the same set, callers
        should be aware that the same ``(subject, predicate)`` may appear
        with different lifecycle states (e.g. ``proposed`` in one run and
        ``validated`` in another). The latest run's state is **not** applied
        here — the downstream ``build_coverage_report`` treats *any*
        ``exactMatch + {validated, active}`` as identity-grade.  This is
        correct for Phase A where ingest produces ``closeMatch/proposed``
        and validation (#73) promotes to ``exactMatch/validated``;
        cross-run conflicts are resolved by dataset design, not by query.
        """
        sql = text("SELECT subject_id, predicate_id, lifecycle_state FROM concept_xref")
        async with self._sf() as s:
            result = await s.execute(sql)
            out: dict[str, set[tuple[str, str]]] = {}
            for r in result.mappings().all():
                key = r["subject_id"]
                pair = (r["predicate_id"], r["lifecycle_state"])
                out.setdefault(key, set()).add(pair)
            return out

    async def proposed_candidates(self) -> list[SSSOMRecord]:
        """Every candidate awaiting validation (#73): ``closeMatch`` + ``proposed``.

        The predicate filter is load-bearing, not defensive tidiness: promotion rewrites
        the predicate to ``exactMatch``, so a proposed ``narrowMatch`` (a curator saying
        "the object is *narrower* than the subject" — the golden set has exactly such
        rows) would be silently upgraded to identity-grade equivalence.  Only a
        ``closeMatch`` is a candidate for identity.

        ``DISTINCT`` collapses a byte-identical re-ingest (the same pair, same versions,
        in a later run) — without it each duplicate is re-validated (two JVM launches
        apiece) and inflates the counts that land in ``xref_run.metrics``.  Note it does
        **not** collapse a re-ingest after a version bump: those rows differ in
        ``*_source_version``, so the pair legitimately returns as a fresh candidate that
        must be re-validated against the new release (D29).
        """
        sql = text(
            "SELECT DISTINCT subject_id, predicate_id, object_id, "
            "mapping_justification, confidence, subject_source_version, "
            "object_source_version, lifecycle_state, review_status, author "
            "FROM concept_xref "
            "WHERE lifecycle_state = 'proposed' AND predicate_id = :close "
            "ORDER BY subject_id, object_id"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"close": CLOSE_MATCH})
            return [SSSOMRecord(**dict(row)) for row in result.mappings().all()]

    async def validated_anchors(self) -> tuple[tuple[str, str], ...]:
        """Identity-grade bridges already validated — the trusted anchors for #73.

        Only ``exactMatch`` in a ``validated``/``active`` lifecycle counts: a proposed
        ``closeMatch`` is a candidate, never an anchor another candidate leans on.
        """
        sql = text(
            "SELECT DISTINCT subject_id, object_id FROM concept_xref "
            "WHERE predicate_id = :exact "
            "AND lifecycle_state IN ('validated', 'active') "
            "ORDER BY subject_id, object_id"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"exact": EXACT_MATCH})
            return tuple(
                (r["subject_id"], r["object_id"]) for r in result.mappings().all()
            )

    async def stale_anchors(
        self, *, ncit_version: str, source_version: str, source: str
    ) -> set[tuple[str, str]]:
        """Validated bridges the current endpoint versions have already made stale.

        These still corroborate (sweeping before promotion would leave a release with no
        anchors at all and collapse coverage), but they must NOT *claim* their endpoints
        against a replacement: an upstream release that obsoletes U1 in favour of U2
        would otherwise see the stale (C, U1) block the correct new (C, U2) as a
        "conflicting identity", and then quarantine (C, U1) moments later — leaving C
        with no bridge at all, and blaming a row the same run invalidated.
        """
        sql = text(
            "SELECT DISTINCT subject_id, object_id FROM concept_xref "
            "WHERE lifecycle_state = 'validated' "
            "AND run_id IN (SELECT id FROM xref_run WHERE source = :source) "
            "AND (subject_source_version <> :ncit_version "
            "     OR object_source_version <> :source_version)"
        )
        async with self._sf() as s:
            result = await s.execute(
                sql,
                {
                    "ncit_version": ncit_version,
                    "source_version": source_version,
                    "source": source,
                },
            )
            return {(r["subject_id"], r["object_id"]) for r in result.mappings().all()}

    async def count_stale(
        self, *, ncit_version: str, source_version: str, source: str
    ) -> int:
        """How many validated bridges the current endpoint versions have made stale.

        Read-only twin of :meth:`quarantine_stale`. A run that *cannot* sweep (because
        its reasoner failed) must still be able to say "N bridges are stale and I could
        not act on them — the coverage number is currently unreliable", instead of
        leaving that fact in a log line the pipeline swallows.
        """
        sql = text(
            "SELECT count(*) FROM concept_xref "
            "WHERE lifecycle_state = 'validated' "
            "AND run_id IN (SELECT id FROM xref_run WHERE source = :source) "
            "AND (subject_source_version <> :ncit_version "
            "     OR object_source_version <> :source_version)"
        )
        async with self._sf() as s:
            result = await s.execute(
                sql,
                {
                    "ncit_version": ncit_version,
                    "source_version": source_version,
                    "source": source,
                },
            )
            return int(result.scalar_one())

    async def quarantine_stale(
        self, *, ncit_version: str, source_version: str, source: str
    ) -> int:
        """Quarantine validated bridges whose endpoint versions have moved on (D29).

        An endpoint release bumps the version fields; a bridge validated against an
        older release is no longer *known* good, so it is quarantined until validation
        re-runs over it.

        Precisely what "quarantined" buys today: the bridge stops counting toward the
        published coverage number (``mapping_strength_by_subject`` -> ``_is_identity``
        requires ``validated``/``active``) and stops acting as a trusted anchor
        (``validated_anchors``).  It is **not** withheld from the read path —
        ``mappings_by_subjects`` applies no lifecycle filter, so ``/concept/{id}``
        still surfaces it, tagged with its lifecycle, and the client decides.  Do not
        read this as "quarantined bridges are not served".

        Scoped to *source*: an ``object_source_version`` is only comparable within its
        own upstream. Sweeping unscoped would quarantine every Mondo bridge on a Uberon
        release, because a Mondo version can never equal a Uberon one.
        """
        sql = text(
            "UPDATE concept_xref SET lifecycle_state = 'quarantined' "
            "WHERE lifecycle_state = 'validated' "
            "AND run_id IN (SELECT id FROM xref_run WHERE source = :source) "
            "AND (subject_source_version <> :ncit_version "
            "     OR object_source_version <> :source_version)"
        )
        async with self._sf() as s:
            result: Result = await s.execute(
                sql,
                {
                    "ncit_version": ncit_version,
                    "source_version": source_version,
                    "source": source,
                },
            )
            await s.commit()
            return cast("int", result.rowcount)  # type: ignore[attr-defined]

    async def mappings_by_subjects(
        self, codes: set[str]
    ) -> dict[str, list[tuple[str, str, str]]]:
        if not codes:
            return {}
        sql = text(
            "SELECT subject_id, object_id, predicate_id, lifecycle_state "
            "FROM concept_xref WHERE subject_id = ANY(:codes)"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"codes": list(codes)})
            out: dict[str, list[tuple[str, str, str]]] = {}
            for r in result.mappings().all():
                out.setdefault(r["subject_id"], []).append(
                    (r["object_id"], r["predicate_id"], r["lifecycle_state"])
                )
            return out
