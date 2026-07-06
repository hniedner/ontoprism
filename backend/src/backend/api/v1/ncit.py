"""NCIt repository read endpoints: concept detail, search, graph neighborhood."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import Embeddings, NcitSearch, NcitStore
from ontolib.core.logging_config import get_logger
from ontolib.terminologies.ncit.models import (
    ConceptDetail,
    Neighborhood,
    SearchPage,
    SimilarConcept,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ncit", tags=["ncit"])


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
