"""Identity-bound mechanical acceptance for the certified C3262 corpus.

This module can prepare evidence for human authorization.  It cannot authorize or
publish a corpus, and it never mutates PostgreSQL, QLever, or the official NCIt source.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from scripts.research.group_review_packet import (
    load_historical_group_review_packet,
)

from ontolib.decomposition import vocab
from ontolib.decomposition.axis_diagnostics import read_axis_diagnostic_source
from ontolib.decomposition.corpus_baseline import load_corpus_baseline
from ontolib.decomposition.fanout_baseline import (
    load_fanout_baseline,
    rerun_fanout_concept,
)
from ontolib.decomposition.provenance_models import (
    CompletedRunForEvidence,
    PublicationMarkerSnapshot,
)
from ontolib.decomposition.publication import (
    PublicationGraphClient,
    PublicationMarker,
    PublicationValidationError,
    build_replacement_update,
    read_publication_marker,
    staging_graph_iri,
    validate_artifact,
)
from ontolib.decomposition.r101_conservation import (
    NonR101DeltaRow,
    NonR101MetadataDelta,
    R101ConservationReport,
    load_r101_conservation_report,
)
from ontolib.decomposition.scope import enumerate_scope_codes
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.sibling_store import validate_ncit_sibling_manifest

_SHA256 = r"^[0-9a-f]{64}$"
_SHA256_LENGTH = 64
_CODE = r"^C[0-9]+$"
_TARGET_EXCLUSIONS = ("C102870", "C198031", "C27262", "C35756")


class CorpusAcceptanceValidationError(ValueError):
    """Acceptance evidence is incomplete, inconsistent, or not authorized."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


class ExcludedPairChange(_StrictModel):
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=_CODE)
    comparison_direction: Literal[
        "grouping-disputed", "added-to-candidate", "missing-from-candidate"
    ]


class ReviewRequiredEffectiveExclusion(_StrictModel):
    concept_code: str = Field(pattern=_CODE)
    pair_changes: tuple[ExcludedPairChange, ...] = Field(min_length=1)
    source_assertion_identities: tuple[str, ...] = Field(min_length=1)
    evidence_identities: tuple[str, ...] = Field(min_length=1)
    reason: Literal["unresolved-semantic-ambiguity"]
    delta: Literal["removed-from-effective"]
    official_source_preserved: Literal[True]
    human_approval: Literal[False]
    nci_approval: Literal[False]

    @model_validator(mode="after")
    def _canonical_exact_evidence(self) -> Self:
        if self.pair_changes != _canonical_pair_changes(self.pair_changes):
            raise ValueError(
                "review-required pair changes must be canonical and unique"
            )
        _require_canonical_identities(
            "source assertion", self.source_assertion_identities
        )
        _require_canonical_identities("review evidence", self.evidence_identities)
        return self


def _canonical_pair_changes(
    values: tuple[ExcludedPairChange, ...],
) -> tuple[ExcludedPairChange, ...]:
    return tuple(
        sorted(
            set(values),
            key=lambda item: (
                item.axis,
                item.filler_code,
                item.comparison_direction,
            ),
        )
    )


def _require_canonical_identities(label: str, values: tuple[str, ...]) -> None:
    canonical = values == tuple(sorted(set(values)))
    valid_digests = all(
        len(value) == _SHA256_LENGTH and not set(value) - set("0123456789abcdef")
        for value in values
    )
    if not canonical or not valid_digests:
        raise ValueError(f"{label} identities must be canonical SHA-256 values")


def build_review_required_exclusions(
    review_packet_path: Path,
    rationale_path: Path,
) -> tuple[ReviewRequiredEffectiveExclusion, ...]:
    """Derive the four exact exclusions from the validated tracked review evidence."""
    packet = load_historical_group_review_packet(review_packet_path)
    rationale_identity = hashlib.sha256(rationale_path.read_bytes()).hexdigest()
    rows = {row.concept_code: row for row in packet.review_rows}
    if set(_TARGET_EXCLUSIONS) - rows.keys():
        raise CorpusAcceptanceValidationError(
            "review packet lacks an exclusion concept"
        )

    exclusions: list[ReviewRequiredEffectiveExclusion] = []
    for code in _TARGET_EXCLUSIONS:
        row = rows[code]
        changes = [
            ExcludedPairChange(
                axis=axis,
                filler_code=filler,
                comparison_direction="grouping-disputed",
            )
            for axis, filler in row.grouping_diagnosis.affected_pairs
        ]
        changes.extend(
            ExcludedPairChange(
                axis=axis,
                filler_code=filler,
                comparison_direction="added-to-candidate",
            )
            for axis, filler in row.pair_delta.extra_pairs
        )
        changes.extend(
            ExcludedPairChange(
                axis=axis,
                filler_code=filler,
                comparison_direction="missing-from-candidate",
            )
            for axis, filler in row.pair_delta.missing_pairs
        )
        exclusions.append(
            ReviewRequiredEffectiveExclusion(
                concept_code=code,
                pair_changes=tuple(
                    sorted(
                        changes,
                        key=lambda item: (
                            item.axis,
                            item.filler_code,
                            item.comparison_direction,
                        ),
                    )
                ),
                source_assertion_identities=tuple(sorted(row.evidence_row_ids)),
                evidence_identities=tuple(
                    sorted(
                        (row.row_identity, packet.packet_identity, rationale_identity)
                    )
                ),
                reason="unresolved-semantic-ambiguity",
                delta="removed-from-effective",
                official_source_preserved=True,
                human_approval=False,
                nci_approval=False,
            )
        )
    return tuple(exclusions)


StructuralCategory = Literal[
    "review-required-effective-exclusion",
    "named-source-preserving-policy-transformation",
    "r101-occurrence-linked-output-delta",
    "proposal-minted-projection",
    "conservative-review-escalation",
    "semantic-routing-change",
    "unexplained-blocker",
]
MetadataCategory = Literal[
    "review-required-effective-exclusion",
    "provenance-evidence-binding-refresh",
    "group-identity-rebinding",
    "conservative-review-escalation",
    "authority-required-review-clearance",
    "semantic-routing-change",
    "compound-metadata-change",
]


class StructuralChangeClassification(_StrictModel):
    object_kind: Literal["structural"]
    category: StructuralCategory
    row: NonR101DeltaRow
    evidence_identities: tuple[str, ...] = ()


class MetadataChangeClassification(_StrictModel):
    object_kind: Literal["metadata"]
    category: MetadataCategory
    delta: NonR101MetadataDelta
    evidence_identities: tuple[str, ...] = ()


ChangedObjectClassification = Annotated[
    StructuralChangeClassification | MetadataChangeClassification,
    Field(discriminator="object_kind"),
]


def _classification_object_key(item: ChangedObjectClassification) -> bytes:
    payload = (
        item.row if isinstance(item, StructuralChangeClassification) else item.delta
    )
    return json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class CorpusDeltaClassification(_StrictModel):
    schema_version: Literal[1]
    old_run_id: str
    new_run_id: str
    structural_object_count: int = Field(ge=0)
    metadata_object_count: int = Field(ge=0)
    raw_row_count: int = Field(ge=0)
    classifications: tuple[ChangedObjectClassification, ...]
    category_counts: dict[str, int]
    unexplained_blockers: tuple[str, ...]
    causal_attribution: Literal["prohibited"]
    classification_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _total_partition_and_identity(self) -> Self:
        _validate_classification_partition(self)
        _validate_classification_summary(self)
        expected = _identity(
            self.model_dump(mode="json", exclude={"classification_identity"})
        )
        if self.classification_identity != expected:
            raise ValueError("classification identity differs")
        return self


def _validate_classification_partition(value: CorpusDeltaClassification) -> None:
    expected_objects = value.structural_object_count + value.metadata_object_count
    keys = tuple(_classification_object_key(item) for item in value.classifications)
    if len(value.classifications) != expected_objects or len(set(keys)) != len(keys):
        raise ValueError("every changed object must occur exactly once")
    expected_raw = value.structural_object_count + 2 * value.metadata_object_count
    if value.raw_row_count != expected_raw:
        raise ValueError("raw changed-row arithmetic differs")


def _validate_classification_summary(value: CorpusDeltaClassification) -> None:
    keys = tuple(_classification_object_key(item) for item in value.classifications)
    counts = dict(
        sorted(Counter(item.category for item in value.classifications).items())
    )
    if value.category_counts != counts:
        raise ValueError("classification category counts differ")
    blockers = tuple(
        _identity(json.loads(key))
        for item, key in zip(value.classifications, keys, strict=True)
        if item.category == "unexplained-blocker"
    )
    if value.unexplained_blockers != blockers:
        raise ValueError("unexplained blocker inventory differs")


def _effective_exclusion_keys(
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
) -> tuple[set[tuple[str, str, str]], dict[str, tuple[str, ...]]]:
    keys: set[tuple[str, str, str]] = set()
    evidence: dict[str, tuple[str, ...]] = {}
    for exclusion in exclusions:
        evidence[exclusion.concept_code] = exclusion.evidence_identities
        for pair in exclusion.pair_changes:
            if pair.comparison_direction in {"grouping-disputed", "added-to-candidate"}:
                keys.add((exclusion.concept_code, pair.axis, pair.filler_code))
    return keys, evidence


def _metadata_category(delta: NonR101MetadataDelta) -> MetadataCategory:
    fields = set(delta.changed_fields)
    if len(fields) > 1:
        return "compound-metadata-change"
    field = next(iter(fields))
    if field == "needs_review":
        return (
            "conservative-review-escalation"
            if delta.new.needs_review
            else "authority-required-review-clearance"
        )
    categories: dict[str, MetadataCategory] = {
        "axis_ambiguity_group_id": "group-identity-rebinding",
        "source_definition_ids": "provenance-evidence-binding-refresh",
        "source_occurrence_ids": "provenance-evidence-binding-refresh",
        "axis_source": "semantic-routing-change",
        "source_roles": "semantic-routing-change",
        "most_specific": "semantic-routing-change",
    }
    return categories[field]


def classify_corpus_delta(
    report: R101ConservationReport,
    *,
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
) -> CorpusDeltaClassification:
    """Classify every typed comparator object without inferring R101 causation."""
    evidence = report.non_r101_delta_evidence
    exclusion_keys, exclusion_evidence = _effective_exclusion_keys(exclusions)
    classifications = _classify_structural_rows(
        evidence.rows, exclusion_keys, exclusion_evidence
    )
    classifications.extend(
        StructuralChangeClassification(
            object_kind="structural",
            category="r101-occurrence-linked-output-delta",
            row=item.row,
            evidence_identities=item.r101_occurrence_ids,
        )
        for item in evidence.classified_rows
    )
    classifications.extend(
        _classify_metadata_rows(
            evidence.metadata_deltas, exclusion_keys, exclusion_evidence
        )
    )
    classifications.sort(
        key=lambda item: (item.object_kind, _classification_object_key(item))
    )
    counts = dict(sorted(Counter(item.category for item in classifications).items()))
    blockers = tuple(
        _identity(json.loads(_classification_object_key(item)))
        for item in classifications
        if item.category == "unexplained-blocker"
    )
    payload = {
        "schema_version": 1,
        "old_run_id": evidence.old_run_id,
        "new_run_id": evidence.new_run_id,
        "structural_object_count": len(evidence.rows) + len(evidence.classified_rows),
        "metadata_object_count": len(evidence.metadata_deltas),
        "raw_row_count": evidence.raw_typed_delta_count,
        "classifications": tuple(classifications),
        "category_counts": counts,
        "unexplained_blockers": blockers,
        "causal_attribution": "prohibited",
    }
    return CorpusDeltaClassification.model_validate(
        {**payload, "classification_identity": _identity(_jsonable(payload))}
    )


def _classify_structural_rows(
    rows: tuple[NonR101DeltaRow, ...],
    exclusion_keys: set[tuple[str, str, str]],
    exclusion_evidence: dict[str, tuple[str, ...]],
) -> list[ChangedObjectClassification]:
    return [
        StructuralChangeClassification(
            object_kind="structural",
            category=_structural_category(row, exclusion_keys),
            row=row,
            evidence_identities=exclusion_evidence.get(row.concept_code, ()),
        )
        for row in rows
    ]


def _structural_category(
    row: NonR101DeltaRow,
    exclusion_keys: set[tuple[str, str, str]],
) -> StructuralCategory:
    key = (row.concept_code, row.axis, row.filler_code)
    if key in exclusion_keys:
        return "review-required-effective-exclusion"
    if row.filler_code.startswith("MINT-"):
        return "proposal-minted-projection"
    if row.needs_review:
        return "conservative-review-escalation"
    return "unexplained-blocker"


def _classify_metadata_rows(
    deltas: tuple[NonR101MetadataDelta, ...],
    exclusion_keys: set[tuple[str, str, str]],
    exclusion_evidence: dict[str, tuple[str, ...]],
) -> list[ChangedObjectClassification]:
    return [
        MetadataChangeClassification(
            object_kind="metadata",
            category=_classified_metadata_category(delta, exclusion_keys),
            delta=delta,
            evidence_identities=exclusion_evidence.get(delta.new.concept_code, ()),
        )
        for delta in deltas
    ]


def _classified_metadata_category(
    delta: NonR101MetadataDelta,
    exclusion_keys: set[tuple[str, str, str]],
) -> MetadataCategory:
    key = (delta.new.concept_code, delta.new.axis, delta.new.filler_code)
    if key in exclusion_keys:
        return "review-required-effective-exclusion"
    return _metadata_category(delta)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


class PublicationDryRunEvidence(_StrictModel):
    schema_version: Literal[1]
    status: Literal["passed", "blocked"]
    candidate_content_identity: str = Field(pattern=_SHA256)
    predecessor_marker_identity: str = Field(pattern=_SHA256)
    destination_graph_iri: str = Field(min_length=1)
    artifact_identity: str = Field(pattern=_SHA256)
    run_id: str = Field(min_length=1)
    expected_concept_count: int = Field(ge=0)
    represented_concept_count: int = Field(ge=0)
    marker_protocol_identity: str = Field(pattern=_SHA256)
    recovery_identity: str = Field(pattern=_SHA256)
    postgres_read_verified: bool
    qlever_read_verified: bool
    publication_writes_performed: Literal[False]
    evidence_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_and_status(self) -> Self:
        passed = self.postgres_read_verified and self.qlever_read_verified
        if self.status != ("passed" if passed else "blocked"):
            raise ValueError("publication dry-run status differs")
        expected = _identity(
            self.model_dump(mode="json", exclude={"evidence_identity"})
        )
        if self.evidence_identity != expected:
            raise ValueError("publication dry-run identity differs")
        return self


class PublicationReadStore(Protocol):
    async def completed_run_for_evidence(
        self, run_id: str
    ) -> CompletedRunForEvidence: ...


async def dry_run_corpus_publication(
    *,
    candidate_content_identity: str,
    run_id: str,
    source_identity: str,
    representation_identity: str,
    artifact: Path,
    destination_graph_iri: str,
    expected_codes: tuple[str, ...],
    expected_worklist_count: int,
    graph: PublicationGraphClient,
    provenance: PublicationReadStore,
) -> PublicationDryRunEvidence:
    """Read both publication boundaries and validate the exact intent without writes."""
    if destination_graph_iri != vocab.DECOMPOSED_GRAPH_IRI:
        raise CorpusAcceptanceValidationError("publication destination differs")
    run = await provenance.completed_run_for_evidence(run_id)
    _require_publication_run_binding(
        run,
        source_identity=source_identity,
        representation_identity=representation_identity,
        expected_worklist_count=expected_worklist_count,
    )
    try:
        artifact_identity = validate_artifact(
            artifact, expected_codes=expected_codes, run_id=run_id
        )
    except PublicationValidationError as exc:
        raise CorpusAcceptanceValidationError(str(exc)) from exc
    if artifact_identity != representation_identity:
        raise CorpusAcceptanceValidationError(
            "artifact representation identity differs"
        )
    predecessor = await read_publication_marker(graph)
    predecessor_payload = (
        predecessor.model_dump(mode="json")
        if predecessor is not None
        else {"marker": None}
    )
    predecessor_identity = _identity(predecessor_payload)
    marker = PublicationMarkerSnapshot(
        run_id=run_id,
        source_identity=source_identity,
        representation_identity=representation_identity,
        built_at=run.fingerprint.emitted_at,
    )
    # Building the exact replacement statement proves that the destination/staging
    # protocol is representable; it is deliberately not sent to the graph client.
    update = build_replacement_update(
        PublicationMarker.model_validate(marker.model_dump()), staging_graph_iri(run_id)
    )
    payload = {
        "schema_version": 1,
        "status": "passed",
        "candidate_content_identity": candidate_content_identity,
        "predecessor_marker_identity": predecessor_identity,
        "destination_graph_iri": destination_graph_iri,
        "artifact_identity": artifact_identity,
        "run_id": run_id,
        "expected_concept_count": expected_worklist_count,
        "represented_concept_count": len(expected_codes),
        "marker_protocol_identity": _identity(update),
        "recovery_identity": _identity(
            {
                "predecessor": predecessor_payload,
                "destination": destination_graph_iri,
                "staging": staging_graph_iri(run_id),
            }
        ),
        "postgres_read_verified": True,
        "qlever_read_verified": True,
        "publication_writes_performed": False,
    }
    return PublicationDryRunEvidence.model_validate(
        {**payload, "evidence_identity": _identity(payload)}
    )


def _require_publication_run_binding(
    run: CompletedRunForEvidence,
    *,
    source_identity: str,
    representation_identity: str,
    expected_worklist_count: int,
) -> None:
    same_source = run.fingerprint.source_identity == source_identity
    same_representation = run.representation_identity == representation_identity
    same_worklist_count = len(run.fingerprint.worklist) == expected_worklist_count
    if not (same_source and same_representation and same_worklist_count):
        raise CorpusAcceptanceValidationError("persisted run binding differs")


class PendingHumanAcceptanceDecision(_StrictModel):
    status: Literal["not-requested"]
    candidate_identity: str = Field(pattern=_SHA256)
    publication_dry_run_identity: str = Field(pattern=_SHA256)


class NotRequestedHumanAuthorization(_StrictModel):
    status: Literal["not-requested"]


class AcceptedHumanAcceptanceDecision(_StrictModel):
    status: Literal["accepted"]
    candidate_identity: str = Field(pattern=_SHA256)
    publication_dry_run_identity: str = Field(pattern=_SHA256)
    accountable_authority: str = Field(min_length=1)
    decided_at: AwareDatetime
    decision_evidence_identity: str = Field(pattern=_SHA256)


HumanAcceptanceDecision = (
    PendingHumanAcceptanceDecision | AcceptedHumanAcceptanceDecision
)


def require_publication_authorization(
    *,
    candidate_identity: str,
    dry_run: PublicationDryRunEvidence,
    decision: HumanAcceptanceDecision,
) -> None:
    if not isinstance(decision, AcceptedHumanAcceptanceDecision):
        raise CorpusAcceptanceValidationError(
            "publication requires an accepted human decision"
        )
    if (
        decision.candidate_identity != candidate_identity
        or decision.publication_dry_run_identity != dry_run.evidence_identity
        or dry_run.status != "passed"
    ):
        raise CorpusAcceptanceValidationError("human decision binding differs")


class PublicationReceipt(_StrictModel):
    candidate_identity: str = Field(pattern=_SHA256)
    decision_identity: str = Field(pattern=_SHA256)
    publication_marker_identity: str = Field(pattern=_SHA256)
    predecessor_marker_identity: str = Field(pattern=_SHA256)
    artifact_identity: str = Field(pattern=_SHA256)
    served_projection_identity: str = Field(pattern=_SHA256)
    recovery_identity: str = Field(pattern=_SHA256)


class CandidateScope(_StrictModel):
    root: Literal["C3262"]
    version: Literal["stated-genus-subclass-v1"]
    worklist_count: Literal[15633]
    worklist_identity: str = Field(pattern=_SHA256)


class CandidateSource(_StrictModel):
    release: str
    source_identity: str = Field(pattern=_SHA256)
    stated_artifact_identity: str = Field(pattern=_SHA256)
    inferred_artifact_identity: str = Field(pattern=_SHA256)
    sibling_manifest_identity: str = Field(pattern=_SHA256)
    extraction_plane: Literal["official-stated"]


class CandidateExecution(_StrictModel):
    run_id: str
    run_fingerprint_identity: str = Field(pattern=_SHA256)
    git_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    policy_identity: str = Field(pattern=_SHA256)


class CandidateProjection(_StrictModel):
    artifact_sha256: str = Field(pattern=_SHA256)
    representation_identity: str = Field(pattern=_SHA256)
    served_semantic_projection_identity: str = Field(pattern=_SHA256)
    no_equivalence: Literal[True]


class CandidateMetrics(_StrictModel):
    values: dict[str, int | float | None]


class CandidateGates(_StrictModel):
    primary_site_cardinality_violations: Literal[0]
    proposal_provenance_valid: Literal[True]
    projection_loss_status: Literal["passed"]
    residual_status: Literal["passed"]
    fidelity_status: Literal["passed"]
    issue_274_detector: Literal["clear"]
    gate_liveness_identity: str = Field(pattern=_SHA256)


class R101CandidateSummary(_StrictModel):
    occurrence_count: Literal[43414]
    routed_or_collapsed_or_suppressed_count: Literal[43414]
    unresolved_count: Literal[0]
    occurrence_partition_identity: str = Field(pattern=_SHA256)
    causal_attribution: Literal["prohibited"]

    @model_validator(mode="after")
    def _partition_identity_matches(self) -> Self:
        expected = _identity(
            self.model_dump(mode="json", exclude={"occurrence_partition_identity"})
        )
        if self.occurrence_partition_identity != expected:
            raise ValueError("R101 occurrence partition identity differs")
        return self


class CorpusAcceptanceContent(_StrictModel):
    schema_version: Literal[1]
    scope: CandidateScope
    source: CandidateSource
    execution: CandidateExecution
    projection: CandidateProjection
    metrics: CandidateMetrics
    gates: CandidateGates
    r101_summary: R101CandidateSummary
    delta_classification: CorpusDeltaClassification
    review_required_exclusions: tuple[ReviewRequiredEffectiveExclusion, ...]
    content_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _content_identity_matches(self) -> Self:
        expected = _identity(self.model_dump(mode="json", exclude={"content_identity"}))
        if self.content_identity != expected:
            raise ValueError("candidate content identity differs")
        return self


class CorpusAcceptanceCandidate(_StrictModel):
    schema_version: Literal[1]
    status: Literal["machine-blocked", "ready-for-human-authorization"]
    scope: CandidateScope
    source: CandidateSource
    execution: CandidateExecution
    projection: CandidateProjection
    metrics: CandidateMetrics
    gates: CandidateGates
    r101_summary: R101CandidateSummary
    delta_classification: CorpusDeltaClassification
    review_required_exclusions: tuple[ReviewRequiredEffectiveExclusion, ...]
    publication_dry_run: PublicationDryRunEvidence
    human_authorization: NotRequestedHumanAuthorization
    candidate_content_identity: str = Field(pattern=_SHA256)
    candidate_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _ready_means_mechanically_complete(self) -> Self:
        ready = _candidate_is_ready(self.delta_classification, self.publication_dry_run)
        if self.status != (
            "ready-for-human-authorization" if ready else "machine-blocked"
        ):
            raise ValueError("candidate status differs from mechanical gates")
        content = _candidate_content_payload(self)
        if self.candidate_content_identity != _identity(content):
            raise ValueError("candidate content identity differs")
        if (
            self.publication_dry_run.candidate_content_identity
            != self.candidate_content_identity
        ):
            raise ValueError("publication dry-run candidate binding differs")
        expected_identity = _identity(
            self.model_dump(mode="json", exclude={"candidate_identity"})
        )
        if self.candidate_identity != expected_identity:
            raise ValueError("candidate identity differs")
        return self


def _candidate_is_ready(
    classification: CorpusDeltaClassification,
    dry_run: PublicationDryRunEvidence,
) -> bool:
    return not classification.unexplained_blockers and dry_run.status == "passed"


def _candidate_content_payload(
    candidate: CorpusAcceptanceCandidate,
) -> dict[str, object]:
    excluded = {
        "status",
        "publication_dry_run",
        "human_authorization",
        "candidate_content_identity",
        "candidate_identity",
    }
    return {
        key: value
        for key, value in candidate.model_dump(mode="json").items()
        if key not in excluded
    }


def finalize_corpus_acceptance_candidate(
    content: CorpusAcceptanceContent,
    dry_run: PublicationDryRunEvidence,
) -> CorpusAcceptanceCandidate:
    """Bind a passed no-write dry-run while leaving human authorization unrequested."""
    if dry_run.candidate_content_identity != content.content_identity:
        raise CorpusAcceptanceValidationError(
            "publication dry-run candidate binding differs"
        )
    payload = {
        **content.model_dump(exclude={"content_identity"}),
        "status": (
            "ready-for-human-authorization"
            if _candidate_is_ready(content.delta_classification, dry_run)
            else "machine-blocked"
        ),
        "publication_dry_run": dry_run.model_dump(),
        "human_authorization": {"status": "not-requested"},
        "candidate_content_identity": content.content_identity,
    }
    candidate_identity = _identity(payload)
    return CorpusAcceptanceCandidate.model_validate(
        {**payload, "candidate_identity": candidate_identity}
    )


class FullStoreAcceptanceObservation(_StrictModel):
    scope_root: str
    worklist_count: int
    exclusion_concepts: tuple[str, ...]
    official_source_assertion_count: int
    highest_fanout_codes: tuple[str, ...]
    logical_select_count: int
    r82_select_count: int


async def observe_full_store_acceptance_inputs(
    client,  # type: ignore[no-untyped-def]
    *,
    source_manifest: Path,
    baseline: Path,
    artifact: Path,
    report: Path,
    review_packet: Path,
    fanout_baseline: Path,
) -> FullStoreAcceptanceObservation:
    """Read configured official source and immutable local evidence without writes."""
    manifest = validate_ncit_sibling_manifest(source_manifest)
    corpus = load_corpus_baseline(baseline)
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != corpus.artifact_identity:
        raise CorpusAcceptanceValidationError("full-corpus artifact identity differs")
    conservation = load_r101_conservation_report(report)
    packet = load_historical_group_review_packet(review_packet)
    if conservation.source_identity != manifest.source_identity:
        raise CorpusAcceptanceValidationError("R101 source identity differs")
    codes = await enumerate_scope_codes(client, corpus.scope_root)
    if len(codes) != corpus.worklist_count:
        raise CorpusAcceptanceValidationError("configured C3262 worklist count differs")
    _require_packet_exclusion_assertions(packet)
    official_count = await _official_exclusion_assertion_count(
        client, manifest.graph_layout.stated_graph_iri
    )
    fanout = load_fanout_baseline(
        fanout_baseline,
        expected_source_identity=manifest.source_identity,
        expected_release=manifest.ontology_version,
    )
    diagnostic = await read_axis_diagnostic_source(client, manifest.source_identity)
    observations = await _rerun_fanout_observations(
        client,
        fanout.concept_codes,
        diagnostic,
        source_identity=manifest.source_identity,
    )
    return FullStoreAcceptanceObservation(
        scope_root=corpus.scope_root,
        worklist_count=len(codes),
        exclusion_concepts=tuple(sorted(_TARGET_EXCLUSIONS)),
        official_source_assertion_count=official_count,
        highest_fanout_codes=fanout.concept_codes,
        logical_select_count=max(item.logical_select_count for item in observations),
        r82_select_count=max(item.select_once_r82_count for item in observations),
    )


def _require_packet_exclusion_assertions(packet) -> None:  # type: ignore[no-untyped-def]
    rows_by_code = {row.concept_code: row for row in packet.review_rows}
    if set(_TARGET_EXCLUSIONS) - rows_by_code.keys():
        raise CorpusAcceptanceValidationError(
            "review packet lacks an exclusion concept"
        )
    if any(not rows_by_code[code].evidence_row_ids for code in _TARGET_EXCLUSIONS):
        raise CorpusAcceptanceValidationError("exclusion lacks source assertions")


async def _official_exclusion_assertion_count(
    client,  # type: ignore[no-untyped-def]
    stated_graph_iri: str,
) -> int:
    values = " ".join(f"<{NCIT_NS}{code}>" for code in _TARGET_EXCLUSIONS)
    query = (
        "SELECT ?concept ?predicate ?value WHERE { GRAPH "
        f"<{stated_graph_iri}> {{ VALUES ?concept {{ {values} }} "
        "?concept ?predicate ?value } }"
    )
    rows = await client.select_once(
        query,
        required_variables={"concept", "predicate", "value"},
    )
    observed = {row.get("concept", "").removeprefix(NCIT_NS) for row in rows}
    if observed != set(_TARGET_EXCLUSIONS):
        raise CorpusAcceptanceValidationError(
            "official exclusion concepts are not retrievable"
        )
    return len(rows)


async def _rerun_fanout_observations(
    client,  # type: ignore[no-untyped-def]
    concept_codes: tuple[str, ...],
    diagnostic,  # type: ignore[no-untyped-def]
    *,
    source_identity: str,
):  # type: ignore[no-untyped-def]
    return tuple(
        [
            await rerun_fanout_concept(
                client, code, diagnostic, source_identity=source_identity
            )
            for code in concept_codes
        ]
    )
