"""NCIt repository read endpoints: concept detail, search, graph neighborhood,
mappings."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, computed_field
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import (
    Embeddings,
    NcitClient,
    NcitSearch,
    NcitStore,
    XrefReads,
)
from ontolib.core.logging_config import get_logger
from ontolib.decomposition.read import attach_upstream, decomposition_from_rows
from ontolib.decomposition.read_models import ConceptDecomposition, UpstreamMapping
from ontolib.decomposition.read_queries import build_decomposition_query
from ontolib.repositories.xref.vocab import EXACT_MATCH
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.models import (
    ConceptDetail,
    Neighborhood,
    SearchPage,
    SimilarConcept,
)
from ontolib.terminologies.oxigraph_http_client import safe_iri

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ncit", tags=["ncit"])


class MappingEntry(BaseModel):
    """One upstream mapping for an NCIt concept, serialized for the API.

    ``is_identity`` mirrors ``UpstreamMapping.is_identity``: true when
    the predicate is ``exactMatch`` and lifecycle is ``validated``/``active``.
    """

    object_id: str
    predicate: str
    lifecycle: str
    confidence: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_identity(self) -> bool:
        return self.predicate == EXACT_MATCH and self.lifecycle in (
            "validated",
            "active",
        )


class ConceptMappings(BaseModel):
    """All upstream mappings for one NCIt concept code."""

    code: str
    mappings: list[MappingEntry]


async def _attach_xref_upstream(
    decomposition: ConceptDecomposition,
    xref_store: XrefReads,
    filler_codes: list[str],
) -> ConceptDecomposition:
    if filler_codes:
        upstream_rows = await xref_store.mappings_by_subjects(set(filler_codes))
        upstream_by_filler = {
            code: [
                UpstreamMapping(object_id=o, predicate=p, lifecycle=lc, confidence=c)
                for (o, p, lc, c) in rows
            ]
            for code, rows in upstream_rows.items()
        }
        decomposition = attach_upstream(decomposition, upstream_by_filler)
    return decomposition


@router.get("/search", response_model=SearchPage)
async def search(
    store: NcitStore,
    index: NcitSearch,
    q: Annotated[str, Query(min_length=1, description="Search term")],
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchPage:
    """Search NCIt by label/synonyms; served from the FTS cache when populated.

    Falls back to the live SPARQL scan when the cache is empty or unreachable, so
    search always works (the store remains the source of truth).
    """
    try:
        if await index.is_populated():
            return await index.search(q, limit=limit, offset=offset)
    except SQLAlchemyError as exc:
        logger.warning("NCIt FTS cache unavailable, falling back to SPARQL: %s", exc)
    return await store.search(q, limit=limit, offset=offset)


@router.get("/list", response_model=SearchPage)
async def list_concepts(
    store: NcitStore,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchPage:
    """List concepts in natural order — powers no-search browse of the repository."""
    return await store.list_concepts(limit=limit, offset=offset)


@router.get("/concepts/{code}", response_model=ConceptDetail)
async def concept_detail(store: NcitStore, code: str) -> ConceptDetail:
    """Return full concept detail — parents, roles, associations, incoming roles."""
    try:
        detail = await store.get_concept_detail(code)
    except ValueError as exc:  # malformed code rejected by the IRI guard
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Concept not found: {code}")
    return detail


@router.get("/concepts/{code}/similar", response_model=list[SimilarConcept])
async def similar_concepts(
    store: NcitStore,
    embeddings: Embeddings,
    code: str,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SimilarConcept]:
    """Semantically similar concepts via 768-dim embeddings (pgvector cosine)."""
    try:
        hits = await embeddings.similar_ncit(code, limit=limit)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    labels = await store.labels_for([c for c, _ in hits])
    return [
        SimilarConcept(code=c, label=labels.get(c), score=score) for c, score in hits
    ]


@router.get("/concepts/{code}/neighborhood", response_model=Neighborhood)
async def neighborhood(
    store: NcitStore,
    code: str,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
) -> Neighborhood:
    """Return a concept-centered subgraph for the graph explorer (expand-on-demand)."""
    try:
        return await store.get_neighborhood(code, depth=depth)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc


@router.get("/concepts/{code}/mappings", response_model=ConceptMappings)
async def concept_mappings(
    store: NcitStore,
    xref_store: XrefReads,
    code: str,
) -> ConceptMappings:
    """Return all upstream mappings for an NCIt concept code.

    Searches both by subject (NCIt code as subject) and by object
    (NCIt code as object of an upstream-to-NCIt mapping), so
    ``$translate``-style round-trips are covered from this endpoint alone.
    """
    try:
        safe_iri(code, NCIT_NS)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    upstream = await xref_store.mappings_by_subjects({code})
    reverse = await xref_store.mappings_by_objects({code})
    entries: list[MappingEntry] = [
        MappingEntry(object_id=o, predicate=p, lifecycle=lc, confidence=c)
        for rows in upstream.values()
        for (o, p, lc, c) in rows
    ]
    entries.extend(
        MappingEntry(object_id=s, predicate=p, lifecycle=lc, confidence=c)
        for rows in reverse.values()
        for (s, p, lc, c) in rows
    )
    return ConceptMappings(code=code, mappings=entries)


@router.get("/concepts/{code}/decomposition", response_model=ConceptDecomposition)
async def concept_decomposition(
    client: NcitClient,
    store: NcitStore,
    xref_store: XrefReads,
    code: str,
) -> ConceptDecomposition:
    """Return the concept's decomposition from the additive ``ncit_decomposed`` graph.

    Resolves even for a concept the engine has not decomposed
    (``is_legacy_precoordinated = false``, no constituents) so the UI can show "not
    decomposed" rather than a 404. Filler labels are resolved for display, and
    upstream xref mappings (Uberon/CL equivalents) are attached per constituent.
    """
    try:
        query = build_decomposition_query(code)
    except ValueError as exc:  # code failed the IRI-safety guard
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    rows = await client.select(query)
    decomposition = decomposition_from_rows(code, rows)
    filler_codes = [c.filler for c in decomposition.constituents]
    labels = await store.labels_for(filler_codes) if filler_codes else {}
    for constituent in decomposition.constituents:
        constituent.filler_label = labels.get(constituent.filler)
    decomposition = await _attach_xref_upstream(decomposition, xref_store, filler_codes)
    return decomposition
