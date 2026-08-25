"""Read models for the decomposition serve layer (#9) — pydantic, serialized by the API.

Mirrors the ``op:`` graph written by the engine (design §4.2): a source concept flagged
``legacy-precoordinated`` with a list of constituents (axis + filler + provenance).
"""

from __future__ import annotations

import re
from typing import Self

from pydantic import Field, field_validator, model_validator

from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.decomposition.models import AxisSource  # noqa: TC001 (Pydantic runtime)
from ontolib.repositories.xref.vocab import EXACT_MATCH


class UpstreamMapping(StrictBoundaryModel):
    """An upstream (Uberon/CL) equivalent of an NCIt code, from the xref layer.

    ``predicate`` is the full SKOS mapping IRI (verbatim); ``lifecycle`` is the
    curation state (``proposed``/``validated``/``active``/``quarantined``/``retired``);
    ``confidence`` is the mapping confidence [0,1].  A derived ``is_identity``
    convenience property flags ``exactMatch + {validated,active}``.
    """

    object_id: str
    predicate: str
    lifecycle: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def is_identity(self) -> bool:
        return self.predicate == EXACT_MATCH and self.lifecycle in (
            "validated",
            "active",
        )


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
    group: str | None = None
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
        if self.axis_source == "role" and not self.source_roles:
            raise ValueError("role-derived constituent requires source_roles")
        if self.axis_source in {"parent", "nlp"} and self.source_roles:
            raise ValueError("parent/NLP constituents must have empty source_roles")
        return self


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
