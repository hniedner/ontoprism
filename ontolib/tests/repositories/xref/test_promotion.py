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
    build_disjoint_query,
    corroboration,
    elk_reasoner,
    load_promotion_context,
    parse_inferred_subclasses,
    promote_candidates,
    validate_candidate,
)
from ontolib.repositories.xref.validation import ReasonerUnavailableError
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH
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

_XREF = "semapv:DatabaseCrossReference"
_LEXICAL = "semapv:LexicalMatching"


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
    justification: str = _XREF,
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
    """NCIt: Lung ⊑ Respiratory System Organ.  Uberon: lung ⊑ lower respiratory tract
    ⊑ respiratory system; brain ⊑ nervous system.  Trusted anchor: C12366 ≡
    UBERON:0001004.

    The upstream chain is deliberately **two** levels deep: the anchored class is not
    the object's direct parent, so corroboration only succeeds if the inferred
    hierarchy is walked.  ROBOT transitively reduces its output, so a depth-1 fixture
    would pass even with the membership-test bug this guards against.
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
            ("UBERON:0002048", "UBERON:0001558"),
            ("UBERON:0001558", "UBERON:0001004"),
            ("UBERON:0000955", "UBERON:0000010"),
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
def test_a_disagreeing_anchor_image_is_not_silently_dropped() -> None:
    """An NCIt class may carry more than one validated upstream image.  Collapsing the
    anchors with ``dict()`` would keep only the last, so a *refuting* image could be
    silently discarded and the candidate corroborated anyway — decided by dict ordering.
    Every image of every anchored ancestor must hold.
    """
    inferred = {(_UBERON_LUNG_IRI, _UBERON_RESP_IRI)}
    assert (
        corroboration(
            _record(),
            inferred,
            # C12366 is anchored to BOTH: the object sits under the first, but
            # demonstrably not under the second.  That is a disagreement.
            anchors=(
                ("C12366", "UBERON:0001004"),
                ("C12366", "UBERON:0000955"),
            ),
            ncit_edges={("C12468", "C12366")},
        )
        == NOT_ENTAILED
    )


@pytest.mark.unit
def test_an_empty_upstream_plane_refuses_rather_than_reporting_zero() -> None:
    """The degenerate-context guard must be SYMMETRIC.  An unloaded Uberon store is if
    anything the likelier misconfiguration (every other build step exercises the NCIt
    endpoint; only ingest and this pass touch the upstream one), and it produces the
    same clean `promoted: 0, completed, exit 0` that reads as a conservative verdict."""
    with pytest.raises(PromotionEnvironmentError, match="upstream"):
        promote_candidates(
            [_record()], _context(upstream_edges=set()), reasoner=_ElkLikeReasoner()
        )


@pytest.mark.unit
def test_the_el_gate_is_not_run_when_the_evidence_already_fails() -> None:
    """`el_valid is None` means the gate never ran — distinct from False ('refuted').

    Collapsing them would recreate, inside the outcome type, the very conflation this
    module exists to forbid: a consumer counting `not el_valid` as reasoner rejections
    would include every candidate the reasoner never looked at.  It also means we do not
    pay two JVM launches to learn something we cannot act on.
    """
    reasoner = _ElkLikeReasoner()
    outcome = validate_candidate(
        _record(obj="UBERON:0000955"), _context(), reasoner=reasoner
    )

    assert outcome.reason == REASON_INSUFFICIENT_EVIDENCE
    assert outcome.el_valid is None
    assert reasoner.seen == []  # the reasoner was never invoked at all


@pytest.mark.unit
def test_a_run_never_asserts_two_identities_for_one_subject() -> None:
    """Ingest legitimately yields several upstream candidates for one NCIt code.
    Promoting two asserts C ≡ U1 and C ≡ U2 — hence U1 ≡ U2, an equivalence nobody
    curated and no reasoner saw.

    This must be enforced STRUCTURALLY, not left to the reasoner: ELK only objects if U1
    and U2 are provably disjoint, and the commonest ingest ambiguity (two terms in the
    same Uberon branch, e.g. lung and lower respiratory tract) never is.  Note the pair
    below is deliberately NOT disjoint — a refutation-only oracle is powerless here, and
    a satisfiability-honest reasoner will happily accept both.
    """
    first = _record(obj="UBERON:0002048")  # lung
    second = _record(obj="UBERON:0001558")  # lower respiratory tract — NOT disjoint
    ctx = _context(
        # both curated, so both would otherwise promote on SME evidence alone
        curated_pairs=frozenset(
            {("C12468", "UBERON:0002048"), ("C12468", "UBERON:0001558")}
        ),
    )

    promoted, report = promote_candidates(
        [first, second], ctx, reasoner=_SatisfiabilityHonestReasoner()
    )

    assert [r.object_id for r in promoted] == ["UBERON:0002048"]
    assert report.conflicting_identity == 1
    assert report.refuted == 0  # the reasoner could not have caught this


@pytest.mark.unit
def test_absence_of_subsumption_is_not_a_contradiction_and_must_not_veto() -> None:
    """THE open-world regression test.

    Uberon relates an organ to its system with `part_of`, NOT `subClassOf` — on the
    live store, `lung rdfs:subClassOf* respiratory system` is **false**.  So for the
    canonical CORRECT pair (NCIt Lung -> Uberon lung, anchored on `Respiratory System
    Organ ≡ respiratory system`), the object is simply not *entailed* to sit under the
    anchored image.

    Under the open-world assumption that means *unknown*, not *false*.  Treating it as
    a contradiction (as an earlier round did) vetoed the canonical correct mapping,
    pinned coverage at zero, and logged "the two ontologies disagree about this
    concept" — a confident and false explanation.  A real contradiction can only come
    from the reasoner deriving ⊥, which the disjointness refutation already covers.
    """
    # lung's only subClassOf parent here is an organ class; the respiratory *system*
    # sits above it via part_of, which this walk deliberately does not follow (#78).
    ctx = _context(
        upstream_edges={("UBERON:0002048", "UBERON:0005178")},
        anchors=(("C12366", "UBERON:0001004"),),
        curated_pairs=frozenset({("C12468", "UBERON:0002048")}),
    )

    outcome = validate_candidate(_record(), ctx, reasoner=_ElkLikeReasoner())

    assert (
        corroboration(
            _record(),
            {(_UBERON_LUNG_IRI, f"{_OBO}UBERON_0005178")},
            anchors=ctx.anchors,
            ncit_edges=ctx.ncit_edges,
        )
        == NOT_ENTAILED
    )
    # … and the correct bridge is still promoted, on its curation + label evidence.
    assert outcome.promoted is not None
    assert outcome.promoted.predicate_id == EXACT_MATCH


@pytest.mark.unit
def test_a_stale_bridge_does_not_block_its_own_replacement() -> None:
    """An upstream release obsoletes U1 in favour of U2.  The stale (C, U1) must not
    claim C against the correct new (C, U2): it is about to be quarantined, and blocking
    the replacement would leave C with no bridge at all — while blaming a row this same
    run invalidates."""
    replacement = _record(obj="UBERON:0001558")
    ctx = _context(
        anchors=(("C12468", "UBERON:0002048"),),  # the stale bridge
        curated_pairs=frozenset({("C12468", "UBERON:0001558")}),
    )

    promoted, report = promote_candidates(
        [replacement],
        ctx,
        reasoner=_ElkLikeReasoner(),
        stale_anchors=frozenset({("C12468", "UBERON:0002048")}),
    )

    assert [r.object_id for r in promoted] == ["UBERON:0001558"]
    assert report.conflicting_identity == 0


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
    }


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
                {"upstream": _UBERON_LUNG_IRI, "xref": "NCI:C12468"},
                {"upstream": _UBERON_LUNG_IRI, "xref": "UMLS:C0024109"},
                {"upstream": "http://example.org/not-obo", "xref": "NCI:C99999"},
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
def test_the_ncit_disjoint_query_reads_the_stated_graph() -> None:
    """NCIt's default graph is the *inferred* build.  Reading the reasoner's refutation
    power out of a shipped inferred graph is what D21 forbids."""
    query = build_disjoint_query(base_iri=NCIT_NS, graph_iri=STATED_GRAPH_IRI)
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in query
    assert "owl:disjointWith" in query

    # the upstream store has no named-graph split, so it is queried unscoped
    assert "GRAPH" not in build_disjoint_query(base_iri=_OBO)


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
