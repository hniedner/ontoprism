"""Postgres persistence for xref runs and SSSOM mapping records."""

from __future__ import annotations

import datetime
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Result, text

from ontolib.repositories.xref.models import (
    EndpointIdentity,
    GenerationSourceMetadata,
    MappingResult,
    SSSOMRecord,
    StaleXrefGenerationError,
)
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ontolib.repositories.xref.evidence import EvidenceDict


_CANDIDATE_SOURCE = "uberon-cl"
_PUBLISHER_SOURCE = "uberon-publisher-xref"
_PROMOTION_SOURCE = "uberon-cl-promotion"
_P334_SOURCE = "ncit-p334-icdo32"
_SOURCE_REQUIRED_IDENTITIES: dict[str, tuple[str, ...] | None] = {
    _CANDIDATE_SOURCE: ("ncit_source_identity", "uberon_source_identity"),
    _PUBLISHER_SOURCE: (
        "ncit_source_identity",
        "uberon_source_identity",
        "uberon_serving_identity",
    ),
    _P334_SOURCE: (
        "ncit_source_identity",
        "icdo_generation_identity",
        "icdo_serving_identity",
    ),
    _PROMOTION_SOURCE: None,
}


def _validate_generation_retry(
    found: Any,
    *,
    content_sha256: str,
    source_metadata: GenerationSourceMetadata,
) -> None:
    if found["content_sha256"] != content_sha256:
        raise ValueError("generation identity has different content")
    observed_metadata = GenerationSourceMetadata.model_validate(
        found["source_metadata"]
    )
    if observed_metadata != source_metadata:
        raise ValueError("generation identity has different source metadata")


def _required_source_identities(
    source: str,
    observed: GenerationSourceMetadata,
    expected: GenerationSourceMetadata,
) -> tuple[str, ...] | None:
    if source not in _SOURCE_REQUIRED_IDENTITIES:
        if observed != expected:
            raise StaleXrefGenerationError(
                f"unknown active xref source {source!r} lacks an exact contract"
            )
        return ()
    required = _SOURCE_REQUIRED_IDENTITIES[source]
    if required is None:
        return tuple(observed.model_dump(exclude_none=True))
    expected_values = expected.model_dump(exclude_none=True)
    source_specific = set(required) - {"ncit_source_identity"}
    if source_specific.isdisjoint(expected_values):
        return None
    return required


def _validate_source_contract(
    source: str,
    observed: GenerationSourceMetadata,
    expected: GenerationSourceMetadata,
    required: tuple[str, ...],
) -> None:
    observed_values = observed.model_dump(exclude_none=True)
    expected_values = expected.model_dump(exclude_none=True)
    for key in required:
        if observed_values.get(key) != expected_values.get(key):
            raise StaleXrefGenerationError(
                f"active xref generation {source!r} has stale {key}"
            )


class XrefStore:
    """Persistence for xref run manifests and concept_xref mapping rows."""

    def __init__(self, sf: async_sessionmaker[AsyncSession]) -> None:
        self._sf = sf

    @asynccontextmanager
    async def publication_lock(self, source: str) -> AsyncIterator[None]:
        """Serialize the complete PostgreSQL/RDF publication for one source."""
        async with self._sf() as session:
            await session.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
                {"key": f"xref:{source}"},
            )
            try:
                yield
            finally:
                await session.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": f"xref:{source}"},
                )

    async def prepare_generation(
        self,
        *,
        source: str,
        generation_id: str,
        content_sha256: str,
        source_metadata: GenerationSourceMetadata,
        graph_iri: str,
        run_id: str,
        records: Sequence[SSSOMRecord],
        _publication_locked: bool = False,
    ) -> bool:
        """Persist one immutable generation; an exact retry is a no-op."""
        rows = [
            {
                "generation_id": generation_id,
                "generation_source": source,
                "run_id": run_id,
                "subject_system": r.subject.system,
                "subject_version": r.subject.version,
                "subject_id": r.subject.identifier,
                "predicate_id": r.predicate_id,
                "object_system": r.object.system,
                "object_version": r.object.version,
                "object_id": r.object.identifier,
                "mapping_justification": r.mapping_justification,
                "confidence": r.confidence,
                "lifecycle_state": r.lifecycle_state,
                "review_status": r.review_status,
                "author": r.author,
                "evidence": json.dumps([e.as_dict() for e in r.evidence]),
            }
            for r in records
        ]
        async with self._sf() as s:
            if not _publication_locked:
                await s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"xref:{source}"},
                )
            existing = await s.execute(
                text(
                    "SELECT id, content_sha256, source_metadata FROM xref_generation "
                    "WHERE id = :id AND source = :source"
                ),
                {"id": generation_id, "source": source},
            )
            found = existing.mappings().one_or_none()
            if found is not None:
                _validate_generation_retry(
                    found,
                    content_sha256=content_sha256,
                    source_metadata=source_metadata,
                )
                await s.commit()
                return False
            await s.execute(
                text(
                    "INSERT INTO xref_generation "
                    "(id, source, content_sha256, source_metadata, graph_iri, state) "
                    "VALUES (:id, :source, :content, CAST(:metadata AS jsonb), "
                    ":graph, 'prepared')"
                ),
                {
                    "id": generation_id,
                    "source": source,
                    "content": content_sha256,
                    "metadata": source_metadata.model_dump_json(exclude_none=True),
                    "graph": graph_iri,
                },
            )
            if rows:
                await s.execute(
                    text(
                        "INSERT INTO concept_xref "
                        "(generation_id, generation_source, run_id, subject_system, "
                        "subject_version, "
                        "subject_id, predicate_id, object_system, object_version, "
                        "object_id, mapping_justification, confidence, "
                        "lifecycle_state, review_status, author, evidence) VALUES "
                        "(:generation_id, :generation_source, :run_id, "
                        ":subject_system, :subject_version, "
                        ":subject_id, :predicate_id, :object_system, :object_version, "
                        ":object_id, :mapping_justification, :confidence, "
                        ":lifecycle_state, :review_status, :author, "
                        "CAST(:evidence AS jsonb))"
                    ),
                    rows,
                )
            await s.commit()
            return True

    async def active_generation(self, source: str) -> str | None:
        """Return the exact PostgreSQL active generation for one source."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT generation_id FROM xref_active_generation "
                    "WHERE source = :source"
                ),
                {"source": source},
            )
            value = result.scalar_one_or_none()
            return str(value) if value is not None else None

    async def activate_generation(
        self,
        source: str,
        generation_id: str,
        *,
        _publication_locked: bool = False,
    ) -> bool:
        """Atomically switch one source pointer after its RDF graph is materialized."""
        async with self._sf() as s:
            if not _publication_locked:
                await s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"xref:{source}"},
                )
            generation = await s.execute(
                text(
                    "SELECT state FROM xref_generation "
                    "WHERE id = :id AND source = :source FOR UPDATE"
                ),
                {"id": generation_id, "source": source},
            )
            if generation.scalar_one_or_none() is None:
                raise ValueError("unknown generation for source")
            current = await s.execute(
                text(
                    "SELECT generation_id FROM xref_active_generation "
                    "WHERE source = :source"
                ),
                {"source": source},
            )
            current_id = current.scalar_one_or_none()
            if current_id == generation_id:
                await s.commit()
                return False
            await s.execute(
                text(
                    "UPDATE xref_generation SET state = 'published', "
                    "published_at = COALESCE(published_at, now()) WHERE id = :id"
                ),
                {"id": generation_id},
            )
            await s.execute(
                text(
                    "INSERT INTO xref_activation_history "
                    "(source, generation_id, predecessor_id) "
                    "VALUES (:source, :id, :predecessor)"
                ),
                {"source": source, "id": generation_id, "predecessor": current_id},
            )
            await s.execute(
                text(
                    "INSERT INTO xref_active_generation (source, generation_id) "
                    "VALUES (:source, :id) ON CONFLICT (source) DO UPDATE SET "
                    "generation_id = EXCLUDED.generation_id, activated_at = now()"
                ),
                {"source": source, "id": generation_id},
            )
            await s.commit()
            return True

    async def rollback(self, source: str, *, _publication_locked: bool = False) -> str:
        """Repoint *source* to its active generation's immutable predecessor."""
        async with self._sf() as s:
            if not _publication_locked:
                await s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"xref:{source}"},
                )
            result = await s.execute(
                text(
                    "SELECT h.predecessor_id FROM xref_active_generation a "
                    "JOIN LATERAL (SELECT predecessor_id FROM xref_activation_history "
                    "WHERE source=a.source AND generation_id=a.generation_id "
                    "ORDER BY id DESC LIMIT 1) h ON true "
                    "WHERE a.source = :source FOR UPDATE OF a"
                ),
                {"source": source},
            )
            predecessor = result.scalar_one_or_none()
            if predecessor is None:
                raise ValueError("active generation has no predecessor")
            await s.execute(
                text(
                    "UPDATE xref_active_generation SET generation_id = :id, "
                    "activated_at = now() WHERE source = :source"
                ),
                {"id": predecessor, "source": source},
            )
            await s.commit()
            return str(predecessor)

    async def set_active_generation(
        self,
        source: str,
        generation_id: str | None,
        *,
        _publication_locked: bool = False,
    ) -> None:
        """Set or clear a source pointer for cross-store compensation."""
        async with self._sf() as session:
            if not _publication_locked:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"xref:{source}"},
                )
            if generation_id is None:
                await session.execute(
                    text("DELETE FROM xref_active_generation WHERE source = :source"),
                    {"source": source},
                )
            else:
                await session.execute(
                    text(
                        "INSERT INTO xref_active_generation (source, generation_id) "
                        "VALUES (:source, :id) ON CONFLICT (source) DO UPDATE SET "
                        "generation_id = EXCLUDED.generation_id, activated_at = now()"
                    ),
                    {"source": source, "id": generation_id},
                )
            await session.commit()

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

    async def evidence_by_pair(
        self, run_id: str
    ) -> dict[tuple[str, str], list[EvidenceDict]]:
        """The persisted evidence behind each row of *run_id* (#122, D36).

        Keyed by ``(subject_id, object_id)``; each value is the list of evidence dicts
        (``kind``/``source``/``detail``) the promotion decision used. A candidate row
        carries ``[]`` — so a curation-alone promotion is distinguishable from a
        source-agreement one at the row level, not only in the aggregate run metrics.

        Assumes **one predicate per pair** within the run: the key omits
        ``predicate_id``, so pointing this at an ingest run (which may hold a
        ``closeMatch`` and a ``narrowMatch`` for one pair) would let the rows overwrite
        each other. Promotion runs are one-``exactMatch``-per-pair by construction
        (``_one_per_pair``), which is the intended caller.
        """
        async with self._sf() as s:
            result = await s.execute(
                text(
                    "SELECT subject_id, object_id, evidence "
                    "FROM concept_xref WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            out: dict[tuple[str, str], list[EvidenceDict]] = {}
            for r in result.mappings().all():
                # asyncpg returns jsonb already decoded; SQLite/others may hand back a
                # string. The column is NOT NULL DEFAULT '[]', so a null is unreachable
                # today — but normalize it to [] rather than let a future schema slip
                # surface as a TypeError at the caller's `for e in ...`.
                ev = r["evidence"]
                if isinstance(ev, str):
                    ev = json.loads(ev)
                out[(r["subject_id"], r["object_id"])] = ev if ev is not None else []
            return out

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
        """Return mapping strengths per subject from active generations.

        Because rows from multiple active sources coalesce in the same set, callers
        should be aware that the same ``(subject, predicate)`` may appear
        with different lifecycle states (e.g. ``proposed`` in one generation and
        ``validated`` in another). The downstream ``build_coverage_report`` treats any
        ``exactMatch + {validated, active}`` as identity-grade.  This is
        correct for Phase A where ingest produces ``closeMatch/proposed``
        and validation (#73) promotes to ``exactMatch/validated``;
        cross-source conflicts are resolved by dataset design, not by this query.
        """
        sql = text(
            "SELECT x.subject_id, x.predicate_id, x.lifecycle_state "
            "FROM concept_xref x JOIN xref_active_generation a "
            "ON a.generation_id = x.generation_id"
        )
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

        Only active generations are read. ``DISTINCT`` collapses a byte-identical pair
        present in multiple active sources; without it each duplicate is re-validated
        (two JVM launches apiece) and inflates ``xref_run.metrics`` counts. Note it does
        **not** collapse a re-ingest after a version bump: those rows differ in
        ``*_source_version``, so the pair legitimately returns as a fresh candidate that
        must be re-validated against the new release (D29).
        """
        sql = text(
            "SELECT DISTINCT x.subject_id, x.subject_system, x.predicate_id, "
            "x.object_id, x.object_system, "
            "mapping_justification, confidence, subject_source_version, "
            "object_source_version, lifecycle_state, review_status, author "
            "FROM (SELECT subject_id, subject_system, predicate_id, object_id, "
            "object_system, mapping_justification, confidence, "
            "subject_version AS subject_source_version, "
            "object_version AS object_source_version, lifecycle_state, "
            "review_status, author, generation_id FROM concept_xref) x "
            "JOIN xref_active_generation a ON a.generation_id = x.generation_id "
            "WHERE lifecycle_state = 'proposed' AND predicate_id = :close "
            "ORDER BY subject_id, object_id"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"close": CLOSE_MATCH})
            return [SSSOMRecord(**dict(row)) for row in result.mappings().all()]

    async def validated_anchors(
        self, *, source: str | None = None
    ) -> tuple[tuple[str, str], ...]:
        """Identity-grade bridges already validated — the trusted anchors for #73.

        Only ``exactMatch`` in a ``validated``/``active`` lifecycle counts: a proposed
        ``closeMatch`` is a candidate, never an anchor another candidate leans on.

        *source* scopes to one upstream, and a promotion run must pass it. An anchor
        from
        another upstream is not merely irrelevant: its CURIE (``MONDO:…``) cannot be
        expanded by ``ttl_writer.object_iri``, so it raises ``KeyError`` inside the
        merge
        builder and aborts the entire run.
        """
        scoped = text(
            "SELECT DISTINCT subject_id, object_id FROM concept_xref "
            "WHERE predicate_id = :exact "
            "AND lifecycle_state IN ('validated', 'active') "
            "AND generation_id IN ("
            "SELECT generation_id FROM xref_active_generation WHERE source = :source"
            ") "
            "ORDER BY subject_id, object_id"
        )
        unscoped = text(
            "SELECT DISTINCT subject_id, object_id FROM concept_xref "
            "WHERE predicate_id = :exact "
            "AND lifecycle_state IN ('validated', 'active') "
            "ORDER BY subject_id, object_id"
        )
        sql = scoped if source else unscoped
        params: dict[str, str] = {"exact": EXACT_MATCH}
        if source:
            params["source"] = source
        async with self._sf() as s:
            result = await s.execute(sql, params)
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
            "AND generation_id IN ("
            "SELECT generation_id FROM xref_active_generation WHERE source = :source"
            ") "
            "AND (subject_version <> :ncit_version "
            "     OR object_version <> :source_version)"
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
            "AND generation_id IN ("
            "SELECT generation_id FROM xref_active_generation WHERE source = :source"
            ") "
            "AND (subject_version <> :ncit_version "
            "     OR object_version <> :source_version)"
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
            "AND generation_id IN ("
            "SELECT generation_id FROM xref_active_generation WHERE source = :source"
            ") "
            "AND (subject_version <> :ncit_version "
            "     OR object_version <> :source_version)"
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
        self, codes: set[str], *, expected: GenerationSourceMetadata
    ) -> dict[str, list[MappingResult]]:
        if not codes:
            return {}
        async with self._sf() as s:
            generation_ids = await self._validated_active_generations(s, expected)
            if not generation_ids:
                return {}
            result = await s.execute(
                text(
                    "SELECT x.subject_system, x.subject_version, x.subject_id, "
                    "x.object_system, x.object_version, x.object_id, x.predicate_id, "
                    "x.lifecycle_state, x.confidence FROM concept_xref x "
                    "WHERE x.generation_id = ANY(:generation_ids) "
                    "AND x.subject_id = ANY(:codes)"
                ),
                {"generation_ids": generation_ids, "codes": list(codes)},
            )
            rows = result.mappings().all()
            out: dict[str, list[MappingResult]] = {}
            for r in rows:
                out.setdefault(r["subject_id"], []).append(
                    MappingResult(
                        subject=EndpointIdentity(
                            r["subject_system"], r["subject_version"], r["subject_id"]
                        ),
                        predicate=r["predicate_id"],
                        object=EndpointIdentity(
                            r["object_system"], r["object_version"], r["object_id"]
                        ),
                        lifecycle=r["lifecycle_state"],
                        confidence=r["confidence"],
                    )
                )
            return out

    async def mappings_by_objects(
        self, curies: set[str], *, expected: GenerationSourceMetadata
    ) -> dict[str, list[MappingResult]]:
        if not curies:
            return {}
        async with self._sf() as s:
            generation_ids = await self._validated_active_generations(s, expected)
            if not generation_ids:
                return {}
            result = await s.execute(
                text(
                    "SELECT x.subject_system, x.subject_version, x.subject_id, "
                    "x.object_system, x.object_version, x.object_id, x.predicate_id, "
                    "x.lifecycle_state, x.confidence FROM concept_xref x "
                    "WHERE x.generation_id = ANY(:generation_ids) "
                    "AND x.object_id = ANY(:curies)"
                ),
                {"generation_ids": generation_ids, "curies": list(curies)},
            )
            rows = result.mappings().all()
            out: dict[str, list[MappingResult]] = {}
            for r in rows:
                out.setdefault(r["object_id"], []).append(
                    MappingResult(
                        subject=EndpointIdentity(
                            r["subject_system"], r["subject_version"], r["subject_id"]
                        ),
                        predicate=r["predicate_id"],
                        object=EndpointIdentity(
                            r["object_system"], r["object_version"], r["object_id"]
                        ),
                        lifecycle=r["lifecycle_state"],
                        confidence=r["confidence"],
                    )
                )
            return out

    async def mappings_for_identifiers(
        self, identifiers: set[str], *, expected: GenerationSourceMetadata
    ) -> dict[str, list[MappingResult]]:
        """Find active mappings in either direction in one indexed roundtrip."""
        if not identifiers:
            return {}
        async with self._sf() as s:
            generation_ids = await self._validated_active_generations(s, expected)
            if not generation_ids:
                return {}
            result = await s.execute(
                text(
                    "SELECT x.subject_system, x.subject_version, x.subject_id, "
                    "x.object_system, x.object_version, x.object_id, x.predicate_id, "
                    "x.lifecycle_state, x.confidence FROM concept_xref x "
                    "WHERE x.generation_id = ANY(:generation_ids) "
                    "AND x.subject_id = ANY(:identifiers) UNION ALL "
                    "SELECT x.subject_system, x.subject_version, x.subject_id, "
                    "x.object_system, x.object_version, x.object_id, x.predicate_id, "
                    "x.lifecycle_state, x.confidence FROM concept_xref x "
                    "WHERE x.generation_id = ANY(:generation_ids) "
                    "AND x.object_id = ANY(:identifiers) "
                    "AND NOT (x.subject_id = ANY(:identifiers))"
                ),
                {
                    "generation_ids": generation_ids,
                    "identifiers": list(identifiers),
                },
            )
            rows = result.mappings().all()
            out: dict[str, list[MappingResult]] = {}
            for row in rows:
                mapping = MappingResult(
                    subject=EndpointIdentity(
                        row["subject_system"], row["subject_version"], row["subject_id"]
                    ),
                    predicate=row["predicate_id"],
                    object=EndpointIdentity(
                        row["object_system"], row["object_version"], row["object_id"]
                    ),
                    lifecycle=row["lifecycle_state"],
                    confidence=row["confidence"],
                )
                key = (
                    mapping.subject.identifier
                    if mapping.subject.identifier in identifiers
                    else mapping.object.identifier
                )
                out.setdefault(key, []).append(mapping)
            return out

    async def _validated_active_generations(
        self, session: AsyncSession, expected: GenerationSourceMetadata
    ) -> list[str]:
        result = await session.execute(
            text(
                "SELECT a.source, a.generation_id, g.source_metadata "
                "FROM xref_active_generation a JOIN xref_generation g "
                "ON g.source=a.source AND g.id=a.generation_id ORDER BY a.source"
            )
        )
        generation_ids: list[str] = []
        for row in result.mappings().all():
            source = str(row["source"])
            observed = GenerationSourceMetadata.model_validate(row["source_metadata"])
            required = _required_source_identities(source, observed, expected)
            if required is None:
                continue
            _validate_source_contract(source, observed, expected, required)
            generation_ids.append(str(row["generation_id"]))
        return generation_ids
