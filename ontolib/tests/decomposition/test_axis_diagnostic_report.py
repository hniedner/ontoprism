from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest
from scripts.adjudication import _parser
from scripts.research.axis_diagnostic_report import (
    AxisDiagnosticReport,
    PairRangeDiagnostic,
    ResidualPrecoordinationVerdict,
    SourcePairEvidence,
    build_axis_diagnostic_report,
    collect_residual_verdicts,
    collect_source_pair_evidence,
    generate_axis_diagnostic_report,
)
from scripts.research.current_evidence import (
    CurrentComparison,
    CurrentEngineEvidence,
)
from scripts.research.golden_review import load_adjudication, load_row_decisions

from ontolib.decomposition.axis_diagnostics import (
    AxisHierarchyEvidence,
    HierarchyEdge,
    ValidAxisEvidence,
    classify_axis_range,
)
from ontolib.decomposition.proposal_registry import load_proposal_registry
from ontolib.terminologies.ncit.client import ncit_sparql_client

pytestmark = pytest.mark.unit

_GOLDEN = Path(__file__).with_name("golden")
_ORACLE = _GOLDEN / "neoplasm-adjudicated.json"
_ROWS = _GOLDEN / "neoplasm-row-decisions.json"
_REGISTRY = _GOLDEN / "proposal-registry.json"
_EVIDENCE = _GOLDEN / "neoplasm-current-engine-evidence.json"
_COMPARISON = _GOLDEN / "neoplasm-current-comparison.json"


def _inputs():  # type: ignore[no-untyped-def]
    registry = load_proposal_registry(_REGISTRY)
    return (
        load_adjudication(_ORACLE, registry),
        load_row_decisions(_ROWS),
        registry,
        CurrentEngineEvidence.model_validate_json(_EVIDENCE.read_bytes()),
        CurrentComparison.model_validate_json(_COMPARISON.read_bytes()),
    )


def test_report_exhaustively_separates_revise_and_candidate_diagnostics() -> None:
    oracle, rows, registry, evidence, comparison = _inputs()
    supports: dict[tuple[str, str, str], SourcePairEvidence] = {}
    for result in comparison.row_replay.results:
        if (
            result.row_type == "ADD IF MISSING"
            and result.expected is not None
            and result.status == "selection-miss"
        ):
            supports[(result.code, result.expected.axis, result.expected.filler)] = (
                SourcePairEvidence(stage="extracted", source_definition_ids=("a" * 64,))
            )
    supports[("C27262", "op:Morphology", "C9290")] = SourcePairEvidence(
        stage="source-only",
        source_definition_ids=("b" * 64,),
    )

    range_verdict = classify_axis_range(
        "op:Morphology",
        "C35501",
        "C7057",
        AxisHierarchyEvidence(
            source_identity=evidence.source_identity,
            edges=(HierarchyEdge(child="C35501", parent="C7057"),),
            disjoint_pairs=(),
        ),
    )
    residual_verdict = ResidualPrecoordinationVerdict(
        status="detected",
        reason="production-detector",
        detector_identity=evidence.detector_identity,
    )
    assert isinstance(range_verdict, ValidAxisEvidence)
    report = build_axis_diagnostic_report(
        oracle=oracle,
        rows=rows,
        registry=registry,
        evidence=evidence,
        comparison=comparison,
        source_evidence=supports,
        range_verdicts={("C27262", "op:Morphology", "C35501"): range_verdict},
        residual_verdicts={"C35501": residual_verdict},
    )

    assert len(report.revise_rows) == 42
    assert len(report.candidate_rows) == 64
    assert report.metrics.sme_include_rate.model_dump() == {
        "numerator": 48,
        "denominator": 106,
        "rate": 48 / 106,
    }
    assert (
        report.metrics.exact_pair_precision == comparison.metrics.exact_pair_precision
    )
    c9290 = next(
        row
        for row in report.candidate_rows
        if (row.code, row.expected.axis, row.expected.filler)
        == ("C27262", "op:Morphology", "C9290")
    )
    assert c9290.classification == "added"
    assert c9290.source_definition_ids == ("b" * 64,)
    selection_misses = [
        row for row in report.candidate_rows if row.classification == "selection-miss"
    ]
    assert len(selection_misses) == 16
    assert report.range_diagnostics[0].verdict.model_dump(mode="json") == {
        "status": range_verdict.status,
        "axis": range_verdict.axis,
        "filler_code": range_verdict.filler_code,
        "range_code": range_verdict.range_code,
        "source_identity": range_verdict.source_identity,
        "reason": range_verdict.reason,
        "structural_path": list(range_verdict.structural_path),
    }
    assert report.schema_version == 2
    assert report.range_diagnostics[0].current_projection_status == (
        "scoreable-release-bound"
    )
    assert report.range_diagnostics[0].in_expected_oracle is True
    assert report.residual_diagnostics["C35501"].model_dump(mode="json") == {
        "status": residual_verdict.status,
        "reason": residual_verdict.reason,
        "detector_identity": residual_verdict.detector_identity,
    }


def test_revise_row_reports_pair_group_review_and_provenance_independently() -> None:
    oracle, rows, registry, evidence, comparison = _inputs()

    report = build_axis_diagnostic_report(
        oracle=oracle,
        rows=rows,
        registry=registry,
        evidence=evidence,
        comparison=comparison,
        source_evidence={},
        range_verdicts={},
        residual_verdicts={},
    )

    c35501 = next(
        row
        for row in report.revise_rows
        if (row.code, row.expected.axis, row.expected.filler)
        == ("C27262", "op:Morphology", "C35501")
    )
    assert c35501.historical_pair_delta == "unchanged"
    assert c35501.current_presence == "present"
    assert c35501.group_delta == "changed"
    assert c35501.review_delta == "unchanged"
    assert c35501.provenance_delta == "unchanged"


def test_report_identity_rejects_rebound_payload() -> None:
    oracle, rows, registry, evidence, comparison = _inputs()
    report = build_axis_diagnostic_report(
        oracle=oracle,
        rows=rows,
        registry=registry,
        evidence=evidence,
        comparison=comparison,
        source_evidence={},
        range_verdicts={},
        residual_verdicts={},
    )
    payload = report.model_dump(mode="json")
    payload["source_identity"] = "f" * 64

    with pytest.raises(ValueError, match="report identity"):
        type(report).model_validate_json(json.dumps(payload))


def test_current_projection_status_is_typed_and_independent_from_range_verdict() -> (
    None
):
    oracle, rows, registry, evidence, comparison = _inputs()
    verdict = classify_axis_range(
        "op:ClinicalFinding",
        "C47806",
        "C36292",
        AxisHierarchyEvidence(
            source_identity=evidence.source_identity,
            edges=(),
            disjoint_pairs=(),
        ),
    )
    report = build_axis_diagnostic_report(
        oracle=oracle,
        rows=rows,
        registry=registry,
        evidence=evidence,
        comparison=comparison,
        source_evidence={},
        range_verdicts={
            ("C101539", "op:ClinicalFinding", "C47806"): verdict,
            ("C132677", "op:ClinicalFinding", "C41444"): verdict,
        },
        residual_verdicts={},
    )
    statuses = {
        row.filler: row.current_projection_status for row in report.range_diagnostics
    }
    assert statuses == {
        "C47806": "review-bearing-release-bound",
        "C41444": "not-emitted",
    }
    baseline = report.range_diagnostics[0]
    changed_verdict = baseline.model_copy(
        update={"verdict": report.range_diagnostics[1].verdict}
    )
    assert changed_verdict.current_projection_status == (
        baseline.current_projection_status
    )
    assert set(
        get_args(
            PairRangeDiagnostic.model_fields["current_projection_status"].annotation
        )
    ) == {
        "scoreable-release-bound",
        "review-bearing-release-bound",
        "provisional-proposed",
        "not-emitted",
    }


@pytest.mark.full_store
@pytest.mark.integration
async def test_generator_writes_identity_bound_packet_without_changing_inputs(
    tmp_path: Path,
) -> None:
    output = tmp_path / "axis-diagnostics.json"
    before = {
        path: path.read_bytes()
        for path in (_ORACLE, _ROWS, _REGISTRY, _EVIDENCE, _COMPARISON)
    }

    report = await generate_axis_diagnostic_report(
        source_manifest=Path("data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        endpoint="http://localhost:7888",
        oracle_path=_ORACLE,
        row_decisions_path=_ROWS,
        proposal_registry_path=_REGISTRY,
        current_evidence_path=_EVIDENCE,
        current_comparison_path=_COMPARISON,
        residual_fillers=("C35501", "C12431", "MINT-781c8c8c6096"),
        output=output,
    )

    assert output.exists()
    assert AxisDiagnosticReport.model_validate_json(output.read_bytes()) == report
    assert len(report.range_diagnostics) >= 153
    assert report.residual_diagnostics["C35501"].status == "detected"
    invalid = [
        row for row in report.range_diagnostics if row.verdict.status == "invalid"
    ]
    assert [
        (
            row.code,
            row.axis,
            row.filler,
            row.current_projection_status,
            row.in_expected_oracle,
            row.verdict.status,
        )
        for row in invalid
    ] == [
        (
            "C35756",
            "op:StageSystem",
            "C141685",
            "not-emitted",
            True,
            "invalid",
        )
    ]
    assert all(path.read_bytes() == contents for path, contents in before.items())


@pytest.mark.unit
async def test_generator_refuses_missing_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input does not exist"):
        await generate_axis_diagnostic_report(
            source_manifest=tmp_path / "missing.json",
            endpoint="http://localhost:7888",
            oracle_path=_ORACLE,
            row_decisions_path=_ROWS,
            proposal_registry_path=_REGISTRY,
            current_evidence_path=_EVIDENCE,
            current_comparison_path=_COMPARISON,
            residual_fillers=(),
            output=tmp_path / "output.json",
        )


@pytest.mark.unit
def test_axis_diagnostic_cli_requires_all_inputs_and_residual_set() -> None:
    args = _parser().parse_args(
        [
            "generate-axis-diagnostics",
            "--source-manifest",
            "source.json",
            "--endpoint",
            "http://localhost:7888",
            "--oracle",
            "oracle.json",
            "--row-decisions",
            "rows.json",
            "--proposal-registry",
            "registry.json",
            "--current-evidence",
            "evidence.json",
            "--current-comparison",
            "comparison.json",
            "--residual-filler",
            "C35501",
            "--residual-filler",
            "C12431",
            "--output",
            "report.json",
        ]
    )

    assert args.command == "generate-axis-diagnostics"
    assert args.residual_filler == ["C35501", "C12431"]
    assert args.output == Path("report.json")


@pytest.mark.unit
def test_direct_adjudication_entrypoint_loads_axis_command() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/adjudication.py",
            "generate-axis-diagnostics",
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--residual-filler" in result.stdout


@pytest.mark.full_store
@pytest.mark.integration
async def test_source_collector_cites_genus_without_inventing_contextual_facts() -> (
    None
):
    _oracle, rows, _registry, _evidence, _comparison = _inputs()
    async with ncit_sparql_client(
        "http://localhost:7888", query_timeout=180.0
    ) as client:
        source = await collect_source_pair_evidence(
            client,
            rows,
            concept_codes=("C27262",),
        )

    c9290 = source[("C27262", "op:Morphology", "C9290")]
    assert c9290.stage == "source-only"
    assert c9290.source_definition_ids == (
        "aad190c812e6e9587657af7cc2ed9aa858a092b649109ea5b5a523543056cacf",
    )
    assert ("C27262", "op:AssociatedRegion", "C41165") not in source


@pytest.mark.full_store
@pytest.mark.integration
async def test_residual_collector_is_bounded_typed_and_detector_relative() -> None:
    async with ncit_sparql_client(
        "http://localhost:7888", query_timeout=180.0
    ) as client:
        verdicts = await collect_residual_verdicts(
            client,
            ("C35501", "C12431", "MINT-781c8c8c6096"),
            detector_identity="d" * 64,
            walker_max_depth=5,
        )

    assert verdicts["C35501"].status == "detected"
    assert verdicts["C12431"].status == "not-detected"
    assert verdicts["MINT-781c8c8c6096"] == ResidualPrecoordinationVerdict(
        status="unknown",
        reason="proposed-filler-not-in-source",
        detector_identity="d" * 64,
    )


@pytest.mark.unit
async def test_residual_collector_rejects_unbounded_or_duplicate_requests() -> None:
    with pytest.raises(ValueError, match="unique"):
        await collect_residual_verdicts(
            object(),
            ("C1", "C1"),
            detector_identity="d" * 64,
            walker_max_depth=5,
        )
    with pytest.raises(ValueError, match="at most 8"):
        await collect_residual_verdicts(
            object(),
            tuple(f"C{index}" for index in range(1, 10)),
            detector_identity="d" * 64,
            walker_max_depth=5,
        )
