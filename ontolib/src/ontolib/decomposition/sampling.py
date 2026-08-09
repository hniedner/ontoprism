"""Canonical explicit sample manifests for source-bound decomposition review (#154)."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Pydantic resolves these aliases while constructing the runtime model schema.
from ontolib.decomposition.branches import ScopeRoot, ScopeVersion  # noqa: TC001

if TYPE_CHECKING:
    from pathlib import Path

SampleStratum = Literal[
    "semantic-applicable",
    "semantic-excluded",
    "morphology-genus",
    "genus-depth",
    "staging-ajcc-v6",
    "staging-ajcc-v7",
    "staging-ajcc-v8",
    "staging-ajcc-v9",
    "staging-figo-2009",
    "staging-figo-2018",
    "staging-figo-2023",
    "staging-toronto-v2",
    "multi-parent",
    "multi-valued-grouped",
    "nlp-mint",
    "region-organ",
    "known-hard-review",
    "atomic-no-op",
]

REQUIRED_SAMPLE_STRATA: frozenset[SampleStratum] = frozenset(
    {
        "semantic-applicable",
        "semantic-excluded",
        "morphology-genus",
        "genus-depth",
        "staging-ajcc-v6",
        "staging-ajcc-v7",
        "staging-ajcc-v8",
        "staging-ajcc-v9",
        "staging-figo-2009",
        "staging-figo-2018",
        "staging-figo-2023",
        "staging-toronto-v2",
        "multi-parent",
        "multi-valued-grouped",
        "nlp-mint",
        "region-organ",
        "known-hard-review",
        "atomic-no-op",
    }
)


class SampleConcept(BaseModel):
    """One explicitly ordered review concept with overlapping empirical strata."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    code: str = Field(pattern=r"^C[0-9]+$")
    strata: tuple[SampleStratum, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @field_validator("strata")
    @classmethod
    def _strata_are_canonical(
        cls,
        value: tuple[SampleStratum, ...],
    ) -> tuple[SampleStratum, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("strata must be sorted and unique")
        return value

    @field_validator("rationale")
    @classmethod
    def _rationale_is_canonical(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("rationale must be non-empty without outer whitespace")
        return value


class DecompositionSampleManifest(BaseModel):
    """Immutable source/scope-bound explicit review worklist."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    branch: Literal["neoplasm", "disease"]
    scope_root: ScopeRoot
    scope_version: ScopeVersion
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: str = Field(min_length=1)
    selection_method: Literal["explicit-stratified"]
    seed: int | None
    concepts: tuple[SampleConcept, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_review_definition(self) -> Self:
        expected_root = "C3262" if self.branch == "neoplasm" else "C2991"
        if self.scope_root != expected_root:
            raise ValueError(
                f"{self.branch} sample requires scope root {expected_root}"
            )
        if len(self.codes) != len(set(self.codes)):
            raise ValueError("sample concepts must contain unique codes")
        missing = REQUIRED_SAMPLE_STRATA - self.covered_strata
        if missing:
            raise ValueError(
                "sample manifest does not cover required strata: "
                + ", ".join(sorted(missing))
            )
        return self

    @property
    def codes(self) -> tuple[str, ...]:
        """Exact operator-selected worklist order."""
        return tuple(concept.code for concept in self.concepts)

    @property
    def covered_strata(self) -> frozenset[SampleStratum]:
        """Union of all overlapping per-concept strata."""
        return frozenset(
            stratum for concept in self.concepts for stratum in concept.strata
        )

    @property
    def identity(self) -> str:
        """SHA-256 over the exact canonical manifest representation."""
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


def load_sample_manifest(path: Path) -> DecompositionSampleManifest:
    """Read and strictly validate one JSON review manifest."""
    try:
        return DecompositionSampleManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} is not a valid sample manifest") from exc
