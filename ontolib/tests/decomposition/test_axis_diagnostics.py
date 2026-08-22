from __future__ import annotations

import pytest

from ontolib.decomposition.axis_diagnostics import (
    AxisDiagnosticError,
    AxisHierarchyEvidence,
    DisjointPair,
    HierarchyEdge,
    InvalidAxisEvidence,
    UnknownAxisEvidence,
    ValidAxisEvidence,
    classify_axis_range,
    disjoint_pairs_from_rows,
)
from ontolib.terminologies.namespaces import NCIT_NS

SOURCE = "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"


@pytest.mark.unit
def test_axis_range_valid_requires_exact_positive_structural_path() -> None:
    snapshot = AxisHierarchyEvidence(
        source_identity=SOURCE,
        edges=(HierarchyEdge(child="C10", parent="C12219"),),
        disjoint_pairs=(),
    )

    exact = classify_axis_range("op:PrimarySite", "C12219", "C12219", snapshot)
    inherited = classify_axis_range("op:PrimarySite", "C10", "C12219", snapshot)

    assert exact == ValidAxisEvidence(
        status="valid",
        axis="op:PrimarySite",
        filler_code="C12219",
        range_code="C12219",
        source_identity=SOURCE,
        reason="filler-is-range-or-descendant",
        structural_path=("C12219",),
    )
    assert isinstance(inherited, ValidAxisEvidence)
    assert inherited.structural_path == ("C10", "C12219")


@pytest.mark.unit
def test_axis_range_invalid_uses_disjoint_ancestors_in_both_directions() -> None:
    edges = (
        HierarchyEdge(child="C11", parent="C10"),
        HierarchyEdge(child="C12913", parent="C21"),
        HierarchyEdge(child="C21", parent="C20"),
    )
    for pair in (
        DisjointPair(left="C10", right="C20"),
        DisjointPair(left="C20", right="C10"),
    ):
        snapshot = AxisHierarchyEvidence(
            source_identity=SOURCE,
            edges=edges,
            disjoint_pairs=(pair,),
        )
        result = classify_axis_range("op:CellType", "C11", "C12913", snapshot)

        assert result == InvalidAxisEvidence(
            status="invalid",
            axis="op:CellType",
            filler_code="C11",
            range_code="C12913",
            source_identity=SOURCE,
            reason="disjoint-ancestor-pair",
            disjoint_pair=DisjointPair(left="C10", right="C20"),
            filler_ancestor_path=("C11", "C10"),
            range_ancestor_path=("C12913", "C21", "C20"),
        )


@pytest.mark.unit
def test_axis_range_no_proof_and_descendant_disjointness_are_unknown() -> None:
    no_proof = AxisHierarchyEvidence(
        source_identity=SOURCE, edges=(), disjoint_pairs=()
    )
    descendant_disjoint = AxisHierarchyEvidence(
        source_identity=SOURCE,
        edges=(HierarchyEdge(child="C21", parent="C20"),),
        disjoint_pairs=(DisjointPair(left="C10", right="C21"),),
    )

    assert classify_axis_range("op:CellType", "C10", "C12913", no_proof).reason == (
        "no-positive-or-negative-proof"
    )
    assert (
        classify_axis_range("op:CellType", "C10", "C20", descendant_disjoint).status
        == "unknown"
    )


@pytest.mark.unit
def test_axis_range_contradictory_positive_evidence_fails_closed() -> None:
    snapshot = AxisHierarchyEvidence(
        source_identity=SOURCE,
        edges=(HierarchyEdge(child="C10", parent="C12219"),),
        disjoint_pairs=(DisjointPair(left="C10", right="C12219"),),
    )

    result = classify_axis_range("op:PrimarySite", "C10", "C12219", snapshot)

    assert isinstance(result, UnknownAxisEvidence)
    assert result.reason == "contradictory-valid-and-invalid-evidence"


@pytest.mark.unit
def test_axis_contract_controls_the_range_and_unknown_axes_fail_closed() -> None:
    snapshot = AxisHierarchyEvidence(
        source_identity=SOURCE, edges=(), disjoint_pairs=()
    )

    unknown_axis = classify_axis_range("op:NotRegistered", "C10", "C20", snapshot)
    wrong_range = classify_axis_range("op:PrimarySite", "C10", "C20", snapshot)

    assert unknown_axis == UnknownAxisEvidence(
        status="unknown",
        axis="op:NotRegistered",
        filler_code="C10",
        range_code="C20",
        source_identity=SOURCE,
        reason="unknown-axis",
    )
    assert wrong_range.reason == "range-does-not-match-axis-contract"
    assert wrong_range.range_code == "C12219"


@pytest.mark.unit
def test_hierarchy_snapshot_rejects_duplicate_conflicting_or_malformed_facts() -> None:
    with pytest.raises(AxisDiagnosticError, match="duplicate hierarchy edge"):
        AxisHierarchyEvidence(
            source_identity=SOURCE,
            edges=(
                HierarchyEdge(child="C10", parent="C20"),
                HierarchyEdge(child="C10", parent="C20"),
            ),
            disjoint_pairs=(),
        )
    with pytest.raises(AxisDiagnosticError, match="duplicate disjoint pair"):
        AxisHierarchyEvidence(
            source_identity=SOURCE,
            edges=(),
            disjoint_pairs=(
                DisjointPair(left="C10", right="C20"),
                DisjointPair(left="C20", right="C10"),
            ),
        )
    with pytest.raises(AxisDiagnosticError, match="self-disjoint"):
        AxisHierarchyEvidence(
            source_identity=SOURCE,
            edges=(),
            disjoint_pairs=(DisjointPair(left="C10", right="C10"),),
        )
    with pytest.raises(AxisDiagnosticError, match="cycle"):
        AxisHierarchyEvidence(
            source_identity=SOURCE,
            edges=(
                HierarchyEdge(child="C10", parent="C20"),
                HierarchyEdge(child="C20", parent="C10"),
            ),
            disjoint_pairs=(),
        )
    with pytest.raises(ValueError, match="hierarchy child"):
        HierarchyEdge(child="not-a-code", parent="C20")
    with pytest.raises(ValueError, match="source_identity"):
        AxisHierarchyEvidence(source_identity="ABC", edges=(), disjoint_pairs=())


@pytest.mark.unit
def test_snapshot_and_evidence_are_canonical_and_deterministic() -> None:
    snapshot = AxisHierarchyEvidence(
        source_identity=SOURCE,
        edges=(
            HierarchyEdge(child="C10", parent="C30"),
            HierarchyEdge(child="C10", parent="C20"),
            HierarchyEdge(child="C20", parent="C7057"),
            HierarchyEdge(child="C30", parent="C7057"),
        ),
        disjoint_pairs=(DisjointPair(left="C60", right="C50"),),
    )

    assert snapshot.edges == tuple(
        sorted(snapshot.edges, key=lambda edge: (edge.child, edge.parent))
    )
    assert snapshot.disjoint_pairs == (DisjointPair(left="C50", right="C60"),)
    result = classify_axis_range("op:Morphology", "C10", "C7057", snapshot)
    assert isinstance(result, ValidAxisEvidence)
    assert result.structural_path == ("C10", "C20", "C7057")


@pytest.mark.unit
def test_evidence_models_reject_incoherent_values() -> None:
    with pytest.raises(ValueError, match="status"):
        ValidAxisEvidence(
            status="unknown",  # type: ignore[arg-type]
            axis="op:PrimarySite",
            filler_code="C10",
            range_code="C12219",
            source_identity=SOURCE,
            reason="filler-is-range-or-descendant",
            structural_path=("C10", "C12219"),
        )
    with pytest.raises(ValueError, match="structural_path"):
        ValidAxisEvidence(
            status="valid",
            axis="op:PrimarySite",
            filler_code="C10",
            range_code="C12219",
            source_identity=SOURCE,
            reason="filler-is-range-or-descendant",
            structural_path=("C11", "C12219"),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"left": None, "right": f"{NCIT_NS}C7057"}], "invalid left"),
        (
            [{"left": f"{NCIT_NS}C7057", "right": f"{NCIT_NS}C7057"}],
            "self-disjoint",
        ),
        (
            [
                {"left": f"{NCIT_NS}C12218", "right": f"{NCIT_NS}C7057"},
                {"left": f"{NCIT_NS}C7057", "right": f"{NCIT_NS}C12218"},
            ],
            "duplicate disjoint pair",
        ),
    ],
)
def test_disjoint_row_parser_fails_closed(
    rows: list[dict[str, str | None]], message: str
) -> None:
    with pytest.raises(AxisDiagnosticError, match=message):
        disjoint_pairs_from_rows(rows)
