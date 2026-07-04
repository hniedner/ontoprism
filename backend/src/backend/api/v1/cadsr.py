"""caDSR CDE repository endpoints: CDE detail, search, and the NCIt concept join."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import CadsrRepo, Embeddings
from fairlib.repositories.cadsr.models import (
    CdeDetail,
    CdeSearchPage,
    CdeSummary,
    SimilarCde,
)

router = APIRouter(prefix="/api/v1/cadsr", tags=["cadsr"])


@router.get("/search", response_model=CdeSearchPage)
def search(
    repo: CadsrRepo,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CdeSearchPage:
    """Search caDSR CDEs by short/long name and definition."""
    try:
        return repo.search(q, limit=limit, offset=offset)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/cdes/{public_id}", response_model=CdeDetail)
def cde_detail(
    repo: CadsrRepo,
    public_id: str,
    version: Annotated[str | None, Query()] = None,
) -> CdeDetail:
    """Return a CDE with its permissible values and NCIt concept links."""
    try:
        cde = repo.get_cde(public_id, version)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if cde is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"CDE not found: {public_id}")
    return cde


@router.get("/cdes/{public_id}/similar", response_model=list[SimilarCde])
async def similar_cdes(
    repo: CadsrRepo,
    embeddings: Embeddings,
    public_id: str,
    version: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SimilarCde]:
    """Semantically similar CDEs via 768-dim embeddings (pgvector cosine)."""
    cde = repo.get_cde(public_id, version)  # resolve the concrete version
    if cde is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"CDE not found: {public_id}")
    try:
        hits = await embeddings.similar_cde(cde.public_id, cde.version, limit=limit)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    summaries = repo.summaries_for([doc_id for doc_id, _ in hits])
    results: list[SimilarCde] = []
    for doc_id, score in hits:
        summary = summaries.get(doc_id)
        if summary is not None:
            results.append(SimilarCde(**summary.model_dump(), score=score))
    return results


@router.get("/concepts/{concept_code}/cdes", response_model=list[CdeSummary])
def cdes_for_concept(
    repo: CadsrRepo,
    concept_code: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CdeSummary]:
    """Return CDEs mapped to an NCIt concept — the caDSR↔NCIt cross-link."""
    try:
        return repo.find_cdes_by_concept(concept_code, limit=limit)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
