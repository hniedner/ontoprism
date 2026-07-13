"""Read models for the decomposition serve layer (#9) — pydantic, serialized by the API.

Mirrors the ``op:`` graph written by the engine (design §4.2): a source concept flagged
``legacy-precoordinated`` with a list of constituents (axis + filler + provenance).
"""

from pydantic import BaseModel

from ontolib.repositories.xref.vocab import EXACT_MATCH


class UpstreamMapping(BaseModel):
    """An upstream (Uberon/CL) equivalent of an NCIt code, from the xref layer.

    ``predicate`` is the full SKOS mapping IRI (verbatim); ``lifecycle`` is the
    curation state (``proposed``/``validated``/``active``/``quarantined``/``retired``);
    ``confidence`` is the mapping confidence [0,1].  A derived ``is_identity``
    convenience property flags ``exactMatch + {validated,active}``.
    """

    object_id: str
    predicate: str
    lifecycle: str
    confidence: float = 0.0

    @property
    def is_identity(self) -> bool:
        return self.predicate == EXACT_MATCH and self.lifecycle in (
            "validated",
            "active",
        )


class DecompositionConstituent(BaseModel):
    """One decomposed constituent: the axis and the concept that fills it.

    ``axis`` is the NCIt role code (or an ``op:`` axis such as ``op:Morphology``);
    ``filler`` is the constituent concept code. Labels are resolved for display when
    available.
    """

    axis: str
    axis_label: str | None = None
    filler: str
    filler_label: str | None = None
    axis_source: str
    most_specific: bool = False
    upstream: list[UpstreamMapping] = []


class ConceptDecomposition(BaseModel):
    """A concept's decomposition as read from the ``ncit_decomposed`` named graph.

    ``is_legacy_precoordinated`` is False (and ``constituents`` empty) for a concept the
    engine has not decomposed — the endpoint still resolves, so the UI can show "not
    decomposed" rather than 404.
    """

    code: str
    is_legacy_precoordinated: bool
    decomposed_on: str | None = None
    constituents: list[DecompositionConstituent] = []
