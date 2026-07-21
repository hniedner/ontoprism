"""Independent-evidence policy for promotion (#73, D28 non-circularity).

RED first: every test here was written before ``repositories/xref/evidence.py``.

The load-bearing rule: **a mapping may never be its own evidence.** That has two
teeth here — a SKOS mapping annotation is not admissible evidence at all, and the
signal that *generated* a candidate (its ``mapping_justification``) may not be
recycled as the evidence that promotes it.
"""

from __future__ import annotations

import pytest

from ontolib.repositories.xref.evidence import (
    LABEL_AGREEMENT,
    SME_CURATION,
    STRUCTURAL_CORROBORATION,
    XREF_ASSERTION,
    Evidence,
    gather_evidence,
    is_independent,
)
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.vocab import (
    BROAD_MATCH,
    CLOSE_MATCH,
    COMPOSITE_MATCHING,
    DATABASE_CROSS_REFERENCE,
    EXACT_MATCH,
    LEXICAL_MATCHING,
    NARROW_MATCH,
    RELATED_MATCH,
)


def _record(justification: str) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id="C12468",
        predicate_id=CLOSE_MATCH,
        object_id="UBERON:0002048",
        mapping_justification=justification,
        confidence=0.9,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
    )


# ── non-circularity: a SKOS annotation is never evidence ───────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "skos_iri", [EXACT_MATCH, CLOSE_MATCH, BROAD_MATCH, NARROW_MATCH, RELATED_MATCH]
)
def test_skos_mapping_iri_is_never_admissible_as_evidence(skos_iri: str) -> None:
    """Any skos:*Match IRI as an evidence source is rejected at construction."""
    with pytest.raises(ValueError, match="SKOS"):
        Evidence(kind=LABEL_AGREEMENT, source=skos_iri)


@pytest.mark.unit
def test_skos_mapping_iri_is_never_admissible_as_evidence_detail() -> None:
    """The SKOS ban covers the detail field too — no smuggling it through."""
    with pytest.raises(ValueError, match="SKOS"):
        Evidence(kind=LABEL_AGREEMENT, source="ncit:rdfs:label", detail=EXACT_MATCH)


@pytest.mark.unit
@pytest.mark.parametrize(
    "curie",
    ["skos:exactMatch", "skos:closeMatch", "skos:broadMatch", "skos:relatedMatch"],
)
def test_skos_mapping_curie_is_also_rejected(curie: str) -> None:
    """The guard must reject the CURIE spelling, not just the full IRI.

    Every ``source`` this module mints is CURIE-form (``rdfs:label``,
    ``oboInOwl:hasDbXref``), so a guard that only knew the full IRI would reject a form
    nobody writes while admitting the one they would — protection in name only.
    """
    with pytest.raises(ValueError, match="SKOS"):
        Evidence(kind=LABEL_AGREEMENT, source=curie)


@pytest.mark.unit
def test_the_rejection_names_the_offending_field() -> None:
    """A SKOS value in `detail` must be named in the error, not shadowed by `source`."""
    with pytest.raises(ValueError, match="skos:exactMatch"):
        Evidence(kind=LABEL_AGREEMENT, source="rdfs:label", detail="skos:exactMatch")


@pytest.mark.unit
def test_evidence_serializes_to_a_jsonb_ready_dict() -> None:
    """`as_dict` is what `upsert_records` writes to the `evidence` jsonb column."""
    ev = Evidence(kind=LABEL_AGREEMENT, source="rdfs:label", detail="lung")
    assert ev.as_dict() == {
        "kind": LABEL_AGREEMENT,
        "source": "rdfs:label",
        "detail": "lung",
    }


@pytest.mark.unit
def test_unknown_evidence_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="kind"):
        Evidence(kind="vibes", source="ncit:rdfs:label")


@pytest.mark.unit
def test_evidence_source_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="source"):
        Evidence(kind=LABEL_AGREEMENT, source="")


# ── non-circularity: the generating signal is not its own evidence ─────


@pytest.mark.unit
def test_xref_derived_candidate_does_not_count_its_own_xref_as_evidence() -> None:
    """An xref-generated candidate may not be promoted by that same xref."""
    evidence = gather_evidence(
        _record(DATABASE_CROSS_REFERENCE),
        subject_labels={"Lung"},
        object_labels={"lung"},
        object_xref_codes={"C12468"},  # the very xref that generated the candidate
        curated_pairs=frozenset(),
        structurally_corroborated=False,
    )
    kinds = {e.kind for e in evidence}
    assert XREF_ASSERTION not in kinds
    assert kinds == {LABEL_AGREEMENT}


@pytest.mark.unit
def test_lexically_derived_candidate_does_not_count_its_own_label_as_evidence() -> None:
    """A lexically-generated candidate may not be promoted by that same label match."""
    evidence = gather_evidence(
        _record(LEXICAL_MATCHING),
        subject_labels={"Lung"},
        object_labels={"lung"},  # the very label match that generated the candidate
        object_xref_codes={"C12468"},
        curated_pairs=frozenset(),
        structurally_corroborated=False,
    )
    kinds = {e.kind for e in evidence}
    assert LABEL_AGREEMENT not in kinds
    assert kinds == {XREF_ASSERTION}


# ── two generating signals corroborate each other (D34, #73 Option 1) ──


@pytest.mark.unit
def test_a_composite_candidate_counts_both_of_the_signals_that_generated_it() -> None:
    """Ingest mints a composite ONLY when both passes independently produced the pair.

    Dropping both origins would leave it with no evidence at all — making the strongest
    candidates (an OBO curator asserted the xref AND the names agree) the least
    promotable, which is the opposite of what D28 is for.  Nothing is recycled as its
    own evidence here: the xref corroborates the lexically-derived candidate, and the
    label match corroborates the xref-derived one.
    """
    evidence = gather_evidence(
        _record(COMPOSITE_MATCHING),
        subject_labels={"Lung"},
        object_labels={"lung"},
        object_xref_codes={"C12468"},
        curated_pairs=frozenset(),
        structurally_corroborated=False,
    )

    assert {e.kind for e in evidence} == {LABEL_AGREEMENT, XREF_ASSERTION}
    assert is_independent(evidence) is True


@pytest.mark.unit
def test_a_composite_justification_is_not_a_promotion_token() -> None:
    """Every signal is re-derived from the store, never taken on the record's word.

    A composite row asserts "two passes agreed *at ingest time*".  If an NCIt release
    renames the concept the label agreement is simply gone, and the pair must fall back
    to one signal and stop promoting rather than ride on a justification string.
    """
    evidence = gather_evidence(
        _record(COMPOSITE_MATCHING),
        subject_labels={"Pulmonary Organ"},  # the release renamed it
        object_labels={"lung"},
        object_xref_codes={"C12468"},
        curated_pairs=frozenset(),
        structurally_corroborated=False,
    )

    assert {e.kind for e in evidence} == {XREF_ASSERTION}
    assert is_independent(evidence) is False


# ── evidence gathering ─────────────────────────────────────────────────


@pytest.mark.unit
def test_label_agreement_is_case_folded() -> None:
    evidence = gather_evidence(
        _record(DATABASE_CROSS_REFERENCE),
        subject_labels={"LUNG"},
        object_labels={"lung"},
        object_xref_codes=set(),
        curated_pairs=frozenset(),
        structurally_corroborated=False,
    )
    assert [e.kind for e in evidence] == [LABEL_AGREEMENT]


@pytest.mark.unit
def test_disagreeing_labels_yield_no_label_evidence() -> None:
    evidence = gather_evidence(
        _record(DATABASE_CROSS_REFERENCE),
        subject_labels={"Lung"},
        object_labels={"brain"},
        object_xref_codes=set(),
        curated_pairs=frozenset(),
        structurally_corroborated=False,
    )
    assert evidence == []


@pytest.mark.unit
def test_curated_pair_yields_sme_evidence() -> None:
    evidence = gather_evidence(
        _record(DATABASE_CROSS_REFERENCE),
        subject_labels=set(),
        object_labels=set(),
        object_xref_codes=set(),
        curated_pairs=frozenset({("C12468", "UBERON:0002048")}),
        structurally_corroborated=False,
    )
    assert [e.kind for e in evidence] == [SME_CURATION]


@pytest.mark.unit
def test_curated_pair_for_a_different_object_is_not_evidence() -> None:
    evidence = gather_evidence(
        _record(DATABASE_CROSS_REFERENCE),
        subject_labels=set(),
        object_labels=set(),
        object_xref_codes=set(),
        curated_pairs=frozenset({("C12468", "UBERON:0000955")}),
        structurally_corroborated=False,
    )
    assert evidence == []


@pytest.mark.unit
def test_structural_corroboration_is_recorded_as_evidence() -> None:
    evidence = gather_evidence(
        _record(DATABASE_CROSS_REFERENCE),
        subject_labels=set(),
        object_labels=set(),
        object_xref_codes=set(),
        curated_pairs=frozenset(),
        structurally_corroborated=True,
    )
    assert [e.kind for e in evidence] == [STRUCTURAL_CORROBORATION]


# ── the policy itself ──────────────────────────────────────────────────


@pytest.mark.unit
def test_no_evidence_is_not_independent() -> None:
    assert is_independent([]) is False


@pytest.mark.unit
def test_a_single_signal_is_not_independent() -> None:
    """One corroborating signal is never enough (D28: 'a single SKOS annotation
    is never sufficient' — and neither is a single lexical hit)."""
    assert (
        is_independent([Evidence(kind=LABEL_AGREEMENT, source="ncit:label")]) is False
    )


@pytest.mark.unit
def test_two_distinct_signals_are_independent() -> None:
    assert (
        is_independent(
            [
                Evidence(kind=LABEL_AGREEMENT, source="ncit:label"),
                Evidence(kind=STRUCTURAL_CORROBORATION, source="elk:anchored-parent"),
            ]
        )
        is True
    )


@pytest.mark.unit
def test_the_same_signal_twice_is_not_independent() -> None:
    """Two label hits are one signal, not two — independence counts *kinds*."""
    assert (
        is_independent(
            [
                Evidence(kind=LABEL_AGREEMENT, source="ncit:label"),
                Evidence(kind=LABEL_AGREEMENT, source="uberon:exact-synonym"),
            ]
        )
        is False
    )


@pytest.mark.unit
def test_sme_curation_alone_is_independent() -> None:
    """Human curation is sufficient on its own (D28)."""
    assert (
        is_independent([Evidence(kind=SME_CURATION, source="golden/mappings.json")])
        is True
    )


# ── fail closed on an unrecognised generating signal ───────────────────


@pytest.mark.unit
def test_unknown_mapping_justification_refuses_to_gather_evidence() -> None:
    """If we cannot tell which signal produced the candidate, we cannot drop it — and
    the xref that generated the mapping would then count as independent evidence *for*
    that mapping.  That is the circularity D28 forbids, so fail closed, not open.

    ``mapping_justification`` is free text in the DB with no CHECK constraint, so this
    is one ingested row away from being live.
    """
    record = SSSOMRecord(
        subject_id="C12468",
        predicate_id=CLOSE_MATCH,
        object_id="UBERON:0002048",
        mapping_justification="semapv:SomeFutureMatching",
        confidence=0.9,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
    )
    with pytest.raises(ValueError, match="generating signal"):
        gather_evidence(
            record,
            subject_labels={"Lung"},
            object_labels={"lung"},
            object_xref_codes={"C12468"},
            curated_pairs=frozenset(),
            structurally_corroborated=False,
        )
