"""Promotion persistence + D29 lifecycle, against real Postgres (#73).

Integration: the promoted bridge must land as ``exactMatch`` + ``validated`` (which is
what makes it identity-grade for the §13.3 coverage number), the candidate it came from
must survive untouched, and an endpoint version bump must quarantine bridges validated
against the older release rather than keep serving them (D29).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.xref.evidence import (
    LABEL_AGREEMENT,
    SME_CURATION,
    XREF_ASSERTION,
    Evidence,
)
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.promotion import (
    PromotionReport,
    persist_promotions,
    run_promotion,
)
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH, NARROW_MATCH

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_NCIT_VERSION = "26.02d"
_UBERON_VERSION = "uberon-2026-01"


def _candidate(subject: str, obj: str) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=subject,
        predicate_id=CLOSE_MATCH,
        object_id=obj,
        mapping_justification="semapv:DatabaseCrossReference",
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
        for rid in run_ids:
            await s.execute(
                text("DELETE FROM concept_xref WHERE run_id = :rid"), {"rid": rid}
            )
            await s.execute(text("DELETE FROM xref_run WHERE id = :rid"), {"rid": rid})
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
        [promoted],
        PromotionReport(considered=1, promoted=1, insufficient_evidence=0, refuted=0),
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="promotion",
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
    """The point of the issue: today only the aggregate run metrics can tell a
    curation-alone promotion from a source-agreement one. Now the row itself says.
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
        [curated, source_agree],
        PromotionReport(considered=2, promoted=2, insufficient_evidence=0, refuted=0),
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="promotion",
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
    await xref_store.upsert_records(rid, [_candidate("C3", "UBERON:0000003")])

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
    await xref_store.upsert_records(
        candidate_run, [_candidate("C12468", "UBERON:0002048")]
    )

    report = PromotionReport(
        considered=1, promoted=1, insufficient_evidence=0, refuted=0
    )
    promotion_run = await persist_promotions(
        xref_store,
        [_promoted("C12468", "UBERON:0002048")],
        report,
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="promotion",
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
    await xref_store.upsert_records(
        run_id,
        [_candidate("C12468", "UBERON:0002048"), _promoted("C12393", "UBERON:0001264")],
    )

    candidates = await xref_store.proposed_candidates()
    pairs = {(c.subject_id, c.object_id) for c in candidates}
    assert ("C12468", "UBERON:0002048") in pairs
    assert ("C12393", "UBERON:0001264") not in pairs
    assert all(c.lifecycle_state == "proposed" for c in candidates)


@pytest.mark.integration
async def test_an_endpoint_release_quarantines_stale_bridges(
    store: tuple[XrefStore, list[str]],
) -> None:
    """D29: a bridge validated against an older upstream release is no longer *known*
    good — it is quarantined (not served, not deleted), pending re-validation."""
    xref_store, run_ids = store
    run_id = f"test-stale-{uuid.uuid4().hex}"
    run_ids.append(run_id)

    await xref_store.upsert_run(
        run_id=run_id,
        source="promotion",
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
    )
    await xref_store.upsert_records(
        run_id,
        [
            _promoted("C12377", "UBERON:0002110", object_version="uberon-2025-06"),
            _promoted("C12391", "UBERON:0000945"),
        ],
    )

    quarantined = await xref_store.quarantine_stale(
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="promotion",
    )

    assert quarantined >= 1
    anchors = await xref_store.validated_anchors()
    assert ("C12391", "UBERON:0000945") in anchors
    assert ("C12377", "UBERON:0002110") not in anchors


@pytest.mark.integration
async def test_quarantine_is_scoped_to_its_own_upstream_source(
    store: tuple[XrefStore, list[str]],
) -> None:
    """A Mondo bridge's object_source_version can never equal a Uberon one, so an
    unscoped sweep on a Uberon release would quarantine every Mondo bridge."""
    xref_store, run_ids = store
    mondo_run = f"test-mondo-{uuid.uuid4().hex}"
    run_ids.append(mondo_run)

    await xref_store.upsert_run(
        run_id=mondo_run,
        source="mondo-promotion",
        ncit_version=_NCIT_VERSION,
        source_version="mondo-2026-05",
    )
    await xref_store.upsert_records(
        mondo_run,
        [_promoted("C3262", "UBERON:0002107", object_version="mondo-2026-05")],
    )

    await xref_store.quarantine_stale(
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="promotion",  # a Uberon promotion run
    )

    # the Mondo bridge belongs to another source and must be untouched
    assert ("C3262", "UBERON:0002107") in await xref_store.validated_anchors()


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
    await xref_store.upsert_records(
        run_id,
        [
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

    candidates = await xref_store.proposed_candidates()
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
        [stale_candidate],
        report,
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="promotion",
        run_id=run_id,
    )
    quarantined = await xref_store.quarantine_stale(
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="promotion",
    )

    # the bridge this run just validated must survive its own staleness sweep
    assert ("C12971", "UBERON:0000310") in await xref_store.validated_anchors()
    assert quarantined == 0


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
        [],
        report,
        ncit_version=_NCIT_VERSION,
        source_version=_UBERON_VERSION,
        source="promotion",
        run_id=run_id,
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
    finally:
        await dispose_engine(engine)

    assert row["status"] == "failed"
    assert row["metrics"]["reasoner_errors"] == 2


class _StubClient:
    """Canned SPARQL for the run-level test (the stores are not under test here)."""

    def __init__(self, rows: dict[str, list[dict[str, str]]]) -> None:
        self._rows = rows

    async def select(self, query: str) -> list[dict[str, str | None]]:
        for key, rows in self._rows.items():
            if key in query:
                return [dict(r) for r in rows]  # type: ignore[misc]
        return []


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
    await xref_store.upsert_records(
        ingest_run,
        [
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
        source="test-promotion",
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
