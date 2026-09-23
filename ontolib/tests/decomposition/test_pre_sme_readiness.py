from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from scripts.research.current_evidence import (
    CurrentComparison,
    CurrentEngineEvidence,
    CurrentRateMetric,
)
from scripts.research.pre_sme_readiness import (
    ClearSemanticBlocker,
    MachineReadinessInputs,
    PreSmeValidationError,
    PrimarySiteAudit,
    PrimarySiteObservation,
    ReadinessMetrics,
    audit_primary_site_artifact,
    build_machine_readiness,
    generate_pre_sme_readiness,
    generate_primary_site_audit,
    require_current_verify_evidence,
    write_verify_evidence,
)

from ontolib.decomposition.corpus_baseline import (
    CorpusBaseline,
    corpus_baseline_identity,
)
from ontolib.decomposition.evaluation import MetricDenominatorRule
from ontolib.decomposition.r103_review_promotion import (
    load_r103_promoted_review_revision,
)
from ontolib.decomposition.r103_specificity_review import (
    R103SelectedSpecificityReview,
)

_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
_OP = "https://w3id.org/ontoprism/vocab#"


def _site_line(subject: str, filler: str, *, review: bool = False) -> str:
    review_triple = f" <{_OP}needsReview> true ;" if review else ""
    return (
        f"<{_NCIT}{subject}> <{_OP}hasConstituent> ["
        f" <{_OP}axis> <{_OP}PrimarySite> ;"
        f" <{_OP}filler> <{_NCIT}{filler}> ;{review_triple}"
        f" <{_OP}sourceRole> <{_NCIT}R101> ] .\n"
    )


def _baseline(
    artifact: Path,
    *,
    source_identity: str = "a" * 64,
    ontology_release: str = "26.07d",
) -> CorpusBaseline:
    artifact_identity = hashlib.sha256(artifact.read_bytes()).hexdigest()
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": "full-run",
        "source_identity": source_identity,
        "ontology_release": ontology_release,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "run_fingerprint_identity": "b" * 64,
        "representation_identity": artifact_identity,
        "artifact_identity": artifact_identity,
        "detector_identity": "c" * 64,
        "worklist_count": 2,
        "outcome_counts": {
            "decomposed": 2,
            "residual": 0,
            "semantic_excluded": 0,
            "atomic_noop": 0,
            "unknown": 0,
        },
        "emitted_constituent_pair_count": 3,
        "complete_semantic_fact_count": 3,
        "source_occurrence_count": 3,
        "selected_occurrence_count": 3,
        "minted_count": 0,
    }
    return CorpusBaseline.model_validate(
        {**payload, "baseline_identity": corpus_baseline_identity(payload)}
    )


def _stale_grouping_artifacts(
    evidence: CurrentEngineEvidence,
    comparison: CurrentComparison,
    normalized_group_policy: Any,
) -> tuple[CurrentEngineEvidence, CurrentComparison]:
    policy_row = normalized_group_policy.rows[0]
    target_pair = policy_row.blocks[0].pairs[0]
    concept_index, concept = next(
        (index, item)
        for index, item in enumerate(evidence.concepts)
        if item.code == policy_row.concept_code
    )
    constituent_index, constituent = next(
        (index, item)
        for index, item in enumerate(concept.constituents)
        if (item.axis, item.filler) == target_pair
    )
    mutated_constituent = constituent.model_copy(
        update={
            "normalized_group_id": "0" * 64,
            "normalized_group_label": (
                f"stale-grouping:{policy_row.concept_code}:{'0' * 12}"
            ),
        }
    )
    constituents = list(concept.constituents)
    constituents[constituent_index] = mutated_constituent
    concepts = list(evidence.concepts)
    concepts[concept_index] = concept.model_copy(
        update={"constituents": tuple(constituents)}
    )
    mutated_evidence = evidence.model_copy(update={"concepts": tuple(concepts)})
    evidence_payload = mutated_evidence.model_dump(
        mode="json", exclude={"evidence_identity"}
    )
    current_evidence = CurrentEngineEvidence.model_validate_json(
        json.dumps(
            {**evidence_payload, "evidence_identity": _identity(evidence_payload)}
        )
    )
    comparison_payload = comparison.model_dump(
        mode="json", exclude={"comparison_identity"}
    )
    comparison_payload["current_evidence_identity"] = current_evidence.evidence_identity
    current_comparison = CurrentComparison.model_validate_json(
        json.dumps(
            {
                **comparison_payload,
                "comparison_identity": _identity(comparison_payload),
            }
        )
    )
    return current_evidence, current_comparison


def _patch_composed_readiness_loaders(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    golden: Path,
    baseline: CorpusBaseline,
    evidence: CurrentEngineEvidence,
    group: Any,
) -> None:
    source_fixture = module.load_source_inventory(
        golden / "r103-source-inventory-26.07d.json"
    )
    candidate_fixture = module.load_candidate_artifact(
        golden / "r103-c12950-candidates-26.07d.json"
    )
    fixture_manifest_identity = module._identity({})
    monkeypatch.setattr(
        module,
        "validate_ncit_sibling_manifest",
        lambda _path: SimpleNamespace(
            source_identity=baseline.source_identity,
            ontology_version=baseline.ontology_release,
            stated_artifact=SimpleNamespace(
                artifact_identity=source_fixture.source_artifact_identity,
                sha256=source_fixture.source_artifact_sha256,
                size_bytes=source_fixture.source_artifact_size,
            ),
            graph_layout=SimpleNamespace(
                stated_graph_iri=source_fixture.stated_graph_iri
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "load_source_inventory",
        lambda _path: source_fixture.model_copy(
            update={"source_manifest_identity": fixture_manifest_identity}
        ),
    )
    monkeypatch.setattr(
        module,
        "load_candidate_artifact",
        lambda _path: candidate_fixture.model_copy(
            update={"source_manifest_identity": fixture_manifest_identity}
        ),
    )
    monkeypatch.setattr(module, "load_corpus_baseline", lambda _path: baseline)
    monkeypatch.setattr(
        module,
        "validate_migrated_proposal_registry",
        lambda _migration, _path: SimpleNamespace(
            registry_identity=evidence.proposal_registry_identity
        ),
    )
    monkeypatch.setattr(module, "load_group_review_packet", lambda _path: group)


def _composed_readiness_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stale_grouping: bool = False,
) -> tuple[dict[str, Any], Any, Any, Any, Any]:
    module = __import__(
        "scripts.research.pre_sme_readiness", fromlist=["generate_pre_sme_readiness"]
    )
    golden = Path(__file__).parent / "golden"
    evidence_path = golden / "neoplasm-current-engine-evidence.json"
    comparison_path = golden / "neoplasm-current-comparison.json"
    evidence = CurrentEngineEvidence.model_validate_json(evidence_path.read_bytes())
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    normalized_group_policy = module.load_packaged_normalized_group_policy()
    if stale_grouping:
        evidence, comparison = _stale_grouping_artifacts(
            evidence, comparison, normalized_group_policy
        )
        evidence_path = tmp_path / "stale-grouping-evidence.json"
        comparison_path = tmp_path / "stale-grouping-comparison.json"
        evidence_path.write_text(evidence.model_dump_json(), encoding="utf-8")
        comparison_path.write_text(comparison.model_dump_json(), encoding="utf-8")
    corpus_artifact = tmp_path / "corpus.ttl"
    corpus_artifact.write_text(_site_line("C1", "C10"))
    baseline = _baseline(
        corpus_artifact,
        source_identity=evidence.source_identity,
        ontology_release=evidence.ncit_version,
    )
    monkeypatch.setattr(
        module,
        "_configured_r101_counts",
        lambda _run_id: module.R101ConservationCounts(
            total=0,
            projected=0,
            unchanged_unprojected=0,
            one_step_r82=0,
            closure_only_r82=0,
            unresolved=0,
        ),
    )
    audit = audit_primary_site_artifact(
        artifact=corpus_artifact,
        baseline=baseline,
        source_identity=evidence.source_identity,
        source_release=evidence.ncit_version,
    )
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(audit.model_dump_json())
    verify_path = tmp_path / "verify.json"
    write_verify_evidence(
        verify_path,
        git_head="a" * 40,
        docker_context="ontoprism-podman",
        docker_endpoint="unix:///tmp/podman.sock",
        gate_executable="/opt/homebrew/bin/pdm",
        gate_version="PDM, version test",
        observed_exit_code=0,
    )
    group = SimpleNamespace(
        current_evidence_identity=evidence.evidence_identity,
        current_comparison_identity=comparison.comparison_identity,
        packet_identity=normalized_group_policy.basis_packet_identity,
        review_rows=(None,) * 18,
    )
    _patch_composed_readiness_loaders(
        module, monkeypatch, golden, baseline, evidence, group
    )
    unused = tmp_path / "unused.json"
    unused.write_text("{}")
    detector_path = tmp_path / "grouping-detector.json"
    detector_payload = {
        "schema_version": 1,
        "status": "clear",
        "current_evidence_identity": evidence.evidence_identity,
        "current_comparison_identity": comparison.comparison_identity,
        "group_packet_identity": group.packet_identity,
        "normalized_group_policy_identity": normalized_group_policy.policy_identity,
        "axis_contract_violations": (),
        "normalized_group_violations": (),
        "unadjudicated_golden_changes": (),
    }
    detector = module.Issue274DetectorReport.model_validate(
        {**detector_payload, "report_identity": module._identity(detector_payload)}
    )
    detector_path.write_text(detector.model_dump_json(), encoding="utf-8")
    if not stale_grouping:
        monkeypatch.setattr(
            module,
            "_issue_274_semantic_violations",
            lambda _evidence, _comparison, _policy, _packet_identity: ((), (), ()),
        )
    original_r103_loader = module.load_r103_promoted_review_revision

    def load_r103_with_fixture_manifest(path: Path) -> Any:
        revision = original_r103_loader(path)
        packet = revision.packet.model_copy(
            update={"candidate_manifest_identity": module._identity({})}
        )
        return SimpleNamespace(
            packet=packet,
            registry=revision.registry,
            predecessor=revision.predecessor,
            artifact_identity=revision.artifact_identity,
        )

    monkeypatch.setattr(
        module,
        "load_r103_promoted_review_revision",
        load_r103_with_fixture_manifest,
    )
    arguments: dict[str, Any] = {
        "source_manifest": manifest,
        "current_evidence": evidence_path,
        "current_comparison": comparison_path,
        "corpus_baseline": unused,
        "corpus_artifact": corpus_artifact,
        "proposal_registry": unused,
        "proposal_registry_migration": golden
        / "proposal-registry-schema2-migration.json",
        "row_decisions": golden / "neoplasm-row-decisions.json",
        "primary_site_audit": audit_path,
        "group_packet": unused,
        "grouping_detector": detector_path,
        "r103_review_state": golden / "r103-review-state-26.07d-rev2.json",
        "r103_source_inventory": golden / "r103-source-inventory-26.07d.json",
        "r103_candidates": golden / "r103-c12950-candidates-26.07d.json",
        "r103_authority": golden / "r103-authority-normalized-26.07d.json",
        "r103_corroboration": golden / "r103-corroboration-normalized-26.07d.json",
        "r103_applied_policy": golden / "r103-applied-policy-26.07d.json",
        "r103_specificity_target": golden / "r103-c2860-specificity-target-26.07d.json",
        "r103_specificity_review": golden
        / "r103-c2860-specificity-selected-26.07d.json",
        "verify_evidence": verify_path,
        "expected_git_head": "a" * 40,
        "output": tmp_path / "readiness.json",
    }
    return arguments, module, None, comparison, group


@pytest.mark.unit
def test_primary_site_liveness_records_two_resolved_and_minus_one_clears(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "corpus.ttl"
    artifact.write_text(
        _site_line("C1", "C10")
        + _site_line("C1", "C11")
        + _site_line("C2", "C12", review=True)
    )

    blocked = audit_primary_site_artifact(
        artifact=artifact,
        baseline=_baseline(artifact),
        source_identity="a" * 64,
        source_release="26.07d",
    )

    assert [item.model_dump() for item in blocked.cardinality_violations] == [
        {"concept_code": "C1", "filler_codes": ("C10", "C11")}
    ]

    artifact.write_text(_site_line("C1", "C10") + _site_line("C2", "C12", review=True))
    audit = audit_primary_site_artifact(
        artifact=artifact,
        baseline=_baseline(artifact),
        source_identity="a" * 64,
        source_release="26.07d",
    )

    assert audit.resolved_site_count == 1
    assert audit.review_required_site_count == 1
    assert audit.cardinality_violations == ()
    assert audit.parser_passes == 1


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["identity", "malformed", "duplicate", "absent"])
def test_primary_site_audit_refuses_bad_inputs_without_output(
    tmp_path: Path, failure: str
) -> None:
    artifact = tmp_path / "corpus.ttl"
    artifact.write_text(_site_line("C1", "C10"))
    baseline = _baseline(artifact)
    if failure == "identity":
        artifact.write_text(_site_line("C1", "C11"))
    elif failure == "malformed":
        artifact.write_text("this is not Turtle\n")
        baseline = _baseline(artifact)
    elif failure == "duplicate":
        line = _site_line("C1", "C10")
        artifact.write_text(line + line)
        baseline = _baseline(artifact)
    else:
        artifact.unlink()
    output = tmp_path / "audit.json"

    with pytest.raises(PreSmeValidationError):
        audit_primary_site_artifact(
            artifact=artifact,
            baseline=baseline,
            source_identity="a" * 64,
            source_release="26.07d",
            output=output,
        )

    assert not output.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("triples", "message"),
    [
        (
            f"""@prefix op: <{_OP}> .
@prefix ncit: <{_NCIT}> .
ncit:C1 op:hasConstituent _:site .
ncit:C2 op:hasConstituent _:site .
_:site op:axis op:PrimarySite ; op:filler ncit:C10 .
""",
            "reused constituent blank node",
        ),
        (
            f"""@prefix op: <{_OP}> .
@prefix ncit: <{_NCIT}> .
ncit:C1 op:hasConstituent [ op:axis op:PrimarySite, op:PrimarySite ;
                            op:filler ncit:C10 ] .
""",
            "duplicate primary-site axis",
        ),
        (
            f"""@prefix op: <{_OP}> .
@prefix ncit: <{_NCIT}> .
ncit:C1 op:hasConstituent [ op:filler ncit:C10 ] .
""",
            "primary-site axis is missing",
        ),
    ],
)
def test_primary_site_parser_rejects_non_total_constituent_observations(
    tmp_path: Path, triples: str, message: str
) -> None:
    artifact = tmp_path / "corpus.ttl"
    artifact.write_text(triples)

    with pytest.raises(PreSmeValidationError, match=message):
        audit_primary_site_artifact(
            artifact=artifact,
            baseline=_baseline(artifact),
            source_identity="a" * 64,
            source_release="26.07d",
        )


@pytest.mark.unit
@pytest.mark.parametrize("axis", ['"not-an-iri"', "[]"])
def test_primary_site_parser_rejects_non_uri_axis_objects(
    tmp_path: Path, axis: str
) -> None:
    artifact = tmp_path / "corpus.ttl"
    artifact.write_text(
        f"""@prefix op: <{_OP}> .
@prefix ncit: <{_NCIT}> .
ncit:C1 op:hasConstituent [ op:axis {axis} ; op:filler ncit:C10 ] .
"""
    )

    with pytest.raises(PreSmeValidationError, match="axis is not an IRI"):
        audit_primary_site_artifact(
            artifact=artifact,
            baseline=_baseline(artifact),
            source_identity="a" * 64,
            source_release="26.07d",
        )


@pytest.mark.unit
def test_primary_site_parser_skips_non_primary_uri_axis(tmp_path: Path) -> None:
    artifact = tmp_path / "corpus.ttl"
    artifact.write_text(
        _site_line("C1", "C10")
        + f"""@prefix op: <{_OP}> .
@prefix ncit: <{_NCIT}> .
ncit:C2 op:hasConstituent [ op:axis op:PrimarySubsite ; op:filler ncit:C11 ] .
"""
    )

    audit = audit_primary_site_artifact(
        artifact=artifact,
        baseline=_baseline(artifact),
        source_identity="a" * 64,
        source_release="26.07d",
    )

    assert audit.resolved_sites == (
        PrimarySiteObservation(concept_code="C1", filler_code="C10"),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("resolved", "review", "message"),
    [
        (
            (
                PrimarySiteObservation(concept_code="C1", filler_code="C10"),
                PrimarySiteObservation(concept_code="C1", filler_code="C10"),
            ),
            (),
            "pairwise distinct",
        ),
        (
            (PrimarySiteObservation(concept_code="C1", filler_code="C10"),),
            (PrimarySiteObservation(concept_code="C1", filler_code="C10"),),
            "pairwise distinct",
        ),
    ],
)
def test_primary_site_audit_model_rejects_vacuous_observation_invariants(
    resolved: tuple[PrimarySiteObservation, ...],
    review: tuple[PrimarySiteObservation, ...],
    message: str,
) -> None:
    payload = {
        "schema_version": 2,
        "source_identity": "a" * 64,
        "source_release": "26.07d",
        "corpus_baseline_identity": "b" * 64,
        "corpus_artifact_identity": "c" * 64,
        "resolved_sites": resolved,
        "review_required_sites": review,
        "cardinality_violations": (),
        "resolved_site_count": len(resolved),
        "review_required_site_count": len(review),
        "parser_passes": 1,
        "audit_identity": "d" * 64,
    }

    with pytest.raises(ValueError, match=message):
        PrimarySiteAudit.model_validate(payload)


@pytest.mark.unit
def test_primary_site_audit_model_refuses_zero_observations() -> None:
    payload = {
        "schema_version": 2,
        "source_identity": "a" * 64,
        "source_release": "26.07d",
        "corpus_baseline_identity": "b" * 64,
        "corpus_artifact_identity": "c" * 64,
        "resolved_sites": (),
        "review_required_sites": (),
        "cardinality_violations": (),
        "resolved_site_count": 0,
        "review_required_site_count": 0,
        "parser_passes": 1,
        "audit_identity": "d" * 64,
    }

    with pytest.raises(ValueError, match="at least one observation"):
        PrimarySiteAudit.model_validate(payload)


def _machine_readiness_input_payload() -> dict[str, object]:
    return {
        "source_identity": "a" * 64,
        "source_manifest_identity": "b" * 64,
        "current_evidence_identity": "c" * 64,
        "current_comparison_identity": "d" * 64,
        "sample_artifact_identity": "e" * 64,
        "corpus_baseline_identity": "f" * 64,
        "corpus_artifact_identity": "1" * 64,
        "proposal_registry_identity": "7" * 64,
        "proposal_registry_migration_identity": "c" * 64,
        "row_decisions_identity": "d" * 64,
        "primary_site_audit_identity": "8" * 64,
        "primary_site_resolved_count": 1,
        "primary_site_review_required_count": 0,
        "primary_site_cardinality_violations": (),
        "group_packet_identity": "9" * 64,
        "normalized_group_policy_identity": "1" * 64,
        "grouping_detector_report_identity": "2" * 64,
        "axis_contract_violations": (),
        "normalized_group_violations": (),
        "unadjudicated_golden_changes": (),
        "r101_run_id": "neoplasm-run-1",
        "r101_unresolved_count": 0,
        "r103_packet_identity": "0" * 64,
        "verify_evidence_identity": "a" * 64,
        "git_head": "b" * 40,
        "exact_pair_true_positive": 100,
        "exact_pair_emitted": 108,
        "exact_pair_expected": 153,
        "historical_sme_include_count": 48,
        "historical_engine_suggestion_count": 106,
        "full_partition_agreement": {
            "numerator": 2,
            "denominator": 20,
            "value": 0.1,
        },
        "common_partition_agreement": {
            "numerator": 5,
            "denominator": 18,
            "value": 5 / 18,
            "ineligible": 2,
        },
        "group_review_count": 18,
        "r103_review_count": 3,
    }


@pytest.mark.unit
def test_emitted_report_carries_all_five_metric_contracts_and_current_values() -> None:
    report = build_machine_readiness(
        MachineReadinessInputs.model_validate(_machine_readiness_input_payload())
    )

    assert report.schema_version == 3
    assert tuple(
        (view.name, view.denominator_rule)
        for view in (
            report.metrics.sme_include_rate,
            report.metrics.exact_pair_precision,
            report.metrics.exact_pair_recall,
            report.metrics.full_partition_agreement,
            report.metrics.common_pair_partition_agreement,
        )
    ) == (
        ("sme_include_rate", "historical_engine_suggestion_rows"),
        ("exact_pair_precision", "current_emitted_ncit_bound_scoreable_pairs"),
        ("exact_pair_recall", "ncit_bound_non_deferred_oracle_expectations"),
        ("full_partition_agreement", "accepted_20_concept_cohort"),
        (
            "common_pair_partition_agreement",
            "concepts_with_at_least_two_shared_pairs",
        ),
    )
    assert report.metrics.sme_include_rate.fraction.model_dump() == {
        "numerator": 48,
        "denominator": 106,
        "value": 48 / 106,
    }
    assert report.metrics.exact_pair_precision.fraction.numerator == 100
    assert report.metrics.exact_pair_recall.fraction.numerator == 100
    assert report.metrics.full_partition_agreement.fraction.denominator == 20
    common = report.metrics.common_pair_partition_agreement.fraction
    assert common.denominator == 18
    assert common.ineligible == 2
    assert report.quality_target.precision_at_least_90_percent is True
    assert report.quality_target.recall_at_least_90_percent is False
    assert report.quality_target.meets_quality_target is False


@pytest.mark.unit
def test_historical_include_rate_is_independent_of_current_engine_results() -> None:
    baseline = _machine_readiness_input_payload()
    changed = {**baseline, "exact_pair_true_positive": 81, "exact_pair_emitted": 100}

    first = build_machine_readiness(MachineReadinessInputs.model_validate(baseline))
    second = build_machine_readiness(MachineReadinessInputs.model_validate(changed))

    assert first.metrics.sme_include_rate == second.metrics.sme_include_rate
    assert first.metrics.exact_pair_precision != second.metrics.exact_pair_precision


@pytest.mark.unit
@pytest.mark.parametrize(
    ("true_positive", "emitted", "expected", "precision", "recall", "status"),
    [
        (80, 106, 153, False, False, "failed"),
        (81, 107, 153, True, True, "passed"),
        (80, 105, 153, True, False, "failed"),
        (81, 108, 154, False, True, "failed"),
    ],
)
def test_m1_6_improvement_gate_uses_exact_strict_fraction_comparisons(
    true_positive: int,
    emitted: int,
    expected: int,
    precision: bool,
    recall: bool,
    status: str,
) -> None:
    payload = _machine_readiness_input_payload()
    payload.update(
        exact_pair_true_positive=true_positive,
        exact_pair_emitted=emitted,
        exact_pair_expected=expected,
    )

    gate = build_machine_readiness(
        MachineReadinessInputs.model_validate(payload)
    ).m1_6_improvement

    assert (gate.precision_improved, gate.recall_improved, gate.status) == (
        precision,
        recall,
        status,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("true_positive", "emitted", "expected", "precision", "recall", "meets"),
    [
        (90, 100, 100, True, True, True),
        (89, 98, 100, True, False, False),
        (89, 100, 98, False, True, False),
    ],
)
def test_quality_target_indicators_have_independent_inclusive_boundaries(
    true_positive: int,
    emitted: int,
    expected: int,
    precision: bool,
    recall: bool,
    meets: bool,
) -> None:
    payload = _machine_readiness_input_payload()
    payload.update(
        exact_pair_true_positive=true_positive,
        exact_pair_emitted=emitted,
        exact_pair_expected=expected,
    )

    target = build_machine_readiness(
        MachineReadinessInputs.model_validate(payload)
    ).quality_target

    assert (
        target.precision_at_least_90_percent,
        target.recall_at_least_90_percent,
        target.meets_quality_target,
    ) == (precision, recall, meets)


@pytest.mark.unit
def test_semantic_gate_taxonomy_is_complete_and_issue_274_detectors_are_clear() -> None:
    report = build_machine_readiness(
        MachineReadinessInputs.model_validate(_machine_readiness_input_payload())
    )

    assert report.semantic_gate.status == "not-evaluated"
    assert [entry.kind for entry in report.semantic_gate.entries] == [
        "unclassified-delta",
        "axis-contract-violation",
        "normalized-group-violation",
        "unadjudicated-golden-change",
        "primary-site-cardinality",
        "unexplained-r101-loss",
    ]
    assert report.semantic_gate.entries[0].status == "not-evaluated"
    r101_loss = next(
        entry
        for entry in report.semantic_gate.entries
        if entry.kind == "unexplained-r101-loss"
    )
    assert r101_loss.status == "clear"
    assert isinstance(r101_loss, ClearSemanticBlocker)
    assert r101_loss.evidence == ("decomposition-run:neoplasm-run-1",)
    evaluated = tuple(
        cast("ClearSemanticBlocker", entry)
        for entry in report.semantic_gate.entries[1:4]
    )
    assert all(isinstance(entry, ClearSemanticBlocker) for entry in evaluated)
    assert all(entry.status == "clear" for entry in evaluated)
    assert all(entry.blocker_count == 0 for entry in evaluated)
    assert evaluated[0].evidence == (
        f"current-evidence:{report.identities.current_evidence_identity}",
    )
    assert evaluated[1].evidence == (
        f"normalized-group-policy:{report.identities.normalized_group_policy_identity}",
    )
    assert evaluated[2].evidence == (
        f"current-comparison:{report.identities.current_comparison_identity}",
        f"normalized-group-policy:{report.identities.normalized_group_policy_identity}",
    )
    assert report.authorization is False
    assert report.publication.status == "not-attempted"


@pytest.mark.unit
def test_unexplained_r101_loss_blocks_machine_readiness() -> None:
    payload = _machine_readiness_input_payload()
    payload["r101_unresolved_count"] = 2

    report = build_machine_readiness(MachineReadinessInputs.model_validate(payload))

    blocker = next(
        entry
        for entry in report.semantic_gate.entries
        if entry.kind == "unexplained-r101-loss"
    )
    assert blocker.status == "blocked"
    assert blocker.blocker_count == 2  # type: ignore[union-attr]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "kind", "count"),
    [
        (
            "primary_site_cardinality_violations",
            ({"concept_code": "C1", "filler_codes": ("C10", "C11")},),
            "primary-site-cardinality",
            1,
        ),
        (
            "axis_contract_violations",
            ("C1:op:UnknownAxis",),
            "axis-contract-violation",
            1,
        ),
        (
            "normalized_group_violations",
            ("C1:group-mismatch",),
            "normalized-group-violation",
            1,
        ),
        (
            "unadjudicated_golden_changes",
            ("C1:unbound-change",),
            "unadjudicated-golden-change",
            1,
        ),
    ],
)
def test_supported_semantic_violations_emit_blocked_reports(
    field: str, value: object, kind: str, count: int
) -> None:
    payload = _machine_readiness_input_payload()
    payload[field] = value

    report = build_machine_readiness(MachineReadinessInputs.model_validate(payload))
    blocker = next(
        entry for entry in report.semantic_gate.entries if entry.kind == kind
    )

    assert report.semantic_gate.status == "blocked"
    assert blocker.status == "blocked"
    assert blocker.blocker_count == count
    assert blocker.evidence


@pytest.mark.unit
def test_high_metrics_cannot_clear_incomplete_semantic_gate() -> None:
    payload = _machine_readiness_input_payload()
    payload["exact_pair_true_positive"] = 100
    payload["exact_pair_emitted"] = 100
    payload["exact_pair_expected"] = 100

    report = build_machine_readiness(MachineReadinessInputs.model_validate(payload))

    assert report.quality_target.meets_quality_target is True
    assert report.semantic_gate.status == "not-evaluated"


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["duplicate-kind", "blocked-zero"])
def test_semantic_gate_rejects_invalid_correlated_variants(mutation: str) -> None:
    report = build_machine_readiness(
        MachineReadinessInputs.model_validate(_machine_readiness_input_payload())
    )
    payload = report.model_dump(mode="python")
    entries = list(payload["semantic_gate"]["entries"])
    if mutation == "duplicate-kind":
        entries[1]["kind"] = entries[0]["kind"]
    elif mutation == "blocked-zero":
        entries[4] = {
            "kind": "primary-site-cardinality",
            "status": "blocked",
            "blocker_count": 0,
            "evidence": ["audit:" + "8" * 64],
        }
    payload["semantic_gate"]["entries"] = tuple(entries)

    with pytest.raises(
        ValueError, match=r"semantic|blocker|greater than 0|Extra inputs"
    ):
        type(report).model_validate(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("identities", "row_decisions_identity"), "f" * 64),
        (("metrics", "sme_include_rate", "fraction", "numerator"), 47),
        (("m1_6_improvement", "precision_improved"), False),
        (("quality_target", "recall_at_least_90_percent"), True),
        (("semantic_gate", "entries", 0, "reason"), "changed reason"),
    ],
)
def test_report_identity_binds_every_new_load_bearing_section(
    path: tuple[str | int, ...], replacement: object
) -> None:
    report = build_machine_readiness(
        MachineReadinessInputs.model_validate(_machine_readiness_input_payload())
    )
    payload = report.model_dump(mode="python")
    target: Any = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement

    with pytest.raises(
        ValueError, match=r"identity|indicator|fraction|metric|semantic|conjunction"
    ):
        type(report).model_validate(payload)


@pytest.mark.unit
def test_machine_readiness_inputs_serialize_validated_grouping_fractions() -> None:
    inputs = MachineReadinessInputs.model_validate(_machine_readiness_input_payload())

    serialized = inputs.model_dump(mode="json")
    assert serialized["full_partition_agreement"] == {
        "numerator": 2,
        "denominator": 20,
        "value": 0.1,
    }
    assert serialized["common_partition_agreement"] == {
        "numerator": 5,
        "denominator": 18,
        "value": 5 / 18,
        "ineligible": 2,
    }


@pytest.mark.unit
def test_machine_readiness_inputs_refuse_empty_grouping_denominator() -> None:
    payload = _machine_readiness_input_payload()
    payload["full_partition_agreement"] = {
        "numerator": 0,
        "denominator": 0,
        "value": 0.0,
    }

    with pytest.raises(ValueError, match="greater than 0"):
        MachineReadinessInputs.model_validate(payload)


@pytest.mark.unit
def test_grouping_views_require_common_and_ineligible_to_cover_full_cohort() -> None:
    report = build_machine_readiness(
        MachineReadinessInputs.model_validate(_machine_readiness_input_payload())
    )
    metrics = report.metrics.model_dump(mode="python")
    metrics["common_pair_partition_agreement"]["fraction"]["ineligible"] = 1

    with pytest.raises(ValueError, match="grouping denominators"):
        ReadinessMetrics.model_validate(metrics)


@pytest.mark.unit
def test_machine_readiness_inputs_require_grouping_views_to_share_one_cohort() -> None:
    payload = _machine_readiness_input_payload()
    common = payload["common_partition_agreement"]
    assert isinstance(common, dict)
    common["ineligible"] = 1

    with pytest.raises(ValueError, match="grouping denominators"):
        MachineReadinessInputs.model_validate(payload)


@pytest.mark.unit
def test_verify_evidence_writer_documents_fixed_publication_field_as_a_claim() -> None:
    docstring = inspect.getdoc(write_verify_evidence)

    assert docstring is not None
    assert "observed fields" in docstring
    assert "fixed no-publication assertion" in docstring


@pytest.mark.unit
def test_machine_readiness_keeps_human_decisions_pending_without_claiming_delta() -> (
    None
):
    payload = _machine_readiness_input_payload()
    payload.update(
        primary_site_resolved_count=8039,
        primary_site_review_required_count=5918,
    )
    report = build_machine_readiness(MachineReadinessInputs.model_validate(payload))

    assert report.status == "awaiting-later-evaluation"
    assert report.authorization is False
    assert report.publication.status == "not-attempted"
    assert report.publication.publication_writes_performed is False
    assert report.m1_6_improvement.status == "passed"
    assert report.metrics.full_partition_agreement.fraction.model_dump() == {
        "numerator": 2,
        "denominator": 20,
        "value": 0.1,
    }
    assert report.metrics.common_pair_partition_agreement.fraction.model_dump() == {
        "numerator": 5,
        "denominator": 18,
        "value": 5 / 18,
        "ineligible": 2,
    }
    assert report.primary_site_audit.resolved_site_count == 8039
    assert report.primary_site_audit.review_required_site_count == 5918
    assert "claims" not in report.model_dump()
    assert [item.requirement for item in report.human_requirements] == [
        "group-review",
        "r103-review",
        "final-full-corpus-scientific-acceptance-and-publication",
    ]


@pytest.mark.unit
@pytest.mark.unit
@pytest.mark.unit
@pytest.mark.unit
@pytest.mark.unit
def test_readiness_metrics_refuse_a_metric_with_another_views_denominator() -> None:
    report = build_machine_readiness(
        MachineReadinessInputs.model_validate(_machine_readiness_input_payload())
    )
    metrics = report.metrics.model_dump(mode="python")
    metrics["exact_pair_precision"]["denominator_rule"] = (
        MetricDenominatorRule.NCIT_BOUND_ORACLE_EXPECTATIONS
    )

    with pytest.raises(ValueError, match="canonical contract"):
        ReadinessMetrics.model_validate(metrics)


@pytest.mark.unit
def test_atomic_audit_write_preserves_primary_failure_and_reports_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "corpus.ttl"
    artifact.write_text(_site_line("C1", "C10"))
    module = __import__(
        "scripts.research.pre_sme_readiness", fromlist=["audit_primary_site_artifact"]
    )

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("primary replace failure")

    original_unlink = module.Path.unlink

    def fail_staging_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.startswith(".audit.json."):
            raise OSError("cleanup unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(module.os, "replace", fail_replace)
    monkeypatch.setattr(module.Path, "unlink", fail_staging_unlink)

    with pytest.raises(OSError, match="primary replace failure") as raised:
        audit_primary_site_artifact(
            artifact=artifact,
            baseline=_baseline(artifact),
            source_identity="a" * 64,
            source_release="26.07d",
            output=tmp_path / "audit.json",
        )

    assert raised.value.__notes__ == ["cleanup failure: cleanup unlink failure"]
    assert not (tmp_path / "audit.json").exists()


@pytest.mark.unit
def test_readiness_refuses_missing_machine_evidence_without_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "readiness.json"

    with pytest.raises(PreSmeValidationError, match="source manifest"):
        generate_pre_sme_readiness(
            source_manifest=tmp_path / "absent-source.json",
            current_evidence=tmp_path / "absent-evidence.json",
            current_comparison=tmp_path / "absent-comparison.json",
            corpus_baseline=tmp_path / "absent-baseline.json",
            corpus_artifact=tmp_path / "absent.ttl",
            proposal_registry=tmp_path / "absent-proposals.json",
            proposal_registry_migration=tmp_path / "absent-migration.json",
            row_decisions=tmp_path / "absent-row-decisions.json",
            primary_site_audit=tmp_path / "absent-audit.json",
            group_packet=tmp_path / "absent-group.json",
            grouping_detector=tmp_path / "absent-grouping-detector.json",
            r103_review_state=tmp_path / "absent-r103-state.json",
            r103_source_inventory=tmp_path / "absent-r103-inventory.json",
            r103_candidates=tmp_path / "absent-r103-candidates.json",
            r103_authority=tmp_path / "absent-r103-authority.json",
            r103_corroboration=tmp_path / "absent-r103-corroboration.json",
            r103_applied_policy=tmp_path / "absent-r103-application.json",
            r103_specificity_target=tmp_path / "absent-r103-target.json",
            r103_specificity_review=tmp_path / "absent-r103-pending.json",
            verify_evidence=tmp_path / "absent-verify.json",
            expected_git_head="a" * 40,
            output=output,
        )

    assert not output.exists()


@pytest.mark.unit
def test_composed_readiness_rejects_false_clear_detector_for_stale_grouping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, module, _report, comparison, group = _composed_readiness_inputs(
        tmp_path, monkeypatch, stale_grouping=True
    )

    evidence = CurrentEngineEvidence.model_validate_json(
        Path(arguments["current_evidence"]).read_bytes()
    )
    violations = module._issue_274_semantic_violations(
        evidence,
        comparison,
        module.load_packaged_normalized_group_policy(),
        group.packet_identity,
    )
    assert violations[0] == ()
    assert violations[1] == ("C100051:normalized-group-mismatch",)
    assert violations[2] == (
        "policy-evidence-binding",
        "policy-comparison-binding",
    )
    with pytest.raises(PreSmeValidationError, match="detector violations differ"):
        generate_pre_sme_readiness(**arguments)
    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
def test_issue_274_detector_reject_branches_are_live_on_current_artifacts() -> None:
    module = __import__(
        "scripts.research.pre_sme_readiness",
        fromlist=["_issue_274_semantic_violations"],
    )
    evidence = CurrentEngineEvidence.model_validate_json(
        (
            Path(__file__).parent / "golden/neoplasm-current-engine-evidence.json"
        ).read_bytes()
    )
    comparison = CurrentComparison.model_validate_json(
        (Path(__file__).parent / "golden/neoplasm-current-comparison.json").read_bytes()
    )
    policy = module.load_packaged_normalized_group_policy()
    concept = evidence.concepts[0]
    constituent = concept.constituents[0]
    mutated_constituent = constituent.model_copy(
        update={"axis": "op:UncontractedAxis", "normalized_group_id": "0" * 64}
    )
    mutated_concept = concept.model_copy(
        update={"constituents": (mutated_constituent, *concept.constituents[1:])}
    )
    mutated = evidence.model_copy(
        update={"concepts": (mutated_concept, *evidence.concepts[1:])}
    )

    axis, groups, golden = module._issue_274_semantic_violations(
        mutated, comparison, policy, "0" * 64
    )

    assert axis == (f"{concept.code}:op:UncontractedAxis",)
    assert groups
    assert golden == ("policy-group-review-binding",)


@pytest.mark.unit
def test_composed_readiness_rejects_changed_historical_row_decisions_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    row_payload = json.loads(Path(arguments["row_decisions"]).read_text())
    row_payload["rows"][0]["sme_action"] = (
        "revise" if row_payload["rows"][0]["sme_action"] == "include" else "include"
    )
    row_payload["payload_identity"] = _identity(
        {key: value for key, value in row_payload.items() if key != "payload_identity"}
    )
    changed = tmp_path / "changed-row-decisions.json"
    changed.write_text(json.dumps(row_payload), encoding="utf-8")
    arguments["row_decisions"] = changed

    with pytest.raises(PreSmeValidationError, match="row decision"):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
def test_composed_readiness_binds_r103_machine_artifacts_and_requires_one_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )

    readiness = generate_pre_sme_readiness(**arguments)
    payload = readiness.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True)
    r103_requirement = next(
        item
        for item in readiness.human_requirements
        if item.requirement == "r103-review"
    )

    assert readiness.identities.r103_packet_identity == (
        "17c2349cdc0442d9f68d5ad1e7e22a51b85682408662f8ec46ae04bc7b90a449"
    )
    assert readiness.identities.proposal_registry_migration_identity == (
        "ee414d3632cbf4fbf7b1471be3a13573b2d62717b96eaa7128b71d446d2d8705"
    )
    assert r103_requirement.status == "satisfied-by-specificity-review"
    assert r103_requirement.count == 1
    assert r103_requirement.selected_option == "qualify-global-most-specific-claim"
    assert readiness.identities.r103_source_inventory_identity != "0" * 64
    assert readiness.identities.r103_candidate_artifact_identity != "0" * 64
    assert readiness.identities.r103_authority_artifact_identity != "0" * 64
    assert readiness.identities.r103_corroboration_artifact_identity != "0" * 64
    assert readiness.identities.r103_applied_policy_identity != "0" * 64
    assert readiness.authorization is False
    assert readiness.publication.publication_writes_performed is False
    assert "registry" not in payload
    assert "dry_run" not in payload
    assert "concept-scoped-accuracy-exclusion" not in encoded
    assert "ready-for-separate-application" not in encoded
    assert "R. Hannes Niedner" not in encoded


@pytest.mark.unit
def test_r103_pending_status_is_not_inferred_from_candidate_artifact_presence() -> None:
    assert "r103_human_selection_required" not in MachineReadinessInputs.model_fields
    assert "r103_specificity_review_identity" in MachineReadinessInputs.model_fields
    parameters = inspect.signature(generate_pre_sme_readiness).parameters
    assert "r103_specificity_review" in parameters
    assert "r103_pending_specificity_review" not in parameters


@pytest.mark.unit
def test_selected_r103_review_resolves_pending_without_changing_evidence() -> None:
    payload = _machine_readiness_input_payload()
    evidence_identities = {
        "r103_source_inventory_identity": "1" * 64,
        "r103_candidate_artifact_identity": "2" * 64,
        "r103_authority_artifact_identity": "3" * 64,
        "r103_corroboration_artifact_identity": "4" * 64,
        "r103_applied_policy_identity": "5" * 64,
        "r103_specificity_target_identity": "6" * 64,
    }
    selected_payload = {
        "schema_version": 1,
        "status": "selected-human-specificity-review",
        "question_kind": "most-specific-named-stated-descendant",
        "subject_code": "C2860",
        "role_code": "R103",
        "filler_code": "C12950",
        "question": (
            "For C2860/R103/C12950, does one of the 16 enumerated named stated "
            "descendants of C12950 in NCIt 26.07d provide a better "
            "normal-tissue-origin filler than C12950?"
        ),
        "selected_option": "qualify-global-most-specific-claim",
        "selected_candidate_code": None,
        "enumerated_candidate_count": 16,
        "bounded_conclusion": (
            "None of the 16 enumerated named stated descendants of C12950 in NCIt "
            "26.07d is a better normal-tissue-origin filler for C2860/R103 than "
            "C12950."
        ),
        "effective_outcome": "source-supported",
        "effective_rationale": (
            "Retain C12950 as the source-supported C2860/R103 filler. None of the "
            "16 enumerated named stated descendants of C12950 in NCIt 26.07d is a "
            "better normal-tissue-origin filler for C2860/R103 than C12950. This "
            "bounded comparison does not establish that C12950 is the globally "
            "most-specific available NCIt filler."
        ),
        "global_claim_disposition": "withdrawn-bounded-comparison-not-global-proof",
        "target_artifact_identity": evidence_identities[
            "r103_specificity_target_identity"
        ],
        "candidate_artifact_identity": evidence_identities[
            "r103_candidate_artifact_identity"
        ],
        "applied_policy_identity": evidence_identities["r103_applied_policy_identity"],
        "prior_decision_identity": "9" * 64,
        "transcription": {
            "actor": "software-transcriber",
            "authority": "user-confirmed-in-current-conversation",
            "authorship_claimed": False,
            "confirmation_date": "2026-09-07",
        },
        "proposal_created": False,
        "nci_adoption_inferred": False,
        "software_selected_answer": False,
    }
    selected = R103SelectedSpecificityReview.model_validate(
        {**selected_payload, "artifact_identity": _identity(selected_payload)}
    )
    payload.update(
        **evidence_identities,
        r103_registry_identity="7" * 64,
        r103_c3264_terminal_decision_identity="8" * 64,
        r103_specificity_review_identity=selected.artifact_identity,
        r103_specificity_review=selected,
    )

    readiness = build_machine_readiness(MachineReadinessInputs.model_validate(payload))
    requirement = next(
        item
        for item in readiness.human_requirements
        if item.requirement == "r103-review"
    )

    assert requirement.status == "satisfied-by-specificity-review"
    assert requirement.count == 1
    assert requirement.selected_option == "qualify-global-most-specific-claim"
    assert requirement.effective_outcome == "source-supported"
    assert (
        requirement.global_claim_disposition
        == "withdrawn-bounded-comparison-not-global-proof"
    )
    assert requirement.confirmation_date == "2026-09-07"
    assert requirement.proposal_created is False
    assert requirement.nci_adoption_inferred is False
    assert (
        readiness.identities.r103_specificity_review_status
        == "selected-human-specificity-review"
    )
    assert readiness.identities.r103_candidate_artifact_identity == "2" * 64
    assert readiness.identities.r103_source_inventory_identity == "1" * 64


@pytest.mark.unit
def test_selected_r103_review_rejects_another_applied_policy() -> None:
    payload = _machine_readiness_input_payload()
    selected_payload = {
        "schema_version": 1,
        "status": "selected-human-specificity-review",
        "question_kind": "most-specific-named-stated-descendant",
        "subject_code": "C2860",
        "role_code": "R103",
        "filler_code": "C12950",
        "question": (
            "For C2860/R103/C12950, does one of the 16 enumerated named stated "
            "descendants of C12950 in NCIt 26.07d provide a better "
            "normal-tissue-origin filler than C12950?"
        ),
        "selected_option": "qualify-global-most-specific-claim",
        "selected_candidate_code": None,
        "enumerated_candidate_count": 16,
        "bounded_conclusion": (
            "None of the 16 enumerated named stated descendants of C12950 in NCIt "
            "26.07d is a better normal-tissue-origin filler for C2860/R103 than "
            "C12950."
        ),
        "effective_outcome": "source-supported",
        "effective_rationale": (
            "Retain C12950 as the source-supported C2860/R103 filler. None of the "
            "16 enumerated named stated descendants of C12950 in NCIt 26.07d is a "
            "better normal-tissue-origin filler for C2860/R103 than C12950. This "
            "bounded comparison does not establish that C12950 is the globally "
            "most-specific available NCIt filler."
        ),
        "global_claim_disposition": "withdrawn-bounded-comparison-not-global-proof",
        "target_artifact_identity": "6" * 64,
        "candidate_artifact_identity": "2" * 64,
        "applied_policy_identity": "0" * 64,
        "prior_decision_identity": "9" * 64,
        "transcription": {
            "actor": "software-transcriber",
            "authority": "user-confirmed-in-current-conversation",
            "authorship_claimed": False,
            "confirmation_date": "2026-09-07",
        },
        "proposal_created": False,
        "nci_adoption_inferred": False,
        "software_selected_answer": False,
    }
    selected = R103SelectedSpecificityReview.model_validate(
        {**selected_payload, "artifact_identity": _identity(selected_payload)}
    )
    payload.update(
        r103_source_inventory_identity="1" * 64,
        r103_candidate_artifact_identity="2" * 64,
        r103_authority_artifact_identity="3" * 64,
        r103_corroboration_artifact_identity="4" * 64,
        r103_applied_policy_identity="5" * 64,
        r103_specificity_target_identity="6" * 64,
        r103_registry_identity="7" * 64,
        r103_c3264_terminal_decision_identity="8" * 64,
        r103_specificity_review_identity=selected.artifact_identity,
        r103_specificity_review=selected,
    )

    with pytest.raises(ValueError, match="specificity-review evidence identities"):
        MachineReadinessInputs.model_validate(payload)


@pytest.mark.unit
def test_c3264_terminal_exclusion_survives_candidate_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )

    readiness = generate_pre_sme_readiness(**arguments)

    assert readiness.identities.r103_c3264_terminal_decision_identity != "0" * 64
    r103_requirement = next(
        item
        for item in readiness.human_requirements
        if item.requirement == "r103-review"
    )
    assert r103_requirement.status == "satisfied-by-specificity-review"


@pytest.mark.unit
def test_current_readiness_rejects_the_superseded_pending_specificity_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    arguments["r103_specificity_review"] = (
        Path(__file__).parent / "golden/r103-c2860-specificity-pending-26.07d.json"
    )

    with pytest.raises(PreSmeValidationError):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
def test_readiness_r103_satisfaction_reject_branch_is_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    source = Path(arguments["r103_review_state"])
    state = json.loads(source.read_text(encoding="utf-8"))
    state["dry_run"]["unresolved"] = 1
    changed = tmp_path / "changed-r103-state.json"
    changed.write_text(json.dumps(state), encoding="utf-8")
    arguments["r103_review_state"] = changed

    with pytest.raises(PreSmeValidationError):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    ).hexdigest()


@pytest.mark.unit
def test_readiness_rejects_self_consistent_r103_application_for_another_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    application = json.loads(
        Path(arguments["r103_applied_policy"]).read_text(encoding="utf-8")
    )
    application["proposal_registry_identity"] = "0" * 64
    application["artifact_identity"] = _identity(
        {key: value for key, value in application.items() if key != "artifact_identity"}
    )
    changed = tmp_path / "changed-r103-application.json"
    changed.write_text(json.dumps(application), encoding="utf-8")
    arguments["r103_applied_policy"] = changed
    selected = json.loads(
        Path(arguments["r103_specificity_review"]).read_text(encoding="utf-8")
    )
    selected["applied_policy_identity"] = application["artifact_identity"]
    selected["artifact_identity"] = _identity(
        {key: value for key, value in selected.items() if key != "artifact_identity"}
    )
    changed_selected = tmp_path / "changed-r103-selected.json"
    changed_selected.write_text(json.dumps(selected), encoding="utf-8")
    arguments["r103_specificity_review"] = changed_selected

    with pytest.raises(PreSmeValidationError, match="R103 applied proposal"):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
def test_readiness_rejects_self_consistent_authority_with_another_rev1_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    authority = json.loads(
        Path(arguments["r103_authority"]).read_text(encoding="utf-8")
    )
    authority["historical_rev1_file_sha256"] = "0" * 64
    authority["artifact_identity"] = _identity(
        {key: value for key, value in authority.items() if key != "artifact_identity"}
    )
    changed_authority = tmp_path / "changed-r103-authority.json"
    changed_authority.write_text(json.dumps(authority), encoding="utf-8")
    arguments["r103_authority"] = changed_authority

    for argument in ("r103_corroboration", "r103_applied_policy"):
        payload = json.loads(Path(arguments[argument]).read_text(encoding="utf-8"))
        payload["authority_artifact_identity"] = authority["artifact_identity"]
        payload["artifact_identity"] = _identity(
            {key: value for key, value in payload.items() if key != "artifact_identity"}
        )
        changed = tmp_path / f"changed-{argument}.json"
        changed.write_text(json.dumps(payload), encoding="utf-8")
        arguments[argument] = changed
    specificity = __import__(
        "ontolib.decomposition.r103_specificity_review", fromlist=["unused"]
    )
    golden = Path(__file__).parent / "golden"
    inventory = specificity.load_source_inventory(
        golden / "r103-source-inventory-26.07d.json"
    )
    candidates = specificity.load_candidate_artifact(
        golden / "r103-c12950-candidates-26.07d.json"
    )
    changed_authority_model = specificity.load_authority_artifact(changed_authority)
    target = specificity.build_specificity_review_target(
        inventory=inventory,
        candidates=candidates,
        authority=changed_authority_model,
    )
    pending = specificity.build_pending_specificity_review(
        target=target,
        inventory=inventory,
        candidates=candidates,
        authority=changed_authority_model,
        revision_path=golden / "r103-review-state-26.07d-rev2.json",
    )
    changed_target = tmp_path / "changed-target.json"
    changed_selected = tmp_path / "changed-selected.json"
    specificity.write_artifact(changed_target, target)
    selected = specificity.build_selected_specificity_review(
        pending=pending,
        target=target,
        candidates=candidates,
        application=specificity.load_applied_policy_report(
            arguments["r103_applied_policy"]
        ),
    )
    specificity.write_artifact(changed_selected, selected)
    arguments["r103_specificity_target"] = changed_target
    arguments["r103_specificity_review"] = changed_selected

    with pytest.raises(PreSmeValidationError, match="R103 authority history"):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
def test_readiness_rejects_self_consistent_corroboration_from_another_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    corroboration = json.loads(
        Path(arguments["r103_corroboration"]).read_text(encoding="utf-8")
    )
    corroboration["historical_artifact_sha256"] = "0" * 64
    corroboration["historical_corroboration_identity"] = "1" * 64
    corroboration["artifact_identity"] = _identity(
        {
            key: value
            for key, value in corroboration.items()
            if key != "artifact_identity"
        }
    )
    changed = tmp_path / "changed-r103-corroboration.json"
    changed.write_text(json.dumps(corroboration), encoding="utf-8")
    arguments["r103_corroboration"] = changed

    with pytest.raises(PreSmeValidationError, match="R103 corroboration history"):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
def test_readiness_strict_state_load_rejects_changed_registry_outcome_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    source = Path(arguments["r103_review_state"])
    state = json.loads(source.read_text(encoding="utf-8"))
    registry = state["registry"]
    registry["decisions"][0]["outcome"] = "review-required"
    human_rows = [
        [
            decision["outcome"],
            decision["rationale"],
            decision["reviewer"],
            decision["review_date"],
        ]
        for decision in registry["decisions"]
    ]
    workbook_identity = _identity(
        {
            "packet_identity": state["predecessor"]["packet"]["packet_identity"],
            "human_rows": human_rows,
        }
    )
    registry["workbook_identity"] = workbook_identity
    for decision in registry["decisions"]:
        decision["workbook_identity"] = workbook_identity
        decision["decision_identity"] = _identity(
            {
                key: value
                for key, value in decision.items()
                if key != "decision_identity"
            }
        )
    registry["registry_identity"] = _identity(
        {key: value for key, value in registry.items() if key != "registry_identity"}
    )
    state["artifact_identity"] = _identity(
        {key: value for key, value in state.items() if key != "artifact_identity"}
    )
    changed = tmp_path / "changed-r103-state.json"
    changed.write_text(json.dumps(state), encoding="utf-8")
    arguments["r103_review_state"] = changed

    with pytest.raises(PreSmeValidationError, match="revision decision vector differs"):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["malformed", "bare-packet"])
def test_readiness_refuses_non_promoted_state_shapes_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    arguments, _module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    state_path = tmp_path / "invalid-r103-state.json"
    if failure == "malformed":
        state_path.write_text("{not JSON", encoding="utf-8")
        expected = "invalid JSON evidence"
    else:
        source = Path(arguments["r103_review_state"])
        state_path.write_text(
            json.dumps(
                json.loads(source.read_text(encoding="utf-8"))["predecessor"]["packet"]
            ),
            encoding="utf-8",
        )
        expected = "R103PromotedReviewRevision"
    arguments["r103_review_state"] = state_path

    with pytest.raises(PreSmeValidationError, match=expected):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("source_identity", "R103 source"),
        ("candidate_manifest_identity", "R103 manifest"),
        ("proposal_registry_identity", "R103 proposal"),
    ],
)
def test_readiness_independently_rejects_mutated_embedded_packet_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    message: str,
) -> None:
    arguments, module, _report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    state = load_r103_promoted_review_revision(Path(arguments["r103_review_state"]))
    packet = state.packet.model_copy(
        update={"candidate_manifest_identity": _identity({}), field: "f" * 64}
    )
    monkeypatch.setattr(
        module,
        "load_r103_promoted_review_revision",
        lambda _path: SimpleNamespace(
            packet=packet,
            registry=state.registry,
            predecessor=state.predecessor,
            artifact_identity=state.artifact_identity,
        ),
    )

    with pytest.raises(PreSmeValidationError, match=message):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["stale-verify", "identity", "metrics"])
def test_composed_readiness_reject_branches_are_live_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    arguments, module, _report, comparison, group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )
    if failure == "stale-verify":
        write_verify_evidence(
            Path(arguments["verify_evidence"]),
            git_head="b" * 40,
            docker_context="ontoprism-podman",
            docker_endpoint="unix:///tmp/podman.sock",
            gate_executable="/opt/homebrew/bin/pdm",
            gate_version="PDM, version test",
            observed_exit_code=0,
        )
    elif failure == "identity":
        monkeypatch.setattr(
            module,
            "load_group_review_packet",
            lambda _path: SimpleNamespace(
                **{
                    **group.__dict__,
                    "current_evidence_identity": "f" * 64,
                }
            ),
        )
    else:
        recall = comparison.metrics.exact_pair_recall
        bad_recall = CurrentRateMetric(
            numerator=recall.numerator - 1,
            denominator=recall.denominator,
            rate=(recall.numerator - 1) / recall.denominator,
        )
        bad_metrics = comparison.metrics.model_copy(
            update={"exact_pair_recall": bad_recall}
        )
        bad_comparison = comparison.model_copy(update={"metrics": bad_metrics})
        monkeypatch.setattr(
            module.CurrentComparison,
            "model_validate_json",
            classmethod(lambda _cls, _raw: bad_comparison),
        )
        monkeypatch.setattr(
            module, "validate_current_comparison", lambda _evidence, _comparison: None
        )

    expected_error = (
        "exact pair true-positive counts differ" if failure == "metrics" else None
    )
    with pytest.raises(PreSmeValidationError, match=expected_error):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


@pytest.mark.unit
@pytest.mark.unit
def test_primary_site_generation_translates_invalid_manifest_without_output(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "corpus.ttl"
    artifact.write_text(_site_line("C1", "C10"))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(_baseline(artifact).model_dump_json())
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    output = tmp_path / "audit.json"

    with pytest.raises(PreSmeValidationError):
        generate_primary_site_audit(
            source_manifest=manifest,
            baseline=baseline,
            artifact=artifact,
            output=output,
        )

    assert not output.exists()


@pytest.mark.unit
@pytest.mark.unit
def test_readiness_refuses_verify_evidence_from_another_head() -> None:
    with pytest.raises(PreSmeValidationError, match="HEAD"):
        require_current_verify_evidence("a" * 40, "b" * 40)
