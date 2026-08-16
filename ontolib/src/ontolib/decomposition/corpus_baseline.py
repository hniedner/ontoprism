"""Typed source-qualified baseline for one published full neoplasm run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self

import rdflib
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition import vocab
from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.provenance_models import (
    CorpusBaselineAggregate,  # noqa: TC001 - Pydantic resolves runtime annotation
    CorpusOutcomeCounts,  # noqa: TC001 - Pydantic resolves runtime annotation
)
from ontolib.decomposition.publication import (
    PublicationValidationError,
    validate_artifact,
)

if TYPE_CHECKING:
    from ontolib.decomposition.provenance_models import CompletedRunForEvidence

_SHA256 = r"^[0-9a-f]{64}$"
CORPUS_BASELINE_SCHEMA_VERSION = 1


class CorpusBaselineValidationError(ValueError):
    """A persisted run or artifact cannot support a full-corpus baseline."""


class CorpusBaseline(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: int
    run_id: str = Field(min_length=1)
    source_identity: str = Field(pattern=_SHA256)
    ontology_release: str = Field(min_length=1)
    branch: str
    scope_root: str
    scope_version: str
    run_fingerprint_identity: str = Field(pattern=_SHA256)
    representation_identity: str = Field(pattern=_SHA256)
    artifact_identity: str = Field(pattern=_SHA256)
    detector_identity: str = Field(pattern=_SHA256)
    worklist_count: int = Field(ge=0)
    outcome_counts: CorpusOutcomeCounts
    emitted_constituent_pair_count: int = Field(ge=0)
    complete_semantic_fact_count: int = Field(ge=0)
    source_occurrence_count: int = Field(ge=0)
    selected_occurrence_count: int = Field(ge=0)
    minted_count: int = Field(ge=0)
    baseline_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        if self.schema_version != CORPUS_BASELINE_SCHEMA_VERSION:
            raise ValueError("unsupported corpus baseline schema version")
        if self.baseline_identity != corpus_baseline_identity(self):
            raise ValueError("corpus baseline identity does not match its payload")
        return self


class CorpusBaselineStore(Protocol):
    async def completed_run_for_evidence(
        self, run_id: str
    ) -> CompletedRunForEvidence: ...

    async def corpus_baseline_aggregate(
        self, run_id: str
    ) -> CorpusBaselineAggregate: ...


def _identity(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def corpus_baseline_identity(value: CorpusBaseline | dict[str, object]) -> str:
    payload = (
        value.model_dump(mode="json")
        if isinstance(value, CorpusBaseline)
        else dict(value)
    )
    payload.pop("baseline_identity", None)
    return _identity(payload)


def _detector_identity(run: CompletedRunForEvidence) -> str:
    fingerprint = run.fingerprint
    return _identity(
        {
            "algorithm_version": fingerprint.algorithm_version,
            "config_version": fingerprint.config_version,
            "walker_max_depth": fingerprint.walker_max_depth,
            "semantic_types": fingerprint.semantic_types,
        }
    )


def _require_full_production_run(run: CompletedRunForEvidence) -> None:
    fingerprint = run.fingerprint
    spec = branch_spec(DecompositionBranch.NEOPLASM)
    checks = (
        (
            fingerprint.sample_manifest_identity is None,
            "full-corpus run cannot use a sample manifest",
        ),
        (fingerprint.total_limit is None, "full-corpus run cannot use a total limit"),
        (
            fingerprint.branch == DecompositionBranch.NEOPLASM,
            "baseline requires the neoplasm branch",
        ),
        (
            fingerprint.scope_root == spec.root_code,
            "run does not use the production scope root",
        ),
        (
            fingerprint.scope_version == spec.scope_version,
            "run does not use the production scope version",
        ),
        (
            fingerprint.algorithm_version == spec.algorithm_version,
            "run does not use the production algorithm",
        ),
        (
            fingerprint.semantic_types == spec.semantic_types,
            "run does not use the production semantic types",
        ),
    )
    for accepted, message in checks:
        if not accepted:
            raise CorpusBaselineValidationError(message)


def _require_candidate_identity(
    run: CompletedRunForEvidence,
    expected_source_identity: str | None,
    expected_release: str | None,
) -> None:
    if expected_source_identity is not None and (
        run.fingerprint.source_identity != expected_source_identity
    ):
        raise CorpusBaselineValidationError(
            "run source identity does not match candidate source manifest"
        )
    if expected_release is not None and run.ncit_version != expected_release:
        raise CorpusBaselineValidationError(
            "run release does not match candidate source manifest"
        )


def _require_aggregate_worklist(
    aggregate: CorpusBaselineAggregate, run: CompletedRunForEvidence
) -> None:
    if aggregate.worklist_count != len(run.fingerprint.worklist):
        raise CorpusBaselineValidationError(
            "aggregate worklist count does not match run fingerprint"
        )


def _require_exact_representation(
    artifact: Path, expected_codes: tuple[str, ...]
) -> None:
    graph = rdflib.Graph()
    graph.parse(artifact, format="turtle")
    predicate = rdflib.URIRef(vocab.REPRESENTATION_STATUS)
    legacy = rdflib.Literal(vocab.LEGACY_PRECOORDINATED)
    expected_subjects = {
        rdflib.URIRef("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#" + code)
        for code in expected_codes
    }
    represented_subjects = set(graph.subjects(predicate, None))
    if represented_subjects != expected_subjects or any(
        set(graph.objects(subject, predicate)) != {legacy}
        for subject in expected_subjects
    ):
        raise CorpusBaselineValidationError(
            "decomposition artifact has an unexpected representation status"
        )


async def generate_corpus_baseline(
    *,
    run_id: str,
    artifact: Path,
    store: CorpusBaselineStore,
    expected_source_identity: str | None = None,
    expected_release: str | None = None,
) -> CorpusBaseline:
    """Generate a baseline only from an exact completed published full-corpus run."""
    run = await store.completed_run_for_evidence(run_id)
    _require_full_production_run(run)
    fingerprint = run.fingerprint
    _require_candidate_identity(run, expected_source_identity, expected_release)
    if artifact.resolve() != Path(run.publication_artifact_path).resolve():
        raise CorpusBaselineValidationError(
            "artifact path does not match persisted publication artifact"
        )
    aggregate = await store.corpus_baseline_aggregate(run_id)
    _require_aggregate_worklist(aggregate, run)
    try:
        artifact_identity = validate_artifact(
            artifact,
            expected_codes=aggregate.decomposed_codes,
            run_id=run_id,
        )
    except PublicationValidationError as exc:
        raise CorpusBaselineValidationError(str(exc)) from exc
    _require_exact_representation(artifact, aggregate.decomposed_codes)
    if artifact_identity != run.representation_identity:
        raise CorpusBaselineValidationError(
            "artifact representation does not match persisted run"
        )
    payload: dict[str, object] = {
        "schema_version": CORPUS_BASELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "source_identity": fingerprint.source_identity,
        "ontology_release": run.ncit_version,
        "branch": fingerprint.branch,
        "scope_root": fingerprint.scope_root,
        "scope_version": fingerprint.scope_version,
        "run_fingerprint_identity": fingerprint.identity,
        "representation_identity": run.representation_identity,
        "artifact_identity": artifact_identity,
        "detector_identity": _detector_identity(run),
        "worklist_count": aggregate.worklist_count,
        "outcome_counts": aggregate.outcome_counts.model_dump(),
        "emitted_constituent_pair_count": aggregate.emitted_constituent_pair_count,
        "complete_semantic_fact_count": aggregate.complete_semantic_fact_count,
        "source_occurrence_count": aggregate.source_occurrence_count,
        "selected_occurrence_count": aggregate.selected_occurrence_count,
        "minted_count": aggregate.minted_count,
    }
    return CorpusBaseline.model_validate(
        {**payload, "baseline_identity": corpus_baseline_identity(payload)}
    )


def load_corpus_baseline(path: Path) -> CorpusBaseline:
    return CorpusBaseline.model_validate_json(path.read_text())


def write_corpus_baseline(path: Path, baseline: CorpusBaseline) -> None:
    path.write_text(
        json.dumps(
            baseline.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    )
