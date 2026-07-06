"""PubMed repository endpoints: article search, article detail, related articles.

A thin pass-through to the async :class:`PubMedClient` (NCBI E-utilities). Direct
search only — the natural-language / LLM query-building layer from fairdata is not
ported.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.dependencies import PubMed
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.repositories.pubmed.models import (
    PubMedArticleDetail,
    PubMedSearchResult,
    RelatedArticlesResult,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/pubmed", tags=["pubmed"])

LinkType = Literal["similar", "cited_by", "references"]


class PubMedSearchRequest(BaseModel):
    """Search parameters for PubMed."""

    query: Annotated[str, Field(min_length=1, max_length=2000)]
    retmax: Annotated[int, Field(ge=1, le=100)] = 20


def _pmid_or_400(pmid: str) -> str:
    if not pmid.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid PMID: {pmid}")
    return pmid


@router.post("/search", response_model=PubMedSearchResult)
async def search(client: PubMed, body: PubMedSearchRequest) -> PubMedSearchResult:
    """Search PubMed and return resolved article summaries."""
    try:
        return await client.search_articles(body.query, retmax=body.retmax)
    except StorageError as exc:
        logger.warning("PubMed search failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "PubMed request failed."
        ) from exc


@router.get("/{pmid}", response_model=PubMedArticleDetail)
async def article_detail(client: PubMed, pmid: str) -> PubMedArticleDetail:
    """Return one article by PMID (404 if unknown, 400 if the PMID is malformed)."""
    _pmid_or_400(pmid)
    try:
        article = await client.get_article(pmid)
    except StorageError as exc:
        logger.warning("PubMed article fetch failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "PubMed request failed."
        ) from exc
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Article not found: {pmid}")
    return article


@router.get("/{pmid}/related", response_model=RelatedArticlesResult)
async def related_articles(
    client: PubMed,
    pmid: str,
    link_type: Annotated[LinkType, Query()] = "similar",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RelatedArticlesResult:
    """Return related-article PMIDs (similar / cited_by / references) for an article."""
    _pmid_or_400(pmid)
    try:
        return await client.get_related_pmids(pmid, link_type=link_type, limit=limit)
    except StorageError as exc:
        logger.warning("PubMed related-articles fetch failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "PubMed request failed."
        ) from exc
