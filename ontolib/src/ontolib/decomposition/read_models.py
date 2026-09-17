"""Read models for the decomposition serve layer (#9) — pydantic, serialized by the API.

Mirrors the ``op:`` graph written by the engine (design §4.2): a source concept flagged
``legacy-precoordinated`` with a list of constituents (axis + filler + provenance).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.decomposition.models import AxisSource
from ontolib.repositories.xref.vocab import (
    EXACT_MATCH,
    MappingLifecycle,
    MappingPredicate,
)


class UpstreamMapping(StrictBoundaryModel):
    """An upstream (Uberon/CL) equivalent of an NCIt code, from the xref layer.

    ``predicate`` is the full SKOS mapping IRI (verbatim); ``lifecycle`` is the
    curation state (``proposed``/``validated``/``active``/``quarantined``/``retired``);
    ``confidence`` is the mapping confidence [0,1].  A derived ``is_identity``
    convenience property flags ``exactMatch + {validated,active}``.
    """

    object_id: str
    predicate: MappingPredicate
    lifecycle: MappingLifecycle
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def is_identity(self) -> bool:
        return self.predicate == EXACT_MATCH and self.lifecycle in (
            "validated",
            "active",
        )


def _validate_axis_source_roles(
    axis_source: AxisSource, source_roles: tuple[str, ...]
) -> None:
    if axis_source == "role" and not source_roles:
        raise ValueError("role-derived constituent requires source_roles")
    if axis_source in {"parent", "nlp"} and source_roles:
        raise ValueError("parent/NLP constituents must have empty source_roles")


def _validate_source_group_ids(source_group_ids: tuple[str, ...]) -> None:
    if tuple(sorted(set(source_group_ids))) != source_group_ids:
        raise ValueError("source_group_ids must be canonical and unique")
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in source_group_ids):
        raise ValueError("source_group_ids must contain SHA-256 identities")


class DecompositionConstituent(StrictBoundaryModel):
    """One decomposed constituent: the axis and the concept that fills it.

    ``axis`` is a normalized ``op:`` relation (or a legacy NCIt role code);
    ``source_roles`` preserves the NCIt roles from which a normalized relation was
    projected. ``filler`` is the constituent concept code. Labels are resolved for
    display when available.
    """

    axis: str
    axis_label: str | None = None
    filler: str
    filler_label: str | None = None
    axis_source: AxisSource
    source_roles: tuple[str, ...] = ()
    most_specific: bool = False
    needs_review: bool = False
    axis_ambiguity_group_id: str | None = None
    source_group_ids: tuple[str, ...] = ()
    normalized_group_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_group_label: str | None = None
    source_definition_ids: tuple[str, ...] = ()
    upstream: list[UpstreamMapping] = Field(default_factory=list)

    @field_validator("source_roles")
    @classmethod
    def _source_roles_are_canonical_ncit_roles(
        cls, source_roles: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(re.fullmatch(r"R[0-9]+", role) is None for role in source_roles):
            raise ValueError("source_roles must contain only NCIt role codes")
        return tuple(sorted(set(source_roles)))

    @model_validator(mode="after")
    def _source_roles_match_axis_source(self) -> Self:
        _validate_axis_source_roles(self.axis_source, self.source_roles)
        _validate_source_group_ids(self.source_group_ids)
        if (self.normalized_group_id is None) != (self.normalized_group_label is None):
            raise ValueError("normalized group identity and label must be paired")
        return self


class NotAcceptedProjection(StrictBoundaryModel):
    status: Literal["not-accepted"] = "not-accepted"


AcceptanceWithholdingReason = Literal[
    "missing-persisted-assessment",
    "missing-source-fact",
    "missing-source-occurrence",
    "missing-source-role",
    "policy-non-applicable",
    "evidence-contradiction",
    "evidence-ambiguity",
    "review-required",
    "unknown-outcome",
    "residual",
    "residual-unknown",
    "proposal-quarantined",
]


class AcceptanceCompleteness(StrictBoundaryModel):
    included_count: int = Field(ge=0)
    withheld_count: int = Field(ge=0)
    reasons: tuple[AcceptanceWithholdingReason, ...]

    @model_validator(mode="after")
    def _canonical_reasons_match_counts(self) -> Self:
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("acceptance completeness reasons must be canonical")
        if self.withheld_count and not self.reasons:
            raise ValueError("acceptance completeness reasons disagree with count")
        return self


class ProjectedEffectiveProjection(StrictBoundaryModel):
    status: Literal["projected"]
    source_release: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    representation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_status: Literal["projected-effective"]
    acceptance_basis: Literal["machine-evidence", "human-adjudication"]
    official_source_url: str
    official_source_preserved: Literal[True]
    completeness: AcceptanceCompleteness


class ReviewRequiredExcludedProjection(StrictBoundaryModel):
    status: Literal["review-required-excluded"]
    source_release: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    representation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_status: Literal["excluded-from-accepted-effective-projection"]
    exclusion_summary: Literal[
        "Review required — excluded from accepted effective projection"
    ]
    official_source_preserved: Literal[True]
    acceptance_basis: Literal["machine-evidence", "human-adjudication"]
    official_source_url: str
    completeness: AcceptanceCompleteness


class UnknownWithheldProjection(StrictBoundaryModel):
    status: Literal["unknown-withheld"]
    source_release: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    representation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_status: Literal["withheld-from-effective"]
    acceptance_basis: Literal["machine-evidence", "human-adjudication"]
    official_source_preserved: Literal[True]
    official_source_url: str
    completeness: AcceptanceCompleteness


class ResidualWithheldProjection(StrictBoundaryModel):
    status: Literal["residual-withheld"]
    source_release: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    representation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_status: Literal["withheld-from-effective"]
    acceptance_basis: Literal["machine-evidence", "human-adjudication"]
    official_source_preserved: Literal[True]
    official_source_url: str
    completeness: AcceptanceCompleteness


class EvidenceGapWithheldProjection(StrictBoundaryModel):
    status: Literal["withheld-evidence-gap"]
    source_release: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    representation_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_status: Literal["withheld-from-effective"]
    acceptance_basis: Literal["machine-evidence", "human-adjudication"]
    official_source_preserved: Literal[True]
    official_source_url: str
    completeness: AcceptanceCompleteness


AcceptanceProjection = Annotated[
    NotAcceptedProjection
    | ProjectedEffectiveProjection
    | ReviewRequiredExcludedProjection
    | UnknownWithheldProjection
    | ResidualWithheldProjection
    | EvidenceGapWithheldProjection,
    Field(discriminator="status"),
]


class ConceptDecomposition(StrictBoundaryModel):
    """A concept's decomposition as read from the ``ncit_decomposed`` named graph.

    ``is_legacy_precoordinated`` is False (and ``constituents`` empty) for a concept the
    engine has not decomposed — the endpoint still resolves, so the UI can show "not
    decomposed" rather than 404.
    """

    code: str
    is_legacy_precoordinated: bool
    decomposed_on: str | None = None
    constituents: list[DecompositionConstituent] = Field(default_factory=list)
    acceptance: AcceptanceProjection = Field(default_factory=NotAcceptedProjection)
