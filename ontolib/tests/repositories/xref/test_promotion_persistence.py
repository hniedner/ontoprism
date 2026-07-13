"""Promotion persistence + D29 lifecycle, against real Postgres (#73).

Integration: the promoted bridge must land as ``exactMatch`` + ``validated`` (which is
what makes it identity-grade for the §13.3 coverage number), the candidate it came from
must survive untouched, and an endpoint version bump must quarantine bridges validated
against the older release rather than keep serving them (D29).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.promotion import PromotionReport, persist_promotions
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
