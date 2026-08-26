from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.research.current_evidence import CurrentComparison, CurrentEngineEvidence
from scripts.research.pre_sme_readiness import (
    MachineReadinessInputs,
    PreSmeValidationError,
    PrimarySiteAudit,
    PrimarySiteObservation,
    ReadinessMetrics,
    audit_primary_site_artifact,
    build_machine_readiness,
    build_r101_reuse_validation,
    generate_pre_sme_readiness,
    generate_primary_site_audit,
    r101_human_occurrence_count,
    require_current_verify_evidence,
    write_verify_evidence,
)

from ontolib.decomposition.corpus_baseline import (
    CorpusBaseline,
    corpus_baseline_identity,
)
from ontolib.decomposition.r101_conservation import load_r101_conservation_report

_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
_OP = "https://w3id.org/ontoprism/vocab#"
_R101_REPORT = Path(__file__).parent / "golden/neoplasm-r101-v4-conservation.json.gz"


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


def _composed_readiness_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], Any, Any, Any, Any]:
    module = __import__(
        "scripts.research.pre_sme_readiness", fromlist=["generate_pre_sme_readiness"]
    )
    golden = Path(__file__).parent / "golden"
    evidence_path = golden / "neoplasm-current-engine-evidence.json"
    comparison_path = golden / "neoplasm-current-comparison.json"
    evidence = CurrentEngineEvidence.model_validate_json(evidence_path.read_bytes())
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    report = load_r101_conservation_report(_R101_REPORT)
    corpus_artifact = tmp_path / "corpus.ttl"
    corpus_artifact.write_text(_site_line("C1", "C10"))
    baseline = _baseline(
        corpus_artifact,
        source_identity=report.source_identity,
        ontology_release=report.source_release_id,
    )
    audit = audit_primary_site_artifact(
        artifact=corpus_artifact,
        baseline=baseline,
        source_identity=report.source_identity,
        source_release=report.source_release_id,
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    manifest_identity = hashlib.sha256(b"{}").hexdigest()
    validation = build_r101_reuse_validation(
        report_identity=report.report_identity,
        existing_packet_identity="1" * 64,
        current_packet_identity="2" * 64,
        registry_identity="3" * 64,
    )
    validation_path = tmp_path / "r101-validation.json"
    validation_path.write_text(validation.model_dump_json())
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
        r101_report_identity=report.report_identity,
        packet_identity="4" * 64,
        review_rows=(None,) * 18,
    )
    r103 = SimpleNamespace(
        source_identity=report.source_identity,
        candidate_manifest_identity=manifest_identity,
        proposal_registry_identity=evidence.proposal_registry_identity,
        packet_identity="5" * 64,
        rows=(None,) * 3,
    )
    monkeypatch.setattr(
        module,
        "validate_ncit_sibling_manifest",
        lambda _path: SimpleNamespace(
            source_identity=report.source_identity,
            ontology_version=report.source_release_id,
        ),
    )
    monkeypatch.setattr(module, "load_corpus_baseline", lambda _path: baseline)
    monkeypatch.setattr(module, "load_r101_conservation_report", lambda _path: report)
    monkeypatch.setattr(
        module,
        "load_proposal_registry",
        lambda _path: SimpleNamespace(
            registry_identity=evidence.proposal_registry_identity
        ),
    )
    monkeypatch.setattr(module, "load_group_review_packet", lambda _path: group)
    monkeypatch.setattr(module, "load_r103_review_packet", lambda _path: r103)
    unused = tmp_path / "unused.json"
    unused.write_text("{}")
    arguments: dict[str, Any] = {
        "source_manifest": manifest,
        "current_evidence": evidence_path,
        "current_comparison": comparison_path,
        "corpus_baseline": unused,
        "corpus_artifact": corpus_artifact,
        "r101_report": unused,
        "r101_validation": validation_path,
        "proposal_registry": unused,
        "primary_site_audit": audit_path,
        "group_packet": unused,
        "r103_packet": unused,
        "verify_evidence": verify_path,
        "expected_git_head": "a" * 40,
        "output": tmp_path / "readiness.json",
    }
    return arguments, module, report, comparison, group


@pytest.mark.unit
def test_primary_site_liveness_rejects_two_resolved_and_minus_one_passes(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "corpus.ttl"
    artifact.write_text(
        _site_line("C1", "C10")
        + _site_line("C1", "C11")
        + _site_line("C2", "C12", review=True)
    )

    with pytest.raises(PreSmeValidationError, match="at most one resolved"):
        audit_primary_site_artifact(
            artifact=artifact,
            baseline=_baseline(artifact),
            source_identity="a" * 64,
            source_release="26.07d",
        )

    artifact.write_text(_site_line("C1", "C10") + _site_line("C2", "C12", review=True))
    audit = audit_primary_site_artifact(
        artifact=artifact,
        baseline=_baseline(artifact),
        source_identity="a" * 64,
        source_release="26.07d",
    )

    assert audit.resolved_site_count == 1
    assert audit.review_required_site_count == 1
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
            "resolved concepts",
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
        "schema_version": 1,
        "source_identity": "a" * 64,
        "source_release": "26.07d",
        "corpus_baseline_identity": "b" * 64,
        "corpus_artifact_identity": "c" * 64,
        "resolved_sites": resolved,
        "review_required_sites": review,
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
        "schema_version": 1,
        "source_identity": "a" * 64,
        "source_release": "26.07d",
        "corpus_baseline_identity": "b" * 64,
        "corpus_artifact_identity": "c" * 64,
        "resolved_sites": (),
        "review_required_sites": (),
        "resolved_site_count": 0,
        "review_required_site_count": 0,
        "parser_passes": 1,
        "audit_identity": "d" * 64,
    }

    with pytest.raises(ValueError, match="at least one observation"):
        PrimarySiteAudit.model_validate(payload)


@pytest.mark.unit
def test_machine_readiness_keeps_human_decisions_pending_without_claiming_delta() -> (
    None
):
    report = build_machine_readiness(
        MachineReadinessInputs(
            source_identity="a" * 64,
            source_manifest_identity="b" * 64,
            current_evidence_identity="c" * 64,
            current_comparison_identity="d" * 64,
            sample_artifact_identity="e" * 64,
            corpus_baseline_identity="f" * 64,
            corpus_artifact_identity="1" * 64,
            r101_report_identity="2" * 64,
            r101_registry_identity=(
                "358b42f8279c067fbd0543572073cd5f6887eea0dc74d148483328c02ceb6975"
            ),
            r101_existing_packet_identity="a" * 64,
            r101_current_packet_identity="b" * 64,
            r101_validation_identity="3" * 64,
            proposal_registry_identity="4" * 64,
            primary_site_audit_identity="5" * 64,
            primary_site_resolved_count=8039,
            primary_site_review_required_count=5918,
            group_packet_identity="6" * 64,
            r103_packet_identity="7" * 64,
            verify_evidence_identity="8" * 64,
            git_head="9" * 40,
            exact_pair_true_positive=100,
            exact_pair_emitted=108,
            exact_pair_expected=153,
            full_partition_agreement=(2, 20),
            common_partition_agreement=(5, 18),
            common_partition_ineligible=2,
            group_review_count=18,
            r103_review_count=3,
            r101_exact_validation_established=False,
            r101_occurrence_count=3291,
            r101_mechanical_unresolved=0,
            r101_non_r101_delta=0,
        )
    )

    assert report.status == "awaiting-human-review"
    assert report.authorization is False
    assert report.publication.status == "not-attempted"
    assert report.publication.publication_writes_performed is False
    assert report.metrics.exceeds_historical_thresholds is True
    assert report.grouping.full_view != report.grouping.common_pair_view
    assert report.grouping.full_view.model_dump() == {
        "numerator": 2,
        "denominator": 20,
        "value": 0.1,
    }
    assert report.grouping.common_pair_view.model_dump() == {
        "numerator": 5,
        "denominator": 18,
        "value": 5 / 18,
        "ineligible": 2,
    }
    assert report.primary_site_audit.resolved_site_count == 8039
    assert report.primary_site_audit.review_required_site_count == 5918
    assert report.r101_mechanical_unresolved == 0
    assert report.r101_non_r101_delta == 0
    assert report.claims.no_unadjudicated_delta is None
    assert [item.requirement for item in report.human_requirements] == [
        "group-review",
        "r103-review",
        "r101-ledger-authorization",
        "final-full-corpus-scientific-acceptance-and-publication",
    ]
    assert report.human_requirements[2].status == "pending"
    assert report.human_requirements[2].count == 3291


@pytest.mark.unit
def test_exact_r101_reuse_remains_explicit_and_carries_human_evidence_identity() -> (
    None
):
    inputs = MachineReadinessInputs(
        source_identity="a" * 64,
        source_manifest_identity="b" * 64,
        current_evidence_identity="c" * 64,
        current_comparison_identity="d" * 64,
        sample_artifact_identity="e" * 64,
        corpus_baseline_identity="f" * 64,
        corpus_artifact_identity="1" * 64,
        r101_report_identity="2" * 64,
        r101_registry_identity="3" * 64,
        r101_existing_packet_identity="4" * 64,
        r101_current_packet_identity="4" * 64,
        r101_validation_identity="5" * 64,
        proposal_registry_identity="6" * 64,
        primary_site_audit_identity="7" * 64,
        primary_site_resolved_count=8039,
        primary_site_review_required_count=5918,
        group_packet_identity="8" * 64,
        r103_packet_identity="9" * 64,
        verify_evidence_identity="0" * 64,
        git_head="a" * 40,
        exact_pair_true_positive=100,
        exact_pair_emitted=108,
        exact_pair_expected=153,
        full_partition_agreement=(2, 20),
        common_partition_agreement=(5, 18),
        common_partition_ineligible=2,
        group_review_count=18,
        r103_review_count=3,
        r101_exact_validation_established=True,
        r101_occurrence_count=3291,
        r101_mechanical_unresolved=0,
        r101_non_r101_delta=0,
    )

    requirement = build_machine_readiness(inputs).human_requirements[2]

    assert requirement.requirement == "r101-ledger-authorization"
    assert requirement.status == "satisfied-by-exact-reuse"
    assert requirement.packet_identity == "4" * 64
    assert requirement.registry_identity == "3" * 64


@pytest.mark.unit
def test_readiness_report_refuses_r101_requirement_inconsistent_with_identities() -> (
    None
):
    inputs = MachineReadinessInputs(
        source_identity="a" * 64,
        source_manifest_identity="b" * 64,
        current_evidence_identity="c" * 64,
        current_comparison_identity="d" * 64,
        sample_artifact_identity="e" * 64,
        corpus_baseline_identity="f" * 64,
        corpus_artifact_identity="1" * 64,
        r101_report_identity="2" * 64,
        r101_registry_identity="3" * 64,
        r101_existing_packet_identity="4" * 64,
        r101_current_packet_identity="4" * 64,
        r101_validation_identity="5" * 64,
        proposal_registry_identity="6" * 64,
        primary_site_audit_identity="7" * 64,
        primary_site_resolved_count=8039,
        primary_site_review_required_count=5918,
        group_packet_identity="8" * 64,
        r103_packet_identity="9" * 64,
        verify_evidence_identity="0" * 64,
        git_head="a" * 40,
        exact_pair_true_positive=100,
        exact_pair_emitted=108,
        exact_pair_expected=153,
        full_partition_agreement=(2, 20),
        common_partition_agreement=(5, 18),
        common_partition_ineligible=2,
        group_review_count=18,
        r103_review_count=3,
        r101_exact_validation_established=True,
        r101_occurrence_count=3291,
        r101_mechanical_unresolved=0,
        r101_non_r101_delta=0,
    )
    report = build_machine_readiness(inputs)
    payload = report.model_dump(mode="json")
    payload["human_requirements"][2] = {
        "requirement": "r101-ledger-authorization",
        "count": 3291,
        "status": "pending",
    }
    payload["human_requirements"] = tuple(payload["human_requirements"])

    with pytest.raises(
        ValueError, match="R101 requirement differs from packet identities"
    ):
        type(report).model_validate(payload)


@pytest.mark.unit
def test_r101_human_requirement_uses_current_covered_occurrence_count() -> None:
    report = load_r101_conservation_report(_R101_REPORT)

    assert r101_human_occurrence_count(report) == 3291
    assert r101_human_occurrence_count(report) != report.counts.total


@pytest.mark.unit
def test_tracked_r101_grouping_schema_contains_only_consumed_totals() -> None:
    report = load_r101_conservation_report(_R101_REPORT)

    assert all(
        set(pattern.model_dump())
        == {"old_filler_code", "retained_filler_code", "occurrence_count"}
        for pattern in report.grouping_presentation
    )
    occurrence_count = sum(
        pattern.occurrence_count for pattern in report.grouping_presentation
    )
    assert occurrence_count == report.counts.covered_by_retained_r82


@pytest.mark.unit
def test_readiness_metrics_refuse_inconsistent_fraction_and_threshold_claims() -> None:
    with pytest.raises(ValueError, match="fraction"):
        ReadinessMetrics.model_validate(
            {
                "exact_pair_precision": {
                    "numerator": 100,
                    "denominator": 108,
                    "value": 0.1,
                },
                "exact_pair_recall": {
                    "numerator": 100,
                    "denominator": 153,
                    "value": 100 / 153,
                },
                "historical_precision": {
                    "numerator": 80,
                    "denominator": 106,
                    "value": 80 / 106,
                },
                "historical_recall": {
                    "numerator": 80,
                    "denominator": 153,
                    "value": 80 / 153,
                },
                "exceeds_historical_thresholds": True,
            }
        )


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
            r101_report=tmp_path / "absent-report.json.gz",
            r101_validation=tmp_path / "absent-r101-validation.json",
            proposal_registry=tmp_path / "absent-proposals.json",
            primary_site_audit=tmp_path / "absent-audit.json",
            group_packet=tmp_path / "absent-group.json",
            r103_packet=tmp_path / "absent-r103.json",
            verify_evidence=tmp_path / "absent-verify.json",
            expected_git_head="a" * 40,
            output=output,
        )

    assert not output.exists()


@pytest.mark.unit
def test_composed_readiness_derives_zero_delta_from_current_r101_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, _module, report, _comparison, _group = _composed_readiness_inputs(
        tmp_path, monkeypatch
    )

    readiness = generate_pre_sme_readiness(**arguments)

    assert readiness.r101_mechanical_unresolved == report.counts.unresolved
    assert readiness.r101_non_r101_delta == report.counts.non_r101_delta
    assert Path(arguments["output"]).is_file()


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["stale-verify", "identity", "counts", "metrics"])
def test_composed_readiness_reject_branches_are_live_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    arguments, module, report, comparison, group = _composed_readiness_inputs(
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
    elif failure == "counts":
        bad_counts = report.counts.model_copy(update={"unresolved": 1})
        bad_report = report.model_copy(update={"counts": bad_counts})
        monkeypatch.setattr(
            module, "load_r101_conservation_report", lambda _path: bad_report
        )
    else:
        bad_precision = comparison.metrics.exact_pair_precision.model_copy(
            update={"numerator": 99}
        )
        bad_metrics = comparison.metrics.model_copy(
            update={"exact_pair_precision": bad_precision}
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

    with pytest.raises(PreSmeValidationError):
        generate_pre_sme_readiness(**arguments)

    assert not Path(arguments["output"]).exists()


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
def test_r101_reuse_validation_reports_re_attestation_without_authorizing() -> None:
    result = build_r101_reuse_validation(
        report_identity="a" * 64,
        existing_packet_identity="b" * 64,
        current_packet_identity="c" * 64,
        registry_identity=(
            "358b42f8279c067fbd0543572073cd5f6887eea0dc74d148483328c02ceb6975"
        ),
    )

    assert result.status == "human-reattestation-required"
    assert result.exact_reuse is False
    assert result.authorization is False
    assert result.publication_writes_performed is False
    assert result.reason == "packet-bindings-differ"


@pytest.mark.unit
def test_readiness_refuses_verify_evidence_from_another_head() -> None:
    with pytest.raises(PreSmeValidationError, match="HEAD"):
        require_current_verify_evidence("a" * 40, "b" * 40)
