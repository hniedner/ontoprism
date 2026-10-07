"""P334 anchors follow the production told hierarchy, not path-length ranking."""

from unittest.mock import AsyncMock

import pytest
import rdflib

from ontolib.decomposition.axis_diagnostics import AxisHierarchyEvidence, HierarchyEdge
from ontolib.decomposition.histology_anchor import HistologyAnchors, read_p334_values
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Decomposition
from ontolib.decomposition.source_support import ConstituentEvidence
from ontolib.terminologies.namespaces import NCIT_NS

pytestmark = pytest.mark.unit


def test_p334_support_requires_values_and_told_path_not_just_source_label():
    evidence = ConstituentEvidence(
        run_id="r",
        concept_code="C1",
        axis="op:HistologyAnchor",
        filler_code="C2",
        axis_source="p334",
        sources=[],
        policy_choices=["axis-assignment"],
    )
    assert evidence.support == "not-source-backed"
    source = AxisHierarchyEvidence(
        source_identity="a" * 64,
        edges=(HierarchyEdge(child="C1", parent="C2"),),
        disjoint_pairs=(),
    )
    anchors = HistologyAnchors(source, {"C2": ("8000/3", "bad")})
    supported = anchors.support(evidence)
    assert supported.support == "p334-backed"
    assert supported.p334 is not None
    assert supported.p334.eligible_values == ("8000/3",)
    assert supported.p334.other_values == ("bad",)
    assert supported.p334.path == ("C1", "C2")
    assert supported.sources == []
    assert "not equivalence" in supported.inferred_assertions[0]
    bad = supported.model_dump(exclude_computed_fields=True)
    bad["filler_code"] = "C3"
    with pytest.raises(ValueError, match="does not match"):
        ConstituentEvidence.model_validate(bad)
    with pytest.raises(ValueError, match="not a current"):
        HistologyAnchors(source, {}).support(evidence)


def test_anchor_minima_follow_transitive_genus_and_subclass_edges():
    source = AxisHierarchyEvidence(
        source_identity="a" * 64,
        edges=(
            HierarchyEdge(child="C1", parent="C2"),
            HierarchyEdge(child="C2", parent="C3"),
            HierarchyEdge(child="C3", parent="C4"),
        ),
        disjoint_pairs=(),
    )
    anchors = HistologyAnchors(source, {"C2": ("8246/3",), "C4": ("8010/3",)})
    assert anchors.for_concept("C1") == ("C2",)
    assert anchors.for_concept("C2") == ("C2",)
    assert anchors.for_concept("C9") == ()
    assert [
        (c.filler_code, c.axis_source, c.needs_review)
        for c in anchors.constituents("C1")
    ] == [("C2", "p334", False)]


def test_anchor_keeps_incomparable_carriers_and_inclusive_behaviors():
    source = AxisHierarchyEvidence(
        source_identity="a" * 64,
        edges=tuple(HierarchyEdge(child="C1", parent=f"C{i}") for i in range(2, 8)),
        disjoint_pairs=(),
    )
    anchors = HistologyAnchors(
        source,
        {
            "C2": ("8000/0",),
            "C3": ("8000/1",),
            "C4": ("8000/2",),
            "C5": ("8000/3", "8000/9"),
            "C6": ("8000/6",),
            "C7": ("850/30",),
        },
    )
    assert anchors.for_concept("C1") == ("C2", "C3", "C4", "C5")
    assert anchors.values["C5"] == ("8000/3", "8000/9")
    assert all(c.needs_review and c.axis_ambiguous for c in anchors.constituents("C1"))


async def test_read_preserves_nonqualifying_values_but_rejects_missing_carriers():
    client = AsyncMock()
    client.select_once.return_value = [
        {"concept": NCIT_NS + "C2", "value": value}
        for value in ("8000/3", "8000/6", "bad", "8000/3")
    ]
    assert await read_p334_values(client) == {"C2": ("8000/3", "8000/6", "bad")}
    client.select_once.return_value = [{"concept": "urn:other:C2", "value": "8000/3"}]
    with pytest.raises(ValueError, match="named NCIt"):
        await read_p334_values(client)
    client.select_once.return_value = [{"concept": NCIT_NS + "Cbad", "value": "8000/3"}]
    with pytest.raises(ValueError, match="named NCIt"):
        await read_p334_values(client)
    client.select_once.return_value = [{"concept": NCIT_NS + "C2", "value": ""}]
    with pytest.raises(ValueError, match="missing"):
        await read_p334_values(client)


async def test_export_carries_p334_annotations_for_each_emitted_anchor(tmp_path):
    source = AxisHierarchyEvidence(
        source_identity="a" * 64, edges=(), disjoint_pairs=()
    )
    anchors = HistologyAnchors(source, {"C2": ("8000/3", "bad")})
    target = tmp_path / "anchors.ttl"
    decomposition = Decomposition(
        code="C2",
        semantic_type="Neoplastic Process",
        constituents=anchors.constituents("C2"),
    )
    with pytest.raises(ValueError, match="requires source annotation"):
        await write_ttl([decomposition], target)
    assert not target.exists()
    await write_ttl(
        [
            Decomposition(
                code="C2",
                semantic_type="Neoplastic Process",
                constituents=anchors.constituents("C2"),
            )
        ],
        target,
        p334_values=anchors.values,
    )
    graph = rdflib.Graph().parse(target)
    assert set(
        graph.objects(rdflib.URIRef(NCIT_NS + "C2"), rdflib.URIRef(NCIT_NS + "P334"))
    ) == {rdflib.Literal("8000/3"), rdflib.Literal("bad")}
