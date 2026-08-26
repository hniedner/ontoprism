from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ontolib.decomposition.pre_resume import (
    EXPECTED_CANDIDATE_COUNTS,
    EXPECTED_CANDIDATE_DIGEST,
    EXPECTED_RELEASE,
    EXPECTED_RUN_ID,
    EXPECTED_SOURCE_IDENTITY,
    CandidateEvidence,
    CandidateOccurrence,
    CandidatePopulation,
    CandidateTuple,
    MissingP106Verdict,
    PreResumeProof,
    PreResumeValidationEvidence,
    _parse_candidate_rows,
    _validate_candidate_evidence,
    _validate_proof_request,
    _validation_evidence,
    _work_cohorts,
    canonical_pre_resume_json,
    cohort_identity,
    pre_resume_proof_identity,
)

if TYPE_CHECKING:
    from typing import Any


IDENTITY = "a" * 64
PRE_RESUME_COMPLETED_COHORT = "pre-resume-completed-cohort"


def _proof_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": 1,
        "run_id": "neoplasm-test-run",
        "release": "26.07d",
        "status": "failed",
        "error_type": "BrokenPipeError",
        "error_message": "[Errno 32] Broken pipe",
        "source_identity": "1" * 64,
        "fingerprint_identity": "2" * 64,
        "cohort_identity": cohort_identity(2, "8" * 64, 1, "9" * 64),
        "worklist_identity": "4" * 64,
        "worklist_digest": "4" * 64,
        "candidate_identity": "5" * 64,
        "candidate_tuple_digest": "5" * 64,
        "semantic_identity": "6" * 64,
        "query_identity": "7" * 64,
        "table_identity": "a" * 64,
        "pre_fix_execution_identity": "b" * 64,
        "semantic_dependencies": (
            {
                "path": "ontolib/src/ontolib/decomposition/axes.py",
                "identity": "c" * 64,
            },
        ),
        "completed_cohort_label": "pre-fix-completed",
        "pending_cohort_label": "post-fix-pending",
        "claim": (
            "the patched morphology-organ missing-P106 branch was unreachable for "
            "every pre-fix-completed occurrence"
        ),
        "completed_cohort_digest": "8" * 64,
        "pending_cohort_digest": "9" * 64,
        "candidate_concept_count": 3,
        "candidate_tuple_count": 4,
        "candidate_occurrence_count": 5,
        "residual_filler_denominator_count": 6,
        "route_filter_sensitivity": {
            "candidate_concept_count": 4,
            "candidate_tuple_count": 5,
            "candidate_occurrence_count": 6,
            "residual_filler_count": 7,
            "candidate_tuple_digest": "d" * 64,
        },
        "completed_count": 2,
        "pending_count": 1,
        "worklist_count": 3,
        "completion_metadata_mismatch_count": 0,
        "constituent_count_mismatch_count": 0,
        "minted_count_mismatch_count": 0,
        "child_orphan_count": 0,
        "validation": {
            "affected_concept_count": 0,
            "affected_tuple_count": 0,
            "affected_occurrence_count": 0,
            "affected_residual_filler_count": 0,
            "authorizable": True,
            "reason": None,
        },
        "postgres_reads": 2,
        "qlever_reads": 3,
    }
    data.update(overrides)
    return data


@pytest.mark.unit
def test_pre_resume_completed_cohort_keeps_original_candidate_identity() -> None:
    assert (
        PRE_RESUME_COMPLETED_COHORT,
        EXPECTED_CANDIDATE_COUNTS,
        EXPECTED_CANDIDATE_DIGEST,
    ) == (
        PRE_RESUME_COMPLETED_COHORT,
        (133, 193, 212, 5),
        "8742b60f449a38cc5f640ce1d613fc67d51e4189d30dd792159ba9fb12c144bb",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    [
        "source_identity",
        "fingerprint_identity",
        "cohort_identity",
        "worklist_identity",
        "candidate_identity",
        "semantic_identity",
        "query_identity",
        "completed_cohort_digest",
        "pending_cohort_digest",
        "worklist_digest",
        "candidate_tuple_digest",
        "table_identity",
        "pre_fix_execution_identity",
    ],
)
def test_pre_resume_proof_requires_full_lowercase_sha256_identities(field: str) -> None:
    with pytest.raises(ValidationError):
        PreResumeProof.model_validate(_proof_data(**{field: IDENTITY[:-1]}))
    with pytest.raises(ValidationError):
        PreResumeProof.model_validate(_proof_data(**{field: IDENTITY.upper()}))


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    [
        "candidate_concept_count",
        "candidate_tuple_count",
        "candidate_occurrence_count",
        "residual_filler_denominator_count",
    ],
)
def test_pre_resume_candidate_denominators_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        PreResumeProof.model_validate(_proof_data(**{field: 0}))


@pytest.mark.unit
def test_pre_resume_proof_reconciles_worklist_and_separates_cohorts() -> None:
    with pytest.raises(ValidationError, match=r"completed_count \+ pending_count"):
        PreResumeProof.model_validate(_proof_data(worklist_count=4))
    with pytest.raises(ValidationError, match="cohort digests must be distinct"):
        PreResumeProof.model_validate(_proof_data(pending_cohort_digest="8" * 64))


@pytest.mark.unit
def test_pre_resume_proof_binds_contract_labels_digests_and_integrity() -> None:
    proof = PreResumeProof.model_validate(_proof_data())

    assert proof.completed_cohort_label == "pre-fix-completed"
    assert proof.pending_cohort_label == "post-fix-pending"
    with pytest.raises(ValidationError, match="worklist digest"):
        PreResumeProof.model_validate(_proof_data(worklist_digest="d" * 64))
    with pytest.raises(ValidationError, match="candidate tuple digest"):
        PreResumeProof.model_validate(_proof_data(candidate_tuple_digest="d" * 64))
    with pytest.raises(ValidationError, match="cohort identity"):
        PreResumeProof.model_validate(_proof_data(cohort_identity="d" * 64))
    with pytest.raises(ValidationError, match="integrity"):
        PreResumeProof.model_validate(_proof_data(child_orphan_count=1))


@pytest.mark.unit
def test_route_filter_sensitivity_must_be_a_positive_liveness_control() -> None:
    proof = PreResumeProof.model_validate(_proof_data())

    assert proof.route_filter_sensitivity.candidate_tuple_count == 5
    with pytest.raises(ValidationError, match="route-filter sensitivity"):
        PreResumeProof.model_validate(
            _proof_data(
                route_filter_sensitivity={
                    "candidate_concept_count": 3,
                    "candidate_tuple_count": 4,
                    "candidate_occurrence_count": 5,
                    "residual_filler_count": 6,
                    "candidate_tuple_digest": "5" * 64,
                }
            )
        )


@pytest.mark.unit
def test_nonzero_validation_effects_are_evidence_but_cannot_authorize() -> None:
    evidence = PreResumeValidationEvidence(
        affected_concept_count=1,
        affected_tuple_count=2,
        affected_occurrence_count=3,
        affected_residual_filler_count=4,
        authorizable=False,
        reason="routing changes require adjudication",
    )

    proof = PreResumeProof.model_validate(_proof_data(validation=evidence))

    assert proof.validation.affected_tuple_count == 2
    with pytest.raises(ValidationError, match="nonzero affected counts"):
        PreResumeValidationEvidence(
            affected_concept_count=1,
            affected_tuple_count=0,
            affected_occurrence_count=0,
            affected_residual_filler_count=0,
            authorizable=True,
            reason=None,
        )
    with pytest.raises(ValidationError, match="reason is required"):
        PreResumeValidationEvidence(
            affected_concept_count=1,
            affected_tuple_count=0,
            affected_occurrence_count=0,
            affected_residual_filler_count=0,
            authorizable=False,
            reason=None,
        )


@pytest.mark.unit
def test_proof_validation_counts_are_derived_from_inspected_occurrences() -> None:
    verdict = MissingP106Verdict(
        affected=(
            CandidateOccurrence("C1", "o1", "A", "F1", "M"),
            CandidateOccurrence("C1", "o2", "A", "F1", "M"),
            CandidateOccurrence("C2", "o3", "A", "F2", "M"),
        )
    )

    evidence = _validation_evidence(verdict)

    assert (
        evidence.affected_concept_count,
        evidence.affected_tuple_count,
        evidence.affected_occurrence_count,
        evidence.affected_residual_filler_count,
    ) == (2, 2, 3, 2)
    assert evidence.authorizable is False
    assert evidence.reason == "missing-P106 affected set is nonzero"


@pytest.mark.unit
def test_zero_validation_effects_can_authorize_when_proof_invariants_hold() -> None:
    proof = PreResumeProof.model_validate(_proof_data())

    assert proof.validation.authorizable is True


@pytest.mark.unit
def test_pre_resume_models_are_strict_immutable_and_forbid_unknown_fields() -> None:
    proof = PreResumeProof.model_validate(_proof_data())

    with pytest.raises(ValidationError):
        proof.postgres_reads = 9  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PreResumeProof.model_validate(_proof_data(postgres_reads="2"))
    with pytest.raises(ValidationError):
        PreResumeProof.model_validate({**_proof_data(), "unexpected": True})


@pytest.mark.unit
def test_positive_read_freshness_is_excluded_from_canonical_identity() -> None:
    first = PreResumeProof.model_validate(_proof_data(postgres_reads=1, qlever_reads=2))
    second = PreResumeProof.model_validate(
        _proof_data(postgres_reads=99, qlever_reads=77)
    )

    assert pre_resume_proof_identity(first.model_dump()) == pre_resume_proof_identity(
        second.model_dump()
    )
    for field in ("postgres_reads", "qlever_reads"):
        with pytest.raises(ValidationError):
            PreResumeProof.model_validate(_proof_data(**{field: 0}))


@pytest.mark.unit
def test_canonical_json_and_identity_ignore_mapping_insertion_order() -> None:
    payload = _proof_data()
    reversed_payload = dict(reversed(payload.items()))
    reversed_payload["validation"] = dict(reversed(payload["validation"].items()))

    assert canonical_pre_resume_json(payload) == canonical_pre_resume_json(
        reversed_payload
    )
    assert pre_resume_proof_identity(payload) == pre_resume_proof_identity(
        reversed_payload
    )


@pytest.mark.unit
def test_candidate_semantic_lookup_rejects_missing_and_extra_keys() -> None:
    population = CandidatePopulation(
        tuples=(CandidateTuple("C1", "C10", "C20", "C30"),),
        occurrences=(CandidateOccurrence("C1", "a" * 64, "C1", "C10", "C20"),),
    )

    for malformed in ({}, {"C10": None, "C11": None}):
        with pytest.raises(ValueError, match="cover every candidate filler"):
            population.missing_p106_verdict(malformed)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"run_id": "wrong"}, "run identity"),
        ({"live_source_identity": "0" * 64}, "source identity"),
        ({"live_release": "wrong"}, "source identity"),
        ({"source_observation_reads": 0}, "execute QLever reads"),
    ],
)
def test_proof_request_rejects_unbound_source_or_stale_observation(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "run_id": EXPECTED_RUN_ID,
        "live_source_identity": EXPECTED_SOURCE_IDENTITY,
        "live_release": EXPECTED_RELEASE,
        "source_observation_reads": 1,
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        _validate_proof_request(**values)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            {
                "concept_code": "C1",
                "occurrence_id": "a" * 64,
                "anchor_code": "C2",
                "filler_code": 10,
                "morphology_code": "C3",
            },
            "non-string",
        ),
        (
            {
                "concept_code": "not-a-code",
                "occurrence_id": "a" * 64,
                "anchor_code": "C2",
                "filler_code": "C10",
                "morphology_code": "C3",
            },
            "malformed",
        ),
        (
            {
                "concept_code": "C1",
                "occurrence_id": "short",
                "anchor_code": "C2",
                "filler_code": "C10",
                "morphology_code": "C3",
            },
            "malformed",
        ),
    ],
)
def test_candidate_rows_fail_closed_on_partial_or_malformed_postgres_shape(
    row: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _parse_candidate_rows((row,))  # type: ignore[arg-type]


@pytest.mark.unit
def test_candidate_rows_reject_conflicting_persisted_parent_morphologies() -> None:
    common = {
        "concept_code": "C1",
        "anchor_code": "C2",
        "filler_code": "C10",
    }
    rows = (
        {**common, "occurrence_id": "a" * 64, "morphology_code": "C3"},
        {**common, "occurrence_id": "b" * 64, "morphology_code": "C4"},
    )

    with pytest.raises(ValueError, match="multiple parent morphologies"):
        _parse_candidate_rows(rows)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (({"concept_code": "C1", "state": "running", "attempt_count": 0},), "state"),
        (({"concept_code": "C1", "state": "pending", "attempt_count": 1},), "attempt"),
        (({"concept_code": "C1", "state": "complete", "attempt_count": 0},), "attempt"),
        (({"concept_code": "C1", "state": "pending", "attempt_count": 0},), "digest"),
    ],
)
def test_work_cohorts_reject_state_attempt_and_denominator_corruption(
    rows: tuple[dict[str, object], ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _work_cohorts(rows)  # type: ignore[arg-type]


def _candidate_evidence() -> CandidateEvidence:
    production = CandidatePopulation(
        (CandidateTuple("C1", "C10", "C20", "C30"),),
        (CandidateOccurrence("C1", "a" * 64, "C2", "C10", "C20"),),
    )
    sensitivity = CandidatePopulation(
        (
            CandidateTuple("C1", "C10", "C20", "C30"),
            CandidateTuple("C1", "C11", "C20", "C30"),
        ),
        (
            CandidateOccurrence("C1", "a" * 64, "C2", "C10", "C20"),
            CandidateOccurrence("C1", "b" * 64, "C2", "C11", "C20"),
        ),
    )
    return CandidateEvidence(
        production,
        sensitivity,
        MissingP106Verdict(()),
        {"C10": "Body Location"},
        1,
        1,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("production", "production candidate denominator"),
        ("sensitivity", "sensitivity control"),
        ("affected", "affected set"),
    ],
)
def test_candidate_evidence_rejects_wrong_denominator_or_nonzero_impact(
    mutation: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = _candidate_evidence()
    monkeypatch.setattr(
        "ontolib.decomposition.pre_resume.EXPECTED_CANDIDATE_COUNTS",
        evidence.production.counts,
    )
    monkeypatch.setattr(
        "ontolib.decomposition.pre_resume.EXPECTED_CANDIDATE_DIGEST",
        evidence.production.identity,
    )
    monkeypatch.setattr(
        "ontolib.decomposition.pre_resume.EXPECTED_SENSITIVITY_COUNTS",
        evidence.route_filter_sensitivity.counts,
    )
    monkeypatch.setattr(
        "ontolib.decomposition.pre_resume.EXPECTED_SENSITIVITY_DIGEST",
        evidence.route_filter_sensitivity.identity,
    )
    if mutation == "production":
        monkeypatch.setattr(
            "ontolib.decomposition.pre_resume.EXPECTED_CANDIDATE_COUNTS", (9, 9, 9, 9)
        )
    elif mutation == "sensitivity":
        monkeypatch.setattr(
            "ontolib.decomposition.pre_resume.EXPECTED_SENSITIVITY_DIGEST", "0" * 64
        )
    else:
        evidence = CandidateEvidence(
            evidence.production,
            evidence.route_filter_sensitivity,
            MissingP106Verdict(evidence.production.occurrences),
            evidence.semantic_types,
            1,
            1,
        )

    with pytest.raises(ValueError, match=message):
        _validate_candidate_evidence(evidence)
