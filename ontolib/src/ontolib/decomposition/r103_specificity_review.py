"""Strict C2860-only target, pending state, and accountable R103 selection."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ontolib.decomposition.r103_evidence_application import (
    R103AppliedPolicyReport,
    R103AuthorityArtifact,
    R103CandidateArtifact,
    R103SourceInventory,
    R103SourceRow,
    load_applied_policy_report,
    load_authority_artifact,
    load_candidate_artifact,
    load_source_inventory,
)
from ontolib.decomposition.r103_review_promotion import (
    load_r103_promoted_review_revision,
)

_SHA256 = r"^[0-9a-f]{64}$"
_EXPECTED_CANDIDATE_COUNT = 16
SPECIFICITY_QUESTION = (
    "For C2860/R103/C12950, does one of the 16 enumerated named stated "
    "descendants of C12950 in NCIt 26.07d provide a better normal-tissue-origin "
    "filler than C12950?"
)
type SpecificityChoice = Literal[
    "affirm-no-better-enumerated-candidate",
    "qualify-global-most-specific-claim",
    "propose-enumerated-candidate-replacement",
]
SPECIFICITY_OPTIONS: tuple[tuple[SpecificityChoice, str], ...] = (
    (
        "affirm-no-better-enumerated-candidate",
        "Affirm that none of the 16 enumerated named stated descendants of C12950 "
        "in NCIt 26.07d is a better filler for C2860/R103 than C12950.",
    ),
    (
        "qualify-global-most-specific-claim",
        "Retain the source-supported C2860/R103/C12950 assertion while qualifying "
        "or withdrawing the global most-specific claim.",
    ),
    (
        "propose-enumerated-candidate-replacement",
        "Select one of the 16 enumerated existing NCIt candidates as a proposed "
        "replacement and initiate a separately governed correction proposal.",
    ),
)
SELECTED_BOUNDED_CONCLUSION = (
    "None of the 16 enumerated named stated descendants of C12950 in NCIt 26.07d "
    "is a better normal-tissue-origin filler for C2860/R103 than C12950."
)
SELECTED_EFFECTIVE_RATIONALE = (
    "Retain C12950 as the source-supported C2860/R103 filler. "
    f"{SELECTED_BOUNDED_CONCLUSION} This bounded comparison does not establish "
    "that C12950 is the globally most-specific available NCIt filler."
)


class R103SpecificityReviewError(ValueError):
    """The C2860 specificity-review evidence is invalid or mismatched."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    ).hexdigest()


class SpecificityReviewOption(_StrictModel):
    option: SpecificityChoice
    semantics: str = Field(min_length=1)


class R103SpecificityReviewTarget(_StrictModel):
    schema_version: Literal[1]
    subject_code: Literal["C2860"]
    role_code: Literal["R103"]
    filler_code: Literal["C12950"]
    release: Literal["26.07d"]
    source_identity: str = Field(pattern=_SHA256)
    source_inventory_identity: str = Field(pattern=_SHA256)
    source_fact_identity: str = Field(pattern=_SHA256)
    source_group_identity: str = Field(pattern=_SHA256)
    source_occurrence_identity: str = Field(pattern=_SHA256)
    source_row_identity: str = Field(pattern=_SHA256)
    predecessor_decision_identity: str = Field(pattern=_SHA256)
    prior_decision_identity: str = Field(pattern=_SHA256)
    authority_artifact_identity: str = Field(pattern=_SHA256)
    candidate_artifact_identity: str = Field(pattern=_SHA256)
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        expected = _identity(self.model_dump(exclude={"artifact_identity"}))
        if self.artifact_identity != expected:
            raise ValueError("specificity-review target identity differs")
        return self


class R103PendingSpecificityReview(_StrictModel):
    schema_version: Literal[1]
    status: Literal["pending-human-specificity-review"]
    question_kind: Literal["most-specific-named-stated-descendant"]
    subject_code: Literal["C2860"]
    role_code: Literal["R103"]
    filler_code: Literal["C12950"]
    question: str = Field(min_length=1)
    allowed_options: tuple[
        SpecificityReviewOption,
        SpecificityReviewOption,
        SpecificityReviewOption,
    ]
    selected_option: Literal[None]
    selected_candidate_code: Literal[None]
    target_artifact_identity: str = Field(pattern=_SHA256)
    candidate_artifact_identity: str = Field(pattern=_SHA256)
    source_inventory_identity: str = Field(pattern=_SHA256)
    authority_artifact_identity: str = Field(pattern=_SHA256)
    prior_decision_identity: str = Field(pattern=_SHA256)
    prior_decision_outcome: Literal["source-supported"]
    prior_decision_rationale: str = Field(min_length=1)
    software_selected_answer: Literal[False]
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_contract(self) -> Self:
        observed = tuple((item.option, item.semantics) for item in self.allowed_options)
        if self.question != SPECIFICITY_QUESTION:
            raise ValueError("specificity-review question differs")
        if observed != SPECIFICITY_OPTIONS:
            raise ValueError("specificity-review options differ")
        expected = _identity(self.model_dump(exclude={"artifact_identity"}))
        if self.artifact_identity != expected:
            raise ValueError("pending specificity-review identity differs")
        return self


class SpecificityReviewTranscription(_StrictModel):
    actor: Literal["software-transcriber"]
    authority: Literal["user-confirmed-in-current-conversation"]
    authorship_claimed: Literal[False]
    confirmation_date: Literal["2026-09-07"]


class R103SelectedSpecificityReview(_StrictModel):
    schema_version: Literal[1]
    status: Literal["selected-human-specificity-review"]
    question_kind: Literal["most-specific-named-stated-descendant"]
    subject_code: Literal["C2860"]
    role_code: Literal["R103"]
    filler_code: Literal["C12950"]
    question: str = Field(min_length=1)
    selected_option: Literal["qualify-global-most-specific-claim"]
    selected_candidate_code: Literal[None]
    enumerated_candidate_count: Literal[16]
    bounded_conclusion: str = Field(min_length=1)
    effective_outcome: Literal["source-supported"]
    effective_rationale: str = Field(min_length=1)
    global_claim_disposition: Literal["withdrawn-bounded-comparison-not-global-proof"]
    target_artifact_identity: str = Field(pattern=_SHA256)
    candidate_artifact_identity: str = Field(pattern=_SHA256)
    applied_policy_identity: str = Field(pattern=_SHA256)
    prior_decision_identity: str = Field(pattern=_SHA256)
    transcription: SpecificityReviewTranscription
    proposal_created: Literal[False]
    nci_adoption_inferred: Literal[False]
    software_selected_answer: Literal[False]
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_contract(self) -> Self:
        if self.question != SPECIFICITY_QUESTION:
            raise ValueError("selected specificity-review question differs")
        if self.bounded_conclusion != SELECTED_BOUNDED_CONCLUSION:
            raise ValueError("selected specificity-review bounded conclusion differs")
        if self.effective_rationale != SELECTED_EFFECTIVE_RATIONALE:
            raise ValueError("selected specificity-review effective rationale differs")
        expected = _identity(self.model_dump(exclude={"artifact_identity"}))
        if self.artifact_identity != expected:
            raise ValueError("selected specificity-review identity differs")
        return self


type R103SpecificityReview = (
    R103PendingSpecificityReview | R103SelectedSpecificityReview
)


def _c2860_row(inventory: R103SourceInventory) -> R103SourceRow:
    matches = tuple(row for row in inventory.rows if row.subject_code == "C2860")
    if len(matches) != 1:
        raise R103SpecificityReviewError("exact C2860 source occurrence is absent")
    row = matches[0]
    if (row.role_code, row.filler_code) != ("R103", "C12950"):
        raise R103SpecificityReviewError("C2860 review assertion differs")
    return row


def build_specificity_review_target(
    *,
    inventory: R103SourceInventory,
    candidates: R103CandidateArtifact,
    authority: R103AuthorityArtifact,
) -> R103SpecificityReviewTarget:
    """Bind generic candidate evidence to the exact carried-forward C2860 assertion."""
    row = _c2860_row(inventory)
    entry = authority.entries[0]
    if (
        (entry.subject_code, entry.role_code, entry.filler_code)
        != ("C2860", "R103", "C12950")
        or entry.authority_kind != "carried-forward-predecessor-decision"
        or entry.source_occurrence_identity != row.source_occurrence_identity
        or authority.source_inventory_identity != inventory.artifact_identity
        or candidates.source_identity != inventory.source_identity
        or candidates.release != inventory.release
    ):
        raise R103SpecificityReviewError("C2860 target evidence binding differs")
    payload = {
        "schema_version": 1,
        "subject_code": "C2860",
        "role_code": "R103",
        "filler_code": "C12950",
        "release": inventory.release,
        "source_identity": inventory.source_identity,
        "source_inventory_identity": inventory.artifact_identity,
        "source_fact_identity": row.source_fact_identity,
        "source_group_identity": row.source_group_identity,
        "source_occurrence_identity": row.source_occurrence_identity,
        "source_row_identity": row.row_identity,
        "predecessor_decision_identity": entry.predecessor_decision_identity,
        "prior_decision_identity": entry.effective_decision_identity,
        "authority_artifact_identity": authority.artifact_identity,
        "candidate_artifact_identity": candidates.artifact_identity,
    }
    return R103SpecificityReviewTarget.model_validate(
        {**payload, "artifact_identity": _identity(payload)}
    )


def build_pending_specificity_review(
    *,
    target: R103SpecificityReviewTarget,
    inventory: R103SourceInventory,
    candidates: R103CandidateArtifact,
    authority: R103AuthorityArtifact,
    revision_path: Path,
) -> R103PendingSpecificityReview:
    """Create an unanswered C2860-only question from certified machine evidence."""
    expected_target = build_specificity_review_target(
        inventory=inventory, candidates=candidates, authority=authority
    )
    if target != expected_target:
        raise R103SpecificityReviewError("specificity-review target differs")
    revision = load_r103_promoted_review_revision(revision_path)
    decision = revision.registry.decisions[0]
    if (
        (decision.subject_code, decision.role_code, decision.filler_code)
        != ("C2860", "R103", "C12950")
        or decision.decision_identity != target.prior_decision_identity
        or decision.outcome != "source-supported"
    ):
        raise R103SpecificityReviewError("C2860 prior decision differs")
    options = tuple(
        SpecificityReviewOption(option=option, semantics=semantics)
        for option, semantics in SPECIFICITY_OPTIONS
    )
    payload = {
        "schema_version": 1,
        "status": "pending-human-specificity-review",
        "question_kind": "most-specific-named-stated-descendant",
        "subject_code": "C2860",
        "role_code": "R103",
        "filler_code": "C12950",
        "question": SPECIFICITY_QUESTION,
        "allowed_options": tuple(option.model_dump(mode="json") for option in options),
        "selected_option": None,
        "selected_candidate_code": None,
        "target_artifact_identity": target.artifact_identity,
        "candidate_artifact_identity": candidates.artifact_identity,
        "source_inventory_identity": inventory.artifact_identity,
        "authority_artifact_identity": authority.artifact_identity,
        "prior_decision_identity": decision.decision_identity,
        "prior_decision_outcome": decision.outcome,
        "prior_decision_rationale": decision.rationale,
        "software_selected_answer": False,
    }
    return R103PendingSpecificityReview.model_validate(
        {**payload, "artifact_identity": _identity(payload)}
    )


def load_specificity_review_target(
    path: Path,
    *,
    inventory: R103SourceInventory,
    candidates: R103CandidateArtifact,
    authority: R103AuthorityArtifact,
) -> R103SpecificityReviewTarget:
    value = _load(path, R103SpecificityReviewTarget)
    expected = build_specificity_review_target(
        inventory=inventory, candidates=candidates, authority=authority
    )
    if value != expected:
        raise R103SpecificityReviewError("specificity-review target binding differs")
    return value


def load_pending_specificity_review(
    path: Path,
    *,
    target: R103SpecificityReviewTarget,
    inventory: R103SourceInventory,
    candidates: R103CandidateArtifact,
    authority: R103AuthorityArtifact,
    revision_path: Path,
) -> R103PendingSpecificityReview:
    value = _load(path, R103PendingSpecificityReview)
    expected = build_pending_specificity_review(
        target=target,
        inventory=inventory,
        candidates=candidates,
        authority=authority,
        revision_path=revision_path,
    )
    if value != expected:
        raise R103SpecificityReviewError("pending specificity-review binding differs")
    return value


def load_specificity_review(
    path: Path,
    *,
    target: R103SpecificityReviewTarget,
    inventory: R103SourceInventory,
    candidates: R103CandidateArtifact,
    authority: R103AuthorityArtifact,
    application: R103AppliedPolicyReport,
    revision_path: Path,
) -> R103SpecificityReview:
    """Load either the unanswered review or a human-selected answer."""
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise R103SpecificityReviewError(str(error)) from error
    if not isinstance(payload, dict):
        raise R103SpecificityReviewError("specificity-review state must be an object")
    if payload.get("status") == "pending-human-specificity-review":
        return load_pending_specificity_review(
            path,
            target=target,
            inventory=inventory,
            candidates=candidates,
            authority=authority,
            revision_path=revision_path,
        )
    return _load_selected_specificity_review(
        path,
        target=target,
        candidates=candidates,
        application=application,
    )


def _load_selected_specificity_review(
    path: Path,
    *,
    target: R103SpecificityReviewTarget,
    candidates: R103CandidateArtifact,
    application: R103AppliedPolicyReport,
) -> R103SelectedSpecificityReview:
    selected = _load(path, R103SelectedSpecificityReview)
    if (
        selected.target_artifact_identity != target.artifact_identity
        or selected.candidate_artifact_identity != candidates.artifact_identity
        or selected.applied_policy_identity != application.artifact_identity
        or selected.prior_decision_identity != target.prior_decision_identity
    ):
        raise R103SpecificityReviewError("selected specificity-review binding differs")
    return selected


def build_selected_specificity_review(
    *,
    pending: R103PendingSpecificityReview,
    target: R103SpecificityReviewTarget,
    candidates: R103CandidateArtifact,
    application: R103AppliedPolicyReport,
) -> R103SelectedSpecificityReview:
    """Transcribe the accountable human choice without claiming software authorship."""
    observed = (
        pending.target_artifact_identity,
        pending.candidate_artifact_identity,
        pending.prior_decision_identity,
        candidates.candidate_count,
        application.c2860_effective_projected,
        application.proposal_created,
        application.nci_adoption_inferred,
    )
    expected = (
        target.artifact_identity,
        candidates.artifact_identity,
        target.prior_decision_identity,
        _EXPECTED_CANDIDATE_COUNT,
        True,
        False,
        False,
    )
    if observed != expected:
        raise R103SpecificityReviewError("selected specificity-review inputs differ")
    payload = {
        "schema_version": 1,
        "status": "selected-human-specificity-review",
        "question_kind": "most-specific-named-stated-descendant",
        "subject_code": "C2860",
        "role_code": "R103",
        "filler_code": "C12950",
        "question": SPECIFICITY_QUESTION,
        "selected_option": "qualify-global-most-specific-claim",
        "selected_candidate_code": None,
        "enumerated_candidate_count": candidates.candidate_count,
        "bounded_conclusion": SELECTED_BOUNDED_CONCLUSION,
        "effective_outcome": "source-supported",
        "effective_rationale": SELECTED_EFFECTIVE_RATIONALE,
        "global_claim_disposition": ("withdrawn-bounded-comparison-not-global-proof"),
        "target_artifact_identity": target.artifact_identity,
        "candidate_artifact_identity": candidates.artifact_identity,
        "applied_policy_identity": application.artifact_identity,
        "prior_decision_identity": target.prior_decision_identity,
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
    return R103SelectedSpecificityReview.model_validate(
        {**payload, "artifact_identity": _identity(payload)}
    )


def generate_selected_specificity_review(
    *,
    inventory_path: Path,
    candidate_path: Path,
    authority_path: Path,
    application_path: Path,
    revision_path: Path,
    target_path: Path,
    pending_path: Path,
    output_path: Path,
) -> R103SelectedSpecificityReview:
    """Generate the selected state from named evidence and the recorded human choice."""
    inventory = load_source_inventory(inventory_path)
    candidates = load_candidate_artifact(candidate_path)
    authority = load_authority_artifact(authority_path)
    application = load_applied_policy_report(application_path)
    target = load_specificity_review_target(
        target_path,
        inventory=inventory,
        candidates=candidates,
        authority=authority,
    )
    pending = load_pending_specificity_review(
        pending_path,
        target=target,
        inventory=inventory,
        candidates=candidates,
        authority=authority,
        revision_path=revision_path,
    )
    selected = build_selected_specificity_review(
        pending=pending,
        target=target,
        candidates=candidates,
        application=application,
    )
    write_artifact(output_path, selected)
    return selected


def generate_specificity_review_artifacts(
    *,
    inventory_path: Path,
    candidate_path: Path,
    authority_path: Path,
    revision_path: Path,
    output_directory: Path,
) -> tuple[R103SpecificityReviewTarget, R103PendingSpecificityReview]:
    """Generate the target and unanswered review state from named machine inputs."""
    inventory = load_source_inventory(inventory_path)
    candidates = load_candidate_artifact(candidate_path)
    authority = load_authority_artifact(authority_path)
    target = build_specificity_review_target(
        inventory=inventory, candidates=candidates, authority=authority
    )
    pending = build_pending_specificity_review(
        target=target,
        inventory=inventory,
        candidates=candidates,
        authority=authority,
        revision_path=revision_path,
    )
    write_artifact(
        output_directory / "r103-c2860-specificity-target-26.07d.json", target
    )
    write_artifact(
        output_directory / "r103-c2860-specificity-pending-26.07d.json", pending
    )
    return target, pending


def _load[ModelT: _StrictModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as error:
        raise R103SpecificityReviewError(str(error)) from error


def write_artifact(path: Path, artifact: _StrictModel) -> None:
    """Atomically write one canonical machine artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(
            artifact.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        ).encode("ascii")
        + b"\n"
    )
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise
