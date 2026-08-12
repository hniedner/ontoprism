"""Certified Uberon/CL list, search, detail, and neighborhood endpoints."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import RepositoryMetadataReads, UberonSearch, UberonStore
from backend.repository_metadata import RepositoryUnhealthy
from ontolib.core.logging_config import get_logger
from ontolib.terminologies.uberon.models import (
    UberonConceptDetail,
    UberonNeighborhood,
    UberonSearchPage,
    UberonSource,
)

router = APIRouter(prefix="/api/v1/uberon", tags=["uberon"])
logger = get_logger(__name__)


async def _ready(metadata: RepositoryMetadataReads) -> str:
    repository = await metadata.uberon()
    if isinstance(repository, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            repository.model_dump(mode="json"),
        )
    return repository.source_identity


@router.get("/search", response_model=UberonSearchPage)
async def search(
    store: UberonStore,
    index: UberonSearch,
    metadata: RepositoryMetadataReads,
    q: Annotated[str, Query(min_length=1)],
    source: UberonSource | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UberonSearchPage:
    source_identity = await _ready(metadata)
    try:
        if await index.is_populated(source_identity):
            return await index.search(q, source=source, limit=limit, offset=offset)
    except SQLAlchemyError as exc:
        logger.warning("Uberon/CL FTS unavailable; using QLever: %s", exc)
    return await store.search(q, source=source, limit=limit, offset=offset)


@router.get("/list", response_model=UberonSearchPage)
async def list_concepts(
    store: UberonStore,
    metadata: RepositoryMetadataReads,
    source: UberonSource | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UberonSearchPage:
    await _ready(metadata)
    return await store.list_concepts(source=source, limit=limit, offset=offset)


@router.get("/concepts/{code}", response_model=UberonConceptDetail)
async def concept_detail(
    store: UberonStore, metadata: RepositoryMetadataReads, code: str
) -> UberonConceptDetail:
    await _ready(metadata)
    try:
        detail = await store.get_concept_detail(code)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Concept not found: {code}")
    return detail


@router.get("/concepts/{code}/neighborhood", response_model=UberonNeighborhood)
async def neighborhood(
    store: UberonStore,
    metadata: RepositoryMetadataReads,
    code: str,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
) -> UberonNeighborhood:
    await _ready(metadata)
    try:
        return await store.get_neighborhood(code, depth=depth)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
