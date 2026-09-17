"""Identity-bound mechanical acceptance for the certified C3262 corpus.

This module can prepare evidence for human authorization.  It cannot authorize or
publish a corpus, and it never mutates PostgreSQL, QLever, or the official NCIt source.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import tempfile
from collections import Counter
from operator import attrgetter
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from rdflib import Graph, URIRef
from rdflib import Literal as RdfLiteral
from rdflib.term import BNode, Node
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
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.decomposition.proposal_registry import (
    ConceptProposal,
    Proposal,
    ProposalRegistry,
    load_proposal_registry,
)
from ontolib.decomposition.proposal_registry_migration import (
    ProposalRegistryMigrationError,
    load_proposal_registry_migration_envelope,
    validate_migrated_proposal_registry,
)
from ontolib.decomposition.provenance_models import (
    CompletedRunForEvidence,
    CorpusBaselineAggregate,
    PublicationMarkerSnapshot,
    ResidualFillerClassification,
    RunSummary,
    WorkItemOutcome,
)
from ontolib.decomposition.publication import (
    MachinePublicationAuthorization,
    PublicationGraphClient,
    PublicationMarker,
    PublicationValidationError,
    _issue_machine_publication_authorization,
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


class CertifiedSourceBinding(_StrictModel):
    """Exact certified NCIt release and bytes supporting acceptance evidence."""

    release: str = Field(min_length=1)
    source_manifest_identity: str = Field(pattern=_SHA256)
    source_identity: str = Field(pattern=_SHA256)
    stated_artifact_identity: str = Field(pattern=_SHA256)
    certification: Literal["expert-curated-ncit-release"]


class PersistedAssertionEvidence(_StrictModel):
    """Persisted source occurrence and named transformation for one projection."""

    concept_code: str = Field(pattern=_CODE)
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=_CODE)
    source_fact_ids: tuple[str, ...] = Field(min_length=1)
    source_occurrence_ids: tuple[str, ...]
    transformation_rule: str = Field(min_length=1)
    transformation_policy_identity: str = Field(pattern=_SHA256)
    applicability_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _canonical_evidence(self) -> Self:
        _require_canonical_identities("source fact", self.source_fact_ids)
        _require_canonical_identities("source occurrence", self.source_occurrence_ids)
        if self.axis.startswith("op:") and not self.source_occurrence_ids:
            raise ValueError("routed assertion requires exact source occurrences")
        return self


class CanonicalSemanticAssertion(_StrictModel):
    concept_code: str = Field(pattern=_CODE)
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=r"^(?:C[0-9]+|MINT-[0-9a-f]{12})$")
    axis_source: str = Field(min_length=1)
    source_roles: tuple[str, ...]
    source_fact_ids: tuple[str, ...]
    source_occurrence_ids: tuple[str, ...]
    most_specific: bool
    needs_review: bool
    assertion_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        _require_canonical_identities("source fact", self.source_fact_ids)
        _require_canonical_identities("source occurrence", self.source_occurrence_ids)
        expected = _identity(
            self.model_dump(mode="json", exclude={"assertion_identity"})
        )
        if self.assertion_identity != expected:
            raise ValueError("canonical assertion identity differs")
        return self


AssertionExclusionReason = Literal[
    "review-required",
    "evidence-absent",
    "evidence-contradictory",
    "unresolved-ambiguity",
    "proposal-quarantined",
    "historical-dispute",
]


class ExcludedSemanticAssertion(_StrictModel):
    assertion: CanonicalSemanticAssertion
    reason: AssertionExclusionReason
    official_source_preserved: Literal[True]


class ConceptExclusionDisposition(_StrictModel):
    concept_code: str = Field(pattern=_CODE)
    reason: Literal["unknown-outcome", "residual", "residual-unknown"]
    official_source_preserved: Literal[True]


class QualifyingAssertionEvidence(_StrictModel):
    assertion_identity: str = Field(pattern=_SHA256)
    source: CertifiedSourceBinding
    source_fact_ids: tuple[str, ...] = Field(min_length=1)
    source_occurrence_ids: tuple[str, ...]
    evidence_kind: Literal["certified-source-and-deterministic-transformation"]
    transformation_rule: str = Field(min_length=1)
    transformation_policy_identity: str = Field(pattern=_SHA256)
    applicability_identity: str = Field(pattern=_SHA256)


def _require_canonical_partition_ids(label: str, values: tuple[str, ...]) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} closure must be canonical and unique")


def _require_exact_partition_relationships(
    included: tuple[str, ...],
    excluded: tuple[str, ...],
    evidence: tuple[str, ...],
) -> None:
    if set(included) & set(excluded):
        raise ValueError("included and excluded assertion closures overlap")
    if evidence != included:
        raise ValueError("included assertion evidence ledger is not exact")


class AssertionEvidenceClosure(_StrictModel):
    source: CertifiedSourceBinding
    source_artifact_identity: str = Field(pattern=_SHA256)
    effective_artifact_identity: str = Field(pattern=_SHA256)
    rdf_parser_inventory_identity: str = Field(pattern=_SHA256)
    fast_parser_inventory_identity: str = Field(pattern=_SHA256)
    included_assertion_closure: tuple[CanonicalSemanticAssertion, ...]
    excluded_assertion_closure: tuple[ExcludedSemanticAssertion, ...]
    concept_exclusions: tuple[ConceptExclusionDisposition, ...]
    evidence_ledger: tuple[QualifyingAssertionEvidence, ...]
    unresolved_included_assertion_ids: tuple[str, ...]
    contradictory_included_assertion_ids: tuple[str, ...]
    ambiguous_included_assertion_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _is_an_exact_partition(self) -> Self:
        if self.rdf_parser_inventory_identity != self.fast_parser_inventory_identity:
            raise ValueError("parser inventories differ")
        included = tuple(
            item.assertion_identity for item in self.included_assertion_closure
        )
        excluded = tuple(
            item.assertion.assertion_identity
            for item in self.excluded_assertion_closure
        )
        evidence = tuple(item.assertion_identity for item in self.evidence_ledger)
        partitions = (
            ("included assertion", included),
            ("excluded assertion", excluded),
            ("evidence assertion", evidence),
        )
        for label, values in partitions:
            _require_canonical_partition_ids(label, values)
        _require_exact_partition_relationships(included, excluded, evidence)
        return self

    @property
    def closure_identity(self) -> str:
        return _identity(self.model_dump(mode="json"))

    @property
    def evidence_ledger_identity(self) -> str:
        return _identity(
            [item.model_dump(mode="json") for item in self.evidence_ledger]
        )


class EvidenceAmbiguityReport(_StrictModel):
    unresolved_assertion_ids: tuple[str, ...]
    contradictory_assertion_ids: tuple[str, ...]
    ambiguous_assertion_ids: tuple[str, ...]
    report_identity: str = Field(pattern=_SHA256)

    @classmethod
    def build(
        cls,
        *,
        unresolved_assertion_ids: tuple[str, ...],
        contradictory_assertion_ids: tuple[str, ...],
        ambiguous_assertion_ids: tuple[str, ...],
    ) -> EvidenceAmbiguityReport:
        payload = {
            "unresolved_assertion_ids": tuple(sorted(set(unresolved_assertion_ids))),
            "contradictory_assertion_ids": tuple(
                sorted(set(contradictory_assertion_ids))
            ),
            "ambiguous_assertion_ids": tuple(sorted(set(ambiguous_assertion_ids))),
        }
        return cls(**payload, report_identity=_identity(payload))

    @classmethod
    def from_closure(cls, closure: AssertionEvidenceClosure) -> EvidenceAmbiguityReport:
        return cls.build(
            unresolved_assertion_ids=closure.unresolved_included_assertion_ids,
            contradictory_assertion_ids=closure.contradictory_included_assertion_ids,
            ambiguous_assertion_ids=closure.ambiguous_included_assertion_ids,
        )

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        for label, values in (
            ("unresolved", self.unresolved_assertion_ids),
            ("contradictory", self.contradictory_assertion_ids),
            ("ambiguous", self.ambiguous_assertion_ids),
        ):
            _require_canonical_identities(label, values)
        expected = _identity(self.model_dump(mode="json", exclude={"report_identity"}))
        if self.report_identity != expected:
            raise ValueError("ambiguity report identity differs")
        return self


class MachineEvidenceAcceptance(_StrictModel):
    status: Literal["machine-evidence-accepted"]
    candidate_identity: str = Field(pattern=_SHA256)
    effective_artifact_identity: str = Field(pattern=_SHA256)
    included_closure_identity: str = Field(pattern=_SHA256)
    exclusions_identity: str = Field(pattern=_SHA256)
    evidence_ledger_identity: str = Field(pattern=_SHA256)
    policy_identity: str = Field(pattern=_SHA256)
    dry_run_identity: str = Field(pattern=_SHA256)
    ambiguity_report_identity: str = Field(pattern=_SHA256)
    unresolved_included_count: Literal[0]
    contradiction_included_count: Literal[0]
    ambiguity_included_count: Literal[0]
    nci_adoption_claimed: Literal[False]
    acceptance_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        expected = _identity(
            self.model_dump(mode="json", exclude={"acceptance_identity"})
        )
        if self.acceptance_identity != expected:
            raise ValueError("machine acceptance identity differs")
        return self


class HumanEvidenceAcceptance(_StrictModel):
    status: Literal["accepted"]
    escalated_assertion_ids: tuple[str, ...] = Field(min_length=1)
    attestation_artifact_identity: str = Field(pattern=_SHA256)
    acceptance_identity: str = Field(pattern=_SHA256)
    nci_adoption_claimed: Literal[False]


class RejectedHumanEvidenceAcceptance(_StrictModel):
    status: Literal["rejected"]
    escalated_assertion_ids: tuple[str, ...] = Field(min_length=1)
    attestation_artifact_identity: str = Field(pattern=_SHA256)
    rejection_identity: str = Field(pattern=_SHA256)
    nci_adoption_claimed: Literal[False]


def build_machine_evidence_acceptance(
    *,
    candidate_identity: str,
    closure: AssertionEvidenceClosure,
    exclusions_identity: str,
    evidence_ledger_identity: str,
    policy_identity: str,
    dry_run_identity: str,
    ambiguity: EvidenceAmbiguityReport,
) -> MachineEvidenceAcceptance:
    """Accept only a fully evidenced included closure; exclusions may remain."""
    unresolved = ambiguity.unresolved_assertion_ids
    contradictions = ambiguity.contradictory_assertion_ids
    ambiguous = ambiguity.ambiguous_assertion_ids
    if unresolved or contradictions or ambiguous:
        raise CorpusAcceptanceValidationError(
            "included closure contains unresolved, contradictory, or ambiguous evidence"
        )
    if evidence_ledger_identity != closure.evidence_ledger_identity:
        raise CorpusAcceptanceValidationError("evidence ledger identity differs")
    payload = {
        "status": "machine-evidence-accepted",
        "candidate_identity": candidate_identity,
        "effective_artifact_identity": closure.effective_artifact_identity,
        "included_closure_identity": closure.closure_identity,
        "exclusions_identity": exclusions_identity,
        "evidence_ledger_identity": evidence_ledger_identity,
        "policy_identity": policy_identity,
        "dry_run_identity": dry_run_identity,
        "ambiguity_report_identity": ambiguity.report_identity,
        "unresolved_included_count": 0,
        "contradiction_included_count": 0,
        "ambiguity_included_count": 0,
        "nci_adoption_claimed": False,
    }
    return MachineEvidenceAcceptance.model_validate(
        {**payload, "acceptance_identity": _identity(payload)}
    )


def issue_machine_publication_token(
    acceptance: MachineEvidenceAcceptance,
) -> MachinePublicationAuthorization:
    return _issue_machine_publication_authorization(
        acceptance_identity=acceptance.acceptance_identity,
        exclusions_identity=acceptance.exclusions_identity,
    )


def require_machine_publication_authorization(
    *,
    token: MachinePublicationAuthorization | None,
    acceptance: MachineEvidenceAcceptance,
    closure: AssertionEvidenceClosure,
) -> None:
    """Fail before any write unless the opaque capability binds exact accepted bytes."""
    if not isinstance(token, MachinePublicationAuthorization):
        raise CorpusAcceptanceValidationError(
            "publication requires typed authorization"
        )
    if (
        token.acceptance_identity != acceptance.acceptance_identity
        or token.exclusions_identity != acceptance.exclusions_identity
    ):
        raise CorpusAcceptanceValidationError("publication token binding differs")
    if (
        acceptance.effective_artifact_identity != closure.effective_artifact_identity
        or acceptance.included_closure_identity != closure.closure_identity
        or acceptance.evidence_ledger_identity != closure.evidence_ledger_identity
    ):
        raise CorpusAcceptanceValidationError(
            "publication acceptance closure binding differs"
        )


class R101OccurrenceDispositionBinding(_StrictModel):
    source_occurrence_id: str = Field(pattern=_SHA256)
    source_fact_id: str = Field(pattern=_SHA256)
    disposition: str = Field(min_length=1)
    disposition_reason: str = Field(min_length=1)
    proof_identity: str = Field(pattern=_SHA256)
    r82_path: tuple[object, ...]
    path_identity: str | None = Field(default=None, pattern=_SHA256)


class R101OccurrenceClosure(_StrictModel):
    occurrences: tuple[R101OccurrenceDispositionBinding, ...]
    unresolved_occurrence_ids: tuple[str, ...]
    closure_identity: str = Field(pattern=_SHA256)


def _r101_occurrence_binding(item) -> R101OccurrenceDispositionBinding:  # type: ignore[no-untyped-def]
    path = tuple(edge.model_dump(mode="json") for edge in item.r82_path)
    return R101OccurrenceDispositionBinding(
        source_occurrence_id=item.occurrence_id,
        source_fact_id=item.source_fact_id,
        disposition=item.disposition,
        disposition_reason=item.disposition_reason,
        proof_identity=item.proof_id,
        r82_path=path,
        path_identity=_identity(path) if path else None,
    )


def build_r101_occurrence_closure(
    report: R101ConservationReport,
) -> R101OccurrenceClosure:
    """Bind every existing ledger occurrence instead of restating summary counts."""
    occurrences = tuple(
        sorted(
            (_r101_occurrence_binding(item) for item in report.occurrences),
            key=attrgetter("source_occurrence_id"),
        )
    )
    unresolved = tuple(
        item.source_occurrence_id
        for item in occurrences
        if item.disposition == "unresolved"
    )
    payload = {
        "occurrences": tuple(item.model_dump(mode="json") for item in occurrences),
        "unresolved_occurrence_ids": unresolved,
    }
    return R101OccurrenceClosure(
        occurrences=occurrences,
        unresolved_occurrence_ids=unresolved,
        closure_identity=_identity(payload),
    )


_FAST_ASSERTION_LINE = re.compile(
    rf"^<{re.escape(NCIT_NS)}(?P<concept>C[0-9]+)> "
    rf"<{re.escape(vocab.HAS_CONSTITUENT)}>\s+"
    rf"\[<{re.escape(vocab.AXIS)}> <(?P<axis>[^>]+)> ; "
    rf"<{re.escape(vocab.FILLER)}> <(?P<filler>[^>]+)>"
)


def _axis_name(iri: str) -> str:
    return (
        f"op:{iri.removeprefix(vocab.ONTOPRISM_NS)}"
        if iri.startswith(vocab.ONTOPRISM_NS)
        else iri.removeprefix(NCIT_NS)
    )


def _filler_name(iri: str) -> str:
    if iri.startswith(NCIT_NS):
        return iri.removeprefix(NCIT_NS)
    if iri.startswith(vocab.ONTOPRISM_NS):
        return iri.removeprefix(vocab.ONTOPRISM_NS)
    raise CorpusAcceptanceValidationError("constituent filler is outside NCIt output")


def _fast_assertion_coordinates(
    payload: bytes,
) -> tuple[tuple[str, str, str], ...]:
    coordinates: list[tuple[str, str, str]] = []
    for line in payload.splitlines():
        try:
            match = _FAST_ASSERTION_LINE.match(line.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise CorpusAcceptanceValidationError(
                "effective source artifact is not UTF-8"
            ) from exc
        if match is None:
            continue
        coordinates.append(
            (
                match.group("concept"),
                _axis_name(match.group("axis")),
                _filler_name(match.group("filler")),
            )
        )
    canonical = tuple(sorted(coordinates))
    if len(canonical) != len(set(canonical)):
        raise CorpusAcceptanceValidationError(
            "duplicate semantic constituent assertion"
        )
    return canonical


def _single_object(graph: Graph, node: Node, predicate: str, label: str) -> Node:
    values = tuple(graph.objects(node, URIRef(predicate)))
    if len(values) != 1:
        raise CorpusAcceptanceValidationError(
            f"constituent has invalid {label} cardinality"
        )
    return cast("Node", values[0])


def _rdf_assertion_row(
    graph: Graph, subject: Node, constituent: Node
) -> dict[str, object]:
    subject_iri = str(subject)
    if not isinstance(subject, URIRef) or not subject_iri.startswith(NCIT_NS):
        raise CorpusAcceptanceValidationError(
            "constituent subject is not an NCIt concept"
        )
    if not isinstance(constituent, BNode):
        raise CorpusAcceptanceValidationError("constituent node is not anonymous")
    axis = str(_single_object(graph, constituent, vocab.AXIS, "axis"))
    filler = str(_single_object(graph, constituent, vocab.FILLER, "filler"))
    axis_sources = tuple(graph.objects(constituent, URIRef(vocab.AXIS_SOURCE)))
    if len(axis_sources) != 1:
        raise CorpusAcceptanceValidationError(
            "constituent has invalid axis source cardinality"
        )
    source_roles = tuple(
        sorted(
            str(value).removeprefix(NCIT_NS)
            for value in graph.objects(constituent, URIRef(vocab.SOURCE_ROLE))
        )
    )
    source_facts = tuple(
        sorted(
            str(value).removeprefix(vocab.DEFINITION_FACT_NS).rsplit("/", 1)[-1]
            for value in graph.objects(
                constituent, URIRef(vocab.SOURCE_DEFINITION_FACT)
            )
        )
    )
    return {
        "concept_code": subject_iri.removeprefix(NCIT_NS),
        "axis": _axis_name(axis),
        "filler_code": _filler_name(filler),
        "axis_source": str(axis_sources[0]),
        "source_roles": source_roles,
        "source_fact_ids": source_facts,
        "most_specific": RdfLiteral(True)
        in graph.objects(constituent, URIRef(vocab.MOST_SPECIFIC)),
        "needs_review": RdfLiteral(True)
        in graph.objects(constituent, URIRef(vocab.NEEDS_REVIEW)),
    }


def _rdf_assertion_rows(
    payload: bytes,
) -> tuple[dict[str, object], ...]:
    graph = Graph()
    try:
        graph.parse(data=payload, format="turtle")
    except Exception as exc:
        raise CorpusAcceptanceValidationError(
            "effective source artifact is not valid Turtle"
        ) from exc
    rows = [
        _rdf_assertion_row(graph, subject, constituent)
        for subject, constituent in graph.subject_objects(URIRef(vocab.HAS_CONSTITUENT))
    ]
    rows.sort(
        key=lambda item: (
            str(item["concept_code"]),
            str(item["axis"]),
            str(item["filler_code"]),
        )
    )
    coordinates = [
        (str(row["concept_code"]), str(row["axis"]), str(row["filler_code"]))
        for row in rows
    ]
    if len(coordinates) != len(set(coordinates)):
        raise CorpusAcceptanceValidationError(
            "duplicate semantic constituent assertion"
        )
    return tuple(rows)


def _assertion_from_row(
    row: dict[str, object],
    persisted: PersistedAssertionEvidence | None,
) -> CanonicalSemanticAssertion:
    source_facts = cast("tuple[str, ...]", row["source_fact_ids"])
    if persisted is not None and persisted.source_fact_ids != source_facts:
        raise CorpusAcceptanceValidationError(
            "persisted and RDF source fact inventories differ"
        )
    payload = {
        **row,
        "source_occurrence_ids": (
            persisted.source_occurrence_ids if persisted is not None else ()
        ),
    }
    return CanonicalSemanticAssertion.model_validate(
        {**payload, "assertion_identity": _identity(payload)}
    )


def _evidence_for_assertion(
    assertion: CanonicalSemanticAssertion,
    persisted: PersistedAssertionEvidence,
    source: CertifiedSourceBinding,
) -> QualifyingAssertionEvidence:
    return QualifyingAssertionEvidence(
        assertion_identity=assertion.assertion_identity,
        source=source,
        source_fact_ids=persisted.source_fact_ids,
        source_occurrence_ids=persisted.source_occurrence_ids,
        evidence_kind="certified-source-and-deterministic-transformation",
        transformation_rule=persisted.transformation_rule,
        transformation_policy_identity=persisted.transformation_policy_identity,
        applicability_identity=persisted.applicability_identity,
    )


def _closure_exclusion_reason(
    assertion: CanonicalSemanticAssertion,
    persisted: PersistedAssertionEvidence | None,
    concept_exclusions: dict[str, ConceptExclusionDisposition],
) -> AssertionExclusionReason | None:
    if assertion.filler_code.startswith("MINT-"):
        return "proposal-quarantined"
    if assertion.concept_code in concept_exclusions:
        return "unresolved-ambiguity"
    if assertion.needs_review:
        return "review-required"
    if persisted is None or not assertion.source_fact_ids:
        return "evidence-absent"
    return None


def _filter_assertion_lines(
    payload: bytes,
    excluded_coordinates: set[tuple[str, str, str]],
    excluded_concepts: set[str],
) -> bytes:
    retained: list[bytes] = []
    excluded_subject_prefixes = tuple(
        f"<{NCIT_NS}{code}> ".encode() for code in sorted(excluded_concepts)
    )
    for line in payload.splitlines(keepends=True):
        if excluded_subject_prefixes and line.startswith(excluded_subject_prefixes):
            continue
        match = _FAST_ASSERTION_LINE.match(line.decode("utf-8"))
        if match is None:
            retained.append(line)
            continue
        coordinate = (
            match.group("concept"),
            _axis_name(match.group("axis")),
            _filler_name(match.group("filler")),
        )
        if coordinate not in excluded_coordinates:
            retained.append(line)
    return b"".join(retained)


def _closure_indexes(
    persisted_assertions: tuple[PersistedAssertionEvidence, ...],
    concept_exclusions: tuple[ConceptExclusionDisposition, ...],
) -> tuple[
    dict[tuple[str, str, str], PersistedAssertionEvidence],
    dict[str, ConceptExclusionDisposition],
]:
    persisted_by_key = {
        (item.concept_code, item.axis, item.filler_code): item
        for item in persisted_assertions
    }
    if len(persisted_by_key) != len(persisted_assertions):
        raise CorpusAcceptanceValidationError(
            "persisted assertion evidence is ambiguous"
        )
    concepts_by_code = {item.concept_code: item for item in concept_exclusions}
    if len(concepts_by_code) != len(concept_exclusions):
        raise CorpusAcceptanceValidationError("concept exclusion is ambiguous")
    return persisted_by_key, concepts_by_code


def _partition_assertion_rows(
    *,
    rdf_rows: tuple[dict[str, object], ...],
    rdf_coordinates: tuple[tuple[str, str, str], ...],
    persisted_by_key: dict[tuple[str, str, str], PersistedAssertionEvidence],
    concepts_by_code: dict[str, ConceptExclusionDisposition],
    source: CertifiedSourceBinding,
) -> tuple[
    list[CanonicalSemanticAssertion],
    list[ExcludedSemanticAssertion],
    list[QualifyingAssertionEvidence],
    set[tuple[str, str, str]],
]:
    included: list[CanonicalSemanticAssertion] = []
    excluded: list[ExcludedSemanticAssertion] = []
    evidence: list[QualifyingAssertionEvidence] = []
    excluded_coordinates: set[tuple[str, str, str]] = set()
    for row, coordinate in zip(rdf_rows, rdf_coordinates, strict=True):
        persisted = persisted_by_key.get(coordinate)
        assertion = _assertion_from_row(row, persisted)
        reason = _closure_exclusion_reason(assertion, persisted, concepts_by_code)
        if reason is not None:
            excluded.append(
                ExcludedSemanticAssertion(
                    assertion=assertion,
                    reason=reason,
                    official_source_preserved=True,
                )
            )
            excluded_coordinates.add(coordinate)
        else:
            if persisted is None:
                raise CorpusAcceptanceValidationError(
                    "included assertion lacks evidence"
                )
            included.append(assertion)
            evidence.append(_evidence_for_assertion(assertion, persisted, source))
    return included, excluded, evidence, excluded_coordinates


def build_assertion_evidence_closure(
    *,
    source_artifact: Path,
    effective_destination: Path,
    source: CertifiedSourceBinding,
    persisted_assertions: tuple[PersistedAssertionEvidence, ...],
    concept_exclusions: tuple[ConceptExclusionDisposition, ...],
) -> AssertionEvidenceClosure:
    """Build a parser-cross-checked effective projection and exact evidence closure."""
    payload = source_artifact.read_bytes()
    source_identity = hashlib.sha256(payload).hexdigest()
    rdf_rows = _rdf_assertion_rows(payload)
    rdf_coordinates = tuple(
        (
            str(row["concept_code"]),
            str(row["axis"]),
            str(row["filler_code"]),
        )
        for row in rdf_rows
    )
    fast_coordinates = _fast_assertion_coordinates(payload)
    rdf_inventory_identity = _identity(rdf_coordinates)
    fast_inventory_identity = _identity(fast_coordinates)
    if rdf_inventory_identity != fast_inventory_identity:
        raise CorpusAcceptanceValidationError("parser inventories differ")
    persisted_by_key, concepts_by_code = _closure_indexes(
        persisted_assertions, concept_exclusions
    )
    included, excluded, evidence, excluded_coordinates = _partition_assertion_rows(
        rdf_rows=rdf_rows,
        rdf_coordinates=rdf_coordinates,
        persisted_by_key=persisted_by_key,
        concepts_by_code=concepts_by_code,
        source=source,
    )
    effective_payload = _filter_assertion_lines(
        payload, excluded_coordinates, set(concepts_by_code)
    )
    if hashlib.sha256(source_artifact.read_bytes()).hexdigest() != source_identity:
        raise CorpusAcceptanceValidationError(
            "source artifact changed during projection"
        )
    if effective_destination.exists():
        raise CorpusAcceptanceValidationError("effective artifact destination exists")
    atomic_write_bytes(effective_destination, effective_payload)
    included.sort(key=attrgetter("assertion_identity"))
    excluded.sort(key=lambda item: item.assertion.assertion_identity)
    evidence.sort(key=attrgetter("assertion_identity"))
    return AssertionEvidenceClosure(
        source=source,
        source_artifact_identity=source_identity,
        effective_artifact_identity=hashlib.sha256(effective_payload).hexdigest(),
        rdf_parser_inventory_identity=rdf_inventory_identity,
        fast_parser_inventory_identity=fast_inventory_identity,
        included_assertion_closure=tuple(included),
        excluded_assertion_closure=tuple(excluded),
        concept_exclusions=tuple(
            sorted(concept_exclusions, key=attrgetter("concept_code"))
        ),
        evidence_ledger=tuple(evidence),
        unresolved_included_assertion_ids=(),
        contradictory_included_assertion_ids=(),
        ambiguous_included_assertion_ids=(),
    )


def _transformation_binding(constituent: Constituent) -> tuple[str, object]:
    if constituent.axis_source != "role":
        return (
            f"source-{constituent.axis_source}-projection-v1",
            {"axis": constituent.axis, "axis_source": constituent.axis_source},
        )
    contract = AXIS_CONTRACTS.get(constituent.axis)
    if contract is None or not constituent.source_roles:
        raise CorpusAcceptanceValidationError(
            "persisted routed assertion lacks an applicable axis contract"
        )
    if not set(constituent.source_roles) <= set(contract.source_roles):
        raise CorpusAcceptanceValidationError(
            "persisted routed assertion lacks an applicable axis contract"
        )
    return "axis-contract-routing-v1", contract.model_dump(mode="json")


def _persisted_assertion_evidence(
    *, concept_code: str, constituent: Constituent, policy_identity: str
) -> PersistedAssertionEvidence:
    rule, contract_payload = _transformation_binding(constituent)
    applicability = _identity(
        {
            "rule": rule,
            "policy_identity": policy_identity,
            "contract": contract_payload,
            "concept_code": concept_code,
            "axis": constituent.axis,
            "filler_code": constituent.filler_code,
            "source_roles": constituent.source_roles,
            "source_fact_ids": constituent.source_definition_ids,
            "source_occurrence_ids": constituent.source_occurrence_ids,
        }
    )
    try:
        return PersistedAssertionEvidence(
            concept_code=concept_code,
            axis=constituent.axis,
            filler_code=constituent.filler_code,
            source_fact_ids=constituent.source_definition_ids,
            source_occurrence_ids=constituent.source_occurrence_ids,
            transformation_rule=rule,
            transformation_policy_identity=policy_identity,
            applicability_identity=applicability,
        )
    except ValidationError as exc:
        detail = str(exc.errors(include_url=False)[0]["msg"])
        raise CorpusAcceptanceValidationError(
            "persisted assertion "
            f"{concept_code} {constituent.axis} {constituent.filler_code} "
            f"lacks exact qualifying evidence: {detail}"
        ) from exc


def persisted_assertion_evidence(
    decompositions: tuple[Decomposition, ...],
    *,
    policy_identity: str,
) -> tuple[PersistedAssertionEvidence, ...]:
    """Derive exact row-level transformation applicability from persisted output."""
    if re.fullmatch(_SHA256, policy_identity) is None:
        raise CorpusAcceptanceValidationError("policy identity is invalid")
    result: list[PersistedAssertionEvidence] = []
    for decomposition in decompositions:
        for constituent in decomposition.constituents:
            if constituent.filler_code.startswith("MINT-"):
                continue
            if not constituent.source_definition_ids:
                continue
            result.append(
                _persisted_assertion_evidence(
                    concept_code=decomposition.code,
                    constituent=constituent,
                    policy_identity=policy_identity,
                )
            )
    return tuple(
        sorted(
            result,
            key=lambda item: (item.concept_code, item.axis, item.filler_code),
        )
    )


def _outcome_exclusion(
    outcome: WorkItemOutcome,
) -> ConceptExclusionDisposition | None:
    if outcome.outcome == "unknown":
        reason: Literal["unknown-outcome", "residual"] = "unknown-outcome"
    elif outcome.outcome == "residual":
        reason = "residual"
    else:
        return None
    return ConceptExclusionDisposition(
        concept_code=outcome.concept_code,
        reason=reason,
        official_source_preserved=True,
    )


def _contains_unknown_filler(
    decomposition: Decomposition, unknown_fillers: set[str]
) -> bool:
    return any(
        constituent.filler_code in unknown_fillers
        for constituent in decomposition.constituents
    )


def _outcome_dispositions(
    outcomes: tuple[WorkItemOutcome, ...],
) -> dict[str, ConceptExclusionDisposition]:
    return {
        outcome.concept_code: disposition
        for outcome in outcomes
        if (disposition := _outcome_exclusion(outcome)) is not None
    }


def _residual_unknown_dispositions(
    decompositions: tuple[Decomposition, ...],
    residual_classifications: tuple[ResidualFillerClassification, ...],
) -> dict[str, ConceptExclusionDisposition]:
    unknown_fillers = {
        item.filler_code
        for item in residual_classifications
        if item.classification == "unknown"
    }
    return {
        decomposition.code: ConceptExclusionDisposition(
            concept_code=decomposition.code,
            reason="residual-unknown",
            official_source_preserved=True,
        )
        for decomposition in decompositions
        if _contains_unknown_filler(decomposition, unknown_fillers)
    }


def build_concept_exclusion_dispositions(
    *,
    outcomes: tuple[WorkItemOutcome, ...],
    decompositions: tuple[Decomposition, ...],
    residual_classifications: tuple[ResidualFillerClassification, ...],
) -> tuple[ConceptExclusionDisposition, ...]:
    """Derive exact concept-level withholding from persisted typed observations."""
    dispositions = _outcome_dispositions(outcomes)
    dispositions.update(
        _residual_unknown_dispositions(decompositions, residual_classifications)
    )
    return tuple(dispositions[code] for code in sorted(dispositions))


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


class MintProposalAssertion(_StrictModel):
    subject_code: str = Field(pattern=_CODE)
    axis: str = Field(min_length=1)
    proposal_id: str = Field(pattern=r"^MINT-[0-9a-f]{12}$")
    source_line_identity: str = Field(pattern=_SHA256)
    assertion_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        expected = _identity(
            self.model_dump(mode="json", exclude={"assertion_identity"})
        )
        if self.assertion_identity != expected:
            raise ValueError("MINT assertion identity differs")
        return self


class HistoricalProposalRegistryBinding(_StrictModel):
    registry_schema_version: Literal[2]
    registry_identity: str = Field(pattern=_SHA256)
    registry_file_identity: str = Field(pattern=_SHA256)
    registry_source_identity: str = Field(pattern=_SHA256)
    registry_release: str = Field(min_length=1)
    migration_envelope_identity: str = Field(pattern=_SHA256)
    migration_file_identity: str = Field(pattern=_SHA256)
    proposal_count: Literal[7]
    status_counts: dict[Literal["locally-approved", "proposed"], int]
    accepted_in_ncit_count: Literal[0]
    no_adoption_evidence: Literal[True]
    registered_proposal_ids: tuple[str, ...] = Field(min_length=1)
    candidate_source_identity: str = Field(pattern=_SHA256)
    source_mismatch_observed: bool
    reconciliation_envelope_available: Literal[False]

    @model_validator(mode="after")
    def _source_observation_matches(self) -> Self:
        if self.source_mismatch_observed != (
            self.registry_source_identity != self.candidate_source_identity
        ):
            raise ValueError("proposal registry source mismatch observation differs")
        if self.status_counts != {"locally-approved": 2, "proposed": 5}:
            raise ValueError("historical proposal lifecycle counts differ")
        return self


class ExcludedUnreconciledProposal(_StrictModel):
    assertion: MintProposalAssertion
    disposition: Literal["excluded-unreconciled"]
    registry_record_present: bool
    transfer_or_reconciliation_inferred: Literal[False]


class EffectiveProposalDelta(_StrictModel):
    registry: HistoricalProposalRegistryBinding
    original_emitted_count: int = Field(ge=0)
    removed_unreconciled_count: int = Field(ge=0)
    unreconciled_emitted_count: Literal[0]
    accepted_without_evidence_count: Literal[0]
    distinct_proposal_ids: tuple[str, ...]
    registry_intersection: tuple[str, ...]
    removed_assertions: tuple[ExcludedUnreconciledProposal, ...]

    @model_validator(mode="after")
    def _inventory_is_exhaustive(self) -> Self:
        if self.original_emitted_count != len(self.removed_assertions):
            raise ValueError("proposal assertion inventory is not exhaustive")
        if self.removed_unreconciled_count != self.original_emitted_count:
            raise ValueError("unreconciled proposal removal count differs")
        observed_ids = tuple(
            sorted({item.assertion.proposal_id for item in self.removed_assertions})
        )
        if self.distinct_proposal_ids != observed_ids:
            raise ValueError("distinct proposal identifier inventory differs")
        return self


class EffectiveArtifactEvidence(_StrictModel):
    source_artifact_identity: str = Field(pattern=_SHA256)
    effective_artifact_identity: str = Field(pattern=_SHA256)
    removed_pair_count: int = Field(gt=0)
    non_emitted_pair_count: int = Field(ge=0)
    removed_pairs: tuple[EffectivePairDispositionEvidence, ...] = Field(min_length=1)
    non_emitted_pairs: tuple[EffectivePairDispositionEvidence, ...]
    proposal_delta: EffectiveProposalDelta
    source_artifact_preserved: Literal[True]


def build_historical_proposal_registry_binding(
    *,
    registry_path: Path,
    migration_path: Path,
    candidate_source_identity: str,
) -> HistoricalProposalRegistryBinding:
    """Validate immutable historical governance without rebinding its source."""
    try:
        migration = load_proposal_registry_migration_envelope(migration_path)
        registry = validate_migrated_proposal_registry(migration, registry_path)
    except (OSError, ValueError, ProposalRegistryMigrationError) as exc:
        raise CorpusAcceptanceValidationError(
            "historical proposal registry binding is invalid"
        ) from exc
    statuses = Counter(map(attrgetter("status"), registry.proposals))
    accepted = statuses.get("accepted-in-ncit", 0)
    no_adoption = not any(map(_has_adoption_evidence, registry.proposals))
    try:
        return HistoricalProposalRegistryBinding.model_validate(
            {
                "registry_schema_version": registry.schema_version,
                "registry_identity": registry.registry_identity,
                "registry_file_identity": hashlib.sha256(
                    registry_path.read_bytes()
                ).hexdigest(),
                "registry_source_identity": registry.source_identity,
                "registry_release": registry.ontology_version,
                "migration_envelope_identity": migration.envelope_identity,
                "migration_file_identity": hashlib.sha256(
                    migration_path.read_bytes()
                ).hexdigest(),
                "proposal_count": len(registry.proposals),
                "status_counts": dict(sorted(statuses.items())),
                "accepted_in_ncit_count": accepted,
                "no_adoption_evidence": no_adoption,
                "registered_proposal_ids": tuple(
                    sorted(
                        map(
                            attrgetter("id"),
                            filter(_is_mint_registry_record, registry.proposals),
                        )
                    )
                ),
                "candidate_source_identity": candidate_source_identity,
                "source_mismatch_observed": (
                    registry.source_identity != candidate_source_identity
                ),
                "reconciliation_envelope_available": False,
            }
        )
    except ValidationError as exc:
        raise CorpusAcceptanceValidationError(
            "historical proposal registry lifecycle is invalid"
        ) from exc


def _has_adoption_evidence(proposal: Proposal) -> bool:
    return proposal.adoption_evidence is not None


def _is_mint_registry_record(proposal: Proposal) -> bool:
    return proposal.id.startswith("MINT-")


def enumerate_mint_assertions(artifact: bytes) -> tuple[MintProposalAssertion, ...]:
    """Semantically parse every proposal-shaped Turtle statement in the artifact."""
    lines = filter(_contains_mint, io.BytesIO(artifact))
    assertions = tuple(map(_semantic_mint_assertion, lines))
    keys = tuple(map(_mint_assertion_key, assertions))
    if len(keys) != len(set(keys)):
        raise CorpusAcceptanceValidationError("duplicate MINT assertion")
    return tuple(sorted(assertions, key=lambda item: item.assertion_identity))


def _contains_mint(line: bytes) -> bool:
    return b"MINT" in line


def _mint_assertion_key(assertion: MintProposalAssertion) -> tuple[str, str, str]:
    return assertion.subject_code, assertion.axis, assertion.proposal_id


def _semantic_mint_assertion(line: bytes) -> MintProposalAssertion:
    graph = Graph()
    try:
        graph.parse(data=line.decode("utf-8"), format="turtle")
    except Exception as exc:
        raise CorpusAcceptanceValidationError(
            "malformed proposal-shaped Turtle statement"
        ) from exc
    subject, axis_node, filler_node = _single_mint_candidate(graph)
    payload = _mint_assertion_payload(subject, axis_node, filler_node, line)
    return MintProposalAssertion(
        **payload,
        assertion_identity=_identity(payload),  # type: ignore[arg-type]
    )


def _single_mint_candidate(graph: Graph) -> tuple[Node, Node, Node]:
    has_constituent = URIRef(vocab.HAS_CONSTITUENT)
    axis_predicate = URIRef(vocab.AXIS)
    filler_predicate = URIRef(vocab.FILLER)
    candidates = tuple(graph.subject_objects(has_constituent))
    if len(candidates) != 1:
        raise CorpusAcceptanceValidationError(
            "malformed proposal-shaped constituent assertion"
        )
    subject, constituent = candidates[0]
    axes = tuple(graph.objects(constituent, axis_predicate))
    fillers = tuple(graph.objects(constituent, filler_predicate))
    if not all(
        (len(axes) == 1, len(fillers) == 1, "MINT" in "".join(map(str, fillers)))
    ):
        raise CorpusAcceptanceValidationError(
            "malformed proposal-shaped constituent assertion"
        )
    return subject, cast("Node", axes[0]), cast("Node", fillers[0])


def _mint_assertion_payload(
    subject: Node, axis_node: Node, filler_node: Node, line: bytes
) -> dict[str, str]:
    subject_code = str(subject).removeprefix(NCIT_NS)
    axis_iri = str(axis_node)
    axis = (
        f"op:{axis_iri.removeprefix(vocab.ONTOPRISM_NS)}"
        if axis_iri.startswith(vocab.ONTOPRISM_NS)
        else axis_iri
    )
    filler_iri = str(filler_node)
    proposal_id = filler_iri.removeprefix(vocab.ONTOPRISM_NS)
    valid = all(
        (
            re.fullmatch(_CODE, subject_code) is not None,
            axis.startswith("op:"),
            filler_iri.startswith(vocab.ONTOPRISM_NS),
            re.fullmatch(r"MINT-[0-9a-f]{12}", proposal_id) is not None,
        )
    )
    if not valid:
        raise CorpusAcceptanceValidationError(
            "malformed proposal-shaped constituent assertion"
        )
    source_line_identity = hashlib.sha256(line).hexdigest()
    payload = {
        "subject_code": subject_code,
        "axis": axis,
        "proposal_id": proposal_id,
        "source_line_identity": source_line_identity,
    }
    return payload


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
    expected_minted_count: int,
    proposal_registry: Path,
    proposal_registry_migration: Path,
    candidate_source_identity: str,
) -> EffectiveArtifactEvidence:
    """Write a source-derived artifact with only exact disputed pairs withheld."""
    source = source_artifact.read_bytes()
    source_identity = hashlib.sha256(source).hexdigest()
    registry = build_historical_proposal_registry_binding(
        registry_path=proposal_registry,
        migration_path=proposal_registry_migration,
        candidate_source_identity=candidate_source_identity,
    )
    proposal_assertions = _expected_mint_assertions(source, expected_minted_count)
    by_key = _effective_exclusion_map(exclusions)
    proposal_line_ids = set(
        map(attrgetter("source_line_identity"), proposal_assertions)
    )
    payload, removed_keys = _filter_effective_lines(
        source, by_key, proposal_line_ids=proposal_line_ids
    )
    registered = set(registry.registered_proposal_ids)
    _require_no_mint_survivors(payload, registered)
    proposal_delta = _build_effective_proposal_delta(
        proposal_assertions, registry, registered
    )
    evidence = _validate_effective_delta(
        source, payload, removed_keys, exclusions, by_key
    )
    if hashlib.sha256(source_artifact.read_bytes()).hexdigest() != source_identity:
        raise CorpusAcceptanceValidationError(
            "source artifact changed during projection"
        )
    if destination.exists():
        raise CorpusAcceptanceValidationError("effective artifact destination exists")
    atomic_write_bytes(destination, payload)
    return EffectiveArtifactEvidence(
        source_artifact_identity=source_identity,
        effective_artifact_identity=hashlib.sha256(payload).hexdigest(),
        removed_pair_count=len(evidence[0]),
        non_emitted_pair_count=len(evidence[1]),
        removed_pairs=evidence[0],
        non_emitted_pairs=evidence[1],
        proposal_delta=proposal_delta,
        source_artifact_preserved=True,
    )


def _expected_mint_assertions(
    source: bytes, expected_count: int
) -> tuple[MintProposalAssertion, ...]:
    assertions = enumerate_mint_assertions(source)
    if len(assertions) != expected_count:
        raise CorpusAcceptanceValidationError(
            "MINT assertion count differs from candidate baseline"
        )
    return assertions


def _require_no_mint_survivors(payload: bytes, registered: set[str]) -> None:
    surviving = enumerate_mint_assertions(payload)
    if not surviving:
        return
    rejections = sorted(
        {
            _proposal_survivor_disposition(
                item.proposal_id,
                registered=item.proposal_id in registered,
            )
            for item in surviving
        }
    )
    raise CorpusAcceptanceValidationError(
        "unreconciled MINT assertion survived effective filtering: "
        + ", ".join(rejections)
    )


def _build_effective_proposal_delta(
    assertions: tuple[MintProposalAssertion, ...],
    registry: HistoricalProposalRegistryBinding,
    registered: set[str],
) -> EffectiveProposalDelta:
    proposal_ids = tuple(sorted(set(map(attrgetter("proposal_id"), assertions))))
    removed = tuple(
        ExcludedUnreconciledProposal(
            assertion=item,
            disposition="excluded-unreconciled",
            registry_record_present=item.proposal_id in registered,
            transfer_or_reconciliation_inferred=False,
        )
        for item in assertions
    )
    return EffectiveProposalDelta(
        registry=registry,
        original_emitted_count=len(assertions),
        removed_unreconciled_count=len(removed),
        unreconciled_emitted_count=0,
        accepted_without_evidence_count=0,
        distinct_proposal_ids=proposal_ids,
        registry_intersection=tuple(sorted(set(proposal_ids) & registered)),
        removed_assertions=removed,
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
    source: bytes,
    by_key: dict[tuple[str, str, str], ReviewRequiredPairChange],
    *,
    proposal_line_ids: set[str],
) -> tuple[bytes, tuple[tuple[str, str, str], ...]]:
    removed: set[tuple[str, str, str]] = set()
    retained: list[bytes] = []
    for line in io.BytesIO(source):
        if hashlib.sha256(line).hexdigest() in proposal_line_ids:
            continue
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


def _acceptance_statuses(
    closure: AssertionEvidenceClosure,
) -> dict[str, str]:
    projected = {
        assertion.concept_code for assertion in closure.included_assertion_closure
    }
    review_required = {
        item.assertion.concept_code
        for item in closure.excluded_assertion_closure
        if item.reason == "review-required"
    }
    concept_statuses = {
        item.concept_code: (
            "unknown-withheld"
            if item.reason == "unknown-outcome"
            else "residual-withheld"
        )
        for item in closure.concept_exclusions
    }
    statuses = dict.fromkeys(projected, "projected")
    statuses.update(dict.fromkeys(review_required, "review-required-excluded"))
    statuses.update(concept_statuses)
    return statuses


def _add_acceptance_metadata(
    graph: Graph,
    *,
    closure: AssertionEvidenceClosure,
    statuses: dict[str, str],
    run_id: str,
    represented_identity: str,
    publication_identity: str,
) -> None:
    for code, status in sorted(statuses.items()):
        subject = URIRef(f"{NCIT_NS}{code}")
        values = (
            (vocab.ACCEPTANCE_STATUS, status),
            (vocab.ACCEPTANCE_SOURCE_RELEASE, closure.source.release),
            (vocab.ACCEPTANCE_SOURCE_IDENTITY, closure.source.source_identity),
            (vocab.ACCEPTANCE_RUN, run_id),
            (vocab.ACCEPTANCE_REPRESENTATION, represented_identity),
            (vocab.ACCEPTANCE_PUBLICATION, publication_identity),
        )
        for predicate, value in values:
            graph.add((subject, URIRef(predicate), RdfLiteral(value)))
        if status == "review-required-excluded":
            _add_exclusion_summary(graph, subject)


def build_machine_acceptance_metadata_artifact(
    *,
    closure: AssertionEvidenceClosure,
    source_artifact: Path,
    destination: Path,
    run_id: str,
    representation_identity: str | None = None,
    publication_identity: str,
) -> str:
    """Render machine-acceptance metadata from the exact closure dispositions."""
    if destination.exists():
        raise CorpusAcceptanceValidationError(
            "machine acceptance publication artifact destination exists"
        )
    represented_identity = representation_identity or closure.closure_identity
    if not re.fullmatch(_SHA256, publication_identity) or not re.fullmatch(
        _SHA256, represented_identity
    ):
        raise CorpusAcceptanceValidationError(
            "publication or representation identity is invalid"
        )
    graph = _read_effective_graph(source_artifact)
    _add_acceptance_metadata(
        graph,
        closure=closure,
        statuses=_acceptance_statuses(closure),
        run_id=run_id,
        represented_identity=represented_identity,
        publication_identity=publication_identity,
    )
    payload = graph.serialize(format="turtle", encoding="utf-8")
    if not isinstance(payload, bytes):
        raise CorpusAcceptanceValidationError(
            "machine acceptance serializer returned text"
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
                self.verify_currency,
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
    proposal_registry_migration_identity: str = Field(pattern=_SHA256)
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
    proposal_delta: EffectiveProposalDelta
    delta_classification: CorpusDeltaClassification
    review_required_exclusions: tuple[ReviewRequiredEffectiveExclusion, ...]
    content_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _content_identity_matches(self) -> Self:
        expected = _identity(self.model_dump(mode="json", exclude={"content_identity"}))
        if self.content_identity != expected:
            raise ValueError("candidate content identity differs")
        return self


class EvidenceClosedCorpusAcceptanceCandidate(_StrictModel):
    """Final candidate whose accepted plane is exactly the evidenced projection."""

    schema_version: Literal[2]
    status: Literal["machine-blocked", "machine-evidence-accepted"]
    content: CorpusAcceptanceContent
    publication_dry_run: PublicationDryRunEvidence
    assertion_closure: AssertionEvidenceClosure
    ambiguity_report: EvidenceAmbiguityReport
    r101_occurrence_closure: R101OccurrenceClosure
    machine_acceptance: MachineEvidenceAcceptance | None
    nci_adoption_claimed: Literal[False]
    candidate_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_and_status_match(self) -> Self:
        accepted = self.machine_acceptance is not None
        expected_status = "machine-evidence-accepted" if accepted else "machine-blocked"
        if self.status != expected_status:
            raise ValueError("evidence-closed candidate status differs")
        if self.content.projection.artifact_sha256 != (
            self.assertion_closure.effective_artifact_identity
        ):
            raise ValueError("candidate projection and assertion closure differ")
        if (
            self.publication_dry_run.artifact_identity
            != self.assertion_closure.effective_artifact_identity
        ):
            raise ValueError("candidate dry-run and assertion closure differ")
        expected = _identity(
            self.model_dump(mode="json", exclude={"candidate_identity"})
        )
        if self.candidate_identity != expected:
            raise ValueError("evidence-closed candidate identity differs")
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
    "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
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


def _proposal_survivor_disposition(
    proposal_id: str, *, registered: bool
) -> Literal[
    "malformed-proposal-reference",
    "registered-unreconciled-survivor",
    "unregistered-unreconciled-survivor",
]:
    if re.fullmatch(r"MINT-[0-9a-f]{12}", proposal_id) is None:
        return "malformed-proposal-reference"
    if registered:
        return "registered-unreconciled-survivor"
    return "unregistered-unreconciled-survivor"


def _proposal_gate_liveness() -> dict[str, str]:
    return {
        "registered_survivor": _proposal_survivor_disposition(
            "MINT-781c8c8c6096", registered=True
        ),
        "unregistered_survivor": _proposal_survivor_disposition(
            "MINT-deadbeef1234", registered=False
        ),
        "malformed_reference": _proposal_survivor_disposition(
            "MINT-not-valid", registered=False
        ),
    }


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
    roundtrip_fidelity: float | None,
    proposal_delta: EffectiveProposalDelta,
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
    liveness_observation = _proposal_gate_liveness()
    liveness = set(liveness_observation.values()) == {
        "malformed-proposal-reference",
        "registered-unreconciled-survivor",
        "unregistered-unreconciled-survivor",
    }
    improvement_status = _strict_improvement_status(readiness)
    return CandidateGates(
        primary_site_cardinality=_gate(
            status=primary_status,
            evidence=paths["primary_site_audit"],
            observation={"cardinality_violations": violations},
        ),
        proposal_provenance=_gate(
            status="passed",
            evidence=paths["proposal_registry"],
            observation=proposal_delta.model_dump(mode="json"),
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
            observation=liveness_observation,
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
        "proposal_registry_migration",
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


def _validated_git_head(git_head: str | None) -> str:
    if git_head is None or re.fullmatch(r"[0-9a-f]{40}", git_head) is None:
        raise CorpusAcceptanceValidationError("candidate generator git head is invalid")
    return git_head


def _present_run_summary(run_summary: RunSummary | None) -> RunSummary:
    if run_summary is None:
        raise CorpusAcceptanceValidationError("persisted run summary is absent")
    return run_summary


def _residual_concept_codes(
    outcomes: tuple[WorkItemOutcome, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(item.concept_code for item in outcomes if item.outcome == "residual")
    )


def _expected_effective_codes(
    aggregate: CorpusBaselineAggregate,
    concept_exclusions: tuple[ConceptExclusionDisposition, ...],
) -> tuple[str, ...]:
    excluded = {item.concept_code for item in concept_exclusions}
    return tuple(code for code in aggregate.decomposed_codes if code not in excluded)


def _candidate_exclusions_identity(
    *,
    closure: AssertionEvidenceClosure,
    exclusions: tuple[ReviewRequiredEffectiveExclusion, ...],
    effective: EffectiveArtifactEvidence,
) -> str:
    return _identity(
        {
            "assertions": [
                item.model_dump(mode="json")
                for item in closure.excluded_assertion_closure
            ],
            "concepts": [
                item.model_dump(mode="json") for item in closure.concept_exclusions
            ],
            "historical_review": [item.model_dump(mode="json") for item in exclusions],
            "proposals": effective.proposal_delta.model_dump(mode="json"),
        }
    )


def _machine_acceptance_if_ready(
    *,
    gates: CandidateGates,
    classification: CorpusDeltaClassification,
    dry_run: PublicationDryRunEvidence,
    content: CorpusAcceptanceContent,
    closure: AssertionEvidenceClosure,
    exclusions_identity: str,
    evidence: CandidateEvidence,
    ambiguity: EvidenceAmbiguityReport,
) -> MachineEvidenceAcceptance | None:
    if not _candidate_is_ready(gates, classification, dry_run):
        return None
    return build_machine_evidence_acceptance(
        candidate_identity=content.content_identity,
        closure=closure,
        exclusions_identity=exclusions_identity,
        evidence_ledger_identity=closure.evidence_ledger_identity,
        policy_identity=evidence.policy_identity,
        dry_run_identity=dry_run.evidence_identity,
        ambiguity=ambiguity,
    )


def _candidate_status(
    machine_acceptance: MachineEvidenceAcceptance | None,
) -> Literal["machine-evidence-accepted", "machine-blocked"]:
    if machine_acceptance is not None:
        return "machine-evidence-accepted"
    return "machine-blocked"


def _candidate_artifact_sources(
    *,
    root: Path,
    paths: dict[str, Path],
    candidate_path: Path,
    effective_path: Path,
    closure_path: Path,
    dry_run_path: Path,
) -> dict[str, Path]:
    temporary_inputs = {
        f"inputs/{name}{path.suffix}": path
        for name, path in paths.items()
        if path.is_relative_to(root / "tmp")
    }
    return {
        "artifacts/candidate.json": candidate_path,
        "artifacts/effective-c3262.ttl": effective_path,
        "artifacts/assertion-evidence-closure.json": closure_path,
        "artifacts/publication-dry-run.json": dry_run_path,
        **temporary_inputs,
    }


def _candidate_sources(paths: dict[str, Path]) -> tuple[SourceIdentity, ...]:
    return tuple(
        SourceIdentity(
            name=name.replace("_", "-"),
            identity=f"sha256:{_file_identity(path)}",
        )
        for name, path in sorted(paths.items())
    )


def _candidate_result(
    *,
    generation: Path,
    artifact_manifest,
    candidate: EvidenceClosedCorpusAcceptanceCandidate,
    closure: AssertionEvidenceClosure,
    effective: EffectiveArtifactEvidence,
    dry_run: PublicationDryRunEvidence,
) -> dict[str, object]:  # type: ignore[no-untyped-def]
    acceptance_identity = (
        candidate.machine_acceptance.acceptance_identity
        if candidate.machine_acceptance is not None
        else None
    )
    exclusion_counts = dict(
        sorted(Counter(item.reason for item in closure.concept_exclusions).items())
    )
    return {
        "candidate_manifest_path": str(generation / "manifest.json"),
        "candidate_manifest_identity": artifact_manifest.manifest_identity,
        "candidate_identity": candidate.candidate_identity,
        "candidate_status": candidate.status,
        "category_counts": candidate.content.delta_classification.category_counts,
        "unexplained_blocker_count": len(
            candidate.content.delta_classification.unexplained_blockers
        ),
        "effective_artifact_identity": closure.effective_artifact_identity,
        "assertion_closure_identity": closure.closure_identity,
        "evidence_ledger_identity": closure.evidence_ledger_identity,
        "machine_acceptance_identity": acceptance_identity,
        "included_assertion_count": len(closure.included_assertion_closure),
        "excluded_assertion_count": len(closure.excluded_assertion_closure),
        "concept_exclusion_counts": exclusion_counts,
        "removed_from_effective_count": len(closure.excluded_assertion_closure),
        "review_required_non_emitted_count": effective.non_emitted_pair_count,
        "dry_run_identity": dry_run.evidence_identity,
        "dry_run_status": dry_run.status,
        "postgres_before_identity": dry_run.postgres_before_identity,
        "postgres_after_identity": dry_run.postgres_after_identity,
        "qlever_before_identity": dry_run.qlever_before_identity,
        "qlever_after_identity": dry_run.qlever_after_identity,
    }


async def generate_c3262_acceptance_candidate(  # noqa: PLR0915
    root: Path, *, git_head: str | None = None
) -> dict[str, object]:
    """Generate the fixed certified candidate; arbitrary run/count claims are absent."""
    _require_certified_acceptance_inputs(root)
    git_head = _validated_git_head(git_head)
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
        closure_effective_path = staging / "closure-effective-c3262.ttl"
        preliminary_effective_path = staging / "preliminary-effective-c3262.ttl"
        effective = build_effective_artifact(
            source_artifact=paths["artifact"],
            destination=preliminary_effective_path,
            exclusions=exclusions,
            expected_minted_count=baseline.minted_count,
            proposal_registry=paths["proposal_registry"],
            proposal_registry_migration=paths["proposal_registry_migration"],
            candidate_source_identity=baseline.source_identity,
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
            proposal_registry_migration_identity=_file_identity(
                paths["proposal_registry_migration"]
            ),
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
            run_summary = _present_run_summary(await store.get_run(baseline.run_id))
            aggregate = await store.corpus_baseline_aggregate(baseline.run_id)
            outcomes = tuple(await store.work_item_outcomes(baseline.run_id))
            decompositions = tuple(await store.decompositions_for_run(baseline.run_id))
            residual_classifications = tuple(
                await store.residual_filler_classifications(baseline.run_id)
            )
            concept_exclusions = build_concept_exclusion_dispositions(
                outcomes=outcomes,
                decompositions=decompositions,
                residual_classifications=residual_classifications,
            )
            closure = build_assertion_evidence_closure(
                source_artifact=paths["artifact"],
                effective_destination=closure_effective_path,
                source=CertifiedSourceBinding(
                    release=manifest.ontology_version,
                    source_manifest_identity=_file_identity(paths["source_manifest"]),
                    source_identity=manifest.source_identity,
                    stated_artifact_identity=(
                        manifest.stated_artifact.artifact_identity
                    ),
                    certification="expert-curated-ncit-release",
                ),
                persisted_assertions=persisted_assertion_evidence(
                    decompositions, policy_identity=evidence.policy_identity
                ),
                concept_exclusions=concept_exclusions,
            )
            semantic_projection_identity = _identity(
                closure.model_dump(mode="json", exclude={"effective_artifact_identity"})
            )
            published_artifact_identity = build_machine_acceptance_metadata_artifact(
                closure=closure,
                source_artifact=closure_effective_path,
                destination=effective_path,
                run_id=baseline.run_id,
                representation_identity=semantic_projection_identity,
                publication_identity=semantic_projection_identity,
            )
            closure = closure.model_copy(
                update={"effective_artifact_identity": published_artifact_identity}
            )
            residual_concept_codes = _residual_concept_codes(outcomes)
            readiness = _load_json_object(
                paths["machine_readiness"], "machine readiness"
            )
            gates = _candidate_gates(
                paths,
                git_head=git_head,
                roundtrip_fidelity=run_summary.roundtrip_fidelity,
                proposal_delta=effective.proposal_delta,
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
                    artifact_sha256=closure.effective_artifact_identity,
                    representation_identity=semantic_projection_identity,
                    served_semantic_projection_identity=semantic_projection_identity,
                    no_equivalence=True,
                ),
                "metrics": _candidate_metrics(
                    baseline, run_summary, readiness, residual_concept_codes
                ),
                "gates": gates,
                "r101_summary": _r101_candidate_summary(report),
                "evidence": evidence,
                "proposal_delta": effective.proposal_delta,
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
                            semantic_projection_identity
                        ),
                    ),
                    artifact=effective_path,
                    destination_graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
                    expected_codes=_expected_effective_codes(
                        aggregate, concept_exclusions
                    ),
                    expected_worklist_count=baseline.worklist_count,
                    graph=graph,
                    provenance=store,
                )
        finally:
            await database.dispose_engine(engine)
        ambiguity = EvidenceAmbiguityReport.from_closure(closure)
        exclusions_identity = _candidate_exclusions_identity(
            closure=closure,
            exclusions=exclusions,
            effective=effective,
        )
        machine_acceptance = _machine_acceptance_if_ready(
            gates=gates,
            classification=classification,
            dry_run=dry_run,
            content=content,
            closure=closure,
            exclusions_identity=exclusions_identity,
            evidence=evidence,
            ambiguity=ambiguity,
        )
        r101_closure = build_r101_occurrence_closure(report)
        candidate_payload = {
            "schema_version": 2,
            "status": _candidate_status(machine_acceptance),
            "content": content,
            "publication_dry_run": dry_run,
            "assertion_closure": closure,
            "ambiguity_report": ambiguity,
            "r101_occurrence_closure": r101_closure,
            "machine_acceptance": machine_acceptance,
            "nci_adoption_claimed": False,
        }
        candidate = EvidenceClosedCorpusAcceptanceCandidate.model_validate(
            {
                **candidate_payload,
                "candidate_identity": _identity(_jsonable(candidate_payload)),
            }
        )
        candidate_path = staging / "candidate.json"
        dry_run_path = staging / "publication-dry-run.json"
        closure_path = staging / "assertion-evidence-closure.json"
        candidate_path.write_text(
            json.dumps(candidate.model_dump(mode="json"), sort_keys=True, indent=2)
            + "\n"
        )
        dry_run_path.write_text(
            json.dumps(dry_run.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
        )
        closure_path.write_text(
            json.dumps(closure.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
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
            artifact_sources=_candidate_artifact_sources(
                root=root,
                paths=paths,
                candidate_path=candidate_path,
                effective_path=effective_path,
                closure_path=closure_path,
                dry_run_path=dry_run_path,
            ),
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
            sources=_candidate_sources(paths),
            retention=RetentionBinding(
                retention_class="scientific-evidence",
                owner="decomposition",
                expires_at=None,
            ),
        )
    generation = (
        root
        / "tmp/artifacts/v1/generations/c3262-corpus-acceptance-candidate"
        / candidate.candidate_identity
    )
    return _candidate_result(
        generation=generation,
        artifact_manifest=artifact_manifest,
        candidate=candidate,
        closure=closure,
        effective=effective,
        dry_run=dry_run,
    )


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
            "proposal_delta": effective.proposal_delta.model_dump(mode="json"),
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
