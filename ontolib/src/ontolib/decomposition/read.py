"""Pure assembly of SPARQL rows into a ``ConceptDecomposition`` (design §9 read layer).

Kept separate from query execution so every parsing rule is unit-tested without a store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontolib.decomposition import vocab
from ontolib.decomposition.read_models import (
    ConceptDecomposition,
    DecompositionConstituent,
    UpstreamMapping,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

Row = dict[str, str | None]


def _local(iri: str) -> str:
    """Local name from an IRI (``…#C6135`` -> ``C6135``)."""
    return iri.rsplit("#", 1)[-1]


def _axis_code(iri: str) -> str:
    """Axis identifier: an ``op:`` axis (e.g. ``op:Morphology``) keeps its prefix; an
    NCIt role IRI reduces to its code (``R88``)."""
    if iri.startswith(vocab.ONTOPRISM_NS):
        return f"op:{iri[len(vocab.ONTOPRISM_NS) :]}"
    return _local(iri)


def _as_bool(value: str | None) -> bool:
    return value in ("true", "1")


def decomposition_from_rows(code: str, rows: Iterable[Row]) -> ConceptDecomposition:
    """Fold the (repeating) result rows into one decomposition for *code*.

    Status/date repeat on every row (SPARQL cross-product with the constituents); the
    constituents are de-duplicated by (axis, filler) and sorted for determinism.
    """
    status: str | None = None
    decomposed_on: str | None = None
    constituents: dict[tuple[str, str], DecompositionConstituent] = {}

    for row in rows:
        status = status or row.get("status")
        decomposed_on = decomposed_on or row.get("decomposedOn")
        axis_iri = row.get("axis")
        filler_iri = row.get("filler")
        if not axis_iri or not filler_iri:
            continue
        constituents[(axis_iri, filler_iri)] = DecompositionConstituent(
            axis=_axis_code(axis_iri),
            filler=_local(filler_iri),
            axis_source=row.get("axisSource") or "role",
            most_specific=_as_bool(row.get("mostSpecific")),
        )

    return ConceptDecomposition(
        code=code,
        is_legacy_precoordinated=status == vocab.LEGACY_PRECOORDINATED,
        decomposed_on=decomposed_on,
        constituents=sorted(constituents.values(), key=lambda c: (c.axis, c.filler)),
    )


def attach_upstream(
    decomp: ConceptDecomposition,
    upstream_by_filler: dict[str, list[UpstreamMapping]],
) -> ConceptDecomposition:
    new_constituents = [
        c.model_copy(update={"upstream": upstream_by_filler.get(c.filler, [])})
        for c in decomp.constituents
    ]
    return decomp.model_copy(update={"constituents": new_constituents})
