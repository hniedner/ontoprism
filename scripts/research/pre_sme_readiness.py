"""Identity-bound machine checks with local evidence writes before M1.6 SME review.

The writes are local report artifacts only; this module performs no ontology, store,
authorization, or publication writes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Self

import rdflib
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rdflib.store import Store
from scripts.research.current_evidence import (
    CurrentComparison,
    CurrentEngineEvidence,
    CurrentMetrics,
    validate_current_comparison,
)
from scripts.research.golden_review import load_row_decisions
from scripts.research.group_review_packet import load_group_review_packet

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import vocab
from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS
from ontolib.decomposition.evaluation import (
    M1_6_METRIC_CONTRACTS,
    EvaluationMetricName,
    MetricDenominatorRule,
)
from ontolib.decomposition.normalized_group_policy import (
    ActiveNormalizedGroupPolicy,
    load_normalized_group_policy,
    load_packaged_normalized_group_policy,
)
from ontolib.decomposition.proposal_registry_migration import (
    load_proposal_registry_migration_envelope,
    validate_migrated_proposal_registry,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.r101_run_conservation import R101ConservationCounts
from ontolib.terminologies.ncit.sibling_store import (
    SiblingStoreValidationError,
    validate_ncit_sibling_manifest,
)

_SHA256 = r"^[0-9a-f]{64}$"
_GIT_SHA = r"^[0-9a-f]{40}$"
_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
_GROUP_REVIEW_COUNT = 18
_GROUP_REVIEW = "group-review"
_FINAL_ACCEPTANCE = "final-full-corpus-scientific-acceptance-and-publication"
_ACCEPTED_COHORT_COUNT = 20


class PreSmeValidationError(ValueError):
    """Machine evidence cannot support a pre-SME readiness report."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def _canonical_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    ).encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir():
        raise PreSmeValidationError(f"output parent does not exist: {path.parent}")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staging = Path(name)
    primary: BaseException | None = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError as cleanup:
            if primary is None:
                raise
            primary.add_note(f"cleanup failure: {cleanup}")


class PrimarySiteObservation(_StrictModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    filler_code: str = Field(pattern=r"^C[0-9]+$")


class PrimarySiteCardinalityViolation(_StrictModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    filler_codes: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _validate_fillers(self) -> Self:
        if self.filler_codes != tuple(sorted(set(self.filler_codes))):
            raise ValueError(
                "primary-site violation fillers must be canonical and unique"
            )
        return self


def _primary_site_cardinality_violations(
    observations: tuple[PrimarySiteObservation, ...] | list[PrimarySiteObservation],
) -> tuple[PrimarySiteCardinalityViolation, ...]:
    by_concept: dict[str, set[str]] = {}
    for observation in observations:
        by_concept.setdefault(observation.concept_code, set()).add(
            observation.filler_code
        )
    return tuple(
        PrimarySiteCardinalityViolation(
            concept_code=concept_code,
            filler_codes=tuple(sorted(fillers)),
        )
        for concept_code, fillers in sorted(by_concept.items())
        if len(fillers) > 1
    )


class PrimarySiteAudit(_StrictModel):
    schema_version: Literal[3]
    source_identity: str = Field(pattern=_SHA256)
    source_release: str
    corpus_artifact_identity: str = Field(pattern=_SHA256)
    resolved_sites: tuple[PrimarySiteObservation, ...]
    review_required_sites: tuple[PrimarySiteObservation, ...]
    cardinality_violations: tuple[PrimarySiteCardinalityViolation, ...]
    resolved_site_count: int = Field(ge=0)
    review_required_site_count: int = Field(ge=0)
    parser_passes: Literal[1]
    audit_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_audit(self) -> Self:
        if self.resolved_site_count != len(self.resolved_sites):
            raise ValueError("resolved site count differs")
        if self.review_required_site_count != len(self.review_required_sites):
            raise ValueError("review-required site count differs")
        if self.resolved_site_count + self.review_required_site_count == 0:
            raise ValueError("primary-site audit requires at least one observation")
        observations = [
            (item.concept_code, item.filler_code)
            for item in (*self.resolved_sites, *self.review_required_sites)
        ]
        if len(observations) != len(set(observations)):
            raise ValueError(
                "resolved and review observations must be pairwise distinct"
            )
        expected_violations = _primary_site_cardinality_violations(self.resolved_sites)
        if self.cardinality_violations != expected_violations:
            raise ValueError(
                "primary-site cardinality violations differ from observations"
            )
        expected = _identity(self.model_dump(mode="json", exclude={"audit_identity"}))
        if self.audit_identity != expected:
            raise ValueError("primary-site audit identity differs")
        return self


class _PrimarySiteStore(Store):
    context_aware = False
    formula_aware = False
    transaction_aware = False

    def __init__(self) -> None:
        super().__init__()
        self._parts: dict[rdflib.BNode, dict[str, object]] = {}
        self.resolved: list[PrimarySiteObservation] = []
        self.review: list[PrimarySiteObservation] = []
        self._seen: set[tuple[str, str, bool]] = set()

    @property
    def has_unbound_parts(self) -> bool:
        """Whether parsed constituent properties were never bound to a subject."""
        return bool(self._parts)

    def add(
        self,
        triple: tuple[rdflib.Node, rdflib.Node, rdflib.Node],
        context: rdflib.Graph,
        quoted: bool = False,
    ) -> None:
        del context, quoted
        subject, predicate, value = triple
        if isinstance(subject, rdflib.BNode):
            self._add_part(subject, predicate, value)
            return
        if predicate != rdflib.URIRef(vocab.HAS_CONSTITUENT):
            return
        self._add_constituent(subject, value)

    def _add_part(
        self, subject: rdflib.BNode, predicate: rdflib.Node, value: rdflib.Node
    ) -> None:
        part = self._parts.setdefault(subject, {})
        if predicate == rdflib.URIRef(vocab.AXIS):
            if "axis" in part:
                raise PreSmeValidationError("duplicate primary-site axis data")
            part["axis"] = value
        elif predicate == rdflib.URIRef(vocab.FILLER):
            if "filler" in part:
                raise PreSmeValidationError("duplicate primary-site filler data")
            part["filler"] = value
        elif predicate == rdflib.URIRef(vocab.NEEDS_REVIEW):
            if "review" in part:
                raise PreSmeValidationError("duplicate needs-review data")
            part["review"] = value

    def _add_constituent(  # noqa: C901 - validates the complete observation
        self, subject: rdflib.Node, value: rdflib.Node
    ) -> None:
        if not isinstance(value, rdflib.BNode):
            raise PreSmeValidationError("constituent is not a Turtle blank node")
        part = self._parts.pop(value, None)
        if not part:
            raise PreSmeValidationError("reused constituent blank node has no data")
        if "axis" not in part:
            raise PreSmeValidationError("primary-site axis is missing")
        if not isinstance(part["axis"], rdflib.URIRef):
            raise PreSmeValidationError("primary-site axis is not an IRI")
        if part.get("axis") != rdflib.URIRef(f"{vocab.ONTOPRISM_NS}PrimarySite"):
            return
        filler = part.get("filler")
        if not isinstance(subject, rdflib.URIRef) or not str(subject).startswith(_NCIT):
            raise PreSmeValidationError("primary-site subject is not an NCIt concept")
        if not isinstance(filler, rdflib.URIRef) or not str(filler).startswith(_NCIT):
            raise PreSmeValidationError("primary-site filler is not an NCIt concept")
        concept_code = str(subject).removeprefix(_NCIT)
        filler_code = str(filler).removeprefix(_NCIT)
        review_value = part.get("review")
        if review_value is None:
            review = False
        elif review_value == rdflib.Literal(True):
            review = True
        else:
            raise PreSmeValidationError("needsReview must be the boolean true")
        key = (concept_code, filler_code, review)
        if key in self._seen:
            raise PreSmeValidationError("duplicate primary-site constituent data")
        self._seen.add(key)
        observation = PrimarySiteObservation(
            concept_code=concept_code, filler_code=filler_code
        )
        if review:
            self.review.append(observation)
            return
        self.resolved.append(observation)


def _parse_primary_sites(artifact: Path) -> tuple[str, _PrimarySiteStore]:
    if not artifact.is_file():
        raise PreSmeValidationError(f"corpus artifact does not exist: {artifact}")
    store = _PrimarySiteStore()
    graph = rdflib.Graph(store=store)
    try:
        payload = artifact.read_bytes()
        artifact_identity = hashlib.sha256(payload).hexdigest()
        graph.parse(data=payload, format="turtle")
    except PreSmeValidationError:
        raise
    except Exception as exc:
        raise PreSmeValidationError(
            f"corpus artifact is malformed Turtle: {exc}"
        ) from exc
    if store.has_unbound_parts:
        raise PreSmeValidationError(
            "unbound constituent data remains after Turtle parse"
        )
    return artifact_identity, store


def audit_primary_site_artifact(
    *,
    artifact: Path,
    source_identity: str,
    source_release: str,
    output: Path | None = None,
) -> PrimarySiteAudit:
    """Audit every primary-site observation in one parser pass."""
    artifact_identity, store = _parse_primary_sites(artifact)
    payload: dict[str, object] = {
        "schema_version": 3,
        "source_identity": source_identity,
        "source_release": source_release,
        "corpus_artifact_identity": artifact_identity,
        "resolved_sites": tuple(store.resolved),
        "review_required_sites": tuple(store.review),
        "cardinality_violations": _primary_site_cardinality_violations(store.resolved),
        "resolved_site_count": len(store.resolved),
        "review_required_site_count": len(store.review),
        "parser_passes": 1,
    }
    audit = PrimarySiteAudit.model_validate(
        {**payload, "audit_identity": _identity(_jsonable(payload))}
    )
    if output is not None:
        _atomic_write(output, _canonical_bytes(audit))
    return audit


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def generate_primary_site_audit(
    *, source_manifest: Path, artifact: Path, output: Path
) -> PrimarySiteAudit:
    try:
        manifest = validate_ncit_sibling_manifest(source_manifest)
        return audit_primary_site_artifact(
            artifact=artifact,
            source_identity=manifest.source_identity,
            source_release=manifest.ontology_version,
            output=output,
        )
    except PreSmeValidationError:
        raise
    except Exception as exc:
        raise PreSmeValidationError(str(exc)) from exc


class ValidatedFraction(_StrictModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    value: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _validate_fraction(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("fraction numerator exceeds denominator")
        if self.value != self.numerator / self.denominator:
            raise ValueError("fraction value differs from counts")
        return self


class CommonValidatedFraction(ValidatedFraction):
    ineligible: int = Field(ge=0)


class MachineReadinessInputs(_StrictModel):
    source_identity: str = Field(pattern=_SHA256)
    source_manifest_identity: str = Field(pattern=_SHA256)
    current_evidence_identity: str = Field(pattern=_SHA256)
    current_comparison_identity: str = Field(pattern=_SHA256)
    sample_artifact_identity: str = Field(pattern=_SHA256)
    corpus_artifact_identity: str = Field(pattern=_SHA256)
    proposal_registry_identity: str = Field(pattern=_SHA256)
    proposal_registry_migration_identity: str = Field(pattern=_SHA256)
    row_decisions_identity: str = Field(pattern=_SHA256)
    primary_site_audit_identity: str = Field(pattern=_SHA256)
    primary_site_resolved_count: int = Field(ge=0)
    primary_site_review_required_count: int = Field(ge=0)
    primary_site_cardinality_violations: tuple[PrimarySiteCardinalityViolation, ...]
    group_packet_identity: str = Field(pattern=_SHA256)
    normalized_group_policy_identity: str = Field(pattern=_SHA256)
    grouping_detector_report_identity: str = Field(pattern=_SHA256)
    axis_contract_violations: tuple[str, ...]
    normalized_group_violations: tuple[str, ...]
    unadjudicated_golden_changes: tuple[str, ...]
    r101_run_id: str = Field(min_length=1)
    r101_unresolved_count: int = Field(ge=0)
    verify_evidence_identity: str = Field(pattern=_SHA256)
    git_head: str = Field(pattern=_GIT_SHA)
    exact_pair_true_positive: int = Field(ge=0)
    exact_pair_emitted: int = Field(gt=0)
    exact_pair_expected: int = Field(gt=0)
    historical_sme_include_count: int = Field(ge=0)
    historical_engine_suggestion_count: int = Field(gt=0)
    full_partition_agreement: ValidatedFraction
    common_partition_agreement: CommonValidatedFraction
    group_review_count: Literal[18]

    @model_validator(mode="after")
    def _validate_reuse_status(self) -> Self:
        if (
            self.primary_site_resolved_count + self.primary_site_review_required_count
            == 0
        ):
            raise ValueError("primary-site audit requires at least one observation")
        if (
            self.common_partition_agreement.denominator
            + self.common_partition_agreement.ineligible
            != self.full_partition_agreement.denominator
        ):
            raise ValueError("grouping denominators do not describe one cohort")
        if self.full_partition_agreement.denominator != _ACCEPTED_COHORT_COUNT:
            raise ValueError(
                "full partition agreement must cover the 20-concept cohort"
            )
        if self.exact_pair_true_positive > min(
            self.exact_pair_emitted, self.exact_pair_expected
        ):
            raise ValueError("exact pair true-positive count exceeds a denominator")
        if self.historical_sme_include_count > self.historical_engine_suggestion_count:
            raise ValueError("historical include count exceeds suggestion count")
        violation_concepts = tuple(
            item.concept_code for item in self.primary_site_cardinality_violations
        )
        if violation_concepts != tuple(sorted(set(violation_concepts))):
            raise ValueError("primary-site violations must be canonical and unique")
        if (
            len(self.primary_site_cardinality_violations)
            > self.primary_site_resolved_count
        ):
            raise ValueError("primary-site violations exceed resolved observations")
        for name, violations in (
            ("axis-contract", self.axis_contract_violations),
            ("normalized-group", self.normalized_group_violations),
            ("unadjudicated-golden", self.unadjudicated_golden_changes),
        ):
            if violations != tuple(sorted(set(violations))):
                raise ValueError(f"{name} violations must be canonical and unique")
        return self


class MetricView(_StrictModel):
    name: EvaluationMetricName
    denominator_rule: MetricDenominatorRule
    fraction: ValidatedFraction

    @model_validator(mode="after")
    def _contract_is_canonical(self) -> Self:
        expected = next(
            contract.denominator
            for contract in M1_6_METRIC_CONTRACTS
            if contract.name == self.name
        )
        if self.denominator_rule != expected:
            raise ValueError("metric denominator rule differs from canonical contract")
        return self


class CommonMetricView(_StrictModel):
    name: Literal[EvaluationMetricName.COMMON_PAIR_PARTITION_AGREEMENT]
    denominator_rule: Literal[MetricDenominatorRule.COMMON_PAIRS]
    fraction: CommonValidatedFraction


class ReadinessMetrics(_StrictModel):
    sme_include_rate: MetricView
    exact_pair_precision: MetricView
    exact_pair_recall: MetricView
    full_partition_agreement: MetricView
    common_pair_partition_agreement: CommonMetricView

    @model_validator(mode="after")
    def _views_are_exactly_the_five_contracts(self) -> Self:
        views = (
            self.sme_include_rate,
            self.exact_pair_precision,
            self.exact_pair_recall,
            self.full_partition_agreement,
            self.common_pair_partition_agreement,
        )
        actual = tuple((view.name, view.denominator_rule) for view in views)
        expected = tuple(
            (contract.name, contract.denominator) for contract in M1_6_METRIC_CONTRACTS
        )
        if actual != expected:
            raise ValueError(
                "readiness metrics differ from the five canonical contracts"
            )
        if (
            self.exact_pair_precision.fraction.numerator
            != self.exact_pair_recall.fraction.numerator
        ):
            raise ValueError("exact pair true-positive counts differ")
        common = self.common_pair_partition_agreement.fraction
        if (
            common.denominator + common.ineligible
            != self.full_partition_agreement.fraction.denominator
        ):
            raise ValueError("grouping denominators do not describe one cohort")
        return self


class M16ImprovementGate(_StrictModel):
    status: Literal["passed", "failed"]
    precision_baseline: ValidatedFraction
    recall_baseline: ValidatedFraction
    precision_improved: bool
    recall_improved: bool

    @model_validator(mode="after")
    def _validate_exact_comparisons(self) -> Self:
        if self.precision_baseline != ValidatedFraction(
            numerator=80, denominator=106, value=80 / 106
        ) or self.recall_baseline != ValidatedFraction(
            numerator=80, denominator=153, value=80 / 153
        ):
            raise ValueError("M1.6 baseline fractions differ")
        expected_status = (
            "passed" if self.precision_improved and self.recall_improved else "failed"
        )
        if self.status != expected_status:
            raise ValueError("M1.6 improvement status differs from indicators")
        return self


class QualityTargetIndicators(_StrictModel):
    threshold: ValidatedFraction
    precision_at_least_90_percent: bool
    recall_at_least_90_percent: bool
    meets_quality_target: bool

    @model_validator(mode="after")
    def _validate_conjunction(self) -> Self:
        if self.threshold != ValidatedFraction(numerator=9, denominator=10, value=0.9):
            raise ValueError("quality target threshold differs")
        expected = (
            self.precision_at_least_90_percent and self.recall_at_least_90_percent
        )
        if self.meets_quality_target != expected:
            raise ValueError("quality target conjunction differs")
        return self


class PrimarySiteAuditSummary(_StrictModel):
    audit_identity: str = Field(pattern=_SHA256)
    resolved_site_count: int = Field(ge=0)
    review_required_site_count: int = Field(ge=0)
    cardinality_violations: tuple[PrimarySiteCardinalityViolation, ...]

    @model_validator(mode="after")
    def _validate_nonempty(self) -> Self:
        if self.resolved_site_count + self.review_required_site_count == 0:
            raise ValueError("primary-site audit requires at least one observation")
        concepts = tuple(item.concept_code for item in self.cardinality_violations)
        if concepts != tuple(sorted(set(concepts))):
            raise ValueError("primary-site violations must be canonical and unique")
        if len(self.cardinality_violations) > self.resolved_site_count:
            raise ValueError("primary-site violations exceed resolved observations")
        return self


class PublicationState(_StrictModel):
    status: Literal["not-attempted"]
    publication_writes_performed: Literal[False]


SemanticBlockerKind = Literal[
    "unclassified-delta",
    "axis-contract-violation",
    "normalized-group-violation",
    "unadjudicated-golden-change",
    "primary-site-cardinality",
    "unexplained-r101-loss",
]


class ClearSemanticBlocker(_StrictModel):
    kind: SemanticBlockerKind
    status: Literal["clear"]
    blocker_count: Literal[0]
    evidence: tuple[str, ...] = Field(min_length=1)


class BlockedSemanticBlocker(_StrictModel):
    kind: SemanticBlockerKind
    status: Literal["blocked"]
    blocker_count: int = Field(gt=0)
    evidence: tuple[str, ...] = Field(min_length=1)


class NotEvaluatedSemanticBlocker(_StrictModel):
    kind: SemanticBlockerKind
    status: Literal["not-evaluated"]
    owning_issue: str = Field(pattern=r"^#[0-9]+$")
    reason: str = Field(min_length=1)


SemanticBlocker = Annotated[
    ClearSemanticBlocker | BlockedSemanticBlocker | NotEvaluatedSemanticBlocker,
    Field(discriminator="status"),
]


_SEMANTIC_BLOCKER_KINDS: tuple[SemanticBlockerKind, ...] = (
    "unclassified-delta",
    "axis-contract-violation",
    "normalized-group-violation",
    "unadjudicated-golden-change",
    "primary-site-cardinality",
    "unexplained-r101-loss",
)


class SemanticGateSummary(_StrictModel):
    status: Literal["clear", "blocked", "not-evaluated"]
    entries: tuple[SemanticBlocker, ...]

    @model_validator(mode="after")
    def _validate_complete_taxonomy_and_aggregate(self) -> Self:
        kinds = tuple(entry.kind for entry in self.entries)
        if kinds != _SEMANTIC_BLOCKER_KINDS:
            raise ValueError("semantic blocker taxonomy must be complete and canonical")
        statuses = {entry.status for entry in self.entries}
        expected = (
            "blocked"
            if "blocked" in statuses
            else "not-evaluated"
            if "not-evaluated" in statuses
            else "clear"
        )
        if self.status != expected:
            raise ValueError("semantic gate aggregate differs from blocker statuses")
        return self


RequirementKind = Literal[
    "group-review",
    "final-full-corpus-scientific-acceptance-and-publication",
]


class PendingHumanRequirement(_StrictModel):
    requirement: RequirementKind
    count: int | None
    status: Literal["pending"]

    @model_validator(mode="after")
    def _validate_count(self) -> Self:
        final = self.requirement == _FINAL_ACCEPTANCE
        if final != (self.count is None):
            raise ValueError("human requirement count shape differs")
        if self.count is not None and self.count <= 0:
            raise ValueError("human requirement count must be positive")
        return self


HumanRequirement = PendingHumanRequirement


class ReportIdentities(_StrictModel):
    source_identity: str = Field(pattern=_SHA256)
    source_manifest_identity: str = Field(pattern=_SHA256)
    current_evidence_identity: str = Field(pattern=_SHA256)
    current_comparison_identity: str = Field(pattern=_SHA256)
    sample_artifact_identity: str = Field(pattern=_SHA256)
    corpus_artifact_identity: str = Field(pattern=_SHA256)
    proposal_registry_identity: str = Field(pattern=_SHA256)
    proposal_registry_migration_identity: str = Field(pattern=_SHA256)
    row_decisions_identity: str = Field(pattern=_SHA256)
    primary_site_audit_identity: str = Field(pattern=_SHA256)
    group_packet_identity: str = Field(pattern=_SHA256)
    normalized_group_policy_identity: str = Field(pattern=_SHA256)
    grouping_detector_report_identity: str = Field(pattern=_SHA256)
    verify_evidence_identity: str = Field(pattern=_SHA256)
    git_head: str = Field(pattern=_GIT_SHA)


class MachineReadinessReport(_StrictModel):
    schema_version: Literal[4]
    status: Literal[
        "machine-blocked", "awaiting-later-evaluation", "awaiting-human-review"
    ]
    authorization: Literal[False]
    publication: PublicationState
    identities: ReportIdentities
    metrics: ReadinessMetrics
    m1_6_improvement: M16ImprovementGate
    quality_target: QualityTargetIndicators
    semantic_gate: SemanticGateSummary
    primary_site_audit: PrimarySiteAuditSummary
    human_requirements: tuple[HumanRequirement, ...]
    report_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_identity(  # noqa: C901, PLR0912 - full report checks
        self,
    ) -> Self:
        requirements = [item.requirement for item in self.human_requirements]
        if len(requirements) != len(set(requirements)):
            raise ValueError("human requirements must be unique")
        if set(requirements) != {
            _GROUP_REVIEW,
            _FINAL_ACCEPTANCE,
        }:
            raise ValueError("required human requirements differ")
        by_requirement = {item.requirement: item for item in self.human_requirements}
        if by_requirement[_GROUP_REVIEW].count != _GROUP_REVIEW_COUNT:
            raise ValueError("group review count differs")
        if (
            self.primary_site_audit.audit_identity
            != self.identities.primary_site_audit_identity
        ):
            raise ValueError("primary-site audit summary identity differs")
        precision = self.metrics.exact_pair_precision.fraction
        recall = self.metrics.exact_pair_recall.fraction
        precision_improved = precision.numerator * 106 > 80 * precision.denominator
        recall_improved = recall.numerator * 153 > 80 * recall.denominator
        if (
            self.m1_6_improvement.precision_improved != precision_improved
            or self.m1_6_improvement.recall_improved != recall_improved
        ):
            raise ValueError("M1.6 indicators differ from current metrics")
        precision_target = precision.numerator * 10 >= 9 * precision.denominator
        recall_target = recall.numerator * 10 >= 9 * recall.denominator
        if (
            self.quality_target.precision_at_least_90_percent != precision_target
            or self.quality_target.recall_at_least_90_percent != recall_target
        ):
            raise ValueError("quality target indicators differ from current metrics")
        expected_status = {
            "blocked": "machine-blocked",
            "not-evaluated": "awaiting-later-evaluation",
            "clear": "awaiting-human-review",
        }[self.semantic_gate.status]
        if self.status != expected_status:
            raise ValueError("readiness status differs from semantic gate")
        blockers = {item.kind: item for item in self.semantic_gate.entries}
        primary_site = blockers["primary-site-cardinality"]
        primary_count = len(self.primary_site_audit.cardinality_violations)
        if (
            not isinstance(primary_site, (ClearSemanticBlocker, BlockedSemanticBlocker))
            or primary_site.blocker_count != primary_count
        ):
            raise ValueError("primary-site blocker differs from audit")
        r101_blocker = blockers["unexplained-r101-loss"]
        if isinstance(r101_blocker, NotEvaluatedSemanticBlocker):
            raise ValueError("R101 blocker must be evaluated")
        for kind in (
            "axis-contract-violation",
            "normalized-group-violation",
            "unadjudicated-golden-change",
        ):
            if isinstance(blockers[kind], NotEvaluatedSemanticBlocker):
                raise ValueError(f"{kind} must be evaluated")
        if not isinstance(blockers["unclassified-delta"], NotEvaluatedSemanticBlocker):
            raise ValueError("total delta classification must remain not evaluated")
        expected = _identity(self.model_dump(mode="json", exclude={"report_identity"}))
        if self.report_identity != expected:
            raise ValueError("machine readiness report identity differs")
        return self


def _metric_view(
    name: EvaluationMetricName, fraction: ValidatedFraction
) -> dict[str, object]:
    contract = next(item for item in M1_6_METRIC_CONTRACTS if item.name == name)
    return {
        "name": contract.name,
        "denominator_rule": contract.denominator,
        "fraction": fraction.model_dump(mode="json"),
    }


def _evaluated_blocker(
    kind: SemanticBlockerKind,
    blocker_count: int,
    evidence: tuple[str, ...],
) -> ClearSemanticBlocker | BlockedSemanticBlocker:
    if blocker_count:
        return BlockedSemanticBlocker(
            kind=kind,
            status="blocked",
            blocker_count=blocker_count,
            evidence=evidence,
        )
    return ClearSemanticBlocker(
        kind=kind, status="clear", blocker_count=0, evidence=evidence
    )


def _semantic_gate(inputs: MachineReadinessInputs) -> SemanticGateSummary:
    entries: tuple[SemanticBlocker, ...] = (
        NotEvaluatedSemanticBlocker(
            kind="unclassified-delta",
            status="not-evaluated",
            owning_issue="#127",
            reason="no total full-corpus delta classification is available",
        ),
        _evaluated_blocker(
            "axis-contract-violation",
            len(inputs.axis_contract_violations),
            (f"current-evidence:{inputs.current_evidence_identity}",),
        ),
        _evaluated_blocker(
            "normalized-group-violation",
            len(inputs.normalized_group_violations),
            (f"normalized-group-policy:{inputs.normalized_group_policy_identity}",),
        ),
        _evaluated_blocker(
            "unadjudicated-golden-change",
            len(inputs.unadjudicated_golden_changes),
            (
                f"current-comparison:{inputs.current_comparison_identity}",
                f"normalized-group-policy:{inputs.normalized_group_policy_identity}",
            ),
        ),
        _evaluated_blocker(
            "primary-site-cardinality",
            len(inputs.primary_site_cardinality_violations),
            (f"primary-site-audit:{inputs.primary_site_audit_identity}",),
        ),
        _evaluated_blocker(
            "unexplained-r101-loss",
            inputs.r101_unresolved_count,
            (f"decomposition-run:{inputs.r101_run_id}",),
        ),
    )
    status = (
        "blocked"
        if any(item.status == "blocked" for item in entries)
        else "not-evaluated"
        if any(item.status == "not-evaluated" for item in entries)
        else "clear"
    )
    return SemanticGateSummary(status=status, entries=entries)


def build_machine_readiness(inputs: MachineReadinessInputs) -> MachineReadinessReport:
    # D59 baseline is 80/106 precision and 80/153 recall
    # (`pdm run agent-test ontolib/tests/decomposition/test_m1_baseline.py -v`,
    # 2026-08-26); integer cross-products preserve the strict comparison exactly.
    precision_better = (
        inputs.exact_pair_true_positive * 106 > 80 * inputs.exact_pair_emitted
    )
    recall_better = (
        inputs.exact_pair_true_positive * 153 > 80 * inputs.exact_pair_expected
    )
    precision_target = (
        inputs.exact_pair_true_positive * 10 >= 9 * inputs.exact_pair_emitted
    )
    recall_target = (
        inputs.exact_pair_true_positive * 10 >= 9 * inputs.exact_pair_expected
    )
    requirements = [
        PendingHumanRequirement(
            requirement=_GROUP_REVIEW, count=_GROUP_REVIEW_COUNT, status="pending"
        ),
        PendingHumanRequirement(
            requirement=_FINAL_ACCEPTANCE,
            count=None,
            status="pending",
        ),
    ]
    semantic_gate = _semantic_gate(inputs)
    report_status = {
        "blocked": "machine-blocked",
        "not-evaluated": "awaiting-later-evaluation",
        "clear": "awaiting-human-review",
    }[semantic_gate.status]
    precision = ValidatedFraction(
        numerator=inputs.exact_pair_true_positive,
        denominator=inputs.exact_pair_emitted,
        value=inputs.exact_pair_true_positive / inputs.exact_pair_emitted,
    )
    recall = ValidatedFraction(
        numerator=inputs.exact_pair_true_positive,
        denominator=inputs.exact_pair_expected,
        value=inputs.exact_pair_true_positive / inputs.exact_pair_expected,
    )
    identities = {
        key: str(value)
        for key, value in inputs.model_dump(mode="json").items()
        if key.endswith("identity") or key == "git_head"
    }
    payload: dict[str, object] = {
        "schema_version": 4,
        "status": report_status,
        "authorization": False,
        "publication": {
            "status": "not-attempted",
            "publication_writes_performed": False,
        },
        "identities": identities,
        "metrics": {
            "sme_include_rate": _metric_view(
                EvaluationMetricName.SME_INCLUDE_RATE,
                ValidatedFraction(
                    numerator=inputs.historical_sme_include_count,
                    denominator=inputs.historical_engine_suggestion_count,
                    value=(
                        inputs.historical_sme_include_count
                        / inputs.historical_engine_suggestion_count
                    ),
                ),
            ),
            "exact_pair_precision": _metric_view(
                EvaluationMetricName.EXACT_PAIR_PRECISION, precision
            ),
            "exact_pair_recall": _metric_view(
                EvaluationMetricName.EXACT_PAIR_RECALL, recall
            ),
            "full_partition_agreement": _metric_view(
                EvaluationMetricName.FULL_PARTITION_AGREEMENT,
                inputs.full_partition_agreement,
            ),
            "common_pair_partition_agreement": {
                "name": EvaluationMetricName.COMMON_PAIR_PARTITION_AGREEMENT,
                "denominator_rule": MetricDenominatorRule.COMMON_PAIRS,
                "fraction": inputs.common_partition_agreement.model_dump(mode="json"),
            },
        },
        "m1_6_improvement": {
            "status": "passed" if precision_better and recall_better else "failed",
            "precision_baseline": {
                "numerator": 80,
                "denominator": 106,
                "value": 80 / 106,
            },
            "recall_baseline": {
                "numerator": 80,
                "denominator": 153,
                "value": 80 / 153,
            },
            "precision_improved": precision_better,
            "recall_improved": recall_better,
        },
        "quality_target": {
            "threshold": {"numerator": 9, "denominator": 10, "value": 0.9},
            "precision_at_least_90_percent": precision_target,
            "recall_at_least_90_percent": recall_target,
            "meets_quality_target": precision_target and recall_target,
        },
        "semantic_gate": semantic_gate,
        "primary_site_audit": {
            "audit_identity": inputs.primary_site_audit_identity,
            "resolved_site_count": inputs.primary_site_resolved_count,
            "review_required_site_count": inputs.primary_site_review_required_count,
            "cardinality_violations": inputs.primary_site_cardinality_violations,
        },
        "human_requirements": tuple(requirements),
    }
    return MachineReadinessReport.model_validate(
        {**payload, "report_identity": _identity(_jsonable(payload))}
    )


def _load_json_no_duplicates(path: Path, name: str) -> tuple[object, bytes]:
    if not path.is_file():
        raise PreSmeValidationError(f"{name} does not exist: {path}")
    raw = path.read_bytes()

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise PreSmeValidationError(f"{name} contains duplicate data")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=pairs), raw
    except PreSmeValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreSmeValidationError(f"{name} is malformed JSON") from exc


class VerifyEvidence(_StrictModel):
    schema_version: Literal[1]
    command: Literal["pdm run verify"]
    status: Literal["passed"]
    git_head: str = Field(pattern=_GIT_SHA)
    docker_context: Literal["ontoprism-podman"]
    docker_endpoint: str = Field(pattern=r"^unix:///.+")
    gate_executable: str
    gate_version: str
    gate_command_identity: str = Field(pattern=_SHA256)
    observed_exit_code: Literal[0]
    publication_writes_performed: Literal[False]
    evidence_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        expected_command = _identity(
            {
                "argv": [self.gate_executable, "run", "verify"],
                "version": self.gate_version,
            }
        )
        if self.gate_command_identity != expected_command:
            raise ValueError("verify gate command identity differs")
        expected = _identity(
            self.model_dump(mode="json", exclude={"evidence_identity"})
        )
        if self.evidence_identity != expected:
            raise ValueError("verify evidence identity differs")
        return self


def require_current_verify_evidence(evidence_head: str, current_head: str) -> None:
    if evidence_head != current_head:
        raise PreSmeValidationError("verify evidence does not bind current git HEAD")


def _validated_current_metrics(comparison: CurrentComparison) -> CurrentMetrics:
    try:
        return CurrentMetrics.model_validate(comparison.metrics.model_dump())
    except ValidationError as exc:
        raise PreSmeValidationError(str(exc)) from exc


def _issue_274_semantic_violations(
    evidence: CurrentEngineEvidence,
    comparison: CurrentComparison,
    policy: ActiveNormalizedGroupPolicy,
    group_packet_identity: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    axis_violations = tuple(
        sorted(
            f"{concept.code}:{constituent.axis}"
            for concept in evidence.concepts
            for constituent in concept.constituents
            if constituent.axis not in AXIS_CONTRACTS
        )
    )
    concepts = {concept.code: concept for concept in evidence.concepts}
    group_violations: list[str] = []
    for row in policy.rows:
        concept = concepts.get(row.concept_code)
        if concept is None:
            group_violations.append(f"{row.concept_code}:missing-current-concept")
            continue
        actual_rows = [
            (
                (item.axis, item.filler),
                item.normalized_group_id,
                item.normalized_group_label,
            )
            for item in concept.constituents
        ]
        if len(actual_rows) != len({item[0] for item in actual_rows}):
            group_violations.append(f"{row.concept_code}:duplicate-current-pair")
            continue
        actual = {item[0]: item[1:] for item in actual_rows}
        expected = {
            pair: (block.normalized_group_id, block.normalized_group_label)
            for block in row.blocks
            for pair in block.pairs
        }
        if actual != expected:
            group_violations.append(f"{row.concept_code}:normalized-group-mismatch")
    golden_violations = tuple(
        label
        for accepted, label in (
            (
                policy.basis_evidence_identity == evidence.evidence_identity,
                "policy-evidence-binding",
            ),
            (
                policy.basis_comparison_identity == comparison.comparison_identity,
                "policy-comparison-binding",
            ),
            (
                policy.basis_packet_identity == group_packet_identity,
                "policy-group-review-binding",
            ),
            (policy.basis_run_id == evidence.run_id, "policy-run-binding"),
            (
                policy.basis_artifact_identity == evidence.artifact_identity,
                "policy-artifact-binding",
            ),
        )
        if not accepted
    )
    return axis_violations, tuple(sorted(group_violations)), golden_violations


class Issue274DetectorReport(_StrictModel):
    schema_version: Literal[1]
    status: Literal["clear", "blocked"]
    current_evidence_identity: str = Field(pattern=_SHA256)
    current_comparison_identity: str = Field(pattern=_SHA256)
    group_packet_identity: str = Field(pattern=_SHA256)
    normalized_group_policy_identity: str = Field(pattern=_SHA256)
    axis_contract_violations: tuple[str, ...]
    normalized_group_violations: tuple[str, ...]
    unadjudicated_golden_changes: tuple[str, ...]
    report_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        violations = (
            *self.axis_contract_violations,
            *self.normalized_group_violations,
            *self.unadjudicated_golden_changes,
        )
        if self.status != ("blocked" if violations else "clear"):
            raise ValueError("Issue #274 detector status differs from violations")
        expected = _identity(self.model_dump(mode="json", exclude={"report_identity"}))
        if self.report_identity != expected:
            raise ValueError("Issue #274 detector report identity differs")
        return self


def generate_issue_274_detector_report(
    *,
    evidence_path: Path,
    comparison_path: Path,
    group_packet_path: Path,
    policy_path: Path,
    output: Path,
) -> Issue274DetectorReport:
    """Write an identity-bound report for the three #274 semantic detectors."""
    try:
        evidence = CurrentEngineEvidence.model_validate_json(evidence_path.read_bytes())
        comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
        validate_current_comparison(evidence, comparison)
        group = load_group_review_packet(group_packet_path)
        policy = load_normalized_group_policy(policy_path)
    except (OSError, ValidationError, ValueError) as exc:
        raise PreSmeValidationError(str(exc)) from exc
    violations = _issue_274_semantic_violations(
        evidence, comparison, policy, group.packet_identity
    )
    payload = {
        "schema_version": 1,
        "status": "blocked" if any(violations) else "clear",
        "current_evidence_identity": evidence.evidence_identity,
        "current_comparison_identity": comparison.comparison_identity,
        "group_packet_identity": group.packet_identity,
        "normalized_group_policy_identity": policy.policy_identity,
        "axis_contract_violations": violations[0],
        "normalized_group_violations": violations[1],
        "unadjudicated_golden_changes": violations[2],
    }
    report = Issue274DetectorReport.model_validate(
        {**payload, "report_identity": _identity(payload)}
    )
    _atomic_write(output, _canonical_bytes(report))
    return report


def _require_matching_issue_274_detector(
    detector: Issue274DetectorReport,
    violations: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
) -> None:
    detector_violations = (
        detector.axis_contract_violations,
        detector.normalized_group_violations,
        detector.unadjudicated_golden_changes,
    )
    if detector_violations != violations:
        raise PreSmeValidationError("Issue #274 detector violations differ")


def _configured_r101_counts(run_id: str) -> R101ConservationCounts:
    async def load() -> R101ConservationCounts:
        engine = make_engine(get_settings().database_url)
        try:
            return await ProvenanceStore(
                make_sessionmaker(engine)
            ).r101_conservation_counts(run_id)
        finally:
            await dispose_engine(engine)

    return asyncio.run(load())


def generate_pre_sme_readiness(
    *,
    source_manifest: Path,
    current_evidence: Path,
    current_comparison: Path,
    corpus_artifact: Path,
    proposal_registry: Path,
    proposal_registry_migration: Path,
    row_decisions: Path,
    primary_site_audit: Path,
    group_packet: Path,
    grouping_detector: Path,
    verify_evidence: Path,
    expected_git_head: str,
    output: Path,
) -> MachineReadinessReport:
    """Validate every machine input before atomically writing a pending-human report."""
    if not source_manifest.is_file():
        raise PreSmeValidationError(
            f"source manifest does not exist: {source_manifest}"
        )
    try:
        manifest = validate_ncit_sibling_manifest(source_manifest)
        manifest_value, _manifest_raw = _load_json_no_duplicates(
            source_manifest, "source manifest"
        )
        manifest_identity = _identity(manifest_value)
        evidence = CurrentEngineEvidence.model_validate_json(
            _load_json_no_duplicates(current_evidence, "current evidence")[1]
        )
        comparison = CurrentComparison.model_validate_json(
            _load_json_no_duplicates(current_comparison, "current comparison")[1]
        )
        validate_current_comparison(evidence, comparison)
        artifact_identity = hashlib.sha256(corpus_artifact.read_bytes()).hexdigest()
        migration = load_proposal_registry_migration_envelope(
            proposal_registry_migration
        )
        proposals = validate_migrated_proposal_registry(migration, proposal_registry)
        historical_rows = load_row_decisions(row_decisions)
        audit = PrimarySiteAudit.model_validate_json(
            _load_json_no_duplicates(primary_site_audit, "primary-site audit")[1]
        )
        group = load_group_review_packet(group_packet)
        normalized_group_policy = load_packaged_normalized_group_policy()
        issue_274_detector = Issue274DetectorReport.model_validate_json(
            _load_json_no_duplicates(grouping_detector, "Issue #274 grouping detector")[
                1
            ]
        )
        gate = VerifyEvidence.model_validate_json(
            _load_json_no_duplicates(verify_evidence, "verify evidence")[1]
        )
    except PreSmeValidationError:
        raise
    except (OSError, SiblingStoreValidationError, ValidationError, ValueError) as exc:
        raise PreSmeValidationError(str(exc)) from exc
    require_current_verify_evidence(gate.git_head, expected_git_head)
    (
        axis_contract_violations,
        normalized_group_violations,
        unadjudicated_golden_changes,
    ) = _issue_274_semantic_violations(
        evidence,
        comparison,
        normalized_group_policy,
        group.packet_identity,
    )
    _require_matching_issue_274_detector(
        issue_274_detector,
        (
            axis_contract_violations,
            normalized_group_violations,
            unadjudicated_golden_changes,
        ),
    )
    checks = (
        (manifest.source_identity == evidence.source_identity, "sample source"),
        (manifest.source_identity == audit.source_identity, "audit source"),
        (artifact_identity == audit.corpus_artifact_identity, "audit artifact"),
        (
            proposals.registry_identity == evidence.proposal_registry_identity,
            "proposal",
        ),
        (
            historical_rows.payload_identity == evidence.row_decision_identity,
            "row decision",
        ),
        (
            group.current_evidence_identity == evidence.evidence_identity,
            "group evidence",
        ),
        (
            group.current_comparison_identity == comparison.comparison_identity,
            "group comparison",
        ),
        (
            issue_274_detector.status == "clear"
            and issue_274_detector.current_evidence_identity
            == evidence.evidence_identity
            and issue_274_detector.current_comparison_identity
            == comparison.comparison_identity
            and issue_274_detector.group_packet_identity == group.packet_identity
            and issue_274_detector.normalized_group_policy_identity
            == normalized_group_policy.policy_identity,
            "Issue #274 grouping detector",
        ),
    )
    for accepted, name in checks:
        if not accepted:
            raise PreSmeValidationError(f"{name} identity or invariant differs")
    metrics = _validated_current_metrics(comparison)
    r101_conservation = _configured_r101_counts(evidence.run_id)
    historical_tally = historical_rows.cross_tab().engine_suggestion
    if (
        metrics.full_partition_agreement.rate is None
        or metrics.common_pair_partition_agreement.rate is None
    ):
        raise PreSmeValidationError("readiness grouping metrics are uncomputed")
    if len(group.review_rows) != _GROUP_REVIEW_COUNT:
        raise PreSmeValidationError(
            f"group review count differs from {_GROUP_REVIEW_COUNT}"
        )
    try:
        inputs = MachineReadinessInputs(
            source_identity=manifest.source_identity,
            source_manifest_identity=manifest_identity,
            current_evidence_identity=evidence.evidence_identity,
            current_comparison_identity=comparison.comparison_identity,
            sample_artifact_identity=evidence.artifact_identity,
            corpus_artifact_identity=artifact_identity,
            proposal_registry_identity=proposals.registry_identity,
            proposal_registry_migration_identity=migration.envelope_identity,
            row_decisions_identity=historical_rows.payload_identity,
            primary_site_audit_identity=audit.audit_identity,
            primary_site_resolved_count=audit.resolved_site_count,
            primary_site_review_required_count=audit.review_required_site_count,
            primary_site_cardinality_violations=audit.cardinality_violations,
            group_packet_identity=group.packet_identity,
            normalized_group_policy_identity=normalized_group_policy.policy_identity,
            grouping_detector_report_identity=issue_274_detector.report_identity,
            axis_contract_violations=axis_contract_violations,
            normalized_group_violations=normalized_group_violations,
            unadjudicated_golden_changes=unadjudicated_golden_changes,
            r101_run_id=evidence.run_id,
            r101_unresolved_count=r101_conservation.unresolved,
            verify_evidence_identity=gate.evidence_identity,
            git_head=gate.git_head,
            exact_pair_true_positive=metrics.exact_pair_precision.numerator,
            exact_pair_emitted=metrics.exact_pair_precision.denominator,
            exact_pair_expected=metrics.exact_pair_recall.denominator,
            historical_sme_include_count=historical_tally.include,
            historical_engine_suggestion_count=historical_tally.adjudicated,
            full_partition_agreement=ValidatedFraction(
                numerator=metrics.full_partition_agreement.numerator,
                denominator=metrics.full_partition_agreement.denominator,
                value=metrics.full_partition_agreement.rate,
            ),
            common_partition_agreement=CommonValidatedFraction(
                numerator=metrics.common_pair_partition_agreement.numerator,
                denominator=metrics.common_pair_partition_agreement.denominator,
                value=metrics.common_pair_partition_agreement.rate,
                ineligible=metrics.common_pair_partition_agreement.ineligible,
            ),
            group_review_count=_GROUP_REVIEW_COUNT,
        )
    except ValidationError as exc:
        raise PreSmeValidationError(str(exc)) from exc
    readiness = build_machine_readiness(inputs)
    _atomic_write(output, _canonical_bytes(readiness))
    return readiness


def write_verify_evidence(
    path: Path,
    *,
    git_head: str,
    docker_context: str,
    docker_endpoint: str,
    gate_executable: str,
    gate_version: str,
    observed_exit_code: Literal[0],
) -> VerifyEvidence:
    """Write evidence for one successful, clean-worktree verify execution.

    The observed fields are the Git/runtime/gate values supplied by the caller after
    observing the same clean Git HEAD before and after the gate. The payload also makes
    the fixed no-publication assertion ``publication_writes_performed=False``; that
    field is a scoped claim about this local operation, not an independently observed
    value.
    """
    command = "pdm run verify"
    command_identity = _identity(
        {
            "argv": [gate_executable, "run", "verify"],
            "version": gate_version,
        }
    )
    payload = {
        "schema_version": 1,
        "command": command,
        "status": "passed",
        "git_head": git_head,
        "docker_context": docker_context,
        "docker_endpoint": docker_endpoint,
        "gate_executable": gate_executable,
        "gate_version": gate_version,
        "gate_command_identity": command_identity,
        "observed_exit_code": observed_exit_code,
        "publication_writes_performed": False,
    }
    evidence = VerifyEvidence.model_validate(
        {**payload, "evidence_identity": _identity(payload)}
    )
    _atomic_write(path, _canonical_bytes(evidence))
    return evidence
