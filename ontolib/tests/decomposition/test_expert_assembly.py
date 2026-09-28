"""Behavioral contracts for the bounded G1 rehearsal sample and read-only record."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from scripts.research.expert_assembly import (
    _p334_carriers,
    _require_current_frame,
    assemble_concept,
    histology_anchors,
    select_packet_sample,
    told_parent_index,
)

from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.scope import HierarchyEdge
from ontolib.decomposition.semantic_identity import routing_implementation_identity
from ontolib.repositories.cadsr.models import CdeSummary
from ontolib.terminologies.ncit.models import ConceptDetail, ConceptRef, Relationship

pytestmark = pytest.mark.unit


def test_seeded_sample_keeps_every_oracle_and_draws_only_decomposed_nonoracle() -> None:
    oracle = [f"C{i}" for i in range(1, 21)]
    eligible = [f"C{i}" for i in range(1, 101)]
    chosen = select_packet_sample(oracle, eligible, seed=472)

    assert chosen[:20] == [(code, "oracle") for code in oracle]
    assert len(chosen) == len({code for code, _cohort in chosen}) == 50
    assert {code for code, _cohort in chosen[20:]} <= set(eligible) - set(oracle)
    assert chosen == select_packet_sample(oracle, reversed(eligible), seed=472)
    with pytest.raises(ValueError, match="eligible"):
        select_packet_sample(oracle, oracle, seed=472)


def test_told_anchor_candidates_include_self_or_incomparable_or_none() -> None:
    edges = (
        HierarchyEdge("C1", "C2"),
        HierarchyEdge("C1", "C3"),
        HierarchyEdge("C2", "C4"),
        HierarchyEdge("C3", "C5"),
        HierarchyEdge("C5", "C4"),
    )
    parents = told_parent_index(edges)
    assert histology_anchors("C1", parents, {"C1", "C2", "C3", "C4"}) == ("C1",)
    assert histology_anchors("C1", parents, {"C2", "C3", "C4"}) == ("C2", "C3")
    assert histology_anchors("C1", parents, {"C4", "C5"}) == ("C5",)
    assert histology_anchors("C1", parents, set()) == ()


def test_frame_refuses_incomplete_or_outdated_routing_and_requires_file_only() -> None:
    fingerprint = SimpleNamespace(
        branch="neoplasm",
        worklist=tuple(f"C{i}" for i in range(1000)),
        algorithm_version=branch_spec(DecompositionBranch.NEOPLASM).algorithm_version,
        routing_implementation_identity=routing_implementation_identity(),
        load_mode="none",
    )
    run = SimpleNamespace(status="complete")
    _require_current_frame(run, fingerprint)
    for value in ("failed", "running"):
        with pytest.raises(ValueError, match="complete"):
            _require_current_frame(SimpleNamespace(status=value), fingerprint)
    for field, value in (
        ("algorithm_version", "old"),
        ("routing_implementation_identity", "old"),
        ("load_mode", "named-graph"),
        ("branch", "disease"),
        ("worklist", ("C1",)),
    ):
        with pytest.raises(ValueError, match="frame"):
            _require_current_frame(
                run, SimpleNamespace(**{**vars(fingerprint), field: value})
            )


def test_assembled_record_keeps_source_fields_flags_cdes_and_missing_values() -> None:
    detail = ConceptDetail(
        code="C1",
        label="Carcinoma",
        parents=[ConceptRef(code="C2", label="Parent")],
        roles=[
            Relationship(
                relation="R105",
                relation_label="Cell",
                target=ConceptRef(code="C5", label="Tumor cell"),
            )
        ],
    )
    cde = CdeSummary(
        public_id="42", version="1", short_name="Test", long_name="Test CDE"
    )
    record = assemble_concept(
        detail,
        cohort="oracle",
        outcome="decomposed",
        outcome_reason="engine emitted 1",
        pairs=[("op:Morphology", "C2", True, True)],
        labels={"C2": "Parent"},
        genus=["C2"],
        anchors=["C1", "C3"],
        cdes={"C1": [cde], "C2": []},
        stated_roles=[("R101", "Site", "C4", "Lung")],
        flags=[("needs-review", "constituent op:Morphology / C2 needs review")],
    )
    assert record.code == "C1"
    assert record.label == "Carcinoma"
    assert record.parents == (("C2", "Parent"),)
    assert ("R101", "Site", "C4", "Lung") in record.stated_roles
    assert ("R105", "Cell", "C5", "Tumor cell") in record.stated_roles
    assert record.constituents[0][:3] == ("op:Morphology", "C2", "Parent")
    assert "ambiguous" in record.constituents[0][4]
    assert record.anchors[0] == ("C1", "Carcinoma", True)
    assert record.anchor_count == 2
    assert record.cdes["C1"] == (("42", "1", "Test CDE"),)
    assert record.cdes["C2"] == ()
    assert record.flags[0][1] == "constituent op:Morphology / C2 needs review"


def test_no_cdes_and_no_anchors_remain_explicit_empty_values() -> None:
    record = assemble_concept(
        ConceptDetail(code="C1", label="Neoplasm"),
        cohort="random",
        outcome="decomposed",
        outcome_reason="engine emitted 0",
        pairs=[],
        labels={},
        genus=[],
        anchors=[],
        cdes={"C1": []},
        flags=[],
    )
    assert record.anchor_count == 0
    assert record.anchors == ()
    assert record.cdes == {"C1": ()}


@pytest.mark.asyncio
async def test_p334_lookup_uses_stated_graph_and_only_returns_ncit_carrier_codes() -> (
    None
):
    class Client:
        async def select(self, query: str, *, required_variables=()):
            assert "Thesaurus-stated.owl" in query
            assert "?carrier ncit:P334 ?p334" in query
            assert "SELECT DISTINCT ?carrier" in query
            return [
                {"carrier": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1"}
            ]

    assert await _p334_carriers(Client(), {"C1", "C2"}) == {"C1"}
