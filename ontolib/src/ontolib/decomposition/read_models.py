"""Read models for the decomposition serve layer (#9) — pydantic, serialized by the API.

Mirrors the ``op:`` graph written by the engine (design §4.2): a source concept flagged
``legacy-precoordinated`` with a list of constituents (axis + filler + provenance).
"""

from pydantic import BaseModel


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
