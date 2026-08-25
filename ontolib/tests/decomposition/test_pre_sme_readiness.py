from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
from scripts.research.pre_sme_readiness import (
    MachineReadinessInputs,
    PreSmeValidationError,
    audit_primary_site_artifact,
    build_machine_readiness,
    build_r101_reuse_validation,
    generate_pre_sme_readiness,
    require_current_verify_evidence,
)

from ontolib.decomposition.corpus_baseline import (
    CorpusBaseline,
    corpus_baseline_identity,
)

if TYPE_CHECKING:
    from pathlib import Path


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


def _baseline(artifact: Path) -> CorpusBaseline:
    artifact_identity = hashlib.sha256(artifact.read_bytes()).hexdigest()
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": "full-run",
        "source_identity": "a" * 64,
        "ontology_release": "26.07d",
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
    assert audit.resolved_violation_count == 0
    assert audit.scan_passes == 1


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
            r101_validation_identity="3" * 64,
            proposal_registry_identity="4" * 64,
            primary_site_audit_identity="5" * 64,
            group_packet_identity="6" * 64,
            r103_packet_identity="7" * 64,
            verify_evidence_identity="8" * 64,
            git_head="9" * 40,
            exact_pair_true_positive=100,
            exact_pair_emitted=108,
            exact_pair_expected=153,
            full_partition_agreement=(2, 20),
            common_partition_agreement=(5, 18),
            group_review_count=18,
            r103_review_count=3,
            r101_exact_validation_established=True,
        )
    )

    assert report.status == "awaiting-human-review"
    assert report.authorization is False
    assert report.publication.status == "not-attempted"
    assert report.publication.writes_performed is False
    assert report.metrics.exceeds_historical_thresholds is True
    assert report.grouping.full_view != report.grouping.common_pair_view
    assert report.claims.no_unadjudicated_delta is None
    assert [item.requirement for item in report.human_requirements] == [
        "group-review",
        "r103-review",
        "final-full-corpus-scientific-acceptance-and-publication",
    ]


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
    assert result.writes_performed is False
    assert result.reason == "packet-bindings-differ"


@pytest.mark.unit
def test_readiness_refuses_verify_evidence_from_another_head() -> None:
    with pytest.raises(PreSmeValidationError, match="HEAD"):
        require_current_verify_evidence("a" * 40, "b" * 40)
