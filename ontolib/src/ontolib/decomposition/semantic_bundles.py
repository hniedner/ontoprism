"""Source-backed semantic bundles and association-aware scoring."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ontolib.decomposition.score import Constituent

_CONCEPT_CODE = re.compile(r"C[0-9]+")
_ROLE_CODE = re.compile(r"R[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")

type Qualifier = tuple[str, str]
type MemberKey = tuple[str, str, str]
type ContextKey = tuple[str, str, tuple[Qualifier, ...]]


def _require_nonempty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_match(value: str, pattern: re.Pattern[str], field_name: str) -> None:
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid: {value!r}")


def _stable_identity(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical_qualifiers(qualifiers: tuple[Qualifier, ...]) -> tuple[Qualifier, ...]:
    for key, value in qualifiers:
        _require_nonempty(key, "qualifier key")
        _require_nonempty(value, "qualifier value")
    canonical = tuple(sorted(qualifiers))
    if len({key for key, _ in canonical}) != len(canonical):
        raise ValueError("qualifier keys must be unique")
    return canonical


def _canonical_evidence(evidence_ids: tuple[str, ...]) -> tuple[str, ...]:
    for evidence_id in evidence_ids:
        _require_nonempty(evidence_id, "evidence ID")
    canonical = tuple(sorted(evidence_ids))
    if len(set(canonical)) != len(canonical):
        raise ValueError("evidence IDs must be unique")
    return canonical


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceFactReference:
    """One lossless reference to a stated NCIt restriction occurrence."""

    ncit_release: str
    root_code: str
    role_code: str
    filler_code: str
    anchor_code: str
    depth: int
    source_group_id: str

    def __post_init__(self) -> None:
        _require_nonempty(self.ncit_release, "ncit_release")
        _require_match(self.root_code, _CONCEPT_CODE, "root_code")
        _require_match(self.role_code, _ROLE_CODE, "role_code")
        _require_match(self.filler_code, _CONCEPT_CODE, "filler_code")
        _require_match(self.anchor_code, _CONCEPT_CODE, "anchor_code")
        _require_match(self.source_group_id, _SHA256, "source_group_id")
        if self.depth < 0:
            raise ValueError("depth must be non-negative")


def _source_fact_key(
    fact: SourceFactReference,
) -> tuple[str, str, str, str, str, int, str]:
    return (
        fact.ncit_release,
        fact.root_code,
        fact.role_code,
        fact.filler_code,
        fact.anchor_code,
        fact.depth,
        fact.source_group_id,
    )


def _canonical_source_facts(
    filler_code: str,
    source_facts: tuple[SourceFactReference, ...],
) -> tuple[SourceFactReference, ...]:
    canonical = tuple(sorted(source_facts, key=_source_fact_key))
    if len(set(canonical)) != len(canonical):
        raise ValueError("source fact references must be unique")
    if any(fact.filler_code != filler_code for fact in canonical):
        raise ValueError("source fact filler must match its semantic member")
    return canonical


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticBundleMember:
    """One typed member with NCIt occurrences or named external support."""

    role: str
    axis: str
    filler_code: str
    source_facts: tuple[SourceFactReference, ...]
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.role, "member role")
        if not self.axis.startswith("op:") or len(self.axis) == len("op:"):
            raise ValueError("member axis must be an op: term")
        _require_match(self.filler_code, _CONCEPT_CODE, "filler_code")
        canonical = _canonical_source_facts(self.filler_code, self.source_facts)
        evidence = _canonical_evidence(self.evidence_ids)
        if not canonical and not evidence:
            raise ValueError(
                "semantic bundle members require a source fact or external evidence"
            )
        object.__setattr__(self, "source_facts", canonical)
        object.__setattr__(self, "evidence_ids", evidence)

    @property
    def pair(self) -> Constituent:
        return self.axis, self.filler_code

    @property
    def semantic_key(self) -> MemberKey:
        return self.role, self.axis, self.filler_code


def _validate_stage_cardinality(
    kind: str, members: tuple[SemanticBundleMember, ...]
) -> None:
    if kind != "cancer-stage-classification":
        return
    role_counts = Counter(member.role for member in members)
    if role_counts["stage-value"] != 1:
        raise ValueError("cancer-stage bundles require exactly one stage-value")
    if role_counts["stage-type"] > 1:
        raise ValueError("cancer-stage bundles permit at most one stage-type")
    if role_counts["staging-method"] > 1:
        raise ValueError("cancer-stage bundles permit at most one staging-method")


def _validate_bundle_fields(
    *,
    rule_id: str,
    subject_code: str,
    kind: str,
    name: str,
    members: tuple[SemanticBundleMember, ...],
) -> None:
    _require_nonempty(rule_id, "rule_id")
    _require_match(subject_code, _CONCEPT_CODE, "subject_code")
    _require_nonempty(kind, "kind")
    _require_nonempty(name, "name")
    if not members:
        raise ValueError("semantic bundles require at least one member")
    if len({member.semantic_key for member in members}) != len(members):
        raise ValueError("semantic bundle members must be unique")
    if any(
        fact.root_code != subject_code
        for member in members
        for fact in member.source_facts
    ):
        raise ValueError("source fact root must match the semantic bundle subject")
    _validate_stage_cardinality(kind, members)


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticBundleRule:
    """A source-backed rule declaring one intended semantic bundle."""

    rule_id: str
    subject_code: str
    kind: str
    name: str
    members: tuple[SemanticBundleMember, ...]
    qualifiers: tuple[Qualifier, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_bundle_fields(
            rule_id=self.rule_id,
            subject_code=self.subject_code,
            kind=self.kind,
            name=self.name,
            members=self.members,
        )
        object.__setattr__(self, "qualifiers", _canonical_qualifiers(self.qualifiers))
        object.__setattr__(self, "evidence_ids", _canonical_evidence(self.evidence_ids))


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticBundle:
    """A complete generated bundle whose members all occur in the decomposition."""

    rule_id: str
    subject_code: str
    kind: str
    name: str
    members: tuple[SemanticBundleMember, ...]
    qualifiers: tuple[Qualifier, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_bundle_fields(
            rule_id=self.rule_id,
            subject_code=self.subject_code,
            kind=self.kind,
            name=self.name,
            members=self.members,
        )
        object.__setattr__(self, "qualifiers", _canonical_qualifiers(self.qualifiers))
        object.__setattr__(self, "evidence_ids", _canonical_evidence(self.evidence_ids))

    @classmethod
    def from_rule(cls, rule: SemanticBundleRule) -> SemanticBundle:
        return cls(
            rule_id=rule.rule_id,
            subject_code=rule.subject_code,
            kind=rule.kind,
            name=rule.name,
            members=rule.members,
            qualifiers=rule.qualifiers,
            evidence_ids=rule.evidence_ids,
        )

    @property
    def context_key(self) -> ContextKey:
        return self.subject_code, self.kind, self.qualifiers

    @property
    def identity(self) -> str:
        """Stable semantic identity, excluding editorial text and provenance."""
        return _stable_identity(
            (
                "semantic-bundle-v1",
                self.context_key,
                tuple(sorted(member.semantic_key for member in self.members)),
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class IncompleteSemanticBundle:
    """A rule that was not emitted because one or more members were absent."""

    rule_id: str
    subject_code: str
    kind: str
    name: str
    qualifiers: tuple[Qualifier, ...]
    evidence_ids: tuple[str, ...]
    present_members: tuple[SemanticBundleMember, ...]
    missing_members: tuple[SemanticBundleMember, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticBundleGeneration:
    bundles: tuple[SemanticBundle, ...]
    incomplete: tuple[IncompleteSemanticBundle, ...]


def _require_unique_rules(rules: tuple[SemanticBundleRule, ...]) -> None:
    if len({rule.rule_id for rule in rules}) != len(rules):
        raise ValueError("semantic bundle rule IDs must be unique")
    identities = [SemanticBundle.from_rule(rule).identity for rule in rules]
    if len(set(identities)) != len(identities):
        raise ValueError("semantic bundle rule identities must be unique")


def _partition_members(
    rule: SemanticBundleRule, constituents: set[Constituent]
) -> tuple[tuple[SemanticBundleMember, ...], tuple[SemanticBundleMember, ...]]:
    present = tuple(member for member in rule.members if member.pair in constituents)
    missing = tuple(
        member for member in rule.members if member.pair not in constituents
    )
    return present, missing


def _incomplete_bundle(
    rule: SemanticBundleRule,
    present: tuple[SemanticBundleMember, ...],
    missing: tuple[SemanticBundleMember, ...],
) -> IncompleteSemanticBundle:
    return IncompleteSemanticBundle(
        rule_id=rule.rule_id,
        subject_code=rule.subject_code,
        kind=rule.kind,
        name=rule.name,
        qualifiers=rule.qualifiers,
        evidence_ids=rule.evidence_ids,
        present_members=present,
        missing_members=missing,
    )


def generate_semantic_bundles(
    subject_code: str,
    constituents: set[Constituent],
    rules: tuple[SemanticBundleRule, ...],
) -> SemanticBundleGeneration:
    """Generate complete bundles without inferring or inventing absent members."""
    _require_match(subject_code, _CONCEPT_CODE, "subject_code")
    _require_unique_rules(rules)
    bundles: list[SemanticBundle] = []
    incomplete: list[IncompleteSemanticBundle] = []
    for rule in rules:
        if rule.subject_code != subject_code:
            continue
        present, missing = _partition_members(rule, constituents)
        if not missing:
            bundles.append(SemanticBundle.from_rule(rule))
            continue
        incomplete.append(_incomplete_bundle(rule, present, missing))
    return SemanticBundleGeneration(
        bundles=tuple(bundles),
        incomplete=tuple(incomplete),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticMetricScore:
    expected: int
    actual: int
    true_positive: int
    missing: frozenset[str]
    extra: frozenset[str]

    @property
    def precision(self) -> float:
        return self.true_positive / self.actual if self.actual else 1.0

    @property
    def recall(self) -> float:
        return self.true_positive / self.expected if self.expected else 1.0

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        return (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )

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


def _contextual_member_id(context: ContextKey, member: SemanticBundleMember) -> str:
    return _stable_identity(("contextual-member-v1", context, member.semantic_key))


def _association_ids(bundle: SemanticBundle) -> set[str]:
    return {
        _stable_identity(
            (
                "semantic-association-v1",
                bundle.context_key,
                tuple(sorted((left.semantic_key, right.semantic_key))),
            )
        )
        for left, right in combinations(bundle.members, 2)
    }


def _require_unique_bundles(
    bundles: tuple[SemanticBundle, ...], label: str
) -> set[str]:
    identities = {bundle.identity for bundle in bundles}
    if len(identities) != len(bundles):
        raise ValueError(f"{label} bundles must have unique semantic identities")
    return identities


def _contextual_member_ids(bundles: tuple[SemanticBundle, ...]) -> set[str]:
    return {
        _contextual_member_id(bundle.context_key, member)
        for bundle in bundles
        for member in bundle.members
    }


def _all_association_ids(bundles: tuple[SemanticBundle, ...]) -> set[str]:
    return {
        association for bundle in bundles for association in _association_ids(bundle)
    }


def score_semantic_bundles(
    expected: tuple[SemanticBundle, ...], actual: tuple[SemanticBundle, ...]
) -> SemanticBundleScore:
    """Score whole bundles, contextual members, and within-bundle associations."""
    expected_bundles = _require_unique_bundles(expected, "expected")
    actual_bundles = _require_unique_bundles(actual, "actual")
    return SemanticBundleScore(
        exact_bundle=_score_identities(expected_bundles, actual_bundles),
        contextual_member=_score_identities(
            _contextual_member_ids(expected),
            _contextual_member_ids(actual),
        ),
        association=_score_identities(
            _all_association_ids(expected),
            _all_association_ids(actual),
        ),
    )
