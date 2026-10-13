"""Disjoint co-fillers are inspection pairs, not inferred contradictions."""

import pytest
from rdflib import Graph

from ontolib.decomposition.axis_diagnostics import (
    AxisHierarchyEvidence,
    DisjointPair,
    HierarchyEdge,
)
from ontolib.decomposition.reasoner_check import _subsumptions, disjoint_fillers

pytestmark = pytest.mark.unit


def test_disjoint_inspection_respects_concept_axis_and_inherited_pairs():
    hierarchy = AxisHierarchyEvidence(
        source_identity="a" * 64,
        edges=(
            HierarchyEdge(child="C1", parent="C10"),
            HierarchyEdge(child="C2", parent="C20"),
        ),
        disjoint_pairs=(DisjointPair(left="C10", right="C20"),),
    )
    rows = [
        {"concept_code": c, "axis": a, "filler_code": f}
        for c, a, f in (
            ("C9", "op:PrimarySite", "C1"),
            ("C9", "op:PrimarySite", "C2"),
            ("C9", "op:PrimarySite", "C3"),
            ("C9", "op:AssociatedSite", "C1"),
            ("C8", "op:PrimarySite", "C2"),
        )
    ]
    assert disjoint_fillers(rows, hierarchy) == [
        {
            "concept_code": "C9",
            "axis": "op:PrimarySite",
            "fillers": ["C1", "C2"],
            "disjoint_pairs": [("C10", "C20")],
        }
    ]


def test_named_entailment_reader_expands_equivalence_and_excludes_restriction_nodes():
    graph = Graph().parse(
        data="""
      @prefix owl: <http://www.w3.org/2002/07/owl#> .
      @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
      <urn:A> owl:equivalentClass <urn:B> ; rdfs:subClassOf [a owl:Restriction] .
      <urn:B> rdfs:subClassOf <urn:C> .
    """,
        format="turtle",
    )
    assert _subsumptions(graph) == {
        ("urn:A", "urn:B"),
        ("urn:B", "urn:A"),
        ("urn:A", "urn:C"),
        ("urn:B", "urn:C"),
    }
