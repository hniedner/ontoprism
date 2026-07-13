"""Tests for upstream xref on decomposition constituents (issues #77/#82)."""

import pytest

from ontolib.decomposition.read import attach_upstream, decomposition_from_rows
from ontolib.decomposition.read_models import (
    ConceptDecomposition,
    DecompositionConstituent,
    UpstreamMapping,
)
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH


@pytest.mark.unit
def test_upstream_mapping_defaults() -> None:
    m = UpstreamMapping(
        object_id="UBERON:0002046", predicate=EXACT_MATCH, lifecycle="active"
    )
    assert m.object_id == "UBERON:0002046"
    assert m.predicate == EXACT_MATCH
    assert m.lifecycle == "active"
    assert m.confidence == 0.0
    assert m.is_identity is True


@pytest.mark.unit
def test_upstream_mapping_not_identity() -> None:
    m = UpstreamMapping(
        object_id="UBERON:0002046", predicate=CLOSE_MATCH, lifecycle="proposed"
    )
    assert m.is_identity is False


@pytest.mark.unit
def test_upstream_mapping_carries_confidence() -> None:
    m = UpstreamMapping(
        object_id="UBERON:0002046",
        predicate=EXACT_MATCH,
        lifecycle="validated",
        confidence=0.9,
    )
    assert m.confidence == 0.9
    assert m.is_identity is True


@pytest.mark.unit
def test_upstream_mapping_low_confidence_not_identity() -> None:
    m = UpstreamMapping(
        object_id="UBERON:0002046",
        predicate=CLOSE_MATCH,
        lifecycle="proposed",
        confidence=0.7,
    )
    assert m.confidence == 0.7
    assert m.is_identity is False


@pytest.mark.unit
def test_decomposition_constituent_upstream_defaults_empty() -> None:
    c = DecompositionConstituent(axis="R101", filler="C12400", axis_source="role")
    assert c.upstream == []


@pytest.mark.unit
def test_attach_upstream_populates_by_filler() -> None:
    rows = [
        {
            "status": "legacy-precoordinated",
            "decomposedOn": "2026-07-06",
            "axis": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#R101",
            "filler": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12400",
            "axisSource": "role",
            "mostSpecific": "true",
        }
    ]
    decomp = decomposition_from_rows("C6135", rows)
    upstream = {
        "C12400": [
            UpstreamMapping(
                object_id="UBERON:0002046", predicate=EXACT_MATCH, lifecycle="validated"
            ),
        ]
    }
    result = attach_upstream(decomp, upstream)
    assert len(result.constituents) == 1
    assert result.constituents[0].filler == "C12400"
    assert len(result.constituents[0].upstream) == 1
    assert result.constituents[0].upstream[0].object_id == "UBERON:0002046"
    assert result.constituents[0].upstream[0].is_identity is True


@pytest.mark.unit
def test_attach_upstream_unmapped_filler_gets_empty() -> None:
    decomp = ConceptDecomposition(
        code="C6135",
        is_legacy_precoordinated=True,
        constituents=[
            DecompositionConstituent(axis="R101", filler="C12400", axis_source="role"),
        ],
    )
    result = attach_upstream(decomp, {})
    assert result.constituents[0].upstream == []


@pytest.mark.unit
def test_attach_upstream_does_not_mutate_input() -> None:
    decomp = ConceptDecomposition(
        code="C6135",
        is_legacy_precoordinated=True,
        constituents=[
            DecompositionConstituent(axis="R101", filler="C12400", axis_source="role"),
        ],
    )
    upstream = {
        "C12400": [
            UpstreamMapping(
                object_id="UBERON:0002046", predicate=EXACT_MATCH, lifecycle="validated"
            ),
        ]
    }
    result = attach_upstream(decomp, upstream)
    assert decomp.constituents[0].upstream == []
    assert result.constituents[0].upstream[0].object_id == "UBERON:0002046"
