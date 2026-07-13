"""Merged-EL validation ontology + curated bridge axiom (#73, D28 §4.4).

RED first: written before ``repositories/xref/bridge.py``.

The bridge is a **separate curated ``owl:equivalentClass`` axiom**. A SKOS mapping
annotation may never be the logical bridge, and the merged validation ontology must
carry no SKOS mapping triples at all — feeding them to the reasoner would import
every mapping error as an axiom.
"""

from __future__ import annotations

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from ontolib.repositories.xref.bridge import (
    OWL_EQUIVALENT_CLASS,
    bridge_axiom,
    build_validation_ontology,
)
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.vocab import (
    ALLOWED_PREDICATES,
    BROAD_MATCH,
    CLOSE_MATCH,
    EXACT_MATCH,
    NARROW_MATCH,
    RELATED_MATCH,
)
from ontolib.terminologies.namespaces import NCIT_NS

_LUNG = URIRef(f"{NCIT_NS}C12468")
_RESP_ORGAN = URIRef(f"{NCIT_NS}C12366")
_UBERON_LUNG = URIRef("http://purl.obolibrary.org/obo/UBERON_0002048")
_UBERON_RESP = URIRef("http://purl.obolibrary.org/obo/UBERON_0001004")
_UBERON_NERVOUS = URIRef("http://purl.obolibrary.org/obo/UBERON_0001016")

# NCIt: Lung ⊑ Respiratory System Organ.  Uberon: lung ⊑ respiratory system.
_NCIT_EDGES = {("C12468", "C12366")}
_UPSTREAM_EDGES = {("UBERON:0002048", "UBERON:0001004")}
# A separately-validated anchor (NOT the candidate pair).
_ANCHORS = (("C12366", "UBERON:0001004"),)


@pytest.fixture
def candidate() -> SSSOMRecord:
    return SSSOMRecord(
        subject_id="C12468",
        predicate_id=CLOSE_MATCH,
        object_id="UBERON:0002048",
        mapping_justification="semapv:DatabaseCrossReference",
        confidence=0.9,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
    )


def _graph(ttl: str) -> Graph:
    return Graph().parse(data=ttl, format="turtle")


# ── the bridge axiom is never a SKOS annotation ────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "skos_iri", [EXACT_MATCH, CLOSE_MATCH, BROAD_MATCH, NARROW_MATCH, RELATED_MATCH]
)
def test_skos_predicate_is_rejected_as_the_logical_bridge(skos_iri: str) -> None:
    """Feeding a SKOS mapping property as owl:equivalentClass is rejected."""
    with pytest.raises(ValueError, match="SKOS"):
        bridge_axiom(str(_LUNG), str(_UBERON_LUNG), property_iri=skos_iri)


@pytest.mark.unit
def test_bridge_axiom_defaults_to_owl_equivalent_class() -> None:
    axiom = bridge_axiom(str(_LUNG), str(_UBERON_LUNG))
    assert OWL_EQUIVALENT_CLASS in axiom
    assert str(_LUNG) in axiom
    assert str(_UBERON_LUNG) in axiom


# ── the merged validation ontology ─────────────────────────────────────


@pytest.mark.unit
def test_bridged_ontology_asserts_the_candidate_as_equivalent_class(
    candidate: SSSOMRecord,
) -> None:
    g = _graph(
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges=_UPSTREAM_EDGES,
            anchors=_ANCHORS,
            include_bridge=True,
        )
    )
    assert (_LUNG, OWL.equivalentClass, _UBERON_LUNG) in g


@pytest.mark.unit
def test_corroboration_ontology_omits_the_candidate_bridge(
    candidate: SSSOMRecord,
) -> None:
    """``include_bridge=False`` carries the trusted anchors but NOT the candidate —
    this is what makes structural corroboration non-circular (D28)."""
    g = _graph(
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges=_UPSTREAM_EDGES,
            anchors=_ANCHORS,
            include_bridge=False,
        )
    )
    assert (_LUNG, OWL.equivalentClass, _UBERON_LUNG) not in g
    assert (_RESP_ORGAN, OWL.equivalentClass, _UBERON_RESP) in g


@pytest.mark.unit
def test_ontology_carries_the_stated_taxonomy_of_both_planes(
    candidate: SSSOMRecord,
) -> None:
    g = _graph(
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges=_UPSTREAM_EDGES,
            anchors=_ANCHORS,
            include_bridge=True,
        )
    )
    assert (_LUNG, RDFS.subClassOf, _RESP_ORGAN) in g
    assert (_UBERON_LUNG, RDFS.subClassOf, _UBERON_RESP) in g
    for iri in (_LUNG, _RESP_ORGAN, _UBERON_LUNG, _UBERON_RESP):
        assert (iri, RDF.type, OWL.Class) in g


@pytest.mark.unit
def test_ontology_contains_no_skos_mapping_triples(candidate: SSSOMRecord) -> None:
    """No skos:*Match ever reaches the reasoner (D28: annotation ≠ logic)."""
    g = _graph(
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges=_UPSTREAM_EDGES,
            anchors=_ANCHORS,
            include_bridge=True,
        )
    )
    skos = {URIRef(p) for p in ALLOWED_PREDICATES}
    assert not [t for t in g if t[1] in skos]


@pytest.mark.unit
def test_ontology_stays_within_the_el_vocabulary(candidate: SSSOMRecord) -> None:
    """Only class declarations, subClassOf, and equivalentClass — nothing that
    escapes OWL 2 EL (the profile gate must never have to reject our own output)."""
    g = _graph(
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges=_UPSTREAM_EDGES,
            anchors=_ANCHORS,
            include_bridge=True,
        )
    )
    predicates = {t[1] for t in g}
    assert predicates <= {RDF.type, RDFS.subClassOf, OWL.equivalentClass}


@pytest.mark.unit
def test_disjointness_axioms_are_carried_into_the_merge(
    candidate: SSSOMRecord,
) -> None:
    """Without disjointness the merge is trivially satisfiable — a TBox of only
    subsumptions and equivalences over named classes has a model in which every class is
    the whole domain.  ELK could then never derive ``⊥``, the satisfiability gate could
    never fire, and the reasoner would contribute nothing a graph walk would not.  The
    disjointness axioms are the reasoner's only refutation power.
    """
    g = _graph(
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges=_UPSTREAM_EDGES,
            anchors=_ANCHORS,
            disjoints=((str(_UBERON_RESP), str(_UBERON_NERVOUS)),),
            include_bridge=True,
        )
    )
    assert (_UBERON_RESP, OWL.disjointWith, _UBERON_NERVOUS) in g


@pytest.mark.unit
def test_disjointness_stays_within_the_el_vocabulary(candidate: SSSOMRecord) -> None:
    """owl:disjointWith is in OWL 2 EL — carrying it must not push the merge out of
    profile (which would make the gate reject our own well-formed output)."""
    g = _graph(
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges=_UPSTREAM_EDGES,
            anchors=_ANCHORS,
            disjoints=((str(_UBERON_RESP), str(_UBERON_NERVOUS)),),
            include_bridge=True,
        )
    )
    predicates = {t[1] for t in g}
    assert predicates <= {
        RDF.type,
        RDFS.subClassOf,
        OWL.equivalentClass,
        OWL.disjointWith,
    }


@pytest.mark.unit
def test_candidate_may_not_be_its_own_anchor(candidate: SSSOMRecord) -> None:
    """Passing the candidate pair in as a 'trusted anchor' is the circularity D28
    forbids — reject it loudly rather than validate a tautology."""
    with pytest.raises(ValueError, match="own anchor"):
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges=_UPSTREAM_EDGES,
            anchors=(("C12468", "UBERON:0002048"),),
            include_bridge=True,
        )


@pytest.mark.unit
def test_unknown_upstream_prefix_is_rejected(candidate: SSSOMRecord) -> None:
    """A CURIE we have no base IRI for cannot be silently dropped from the merge."""
    with pytest.raises(KeyError):
        build_validation_ontology(
            candidate,
            ncit_edges=_NCIT_EDGES,
            upstream_edges={("UBERON:0002048", "SNOMED:12345")},
            anchors=_ANCHORS,
            include_bridge=True,
        )
