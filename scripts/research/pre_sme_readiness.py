"""Write-free, identity-bound machine checks before final M1.6 SME review."""

from __future__ import annotations

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
    validate_current_comparison,
)
from scripts.research.group_review_packet import load_group_review_packet

from ontolib.decomposition import vocab
from ontolib.decomposition.corpus_baseline import CorpusBaseline, load_corpus_baseline
from ontolib.decomposition.proposal_registry import load_proposal_registry
from ontolib.decomposition.r101_conservation import load_r101_conservation_report
from ontolib.decomposition.r101_review import (
    dry_run_r101_decision_expansion,
    load_r101_decision_registry,
    load_r101_review_packet,
)
from ontolib.decomposition.r103_review import load_r103_review_packet
from ontolib.terminologies.ncit.sibling_store import (
    SiblingStoreValidationError,
    validate_ncit_sibling_manifest,
)

_SHA256 = r"^[0-9a-f]{64}$"
_GIT_SHA = r"^[0-9a-f]{40}$"
_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
_GROUP_REVIEW_COUNT = 18
_R103_REVIEW_COUNT = 3


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


class PrimarySiteAudit(_StrictModel):
    schema_version: Literal[1]
    source_identity: str = Field(pattern=_SHA256)
    source_release: str
    corpus_baseline_identity: str = Field(pattern=_SHA256)
    corpus_artifact_identity: str = Field(pattern=_SHA256)
    resolved_sites: tuple[PrimarySiteObservation, ...]
    review_required_sites: tuple[PrimarySiteObservation, ...]
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
        resolved_concepts = [item.concept_code for item in self.resolved_sites]
        if len(resolved_concepts) != len(set(resolved_concepts)):
            raise ValueError("resolved concepts must be unique")
        observations = [
            (item.concept_code, item.filler_code)
            for item in (*self.resolved_sites, *self.review_required_sites)
        ]
        if len(observations) != len(set(observations)):
            raise ValueError(
                "resolved and review observations must be pairwise distinct"
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
        self._resolved_by_concept: dict[str, str] = {}

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

    def _add_constituent(  # noqa: C901 - validates the complete RDF observation
        self, subject: rdflib.Node, value: rdflib.Node
    ) -> None:
        if not isinstance(value, rdflib.BNode):
            raise PreSmeValidationError("constituent is not a Turtle blank node")
        part = self._parts.pop(value, None)
        if not part:
            raise PreSmeValidationError("reused constituent blank node has no data")
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
        previous = self._resolved_by_concept.setdefault(concept_code, filler_code)
        if previous != filler_code:
            raise PreSmeValidationError(
                f"{concept_code} has more than one resolved primary site; "
                "at most one resolved site is permitted"
            )
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
    if store._parts:
        raise PreSmeValidationError(
            "unbound constituent data remains after Turtle parse"
        )
    return artifact_identity, store


def audit_primary_site_artifact(
    *,
    artifact: Path,
    baseline: CorpusBaseline,
    source_identity: str,
    source_release: str,
    output: Path | None = None,
) -> PrimarySiteAudit:
    """Audit every primary-site observation in one parser pass."""
    if baseline.source_identity != source_identity:
        raise PreSmeValidationError("corpus baseline source identity differs")
    if baseline.ontology_release != source_release:
        raise PreSmeValidationError("corpus baseline source release differs")
    artifact_identity, store = _parse_primary_sites(artifact)
    if (
        artifact_identity
        not in {
            baseline.artifact_identity,
            baseline.representation_identity,
        }
        or baseline.artifact_identity != baseline.representation_identity
    ):
        raise PreSmeValidationError("corpus artifact identity differs from baseline")
    payload: dict[str, object] = {
        "schema_version": 1,
        "source_identity": source_identity,
        "source_release": source_release,
        "corpus_baseline_identity": baseline.baseline_identity,
        "corpus_artifact_identity": artifact_identity,
        "resolved_sites": tuple(store.resolved),
        "review_required_sites": tuple(store.review),
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
    *, source_manifest: Path, baseline: Path, artifact: Path, output: Path
) -> PrimarySiteAudit:
    try:
        manifest = validate_ncit_sibling_manifest(source_manifest)
        corpus_baseline = load_corpus_baseline(baseline)
        return audit_primary_site_artifact(
            artifact=artifact,
            baseline=corpus_baseline,
            source_identity=manifest.source_identity,
            source_release=manifest.ontology_version,
            output=output,
        )
    except PreSmeValidationError:
        raise
    except Exception as exc:
        raise PreSmeValidationError(str(exc)) from exc


class MachineReadinessInputs(_StrictModel):
    source_identity: str = Field(pattern=_SHA256)
    source_manifest_identity: str = Field(pattern=_SHA256)
    current_evidence_identity: str = Field(pattern=_SHA256)
    current_comparison_identity: str = Field(pattern=_SHA256)
    sample_artifact_identity: str = Field(pattern=_SHA256)
    corpus_baseline_identity: str = Field(pattern=_SHA256)
    corpus_artifact_identity: str = Field(pattern=_SHA256)
    r101_report_identity: str = Field(pattern=_SHA256)
    r101_registry_identity: str = Field(pattern=_SHA256)
    r101_existing_packet_identity: str = Field(pattern=_SHA256)
    r101_current_packet_identity: str = Field(pattern=_SHA256)
    r101_validation_identity: str = Field(pattern=_SHA256)
    proposal_registry_identity: str = Field(pattern=_SHA256)
    primary_site_audit_identity: str = Field(pattern=_SHA256)
    group_packet_identity: str = Field(pattern=_SHA256)
    r103_packet_identity: str = Field(pattern=_SHA256)
    verify_evidence_identity: str = Field(pattern=_SHA256)
    git_head: str = Field(pattern=_GIT_SHA)
    exact_pair_true_positive: int = Field(ge=0)
    exact_pair_emitted: int = Field(gt=0)
    exact_pair_expected: int = Field(gt=0)
    full_partition_agreement: tuple[int, int]
    common_partition_agreement: tuple[int, int]
    group_review_count: Literal[18]
    r103_review_count: Literal[3]
    r101_exact_validation_established: bool

    @model_validator(mode="after")
    def _validate_reuse_status(self) -> Self:
        exact = self.r101_existing_packet_identity == self.r101_current_packet_identity
        if self.r101_exact_validation_established != exact:
            raise ValueError("R101 exact-reuse status differs from packet identities")
        return self


class R101ReuseValidation(_StrictModel):
    schema_version: Literal[1]
    status: Literal["exact-reuse-established", "human-reattestation-required"]
    reason: Literal["exact-bindings-match", "packet-bindings-differ"]
    report_identity: str = Field(pattern=_SHA256)
    existing_packet_identity: str = Field(pattern=_SHA256)
    current_packet_identity: str = Field(pattern=_SHA256)
    registry_identity: str = Field(pattern=_SHA256)
    exact_reuse: bool
    authorization: Literal[False]
    publication_writes_performed: Literal[False]
    validation_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_result(self) -> Self:
        established = self.existing_packet_identity == self.current_packet_identity
        if self.exact_reuse != established:
            raise ValueError("R101 exact-reuse verdict differs from packet identities")
        expected_status = (
            "exact-reuse-established" if established else "human-reattestation-required"
        )
        expected_reason = (
            "exact-bindings-match" if established else "packet-bindings-differ"
        )
        if (self.status, self.reason) != (expected_status, expected_reason):
            raise ValueError("R101 reuse status differs from packet identities")
        expected = _identity(
            self.model_dump(mode="json", exclude={"validation_identity"})
        )
        if self.validation_identity != expected:
            raise ValueError("R101 reuse validation identity differs")
        return self


def build_r101_reuse_validation(
    *,
    report_identity: str,
    existing_packet_identity: str,
    current_packet_identity: str,
    registry_identity: str,
) -> R101ReuseValidation:
    exact = existing_packet_identity == current_packet_identity
    payload = {
        "schema_version": 1,
        "status": (
            "exact-reuse-established" if exact else "human-reattestation-required"
        ),
        "reason": "exact-bindings-match" if exact else "packet-bindings-differ",
        "report_identity": report_identity,
        "existing_packet_identity": existing_packet_identity,
        "current_packet_identity": current_packet_identity,
        "registry_identity": registry_identity,
        "exact_reuse": exact,
        "authorization": False,
        "publication_writes_performed": False,
    }
    return R101ReuseValidation.model_validate(
        {**payload, "validation_identity": _identity(payload)}
    )


def generate_r101_reuse_validation(
    *,
    report: Path,
    existing_packet: Path,
    current_packet: Path,
    registry: Path,
    output: Path,
) -> R101ReuseValidation:
    """Report whether an explicitly supplied attestation is exactly reusable.

    Exact reuse requires the registry to bind the supplied existing packet and the
    regenerated packet to be byte-semantically identical. It reuses that prior human
    evidence; this operation never creates or rebinds an authorization.
    """
    try:
        report_value = load_r101_conservation_report(report)
        existing = load_r101_review_packet(existing_packet)
        current = load_r101_review_packet(current_packet)
        registry_value = load_r101_decision_registry(registry)
    except (OSError, SiblingStoreValidationError, ValidationError, ValueError) as exc:
        raise PreSmeValidationError(str(exc)) from exc
    if current.bindings.report_identity != report_value.report_identity:
        raise PreSmeValidationError(
            "regenerated R101 packet does not bind current report"
        )
    if registry_value.packet_identity != existing.packet_identity:
        raise PreSmeValidationError(
            "R101 registry does not bind existing attested packet"
        )
    result = build_r101_reuse_validation(
        report_identity=report_value.report_identity,
        existing_packet_identity=existing.packet_identity,
        current_packet_identity=current.packet_identity,
        registry_identity=registry_value.registry_identity,
    )
    if result.exact_reuse:
        dry_run_r101_decision_expansion(report_value, current, registry_value)
    _atomic_write(output, _canonical_bytes(result))
    return result


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


class ReadinessMetrics(_StrictModel):
    exact_pair_precision: ValidatedFraction
    exact_pair_recall: ValidatedFraction
    historical_precision: ValidatedFraction
    historical_recall: ValidatedFraction
    exceeds_historical_thresholds: bool

    @model_validator(mode="after")
    def _validate_thresholds(self) -> Self:
        exceeds = (
            self.exact_pair_precision.value > self.historical_precision.value
            and self.exact_pair_recall.value > self.historical_recall.value
        )
        if self.exceeds_historical_thresholds != exceeds:
            raise ValueError("historical threshold claim differs from fractions")
        return self


class GroupingViews(_StrictModel):
    full_view: tuple[int, int]
    common_pair_view: tuple[int, int]


class PublicationState(_StrictModel):
    status: Literal["not-attempted"]
    publication_writes_performed: Literal[False]


class ReadinessClaims(_StrictModel):
    no_unadjudicated_delta: None


RequirementKind = Literal[
    "group-review",
    "r103-review",
    "r101-ledger-authorization",
    "final-full-corpus-scientific-acceptance-and-publication",
]


class PendingHumanRequirement(_StrictModel):
    requirement: RequirementKind
    count: Literal[18, 3, 3291] | None
    status: Literal["pending"]

    @model_validator(mode="after")
    def _validate_count(self) -> Self:
        expected = {
            "group-review": 18,
            "r103-review": 3,
            "r101-ledger-authorization": 3291,
            "final-full-corpus-scientific-acceptance-and-publication": None,
        }[self.requirement]
        if self.count != expected:
            raise ValueError("human requirement count differs")
        return self


class SatisfiedR101Requirement(_StrictModel):
    requirement: Literal["r101-ledger-authorization"]
    count: Literal[3291]
    status: Literal["satisfied-by-exact-reuse"]
    packet_identity: str = Field(pattern=_SHA256)
    registry_identity: str = Field(pattern=_SHA256)


HumanRequirement = Annotated[
    PendingHumanRequirement | SatisfiedR101Requirement,
    Field(discriminator="status"),
]


class ReportIdentities(_StrictModel):
    source_identity: str = Field(pattern=_SHA256)
    source_manifest_identity: str = Field(pattern=_SHA256)
    current_evidence_identity: str = Field(pattern=_SHA256)
    current_comparison_identity: str = Field(pattern=_SHA256)
    sample_artifact_identity: str = Field(pattern=_SHA256)
    corpus_baseline_identity: str = Field(pattern=_SHA256)
    corpus_artifact_identity: str = Field(pattern=_SHA256)
    r101_report_identity: str = Field(pattern=_SHA256)
    r101_registry_identity: str = Field(pattern=_SHA256)
    r101_existing_packet_identity: str = Field(pattern=_SHA256)
    r101_current_packet_identity: str = Field(pattern=_SHA256)
    r101_validation_identity: str = Field(pattern=_SHA256)
    proposal_registry_identity: str = Field(pattern=_SHA256)
    primary_site_audit_identity: str = Field(pattern=_SHA256)
    group_packet_identity: str = Field(pattern=_SHA256)
    r103_packet_identity: str = Field(pattern=_SHA256)
    verify_evidence_identity: str = Field(pattern=_SHA256)
    git_head: str = Field(pattern=_GIT_SHA)


class MachineReadinessReport(_StrictModel):
    schema_version: Literal[1]
    status: Literal["awaiting-human-review"]
    authorization: Literal[False]
    publication: PublicationState
    identities: ReportIdentities
    metrics: ReadinessMetrics
    grouping: GroupingViews
    r101_mechanical_unresolved: Literal[0]
    r101_non_r101_delta: Literal[0]
    claims: ReadinessClaims
    human_requirements: tuple[HumanRequirement, ...]
    report_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        requirements = [item.requirement for item in self.human_requirements]
        if len(requirements) != len(set(requirements)):
            raise ValueError("human requirements must be unique")
        if set(requirements) != {
            "group-review",
            "r103-review",
            "r101-ledger-authorization",
            "final-full-corpus-scientific-acceptance-and-publication",
        }:
            raise ValueError("required human requirements differ")
        expected = _identity(self.model_dump(mode="json", exclude={"report_identity"}))
        if self.report_identity != expected:
            raise ValueError("machine readiness report identity differs")
        return self


def build_machine_readiness(inputs: MachineReadinessInputs) -> MachineReadinessReport:
    precision_better = (
        inputs.exact_pair_true_positive * 106 > 80 * inputs.exact_pair_emitted
    )
    recall_better = (
        inputs.exact_pair_true_positive * 153 > 80 * inputs.exact_pair_expected
    )
    if not precision_better or not recall_better:
        raise PreSmeValidationError("current exact-pair metrics do not exceed baseline")
    requirements: list[PendingHumanRequirement | SatisfiedR101Requirement] = [
        PendingHumanRequirement(requirement="group-review", count=18, status="pending"),
        PendingHumanRequirement(requirement="r103-review", count=3, status="pending"),
    ]
    if inputs.r101_exact_validation_established:
        requirements.append(
            SatisfiedR101Requirement(
                requirement="r101-ledger-authorization",
                count=3291,
                status="satisfied-by-exact-reuse",
                packet_identity=inputs.r101_current_packet_identity,
                registry_identity=inputs.r101_registry_identity,
            )
        )
    else:
        requirements.append(
            PendingHumanRequirement(
                requirement="r101-ledger-authorization", count=3291, status="pending"
            )
        )
    requirements.append(
        PendingHumanRequirement(
            requirement="final-full-corpus-scientific-acceptance-and-publication",
            count=None,
            status="pending",
        )
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "awaiting-human-review",
        "authorization": False,
        "publication": {
            "status": "not-attempted",
            "publication_writes_performed": False,
        },
        "identities": {
            key: str(value)
            for key, value in inputs.model_dump(mode="json").items()
            if key.endswith("identity") or key == "git_head"
        },
        "metrics": {
            "exact_pair_precision": {
                "numerator": inputs.exact_pair_true_positive,
                "denominator": inputs.exact_pair_emitted,
                "value": inputs.exact_pair_true_positive / inputs.exact_pair_emitted,
            },
            "exact_pair_recall": {
                "numerator": inputs.exact_pair_true_positive,
                "denominator": inputs.exact_pair_expected,
                "value": inputs.exact_pair_true_positive / inputs.exact_pair_expected,
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
        },
        "grouping": {
            "full_view": inputs.full_partition_agreement,
            "common_pair_view": inputs.common_partition_agreement,
        },
        "r101_mechanical_unresolved": 0,
        "r101_non_r101_delta": 0,
        "claims": {"no_unadjudicated_delta": None},
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


def generate_pre_sme_readiness(  # noqa: C901 - fail-closed cross-artifact validation
    *,
    source_manifest: Path,
    current_evidence: Path,
    current_comparison: Path,
    corpus_baseline: Path,
    corpus_artifact: Path,
    r101_report: Path,
    r101_validation: Path,
    proposal_registry: Path,
    primary_site_audit: Path,
    group_packet: Path,
    r103_packet: Path,
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
        baseline = load_corpus_baseline(corpus_baseline)
        artifact_identity = hashlib.sha256(corpus_artifact.read_bytes()).hexdigest()
        if artifact_identity != baseline.artifact_identity:
            raise PreSmeValidationError("full-corpus artifact identity differs")
        report = load_r101_conservation_report(r101_report)
        unresolved = report.counts.unresolved
        non_r101_delta = report.counts.non_r101_delta
        if unresolved != 0 or non_r101_delta != 0:
            raise PreSmeValidationError(
                "R101 mechanical ledger is not zero-delta complete"
            )
        validation_value, _validation_raw = _load_json_no_duplicates(
            r101_validation, "R101 current validation"
        )
        validation = R101ReuseValidation.model_validate(validation_value)
        proposals = load_proposal_registry(proposal_registry)
        audit = PrimarySiteAudit.model_validate_json(
            _load_json_no_duplicates(primary_site_audit, "primary-site audit")[1]
        )
        group = load_group_review_packet(group_packet)
        r103 = load_r103_review_packet(r103_packet)
        gate = VerifyEvidence.model_validate_json(
            _load_json_no_duplicates(verify_evidence, "verify evidence")[1]
        )
    except PreSmeValidationError:
        raise
    except (OSError, SiblingStoreValidationError, ValidationError, ValueError) as exc:
        raise PreSmeValidationError(str(exc)) from exc
    require_current_verify_evidence(gate.git_head, expected_git_head)
    checks = (
        (manifest.source_identity == evidence.source_identity, "sample source"),
        (manifest.source_identity == baseline.source_identity, "corpus source"),
        (manifest.source_identity == report.source_identity, "R101 source"),
        (manifest.source_identity == audit.source_identity, "audit source"),
        (
            baseline.baseline_identity == audit.corpus_baseline_identity,
            "audit baseline",
        ),
        (artifact_identity == audit.corpus_artifact_identity, "audit artifact"),
        (
            validation.report_identity == report.report_identity,
            "R101 validation report",
        ),
        (
            proposals.registry_identity == evidence.proposal_registry_identity,
            "proposal",
        ),
        (
            group.current_evidence_identity == evidence.evidence_identity,
            "group evidence",
        ),
        (
            group.current_comparison_identity == comparison.comparison_identity,
            "group comparison",
        ),
        (group.r101_report_identity == report.report_identity, "group R101"),
        (r103.source_identity == manifest.source_identity, "R103 source"),
        (r103.candidate_manifest_identity == manifest_identity, "R103 manifest"),
        (
            r103.proposal_registry_identity == proposals.registry_identity,
            "R103 proposal",
        ),
    )
    for accepted, name in checks:
        if not accepted:
            raise PreSmeValidationError(f"{name} identity or invariant differs")
    metrics = comparison.metrics
    if len(group.review_rows) != _GROUP_REVIEW_COUNT:
        raise PreSmeValidationError("group review count differs from 18")
    if len(r103.rows) != _R103_REVIEW_COUNT:
        raise PreSmeValidationError("R103 review count differs from 3")
    try:
        inputs = MachineReadinessInputs(
            source_identity=manifest.source_identity,
            source_manifest_identity=manifest_identity,
            current_evidence_identity=evidence.evidence_identity,
            current_comparison_identity=comparison.comparison_identity,
            sample_artifact_identity=evidence.artifact_identity,
            corpus_baseline_identity=baseline.baseline_identity,
            corpus_artifact_identity=artifact_identity,
            r101_report_identity=report.report_identity,
            r101_registry_identity=validation.registry_identity,
            r101_existing_packet_identity=validation.existing_packet_identity,
            r101_current_packet_identity=validation.current_packet_identity,
            r101_validation_identity=validation.validation_identity,
            proposal_registry_identity=proposals.registry_identity,
            primary_site_audit_identity=audit.audit_identity,
            group_packet_identity=group.packet_identity,
            r103_packet_identity=r103.packet_identity,
            verify_evidence_identity=gate.evidence_identity,
            git_head=gate.git_head,
            exact_pair_true_positive=metrics.exact_pair_precision.numerator,
            exact_pair_emitted=metrics.exact_pair_precision.denominator,
            exact_pair_expected=metrics.exact_pair_recall.denominator,
            full_partition_agreement=(
                metrics.full_partition_agreement.numerator,
                metrics.full_partition_agreement.denominator,
            ),
            common_partition_agreement=(
                metrics.common_pair_partition_agreement.numerator,
                metrics.common_pair_partition_agreement.denominator,
            ),
            group_review_count=18,
            r103_review_count=3,
            r101_exact_validation_established=validation.exact_reuse,
        )
    except ValidationError as exc:
        raise PreSmeValidationError(str(exc)) from exc
    if (
        inputs.exact_pair_true_positive,
        inputs.exact_pair_emitted,
        inputs.exact_pair_expected,
    ) != (100, 108, 153):
        raise PreSmeValidationError(
            "current exact-pair metrics differ from 100/108 and 100/153"
        )
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
    observed_exit_code: int,
) -> VerifyEvidence:
    """Write observations from one successful, clean-worktree verify execution.

    The caller must observe the same clean Git HEAD before and after the gate. This
    local evidence write is not an ontology, store, or publication write.
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
