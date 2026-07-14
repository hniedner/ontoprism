"""Validation-driven promotion: candidate closeMatch → validated exactMatch (#73).

RED first: written before ``repositories/xref/promotion.py``.

This is the correctness core of D28. A candidate is promoted **only** when

1. the evidence for it is independent of the mapping itself (``evidence.py``), and
2. the merged NCIt+upstream fragment carrying the curated ``owl:equivalentClass``
   bridge passes the EL profile + satisfiability gate and classifies (``ELK``).

The reasoner is an *error detector*, never the oracle: structural corroboration is
computed over a merge that **excludes** the candidate bridge, so a mapping can never
corroborate itself.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from rdflib import Graph
from rdflib.namespace import OWL, RDFS

from ontolib.repositories.xref.coverage import (
    CdeAnchor,
    CdeAnchors,
    build_coverage_report,
)
from ontolib.repositories.xref.evidence import (
    LABEL_AGREEMENT,
    SME_CURATION,
    STRUCTURAL_CORROBORATION,
)
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.promotion import (
    CORROBORATED,
    NO_ANCHORED_ANCESTOR,
    NOT_ENTAILED,
    REASON_INSUFFICIENT_EVIDENCE,
    REASON_PROMOTED,
    REASON_REFUTED,
    PromotionContext,
    PromotionEnvironmentError,
    PromotionReport,
    _curation_alone,
    _refuse_degenerate_context,
    build_disjoint_query,
    build_upstream_partof_query,
    corroboration,
    elk_reasoner,
    load_promotion_context,
    parse_inferred_subclasses,
    promote_candidates,
    validate_candidate,
)
from ontolib.repositories.xref.validation import ReasonerUnavailableError
from ontolib.repositories.xref.vocab import (
    CLOSE_MATCH,
    COMPOSITE_MATCHING,
    DATABASE_CROSS_REFERENCE,
    EXACT_MATCH,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from pathlib import Path

_OBO = "http://purl.obolibrary.org/obo/"
_LUNG_IRI = f"{NCIT_NS}C12468"
_UBERON_LUNG_IRI = f"{_OBO}UBERON_0002048"
_UBERON_RESP_IRI = f"{_OBO}UBERON_0001004"
_UBERON_BRAIN_IRI = f"{_OBO}UBERON_0000955"

_NCIT_VERSION = "26.02d"
_UBERON_VERSION = "uberon-2026-01"

# ── an ELK-faithful reasoner double ────────────────────────────────────
#
# ``robot``/ELK is an external process and is not on PATH in the hermetic suite
# (the real thing is exercised by ``test_promotion_with_real_elk``, below, and by
# ``test_validation.py::test_robot_elk_smoke``).
#
# The double must be faithful on the one property the promotion logic leans on:
# ``robot reason`` emits the **direct** subsumptions (the hierarchy is transitively
# reduced), NOT the transitive closure — given ``A ⊑ B`` and ``B ⊑ C`` it does not
# state ``A ⊑ C``.  A double that closed the hierarchy would let a bug through in
# which corroboration only ever finds a *direct* anchored parent, so this one
# deliberately does not close it: the production code must do the walk.


class _ElkLikeReasoner:
    """Return the direct subsumptions of a Turtle fragment, as ROBOT/ELK does."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, ttl: str) -> set[tuple[str, str]]:
        self.seen.append(ttl)
        g = Graph().parse(data=ttl, format="turtle")
        edges: set[tuple[str, str]] = {
            (str(s), str(o)) for s, o in g.subject_objects(RDFS.subClassOf)
        }
        for s, o in g.subject_objects(OWL.equivalentClass):
            edges.add((str(s), str(o)))
            edges.add((str(o), str(s)))
        return edges


class _RefutesTheBridge(_ElkLikeReasoner):
    """Accepts the bridge-free merge; refutes any merge carrying the candidate bridge.

    This is what a real refutation looks like: the anchors and taxonomy are fine, and
    *adding the candidate equivalence* is what forces a class under two disjoint
    parents.
    """

    def __call__(  # type: ignore[override]
        self, ttl: str
    ) -> set[tuple[str, str]] | None:
        edges = super().__call__(ttl)
        bridged = any({a, b} == {_LUNG_IRI, _UBERON_LUNG_IRI} for a, b in edges)
        return None if bridged else edges


def _closure(edges: set[tuple[str, str]]) -> set[tuple[str, str]]:
    closed = set(edges)
    while True:
        grown = closed | {
            (a, d) for a, b in closed for c, d in closed if b == c and a != d
        }
        if grown == closed:
            return closed
        closed = grown


class _SatisfiabilityHonestReasoner(_ElkLikeReasoner):
    """Refutes iff the merge really entails ⊥ — some class inferred under two classes
    asserted ``owl:disjointWith``.

    That is the ONLY refutation rule ELK can apply over the fragment we emit
    (declarations + named subClassOf + named equivalentClass + disjointWith).  An
    earlier double refuted whenever a subject had more than one equivalent — a rule ELK
    does not implement.  It encoded the guard we *wanted* instead of the reasoner's
    behaviour, and so it certified a same-subject guard that did not exist in the code.
    A test double must be at most as strong as the real thing, never stronger.
    """

    def __call__(  # type: ignore[override]
        self, ttl: str
    ) -> set[tuple[str, str]] | None:
        edges = super().__call__(ttl)
        graph = Graph().parse(data=ttl, format="turtle")
        disjoint = {
            (str(a), str(b)) for a, b in graph.subject_objects(OWL.disjointWith)
        }
        if not disjoint:
            return edges

        closure = _closure(edges)
        classes = {c for c, _ in closure} | {p for _, p in closure}
        for cls in classes:
            above = {p for c, p in closure if c == cls} | {cls}
            if any(a in above and b in above for a, b in disjoint):
                return None  # cls sits under two disjoint classes -> unsatisfiable
        return edges


def _unavailable(ttl: str) -> set[tuple[str, str]] | None:
    """A reasoner that cannot run at all (no Java, corrupt jar, OOM, timeout)."""
    raise ReasonerUnavailableError("robot is not on PATH")


def _record(
    subject: str = "C12468",
    obj: str = "UBERON:0002048",
    justification: str = DATABASE_CROSS_REFERENCE,
) -> SSSOMRecord:
    return SSSOMRecord(
        subject_id=subject,
        predicate_id=CLOSE_MATCH,
        object_id=obj,
        mapping_justification=justification,
        confidence=0.9,
        subject_source_version=_NCIT_VERSION,
        object_source_version=_UBERON_VERSION,
    )


def _context(**overrides: Any) -> PromotionContext:
    """NCIt: Lung ⊑ Respiratory System Organ.  Uberon: lung ⊑ respiration organ, which
    is **part_of** respiratory system (BFO:0000050); brain ⊑ nervous system.  Trusted
    anchor: C12366 ≡ UBERON:0001004 (respiratory system).

    This mirrors the **real** store, and that is the point of #78: ``lung
    rdfs:subClassOf* respiratory system`` is *false* on the live Uberon (checked in
    ``test_upstream_data_contract``), so the anchored system is reached only by the
    mixed ``subClassOf`` ∘ ``part_of`` walk — ``lung ⊑* respiration organ`` then
    ``respiration organ part_of respiratory system``.  Neither leg reaches it alone.  An
    earlier fixture modelled the link as ``subClassOf`` and so passed while the real
    pipeline promoted nothing but curated pairs — exactly the fiction a data-shape
    contract exists to kill.
    """
    base: dict[str, Any] = {
        "subject_labels": {"C12468": {"Lung"}},
        "object_labels": {
            "UBERON:0002048": {"lung"},
            "UBERON:0000955": {"brain"},
        },
        "object_xrefs": {"UBERON:0002048": {"C12468"}},
        "ncit_edges": {("C12468", "C12366")},
        "upstream_edges": {
            ("UBERON:0002048", "UBERON:0000171"),  # lung ⊑ respiration organ
            ("UBERON:0000955", "UBERON:0000010"),  # brain ⊑ nervous system
        },
        "upstream_partof_edges": {
            # respiration organ part_of respiratory system — the edge subClassOf lacks
            ("UBERON:0000171", "UBERON:0001004"),
        },
        "anchors": (("C12366", "UBERON:0001004"),),
        "curated_pairs": frozenset(),
    }
    base.update(overrides)
    return PromotionContext(**base)


# ── the happy path: a known-equivalent pair promotes ────────────────────


@pytest.mark.unit
def test_known_equivalent_pair_is_promoted() -> None:
    """Lung ↔ lung: label agreement (independent of the xref that generated it)
    plus structural corroboration through a *separate* anchored parent."""
    outcome = validate_candidate(_record(), _context(), reasoner=_ElkLikeReasoner())

    assert outcome.reason == REASON_PROMOTED
    assert outcome.promoted is not None
    assert outcome.promoted.predicate_id == EXACT_MATCH
    assert outcome.promoted.lifecycle_state == "validated"
    assert {e.kind for e in outcome.evidence} == {
        LABEL_AGREEMENT,
        STRUCTURAL_CORROBORATION,
    }
    # the candidate itself is untouched (immutability)
    assert outcome.record.predicate_id == CLOSE_MATCH
    assert outcome.record.lifecycle_state == "proposed"


@pytest.mark.unit
def test_promoted_record_keeps_its_provenance() -> None:
    outcome = validate_candidate(_record(), _context(), reasoner=_ElkLikeReasoner())
    assert outcome.promoted is not None
    assert outcome.promoted.subject_source_version == _NCIT_VERSION
    assert outcome.promoted.object_source_version == _UBERON_VERSION
    assert outcome.promoted.subject_id == "C12468"
    assert outcome.promoted.object_id == "UBERON:0002048"


# ── the unhappy path: a known non-equivalent pair is not promoted ───────


@pytest.mark.unit
def test_known_non_equivalent_pair_is_not_promoted() -> None:
    """Lung ↔ brain: labels disagree and the upstream object does not sit under the
    upstream image of the subject's anchored parent, so nothing corroborates it."""
    outcome = validate_candidate(
        _record(obj="UBERON:0000955"), _context(), reasoner=_ElkLikeReasoner()
    )

    assert outcome.promoted is None
    assert outcome.reason == REASON_INSUFFICIENT_EVIDENCE
    assert outcome.evidence == ()


@pytest.mark.unit
def test_a_single_signal_does_not_promote() -> None:
    """Label agreement alone (no anchored parent to corroborate) is not enough."""
    outcome = validate_candidate(
        _record(), _context(anchors=()), reasoner=_ElkLikeReasoner()
    )

    assert outcome.promoted is None
    assert outcome.reason == REASON_INSUFFICIENT_EVIDENCE
    assert {e.kind for e in outcome.evidence} == {LABEL_AGREEMENT}


@pytest.mark.unit
def test_sme_curated_pair_promotes_on_curation_alone() -> None:
    outcome = validate_candidate(
        _record(subject="C12377", obj="UBERON:0002110"),
        _context(curated_pairs=frozenset({("C12377", "UBERON:0002110")})),
        reasoner=_ElkLikeReasoner(),
    )

    assert outcome.promoted is not None
    assert {e.kind for e in outcome.evidence} == {SME_CURATION}


@pytest.mark.unit
def test_curated_pair_with_structural_corroboration_is_not_curation_alone() -> None:
    """A curated pair that also has an anchored ancestor is booked as structural
    corroboration, not curation alone — the buckets are mutually exclusive."""
    # Default _context() provides a structural path (C12468→C12366≡UBERON:0001004
    # and lung→respiration organ→respiratory system). Give C12468 a label that
    # does NOT match "lung" to prevent label agreement, producing {SME, STRUCTURAL}.
    promoted, report = promote_candidates(
        [_record()],
        _context(
            curated_pairs=frozenset({("C12468", "UBERON:0002048")}),
            subject_labels={"C12468": {"Pulmonary Organ"}},
        ),
        reasoner=_ElkLikeReasoner(),
    )
    assert len(promoted) == 1
    assert report.promoted_with_structural_corroboration == 1
    assert report.promoted_on_curation_alone == 0


@pytest.mark.unit
def test_curated_pair_with_label_agreement_is_also_curation_alone() -> None:
    """A curated pair whose labels also agree is still booked as curation alone.

    Without this, every curated pair with agreeing labels would be counted as
    ``promoted_on_source_agreement`` — making curation-investment indistinguishable from
    machine-generated corroboration and hiding the fact that the curated portfolio was
    still load-bearing.
    """
    outcome = validate_candidate(
        _record(),
        _no_anchor_context(
            curated_pairs=frozenset({("C12468", "UBERON:0002048")}),
        ),
        reasoner=_ElkLikeReasoner(),
    )

    assert outcome.promoted is not None
    assert SME_CURATION in {e.kind for e in outcome.evidence}
    assert LABEL_AGREEMENT in {e.kind for e in outcome.evidence}
    assert _curation_alone(outcome) is True


@pytest.mark.unit
def test_curated_candidate_is_not_used_as_its_own_anchor() -> None:
    """A curated pair is a trusted anchor for *other* candidates — but when it is
    itself the candidate under test, it must be dropped from the anchor set."""
    pair = ("C12468", "UBERON:0002048")
    outcome = validate_candidate(
        _record(),
        _context(curated_pairs=frozenset({pair}), anchors=(pair,)),
        reasoner=_ElkLikeReasoner(),
    )

    assert outcome.promoted is not None
    assert STRUCTURAL_CORROBORATION not in {e.kind for e in outcome.evidence}


# ── the EL gate ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_candidate_refuted_by_the_reasoner_is_not_promoted() -> None:
    """A bridge the reasoner *refutes* (it forces a class under two disjoint parents)
    is rejected, not force-classified — and no amount of evidence promotes it."""
    outcome = validate_candidate(
        _record(),
        _context(curated_pairs=frozenset({("C12468", "UBERON:0002048")})),
        reasoner=_RefutesTheBridge(),
    )

    assert outcome.promoted is None
    assert outcome.el_valid is False
    assert outcome.reason == REASON_REFUTED


@pytest.mark.unit
def test_a_reasoner_that_cannot_run_is_never_read_as_a_verdict() -> None:
    """An unusable reasoner (no Java, corrupt jar, OOM, timeout) must NOT be laundered
    into 'this candidate did not qualify' — the run is failed, loudly.

    This is the defect that hid three separate bugs in this module's history: a broken
    reasoner call looks exactly like a clean, conservative zero-promotion run.
    """
    _, report = promote_candidates([_record()], _context(), reasoner=_unavailable)

    assert report.as_dict() == {
        "considered": 1,
        "promoted": 0,
        "insufficient_evidence": 0,
        "refuted": 0,
        "reasoner_errors": 1,
        "conflicting_identity": 0,
        "skipped_unexpandable": 0,
        "promoted_on_curation_alone": 0,
        "promoted_with_structural_corroboration": 0,
        "promoted_on_source_agreement": 0,
    }
    assert report.failed is True


@pytest.mark.unit
def test_refuting_the_bridge_free_merge_is_a_data_integrity_alarm() -> None:
    """The bridge-free merge carries no candidate axiom, so it cannot legitimately be
    refuted *about this candidate*.  If the reasoner refutes it, the validated anchors
    contradict the taxonomy — raise, rather than bury it as 'not corroborated'."""

    def _refuse_everything(ttl: str) -> None:
        return None

    with pytest.raises(PromotionEnvironmentError, match="anchors"):
        validate_candidate(_record(), _context(), reasoner=_refuse_everything)


@pytest.mark.unit
def test_a_run_with_no_stated_ncit_edges_refuses_rather_than_reporting_zero() -> None:
    """An unloaded stated graph would fail every candidate for a reason that has nothing
    to do with the candidate — and would read as a conservative zero."""
    with pytest.raises(PromotionEnvironmentError, match="stated"):
        promote_candidates(
            [_record()], _context(ncit_edges=set()), reasoner=_ElkLikeReasoner()
        )


@pytest.mark.unit
def test_refuse_degenerate_context_rejects_empty_records() -> None:
    """An empty records list means ingest hasn't run — refuse rather than report
    a zero that looks like a conservative verdict."""
    ctx = _context()
    with pytest.raises(PromotionEnvironmentError, match="no proposed candidates"):
        _refuse_degenerate_context([], ctx)


@pytest.mark.unit
def test_structural_corroboration_never_sees_the_candidate_bridge() -> None:
    """THE non-circularity test: the merge the reasoner corroborates over must not
    contain owl:equivalentClass(subject, object) — otherwise the candidate proves
    itself and every mapping 'validates'."""
    reasoner = _ElkLikeReasoner()
    validate_candidate(_record(), _context(), reasoner=reasoner)

    corroboration_merge = Graph().parse(data=reasoner.seen[0], format="turtle")
    assert (None, OWL.equivalentClass, None) in corroboration_merge  # anchors present
    assert not [
        t
        for t in corroboration_merge.triples((None, OWL.equivalentClass, None))
        if {str(t[0]), str(t[2])} == {_LUNG_IRI, _UBERON_LUNG_IRI}
    ]


@pytest.mark.unit
def test_structural_corroboration_requires_an_anchored_parent() -> None:
    inferred = {(_UBERON_LUNG_IRI, _UBERON_RESP_IRI)}
    assert (
        corroboration(
            _record(),
            inferred,
            anchors=(),
            ncit_edges={("C12468", "C12366")},
        )
        == NO_ANCHORED_ANCESTOR
    )


@pytest.mark.unit
def test_structural_corroboration_fails_when_the_upstream_parent_disagrees() -> None:
    """Subject's anchored parent maps to 'respiratory system'; the candidate object
    (brain) is not inferred to sit under it → the anchor actively contradicts."""
    inferred = {(_UBERON_BRAIN_IRI, f"{_OBO}UBERON_0000010")}
    assert (
        corroboration(
            _record(obj="UBERON:0000955"),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C12366")},
        )
        == NOT_ENTAILED
    )


@pytest.mark.unit
def test_structural_corroboration_follows_upstream_ancestors() -> None:
    """The anchored class may sit several levels above the object in the *upstream*
    hierarchy.

    ``robot reason`` emits the **direct** subsumptions (transitively reduced): given
    ``lung ⊑ lower respiratory tract`` and ``lower respiratory tract ⊑ respiratory
    system`` it does NOT state ``lung ⊑ respiratory system``.  Corroboration must walk
    the inferred hierarchy, not test it for membership — otherwise only a *direct*
    anchored parent could ever corroborate anything.
    """
    lower_tract = f"{_OBO}UBERON_0001558"
    inferred = {
        (_UBERON_LUNG_IRI, lower_tract),
        (lower_tract, _UBERON_RESP_IRI),
    }
    assert (
        corroboration(
            _record(),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C12366")},
        )
        == CORROBORATED
    )


@pytest.mark.unit
def test_structural_corroboration_follows_ncit_ancestors() -> None:
    """The anchored NCIt class may be a *grand*parent of the subject."""
    inferred = {(_UBERON_LUNG_IRI, _UBERON_RESP_IRI)}
    assert (
        corroboration(
            _record(),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C99999"), ("C99999", "C12366")},
        )
        == CORROBORATED
    )


@pytest.mark.unit
def test_one_entailed_anchor_image_corroborates_despite_another_unentailed() -> None:
    """Open-world: a NON-entailed anchor image means *unknown*, so it cannot cancel a
    different anchored ancestor that IS entailed.

    This test previously pinned the opposite (`all()`), on the reasoning that a
    non-entailed image is "a disagreement between the planes".  That was the veto
    semantics, and it is wrong: on the live store `lung ⊑* organ` is TRUE while
    `lung ⊑* respiratory system` is FALSE (part_of), so `all()` would silently withhold
    the corroboration the *organ* anchor genuinely established, and dump the candidate
    into `insufficient_independent_evidence` — a clean, normal-looking bucket.
    """
    organ = f"{_OBO}UBERON_0000062"
    # entailed under organ, NOT under respiratory system (part_of)
    inferred = {(_UBERON_LUNG_IRI, organ)}

    assert (
        corroboration(
            _record(),
            inferred,
            anchors=(
                ("C12366", "UBERON:0000062"),  # organ — entailed
                ("C12366", "UBERON:0001004"),  # respiratory system — NOT entailed
            ),
            ncit_edges={("C12468", "C12366")},
        )
        == CORROBORATED
    )


@pytest.mark.unit
def test_no_entailed_anchor_image_is_not_entailed_and_grants_no_evidence() -> None:
    """With anchored ancestors but NO entailed image, the verdict is NOT_ENTAILED: it
    grants no evidence — and it vetoes nothing (absence of entailment is not
    contradiction)."""
    inferred = {(_UBERON_LUNG_IRI, f"{_OBO}UBERON_0005178")}

    assert (
        corroboration(
            _record(),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C12366")},
        )
        == NOT_ENTAILED
    )


# ── #78: the mixed subClassOf / part_of walk ────────────────────────────
#
# Uberon relates an organ to its system with part_of, not subClassOf (verified on the
# live store in test_upstream_data_contract). The canonical correct pair therefore
# reaches its anchored system ONLY through the mixed walk, and these tests pin exactly
# that — including gate liveness: the SAME inputs minus the part_of edge must fall back
# to NOT_ENTAILED, so we know the part_of leg is what flips the verdict rather than some
# subClassOf path doing the work.

_RESPIRATION_ORGAN_IRI = f"{_OBO}UBERON_0000171"


@pytest.mark.unit
def test_part_of_edge_is_what_corroborates_the_organ_under_its_system() -> None:
    """lung ⊑* respiration organ (inferred subClassOf), respiration organ part_of
    respiratory system (a part_of edge): the object reaches the anchored system only by
    crossing from subClassOf to part_of mid-walk."""
    inferred = {(_UBERON_LUNG_IRI, _RESPIRATION_ORGAN_IRI)}  # subClassOf, from ELK
    partof = {
        ("UBERON:0000171", "UBERON:0001004")
    }  # respiration organ part_of resp sys

    assert (
        corroboration(
            _record(),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C12366")},
            upstream_partof_edges=partof,
        )
        == CORROBORATED
    )


@pytest.mark.unit
def test_without_the_part_of_edge_the_same_pair_is_not_entailed() -> None:
    """Gate liveness for #78. Identical to the test above but with NO part_of edge: the
    anchored system is now unreachable (subClassOf alone does not get there, which is
    the real store's shape), so the verdict is NOT_ENTAILED.

    If this ever returns CORROBORATED, a subClassOf path is quietly reaching the system
    and the part_of walk is not the thing being exercised — the #78 tests above would be
    passing for the wrong reason.
    """
    inferred = {(_UBERON_LUNG_IRI, _RESPIRATION_ORGAN_IRI)}

    assert (
        corroboration(
            _record(),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C12366")},
            upstream_partof_edges=frozenset(),
        )
        == NOT_ENTAILED
    )


@pytest.mark.unit
def test_part_of_is_transitive_across_a_multi_hop_chain() -> None:
    """The corroboration WALK is transitive over the part_of edges it is given: fed a
    two-hop chain it must follow both hops.

    This guards the walk primitive, not the end-to-end reach.
    `build_upstream_partof_query` only gathers part_of edges one hop off the
    `subClassOf*` cone (see D32), so the *deployed* pipeline rarely supplies a chain
    like this; the canonical organ->system case is a single hop. The walk staying
    transitive is still worth pinning so a future single-hop-only regression in the walk
    fails here.
    """
    inferred: set[tuple[str, str]] = set()  # no subClassOf needed for this one
    partof = {
        ("UBERON:0002048", "UBERON:0001558"),  # lung part_of lower respiratory tract
        ("UBERON:0001558", "UBERON:0001004"),  # lower resp tract part_of resp system
    }

    assert (
        corroboration(
            _record(),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C12366")},
            upstream_partof_edges=partof,
        )
        == CORROBORATED
    )


@pytest.mark.unit
def test_a_run_that_only_imported_curated_pairs_says_so() -> None:
    """A promotion earned by curation ALONE must be distinguishable from one the
    validation machinery earned.

    This is the difference between the feature working and the feature being a file
    importer wearing a reasoner costume — which is what it was for every non-curated
    candidate until #73 Option 1 (D33/D34) made source agreement reachable.  A report
    that cannot tell the two apart publishes a number that means something entirely
    different from what it says, so the split stays whatever the promotion mix becomes.
    """
    curated = _record(subject="C12377", obj="UBERON:0002110")
    promoted, report = promote_candidates(
        [curated],
        _context(curated_pairs=frozenset({("C12377", "UBERON:0002110")})),
        reasoner=_ElkLikeReasoner(),
    )

    assert len(promoted) == 1
    assert report.promoted_on_curation_alone == 1
    assert report.promoted_with_structural_corroboration == 0, (
        "the reasoner, the anchors and the disjointness axioms contributed nothing to "
        "this promotion — the report must not imply otherwise"
    )


# ── source agreement: the promotion path D33/Option 1 opens (D34) ──────


def _no_anchor_context(**overrides: Any) -> PromotionContext:
    """The same two planes, with NO anchors and NO curation.

    Structural corroboration therefore *cannot* fire and curation cannot carry anything:
    whatever promotes here promoted on source agreement alone, so the bucket the report
    credits is not a coincidence of the fixture.
    """
    base: dict[str, Any] = {
        "anchors": (),
        "subject_labels": {"C12468": {"Lung"}, "C12377": {"Pancreas"}},
        "object_labels": {
            "UBERON:0002048": {"lung"},
            "UBERON:0002110": {"pancreas"},
        },
        "object_xrefs": {
            "UBERON:0002048": {"C12468"},
            "UBERON:0002110": {"C12377"},
        },
    }
    base.update(overrides)
    return _context(**base)


@pytest.mark.unit
def test_an_xref_the_labels_agree_with_promotes_on_source_agreement() -> None:
    """GATE LIVENESS (the point of #73 Option 1): two independent sources agreeing is a
    reachable promotion, with no curation and no anchored ancestor anywhere.

    Before this, `promoted_on_source_agreement` could never be non-zero: ingest never
    produced a candidate that could hold both signals, and `gather_evidence` dropped
    whichever one had generated it — so the machinery promoted curated pairs and nothing
    else, on real data.
    """
    promoted, report = promote_candidates(
        [_record(justification=COMPOSITE_MATCHING)],
        _no_anchor_context(),
        reasoner=_ElkLikeReasoner(),
    )

    assert len(promoted) == 1
    assert promoted[0].predicate_id == EXACT_MATCH
    assert promoted[0].lifecycle_state == "validated"
    assert report.promoted == 1
    assert report.promoted_on_source_agreement == 1
    assert report.promoted_on_curation_alone == 0
    assert report.promoted_with_structural_corroboration == 0


@pytest.mark.unit
def test_a_single_source_candidate_still_does_not_promote() -> None:
    """GATE LIVENESS, reject branch: the bar is unchanged for everything else.

    Identical facts to the test above — the labels agree and the upstream object xrefs
    the subject — but the row says only the xref pass produced it, so that signal is its
    origin and cannot also justify it (D28).  One kind of evidence is not two.
    """
    promoted, report = promote_candidates(
        [_record(justification=DATABASE_CROSS_REFERENCE)],
        _no_anchor_context(),
        reasoner=_ElkLikeReasoner(),
    )

    assert promoted == []
    assert report.promoted == 0
    assert report.insufficient_evidence == 1


@pytest.mark.unit
def test_a_composite_row_outranks_a_stale_single_source_row_for_the_same_pair() -> None:
    """A re-ingest leaves BOTH rows in `concept_xref`, and `proposed_candidates` returns
    both — they differ in `mapping_justification`, so `DISTINCT` cannot collapse them.

    Which duplicate survives dedup decides which signals are suppressed as generating,
    and Postgres leaves the tie order unspecified.  If the single-source row wins, the
    pair silently reverts to one evidence kind and never promotes — the bug this issue
    exists to fix, reintroduced through the back door.
    """
    promoted, report = promote_candidates(
        [
            _record(justification=DATABASE_CROSS_REFERENCE),
            _record(justification=COMPOSITE_MATCHING),
        ],
        _no_anchor_context(),
        reasoner=_ElkLikeReasoner(),
    )

    assert report.considered == 1, "the same pair twice is ONE candidate"
    assert len(promoted) == 1
    assert report.promoted_on_source_agreement == 1


@pytest.mark.unit
def test_two_qualifying_candidates_for_one_subject_promote_neither() -> None:
    """Identity must be decided on evidence, never on CURIE sort order.

    Both candidates here are curated, so both qualify. An earlier cut let the FIRST to
    qualify claim the subject — so the winner was whichever CURIE sorted lower, and an
    arbitrary identity was published as exactMatch/validated while
    `conflicting_identity`
    made it look like the ambiguity had been detected and handled. Neither may promote:
    that is what "needs SME adjudication" actually means.
    """
    first = _record(obj="UBERON:0002048")
    second = _record(
        obj="UBERON:0001558"
    )  # same subject, not disjoint — ELK is powerless
    ctx = _context(
        curated_pairs=frozenset(
            {("C12468", "UBERON:0002048"), ("C12468", "UBERON:0001558")}
        ),
    )

    promoted, report = promote_candidates(
        [first, second], ctx, reasoner=_SatisfiabilityHonestReasoner()
    )

    assert promoted == []
    assert report.conflicting_identity == 2
    assert report.promoted == 0


@pytest.mark.unit
def test_stale_anchor_replacement_promotes_alone() -> None:
    """A replacement for a stale bridge promotes when no other candidate claims
    the same subject — the stale anchor's endpoint claim is released."""
    promoted, report = promote_candidates(
        [_record(justification=COMPOSITE_MATCHING)],
        _no_anchor_context(),
        stale_anchors=frozenset({("C12468", "UBERON:0002048")}),
        reasoner=_ElkLikeReasoner(),
    )
    assert len(promoted) == 1
    assert report.promoted == 1
    assert report.conflicting_identity == 0


@pytest.mark.unit
def test_stale_anchor_replacement_does_not_hide_a_separate_contest() -> None:
    """GATE LIVENESS (stale-anchor blind spot): a stale pair's replacement must
    not silently coexist with another candidate for the same endpoint —
    contested-endpoint detection must consider ALL qualified outcomes."""
    promoted, report = promote_candidates(
        [
            _record(justification=COMPOSITE_MATCHING),  # replacement for stale
            _record(  # separate same-subject candidate
                obj="UBERON:0000955",
                justification=COMPOSITE_MATCHING,
            ),
        ],
        _no_anchor_context(
            subject_labels={"C12468": {"Lung", "Brain"}},
            object_labels={
                "UBERON:0002048": {"lung"},
                "UBERON:0000955": {"brain"},
            },
            object_xrefs={
                "UBERON:0002048": {"C12468"},
                "UBERON:0000955": {"C12468"},
            },
        ),
        stale_anchors=frozenset({("C12468", "UBERON:0002048")}),
        reasoner=_ElkLikeReasoner(),
    )
    assert promoted == [], "both must be contested — neither may promote"
    assert report.conflicting_identity == 2
    assert report.promoted == 0


# ── the run: report + the number it moves ──────────────────────────────


@pytest.mark.unit
def test_promotion_report_counts_every_outcome() -> None:
    records = [
        _record(),  # promotes
        # a *different* subject, so this is a plain evidence failure rather than an
        # identity conflict with the promotion above
        _record(subject="C12377", obj="UBERON:0000955"),  # insufficient evidence
    ]
    promoted, report = promote_candidates(
        records, _context(), reasoner=_ElkLikeReasoner()
    )

    assert [r.predicate_id for r in promoted] == [EXACT_MATCH]
    assert report.as_dict() == {
        "considered": 2,
        "promoted": 1,
        "insufficient_evidence": 1,
        "refuted": 0,
        "reasoner_errors": 0,
        "conflicting_identity": 0,
        "skipped_unexpandable": 0,
        "promoted_on_curation_alone": 0,
        "promoted_with_structural_corroboration": 1,
        "promoted_on_source_agreement": 0,
    }
    assert report.failed is False


@pytest.mark.unit
def test_report_separates_refutations_from_weak_evidence() -> None:
    """A curated pair the reasoner refutes is counted as a refutation, not as missing
    evidence — they are different problems with different fixes."""
    _, report = promote_candidates(
        [_record(), _record(obj="UBERON:0000955")],
        _context(curated_pairs=frozenset({("C12468", "UBERON:0002048")})),
        reasoner=_RefutesTheBridge(),
    )

    assert report.as_dict() == {
        "considered": 2,
        "promoted": 0,
        "insufficient_evidence": 1,
        "refuted": 1,
        "reasoner_errors": 0,
        "conflicting_identity": 0,
        "skipped_unexpandable": 0,
        "promoted_on_curation_alone": 0,
        "promoted_with_structural_corroboration": 0,
        "promoted_on_source_agreement": 0,
    }


@pytest.mark.unit
def test_report_rejects_overlapping_sub_buckets() -> None:
    """The sub-bucket invariant catches a future miscount before it publishes."""
    with pytest.raises(ValueError, match="promoted sub-buckets"):
        PromotionReport(
            considered=5,
            promoted=3,
            insufficient_evidence=1,
            refuted=1,
            promoted_on_curation_alone=2,
            promoted_with_structural_corroboration=2,
        )


@pytest.mark.unit
def test_promotion_moves_the_published_cadsr_coverage_number() -> None:
    """The whole point of #73: COV (§13.3) is pinned at 0 while every mapping is a
    proposed closeMatch, and rises exactly when a subject's mapping is promoted."""
    cde = CdeAnchors(
        public_id="2001234",
        version="1.0",
        anchors=(
            CdeAnchor(concept_code="C12468", concept_type="DEC", is_primary=True),
        ),
    )
    anchor_map = {("2001234", "1.0"): cde}
    live = {"C12468": "live"}

    before = build_coverage_report(
        anchor_map,
        live_status=live,
        strength_by_subject={"C12468": {(CLOSE_MATCH, "proposed")}},
        role_codes=frozenset({"C12468"}),
    )
    assert before.cde_coverage == 0.0
    assert before.anchors_identity_mapped == 0

    promoted, _ = promote_candidates(
        [_record()], _context(), reasoner=_ElkLikeReasoner()
    )
    strength = {r.subject_id: {(r.predicate_id, r.lifecycle_state)} for r in promoted}
    after = build_coverage_report(
        anchor_map,
        live_status=live,
        strength_by_subject=strength,
        role_codes=frozenset({"C12468"}),
    )

    assert after.cde_coverage == 1.0
    assert after.anchors_identity_mapped == 1


# ── the real reasoner boundary ─────────────────────────────────────────


@pytest.mark.unit
def test_parse_inferred_subclasses_reads_named_class_edges(tmp_path: Path) -> None:
    """Blank-node (restriction) superclasses and owl:Thing are not subsumption
    evidence we act on — only named-class edges come back."""
    inferred = tmp_path / "inferred.ttl"
    inferred.write_text(
        f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{_UBERON_LUNG_IRI}> a owl:Class ; rdfs:subClassOf <{_UBERON_RESP_IRI}> .
<{_UBERON_RESP_IRI}> a owl:Class ; rdfs:subClassOf owl:Thing ;
    owl:equivalentClass <{NCIT_NS}C12366> .
<{_LUNG_IRI}> a owl:Class ; rdfs:subClassOf [ a owl:Restriction ] .
"""
    )

    # owl:Thing and the blank-node restriction carry no signal; an equivalence
    # (a trusted anchor bridge) subsumes both ways, so it contributes both edges.
    assert parse_inferred_subclasses(inferred) == {
        (_UBERON_LUNG_IRI, _UBERON_RESP_IRI),
        (_UBERON_RESP_IRI, f"{NCIT_NS}C12366"),
        (f"{NCIT_NS}C12366", _UBERON_RESP_IRI),
    }


@pytest.mark.unit
@patch("ontolib.repositories.xref.promotion.validate_and_classify")
def test_elk_reasoner_returns_none_when_the_el_gate_rejects(
    mock_validate: MagicMock,
) -> None:
    mock_validate.return_value = None
    assert elk_reasoner("<urn:a> a <urn:C> .") is None


@pytest.mark.unit
@patch("ontolib.repositories.xref.promotion.validate_and_classify")
def test_elk_reasoner_tolerates_robots_redundancy_removal(
    mock_validate: MagicMock, tmp_path: Path
) -> None:
    """`robot reason` REMOVES asserted subclass axioms its inferences make redundant
    (`--remove-redundant-subclass-axioms` defaults to true).  So a stated edge can be
    legitimately absent from the output as a direct triple while still being entailed.

    The "did ROBOT actually classify this?" check must therefore test *reachability*,
    not literal set inclusion — real ELK rejected the stricter version, and a false
    alarm here would fail an entire sound run.
    """
    merged = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{_UBERON_LUNG_IRI}> a owl:Class ; rdfs:subClassOf <{_UBERON_RESP_IRI}> .
<{_UBERON_RESP_IRI}> a owl:Class .
"""
    # ROBOT's output routes the stated edge through an intermediate and drops the now
    # redundant direct triple. The stated subsumption still HOLDS.
    intermediate = f"{_OBO}UBERON_0001558"
    inferred = tmp_path / "inferred.ttl"
    inferred.write_text(
        f"""@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{_UBERON_LUNG_IRI}> rdfs:subClassOf <{intermediate}> .
<{intermediate}> rdfs:subClassOf <{_UBERON_RESP_IRI}> .
"""
    )
    mock_validate.return_value = inferred

    entailed = elk_reasoner(merged)
    assert entailed is not None
    assert (_UBERON_LUNG_IRI, intermediate) in entailed


@pytest.mark.unit
@patch("ontolib.repositories.xref.promotion.validate_and_classify")
def test_elk_reasoner_rejects_an_output_that_lost_the_merge(
    mock_validate: MagicMock, tmp_path: Path
) -> None:
    """ROBOT exiting 0 with an output that does not entail what it was given means it
    did not classify our merge.  Returning that empty set would be `is not None` → the
    EL gate reads `el_valid=True` → the candidate is PROMOTED on a classification that
    never happened.  A false positive, not a conservative failure."""
    merged = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{_UBERON_LUNG_IRI}> a owl:Class ; rdfs:subClassOf <{_UBERON_RESP_IRI}> .
"""
    empty = tmp_path / "inferred.ttl"
    empty.write_text("@prefix owl: <http://www.w3.org/2002/07/owl#> .\n")
    mock_validate.return_value = empty

    with pytest.raises(ReasonerUnavailableError, match="not entailed"):
        elk_reasoner(merged)


@pytest.mark.unit
@patch("ontolib.repositories.xref.promotion.validate_and_classify")
def test_elk_reasoner_propagates_an_unusable_reasoner(
    mock_validate: MagicMock,
) -> None:
    """An unusable reasoner must NOT come back as None: None means 'refuted', and an
    environment failure is not a verdict about the merge."""
    mock_validate.side_effect = ReasonerUnavailableError("java not found")
    with pytest.raises(ReasonerUnavailableError):
        elk_reasoner("<urn:a> a <urn:C> .")


@pytest.mark.unit
@patch("ontolib.repositories.xref.promotion.validate_and_classify")
def test_elk_reasoner_parses_the_inferred_hierarchy(
    mock_validate: MagicMock, tmp_path: Path
) -> None:
    inferred = tmp_path / "inferred.ttl"
    inferred.write_text(
        f"""@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{_UBERON_LUNG_IRI}> rdfs:subClassOf <{_UBERON_RESP_IRI}> .
"""
    )
    mock_validate.return_value = inferred

    assert elk_reasoner("<urn:a> a <urn:C> .") == {(_UBERON_LUNG_IRI, _UBERON_RESP_IRI)}


# ── context loading (SPARQL → facts) ───────────────────────────────────


class _MockClient:
    """Canned SPARQL results, matched against the query by containment."""

    def __init__(self, responses: dict[str, list[dict[str, str]]]) -> None:
        self._responses = responses
        self.queries: list[str] = []

    async def select(self, query: str) -> list[dict[str, str | None]]:
        self.queries.append(query)
        for key, rows in self._responses.items():
            if key in query:
                return [dict(r) for r in rows]  # type: ignore[misc]
        return []


@pytest.mark.unit
async def test_load_promotion_context_gathers_both_planes() -> None:
    ncit = _MockClient(
        {
            "disjointWith": [],  # NCIt ships essentially none — see the assertion below
            "?parent": [{"child": f"{NCIT_NS}C12468", "parent": f"{NCIT_NS}C12366"}],
            "rdfs:label": [{"code": "C12468", "label": "Lung"}],
        }
    )
    uberon = _MockClient(
        {
            # `onProperty` appears ONLY in the part_of query, and is matched before
            # `?parent` (insertion order) so the two edge queries route to different
            # canned rows — otherwise the mock cannot tell subClassOf from part_of and
            # part_of loading would be untested.
            "onProperty": [
                {"child": f"{_OBO}UBERON_0000171", "parent": _UBERON_RESP_IRI},
            ],
            # the junk rows are real: an upstream store carries IRIs that are not OBO
            # class IRIs at all.  They must be skipped, never crash the run.
            "?parent": [
                {"child": _UBERON_LUNG_IRI, "parent": _UBERON_RESP_IRI},
                {"child": "http://example.org/not-obo", "parent": _UBERON_RESP_IRI},
                {"child": f"{_OBO}no-underscore", "parent": _UBERON_RESP_IRI},
            ],
            "disjointWith": [
                {"left": _UBERON_RESP_IRI, "right": f"{_OBO}UBERON_0000010"}
            ],
            "hasDbXref": [
                {"upstream": _UBERON_LUNG_IRI, "xref": "NCIT:C12468"},
                {"upstream": _UBERON_LUNG_IRI, "xref": "UMLS:C0024109"},
                {"upstream": "http://example.org/not-obo", "xref": "NCIT:C99999"},
            ],
            "rdfs:label": [
                {"concept": _UBERON_LUNG_IRI, "label": "lung"},
                {"concept": "http://example.org/not-obo", "label": "junk"},
            ],
        }
    )

    ctx = await load_promotion_context(
        ncit,  # type: ignore[arg-type]
        uberon,  # type: ignore[arg-type]
        [_record()],
        curated_pairs=frozenset({("C12377", "UBERON:0002110")}),
        validated_anchors=(("C12366", "UBERON:0001004"),),
    )

    assert ctx.subject_labels == {"C12468": {"Lung"}}
    assert ctx.object_labels == {"UBERON:0002048": {"lung"}}
    assert ctx.object_xrefs == {"UBERON:0002048": {"C12468"}}
    assert ctx.ncit_edges == {("C12468", "C12366")}
    assert ctx.upstream_edges == {("UBERON:0002048", "UBERON:0001004")}
    # part_of is loaded from a DISTINCT query and lands in its own field — the junk-row
    # filtering applies here too (only expandable OBO CURIEs survive).
    assert ctx.upstream_partof_edges == {("UBERON:0000171", "UBERON:0001004")}
    # curated pairs bootstrap the anchor set alongside already-validated bridges
    assert set(ctx.anchors) == {
        ("C12366", "UBERON:0001004"),
        ("C12377", "UBERON:0002110"),
    }
    # The disjointness axioms are the reasoner's ONLY refutation power: if this link of
    # the chain silently returns nothing, every merge is trivially satisfiable and the
    # satisfiability gate is decorative again — the exact bug this PR was fixing.
    assert ctx.disjoints == ((_UBERON_RESP_IRI, f"{_OBO}UBERON_0000010"),)


@pytest.mark.unit
async def test_load_promotion_context_warns_when_a_signal_is_absent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both silent-failure guards must actually fire, not just exist.

    A run with candidates but no ``owl:disjointWith`` (the reasoner's only refutation
    power) or no ``part_of`` edges (anatomy's only structural signal) still exits 0 with
    ``promoted: 0`` — which reads as a conservative verdict. The warnings are what make
    that state visible, so prove their branches are reachable on realistic input.
    """
    ncit = _MockClient(
        {
            "?parent": [{"child": f"{NCIT_NS}C12468", "parent": f"{NCIT_NS}C12366"}],
            "rdfs:label": [{"code": "C12468", "label": "Lung"}],
        }
    )
    # No `onProperty` rows (⇒ no part_of edges) and no `disjointWith` rows: both guards
    # should trip. subClassOf edges still load, so the context is not degenerate.
    uberon = _MockClient(
        {
            "onProperty": [],  # the part_of query matches here first ⇒ no part_of edges
            "?parent": [{"child": _UBERON_LUNG_IRI, "parent": _UBERON_RESP_IRI}],
            "rdfs:label": [{"concept": _UBERON_LUNG_IRI, "label": "lung"}],
            "hasDbXref": [{"upstream": _UBERON_LUNG_IRI, "xref": "NCIT:C12468"}],
        }
    )

    with caplog.at_level("WARNING"):
        ctx = await load_promotion_context(
            ncit,  # type: ignore[arg-type]
            uberon,  # type: ignore[arg-type]
            [_record()],
            curated_pairs=frozenset(),
            validated_anchors=(("C12366", "UBERON:0001004"),),
        )

    assert ctx.upstream_partof_edges == set()
    assert ctx.disjoints == ()
    assert any("no owl:disjointWith axioms loaded" in m for m in caplog.messages)
    assert any("no upstream part_of" in m for m in caplog.messages)


@pytest.mark.unit
def test_the_ncit_disjoint_query_reads_the_stated_graph() -> None:
    """NCIt's default graph is the *inferred* build.  Reading the reasoner's refutation
    power out of a shipped inferred graph is what D21 forbids."""
    query = build_disjoint_query(base_iri=NCIT_NS, graph_iri=STATED_GRAPH_IRI)
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in query
    assert "owl:disjointWith" in query

    # the upstream store has no named-graph split, so it is queried unscoped
    assert "GRAPH" not in build_disjoint_query(base_iri=_OBO)


@pytest.mark.unit
def test_the_part_of_query_targets_the_bfo_part_of_property() -> None:
    """A typo in the property IRI would make the query return nothing — and an empty
    result is indistinguishable from "this organ genuinely has no part_of", so
    structural corroboration would silently die with no error. Pin the restriction
    pattern.

    The behavioural counterpart (that this query returns the real organ->system edge)
    lives in ``test_upstream_data_contract`` against the live store; this unit test just
    guards the query text so a rename fails in the fast suite too.
    """
    query = build_upstream_partof_query(["UBERON:0002048"])
    assert "<http://purl.obolibrary.org/obo/BFO_0000050>" in query
    assert "owl:onProperty" in query
    assert "owl:someValuesFrom" in query
    # both ends filtered to expandable prefixes (an unexpandable filler aborts the run)
    assert 'STRSTARTS(STR(?child), "http://purl.obolibrary.org/obo/UBERON_")' in query
    assert 'STRSTARTS(STR(?parent), "http://purl.obolibrary.org/obo/UBERON_")' in query


# ── integration: the real ELK ──────────────────────────────────────────


@pytest.mark.integration
def test_real_elk_refutes_a_bridge_that_violates_disjointness() -> None:
    """The satisfiability gate, proven against the real reasoner — and it must be the
    REASONER that stops this bridge, not an earlier gate.

    The candidate (NCIt Lung -> Uberon brain) is curated, so it clears the evidence bar
    on SME curation alone, and its subject has **no anchored ancestor**, so structural
    corroboration has no opinion and cannot veto it.  The only thing left that can
    refute it is ELK:

        NCIt:   Lung ⊑ Respiratory-System-Organ
                Respiratory-System-Organ  disjointWith  Nervous-System
        anchor: Nervous-System ≡ UBERON:nervous system  (a SEPARATE validated bridge)
        Uberon: brain ⊑ UBERON:nervous system
        bridge: Lung ≡ brain
             ⟹  Lung ⊑ Nervous-System ⊓ Respiratory-System-Organ  ⟹  ⊥

    This exercises both round-4 fixes at once: the anchor is on the *object's* cone, not
    an ancestor of the subject, so a one-sided anchor filter would drop it and the merge
    could never be refuted — and the anchor's ancestor edges must actually have been
    fetched, or its upstream image is a parentless floating class ELK cannot walk from.
    """
    if shutil.which("robot") is None:
        pytest.skip("robot not on PATH")

    ncit_nervous = "C12755"
    uberon_nervous = f"{_OBO}UBERON_0000010"
    bad = _record(obj="UBERON:0000955")  # brain

    ctx = _context(
        ncit_edges={("C12468", "C12366")},
        upstream_edges={("UBERON:0000955", "UBERON:0000010")},
        anchors=((ncit_nervous, "UBERON:0000010"),),
        disjoints=((f"{NCIT_NS}C12366", f"{NCIT_NS}{ncit_nervous}"),),
        curated_pairs=frozenset({(bad.subject_id, bad.object_id)}),
    )

    # the subject has no anchored ancestor, so corroboration cannot veto it …
    assert (
        corroboration(
            bad,
            {(f"{_OBO}UBERON_0000955", uberon_nervous)},
            anchors=ctx.anchors,
            ncit_edges=ctx.ncit_edges,
        )
        == NO_ANCHORED_ANCESTOR
    )

    # … so the refutation below is ELK's, and nothing else's.
    outcome = validate_candidate(bad, ctx, reasoner=elk_reasoner)

    assert outcome.el_valid is False
    assert outcome.promoted is None
    assert outcome.reason == REASON_REFUTED


@pytest.mark.integration
def test_promotion_with_real_elk() -> None:
    """End-to-end through ROBOT + ELK: the golden Lung pair promotes, the Lung↔brain
    pair does not.  Skipped where ``robot`` is not installed (see DATA_SETUP.md)."""
    if shutil.which("robot") is None:
        pytest.skip("robot not on PATH")

    good = validate_candidate(_record(), _context(), reasoner=elk_reasoner)
    bad = validate_candidate(
        _record(obj="UBERON:0000955"), _context(), reasoner=elk_reasoner
    )

    assert good.promoted is not None
    assert good.promoted.predicate_id == EXACT_MATCH
    assert good.el_valid is True
    assert bad.promoted is None
