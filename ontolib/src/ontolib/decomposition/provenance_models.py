"""Typed identities and read models for decomposition provenance."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class NcitSourceSnapshot(BaseModel):
    """Identity returned by a revalidated #181 candidate proof."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: str = Field(min_length=1)


class RunFingerprint(BaseModel):
    """Canonical immutable identity for one exact decomposition worklist."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch: str = Field(min_length=1)
    semantic_types: tuple[str, ...]
    worklist: tuple[str, ...]
    total_limit: int | None = Field(default=None, gt=0)
    algorithm_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    walker_max_depth: int = Field(gt=0)
    output_mode: Literal["none", "file"]
    load_mode: Literal["none", "named-graph"]
    emitted_at: AwareDatetime

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
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class RunResumeIdentity(BaseModel):
    """Caller-controlled dimensions that must match a persisted resumable run."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch: str = Field(min_length=1)
    semantic_types: tuple[str, ...]
    total_limit: int | None = Field(default=None, gt=0)
    algorithm_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    walker_max_depth: int = Field(gt=0)
    output_mode: Literal["none", "file"]
    load_mode: Literal["none", "named-graph"]

    @classmethod
    def from_fingerprint(cls, fingerprint: RunFingerprint) -> RunResumeIdentity:
        """Project only the dimensions a resume invocation can independently know."""
        return cls(
            source_identity=fingerprint.source_identity,
            branch=fingerprint.branch,
            semantic_types=fingerprint.semantic_types,
            total_limit=fingerprint.total_limit,
            algorithm_version=fingerprint.algorithm_version,
            config_version=fingerprint.config_version,
            walker_max_depth=fingerprint.walker_max_depth,
            output_mode=fingerprint.output_mode,
            load_mode=fingerprint.load_mode,
        )


class RunOutcomeCounts(BaseModel):
    """Cumulative outcomes derived from the exact persisted worklist."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    total_in_scope: int = Field(ge=0)
    decomposed: int = Field(ge=0)
    residual: int = Field(ge=0)
    minted_count: int = Field(ge=0)


class RunSummary(BaseModel):
    """One decomposition run's manifest + (if finished) its metric counts."""

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
    total_in_scope: int | None = None
    decomposed: int | None = None
    residual: int | None = None
    minted_count: int | None = None
    pct_decomposed: float | None = None
    roundtrip_fidelity: float | None = None


class MintedConcept(BaseModel):
    """A minted-concept proposal awaiting curator approval."""

    id: str
    run_id: str
    axis: str
    label: str
    source_signal: str
    status: str
