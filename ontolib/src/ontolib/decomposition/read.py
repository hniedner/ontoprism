"""Pure assembly of SPARQL rows into a ``ConceptDecomposition`` (design §9 read layer).

Kept separate from query execution so every parsing rule is unit-tested without a store.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ontolib.decomposition import vocab
from ontolib.decomposition.read_models import (
    ConceptDecomposition,
    DecompositionConstituent,
    UpstreamMapping,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

Row = Mapping[str, str | None]
_SHA256_LENGTH = 64


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


def _source_definition_id(iri: str | None) -> str | None:
    if iri is None:
        return None
    if not iri.startswith(vocab.DEFINITION_FACT_NS):
        raise ValueError("source definition fact is outside the OntoPrism namespace")
    fact_id = iri.removeprefix(vocab.DEFINITION_FACT_NS)
    if len(fact_id) != _SHA256_LENGTH or any(
        c not in "0123456789abcdef" for c in fact_id
    ):
        raise ValueError("source definition fact does not contain a SHA-256 ID")
    return fact_id


def _constituent_from_row(
    axis_iri: str,
    filler_iri: str,
    row: Row,
) -> DecompositionConstituent:
    source_id = _source_definition_id(row.get("sourceDefinitionFact"))
    return DecompositionConstituent(
        axis=_axis_code(axis_iri),
        filler=_local(filler_iri),
        axis_source=row.get("axisSource") or "role",
        most_specific=_as_bool(row.get("mostSpecific")),
        needs_review=_as_bool(row.get("needsReview")),
        group=row.get("group"),
        source_definition_ids=((source_id,) if source_id is not None else ()),
    )


def _without_source_ids(
    constituent: DecompositionConstituent,
) -> DecompositionConstituent:
    return constituent.model_copy(update={"source_definition_ids": ()})


def _merge_constituent(
    existing: DecompositionConstituent | None,
    candidate: DecompositionConstituent,
) -> DecompositionConstituent:
    if existing is None:
        return candidate
    if _without_source_ids(existing) != _without_source_ids(candidate):
        raise ValueError("one constituent resolved to conflicting persisted fields")
    source_ids = tuple(
        sorted(
            set(existing.source_definition_ids) | set(candidate.source_definition_ids)
        )
    )
    return existing.model_copy(update={"source_definition_ids": source_ids})


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
        key = (axis_iri, filler_iri)
        candidate = _constituent_from_row(axis_iri, filler_iri, row)
        constituents[key] = _merge_constituent(
            constituents.get(key),
            candidate,
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
