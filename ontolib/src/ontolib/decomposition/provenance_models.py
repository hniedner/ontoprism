"""Typed identities and read models for decomposition provenance."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Literal, Self, cast

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Pydantic resolves these aliases while constructing the runtime model schema.
from ontolib.decomposition.branches import ScopeRoot, ScopeVersion  # noqa: TC001
from ontolib.decomposition.models import ConceptOutcome  # noqa: TC001

_STANDARD_RUN_SCHEMA = 2
_SAMPLE_RUN_SCHEMA = 3


def _require_matching_scope_root(
    branch: Literal["neoplasm", "disease"],
    scope_root: ScopeRoot,
) -> None:
    expected = "C3262" if branch == "neoplasm" else "C2991"
    if scope_root != expected:
        raise ValueError(f"{branch} branch requires scope root {expected}")


def _require_matching_output_load(
    output_mode: Literal["none", "file"],
    load_mode: Literal["none", "named-graph"],
) -> None:
    if load_mode == "named-graph" and output_mode != "file":
        raise ValueError("named-graph load requires file output")


class NcitSourceSnapshot(BaseModel):
    """Identity returned by a revalidated #181 candidate proof."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: str = Field(min_length=1)


class PublicationMarkerSnapshot(BaseModel):
    """Persisted identity of the graph publication preceding one intent."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$", min_length=1)
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    representation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    built_at: AwareDatetime

    @field_validator("built_at")
    @classmethod
    def canonicalize_graph_timestamp(cls, value: datetime) -> datetime:
        """Use the millisecond precision preserved by QLever ``xsd:dateTime``."""
        utc = value.astimezone(UTC)
        return utc.replace(microsecond=(utc.microsecond // 1000) * 1000)


class RunFingerprint(BaseModel):
    """Canonical immutable identity for one exact decomposition worklist."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2, 3] = 2
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch: Literal["neoplasm", "disease"]
    scope_root: ScopeRoot
    scope_version: ScopeVersion
    semantic_types: tuple[str, ...]
    worklist: tuple[str, ...]
    total_limit: int | None = Field(default=None, gt=0)
    sample_manifest_identity: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    algorithm_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    walker_max_depth: int = Field(gt=0)
    output_mode: Literal["none", "file"]
    load_mode: Literal["none", "named-graph"]
    emitted_at: AwareDatetime

    @model_validator(mode="after")
    def _scope_root_matches_branch(self) -> Self:
        _require_matching_scope_root(self.branch, self.scope_root)
        _require_matching_sample_schema(
            self.schema_version,
            self.sample_manifest_identity,
            self.total_limit,
        )
        _require_matching_output_load(self.output_mode, self.load_mode)
        return self

    @field_validator("semantic_types")
    @classmethod
    def _semantic_types_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item for item in value):
            raise ValueError("semantic_types must contain non-empty values")
        if value != tuple(sorted(set(value))):
            raise ValueError("semantic_types must be sorted and unique")
        return value

    @field_validator("worklist")
    @classmethod
    def _worklist_is_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("worklist must contain unique non-empty concept codes")
        return value

    @property
    def identity(self) -> str:
        """SHA-256 over the exact canonical JSON representation."""
        payload = self.model_dump(mode="json")
        if self.schema_version == _STANDARD_RUN_SCHEMA:
            payload.pop("sample_manifest_identity")
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class CompletedRunForEvidence(BaseModel):
    """Validated completed publication fields needed by evidence generation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$", min_length=1)
    ncit_version: str = Field(min_length=1)
    fingerprint: RunFingerprint
    representation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_artifact_path: str = Field(min_length=1)


class RunResumeIdentity(BaseModel):
    """Caller-controlled dimensions that must match a persisted resumable run."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2, 3] = 2
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch: Literal["neoplasm", "disease"]
    scope_root: ScopeRoot
    scope_version: ScopeVersion
    semantic_types: tuple[str, ...]
    total_limit: int | None = Field(default=None, gt=0)
    sample_manifest_identity: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    algorithm_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    walker_max_depth: int = Field(gt=0)
    output_mode: Literal["none", "file"]
    load_mode: Literal["none", "named-graph"]

    @model_validator(mode="after")
    def _scope_root_matches_branch(self) -> Self:
        _require_matching_scope_root(self.branch, self.scope_root)
        _require_matching_sample_schema(
            self.schema_version,
            self.sample_manifest_identity,
            self.total_limit,
        )
        _require_matching_output_load(self.output_mode, self.load_mode)
        return self

    @classmethod
    def from_fingerprint(cls, fingerprint: RunFingerprint) -> RunResumeIdentity:
        """Project only the dimensions a resume invocation can independently know."""
        return cls(
            schema_version=fingerprint.schema_version,
            source_identity=fingerprint.source_identity,
            branch=fingerprint.branch,
            scope_root=fingerprint.scope_root,
            scope_version=fingerprint.scope_version,
            semantic_types=fingerprint.semantic_types,
            total_limit=fingerprint.total_limit,
            sample_manifest_identity=fingerprint.sample_manifest_identity,
            algorithm_version=fingerprint.algorithm_version,
            config_version=fingerprint.config_version,
            walker_max_depth=fingerprint.walker_max_depth,
            output_mode=fingerprint.output_mode,
            load_mode=fingerprint.load_mode,
        )


def _require_matching_sample_schema(
    schema_version: Literal[2, 3],
    sample_manifest_identity: str | None,
    total_limit: int | None,
) -> None:
    if schema_version == _SAMPLE_RUN_SCHEMA and sample_manifest_identity is None:
        raise ValueError("schema-v3 runs require a sample manifest identity")
    if schema_version == _STANDARD_RUN_SCHEMA and sample_manifest_identity is not None:
        raise ValueError("sample manifest identity requires schema-v3")
    if sample_manifest_identity is not None and total_limit is not None:
        raise ValueError("sample manifest and total_limit are mutually exclusive")


class RunOutcomeCounts(BaseModel):
    """Cumulative outcomes derived from the exact persisted worklist."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    total_in_scope: int = Field(ge=0)
    decomposed: int = Field(ge=0)
    residual: int = Field(ge=0)
    semantic_excluded: int = Field(default=0, ge=0)
    atomic_noop: int = Field(default=0, ge=0)
    unknown_outcome: int = Field(default=0, ge=0)
    minted_count: int = Field(ge=0)


class CorpusOutcomeCounts(BaseModel):
    """Exact outcome categories for a full-corpus baseline."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    decomposed: int = Field(ge=0)
    residual: int = Field(ge=0)
    semantic_excluded: int = Field(ge=0)
    atomic_noop: int = Field(ge=0)
    unknown: int = Field(ge=0)


class CorpusBaselineAggregate(BaseModel):
    """Counts derived from persisted rows in one bounded aggregate query."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    worklist_count: int = Field(ge=0)
    outcome_counts: CorpusOutcomeCounts
    decomposed_codes: tuple[str, ...]
    emitted_constituent_pair_count: int = Field(ge=0)
    complete_semantic_fact_count: int = Field(ge=0)
    source_occurrence_count: int = Field(ge=0)
    selected_occurrence_count: int = Field(ge=0)
    minted_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _counts_are_complete(self) -> Self:
        if sum(self.outcome_counts.model_dump().values()) != self.worklist_count:
            raise ValueError("outcome counts do not sum to worklist count")
        if len(self.decomposed_codes) != self.outcome_counts.decomposed:
            raise ValueError("decomposed code count does not match outcome counts")
        return self


class PersistedRunMetrics(BaseModel):
    """Validated metrics stored in ``decomp_run.metrics``."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    total_in_scope: int | None = Field(default=None, ge=0)
    decomposed: int | None = Field(default=None, ge=0)
    residual: int | None = Field(default=None, ge=0)
    semantic_excluded: int | None = Field(default=None, ge=0)
    atomic_noop: int | None = Field(default=None, ge=0)
    unknown_outcome: int | None = Field(default=None, ge=0)
    residual_precoordinated_count: int | None = Field(default=None, ge=0)
    residual_precoordination: float | None = Field(default=None, ge=0, le=1)
    minted_count: int | None = Field(default=None, ge=0)
    complete_definition_count: int | None = Field(default=None, ge=0)
    complete_fact_count: int | None = Field(default=None, ge=0)
    projected_fact_count: int | None = Field(default=None, ge=0)
    projection_loss_count: int | None = Field(default=None, ge=0)
    projection_loss_rate: float | None = Field(default=None, ge=0, le=1)
    pct_decomposed: float | None = Field(default=None, ge=0, le=1)
    roundtrip_fidelity: float | None = Field(default=None, ge=0, le=1)

    @staticmethod
    def _require_rate(name: str, actual: float | None, expected: float) -> None:
        if actual is not None and not math.isclose(actual, expected, abs_tol=1e-12):
            raise ValueError(f"{name} does not match its persisted counts")

    def _validate_outcome_counts(self) -> None:
        outcome_counts = (
            self.decomposed,
            self.residual,
            self.semantic_excluded,
            self.atomic_noop,
            self.unknown_outcome,
        )
        if (
            self.total_in_scope is not None
            and all(count is not None for count in outcome_counts)
            and sum(cast("tuple[int, ...]", outcome_counts)) != self.total_in_scope
        ):
            raise ValueError("outcome counts do not sum to total_in_scope")

    def _validate_residual_metrics(self) -> None:
        if self.residual_precoordinated_count is None or self.decomposed is None:
            return
        if self.residual_precoordinated_count > self.decomposed:
            raise ValueError("residual count exceeds decomposed count")
        expected = (
            self.residual_precoordinated_count / self.decomposed
            if self.decomposed
            else 0.0
        )
        self._require_rate(
            "residual_precoordination",
            self.residual_precoordination,
            expected,
        )

    def _validate_definition_count(self) -> None:
        if (
            self.complete_definition_count is not None
            and self.decomposed is not None
            and self.complete_definition_count > self.decomposed
        ):
            raise ValueError("complete-definition count exceeds decomposed count")
        # ck_decomp_work_item_outcome_shape forces minted_count = 0 on every
        # non-decomposed outcome, so a positive sum implies decomposed >= 1.
        if self.minted_count and self.decomposed == 0:
            raise ValueError("minted count requires at least one decomposed concept")

    def _validate_fact_metrics(self) -> None:
        if self.complete_fact_count is None or self.projected_fact_count is None:
            return
        if self.projected_fact_count > self.complete_fact_count:
            raise ValueError("projected fact count exceeds complete fact count")
        expected_loss = self.complete_fact_count - self.projected_fact_count
        if (
            self.projection_loss_count is not None
            and self.projection_loss_count != expected_loss
        ):
            raise ValueError("projection loss count does not match fact counts")
        expected_rate = (
            expected_loss / self.complete_fact_count
            if self.complete_fact_count
            else 0.0
        )
        self._require_rate(
            "projection_loss_rate", self.projection_loss_rate, expected_rate
        )

    def _validate_decomposed_rate(self) -> None:
        if self.total_in_scope is None or self.decomposed is None:
            return
        expected_pct = (
            self.decomposed / self.total_in_scope if self.total_in_scope else 0.0
        )
        self._require_rate("pct_decomposed", self.pct_decomposed, expected_pct)

    @model_validator(mode="after")
    def _counts_and_rates_are_consistent(self) -> Self:
        self._validate_outcome_counts()
        self._validate_residual_metrics()
        self._validate_definition_count()
        self._validate_fact_metrics()
        self._validate_decomposed_rate()
        return self


class CompletionRunMetrics(BaseModel):
    """Complete metric payload required when a current run becomes complete."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    total_in_scope: int = Field(ge=0)
    decomposed: int = Field(ge=0)
    residual: int = Field(ge=0)
    semantic_excluded: int = Field(ge=0)
    atomic_noop: int = Field(ge=0)
    unknown_outcome: Literal[0]
    residual_precoordinated_count: int = Field(ge=0)
    residual_precoordination: float = Field(ge=0, le=1)
    minted_count: int = Field(ge=0)
    complete_definition_count: int = Field(ge=0)
    complete_fact_count: int = Field(ge=0)
    projected_fact_count: int = Field(ge=0)
    projection_loss_count: int = Field(ge=0)
    projection_loss_rate: float = Field(ge=0, le=1)
    pct_decomposed: float = Field(ge=0, le=1)
    roundtrip_fidelity: float | None = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _counts_and_rates_are_consistent(self) -> Self:
        PersistedRunMetrics.model_validate(self.model_dump())
        return self


_OUTCOME_FLAGS: dict[ConceptOutcome, tuple[bool, bool]] = {
    "decomposed": (True, False),
    "residual": (False, True),
    "semantic-excluded": (False, False),
    "atomic-no-op": (False, False),
    "unknown": (False, False),
}


def _require_complete_outcome_fields(
    outcome: ConceptOutcome | None,
    semantic_types: tuple[str, ...] | None,
    is_decomposed: bool | None,
    is_residual: bool | None,
    constituent_count: int | None,
    minted_count: int | None,
) -> tuple[ConceptOutcome, tuple[str, ...], bool, bool, int, int]:
    values = (
        outcome,
        semantic_types,
        is_decomposed,
        is_residual,
        constituent_count,
        minted_count,
    )
    if any(value is None for value in values):
        raise ValueError("complete work item requires a typed outcome")
    return cast(
        "tuple[ConceptOutcome, tuple[str, ...], bool, bool, int, int]",
        values,
    )


def _validate_source_semantic_types(
    semantic_type: str | None,
    semantic_types: tuple[str, ...],
) -> None:
    if semantic_types != tuple(sorted(set(semantic_types))):
        raise ValueError("semantic_types must be sorted and unique")
    if semantic_type is not None and semantic_type not in semantic_types:
        raise ValueError("representative semantic type must occur in semantic_types")


def _validate_typed_outcome_shape(
    outcome: ConceptOutcome,
    is_decomposed: bool,
    is_residual: bool,
    constituent_count: int,
    minted_count: int,
) -> None:
    if (is_decomposed, is_residual) != _OUTCOME_FLAGS[outcome]:
        raise ValueError("outcome flags do not match typed outcome")
    if outcome == "decomposed" and constituent_count == 0:
        raise ValueError("decomposed outcome requires at least one constituent")
    if outcome != "decomposed" and (constituent_count != 0 or minted_count != 0):
        raise ValueError("non-decomposed outcome cannot carry constituents or mints")


def _require_no_incomplete_outcome_data(*values: object | None) -> None:
    if any(value is not None for value in values):
        raise ValueError("non-complete work item cannot expose a completion outcome")


class WorkItemOutcome(BaseModel):
    """Observable classification and source types for one exact run work item."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: str = Field(min_length=1)
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    ordinal: int = Field(ge=0)
    state: Literal["pending", "running", "failed", "complete"]
    outcome: ConceptOutcome | None = None
    semantic_type: str | None = None
    semantic_types: tuple[str, ...] | None = None
    is_decomposed: bool | None = None
    is_residual: bool | None = None
    constituent_count: int | None = Field(default=None, ge=0)
    minted_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _completion_shape_is_closed(self) -> Self:
        if self.state == "complete":
            (
                outcome,
                semantic_types,
                is_decomposed,
                is_residual,
                constituent_count,
                minted_count,
            ) = _require_complete_outcome_fields(
                self.outcome,
                self.semantic_types,
                self.is_decomposed,
                self.is_residual,
                self.constituent_count,
                self.minted_count,
            )
            _validate_source_semantic_types(self.semantic_type, semantic_types)
            _validate_typed_outcome_shape(
                outcome,
                is_decomposed,
                is_residual,
                constituent_count,
                minted_count,
            )
        else:
            _require_no_incomplete_outcome_data(
                self.outcome,
                self.semantic_type,
                self.semantic_types,
                self.is_decomposed,
                self.is_residual,
                self.constituent_count,
                self.minted_count,
            )
        return self


class RunSummary(BaseModel):
    """One run manifest plus metrics, including immutable historical labels."""

    id: str
    branch: str
    status: str
    ncit_version: str
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    source_identity: str | None = None
    fingerprint_sha256: str | None = None
    emitted_at: AwareDatetime | None = None
    error_type: str | None = None
    error_message: str | None = None
    publication_state: Literal[
        "legacy",
        "not_requested",
        "pending",
        "publishing",
        "failed",
        "published",
    ] = "legacy"
    publication_attempt_count: int = Field(default=0, ge=0)
    representation_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    publication_artifact_path: str | None = None
    publication_built_at: AwareDatetime | None = None
    publication_started_at: AwareDatetime | None = None
    publication_finished_at: AwareDatetime | None = None
    publication_error_type: str | None = None
    publication_error_message: str | None = None
    publication_predecessor_captured: bool = False
    publication_predecessor: PublicationMarkerSnapshot | None = None
    total_in_scope: int | None = Field(default=None, ge=0)
    decomposed: int | None = Field(default=None, ge=0)
    residual: int | None = Field(default=None, ge=0)
    semantic_excluded: int | None = Field(default=None, ge=0)
    atomic_noop: int | None = Field(default=None, ge=0)
    unknown_outcome: int | None = Field(default=None, ge=0)
    residual_precoordinated_count: int | None = Field(default=None, ge=0)
    residual_precoordination: float | None = Field(default=None, ge=0, le=1)
    minted_count: int | None = Field(default=None, ge=0)
    complete_definition_count: int | None = Field(default=None, ge=0)
    complete_fact_count: int | None = Field(default=None, ge=0)
    projected_fact_count: int | None = Field(default=None, ge=0)
    projection_loss_count: int | None = Field(default=None, ge=0)
    projection_loss_rate: float | None = Field(default=None, ge=0, le=1)
    pct_decomposed: float | None = Field(default=None, ge=0, le=1)
    roundtrip_fidelity: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _validate_publication_predecessor(self) -> Self:
        """Mirror ``ck_decomp_run_publication_predecessor`` (migration 0014).

        A snapshot without the capture flag would make the run permanently
        unpublishable — ``_prepare_publication_intent`` refuses to retry an intent
        it cannot prove captured a predecessor — while a usable snapshot sits in
        the row. Reject the pair here so the state cannot be read back at all.
        """
        if (
            self.publication_predecessor is not None
            and not self.publication_predecessor_captured
        ):
            raise ValueError(
                "publication predecessor snapshot requires the capture flag"
            )
        if (
            self.publication_state in {"legacy", "not_requested", "pending"}
            and self.publication_predecessor_captured
        ):
            raise ValueError(
                f"publication state {self.publication_state} cannot carry a "
                "captured predecessor"
            )
        return self


class MintedConcept(BaseModel):
    """A minted-concept proposal awaiting curator approval."""

    id: str
    run_id: str
    axis: str
    label: str
    source_signal: str
    status: str
