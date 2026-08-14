"""Promotion persistence + D29 lifecycle, against real Postgres (#73).

Integration: the promoted bridge must land as ``exactMatch`` + ``validated`` (which is
what makes it identity-grade for the §13.3 coverage number), the candidate it came from
must survive untouched, and an endpoint version bump must quarantine bridges validated
against the older release rather than keep serving them (D29).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.core.data_build_tools import DataBuildToolIdentity
from ontolib.repositories.xref.evidence import (
    LABEL_AGREEMENT,
    SME_CURATION,
    XREF_ASSERTION,
    Evidence,
)
from ontolib.repositories.xref.models import (
    P334GenerationMetadata,
    SSSOMRecord,
    StaleXrefGenerationError,
    UberonCandidateGenerationMetadata,
    UberonPromotionGenerationMetadata,
    UberonPublisherGenerationMetadata,
    UberonReadIdentity,
    UnavailableXrefGenerationError,
)
from ontolib.repositories.xref.promotion import (
    PromotionReport,
)
from ontolib.repositories.xref.promotion import (
    persist_promotions as _persist_promotions,
)
from ontolib.repositories.xref.promotion import (
    run_promotion as _run_promotion,
)
from ontolib.repositories.xref.publication import (
    generation_graph_iri,
    generation_identity,
)
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.validation import ReasonerUnavailableError
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH, NARROW_MATCH

from .conftest import activate_records

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_NCIT_VERSION = "26.02d"
_UBERON_VERSION = "uberon-2026-01"
_SOURCE_METADATA = UberonPromotionGenerationMetadata(
    ncit_source_identity="a" * 64,
    uberon_source_identity="b" * 64,
    uberon_serving_identity="c" * 64,
)
_CANDIDATE_METADATA = UberonCandidateGenerationMetadata(
    ncit_source_identity="a" * 64,
    uberon_source_identity="b" * 64,
    uberon_serving_identity="c" * 64,
)
_CANDIDATE_IDENTITY = UberonReadIdentity(
    ncit_source_identity="a" * 64,
    uberon_source_identity="b" * 64,
    uberon_serving_identity="c" * 64,
)
_REASONER_TOOL = DataBuildToolIdentity(
    name="test-reasoner",
    source="test://ontolib.tests.repositories.xref",
    version="1",
    digest="sha256:" + "1" * 64,
)


async def persist_promotions(*args: object, **kwargs: object) -> str:
    kwargs.setdefault("source_metadata", _SOURCE_METADATA)
    return await _persist_promotions(*args, **kwargs)  # type: ignore[arg-type]


async def run_promotion(*args: object, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("source_metadata", _SOURCE_METADATA)
    return await _run_promotion(*args, **kwargs)  # type: ignore[arg-type]


class _PublicationClient:
    async def load(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def select(self, _query: str) -> list[dict[str, str]]:
        return []


pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


def _candidate(subject: str, obj: str) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=subject,
        predicate_id=CLOSE_MATCH,
        object_id=obj,
        mapping_justification="https://ontoprism.org/vocab#PublisherDatabaseCrossReference",
        confidence=0.9,
        subject_source_version=_NCIT_VERSION,
        object_source_version=_UBERON_VERSION,
    )


def _promoted(
    subject: str, obj: str, *, object_version: str = _UBERON_VERSION
) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=subject,
        predicate_id=EXACT_MATCH,
        object_id=obj,
        mapping_justification="semapv:ManualMappingCuration",
        confidence=1.0,
        subject_source_version=_NCIT_VERSION,
        object_source_version=object_version,
        lifecycle_state="validated",
    )


@pytest.fixture
async def store() -> AsyncIterator[tuple[XrefStore, list[str]]]:
    """A live XrefStore, plus the run-ids to clean up afterwards."""
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    run_ids: list[str] = []
    yield XrefStore(sf), run_ids
    async with sf() as s:
        await s.execute(text("TRUNCATE xref_generation, xref_run CASCADE"))
        await s.commit()
    await dispose_engine(engine)


def _with_evidence(record: SSSOMRecord, *evidence: Evidence) -> SSSOMRecord:
    return replace(record, evidence=evidence)


@pytest.mark.integration
async def test_a_promoted_bridge_persists_the_evidence_the_decision_used(
    store: tuple[XrefStore, list[str]],
) -> None:
    """The evidence behind a bridge round-trips through Postgres (#122, D36).

    This is the test the asyncpg trap can only be caught by: ``evidence`` is a ``jsonb``
    column, and asyncpg will not adapt a bare list/dict — it must be ``json.dumps`` + an
    explicit ``CAST`` (as ``update_run_metrics`` already does). A fake session would
    accept a Python list and pass; only a real DB round-trip proves the serialization.
    """
    xref_store, run_ids = store
    rid = f"test-promo-{uuid.uuid4().hex}"
    run_ids.append(rid)

    promoted = _with_evidence(
        _promoted("C12468", "UBERON:0002048"),
        Evidence(kind=LABEL_AGREEMENT, source="rdfs:label", detail="lung"),
        Evidence(
            kind=XREF_ASSERTION, source="oboInOwl:hasDbXref", detail="NCIT:C12468"
        ),
    )
    await persist_promotions(
        xref_store,
        _PublicationClient(),  # type: ignore[arg-type]
        [promoted],
        PromotionReport(considered=1, promoted=1, insufficient_evidence=0, refuted=0),
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
        run_id=rid,
    )

    by_pair = await xref_store.evidence_by_pair(rid)
    stored = by_pair[("C12468", "UBERON:0002048")]
    assert {e["kind"] for e in stored} == {LABEL_AGREEMENT, XREF_ASSERTION}
    # provenance survives, not just the kind
    xref = next(e for e in stored if e["kind"] == XREF_ASSERTION)
    assert xref["source"] == "oboInOwl:hasDbXref"
    assert xref["detail"] == "NCIT:C12468"


@pytest.mark.integration
async def test_a_curated_promotion_is_distinguishable_from_source_agreement_per_row(
    store: tuple[XrefStore, list[str]],
) -> None:
    """A row's evidence list distinguishes a curation-alone promotion
    (``sme_curation``) from a source-agreement one (``label_agreement`` +
    ``xref_assertion``) — the row
    itself carries the provenance, not only the aggregate run metrics (#122).
    """
    xref_store, run_ids = store
    rid = f"test-mix-{uuid.uuid4().hex}"
    run_ids.append(rid)

    curated = _with_evidence(
        _promoted("C1", "UBERON:0000001"),
        Evidence(kind=SME_CURATION, source="curated-mapping-set"),
    )
    source_agree = _with_evidence(
        _promoted("C2", "UBERON:0000002"),
        Evidence(kind=LABEL_AGREEMENT, source="rdfs:label", detail="x"),
        Evidence(kind=XREF_ASSERTION, source="oboInOwl:hasDbXref", detail="NCIT:C2"),
    )
    await persist_promotions(
        xref_store,
        _PublicationClient(),  # type: ignore[arg-type]
        [curated, source_agree],
        PromotionReport(considered=2, promoted=2, insufficient_evidence=0, refuted=0),
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
        run_id=rid,
    )

    by_pair = await xref_store.evidence_by_pair(rid)
    assert {e["kind"] for e in by_pair[("C1", "UBERON:0000001")]} == {SME_CURATION}
    assert {e["kind"] for e in by_pair[("C2", "UBERON:0000002")]} == {
        LABEL_AGREEMENT,
        XREF_ASSERTION,
    }


@pytest.mark.integration
async def test_an_unpromoted_candidate_persists_empty_evidence(
    store: tuple[XrefStore, list[str]],
) -> None:
    """A proposed candidate has no evidence — the column stores ``[]``, never null,
    so read-back is always an iterable."""
    xref_store, run_ids = store
    rid = f"test-cand-{uuid.uuid4().hex}"
    run_ids.append(rid)

    await xref_store.upsert_run(
        run_id=rid,
        source="uberon-cl",
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
    )
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=rid,
        records=[_candidate("C3", "UBERON:0000003")],
    )

    by_pair = await xref_store.evidence_by_pair(rid)
    assert by_pair[("C3", "UBERON:0000003")] == []


@pytest.mark.integration
async def test_promotion_persists_as_validated_exact_match(
    store: tuple[XrefStore, list[str]],
) -> None:
    xref_store, run_ids = store
    candidate_run = f"test-cand-{uuid.uuid4().hex}"
    run_ids.append(candidate_run)

    await xref_store.upsert_run(
        run_id=candidate_run,
        source="uberon-cl",
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
    )
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=candidate_run,
        records=[_candidate("C12468", "UBERON:0002048")],
    )

    report = PromotionReport(
        considered=1, promoted=1, insufficient_evidence=0, refuted=0
    )
    promotion_run = await persist_promotions(
        xref_store,
        _PublicationClient(),  # type: ignore[arg-type]
        [_promoted("C12468", "UBERON:0002048")],
        report,
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
    )
    run_ids.append(promotion_run)

    # the promoted bridge is identity-grade …
    assert ("C12468", "UBERON:0002048") in await xref_store.validated_anchors()
    strength = await xref_store.mapping_strength_by_subject()
    assert (EXACT_MATCH, "validated") in strength["C12468"]
    # … and the candidate it came from is still there, untouched and auditable
    assert (CLOSE_MATCH, "proposed") in strength["C12468"]


@pytest.mark.integration
async def test_proposed_candidates_returns_only_unvalidated(
    store: tuple[XrefStore, list[str]],
) -> None:
    xref_store, run_ids = store
    run_id = f"test-prop-{uuid.uuid4().hex}"
    run_ids.append(run_id)

    await xref_store.upsert_run(
        run_id=run_id,
        source="uberon-cl",
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
    )
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=run_id,
        records=[
            _candidate("C12468", "UBERON:0002048"),
            replace(
                _candidate("C-VALIDATED", "UBERON:VALIDATED"),
                lifecycle_state="validated",
            ),
            replace(_candidate("C-ACTIVE", "UBERON:ACTIVE"), lifecycle_state="active"),
            replace(
                _candidate("C-QUARANTINED", "UBERON:QUARANTINED"),
                lifecycle_state="quarantined",
            ),
            replace(
                _candidate("C-RETIRED", "UBERON:RETIRED"),
                lifecycle_state="retired",
            ),
        ],
    )

    candidates = await xref_store.proposed_candidates(expected=_CANDIDATE_IDENTITY)
    pairs = {(c.subject_id, c.object_id) for c in candidates}
    assert ("C12468", "UBERON:0002048") in pairs
    assert pairs == {("C12468", "UBERON:0002048")}
    assert all(c.lifecycle_state == "proposed" for c in candidates)


@pytest.mark.integration
async def test_proposed_candidates_only_reads_candidate_generation_source(
    store: tuple[XrefStore, list[str]],
) -> None:
    xref_store, run_ids = store
    rows = (
        ("uberon-cl", _candidate("C-CANDIDATE", "UBERON:CANDIDATE"), None),
        (
            "uberon-publisher-xref",
            SSSOMRecord(
                subject_id="UBERON:PUBLISHER",
                subject_system="uberon-cl",
                predicate_id=CLOSE_MATCH,
                object_id="C-PUBLISHER",
                object_system="ncit",
                mapping_justification="semapv:ManualMappingCuration",
                confidence=1.0,
                subject_source_version=_UBERON_VERSION,
                object_source_version=_NCIT_VERSION,
            ),
            UberonPublisherGenerationMetadata(
                ncit_source_identity="a" * 64,
                uberon_source_identity="b" * 64,
                uberon_serving_identity="c" * 64,
                uberon_assertion_identity="d" * 64,
                ncit_target_identity="e" * 64,
            ),
        ),
        (
            "uberon-cl-promotion",
            _candidate("C-PROMOTION", "UBERON:PROMOTION"),
            _SOURCE_METADATA,
        ),
        (
            "ncit-p334-icdo32",
            SSSOMRecord(
                subject_id="C-P334",
                predicate_id=CLOSE_MATCH,
                object_id="8140/3",
                object_system="icdo",
                mapping_justification="semapv:ManualMappingCuration",
                confidence=1.0,
                subject_source_version=_NCIT_VERSION,
                object_source_version="3.2",
            ),
            P334GenerationMetadata(
                ncit_source_identity="a" * 64,
                icdo_generation_identity="f" * 64,
                icdo_serving_identity="1" * 64,
                ncit_p334_identity="2" * 64,
            ),
        ),
    )
    for source, record, metadata in rows:
        run_id = f"test-source-filter-{source}-{uuid.uuid4().hex}"
        run_ids.append(run_id)
        await xref_store.upsert_run(
            run_id, source, _NCIT_VERSION, record.object_source_version
        )
        kwargs = {"source_metadata": metadata} if metadata is not None else {}
        await activate_records(
            xref_store, source=source, run_id=run_id, records=[record], **kwargs
        )

    candidates = await xref_store.proposed_candidates(expected=_CANDIDATE_IDENTITY)

    assert {(row.subject_id, row.object_id) for row in candidates} == {
        ("C-CANDIDATE", "UBERON:CANDIDATE")
    }


@pytest.mark.integration
async def test_proposed_candidates_requires_an_active_candidate_generation(
    store: tuple[XrefStore, list[str]],
) -> None:
    xref_store, _run_ids = store
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as session:
            await session.execute(
                text("DELETE FROM xref_active_generation WHERE source = 'uberon-cl'")
            )
            await session.commit()

        with pytest.raises(UnavailableXrefGenerationError):
            await xref_store.proposed_candidates(expected=_CANDIDATE_IDENTITY)
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_proposed_candidates_rejects_a_stale_candidate_generation(
    store: tuple[XrefStore, list[str]],
) -> None:
    xref_store, run_ids = store
    run_id = f"test-stale-candidate-{uuid.uuid4().hex}"
    run_ids.append(run_id)
    stale_metadata = UberonCandidateGenerationMetadata(
        ncit_source_identity="d" * 64,
        uberon_source_identity="b" * 64,
        uberon_serving_identity="c" * 64,
    )
    await xref_store.upsert_run(run_id, "uberon-cl", _NCIT_VERSION, _UBERON_VERSION)
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=run_id,
        records=[_candidate("C-STALE", "UBERON:STALE")],
        source_metadata=stale_metadata,
    )

    with pytest.raises(StaleXrefGenerationError):
        await xref_store.proposed_candidates(expected=_CANDIDATE_IDENTITY)


@pytest.mark.integration
async def test_an_endpoint_release_plans_stale_bridges_without_mutating_published_rows(
    store: tuple[XrefStore, list[str]],
) -> None:
    """D29: a bridge validated against an older upstream release is no longer *known*
    good — it is quarantined (not served, not deleted), pending re-validation."""
    xref_store, run_ids = store
    run_id = f"test-stale-{uuid.uuid4().hex}"
    run_ids.append(run_id)

    await xref_store.upsert_run(
        run_id=run_id,
        source="uberon-cl-promotion",
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
    )
    await activate_records(
        xref_store,
        source="uberon-cl-promotion",
        run_id=run_id,
        source_metadata=_SOURCE_METADATA,
        records=[
            _promoted("C12377", "UBERON:0002110", object_version="uberon-2025-06"),
            _promoted("C12391", "UBERON:0000945"),
        ],
    )

    original_generation = await xref_store.active_generation("uberon-cl-promotion")
    stale = await xref_store.stale_anchors(
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
        generation_id=original_generation,
    )

    assert stale == {("C12377", "UBERON:0002110")}
    assert (
        await xref_store.active_generation("uberon-cl-promotion") == original_generation
    )
    assert ("C12377", "UBERON:0002110") in await xref_store.validated_anchors(
        source="uberon-cl-promotion", generation_id=original_generation
    )


@pytest.mark.integration
async def test_stale_planning_is_scoped_to_its_own_upstream_source(
    store: tuple[XrefStore, list[str]],
) -> None:
    """A promotion sweep cannot quarantine a candidate-source generation."""
    xref_store, run_ids = store
    candidate_run = f"test-candidate-source-{uuid.uuid4().hex}"
    run_ids.append(candidate_run)

    await xref_store.upsert_run(
        run_id=candidate_run,
        source="uberon-cl",
        ncit_version=_NCIT_VERSION,
        source_version="uberon-2025-06",
    )
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=candidate_run,
        records=[_promoted("C3262", "UBERON:0002107", object_version="uberon-2025-06")],
    )

    stale = await xref_store.stale_anchors(
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
    )

    # The bridge belongs to another known source and must be untouched.
    assert ("C3262", "UBERON:0002107") in await xref_store.validated_anchors()
    assert stale == set()


@pytest.mark.integration
async def test_a_narrow_match_is_never_offered_up_for_promotion(
    store: tuple[XrefStore, list[str]],
) -> None:
    """Promotion rewrites the predicate to exactMatch, so a curator's explicit
    narrowMatch ("the object is NARROWER than the subject" — the golden set has exactly
    such rows) must never enter the candidate set, or it would be silently upgraded to
    identity."""
    xref_store, run_ids = store
    run_id = f"test-narrow-{uuid.uuid4().hex}"
    run_ids.append(run_id)

    await xref_store.upsert_run(
        run_id=run_id,
        source="uberon-cl",
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
    )
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=run_id,
        records=[
            SSSOMRecord(
                subject_id="C19184",
                predicate_id=NARROW_MATCH,
                object_id="UBERON:0001155",
                mapping_justification="semapv:ManualMappingCuration",
                confidence=1.0,
                subject_source_version=_NCIT_VERSION,
                object_source_version=_UBERON_VERSION,
            )
        ],
    )

    candidates = await xref_store.proposed_candidates(expected=_CANDIDATE_IDENTITY)
    pairs = {(c.subject_id, c.object_id) for c in candidates}
    assert ("C19184", "UBERON:0001155") not in pairs


@pytest.mark.integration
async def test_a_promotion_run_does_not_quarantine_what_it_just_promoted(
    store: tuple[XrefStore, list[str]],
) -> None:
    """THE regression test for the self-quarantine bug.

    A promoted record inherits the candidate's *ingest-time* versions.  The D29 sweep
    compares exactly those columns against the versions the run validated against, so if
    the two differ by a character — an operator passing `--uberon-version`, or NCIt
    reloaded between ingest and promote — the run would promote N bridges and quarantine
    those same N bridges moments later, while reporting `promoted: N` and exiting 0.
    Coverage would stay at zero forever and nothing would say why.

    `persist_promotions` therefore re-stamps each row with the versions the run actually
    validated against, which is what the row asserts.
    """
    xref_store, run_ids = store
    run_id = f"test-selfq-{uuid.uuid4().hex}"
    run_ids.append(run_id)

    # the candidate was ingested against an OLDER upstream release …
    stale_candidate = SSSOMRecord(
        subject_id="C12971",
        predicate_id=EXACT_MATCH,
        object_id="UBERON:0000310",
        mapping_justification="semapv:ManualMappingCuration",
        confidence=1.0,
        subject_source_version=_NCIT_VERSION,
        object_source_version="uberon-2025-06",
        lifecycle_state="validated",
    )
    report = PromotionReport(
        considered=1, promoted=1, insufficient_evidence=0, refuted=0
    )

    # … and the run validates against the CURRENT one
    await persist_promotions(
        xref_store,
        _PublicationClient(),  # type: ignore[arg-type]
        [stale_candidate],
        report,
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
        run_id=run_id,
        tool_identity=_REASONER_TOOL,
    )
    stale = await xref_store.stale_anchors(
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
    )

    # the bridge this run just validated must survive its own staleness sweep
    assert ("C12971", "UBERON:0000310") in await xref_store.validated_anchors()
    assert stale == set()


@pytest.mark.integration
async def test_a_failed_run_is_persisted_as_failed_not_completed(
    store: tuple[XrefStore, list[str]],
) -> None:
    """A run whose reasoner never ran must not be recorded as a completed run that
    conservatively promoted nothing — that is the lie this module exists to abolish."""
    xref_store, run_ids = store
    run_id = f"test-failed-{uuid.uuid4().hex}"
    run_ids.append(run_id)

    report = PromotionReport(
        considered=2,
        promoted=0,
        insufficient_evidence=0,
        refuted=0,
        reasoner_errors=2,
    )
    assert report.failed is True

    await persist_promotions(
        xref_store,
        _PublicationClient(),  # type: ignore[arg-type]
        [],
        report,
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
        run_id=run_id,
        tool_identity=_REASONER_TOOL,
    )

    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as s:
            row = (
                (
                    await s.execute(
                        text("SELECT status, metrics FROM xref_run WHERE id = :rid"),
                        {"rid": run_id},
                    )
                )
                .mappings()
                .one()
            )
        async with sf() as s:
            generation_count = await s.scalar(
                text(
                    "SELECT count(*) FROM xref_generation g JOIN concept_xref x "
                    "ON x.generation_source=g.source AND x.generation_id=g.id "
                    "WHERE x.run_id = :rid"
                ),
                {"rid": run_id},
            )
    finally:
        await dispose_engine(engine)

    assert row["status"] == "failed"
    assert row["metrics"]["reasoner_errors"] == 2
    assert row["metrics"]["tools"] == [_REASONER_TOOL.as_dict()]
    assert generation_count == 0


class _StubClient:
    """Canned SPARQL for the run-level test (the stores are not under test here)."""

    def __init__(self, rows: dict[str, list[dict[str, str]]]) -> None:
        self._rows = rows

    async def select(self, query: str) -> list[dict[str, str | None]]:
        for key, rows in self._rows.items():
            if key in query:
                return [dict(r) for r in rows]  # type: ignore[misc]
        return []

    async def load(self, *_args: object, **_kwargs: object) -> None:
        pass


def _echo_reasoner(ttl: str) -> set[tuple[str, str]]:
    """Accepts every merge, echoing its stated edges (ELK-shaped: no closure)."""
    from rdflib import Graph  # noqa: PLC0415
    from rdflib.namespace import OWL, RDFS  # noqa: PLC0415

    g = Graph().parse(data=ttl, format="turtle")
    edges = {(str(s), str(o)) for s, o in g.subject_objects(RDFS.subClassOf)}
    for s_, o_ in g.subject_objects(OWL.equivalentClass):
        edges.add((str(s_), str(o_)))
        edges.add((str(o_), str(s_)))
    return edges


@pytest.mark.integration
async def test_run_promotion_never_lets_an_unexpandable_candidate_reach_the_merge(
    store: tuple[XrefStore, list[str]],
) -> None:
    """THE seam test. `run_promotion` is where the wiring lives, and it had NO test —
    which is exactly how a boundary filter that rebound a local (and so filtered
    nothing) shipped while its own log line announced that it had.

    A `GO:` candidate is real (ingest's lexical pass indexes every labelled class in the
    upstream store, imports included). If it reaches `build_validation_ontology`,
    `object_iri` raises KeyError and the WHOLE run dies, discarding every promotion.
    """
    xref_store, run_ids = store
    ingest_run = f"test-seam-{uuid.uuid4().hex}"
    run_ids.append(ingest_run)

    await xref_store.upsert_run(
        run_id=ingest_run,
        source="uberon-cl",
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
    )
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=ingest_run,
        records=[
            _candidate("C12468", "UBERON:0002048"),  # expandable
            _candidate("C99999", "GO:0110165"),  # NOT expandable — must never be scored
        ],
    )

    ncit_ns = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
    obo = "http://purl.obolibrary.org/obo/"
    ncit = _StubClient(
        {
            "?parent": [{"child": f"{ncit_ns}C12468", "parent": f"{ncit_ns}C12366"}],
            "rdfs:label": [{"code": "C12468", "label": "Lung"}],
        }
    )
    uberon = _StubClient(
        {
            "?parent": [
                {"child": f"{obo}UBERON_0002048", "parent": f"{obo}UBERON_0001004"}
            ],
            "rdfs:label": [{"concept": f"{obo}UBERON_0002048", "label": "lung"}],
        }
    )

    report = await run_promotion(
        xref_store,
        ncit,  # type: ignore[arg-type]
        uberon,  # type: ignore[arg-type]
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
        tool_identity=_REASONER_TOOL,
        curated_pairs=frozenset({("C12468", "UBERON:0002048")}),
        reasoner=_echo_reasoner,
    )
    run_ids.append(report["run_id"])

    # it did not crash, the GO row was never scored, and the drop is VISIBLE
    assert report["skipped_unexpandable"] == 1
    assert report["considered"] == 1
    assert report["promoted"] == 1
    # …and the run says plainly that curation, not the machinery, earned it
    assert report["promoted_on_curation_alone"] == 1
    assert report["promoted_with_structural_corroboration"] == 0
    assert report["tools"] == [_REASONER_TOOL.as_dict()]

    # GATE LIVENESS for #122: the evidence that curation earned it survived the ENTIRE
    # real path (validate_candidate → promote_candidate → _settle_contests → persist),
    # not just a hand-built record — so a future refactor that drops evidence between
    # promote_candidates and the row would fail here. The isolated persistence test
    # cannot catch that; only reading evidence off a run the machinery produced.
    by_pair = await xref_store.evidence_by_pair(report["run_id"])
    assert SME_CURATION in {e["kind"] for e in by_pair[("C12468", "UBERON:0002048")]}


@pytest.mark.parametrize(
    "switch_after", ["proposed_candidates", "validated_anchors", "stale_anchors"]
)
@pytest.mark.integration
async def test_promotion_serializes_a_candidate_pointer_change_until_after_publication(
    store: tuple[XrefStore, list[str]], switch_after: str
) -> None:
    xref_store, run_ids = store
    first_run = f"test-snapshot-first-{uuid.uuid4().hex}"
    second_run = f"test-snapshot-second-{uuid.uuid4().hex}"
    run_ids.extend((first_run, second_run))
    await xref_store.upsert_run(first_run, "uberon-cl", _NCIT_VERSION, _UBERON_VERSION)
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=first_run,
        records=[_candidate("C-SNAPSHOT", "UBERON:0002048")],
    )
    captured_generation = await xref_store.active_generation("uberon-cl")
    await xref_store.upsert_run(second_run, "uberon-cl", _NCIT_VERSION, _UBERON_VERSION)
    second_records = [_candidate("C-OTHER", "UBERON:0001264")]
    second_generation, second_content = generation_identity(
        "uberon-cl", second_records, _CANDIDATE_METADATA
    )
    await xref_store.prepare_generation(
        source="uberon-cl",
        generation_id=second_generation,
        content_sha256=second_content,
        source_metadata=_CANDIDATE_METADATA,
        graph_iri=generation_graph_iri("uberon-cl", second_generation),
        run_id=second_run,
        records=second_records,
    )

    class SwitchingStore(XrefStore):
        switched = False
        switch_task: asyncio.Task[bool] | None = None

        async def _switch(self, phase: str) -> None:
            if phase == switch_after and not self.switched:
                self.switched = True
                self.switch_task = asyncio.create_task(
                    xref_store.activate_generation("uberon-cl", second_generation)
                )
                await asyncio.sleep(0.05)

        async def proposed_candidates(self, **kwargs: object) -> list[SSSOMRecord]:
            rows = await xref_store.proposed_candidates(**kwargs)  # type: ignore[arg-type]
            await self._switch("proposed_candidates")
            return rows

        async def validated_anchors(
            self, **kwargs: object
        ) -> tuple[tuple[str, str], ...]:
            rows = await xref_store.validated_anchors(**kwargs)  # type: ignore[arg-type]
            await self._switch("validated_anchors")
            return rows

        async def stale_anchors(self, **kwargs: object) -> set[tuple[str, str]]:
            rows = await xref_store.stale_anchors(**kwargs)  # type: ignore[arg-type]
            await self._switch("stale_anchors")
            return rows

    switching_store = SwitchingStore(xref_store._sf)  # type: ignore[attr-defined]
    ncit_ns = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
    obo = "http://purl.obolibrary.org/obo/"
    ncit = _StubClient(
        {
            "?parent": [
                {
                    "child": f"{ncit_ns}C-SNAPSHOT",
                    "parent": f"{ncit_ns}C12366",
                }
            ],
            "rdfs:label": [{"code": "C-SNAPSHOT", "label": "lung"}],
        }
    )
    uberon = _StubClient(
        {
            "?parent": [
                {
                    "child": f"{obo}UBERON_0002048",
                    "parent": f"{obo}UBERON_0001004",
                }
            ],
            "rdfs:label": [
                {
                    "concept": "http://purl.obolibrary.org/obo/UBERON_0002048",
                    "label": "lung",
                }
            ],
        }
    )

    report = await run_promotion(
        switching_store,
        ncit,  # type: ignore[arg-type]
        uberon,  # type: ignore[arg-type]
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
        tool_identity=_REASONER_TOOL,
        curated_pairs=frozenset({("C-SNAPSHOT", "UBERON:0002048")}),
        reasoner=_echo_reasoner,
    )
    run_ids.append(str(report["run_id"]))
    assert switching_store.switch_task is not None
    await switching_store.switch_task

    assert captured_generation != await xref_store.active_generation("uberon-cl")
    assert report["status"] == "completed"
    assert ("C-SNAPSHOT", "UBERON:0002048") in await xref_store.validated_anchors(
        source="uberon-cl-promotion"
    )


@pytest.mark.integration
async def test_failed_promotion_run_writes_only_the_failed_run_record(
    store: tuple[XrefStore, list[str]],
) -> None:
    xref_store, run_ids = store
    ingest_run = f"test-failed-seam-{uuid.uuid4().hex}"
    run_ids.append(ingest_run)
    await xref_store.upsert_run(ingest_run, "uberon-cl", _NCIT_VERSION, _UBERON_VERSION)
    await activate_records(
        xref_store,
        source="uberon-cl",
        run_id=ingest_run,
        records=[_candidate("C-FAILED-RUN", "UBERON:0099999")],
    )
    stale_run = f"test-stale-promotion-{uuid.uuid4().hex}"
    run_ids.append(stale_run)
    await xref_store.upsert_run(
        stale_run, "uberon-cl-promotion", _NCIT_VERSION, "uberon-2025-06"
    )
    await activate_records(
        xref_store,
        source="uberon-cl-promotion",
        run_id=stale_run,
        records=[
            _promoted("C12377", "UBERON:0002110", object_version="uberon-2025-06")
        ],
        source_metadata=_SOURCE_METADATA,
    )
    generation_before = await xref_store.active_generation("uberon-cl-promotion")

    ncit_ns = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
    obo = "http://purl.obolibrary.org/obo/"
    ncit = _StubClient(
        {
            "?parent": [
                {"child": f"{ncit_ns}C-FAILED-RUN", "parent": f"{ncit_ns}C12366"}
            ],
            "rdfs:label": [{"code": "C-FAILED-RUN", "label": "Failed run"}],
        }
    )
    uberon = _StubClient(
        {
            "?parent": [
                {"child": f"{obo}UBERON_0099999", "parent": f"{obo}UBERON_0001004"}
            ],
            "rdfs:label": [{"concept": f"{obo}UBERON_0099999", "label": "failed run"}],
        }
    )

    def unavailable(_ttl: str) -> set[tuple[str, str]]:
        raise ReasonerUnavailableError("robot unavailable")

    report = await run_promotion(
        xref_store,
        ncit,  # type: ignore[arg-type]
        uberon,  # type: ignore[arg-type]
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="uberon-cl-promotion",
        tool_identity=_REASONER_TOOL,
        curated_pairs=frozenset({("C-FAILED-RUN", "UBERON:0099999")}),
        reasoner=unavailable,
    )
    run_ids.append(report["run_id"])

    assert report["status"] == "failed"
    assert report["quarantined"] == 0
    assert report["stale_pending"] == 0
    assert (
        await xref_store.active_generation("uberon-cl-promotion") == generation_before
    )
    assert ("C12377", "UBERON:0002110") in await xref_store.validated_anchors(
        source="uberon-cl-promotion"
    )
    assert await xref_store.records_for_run(report["run_id"]) == []
