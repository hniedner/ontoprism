"""SSSOM records for NCIt alignments derived from corroborating terminologies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ontolib.repositories.xref.vocab import (
    ALLOWED_PREDICATES,
    LIFECYCLE_STATES,
    MappingLifecycle,
    MappingPredicate,
)

if TYPE_CHECKING:
    from ontolib.repositories.xref.evidence import Evidence


class _GenerationMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    ncit_source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class UberonCandidateGenerationMetadata(_GenerationMetadata):
    source: Literal["uberon-cl"] = "uberon-cl"
    uberon_source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_serving_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class UberonPromotionGenerationMetadata(_GenerationMetadata):
    source: Literal["uberon-cl-promotion"] = "uberon-cl-promotion"
    uberon_source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_serving_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class UberonPublisherGenerationMetadata(_GenerationMetadata):
    source: Literal["uberon-publisher-xref"] = "uberon-publisher-xref"
    uberon_source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_serving_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_assertion_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ncit_target_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class P334GenerationMetadata(_GenerationMetadata):
    source: Literal["ncit-p334-icdo32"] = "ncit-p334-icdo32"
    icdo_generation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    icdo_serving_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    ncit_p334_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


type GenerationSourceMetadata = Annotated[
    UberonCandidateGenerationMetadata
    | UberonPromotionGenerationMetadata
    | UberonPublisherGenerationMetadata
    | P334GenerationMetadata,
    Field(discriminator="source"),
]
generation_source_metadata_adapter = TypeAdapter(GenerationSourceMetadata)


class UberonReadIdentity(BaseModel):
    """Exact Uberon-capable repository identities expected by a read."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    ncit_source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_serving_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class IcdoReadIdentity(BaseModel):
    """Exact ICD-O-capable repository identities expected by a read."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    ncit_source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    icdo_generation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    icdo_serving_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class UberonReadIdentityValue:
    ncit_source_identity: str
    uberon_source_identity: str
    uberon_serving_identity: str

    def as_dict(self) -> dict[str, str]:
        return {
            "ncit_source_identity": self.ncit_source_identity,
            "uberon_source_identity": self.uberon_source_identity,
            "uberon_serving_identity": self.uberon_serving_identity,
        }


@dataclass(frozen=True, slots=True)
class IcdoReadIdentityValue:
    ncit_source_identity: str
    icdo_generation_identity: str
    icdo_serving_identity: str

    def as_dict(self) -> dict[str, str]:
        return {
            "ncit_source_identity": self.ncit_source_identity,
            "icdo_generation_identity": self.icdo_generation_identity,
            "icdo_serving_identity": self.icdo_serving_identity,
        }


def _uberon_identity_value(value: UberonReadIdentity) -> UberonReadIdentityValue:
    return UberonReadIdentityValue(
        ncit_source_identity=value.ncit_source_identity,
        uberon_source_identity=value.uberon_source_identity,
        uberon_serving_identity=value.uberon_serving_identity,
    )


def _icdo_identity_value(value: IcdoReadIdentity) -> IcdoReadIdentityValue:
    return IcdoReadIdentityValue(
        ncit_source_identity=value.ncit_source_identity,
        icdo_generation_identity=value.icdo_generation_identity,
        icdo_serving_identity=value.icdo_serving_identity,
    )


@dataclass(frozen=True, slots=True, init=False)
class XrefReadPolicy:
    """Sources relevant to one mapping read and their serving policy."""

    uberon: UberonReadIdentityValue | None
    icdo: IcdoReadIdentityValue | None
    allow_licensed: bool

    def __init__(
        self,
        uberon: UberonReadIdentity | None = None,
        icdo: IcdoReadIdentity | None = None,
        *,
        allow_licensed: bool = False,
    ) -> None:
        if uberon is None and icdo is None:
            raise ValueError("xref read policy must select at least one source family")
        object.__setattr__(
            self,
            "uberon",
            _uberon_identity_value(uberon) if uberon else None,
        )
        object.__setattr__(self, "icdo", _icdo_identity_value(icdo) if icdo else None)
        object.__setattr__(self, "allow_licensed", allow_licensed)

    def serves(self, mapping: MappingResult) -> bool:
        """Whether one current-generation mapping is eligible for consumers."""
        return mapping.lifecycle in {"validated", "active"} and (
            self.allow_licensed or not _mapping_is_licensed(mapping)
        )


class StaleXrefGenerationError(RuntimeError):
    """An active mapping generation is not bound to current repositories."""


class UnavailableXrefGenerationError(RuntimeError):
    """A requested mapping family has no active certified generation."""


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
    """One mapping row with both endpoint identities intact."""

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


_LICENSED_IDENTIFIER_PREFIXES = frozenset({"SNOMED", "ICD-O-3"})


def _mapping_is_licensed(mapping: MappingResult) -> bool:
    return any(
        endpoint.system == "icdo"
        or endpoint.identifier.partition(":")[0] in _LICENSED_IDENTIFIER_PREFIXES
        for endpoint in (mapping.subject, mapping.object)
    )


@dataclass(frozen=True, order=True, slots=True)
class CandidateContext:
    """Source-role route that made one NCIt filler eligible for mapping."""

    source_role: str
    source_filler: str
    normalized_axis: str

    def __post_init__(self) -> None:
        if not self.source_role.startswith("R") or not self.source_role[1:].isdigit():
            raise ValueError("source_role must be an NCIt role code")
        if (
            not self.source_filler.startswith("C")
            or not self.source_filler[1:].isdigit()
        ):
            raise ValueError("source_filler must be an NCIt concept code")
        if not self.normalized_axis.startswith("op:"):
            raise ValueError("normalized_axis must be an OntoPrism axis")


def _canonical_candidate_contexts(
    subject_id: str, contexts: tuple[CandidateContext, ...]
) -> tuple[CandidateContext, ...]:
    canonical = tuple(sorted(set(contexts)))
    for context in canonical:
        if context.source_filler != subject_id:
            raise ValueError("candidate context filler must match the mapping subject")
    return canonical


@dataclass(frozen=True)
class SSSOMRecord:
    """NCIt<->upstream mapping with provenance.

    IDs, source versions, predicate, justification, and confidence are required;
    systems, lifecycle, review status, author, evidence, and candidate context carry
    defaults.
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
    # The independent signals supporting this bridge (#122, D36). Candidate records are
    # normally empty; promoted and later quarantined records preserve their evidence.
    #
    # `compare=False` affects only dataclass equality and hashing. Publication
    # explicitly serializes evidence, so it remains part of generation identity.
    evidence: tuple[Evidence, ...] = field(default=(), compare=False)
    candidate_contexts: tuple[CandidateContext, ...] = ()

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
        object.__setattr__(
            self,
            "candidate_contexts",
            _canonical_candidate_contexts(self.subject_id, self.candidate_contexts),
        )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")
