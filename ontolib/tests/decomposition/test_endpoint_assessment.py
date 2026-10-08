"""Endpoint evidence categories do not claim terminality or semantic atomhood."""

from collections import Counter
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from scripts import endpoint_assessment as report_module
from scripts.endpoint_assessment import render_assessment

from ontolib.decomposition.axis_diagnostics import (
    AxisDiagnosticSource,
    AxisHierarchyEvidence,
    DisjointPair,
    HierarchyEdge,
)
from ontolib.decomposition.endpoint_assessment import (
    Endpoint,
    assess_endpoint,
    assessment_counts,
)
from ontolib.decomposition.histology_anchor import HistologyAnchors

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "failure", [None, "version", "changed-version", "changed-source", "transport"]
)
async def test_report_source_failures_do_not_produce_success(
    monkeypatch, failure, tmp_path
):
    source, anchors = evidence()
    snapshot = SimpleNamespace(source_identity="a" * 64)
    monkeypatch.setattr(
        report_module,
        "_source_snapshot",
        AsyncMock(
            side_effect=[snapshot, snapshot if failure != "changed-source" else None]
        ),
    )
    client = SimpleNamespace(
        version=AsyncMock(
            side_effect=[
                "bad" if failure == "version" else "26.07d",
                "bad" if failure == "changed-version" else "26.07d",
            ]
        )
    )

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield client

    engine = SimpleNamespace(connect=connection, dispose=AsyncMock())
    monkeypatch.setattr(report_module, "create_async_engine", lambda *a, **k: engine)
    monkeypatch.setattr(report_module, "ncit_sparql_client", connection)
    monkeypatch.setattr(
        report_module,
        "read_assessment_inputs",
        AsyncMock(
            return_value=(
                {
                    "ncit_version": "26.07d",
                    "fingerprint": {"routing_implementation_identity": "stored"},
                    "metrics": {"decomposed": 1, "residual_precoordination": 1.0},
                },
                [
                    Endpoint(
                        concept_code="C1",
                        axis="op:HistologyAnchor",
                        filler_code="C2",
                        axis_source="p334",
                        classification="precoordinated",
                    )
                ],
                {"C1"},
                Counter({"decomposed": 1}),
            )
        ),
    )
    monkeypatch.setattr(
        report_module, "read_axis_diagnostic_source", AsyncMock(return_value=source)
    )
    monkeypatch.setattr(
        report_module,
        "read_p334_values",
        AsyncMock(
            return_value=anchors.values,
            side_effect=RuntimeError("offline") if failure == "transport" else None,
        ),
    )
    if failure:
        with pytest.raises(
            (ValueError, RuntimeError), match=r"differs|changed|offline"
        ):
            await report_module.endpoint_report("r", tmp_path / "manifest.json")
    else:
        result = await report_module.endpoint_report("r", tmp_path / "manifest.json")
        assert "D37.residual_precoordination=1.0" in result
        assert "assessment.endpoints.category.source-qualified-anchor=1" in result
        assert "assessment.outcomes.decomposed=1" in result
    engine.dispose.assert_awaited_once()


def test_false_anchor_provenance_is_unknown():
    source, anchors = evidence()
    item = Endpoint(
        concept_code="C1",
        axis="op:HistologyAnchor",
        filler_code="C2",
        axis_source="parent",
        classification="atomic",
    )
    assert assess_endpoint(item, source, anchors).reason == "anchor-provenance-not-p334"


def evidence():
    source = AxisDiagnosticSource(
        AxisHierarchyEvidence(
            source_identity="a" * 64,
            edges=(
                HierarchyEdge(child="C1", parent="C2"),
                HierarchyEdge(child="C2", parent="C3262"),
                HierarchyEdge(child="C3", parent="C12219"),
                HierarchyEdge(child="C4", parent="C7057"),
            ),
            disjoint_pairs=(DisjointPair(left="C4", right="C12219"),),
        )
    )
    return source, HistologyAnchors(source.snapshot, {"C2": ("8000/3",)})


@pytest.mark.parametrize(
    ("axis", "filler", "detected", "category"),
    [
        ("op:ToldGenus", "C4", "precoordinated", "retained-context"),
        ("op:Morphology", "C4", "atomic", "retained-context"),
        ("op:HistologyAnchor", "C2", "precoordinated", "source-qualified-anchor"),
        ("op:PrimarySite", "C3", "atomic", "range-valid-endpoint"),
        (
            "op:ClinicalFinding",
            "C4",
            "precoordinated",
            "detector-positive-disease-endpoint",
        ),
        ("op:ClinicalFinding", "C4", "atomic", "range-valid-endpoint"),
        ("op:PrimarySite", "C4", "atomic", "invalid-endpoint"),
        ("op:PrimarySite", "C9", "atomic", "unknown"),
        ("R126", "C4", "precoordinated", "unknown"),
        ("op:StageSystem", "MINT-123456789abc", None, "unknown"),
        ("op:ClinicalFinding", "C4", None, "unknown"),
        ("op:ClinicalFinding", "C4", "unknown", "unknown"),
        ("op:HistologyAnchor", "C4", "atomic", "unknown"),
    ],
)
def test_categories_separate_range_anchor_detector_and_invalid(
    axis, filler, detected, category
):
    source, anchors = evidence()
    item = Endpoint(
        concept_code="C1",
        axis=axis,
        filler_code=filler,
        axis_source="p334" if axis == "op:HistologyAnchor" else "role",
        classification=detected,
    )
    result = assess_endpoint(item, source, anchors)
    assert result.category == category
    assert result.reason


def test_flags_do_not_disappear_when_endpoint_has_range_evidence():
    source, anchors = evidence()
    item = Endpoint(
        concept_code="C1",
        axis="op:PrimarySite",
        filler_code="C3",
        axis_source="role",
        classification="atomic",
        needs_review=True,
        axis_ambiguous=True,
    )
    assert assess_endpoint(item, source, anchors).category == "range-valid-endpoint"
    assert item.needs_review
    assert item.axis_ambiguous


def test_d37_values_are_not_replaced_by_new_category_counts():
    report = render_assessment(
        "r",
        {
            "ncit_version": "26.07d",
            "fingerprint": {"routing_implementation_identity": "historical"},
            "metrics": {
                "decomposed": 10,
                "residual_precoordinated_count": 9,
                "residual_precoordination_unknown_count": 2,
                "residual_precoordination": None,
            },
        },
        Counter(
            {
                "endpoints.category.detector-positive-disease-endpoint": 1,
                "endpoints.category.unknown": 3,
            }
        ),
    )
    assert "D37.residual_precoordinated_count=9" in report
    assert "D37.residual_precoordination_unknown_count=2" in report
    assert "D37.residual_precoordination=None" in report
    assert (
        "assessment.endpoints.category.detector-positive-disease-endpoint=1" in report
    )


def test_summary_keeps_overlapping_flags_and_absent_anchors_visible():
    source, anchors = evidence()
    rows = [
        Endpoint(
            concept_code="C1",
            axis="op:HistologyAnchor",
            filler_code="C2",
            axis_source="p334",
            classification="precoordinated",
            needs_review=True,
            axis_ambiguous=True,
        ),
        Endpoint(
            concept_code="C1",
            axis="op:PrimarySite",
            filler_code="C4",
            axis_source="role",
            classification="atomic",
        ),
        Endpoint(
            concept_code="C9",
            axis="op:ToldGenus",
            filler_code="C4",
            axis_source="parent",
            classification="precoordinated",
        ),
    ]
    counts = assessment_counts(rows, {"C1", "C9"}, source, anchors)
    assert counts["endpoints.category.source-qualified-anchor"] == 1
    assert counts["endpoints.category.invalid-endpoint"] == 1
    assert counts["review.needs-review.axis.op:HistologyAnchor"] == 1
    assert counts["review.axis-ambiguous.axis.op:HistologyAnchor"] == 1
    assert counts["anchors.no-eligible-source-anchor.concepts"] == 1
    assert counts["anchors.no-emitted-anchor.concepts"] == 1
    assert counts["concepts.context-only"] == 1
    assert sum(v for k, v in counts.items() if k.startswith("endpoints.category.")) == 3


def test_summary_counts_real_anchor_ambiguity_separately_from_qualification():
    source, _ = evidence()
    tied = HistologyAnchors(source.snapshot, {"C3": ("8000/0",), "C4": ("8000/1",)})
    # Two genuinely incomparable carriers are ancestors of C5.
    source = AxisDiagnosticSource(
        AxisHierarchyEvidence(
            source_identity="a" * 64,
            edges=(
                HierarchyEdge(child="C5", parent="C3"),
                HierarchyEdge(child="C5", parent="C4"),
            ),
            disjoint_pairs=(),
        )
    )
    tied = HistologyAnchors(source.snapshot, tied.values)
    rows = [
        Endpoint(
            concept_code="C5",
            axis="op:HistologyAnchor",
            filler_code=f,
            axis_source="p334",
            classification="atomic",
        )
        for f in ("C3", "C4")
    ]
    counts = assessment_counts(rows, {"C5"}, source, tied)
    assert counts["endpoints.category.source-qualified-anchor"] == 2
    assert counts["anchors.incomparable-source-anchors.concepts"] == 1
    assert counts["anchors.multiple-emitted-anchors.concepts"] == 1
