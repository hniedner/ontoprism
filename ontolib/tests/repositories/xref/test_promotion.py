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
import subprocess
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
    REASON_INSUFFICIENT_EVIDENCE,
    REASON_NOT_EL_VALID,
    REASON_PROMOTED,
    PromotionContext,
    elk_reasoner,
    is_structurally_corroborated,
    load_promotion_context,
    parse_inferred_subclasses,
    promote_candidates,
    validate_candidate,
)
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH
from ontolib.terminologies.namespaces import NCIT_NS

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


# ── a real (tiny) EL reasoner double ───────────────────────────────────
#
# ``robot``/ELK is an external process and is not on PATH in the hermetic suite
# (the real thing is exercised by ``test_promotion_with_real_elk``, below, and by
# ``test_validation.py::test_robot_elk_smoke``).  This double does the *actual*
# work ELK does for our fragments — transitive closure of subClassOf, with
# equivalentClass read in both directions — so the assertions below are about
# behaviour, not about a mock being called.


class _ClosureReasoner:
    """Compute the subsumption closure of a Turtle fragment, ELK-style."""

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
        closure = set(edges)
        while True:
            grown = closure | {
                (a, d) for a, b in closure for c, d in closure if b == c and a != d
            }
            if grown == closure:
                return closure
            closure = grown


def _reject_all(ttl: str) -> None:
    """A reasoner that rejects every merge (non-EL, or unsatisfiable)."""
    return None


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
    """NCIt: Lung ⊑ Respiratory System Organ.  Uberon: lung ⊑ respiratory system,
    brain ⊑ nervous system.  Trusted anchor: C12366 ≡ UBERON:0001004."""
    base: dict[str, Any] = {
        "subject_labels": {"C12468": {"Lung"}},
        "object_labels": {
            "UBERON:0002048": {"lung"},
            "UBERON:0000955": {"brain"},
        },
        "object_xrefs": {"UBERON:0002048": {"C12468"}},
        "ncit_edges": {("C12468", "C12366")},
        "upstream_edges": {
            ("UBERON:0002048", "UBERON:0001004"),
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
    outcome = validate_candidate(_record(), _context(), reasoner=_ClosureReasoner())

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
    outcome = validate_candidate(_record(), _context(), reasoner=_ClosureReasoner())
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
        _record(obj="UBERON:0000955"), _context(), reasoner=_ClosureReasoner()
    )

    assert outcome.promoted is None
    assert outcome.reason == REASON_INSUFFICIENT_EVIDENCE
    assert outcome.evidence == ()


@pytest.mark.unit
def test_a_single_signal_does_not_promote() -> None:
    """Label agreement alone (no anchored parent to corroborate) is not enough."""
    outcome = validate_candidate(
        _record(), _context(anchors=()), reasoner=_ClosureReasoner()
    )

    assert outcome.promoted is None
    assert outcome.reason == REASON_INSUFFICIENT_EVIDENCE
    assert {e.kind for e in outcome.evidence} == {LABEL_AGREEMENT}


@pytest.mark.unit
def test_sme_curated_pair_promotes_on_curation_alone() -> None:
    outcome = validate_candidate(
        _record(subject="C12377", obj="UBERON:0002110"),
        _context(curated_pairs=frozenset({("C12377", "UBERON:0002110")})),
        reasoner=_ClosureReasoner(),
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
        reasoner=_ClosureReasoner(),
    )

    assert outcome.promoted is not None
    assert STRUCTURAL_CORROBORATION not in {e.kind for e in outcome.evidence}


# ── the EL gate ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_candidate_is_not_promoted_when_the_merge_fails_the_el_gate() -> None:
    """A merge that escapes EL or is unsatisfiable is *rejected*, not force-classified
    — and no amount of evidence promotes it."""
    outcome = validate_candidate(
        _record(),
        _context(curated_pairs=frozenset({("C12468", "UBERON:0002048")})),
        reasoner=_reject_all,
    )

    assert outcome.promoted is None
    assert outcome.el_valid is False
    assert outcome.reason == REASON_NOT_EL_VALID


@pytest.mark.unit
def test_structural_corroboration_never_sees_the_candidate_bridge() -> None:
    """THE non-circularity test: the merge the reasoner corroborates over must not
    contain owl:equivalentClass(subject, object) — otherwise the candidate proves
    itself and every mapping 'validates'."""
    reasoner = _ClosureReasoner()
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
        is_structurally_corroborated(
            _record(),
            inferred,
            anchors=(),
            ncit_edges={("C12468", "C12366")},
        )
        is False
    )


@pytest.mark.unit
def test_structural_corroboration_fails_when_the_upstream_parent_disagrees() -> None:
    """Subject's anchored parent maps to 'respiratory system'; the candidate object
    (brain) is not inferred to sit under it → the anchor actively contradicts."""
    inferred = {(_UBERON_BRAIN_IRI, f"{_OBO}UBERON_0000010")}
    assert (
        is_structurally_corroborated(
            _record(obj="UBERON:0000955"),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C12366")},
        )
        is False
    )


@pytest.mark.unit
def test_structural_corroboration_follows_ncit_ancestors() -> None:
    """The anchored NCIt class may be a *grand*parent of the subject."""
    inferred = {(_UBERON_LUNG_IRI, _UBERON_RESP_IRI)}
    assert (
        is_structurally_corroborated(
            _record(),
            inferred,
            anchors=(("C12366", "UBERON:0001004"),),
            ncit_edges={("C12468", "C99999"), ("C99999", "C12366")},
        )
        is True
    )


# ── the run: report + the number it moves ──────────────────────────────


@pytest.mark.unit
def test_promotion_report_counts_every_outcome() -> None:
    records = [
        _record(),  # promotes
        _record(obj="UBERON:0000955"),  # insufficient evidence
    ]
    promoted, report = promote_candidates(
        records, _context(), reasoner=_ClosureReasoner()
    )

    assert [r.predicate_id for r in promoted] == [EXACT_MATCH]
    assert report.as_dict() == {
        "considered": 2,
        "promoted": 1,
        "insufficient_evidence": 1,
        "not_el_valid": 0,
    }


@pytest.mark.unit
def test_report_separates_el_rejections_from_weak_evidence() -> None:
    """A curated pair whose merge the reasoner rejects is counted as an EL failure,
    not as missing evidence — the two are different problems with different fixes."""
    _, report = promote_candidates(
        [_record(), _record(obj="UBERON:0000955")],
        _context(curated_pairs=frozenset({("C12468", "UBERON:0002048")})),
        reasoner=_reject_all,
    )

    assert report.as_dict() == {
        "considered": 2,
        "promoted": 0,
        "insufficient_evidence": 1,
        "not_el_valid": 1,
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
        [_record()], _context(), reasoner=_ClosureReasoner()
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
<{_UBERON_RESP_IRI}> a owl:Class ; rdfs:subClassOf owl:Thing .
<{_LUNG_IRI}> a owl:Class ; rdfs:subClassOf [ a owl:Restriction ] .
"""
    )

    assert parse_inferred_subclasses(inferred) == {
        (_UBERON_LUNG_IRI, _UBERON_RESP_IRI),
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
def test_elk_reasoner_returns_none_when_the_merge_is_unsatisfiable(
    mock_validate: MagicMock,
) -> None:
    """ROBOT exits non-zero on unsatisfiable classes — that is the satisfiability
    gate, and it must reject the merge rather than blow up the run."""
    mock_validate.side_effect = subprocess.CalledProcessError(1, ["robot"])
    assert elk_reasoner("<urn:a> a <urn:C> .") is None


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


# ── integration: the real ELK ──────────────────────────────────────────


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
