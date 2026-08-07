"""Typed adjudicated and observed semantic-bundle contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import combinations
from typing import Literal, get_args

_CONCEPT_CODE = re.compile(r"C[0-9]+")
_ROLE_CODE = re.compile(r"R[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_STAGE_MEMBER_COUNT = 2

type MemberKey = tuple[str, str, str]
type ContextKey = tuple[str, str, str, str]
type AvailabilityStatus = Literal["available", "deferred", "incomplete"]
type ProjectionAxisSource = Literal["role", "nlp", "parent"]
PairProvenance = Literal[
    "ncit-26.07d",
    "locally-approved",
    "proposed",
    "submitted",
    "accepted-in-ncit",
]

_PAIR_PROVENANCE = frozenset(get_args(PairProvenance))


class BundleKind(StrEnum):
    CANCER_STAGE = "ontoprism-cancer-stage"


class MemberRole(StrEnum):
    STAGE_TYPE = "stage-type"
    STAGING_METHOD = "staging-method"
    STAGE_VALUE = "stage-value"
    CLASSIFICATION_CONTEXT = "classification-context"


class BundleAxis(StrEnum):
    STAGE_SYSTEM = "op:StageSystem"
    STAGE_VALUE = "op:StageValue"


class EvidenceClaimKind(StrEnum):
    STRUCTURE = "structure"
    MEMBER = "member"


def _require_nonempty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_match(value: str, pattern: re.Pattern[str], field_name: str) -> None:
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid: {value!r}")


def _stable_identity(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_restriction_fact_id(
    anchor_code: str,
    source_group_id: str,
    source_role: str,
    filler_code: str,
) -> str:
    """Return the complete-definition identity for one restriction fact."""
    parts = (anchor_code, source_group_id, "restriction", source_role, filler_code)
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceOccurrence:
    """One exact restriction occurrence in a hash-bound NCIt source audit."""

    source_identity: str
    ncit_release: str
    root_code: str
    fact_id: str
    source_role: str
    filler_code: str
    anchor_code: str
    depth: int
    source_group_id: str

    def __post_init__(self) -> None:
        _require_match(self.source_identity, _SHA256, "source_identity")
        _require_nonempty(self.ncit_release, "ncit_release")
        _require_match(self.root_code, _CONCEPT_CODE, "root_code")
        _require_match(self.filler_code, _CONCEPT_CODE, "filler_code")
        _require_match(self.anchor_code, _CONCEPT_CODE, "anchor_code")
        _require_nonempty(self.source_group_id, "source_group_id")
        if self.source_role != "R88":
            raise ValueError("stage source occurrences must use R88")
        expected = canonical_restriction_fact_id(
            self.anchor_code,
            self.source_group_id,
            self.source_role,
            self.filler_code,
        )
        if self.fact_id != expected:
            raise ValueError("fact_id does not match canonical restriction identity")
        if self.depth < 0:
            raise ValueError("depth must be non-negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class StageClassification:
    authority: str
    version: str

    def __post_init__(self) -> None:
        _require_nonempty(self.authority, "authority")
        _require_nonempty(self.version, "version")


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceClaimTarget:
    subject_code: str | None
    role: MemberRole
    filler_code: str

    def __post_init__(self) -> None:
        if self.subject_code is not None:
            _require_match(self.subject_code, _CONCEPT_CODE, "subject_code")
        if not isinstance(self.role, MemberRole):
            raise ValueError("evidence target role must be typed")
        _require_match(self.filler_code, _CONCEPT_CODE, "filler_code")


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceClaim:
    """A versioned, hash-bound assertion made by one external source."""

    claim_id: str
    kind: EvidenceClaimKind
    source_id: str
    source_version: str
    uri: str
    assertion: str
    target: EvidenceClaimTarget | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.claim_id, "claim_id")
        if not isinstance(self.kind, EvidenceClaimKind):
            raise ValueError("evidence claim kind must be typed")
        for field_name in ("source_id", "source_version", "uri", "assertion"):
            _require_nonempty(getattr(self, field_name), field_name)
        _validate_claim_target(self.kind, self.target)

    @property
    def claim_identity(self) -> str:
        target = self.target
        return _stable_identity(
            {
                "kind": self.kind.value,
                "source_id": self.source_id,
                "source_version": self.source_version,
                "uri": self.uri,
                "assertion": self.assertion,
                "target": (
                    {
                        "subject_code": target.subject_code,
                        "role": target.role.value,
                        "filler_code": target.filler_code,
                    }
                    if target is not None
                    else None
                ),
            }
        )


def _validate_claim_target(
    kind: EvidenceClaimKind, target: EvidenceClaimTarget | None
) -> None:
    if kind is EvidenceClaimKind.MEMBER and target is None:
        raise ValueError("member evidence claims require an exact target")
    if kind is EvidenceClaimKind.STRUCTURE and target is not None:
        raise ValueError("structural evidence claims cannot target a member")


@dataclass(frozen=True, slots=True)
class EvidenceRegistry:
    claims: tuple[EvidenceClaim, ...]

    def __post_init__(self) -> None:
        canonical = tuple(sorted(self.claims, key=lambda claim: claim.claim_id))
        if len({claim.claim_id for claim in canonical}) != len(canonical):
            raise ValueError("evidence claim IDs must be unique")
        object.__setattr__(self, "claims", canonical)

    @property
    def identity(self) -> str:
        return _stable_identity(
            [
                {
                    "claim_id": claim.claim_id,
                    "kind": claim.kind.value,
                    "source_id": claim.source_id,
                    "source_version": claim.source_version,
                    "claim_identity": claim.claim_identity,
                    "uri": claim.uri,
                    "assertion": claim.assertion,
                    "target": (
                        {
                            "subject_code": claim.target.subject_code,
                            "role": claim.target.role.value,
                            "filler_code": claim.target.filler_code,
                        }
                        if claim.target
                        else None
                    ),
                }
                for claim in self.claims
            ]
        )

    def get(self, claim_id: str) -> EvidenceClaim:
        for claim in self.claims:
            if claim.claim_id == claim_id:
                return claim
        raise ValueError(f"unknown evidence claim: {claim_id}")


def _required_axis(role: MemberRole) -> BundleAxis:
    return (
        BundleAxis.STAGE_VALUE
        if role is MemberRole.STAGE_VALUE
        else BundleAxis.STAGE_SYSTEM
    )


def _validate_projection_metadata(
    relationship_group: str | None,
    source_role: str | None,
    axis_source: ProjectionAxisSource | None,
) -> None:
    if relationship_group is not None:
        _require_nonempty(relationship_group, "relationship_group")
    if source_role is not None:
        _require_match(source_role, _ROLE_CODE, "source_role")
    if axis_source not in {None, "role", "nlp", "parent"}:
        raise ValueError("axis_source is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticBundleMember:
    role: MemberRole
    axis: BundleAxis
    filler_code: str
    provenance_status: PairProvenance
    source_occurrences: tuple[SourceOccurrence, ...] = ()
    evidence_claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.role, MemberRole):
            raise ValueError("semantic member role must be typed")
        if not isinstance(self.axis, BundleAxis):
            raise ValueError("semantic member axis must be typed")
        if self.axis is not _required_axis(self.role):
            required_axis = _required_axis(self.role)
            raise ValueError(f"{self.role.value} role requires {required_axis}")
        _require_match(self.filler_code, _CONCEPT_CODE, "filler_code")
        if self.provenance_status not in _PAIR_PROVENANCE:
            raise ValueError("semantic member provenance status is invalid")
        occurrences, claim_ids = _canonical_member_support(self)
        object.__setattr__(self, "source_occurrences", occurrences)
        object.__setattr__(self, "evidence_claim_ids", claim_ids)

    @property
    def pair(self) -> tuple[BundleAxis, str]:
        return self.axis, self.filler_code

    @property
    def semantic_key(self) -> MemberKey:
        return self.role.value, self.axis.value, self.filler_code


def _canonical_member_support(
    member: SemanticBundleMember,
) -> tuple[tuple[SourceOccurrence, ...], tuple[str, ...]]:
    occurrences = tuple(
        sorted(
            member.source_occurrences,
            key=lambda occurrence: (occurrence.source_identity, occurrence.fact_id),
        )
    )
    if len(set(occurrences)) != len(occurrences):
        raise ValueError("source occurrences must be unique")
    if any(item.filler_code != member.filler_code for item in occurrences):
        raise ValueError("source occurrence filler must match its member")
    claim_ids = tuple(sorted(member.evidence_claim_ids))
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("member evidence claim IDs must be unique")
    if not occurrences and not claim_ids:
        raise ValueError(
            "semantic members require a source occurrence or evidence claim"
        )
    return occurrences, claim_ids


def _validate_stage_members(
    members: tuple[SemanticBundleMember | ObservedBundleMember, ...],
) -> None:
    counts = Counter(member.role for member in members)
    frameworks = counts[MemberRole.STAGE_TYPE] + counts[MemberRole.STAGING_METHOD]
    if frameworks != 1:
        raise ValueError("stage bundles require exactly one classification framework")
    if counts[MemberRole.STAGE_VALUE] != 1:
        raise ValueError("stage bundles require exactly one stage value")
    if len(members) != _STAGE_MEMBER_COUNT:
        raise ValueError("stage bundles contain only framework and value members")


def _validate_subject_occurrences(
    subject_code: str, members: tuple[SemanticBundleMember, ...]
) -> None:
    if any(
        occurrence.root_code != subject_code
        for member in members
        for occurrence in member.source_occurrences
    ):
        raise ValueError("source occurrence root must match the construct subject")


def _validate_source_snapshot(members: tuple[SemanticBundleMember, ...]) -> None:
    snapshots = {
        (occurrence.source_identity, occurrence.ncit_release)
        for member in members
        for occurrence in member.source_occurrences
    }
    if len(snapshots) > 1:
        raise ValueError("semantic candidate must use one NCIt source snapshot")


def _semantic_identity(
    subject_code: str,
    kind: BundleKind,
    classification: StageClassification,
    members: tuple[SemanticBundleMember | ObservedBundleMember, ...],
) -> str:
    return _stable_identity(
        (
            "ontoprism-semantic-bundle-v1",
            subject_code,
            kind.value,
            classification.authority,
            classification.version,
            tuple(sorted(member.semantic_key for member in members)),
        )
    )


def _canonical_evidence_ids(
    values: tuple[str, ...],
    *,
    required_message: str,
    duplicate_message: str,
    empty_message: str | None = None,
) -> tuple[str, ...]:
    canonical = tuple(sorted(values))
    if not canonical:
        raise ValueError(required_message)
    if empty_message is not None and any(not value.strip() for value in canonical):
        raise ValueError(empty_message)
    if len(set(canonical)) != len(canonical):
        raise ValueError(duplicate_message)
    return canonical


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticBundleCandidate:
    candidate_id: str
    subject_code: str
    name: str
    kind: BundleKind
    classification: StageClassification
    members: tuple[SemanticBundleMember, ...]
    evidence_claim_ids: tuple[str, ...]
    evidence_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.candidate_id, "candidate_id")
        _require_match(self.subject_code, _CONCEPT_CODE, "subject_code")
        _require_nonempty(self.name, "name")
        if self.kind is not BundleKind.CANCER_STAGE:
            raise ValueError("candidate kind must be the ONTOPRISM cancer-stage model")
        _validate_stage_members(self.members)
        _validate_subject_occurrences(self.subject_code, self.members)
        _validate_source_snapshot(self.members)
        claim_ids = _canonical_evidence_ids(
            self.evidence_claim_ids,
            required_message="bundle candidates require structural evidence claims",
            duplicate_message="bundle evidence claim IDs must be unique",
        )
        object.__setattr__(self, "evidence_claim_ids", claim_ids)
        source_ids = _canonical_evidence_ids(
            self.evidence_source_ids,
            required_message="bundle candidates require evidence source references",
            duplicate_message="bundle evidence source IDs must be unique",
            empty_message="bundle evidence source IDs must not be empty",
        )
        object.__setattr__(self, "evidence_source_ids", source_ids)

    @property
    def semantic_identity(self) -> str:
        return _semantic_identity(
            self.subject_code,
            self.kind,
            self.classification,
            self.members,
        )


def _validate_member_claim(
    subject_code: str,
    member: SemanticBundleMember,
    claim: EvidenceClaim,
) -> None:
    target = claim.target
    supported_subject = target and target.subject_code in {None, subject_code}
    if (
        claim.kind is not EvidenceClaimKind.MEMBER
        or target is None
        or not supported_subject
        or target.role is not member.role
        or target.filler_code != member.filler_code
    ):
        raise ValueError(f"evidence claim {claim.claim_id} does not support member")


def validate_candidate_evidence(
    candidate: SemanticBundleCandidate, registry: EvidenceRegistry
) -> None:
    for claim_id in candidate.evidence_claim_ids:
        claim = registry.get(claim_id)
        if claim.kind is not EvidenceClaimKind.STRUCTURE:
            raise ValueError(f"evidence claim {claim_id} is not structural evidence")
    for member in candidate.members:
        for claim_id in member.evidence_claim_ids:
            claim = registry.get(claim_id)
            _validate_member_claim(candidate.subject_code, member, claim)


@dataclass(frozen=True, slots=True, kw_only=True)
class AdjudicatedSemanticBundle:
    candidate: SemanticBundleCandidate
    decision_id: str
    decision: Literal["ACCEPT"]
    rationale: str
    reviewer: str
    reviewed_at: str

    def __post_init__(self) -> None:
        _require_nonempty(self.decision_id, "decision_id")
        if self.decision != "ACCEPT":
            raise ValueError("adjudicated semantic bundle must be accepted")
        _require_nonempty(self.rationale, "rationale")
        _require_nonempty(self.reviewer, "reviewer")
        try:
            date.fromisoformat(self.reviewed_at)
        except ValueError as error:
            raise ValueError("reviewed_at must be an ISO date") from error

    @property
    def semantic_identity(self) -> str:
        return self.candidate.semantic_identity


@dataclass(frozen=True, slots=True, kw_only=True)
class AdjudicatedSemanticContext:
    context_id: str
    subject_code: str
    name: str
    member: SemanticBundleMember
    rationale: str

    def __post_init__(self) -> None:
        _require_nonempty(self.context_id, "context_id")
        _require_match(self.subject_code, _CONCEPT_CODE, "subject_code")
        _require_nonempty(self.name, "name")
        _require_nonempty(self.rationale, "rationale")
        if self.member.role is not MemberRole.CLASSIFICATION_CONTEXT:
            raise ValueError(
                "semantic context requires a classification-context member"
            )
        _validate_subject_occurrences(self.subject_code, (self.member,))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectedConstituentEvidence:
    axis: BundleAxis
    filler_code: str
    needs_review: bool
    relationship_group: str | None
    source_role: str | None
    axis_source: ProjectionAxisSource | None
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.axis, BundleAxis):
            raise ValueError("projected constituent axis must be typed")
        _require_match(self.filler_code, _CONCEPT_CODE, "filler_code")
        _validate_projection_metadata(
            self.relationship_group,
            self.source_role,
            self.axis_source,
        )
        canonical = tuple(sorted(self.source_fact_ids))
        for fact_id in canonical:
            _require_match(fact_id, _SHA256, "source_fact_id")
        if len(set(canonical)) != len(canonical):
            raise ValueError("source fact IDs must be unique")
        object.__setattr__(self, "source_fact_ids", canonical)

    @property
    def pair(self) -> tuple[BundleAxis, str]:
        return self.axis, self.filler_code


@dataclass(frozen=True, slots=True, kw_only=True)
class BundlePairAvailability:
    candidate_id: str
    available_members: tuple[SemanticBundleMember, ...]
    deferred_members: tuple[SemanticBundleMember, ...]
    missing_members: tuple[SemanticBundleMember, ...]
    available_evidence: tuple[ProjectedConstituentEvidence, ...]
    deferred_evidence: tuple[ProjectedConstituentEvidence, ...]

    @property
    def status(self) -> AvailabilityStatus:
        if self.missing_members:
            return "incomplete"
        if self.deferred_members:
            return "deferred"
        return "available"


def evaluate_pair_availability(
    candidate: SemanticBundleCandidate,
    constituents: tuple[ProjectedConstituentEvidence, ...],
) -> BundlePairAvailability:
    by_pair = {constituent.pair: constituent for constituent in constituents}
    if len(by_pair) != len(constituents):
        raise ValueError("projected constituent pairs must be unique")
    available: list[SemanticBundleMember] = []
    deferred: list[SemanticBundleMember] = []
    missing: list[SemanticBundleMember] = []
    available_evidence: list[ProjectedConstituentEvidence] = []
    deferred_evidence: list[ProjectedConstituentEvidence] = []
    for member in candidate.members:
        evidence = by_pair.get(member.pair)
        if evidence is None:
            missing.append(member)
        elif evidence.needs_review:
            deferred.append(member)
            deferred_evidence.append(evidence)
        else:
            available.append(member)
            available_evidence.append(evidence)
    return BundlePairAvailability(
        candidate_id=candidate.candidate_id,
        available_members=tuple(available),
        deferred_members=tuple(deferred),
        missing_members=tuple(missing),
        available_evidence=tuple(available_evidence),
        deferred_evidence=tuple(deferred_evidence),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservedBundleMember:
    role: MemberRole
    axis: BundleAxis
    filler_code: str
    needs_review: bool
    source_fact_ids: tuple[str, ...]
    relationship_group: str | None = None
    source_role: str | None = None
    axis_source: ProjectionAxisSource | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, MemberRole):
            raise ValueError("observed member role must be typed")
        if not isinstance(self.axis, BundleAxis):
            raise ValueError("observed member axis must be typed")
        if self.role is MemberRole.CLASSIFICATION_CONTEXT:
            raise ValueError("observed bundles cannot contain context-only members")
        if self.axis is not _required_axis(self.role):
            required_axis = _required_axis(self.role)
            raise ValueError(f"{self.role.value} role requires {required_axis}")
        _require_match(self.filler_code, _CONCEPT_CODE, "filler_code")
        _validate_projection_metadata(
            self.relationship_group,
            self.source_role,
            self.axis_source,
        )
        canonical = tuple(sorted(self.source_fact_ids))
        for fact_id in canonical:
            _require_match(fact_id, _SHA256, "source_fact_id")
        if len(set(canonical)) != len(canonical):
            raise ValueError("source fact IDs must be unique")
        object.__setattr__(self, "source_fact_ids", canonical)

    @property
    def semantic_key(self) -> MemberKey:
        return self.role.value, self.axis.value, self.filler_code


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservedSemanticBundle:
    """A semantic association explicitly emitted by an engine."""

    association_id: str
    subject_code: str
    kind: BundleKind
    classification: StageClassification
    members: tuple[ObservedBundleMember, ...]

    def __post_init__(self) -> None:
        _require_match(self.association_id, _SHA256, "association_id")
        _require_match(self.subject_code, _CONCEPT_CODE, "subject_code")
        if self.kind is not BundleKind.CANCER_STAGE:
            raise ValueError("observed kind must be the ONTOPRISM cancer-stage model")
        _validate_stage_members(self.members)

    @property
    def semantic_identity(self) -> str:
        return _semantic_identity(
            self.subject_code,
            self.kind,
            self.classification,
            self.members,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticMetricScore:
    expected: int
    actual: int
    true_positive: int
    missing: frozenset[str]
    extra: frozenset[str]

    def __post_init__(self) -> None:
        if min(self.expected, self.actual, self.true_positive) < 0:
            raise ValueError("semantic metric counts must be non-negative")
        if self.true_positive > min(self.expected, self.actual):
            raise ValueError("semantic metric true positives exceed a denominator")
        if len(self.missing) != self.expected - self.true_positive:
            raise ValueError("semantic metric missing set does not match counts")
        if len(self.extra) != self.actual - self.true_positive:
            raise ValueError("semantic metric extra set does not match counts")
        if self.missing & self.extra:
            raise ValueError("semantic metric missing and extra sets must be disjoint")

    @property
    def precision(self) -> float | None:
        return self.true_positive / self.actual if self.actual else None

    @property
    def recall(self) -> float | None:
        return self.true_positive / self.expected if self.expected else None

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if precision is None or recall is None:
            return None
        if not precision + recall:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    @property
    def exact(self) -> bool:
        return not self.missing and not self.extra


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticBundleScore:
    exact_bundle: SemanticMetricScore
    contextual_member: SemanticMetricScore
    association: SemanticMetricScore


def _score_identities(expected: set[str], actual: set[str]) -> SemanticMetricScore:
    return SemanticMetricScore(
        expected=len(expected),
        actual=len(actual),
        true_positive=len(expected & actual),
        missing=frozenset(expected - actual),
        extra=frozenset(actual - expected),
    )


def _candidate_context(candidate: SemanticBundleCandidate) -> ContextKey:
    return (
        candidate.subject_code,
        candidate.kind.value,
        candidate.classification.authority,
        candidate.classification.version,
    )


def _observed_context(bundle: ObservedSemanticBundle) -> ContextKey:
    return (
        bundle.subject_code,
        bundle.kind.value,
        bundle.classification.authority,
        bundle.classification.version,
    )


def _contextual_member_identity(context: ContextKey, member: MemberKey) -> str:
    return _stable_identity(("contextual-member-v1", context, member))


def _association_identity(context: ContextKey, members: tuple[MemberKey, ...]) -> str:
    payload = ("semantic-association-v1", context, tuple(sorted(members)))
    return _stable_identity(payload)


def _expected_member_ids(
    bundles: tuple[AdjudicatedSemanticBundle, ...],
) -> set[str]:
    return {
        _contextual_member_identity(
            _candidate_context(bundle.candidate), member.semantic_key
        )
        for bundle in bundles
        for member in bundle.candidate.members
    }


def _actual_member_ids(bundles: tuple[ObservedSemanticBundle, ...]) -> set[str]:
    return {
        _contextual_member_identity(_observed_context(bundle), member.semantic_key)
        for bundle in bundles
        for member in bundle.members
    }


def _expected_association_ids(
    bundles: tuple[AdjudicatedSemanticBundle, ...],
) -> set[str]:
    return {
        _association_identity(
            _candidate_context(bundle.candidate),
            tuple(member.semantic_key for member in pair),
        )
        for bundle in bundles
        for pair in combinations(bundle.candidate.members, 2)
    }


def _actual_association_ids(bundles: tuple[ObservedSemanticBundle, ...]) -> set[str]:
    return {
        _association_identity(
            _observed_context(bundle),
            tuple(member.semantic_key for member in pair),
        )
        for bundle in bundles
        for pair in combinations(bundle.members, 2)
    }


def score_observed_bundles(
    expected: tuple[AdjudicatedSemanticBundle, ...],
    actual: tuple[ObservedSemanticBundle, ...],
) -> SemanticBundleScore:
    """Score only explicitly observed associations against adjudicated bundles."""
    expected_ids = {bundle.semantic_identity for bundle in expected}
    actual_ids = {bundle.semantic_identity for bundle in actual}
    if len(expected_ids) != len(expected):
        raise ValueError("adjudicated semantic bundle identities must be unique")
    if len(actual_ids) != len(actual):
        raise ValueError("observed semantic bundle identities must be unique")
    return SemanticBundleScore(
        exact_bundle=_score_identities(expected_ids, actual_ids),
        contextual_member=_score_identities(
            _expected_member_ids(expected),
            _actual_member_ids(actual),
        ),
        association=_score_identities(
            _expected_association_ids(expected),
            _actual_association_ids(actual),
        ),
    )
