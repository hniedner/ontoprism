"""Read models for the decomposition serve layer (#9) — pydantic, serialized by the API.

Mirrors the ``op:`` graph written by the engine (design §4.2): a source concept flagged
``legacy-precoordinated`` with a list of constituents (axis + filler + provenance).
"""

from __future__ import annotations

import re
from typing import Self, get_args

from pydantic import Field, NonNegativeInt, field_validator, model_validator

from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.decomposition.models import AxisSource, ConceptOutcome
from ontolib.decomposition.provenance_models import ConceptReviewFlag, ReviewFlagKind
from ontolib.decomposition.vocab import PublicationNotice, PublicationStatus
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


def _canonical_source_group_ids(source_group_ids: tuple[str, ...]) -> tuple[str, ...]:
    canonical = tuple(sorted(set(source_group_ids)))
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in canonical):
        raise ValueError("source_group_ids must contain SHA-256 identities")
    return canonical


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
    axis_ambiguous: bool = False
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

    @field_validator("source_group_ids")
    @classmethod
    def _source_group_ids_are_canonical(
        cls, source_group_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _canonical_source_group_ids(source_group_ids)

    @model_validator(mode="after")
    def _source_roles_match_axis_source(self) -> Self:
        _validate_axis_source_roles(self.axis_source, self.source_roles)
        if (self.normalized_group_id is None) != (self.normalized_group_label is None):
            raise ValueError("normalized group identity and label must be paired")
        return self


class ConceptDecomposition(StrictBoundaryModel):
    """A concept's decomposition as read from the ``ncit_decomposed`` named graph.

    ``is_legacy_precoordinated`` is False (and ``constituents`` empty) for a concept the
    engine has not decomposed — the endpoint still resolves, so the UI can show "not
    decomposed" rather than 404.
    """

    code: str
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]+$")
    publication_status: PublicationStatus | None = None
    publication_notice: PublicationNotice | None = None
    outcome: ConceptOutcome | None = None
    outcome_reason: str | None = Field(default=None, min_length=1)
    review_flags: list[ConceptReviewFlag] = Field(default_factory=list)
    is_legacy_precoordinated: bool
    decomposed_on: str | None = None
    constituents: list[DecompositionConstituent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _publication_shape_is_paired(self) -> Self:
        _require_publication_shape(self)
        return self


class PublicationProgress(StrictBoundaryModel):
    """Backend-computed counts for the graph's currently published D93 run."""

    run_id: str = Field(min_length=1)
    publication_status: PublicationStatus
    publication_notice: PublicationNotice
    total_concepts: int = Field(ge=0)
    outcome_counts: dict[ConceptOutcome, NonNegativeInt]
    review_flag_counts: dict[ReviewFlagKind, NonNegativeInt]

    @model_validator(mode="after")
    def _counts_are_complete(self) -> Self:
        outcomes = set(get_args(ConceptOutcome))
        flags = set(get_args(ReviewFlagKind))
        if set(self.outcome_counts) != outcomes:
            raise ValueError("publication progress must include every D93 outcome")
        if set(self.review_flag_counts) != flags:
            raise ValueError("publication progress must include every D93 review flag")
        if sum(self.outcome_counts.values()) != self.total_concepts:
            raise ValueError("publication outcome counts do not sum to total concepts")
        return self


def _require_publication_shape(value: ConceptDecomposition) -> None:
    fields = (
        value.publication_status,
        value.publication_notice,
        value.outcome,
        value.outcome_reason,
    )
    if any(item is not None for item in fields) and any(
        item is None for item in fields
    ):
        raise ValueError("published concept metadata must be complete")
    if value.review_flags and value.outcome is None:
        raise ValueError("review flags require a published concept outcome")
