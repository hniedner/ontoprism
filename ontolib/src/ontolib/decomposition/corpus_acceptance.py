"""Identity-bound mechanical acceptance for the certified C3262 corpus.

This module can prepare evidence for human authorization.  It cannot authorize or
publish a corpus, and it never mutates PostgreSQL, QLever, or the official NCIt source.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from rdflib import Graph, URIRef
from rdflib import Literal as RdfLiteral
from rdflib.term import Node
from scripts.research.group_review_packet import (
    ActualGenusFactEvidence,
    ActualPairEvidence,
    ActualSourceFactEvidence,
    HistoricalGroupReviewConcept,
    load_historical_group_review_packet,
)

from ontolib.decomposition import vocab
from ontolib.decomposition.atomic_write import atomic_write_bytes
from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS
from ontolib.decomposition.axis_diagnostics import read_axis_diagnostic_source
from ontolib.decomposition.corpus_baseline import CorpusBaseline, load_corpus_baseline
from ontolib.decomposition.fanout_baseline import (
    load_fanout_baseline,
    rerun_fanout_concept,
)
from ontolib.decomposition.proposal_registry import (
    ConceptProposal,
    ProposalRegistry,
    load_proposal_registry,
)
from ontolib.decomposition.provenance_models import (
    CompletedRunForEvidence,
    PublicationMarkerSnapshot,
    RunSummary,
)
from ontolib.decomposition.publication import (
    PublicationGraphClient,
    PublicationMarker,
    PublicationValidationError,
    build_replacement_update,
    publication_recovery_decision,
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
from ontolib.decomposition.run_artifacts import (
    GeneratorBinding,
    RetentionBinding,
    SourceIdentity,
    publish_generation,
)
from ontolib.decomposition.scope import enumerate_scope_codes
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.sibling_store import (
    NcitSiblingStoreManifest,
    validate_ncit_sibling_manifest,
)

_SHA256 = r"^[0-9a-f]{64}$"
_SHA256_LENGTH = 64
_CODE = r"^C[0-9]+$"
_TARGET_EXCLUSIONS = ("C102870", "C198031", "C27262", "C35756")
_CERTIFIED_WORKLIST_COUNT = 15_633
_REPORT_ONLY_FIDELITY_TARGET = 0.9


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


ReviewRelation = Literal[
    "grouping-disputed", "added-to-candidate", "missing-from-candidate"
]
_REVIEW_RELATION_ORDER: dict[ReviewRelation, int] = {
    "added-to-candidate": 0,
    "missing-from-candidate": 1,
    "grouping-disputed": 2,
}


class _ReviewRequiredPair(_StrictModel):
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=_CODE)
    review_relations: tuple[ReviewRelation, ...] = Field(min_length=1)
    historical_evidence_identities: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _canonical_evidence(self) -> Self:
        expected_relations = tuple(
            sorted(set(self.review_relations), key=_REVIEW_RELATION_ORDER.__getitem__)
        )
        if self.review_relations != expected_relations:
            raise ValueError("review relations must be canonical and unique")
        _require_canonical_identities(
            "historical evidence", self.historical_evidence_identities
        )
        return self


class RemovedFromEffectivePair(_ReviewRequiredPair):
    effective_disposition: Literal["removed-from-effective"]
    source_assertion_identities: tuple[str, ...] = Field(min_length=1)
    next_step: Literal["specialist-decision-required-before-inclusion"]

    @model_validator(mode="after")
    def _canonical_source_assertions(self) -> Self:
        _require_canonical_identities(
            "source assertion", self.source_assertion_identities
        )
        return self


class ReviewRequiredNonEmittedPair(_ReviewRequiredPair):
    effective_disposition: Literal["review-required-non-emitted"]
    source_assertion_identities: tuple[()] = ()
    interpretation: Literal["absence-is-scoped-evidence-not-falsehood"]
    next_step: Literal[
        "engineering-source-provenance-prerequisite-plus-specialist-decision-before-inclusion"
    ]

    @model_validator(mode="after")
    def _historical_oracle_relation_is_bound(self) -> Self:
        if "missing-from-candidate" not in self.review_relations:
            raise ValueError(
                "review-required-non-emitted pair requires historical missing relation"
            )
        return self


ReviewRequiredPairChange = Annotated[
    RemovedFromEffectivePair | ReviewRequiredNonEmittedPair,
    Field(discriminator="effective_disposition"),
]


class ReviewRequiredEffectiveExclusion(_StrictModel):
    concept_code: str = Field(pattern=_CODE)
    pair_changes: tuple[ReviewRequiredPairChange, ...] = Field(min_length=1)
    reason: Literal["unresolved-semantic-ambiguity"]
    official_source_preserved: Literal[True]
    human_approval: Literal[False]
    nci_approval: Literal[False]

    @model_validator(mode="after")
    def _canonical_exact_evidence(self) -> Self:
        if self.pair_changes != _canonical_pair_changes(self.pair_changes):
            raise ValueError(
                "review-required pair changes must be canonical and unique"
            )
        return self


def _canonical_pair_changes(
    values: tuple[ReviewRequiredPairChange, ...],
) -> tuple[ReviewRequiredPairChange, ...]:
    return tuple(
        sorted(
            set(values),
            key=lambda item: (
                item.axis,
                item.filler_code,
                item.effective_disposition,
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
    source_artifact: Path,
) -> tuple[ReviewRequiredEffectiveExclusion, ...]:
    """Derive the four exact exclusions from the validated tracked review evidence."""
    packet = load_historical_group_review_packet(review_packet_path)
    rationale_identity = hashlib.sha256(rationale_path.read_bytes()).hexdigest()
    rows = {row.concept_code: row for row in packet.review_rows}
    concepts = {concept.code: concept for concept in packet.concepts}
    source_assertions = _source_projection_assertion_identities(
        source_artifact.read_bytes()
    )
    input_keys = set(source_assertions)
    if set(_TARGET_EXCLUSIONS) - rows.keys():
        raise CorpusAcceptanceValidationError(
            "review packet lacks an exclusion concept"
        )

    exclusions: list[ReviewRequiredEffectiveExclusion] = []
    for code in _TARGET_EXCLUSIONS:
        row = rows[code]
        concept = concepts[code]
        relations = _pair_review_relations(row)
        changes = tuple(
            _review_required_pair(
                pair,
                review_relations=tuple(
                    sorted(pair_relations, key=_REVIEW_RELATION_ORDER.__getitem__)
                ),
                concept=concept,
                rule_evidence=packet.rule_evidence,
                input_present=(code, pair[0], pair[1]) in input_keys,
                projection_assertion_identities=source_assertions.get(
                    (code, pair[0], pair[1]), ()
                ),
                historical_evidence_identities=tuple(
                    sorted(
                        {
                            row.row_identity,
                            packet.packet_identity,
                            rationale_identity,
                            *_historical_group_identities(concept, pair),
                        }
                    )
                ),
            )
            for pair, pair_relations in sorted(relations.items())
        )
        exclusions.append(
            ReviewRequiredEffectiveExclusion(
                concept_code=code,
                pair_changes=changes,
                reason="unresolved-semantic-ambiguity",
                official_source_preserved=True,
                human_approval=False,
                nci_approval=False,
            )
        )
    return tuple(exclusions)


def _pair_review_relations(row) -> dict[tuple[str, str], set[ReviewRelation]]:  # type: ignore[no-untyped-def]
    relations: dict[tuple[str, str], set[ReviewRelation]] = {}
    for pair in row.grouping_diagnosis.affected_pairs:
        relations.setdefault(pair, set()).add("grouping-disputed")
    for pair in row.pair_delta.extra_pairs:
        relations.setdefault(pair, set()).add("added-to-candidate")
    for pair in row.pair_delta.missing_pairs:
        relations.setdefault(pair, set()).add("missing-from-candidate")
    return relations


def _historical_group_identities(
    concept: HistoricalGroupReviewConcept, pair: tuple[str, str]
) -> tuple[str, ...]:
    return tuple(
        group.normalized_group_id
        for group in concept.expected_groups
        if pair in group.pairs
    )


def _current_source_assertion_identities(
    concept: HistoricalGroupReviewConcept,
    pair: tuple[str, str],
    rule_evidence,  # type: ignore[no-untyped-def]
) -> tuple[str, ...]:
    identities: set[str] = set()
    for group in concept.actual_groups:
        for documented in group.pairs:
            if documented.pair != pair:
                continue
            identities.update(_documented_source_assertion_identities(documented))
    for evidence in rule_evidence:
        if evidence.concept_code == concept.code and pair in evidence.output_pairs:
            identities.update(evidence.source_occurrence_ids)
            identities.update(evidence.source_fact_ids)
    return tuple(sorted(identities))


def _documented_source_assertion_identities(documented) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    if isinstance(documented, ActualPairEvidence):
        return tuple(
            {
                *documented.occurrence_ids,
                *(item.source_fact_id for item in documented.occurrences),
            }
        )
    if isinstance(documented, ActualGenusFactEvidence | ActualSourceFactEvidence):
        return tuple(item.fact_id for item in documented.source_facts)
    return ()


def _source_projection_assertion_identities(
    artifact: bytes,
) -> dict[tuple[str, str, str], tuple[str, ...]]:
    identities: dict[tuple[str, str, str], set[str]] = {}
    for line in artifact.splitlines(keepends=True):
        key = _effective_constituent_key(line)
        if key is not None:
            identities.setdefault(key, set()).add(hashlib.sha256(line).hexdigest())
    return {key: tuple(sorted(values)) for key, values in identities.items()}


def _review_required_pair(
    pair: tuple[str, str],
    *,
    review_relations: tuple[ReviewRelation, ...],
    concept: HistoricalGroupReviewConcept,
    rule_evidence,  # type: ignore[no-untyped-def]
    input_present: bool,
    projection_assertion_identities: tuple[str, ...],
    historical_evidence_identities: tuple[str, ...],
) -> ReviewRequiredPairChange:
    axis, filler = pair
    if not input_present:
        return ReviewRequiredNonEmittedPair(
            axis=axis,
            filler_code=filler,
            review_relations=review_relations,
            historical_evidence_identities=historical_evidence_identities,
            effective_disposition="review-required-non-emitted",
            source_assertion_identities=(),
            interpretation="absence-is-scoped-evidence-not-falsehood",
            next_step=(
                "engineering-source-provenance-prerequisite-plus-specialist-decision-"
                "before-inclusion"
            ),
        )
    source_identities = tuple(
        sorted(
            {
                *_current_source_assertion_identities(concept, pair, rule_evidence),
                *projection_assertion_identities,
            }
        )
    )
    if not source_identities:
        raise CorpusAcceptanceValidationError(
            f"physically removed pair lacks exact source assertions: {pair!r}"
        )
    return RemovedFromEffectivePair(
        axis=axis,
        filler_code=filler,
        review_relations=review_relations,
        historical_evidence_identities=historical_evidence_identities,
        effective_disposition="removed-from-effective",
        source_assertion_identities=source_identities,
        next_step="specialist-decision-required-before-inclusion",
    )


class EffectivePairDispositionEvidence(_StrictModel):
    concept_code: str = Field(pattern=_CODE)
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=_CODE)
    review_relations: tuple[ReviewRelation, ...] = Field(min_length=1)
    effective_disposition: Literal[
        "removed-from-effective", "review-required-non-emitted"
    ]
    input_present: bool
    output_present: bool


class EffectiveArtifactEvidence(_StrictModel):
    source_artifact_identity: str = Field(pattern=_SHA256)
    effective_artifact_identity: str = Field(pattern=_SHA256)
    removed_pair_count: int = Field(gt=0)
    non_emitted_pair_count: int = Field(ge=0)
    removed_pairs: tuple[EffectivePairDispositionEvidence, ...] = Field(min_length=1)
    non_emitted_pairs: tuple[EffectivePairDispositionEvidence, ...]
    source_artifact_preserved: Literal[True]


_CONSTITUENT_LINE = re.compile(
    rf"^<{re.escape(NCIT_NS)}(?P<concept>C[0-9]+)> "
    rf"<{re.escape(vocab.HAS_CONSTITUENT)}>\s+"
    rf"\[<{re.escape(vocab.AXIS)}> <(?P<axis>[^>]+)> ; "
    rf"<{re.escape(vocab.FILLER)}> <{re.escape(NCIT_NS)}(?P<filler>C[0-9]+)>"
    rf"(?: ;| \])"
)


def build_effective_artifact(
    *,
    source_artifact: Path,
    destination: Path,
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
) -> EffectiveArtifactEvidence:
    """Write a source-derived artifact with only exact disputed pairs withheld."""
    source = source_artifact.read_bytes()
    source_identity = hashlib.sha256(source).hexdigest()
    by_key = _effective_exclusion_map(exclusions)
    payload, removed_keys = _filter_effective_lines(source, by_key)
    evidence = _validate_effective_delta(
        source, payload, removed_keys, exclusions, by_key
    )
    if destination.exists():
        raise CorpusAcceptanceValidationError("effective artifact destination exists")
    atomic_write_bytes(destination, payload)
    if hashlib.sha256(source_artifact.read_bytes()).hexdigest() != source_identity:
        raise CorpusAcceptanceValidationError(
            "source artifact changed during projection"
        )
    return EffectiveArtifactEvidence(
        source_artifact_identity=source_identity,
        effective_artifact_identity=hashlib.sha256(payload).hexdigest(),
        removed_pair_count=len(evidence[0]),
        non_emitted_pair_count=len(evidence[1]),
        removed_pairs=evidence[0],
        non_emitted_pairs=evidence[1],
        source_artifact_preserved=True,
    )


def _effective_constituent_key(line: bytes) -> tuple[str, str, str] | None:
    match = _CONSTITUENT_LINE.match(line.decode("utf-8"))
    if match is None:
        return None
    axis_iri = match.group("axis")
    axis = (
        f"op:{axis_iri.removeprefix(vocab.ONTOPRISM_NS)}"
        if axis_iri.startswith(vocab.ONTOPRISM_NS)
        else axis_iri
    )
    return match.group("concept"), axis, match.group("filler")


def _filter_effective_lines(
    source: bytes, by_key: dict[tuple[str, str, str], ReviewRequiredPairChange]
) -> tuple[bytes, tuple[tuple[str, str, str], ...]]:
    removed: set[tuple[str, str, str]] = set()
    retained: list[bytes] = []
    for line in source.splitlines(keepends=True):
        key = _effective_constituent_key(line)
        pair = by_key.get(key) if key is not None else None
        if (
            key is not None
            and pair is not None
            and pair.effective_disposition == "removed-from-effective"
        ):
            removed.add(key)
        else:
            retained.append(line)
    return b"".join(retained), tuple(sorted(removed))


def _validate_effective_delta(
    source: bytes,
    payload: bytes,
    removed_keys: tuple[tuple[str, str, str], ...],
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
    by_key: dict[tuple[str, str, str], ReviewRequiredPairChange],
) -> tuple[
    tuple[EffectivePairDispositionEvidence, ...],
    tuple[EffectivePairDispositionEvidence, ...],
]:
    input_keys = _constituent_keys(source)
    output_keys = _constituent_keys(payload)
    required_removed = {
        key
        for key, pair in by_key.items()
        if pair.effective_disposition == "removed-from-effective"
    }
    observed_removed = set(removed_keys)
    non_emitted_keys = {
        key
        for key, pair in by_key.items()
        if pair.effective_disposition == "review-required-non-emitted"
    }
    _require_effective_pair_presence(
        observed_removed=observed_removed,
        required_removed=required_removed,
        non_emitted_keys=non_emitted_keys,
        input_keys=input_keys,
        output_keys=output_keys,
    )
    if not _each_excluded_concept_has_delta(required_removed, exclusions):
        raise CorpusAcceptanceValidationError(
            "each review-required concept must have a nonempty effective delta"
        )
    return (
        _disposition_evidence(required_removed, by_key, input_keys, output_keys),
        _disposition_evidence(non_emitted_keys, by_key, input_keys, output_keys),
    )


def _require_effective_pair_presence(
    *,
    observed_removed: set[tuple[str, str, str]],
    required_removed: set[tuple[str, str, str]],
    non_emitted_keys: set[tuple[str, str, str]],
    input_keys: set[tuple[str, str, str]],
    output_keys: set[tuple[str, str, str]],
) -> None:
    if observed_removed != required_removed:
        raise CorpusAcceptanceValidationError(
            "removed-from-effective disposition differs from input pair presence"
        )
    if non_emitted_keys & (input_keys | output_keys):
        raise CorpusAcceptanceValidationError(
            "review-required-non-emitted disposition differs from pair presence"
        )
    if output_keys & required_removed:
        raise CorpusAcceptanceValidationError(
            "removed-from-effective pair remains in effective output"
        )


def _constituent_keys(artifact: bytes) -> set[tuple[str, str, str]]:
    return {
        key
        for line in artifact.splitlines(keepends=True)
        if (key := _effective_constituent_key(line)) is not None
    }


def _disposition_evidence(
    keys: set[tuple[str, str, str]],
    by_key: dict[tuple[str, str, str], ReviewRequiredPairChange],
    input_keys: set[tuple[str, str, str]],
    output_keys: set[tuple[str, str, str]],
) -> tuple[EffectivePairDispositionEvidence, ...]:
    return tuple(
        EffectivePairDispositionEvidence(
            concept_code=key[0],
            axis=key[1],
            filler_code=key[2],
            review_relations=by_key[key].review_relations,
            effective_disposition=by_key[key].effective_disposition,
            input_present=key in input_keys,
            output_present=key in output_keys,
        )
        for key in sorted(keys)
    )


def _each_excluded_concept_has_delta(
    removed_keys: set[tuple[str, str, str]],
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
) -> bool:
    observed_concepts = {item[0] for item in removed_keys}
    excluded_concepts = {item.concept_code for item in exclusions}
    return observed_concepts == excluded_concepts


def _effective_exclusion_map(
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
) -> dict[tuple[str, str, str], ReviewRequiredPairChange]:
    by_key: dict[tuple[str, str, str], ReviewRequiredPairChange] = {}
    for exclusion in exclusions:
        for pair in exclusion.pair_changes:
            key = (exclusion.concept_code, pair.axis, pair.filler_code)
            previous = by_key.setdefault(key, pair)
            if previous != pair:
                raise CorpusAcceptanceValidationError(
                    "effective exclusion has ambiguous pair dispositions"
                )
    return by_key


StructuralCategory = Literal[
    "r101-occurrence-linked-output-delta",
    "source-role-routed-projection",
    "proposal-minted-projection",
    "unexplained-blocker",
]
MetadataCategory = Literal[
    "provenance-evidence-binding-refresh",
    "group-identity-rebinding",
    "conservative-review-escalation",
    "authority-required-review-clearance",
    "semantic-routing-change",
    "evidence-bound-metadata-composite",
    "unexplained-blocker",
]
BlockerCategory = Literal[
    "missing-source-definition-evidence",
    "missing-source-occurrence-evidence",
    "missing-source-role-evidence",
    "unregistered-minted-filler",
    "routing-policy-not-applicable",
    "incompatible-metadata-combination",
]


class ClassificationBlocker(_StrictModel):
    category: BlockerCategory
    reason: str = Field(min_length=1)
    row_key: tuple[str, str, str, str]


class StructuralChangeClassification(_StrictModel):
    object_kind: Literal["structural"]
    category: StructuralCategory
    row: NonR101DeltaRow
    rule_name: str = Field(min_length=1)
    evidence_identities: tuple[str, ...] = Field(min_length=1)
    source_identities: tuple[str, ...] = Field(min_length=1)


class MetadataChangeClassification(_StrictModel):
    object_kind: Literal["metadata"]
    category: MetadataCategory
    delta: NonR101MetadataDelta
    rule_name: str = Field(min_length=1)
    evidence_identities: tuple[str, ...] = Field(min_length=1)
    source_identities: tuple[str, ...] = Field(min_length=1)


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
    unexplained_blockers: tuple[ClassificationBlocker, ...]
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
    counts = dict(
        sorted(Counter(item.category for item in value.classifications).items())
    )
    if value.category_counts != counts:
        raise ValueError("classification category counts differ")
    blockers = tuple(
        _classification_blocker(item)
        for item in value.classifications
        if item.category == "unexplained-blocker"
    )
    if value.unexplained_blockers != blockers:
        raise ValueError("unexplained blocker inventory differs")


_METADATA_CATEGORIES: dict[str, MetadataCategory] = {
    "axis_ambiguity_group_id": "group-identity-rebinding",
    "source_definition_ids": "provenance-evidence-binding-refresh",
    "source_occurrence_ids": "provenance-evidence-binding-refresh",
    "axis_source": "semantic-routing-change",
    "source_roles": "semantic-routing-change",
    "most_specific": "semantic-routing-change",
}


def _metadata_categories(delta: NonR101MetadataDelta) -> tuple[MetadataCategory, ...]:
    fields = set(delta.changed_fields)
    allowed = {
        "axis_ambiguity_group_id",
        "source_definition_ids",
        "source_occurrence_ids",
        "axis_source",
        "source_roles",
        "most_specific",
        "needs_review",
    }
    if not fields or not fields <= allowed:
        raise CorpusAcceptanceValidationError("metadata changed fields are invalid")
    if len(fields) > 1:
        evidence_fields = {
            "source_definition_ids",
            "source_occurrence_ids",
            "source_roles",
        }
        if fields & evidence_fields:
            return ("unexplained-blocker",)
        return tuple(
            sorted({_single_metadata_category(delta, field) for field in fields})
        )
    return (_single_metadata_category(delta, next(iter(fields))),)


def _single_metadata_category(
    delta: NonR101MetadataDelta, field: str
) -> MetadataCategory:
    if field == "needs_review":
        return (
            "conservative-review-escalation"
            if delta.new.needs_review
            else "authority-required-review-clearance"
        )
    try:
        return _METADATA_CATEGORIES[field]
    except KeyError as exc:
        raise CorpusAcceptanceValidationError(
            "metadata changed fields are invalid"
        ) from exc


def classify_corpus_delta(
    report: R101ConservationReport,
    *,
    routing_policy_identity: str,
    proposal_registry: ProposalRegistry,
) -> CorpusDeltaClassification:
    """Classify every typed comparator object without inferring R101 causation."""
    evidence = report.non_r101_delta_evidence
    if re.fullmatch(_SHA256, routing_policy_identity) is None:
        raise CorpusAcceptanceValidationError("routing policy identity is invalid")
    classifications = _classify_structural_rows(
        evidence.rows,
        report_identity=report.report_identity,
        source_identity=report.source_identity,
        routing_policy_identity=routing_policy_identity,
        proposal_registry=proposal_registry,
    )
    classifications.extend(
        StructuralChangeClassification(
            object_kind="structural",
            category="r101-occurrence-linked-output-delta",
            row=item.row,
            rule_name="r101-occurrence-link-v1",
            evidence_identities=tuple(
                sorted({report.report_identity, *item.r101_occurrence_ids})
            ),
            source_identities=_row_source_identities(
                item.row, source_identity=report.source_identity
            ),
        )
        for item in evidence.classified_rows
    )
    classifications.extend(
        _classify_metadata_rows(
            evidence.metadata_deltas,
            report_identity=report.report_identity,
            source_identity=report.source_identity,
            routing_policy_identity=routing_policy_identity,
        )
    )
    classifications.sort(
        key=lambda item: (item.object_kind, _classification_object_key(item))
    )
    counts = dict(sorted(Counter(item.category for item in classifications).items()))
    blockers = tuple(
        _classification_blocker(item)
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
    *,
    report_identity: str,
    source_identity: str,
    routing_policy_identity: str,
    proposal_registry: ProposalRegistry,
) -> list[ChangedObjectClassification]:
    result: list[ChangedObjectClassification] = []
    for row in rows:
        category = _structural_category(
            row, proposal_registry, source_identity=source_identity
        )
        routing_evidence = _structural_routing_evidence(row, category)
        result.append(
            StructuralChangeClassification(
                object_kind="structural",
                category=category,
                row=row,
                rule_name=_structural_rule_name(category),
                evidence_identities=tuple(
                    sorted(
                        {
                            report_identity,
                            routing_policy_identity,
                            routing_evidence,
                        }
                    )
                ),
                source_identities=tuple(
                    sorted(
                        {
                            *_row_source_identities(
                                row, source_identity=source_identity
                            ),
                        }
                    )
                ),
            )
        )
    return result


def _structural_category(
    row: NonR101DeltaRow,
    proposal_registry: ProposalRegistry,
    *,
    source_identity: str,
) -> StructuralCategory:
    if row.filler_code.startswith("MINT-"):
        if _eligible_minted_proposal(
            row.filler_code, proposal_registry, source_identity=source_identity
        ):
            return "proposal-minted-projection"
        return "unexplained-blocker"
    if not row.source_definition_ids:
        return "unexplained-blocker"
    if row.axis_source == "role" and not row.source_occurrence_ids:
        return "unexplained-blocker"
    if not _named_routing_policy_applies(row):
        return "unexplained-blocker"
    return "source-role-routed-projection"


def _eligible_minted_proposal(
    filler_code: str,
    proposal_registry: ProposalRegistry,
    *,
    source_identity: str,
) -> bool:
    proposal = next(
        (
            item
            for item in proposal_registry.proposals
            if isinstance(item, ConceptProposal) and item.id == filler_code
        ),
        None,
    )
    return (
        proposal_registry.source_identity == source_identity
        and proposal is not None
        and proposal.status in {"locally-approved", "submitted", "accepted-in-ncit"}
    )


def _named_routing_policy_applies(row: NonR101DeltaRow) -> bool:
    """Require an exact declared axis contract for this directional row key."""
    if row.axis_source != "role" or not row.source_roles:
        return False
    contract = AXIS_CONTRACTS.get(row.axis)
    return contract is not None and set(row.source_roles) <= set(contract.source_roles)


def _structural_routing_evidence(
    row: NonR101DeltaRow, category: StructuralCategory
) -> str:
    return _identity(
        {
            "rule": _structural_rule_name(category),
            "direction": row.change,
            "concept_code": row.concept_code,
            "axis": row.axis,
            "filler_code": row.filler_code,
            "source_roles": row.source_roles,
            "source_definition_ids": row.source_definition_ids,
            "source_occurrence_ids": row.source_occurrence_ids,
        }
    )


def _classify_metadata_rows(
    deltas: tuple[NonR101MetadataDelta, ...],
    *,
    report_identity: str,
    source_identity: str,
    routing_policy_identity: str,
) -> list[ChangedObjectClassification]:
    result: list[ChangedObjectClassification] = []
    for delta in deltas:
        try:
            validated_delta = NonR101MetadataDelta.model_validate(delta.model_dump())
        except ValidationError as exc:
            raise CorpusAcceptanceValidationError(
                "metadata changed fields are invalid"
            ) from exc
        categories = _metadata_categories(validated_delta)
        if not categories:
            raise CorpusAcceptanceValidationError("metadata changed fields are invalid")
        category: MetadataCategory = (
            "evidence-bound-metadata-composite"
            if len(categories) > 1
            else next(iter(categories))
        )
        rule_name = (
            "metadata-composite:" + "+".join(categories) + "-v1"
            if len(categories) > 1
            else f"metadata-{category}-v1"
        )
        result.append(
            MetadataChangeClassification(
                object_kind="metadata",
                category=category,
                delta=validated_delta,
                rule_name=rule_name,
                evidence_identities=tuple(
                    sorted(
                        {
                            report_identity,
                            routing_policy_identity,
                        }
                    )
                ),
                source_identities=tuple(
                    sorted(
                        {
                            *_row_source_identities(
                                validated_delta.old, source_identity=source_identity
                            ),
                            *_row_source_identities(
                                validated_delta.new, source_identity=source_identity
                            ),
                        }
                    )
                ),
            )
        )
    return result


def _classification_blocker(
    item: ChangedObjectClassification,
) -> ClassificationBlocker:
    row = (
        item.row if isinstance(item, StructuralChangeClassification) else item.delta.new
    )
    row_key = (row.change, row.concept_code, row.axis, row.filler_code)
    if isinstance(item, MetadataChangeClassification):
        return ClassificationBlocker(
            category="incompatible-metadata-combination",
            reason="metadata combination changes source evidence or routing roles",
            row_key=row_key,
        )
    category, reason = _structural_blocker_reason(row)
    return ClassificationBlocker(category=category, reason=reason, row_key=row_key)


def _structural_blocker_reason(
    row: NonR101DeltaRow,
) -> tuple[BlockerCategory, str]:
    if not row.source_definition_ids:
        return (
            "missing-source-definition-evidence",
            "structural row lacks source definition identities",
        )
    if row.axis_source == "role" and not row.source_occurrence_ids:
        return (
            "missing-source-occurrence-evidence",
            "structural row lacks source occurrence identities",
        )
    if not row.source_roles:
        return "missing-source-role-evidence", "structural row lacks source roles"
    if not _named_routing_policy_applies(row):
        return (
            "routing-policy-not-applicable",
            "no exact named routing policy applies to the structural row",
        )
    return (
        "unregistered-minted-filler",
        "minted filler lacks an eligible exact proposal registry record",
    )


def _row_source_identities(
    row: NonR101DeltaRow, *, source_identity: str
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                source_identity,
                *row.source_definition_ids,
                *row.source_occurrence_ids,
            }
        )
    )


def _structural_rule_name(category: StructuralCategory) -> str:
    return f"structural-{category}-v1"


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
    postgres_before_identity: str = Field(pattern=_SHA256)
    postgres_after_identity: str = Field(pattern=_SHA256)
    qlever_before_identity: str = Field(pattern=_SHA256)
    qlever_after_identity: str = Field(pattern=_SHA256)
    recoverability_status: Literal["passed", "blocked"]
    publication_writes_performed: Literal[False]
    evidence_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_and_status(self) -> Self:
        passed = _dry_run_passed(self)
        if self.status != ("passed" if passed else "blocked"):
            raise ValueError("publication dry-run status differs")
        expected = _identity(
            self.model_dump(mode="json", exclude={"evidence_identity"})
        )
        if self.evidence_identity != expected:
            raise ValueError("publication dry-run identity differs")
        return self


def _dry_run_passed(evidence: PublicationDryRunEvidence) -> bool:
    checks = (
        evidence.postgres_read_verified,
        evidence.qlever_read_verified,
        evidence.postgres_before_identity == evidence.postgres_after_identity,
        evidence.qlever_before_identity == evidence.qlever_after_identity,
        evidence.recoverability_status == "passed",
    )
    return all(checks)


class PublicationPlaneBinding(_StrictModel):
    """Bind the persisted official run to its separately identified effective view."""

    official_persisted_representation_identity: str = Field(pattern=_SHA256)
    effective_representation_identity: str = Field(pattern=_SHA256)


def _marker_payload(marker: PublicationMarker | None) -> dict[str, object]:
    if marker is None:
        return {"marker": None}
    return marker.model_dump(mode="json")


def _recoverability_status(
    predecessor: PublicationMarker | None, intent: PublicationMarker
) -> Literal["passed", "blocked"]:
    decisions = (
        publication_recovery_decision(
            current=predecessor, intent=intent, predecessor=predecessor
        ),
        publication_recovery_decision(
            current=intent, intent=intent, predecessor=predecessor
        ),
    )
    return "passed" if decisions == ("apply", "already-committed") else "blocked"


class PublicationReadStore(Protocol):
    async def completed_run_for_evidence(
        self, run_id: str
    ) -> CompletedRunForEvidence: ...


async def dry_run_corpus_publication(
    *,
    candidate_content_identity: str,
    run_id: str,
    source_identity: str,
    planes: PublicationPlaneBinding,
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
    postgres_before_identity = _identity(run.model_dump(mode="json"))
    _require_publication_run_binding(
        run,
        source_identity=source_identity,
        representation_identity=planes.official_persisted_representation_identity,
        expected_worklist_count=expected_worklist_count,
    )
    try:
        artifact_identity = validate_artifact(
            artifact, expected_codes=expected_codes, run_id=run_id
        )
    except PublicationValidationError as exc:
        raise CorpusAcceptanceValidationError(str(exc)) from exc
    if artifact_identity != planes.effective_representation_identity:
        raise CorpusAcceptanceValidationError(
            "artifact representation identity differs"
        )
    predecessor = await read_publication_marker(graph)
    predecessor_payload = _marker_payload(predecessor)
    predecessor_identity = _identity(predecessor_payload)
    marker = PublicationMarkerSnapshot(
        run_id=run_id,
        source_identity=source_identity,
        representation_identity=planes.effective_representation_identity,
        built_at=run.fingerprint.emitted_at,
    )
    # Building the exact replacement statement proves that the destination/staging
    # protocol is representable; it is deliberately not sent to the graph client.
    update = build_replacement_update(
        PublicationMarker.model_validate(marker.model_dump()), staging_graph_iri(run_id)
    )
    publication_marker = PublicationMarker.model_validate(marker.model_dump())
    run_after = await provenance.completed_run_for_evidence(run_id)
    predecessor_after = await read_publication_marker(graph)
    postgres_after_identity = _identity(run_after.model_dump(mode="json"))
    predecessor_after_payload = _marker_payload(predecessor_after)
    qlever_before_identity = _identity(predecessor_payload)
    qlever_after_identity = _identity(predecessor_after_payload)
    recoverability_status = _recoverability_status(predecessor, publication_marker)
    unchanged = all(
        (
            postgres_before_identity == postgres_after_identity,
            qlever_before_identity == qlever_after_identity,
        )
    )
    payload = {
        "schema_version": 1,
        "status": "passed"
        if unchanged and recoverability_status == "passed"
        else "blocked",
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
        "postgres_before_identity": postgres_before_identity,
        "postgres_after_identity": postgres_after_identity,
        "qlever_before_identity": qlever_before_identity,
        "qlever_after_identity": qlever_after_identity,
        "recoverability_status": recoverability_status,
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
    attestation_artifact_identity: str = Field(pattern=_SHA256)
    decision_evidence_identity: str = Field(pattern=_SHA256)


HumanAcceptanceDecision = (
    PendingHumanAcceptanceDecision | AcceptedHumanAcceptanceDecision
)


def write_pending_human_acceptance_decision(
    path: Path,
    *,
    candidate_identity: str,
    publication_dry_run_identity: str,
) -> PendingHumanAcceptanceDecision:
    decision = PendingHumanAcceptanceDecision(
        status="not-requested",
        candidate_identity=candidate_identity,
        publication_dry_run_identity=publication_dry_run_identity,
    )
    atomic_write_bytes(
        path,
        (
            json.dumps(decision.model_dump(mode="json"), sort_keys=True, indent=2)
            + "\n"
        ).encode(),
    )
    return decision


def load_human_acceptance_decision(
    path: Path, *, attestation_artifact: Path | None = None
) -> HumanAcceptanceDecision:
    try:
        payload = json.loads(path.read_bytes())
        decision = TypeAdapter(HumanAcceptanceDecision).validate_python(
            payload, strict=True
        )
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise CorpusAcceptanceValidationError(
            "human acceptance decision is invalid"
        ) from exc
    if isinstance(decision, AcceptedHumanAcceptanceDecision):
        if attestation_artifact is None:
            raise CorpusAcceptanceValidationError(
                "accepted decision requires an independent attestation artifact"
            )
        if (
            _file_identity(attestation_artifact)
            != decision.attestation_artifact_identity
        ):
            raise CorpusAcceptanceValidationError(
                "attestation artifact identity differs"
            )
    return decision


def require_publication_authorization(
    *,
    candidate_identity: str,
    dry_run: PublicationDryRunEvidence,
    decision: HumanAcceptanceDecision,
    attestation_artifact: Path,
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
    if _file_identity(attestation_artifact) != decision.attestation_artifact_identity:
        raise CorpusAcceptanceValidationError("attestation artifact identity differs")


def build_accepted_publication_artifact(
    *,
    source_artifact: Path,
    destination: Path,
    candidate_identity: str,
    dry_run: PublicationDryRunEvidence,
    decision: HumanAcceptanceDecision,
    attestation_artifact: Path,
    source_release: str,
    source_identity: str,
    run_id: str,
    representation_identity: str,
    publication_identity: str,
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
) -> str:
    """Emit the accepted API metadata only after exact human authorization."""
    require_publication_authorization(
        candidate_identity=candidate_identity,
        dry_run=dry_run,
        decision=decision,
        attestation_artifact=attestation_artifact,
    )
    if destination.exists():
        raise CorpusAcceptanceValidationError(
            "accepted publication artifact destination exists"
        )
    graph = _read_effective_graph(source_artifact)
    represented = set(
        graph.subjects(
            URIRef(vocab.REPRESENTATION_STATUS),
            RdfLiteral(vocab.LEGACY_PRECOORDINATED),
        )
    )
    _add_acceptance_metadata(
        graph,
        represented=represented,
        exclusions=exclusions,
        source_release=source_release,
        source_identity=source_identity,
        run_id=run_id,
        representation_identity=representation_identity,
        publication_identity=publication_identity,
    )
    payload = graph.serialize(format="turtle", encoding="utf-8")
    if not isinstance(payload, bytes):
        raise CorpusAcceptanceValidationError(
            "accepted publication serializer returned text"
        )
    atomic_write_bytes(destination, payload)
    return hashlib.sha256(payload).hexdigest()


def _read_effective_graph(source_artifact: Path) -> Graph:
    graph = Graph()
    try:
        graph.parse(source_artifact, format="turtle")
    except Exception as exc:
        raise CorpusAcceptanceValidationError(
            "effective artifact is not valid Turtle"
        ) from exc
    return graph


def _add_acceptance_metadata(
    graph: Graph,
    *,
    represented: Iterable[Node],
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
    source_release: str,
    source_identity: str,
    run_id: str,
    representation_identity: str,
    publication_identity: str,
) -> None:
    excluded = {item.concept_code: item for item in exclusions}
    for subject in represented:
        exclusion = excluded.get(str(subject).removeprefix(NCIT_NS))
        _add_subject_acceptance_values(
            graph,
            subject=subject,
            excluded=exclusion is not None,
            source_release=source_release,
            source_identity=source_identity,
            run_id=run_id,
            representation_identity=representation_identity,
            publication_identity=publication_identity,
        )
        if exclusion is not None:
            _add_exclusion_summary(graph, subject)


def _add_subject_acceptance_values(
    graph: Graph,
    *,
    subject: Node,
    excluded: bool,
    source_release: str,
    source_identity: str,
    run_id: str,
    representation_identity: str,
    publication_identity: str,
) -> None:
    status = "review-required-excluded" if excluded else "accepted-effective"
    values = (
        (vocab.ACCEPTANCE_STATUS, status),
        (vocab.ACCEPTANCE_SOURCE_RELEASE, source_release),
        (vocab.ACCEPTANCE_SOURCE_IDENTITY, source_identity),
        (vocab.ACCEPTANCE_RUN, run_id),
        (vocab.ACCEPTANCE_REPRESENTATION, representation_identity),
        (vocab.ACCEPTANCE_PUBLICATION, publication_identity),
    )
    for predicate, value in values:
        graph.add((subject, URIRef(predicate), RdfLiteral(value)))


def _add_exclusion_summary(graph: Graph, subject: Node) -> None:
    summary = "Review required — excluded from accepted effective projection"
    graph.add(
        (
            subject,
            URIRef(vocab.ACCEPTANCE_EXCLUSION_SUMMARY),
            RdfLiteral(summary),
        )
    )


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
    worklist_count: int = Field(gt=0)
    decomposed_count: int = Field(ge=0)
    atomic_noop_count: int = Field(ge=0)
    residual_count: int = Field(ge=0)
    residual_concept_codes: tuple[str, ...]
    semantic_excluded_count: int = Field(ge=0)
    unknown_outcome_count: int = Field(ge=0)
    source_occurrence_count: int = Field(ge=0)
    selected_occurrence_count: int = Field(ge=0)
    emitted_constituent_pair_count: int = Field(ge=0)
    complete_fact_count: int = Field(ge=0)
    projected_fact_count: int = Field(ge=0)
    projection_loss_count: int = Field(ge=0)
    projection_loss_rate: float = Field(ge=0, le=1)
    residual_precoordinated_count: int = Field(ge=0)
    residual_unknown_count: int = Field(ge=0)
    residual_unknown_rate: float = Field(ge=0, le=1)
    roundtrip_fidelity: float | None = Field(ge=0, le=1)
    exact_pair_precision: float = Field(ge=0, le=1)
    exact_pair_recall: float = Field(ge=0, le=1)
    common_pair_partition_agreement: float = Field(ge=0, le=1)
    full_partition_agreement: float = Field(ge=0, le=1)
    sme_include_rate: float = Field(ge=0, le=1)
    minted_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _arithmetic_is_bound(self) -> Self:
        _validate_candidate_outcomes(self)
        _validate_candidate_projection_metrics(self)
        _validate_candidate_residual_metrics(self)
        return self


def _validate_candidate_outcomes(metrics: CandidateMetrics) -> None:
    outcomes = (
        metrics.decomposed_count,
        metrics.atomic_noop_count,
        metrics.residual_count,
        metrics.semantic_excluded_count,
        metrics.unknown_outcome_count,
    )
    if sum(outcomes) != metrics.worklist_count:
        raise ValueError("candidate outcome counts do not sum to worklist count")
    canonical_residuals = tuple(sorted(set(metrics.residual_concept_codes)))
    valid_codes = all(
        re.fullmatch(_CODE, code) is not None for code in metrics.residual_concept_codes
    )
    if not (
        metrics.residual_concept_codes == canonical_residuals
        and valid_codes
        and len(metrics.residual_concept_codes) == metrics.residual_count
    ):
        raise ValueError("residual concept inventory differs from residual count")


def _validate_candidate_projection_metrics(metrics: CandidateMetrics) -> None:
    if metrics.projected_fact_count > metrics.complete_fact_count:
        raise ValueError("projected fact count exceeds complete fact count")
    expected_loss = metrics.complete_fact_count - metrics.projected_fact_count
    if metrics.projection_loss_count != expected_loss:
        raise ValueError("projection loss count differs from fact counts")
    expected_rate = (
        expected_loss / metrics.complete_fact_count
        if metrics.complete_fact_count
        else 0.0
    )
    if not math.isclose(metrics.projection_loss_rate, expected_rate, abs_tol=1e-12):
        raise ValueError("projection loss rate differs from fact counts")


def _validate_candidate_residual_metrics(metrics: CandidateMetrics) -> None:
    if metrics.residual_precoordinated_count > metrics.decomposed_count:
        raise ValueError("residual precoordination count exceeds decomposed count")
    if metrics.residual_unknown_count > metrics.decomposed_count:
        raise ValueError("residual unknown count exceeds decomposed count")
    expected_rate = (
        metrics.residual_unknown_count / metrics.decomposed_count
        if metrics.decomposed_count
        else 0.0
    )
    if not math.isclose(metrics.residual_unknown_rate, expected_rate, abs_tol=1e-12):
        raise ValueError("residual unknown rate differs from decomposed count")


class GateEvaluation(_StrictModel):
    status: Literal["passed", "failed", "blocked", "unavailable"]
    evidence_identity: str = Field(pattern=_SHA256)
    observation_identity: str = Field(pattern=_SHA256)


class CandidateGates(_StrictModel):
    primary_site_cardinality: GateEvaluation
    proposal_provenance: GateEvaluation
    projection_loss: GateEvaluation
    residual: GateEvaluation
    fidelity: GateEvaluation
    issue_274_detector: GateEvaluation
    m1_6_improvement: GateEvaluation
    gate_liveness: GateEvaluation
    verify_currency: GateEvaluation

    @property
    def all_passed(self) -> bool:
        return all(
            evaluation.status == "passed"
            for evaluation in (
                self.primary_site_cardinality,
                self.proposal_provenance,
                self.issue_274_detector,
                self.m1_6_improvement,
                self.gate_liveness,
            )
        )


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


class CandidateEvidence(_StrictModel):
    corpus_baseline_identity: str = Field(pattern=_SHA256)
    source_artifact_identity: str = Field(pattern=_SHA256)
    effective_artifact_evidence_identity: str = Field(pattern=_SHA256)
    policy_identity: str = Field(pattern=_SHA256)
    detector_identity: str = Field(pattern=_SHA256)
    r101_report_identity: str = Field(pattern=_SHA256)
    r101_qualification_identity: str = Field(pattern=_SHA256)
    primary_site_audit_identity: str = Field(pattern=_SHA256)
    proposal_registry_identity: str = Field(pattern=_SHA256)
    review_packet_identity: str = Field(pattern=_SHA256)
    review_decisions_identity: str = Field(pattern=_SHA256)
    gate_liveness_evidence_identity: str = Field(pattern=_SHA256)
    old_comparator_artifact_identity: str = Field(pattern=_SHA256)
    new_comparator_artifact_identity: str = Field(pattern=_SHA256)


class CorpusAcceptanceContent(_StrictModel):
    schema_version: Literal[1]
    scope: CandidateScope
    source: CandidateSource
    execution: CandidateExecution
    projection: CandidateProjection
    metrics: CandidateMetrics
    gates: CandidateGates
    r101_summary: R101CandidateSummary
    evidence: CandidateEvidence
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
    evidence: CandidateEvidence
    delta_classification: CorpusDeltaClassification
    review_required_exclusions: tuple[ReviewRequiredEffectiveExclusion, ...]
    publication_dry_run: PublicationDryRunEvidence
    human_authorization: NotRequestedHumanAuthorization
    candidate_content_identity: str = Field(pattern=_SHA256)
    candidate_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _ready_means_mechanically_complete(self) -> Self:
        ready = _candidate_is_ready(
            self.gates, self.delta_classification, self.publication_dry_run
        )
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
    gates: CandidateGates,
    classification: CorpusDeltaClassification,
    dry_run: PublicationDryRunEvidence,
) -> bool:
    return (
        gates.all_passed
        and not classification.unexplained_blockers
        and dry_run.status == "passed"
    )


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
            if _candidate_is_ready(content.gates, content.delta_classification, dry_run)
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
    logical_select_count = max(item.logical_select_count for item in observations)
    r82_select_count = max(item.select_once_r82_count for item in observations)
    _require_fanout_budgets(logical_select_count, r82_select_count, fanout)
    return FullStoreAcceptanceObservation(
        scope_root=corpus.scope_root,
        worklist_count=len(codes),
        exclusion_concepts=tuple(sorted(_TARGET_EXCLUSIONS)),
        official_source_assertion_count=official_count,
        highest_fanout_codes=fanout.concept_codes,
        logical_select_count=logical_select_count,
        r82_select_count=r82_select_count,
    )


def _require_fanout_budgets(
    logical_select_count: int,
    r82_select_count: int,
    fanout,  # type: ignore[no-untyped-def]
) -> None:
    within_budget = (
        logical_select_count <= fanout.logical_select_count_budget
        and r82_select_count <= fanout.select_once_r82_count_budget
    )
    if not within_budget:
        raise CorpusAcceptanceValidationError(
            "certified highest-fanout query budget exceeded"
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


_CERTIFIED_ACCEPTANCE_INPUTS = (
    "data/qlever-ncit/.ontoprism-ncit-candidate.json",
    "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
    "tmp/m1-6-current-full-corpus.ttl",
    "ontolib/tests/decomposition/golden/neoplasm-r101-v5-conservation.json.gz",
    "tmp/m1-6-r101-v5-comparator-qualification.json",
    "tmp/m1-6-prechange-v4-full-corpus.ttl",
    "tmp/m1-6-prechange-v4-corpus-baseline.json",
    "ontolib/src/ontolib/decomposition/data/normalized-group-policy.json",
    "tmp/m1-6-current-engine-evidence.json",
    "tmp/m1-6-machine-readiness.json",
    "tmp/m1-6-primary-site-audit.json",
    "ontolib/tests/decomposition/golden/proposal-registry.json",
    "evidence/group-review-packet-26.07d-schema3.json",
    "evidence/group-review-rationale-26.07d.md",
    "tmp/m1-6-group-review-decisions.json",
    "tmp/m1-6-verify-evidence.json",
    "ontolib/tests/decomposition/golden/neoplasm-highest-fanout.json",
)


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CorpusAcceptanceValidationError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise CorpusAcceptanceValidationError(f"{label} is not an object")
    return value


def _file_identity(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate(
    *,
    status: Literal["passed", "failed", "blocked", "unavailable"],
    evidence: Path,
    observation: object,
) -> GateEvaluation:
    return GateEvaluation(
        status=status,
        evidence_identity=_file_identity(evidence),
        observation_identity=_identity(observation),
    )


def _normalized_group_clear(readiness: dict[str, object]) -> bool:
    semantic_gate = readiness.get("semantic_gate")
    if not isinstance(semantic_gate, dict):
        return False
    entries = semantic_gate.get("entries", [])
    return any(_is_normalized_group_clear(item) for item in entries)


def _is_normalized_group_clear(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    return all(
        (
            item.get("kind") == "normalized-group-violation",
            item.get("status") == "clear",
        )
    )


def _verify_evidence_is_current(verify: dict[str, object], git_head: str) -> bool:
    return all(
        (
            verify.get("status") == "passed",
            verify.get("observed_exit_code") == 0,
            verify.get("git_head") == git_head,
        )
    )


def _strict_improvement_status(
    readiness: dict[str, object],
) -> Literal["passed", "failed"]:
    precision = _required_readiness_metric(readiness, "exact_pair_precision")
    recall = _required_readiness_metric(readiness, "exact_pair_recall")
    improvement = readiness.get("m1_6_improvement")
    declared = isinstance(improvement, dict) and all(
        (
            improvement.get("precision_improved") is True,
            improvement.get("recall_improved") is True,
            improvement.get("status") == "passed",
        )
    )
    improved = precision > 80 / 106 and recall > 80 / 153
    return "passed" if declared and improved else "failed"


def _proposal_registry_status(
    registry: ProposalRegistry, source_identity: str
) -> Literal["passed", "failed"]:
    return "passed" if registry.source_identity == source_identity else "failed"


def _proposal_gate_statuses(
    path: Path, source_identity: str
) -> tuple[Literal["passed", "failed"], bool]:
    try:
        registry = load_proposal_registry(path)
    except OSError, ValueError:
        return "failed", False
    status = _proposal_registry_status(registry, source_identity)
    liveness = (
        _proposal_registry_status(registry, "0" * 64) == "failed" and status == "passed"
    )
    return status, liveness


def _fidelity_gate_status(
    roundtrip_fidelity: float | None,
) -> Literal["passed", "failed", "unavailable"]:
    if roundtrip_fidelity is None:
        return "unavailable"
    return "passed" if roundtrip_fidelity >= _REPORT_ONLY_FIDELITY_TARGET else "failed"


def _candidate_gates(
    paths: dict[str, Path],
    *,
    git_head: str,
    source_identity: str,
    roundtrip_fidelity: float | None,
) -> CandidateGates:
    primary = _load_json_object(paths["primary_site_audit"], "primary-site audit")
    readiness = _load_json_object(paths["machine_readiness"], "machine readiness")
    verify = _load_json_object(paths["gate_liveness"], "gate-liveness evidence")
    violations = primary.get("cardinality_violations")
    primary_status = "passed" if violations == [] else "failed"
    quality = readiness.get("quality_target")
    fidelity_status = _fidelity_gate_status(roundtrip_fidelity)
    normalized_clear = _normalized_group_clear(readiness)
    verify_current = _verify_evidence_is_current(verify, git_head)
    proposal_status, liveness = _proposal_gate_statuses(
        paths["proposal_registry"], source_identity
    )
    improvement_status = _strict_improvement_status(readiness)
    return CandidateGates(
        primary_site_cardinality=_gate(
            status=primary_status,
            evidence=paths["primary_site_audit"],
            observation={"cardinality_violations": violations},
        ),
        proposal_provenance=_gate(
            status=proposal_status,
            evidence=paths["proposal_registry"],
            observation=_load_json_object(
                paths["proposal_registry"], "proposal registry"
            ),
        ),
        projection_loss=_gate(
            status="blocked",
            evidence=paths["current_evidence"],
            observation={
                "reason": "no candidate-bound projection-loss verdict is present"
            },
        ),
        residual=_gate(
            status="blocked",
            evidence=paths["baseline"],
            observation={
                "reason": "residual count is observed without an acceptance verdict"
            },
        ),
        fidelity=_gate(
            status=fidelity_status,
            evidence=paths["machine_readiness"],
            observation=quality,
        ),
        issue_274_detector=_gate(
            status="passed" if normalized_clear else "failed",
            evidence=paths["machine_readiness"],
            observation={"normalized_group_violation_clear": normalized_clear},
        ),
        m1_6_improvement=_gate(
            status=improvement_status,
            evidence=paths["machine_readiness"],
            observation={
                "strictly_better_than_precision": "80/106",
                "strictly_better_than_recall": "80/153",
                "status": improvement_status,
            },
        ),
        gate_liveness=_gate(
            status="passed" if liveness else "failed",
            evidence=paths["proposal_registry"],
            observation={
                "proposal_registry_accept_branch": proposal_status,
                "wrong_source_reject_branch": "rejected" if liveness else "unproven",
            },
        ),
        verify_currency=_gate(
            status="passed" if verify_current else "blocked",
            evidence=paths["gate_liveness"],
            observation={
                "evidence_git_head": verify.get("git_head"),
                "candidate_git_head": git_head,
                "exit_code": verify.get("observed_exit_code"),
            },
        ),
    )


class _ValidatedCandidateInputs(_StrictModel):
    manifest: NcitSiblingStoreManifest
    baseline: CorpusBaseline
    report: R101ConservationReport
    qualification: dict[str, object]
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...]
    classification: CorpusDeltaClassification
    source_artifact_identity: str


def _candidate_input_paths(root: Path) -> dict[str, Path]:
    names = (
        "source_manifest",
        "baseline",
        "artifact",
        "r101_report",
        "r101_qualification",
        "old_artifact",
        "old_baseline",
        "policy",
        "current_evidence",
        "machine_readiness",
        "primary_site_audit",
        "proposal_registry",
        "review_packet",
        "rationale",
        "review_decisions",
        "gate_liveness",
        "fanout",
    )
    return {
        name: root / relative
        for name, relative in zip(names, _CERTIFIED_ACCEPTANCE_INPUTS, strict=True)
    }


def _require_candidate_binding(condition: bool, message: str) -> None:
    if not condition:
        raise CorpusAcceptanceValidationError(message)


def _required_run_metric(run: RunSummary, field: str) -> int | float:
    value = getattr(run, field)
    if not isinstance(value, (int, float)):
        raise CorpusAcceptanceValidationError(
            f"persisted run metric {field} is unavailable"
        )
    return value


def _required_readiness_metric(readiness: dict[str, object], name: str) -> float:
    metrics = readiness.get("metrics")
    if not isinstance(metrics, dict):
        raise CorpusAcceptanceValidationError("machine readiness metrics are invalid")
    metric = metrics.get(name)
    if not isinstance(metric, dict):
        raise CorpusAcceptanceValidationError(
            f"machine readiness metric {name} is invalid"
        )
    fraction = metric.get("fraction")
    if not isinstance(fraction, dict):
        raise CorpusAcceptanceValidationError(
            f"machine readiness metric {name} is invalid"
        )
    value = _validated_fraction_value(fraction)
    if value is None:
        raise CorpusAcceptanceValidationError(
            f"machine readiness metric {name} arithmetic differs"
        )
    return value


def _validated_fraction_value(fraction: dict[str, object]) -> float | None:
    numerator = fraction.get("numerator")
    denominator = fraction.get("denominator")
    value = fraction.get("value")
    if not isinstance(numerator, int) or not isinstance(denominator, int):
        return None
    if denominator <= 0 or not isinstance(value, (int, float)):
        return None
    if not math.isclose(value, numerator / denominator, abs_tol=1e-12):
        return None
    return float(value)


def _candidate_metrics(
    baseline: CorpusBaseline,
    run: RunSummary,
    readiness: dict[str, object],
    residual_concept_codes: tuple[str, ...],
) -> CandidateMetrics:
    residual_unknown_count = int(
        _required_run_metric(run, "residual_precoordination_unknown_count")
    )
    return CandidateMetrics(
        worklist_count=baseline.worklist_count,
        decomposed_count=baseline.outcome_counts.decomposed,
        atomic_noop_count=baseline.outcome_counts.atomic_noop,
        residual_count=baseline.outcome_counts.residual,
        residual_concept_codes=residual_concept_codes,
        semantic_excluded_count=baseline.outcome_counts.semantic_excluded,
        unknown_outcome_count=baseline.outcome_counts.unknown,
        source_occurrence_count=baseline.source_occurrence_count,
        selected_occurrence_count=baseline.selected_occurrence_count,
        emitted_constituent_pair_count=baseline.emitted_constituent_pair_count,
        complete_fact_count=int(_required_run_metric(run, "complete_fact_count")),
        projected_fact_count=int(_required_run_metric(run, "projected_fact_count")),
        projection_loss_count=int(_required_run_metric(run, "projection_loss_count")),
        projection_loss_rate=float(_required_run_metric(run, "projection_loss_rate")),
        residual_precoordinated_count=int(
            _required_run_metric(run, "residual_precoordinated_count")
        ),
        residual_unknown_count=residual_unknown_count,
        residual_unknown_rate=(
            residual_unknown_count / baseline.outcome_counts.decomposed
        ),
        roundtrip_fidelity=run.roundtrip_fidelity,
        exact_pair_precision=_required_readiness_metric(
            readiness, "exact_pair_precision"
        ),
        exact_pair_recall=_required_readiness_metric(readiness, "exact_pair_recall"),
        common_pair_partition_agreement=_required_readiness_metric(
            readiness, "common_pair_partition_agreement"
        ),
        full_partition_agreement=_required_readiness_metric(
            readiness, "full_partition_agreement"
        ),
        sme_include_rate=_required_readiness_metric(readiness, "sme_include_rate"),
        minted_count=baseline.minted_count,
    )


def _validate_candidate_inputs(
    paths: dict[str, Path],
) -> _ValidatedCandidateInputs:
    manifest = validate_ncit_sibling_manifest(paths["source_manifest"])
    baseline = load_corpus_baseline(paths["baseline"])
    _require_candidate_binding(
        all(
            (
                baseline.scope_root == "C3262",
                baseline.worklist_count == _CERTIFIED_WORKLIST_COUNT,
            )
        ),
        "certified C3262 baseline differs",
    )
    _require_candidate_binding(
        baseline.source_identity == manifest.source_identity,
        "baseline source identity differs",
    )
    source_artifact_identity = _file_identity(paths["artifact"])
    _require_candidate_binding(
        source_artifact_identity == baseline.artifact_identity,
        "certified artifact identity differs",
    )
    report = load_r101_conservation_report(paths["r101_report"])
    _require_candidate_binding(
        report.new_run_id == baseline.run_id,
        "R101 report run binding differs",
    )
    _require_candidate_binding(
        report.new_representation_identity == baseline.representation_identity,
        "R101 report representation binding differs",
    )
    _require_candidate_binding(
        report.source_identity == baseline.source_identity,
        "R101 report source binding differs",
    )
    qualification = _load_json_object(paths["r101_qualification"], "R101 qualification")
    _require_candidate_binding(
        qualification.get("qualification_identity")
        == report.comparator_qualification_identity,
        "R101 qualification binding differs",
    )
    exclusions = build_review_required_exclusions(
        paths["review_packet"],
        paths["rationale"],
        paths["artifact"],
    )
    policy_identity = _file_identity(paths["policy"])
    try:
        proposal_registry = load_proposal_registry(paths["proposal_registry"])
    except (OSError, ValueError) as exc:
        raise CorpusAcceptanceValidationError("proposal registry is invalid") from exc
    classification = classify_corpus_delta(
        report,
        routing_policy_identity=policy_identity,
        proposal_registry=proposal_registry,
    )
    fanout = load_fanout_baseline(
        paths["fanout"],
        expected_source_identity=baseline.source_identity,
        expected_release=baseline.ontology_release,
    )
    _require_candidate_binding(
        fanout.scanned_concept_count == baseline.worklist_count,
        "fanout baseline scope differs",
    )
    return _ValidatedCandidateInputs(
        manifest=manifest,
        baseline=baseline,
        report=report,
        qualification=qualification,
        exclusions=exclusions,
        classification=classification,
        source_artifact_identity=source_artifact_identity,
    )


async def generate_c3262_acceptance_candidate(
    root: Path, *, git_head: str | None = None
) -> dict[str, object]:
    """Generate the fixed certified candidate; arbitrary run/count claims are absent."""
    _require_certified_acceptance_inputs(root)
    if git_head is None or re.fullmatch(r"[0-9a-f]{40}", git_head) is None:
        raise CorpusAcceptanceValidationError("candidate generator git head is invalid")
    paths = _candidate_input_paths(root)
    validated = _validate_candidate_inputs(paths)
    (
        manifest,
        baseline,
        report,
        qualification,
        exclusions,
        classification,
        source_artifact_identity,
    ) = (
        validated.manifest,
        validated.baseline,
        validated.report,
        validated.qualification,
        validated.exclusions,
        validated.classification,
        validated.source_artifact_identity,
    )
    with tempfile.TemporaryDirectory(
        prefix="c3262-acceptance-", dir=root / "tmp"
    ) as temporary:
        staging = Path(temporary)
        effective_path = staging / "effective-c3262.ttl"
        effective = build_effective_artifact(
            source_artifact=paths["artifact"],
            destination=effective_path,
            exclusions=exclusions,
        )
        evidence = CandidateEvidence(
            corpus_baseline_identity=baseline.baseline_identity,
            source_artifact_identity=source_artifact_identity,
            effective_artifact_evidence_identity=_identity(
                effective.model_dump(mode="json")
            ),
            policy_identity=_file_identity(paths["policy"]),
            detector_identity=baseline.detector_identity,
            r101_report_identity=report.report_identity,
            r101_qualification_identity=str(qualification["qualification_identity"]),
            primary_site_audit_identity=_file_identity(paths["primary_site_audit"]),
            proposal_registry_identity=_file_identity(paths["proposal_registry"]),
            review_packet_identity=_file_identity(paths["review_packet"]),
            review_decisions_identity=_file_identity(paths["review_decisions"]),
            gate_liveness_evidence_identity=_file_identity(paths["gate_liveness"]),
            old_comparator_artifact_identity=_file_identity(paths["old_artifact"]),
            new_comparator_artifact_identity=source_artifact_identity,
        )
        settings = __import__(
            "backend.config", fromlist=["get_settings"]
        ).get_settings()
        database = __import__("backend.db", fromlist=["make_engine"])
        provenance_module = __import__(
            "ontolib.decomposition.provenance", fromlist=["ProvenanceStore"]
        )
        client_module = __import__(
            "ontolib.terminologies.ncit.client", fromlist=["ncit_sparql_client"]
        )
        engine = database.make_engine(settings.database_url)
        store = provenance_module.ProvenanceStore(database.make_sessionmaker(engine))
        try:
            run = await store.completed_run_for_evidence(baseline.run_id)
            run_summary = await store.get_run(baseline.run_id)
            if run_summary is None:
                raise CorpusAcceptanceValidationError("persisted run summary is absent")
            aggregate = await store.corpus_baseline_aggregate(baseline.run_id)
            outcomes = await store.work_item_outcomes(baseline.run_id)
            residual_concept_codes = tuple(
                sorted(
                    item.concept_code for item in outcomes if item.outcome == "residual"
                )
            )
            readiness = _load_json_object(
                paths["machine_readiness"], "machine readiness"
            )
            gates = _candidate_gates(
                paths,
                git_head=git_head,
                source_identity=baseline.source_identity,
                roundtrip_fidelity=run_summary.roundtrip_fidelity,
            )
            content_payload = {
                "schema_version": 1,
                "scope": CandidateScope(
                    root="C3262",
                    version="stated-genus-subclass-v1",
                    worklist_count=_CERTIFIED_WORKLIST_COUNT,
                    worklist_identity=_identity(run.fingerprint.worklist),
                ),
                "source": CandidateSource(
                    release=manifest.ontology_version,
                    source_identity=manifest.source_identity,
                    stated_artifact_identity=manifest.stated_artifact.artifact_identity,
                    inferred_artifact_identity=(
                        manifest.inferred_artifact.artifact_identity
                    ),
                    sibling_manifest_identity=_file_identity(paths["source_manifest"]),
                    extraction_plane="official-stated",
                ),
                "execution": CandidateExecution(
                    run_id=baseline.run_id,
                    run_fingerprint_identity=baseline.run_fingerprint_identity,
                    git_head=git_head,
                    policy_identity=evidence.policy_identity,
                ),
                "projection": CandidateProjection(
                    artifact_sha256=effective.effective_artifact_identity,
                    representation_identity=effective.effective_artifact_identity,
                    served_semantic_projection_identity=_served_projection_identity(
                        effective
                    ),
                    no_equivalence=True,
                ),
                "metrics": _candidate_metrics(
                    baseline, run_summary, readiness, residual_concept_codes
                ),
                "gates": gates,
                "r101_summary": _r101_candidate_summary(report),
                "evidence": evidence,
                "delta_classification": classification,
                "review_required_exclusions": exclusions,
            }
            content_identity = _identity(_jsonable(content_payload))
            content = CorpusAcceptanceContent.model_validate(
                {**content_payload, "content_identity": content_identity}
            )
            async with client_module.ncit_sparql_client(
                settings.ncit_sparql_url
            ) as graph:
                dry_run = await dry_run_corpus_publication(
                    candidate_content_identity=content.content_identity,
                    run_id=baseline.run_id,
                    source_identity=baseline.source_identity,
                    planes=PublicationPlaneBinding(
                        official_persisted_representation_identity=(
                            baseline.representation_identity
                        ),
                        effective_representation_identity=(
                            effective.effective_artifact_identity
                        ),
                    ),
                    artifact=effective_path,
                    destination_graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
                    expected_codes=aggregate.decomposed_codes,
                    expected_worklist_count=baseline.worklist_count,
                    graph=graph,
                    provenance=store,
                )
        finally:
            await database.dispose_engine(engine)
        candidate = finalize_corpus_acceptance_candidate(content, dry_run)
        candidate_path = staging / "candidate.json"
        dry_run_path = staging / "publication-dry-run.json"
        decision_path = staging / "human-decision.json"
        candidate_path.write_text(
            json.dumps(candidate.model_dump(mode="json"), sort_keys=True, indent=2)
            + "\n"
        )
        dry_run_path.write_text(
            json.dumps(dry_run.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
        )
        write_pending_human_acceptance_decision(
            decision_path,
            candidate_identity=candidate.candidate_identity,
            publication_dry_run_identity=dry_run.evidence_identity,
        )
        generator_identity = hashlib.sha256(
            Path(__file__).read_bytes()
            + (root / "scripts/validation/run_agent_replay.py").read_bytes()
        ).hexdigest()
        artifact_manifest = publish_generation(
            artifacts_root=root / "tmp/artifacts/v1/generations",
            family="c3262-corpus-acceptance-candidate",
            generation_id=candidate.candidate_identity,
            run_id=baseline.run_id,
            artifact_sources={
                "artifacts/candidate.json": candidate_path,
                "artifacts/effective-c3262.ttl": effective_path,
                "artifacts/human-decision.json": decision_path,
                "artifacts/publication-dry-run.json": dry_run_path,
            },
            parents=(),
            generator=GeneratorBinding(
                identity=f"sha256:{generator_identity}",
                command=(
                    "pdm",
                    "run",
                    "agent-replay",
                    "generate-c3262-acceptance-candidate",
                ),
            ),
            sources=tuple(
                SourceIdentity(
                    name=name.replace("_", "-"),
                    identity=f"sha256:{_file_identity(path)}",
                )
                for name, path in sorted(paths.items())
            ),
            retention=RetentionBinding(
                retention_class="human-acceptance-candidate",
                owner="decomposition",
                expires_at=None,
            ),
        )
    generation = (
        root
        / "tmp/artifacts/v1/generations/c3262-corpus-acceptance-candidate"
        / candidate.candidate_identity
    )
    return {
        "candidate_manifest_path": str(generation / "manifest.json"),
        "candidate_manifest_identity": artifact_manifest.manifest_identity,
        "candidate_identity": candidate.candidate_identity,
        "candidate_status": candidate.status,
        "category_counts": candidate.delta_classification.category_counts,
        "unexplained_blocker_count": len(
            candidate.delta_classification.unexplained_blockers
        ),
        "effective_artifact_identity": effective.effective_artifact_identity,
        "removed_from_effective_count": effective.removed_pair_count,
        "review_required_non_emitted_count": effective.non_emitted_pair_count,
        "dry_run_identity": dry_run.evidence_identity,
        "dry_run_status": dry_run.status,
        "postgres_before_identity": dry_run.postgres_before_identity,
        "postgres_after_identity": dry_run.postgres_after_identity,
        "qlever_before_identity": dry_run.qlever_before_identity,
        "qlever_after_identity": dry_run.qlever_after_identity,
    }


def _require_certified_acceptance_inputs(root: Path) -> None:
    for relative in _CERTIFIED_ACCEPTANCE_INPUTS:
        if not (root / relative).is_file():
            raise CorpusAcceptanceValidationError(
                f"certified acceptance input is absent: {relative}"
            )


def _served_projection_identity(effective: EffectiveArtifactEvidence) -> str:
    return _identity(
        {
            "artifact": effective.effective_artifact_identity,
            "removed_pairs": [
                item.model_dump(mode="json") for item in effective.removed_pairs
            ],
            "non_emitted_pairs": [
                item.model_dump(mode="json") for item in effective.non_emitted_pairs
            ],
        }
    )


def _r101_candidate_summary(report: R101ConservationReport) -> R101CandidateSummary:
    counts = report.counts
    payload = {
        "occurrence_count": counts.total,
        "routed_or_collapsed_or_suppressed_count": counts.total - counts.unresolved,
        "unresolved_count": counts.unresolved,
        "causal_attribution": "prohibited",
    }
    return R101CandidateSummary.model_validate(
        {**payload, "occurrence_partition_identity": _identity(payload)}
    )
