"""SSSOM mapping record (Matentzoglu 2022) — one row per cross-ontology mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from ontolib.repositories.xref.vocab import (
    ALLOWED_PREDICATES,
    LIFECYCLE_STATES,
    MappingLifecycle,
    MappingPredicate,
)

if TYPE_CHECKING:
    from ontolib.repositories.xref.evidence import Evidence


class GenerationSourceMetadata(BaseModel):
    """Exact certified repositories and source observations used by a generation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    ncit_source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_source_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    uberon_serving_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    icdo_generation_identity: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    icdo_serving_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    uberon_assertion_identity: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    ncit_target_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ncit_p334_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class StaleXrefGenerationError(RuntimeError):
    """An active mapping generation is not bound to current repositories."""


@dataclass(frozen=True)
class EndpointIdentity:
    """A terminology endpoint bound to the exact release it identifies."""

    system: str
    version: str
    identifier: str

    def __post_init__(self) -> None:
        for name in ("system", "version", "identifier"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True)
class MappingResult:
    """One currently active mapping with both endpoint identities intact."""

    subject: EndpointIdentity
    predicate: MappingPredicate
    object: EndpointIdentity
    lifecycle: MappingLifecycle
    confidence: float

    def __post_init__(self) -> None:
        if self.predicate not in ALLOWED_PREDICATES:
            raise ValueError(f"predicate not allowed: {self.predicate}")
        if self.lifecycle not in LIFECYCLE_STATES:
            raise ValueError(f"lifecycle not allowed: {self.lifecycle}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")


@dataclass(frozen=True)
class SSSOMRecord:
    """NCIt<->upstream mapping with provenance.

    The five id/version fields are required; ``lifecycle_state``, ``review_status``,
    ``author`` and ``evidence`` carry defaults.
    """

    subject_id: str
    predicate_id: MappingPredicate
    object_id: str
    mapping_justification: str
    confidence: float
    subject_source_version: str
    object_source_version: str
    subject_system: str = "ncit"
    object_system: str = "uberon-cl"
    lifecycle_state: MappingLifecycle = "proposed"
    review_status: str = "unreviewed"
    author: str = ""
    # The independent signals that promoted this bridge (#122, D36). Empty for a
    # candidate; a record acquires evidence only by being promoted — `promote_candidate`
    # is the sole writer, and it sets `evidence` in the same `replace()` that flips the
    # predicate to `exactMatch` and the lifecycle to `validated`. So evidence rides only
    # on validated bridges, by construction, not by convention.
    #
    # `compare=False` keeps it out of equality and hashing, because evidence is
    # provenance, not identity: the mapping is the same bridge whatever justified it.
    # Nothing currently compares whole records or keys a set/dict on one anyway (the
    # `_one_per_pair` dedup keys on an explicit `(subject_id, object_id)` tuple), so
    # this is a guard against a future caller doing so, not a fix for a live path.
    evidence: tuple[Evidence, ...] = field(default=(), compare=False)

    @property
    def subject(self) -> EndpointIdentity:
        return EndpointIdentity(
            system=self.subject_system,
            version=self.subject_source_version,
            identifier=self.subject_id,
        )

    @property
    def object(self) -> EndpointIdentity:
        return EndpointIdentity(
            system=self.object_system,
            version=self.object_source_version,
            identifier=self.object_id,
        )

    def __post_init__(self) -> None:
        for field_name in (
            "subject_id",
            "subject_system",
            "object_id",
            "object_system",
            "mapping_justification",
            "subject_source_version",
            "object_source_version",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty")
        if self.predicate_id not in ALLOWED_PREDICATES:
            raise ValueError(f"predicate_id not allowed: {self.predicate_id}")
        if self.lifecycle_state not in LIFECYCLE_STATES:
            raise ValueError(f"lifecycle_state not allowed: {self.lifecycle_state}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")
