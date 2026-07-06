"""Async client for the NCBI PubMed E-utilities (ESearch / ESummary / EFetch / ELink).

Transport + orchestration only; JSON/XML → model mapping lives in :mod:`parser`. The
public NCBI endpoints allow ~3 requests/second without an API key, so requests are
throttled to a configurable rate. Direct search only — fairdata's LLM query-building
and reranking are intentionally not ported.
"""

from __future__ import annotations

import asyncio
import time
from http import HTTPStatus
from typing import Any, Self
from xml.etree.ElementTree import ParseError

import httpx

from ontolib.common.error_handling import retry_with_backoff
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.repositories.pubmed.models import (
    PubMedArticleDetail,
    PubMedArticleSummary,
    PubMedSearchResult,
    RelatedArticlesResult,
)
from ontolib.repositories.pubmed.parser import parse_efetch_xml, parse_esummary

logger = get_logger(__name__)

DEFAULT_EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_MAX_RETMAX = 100
# ELink linkname per related-article kind (fairdata parity).
_LINK_NAMES = {
    "similar": "pubmed_pubmed",
    "cited_by": "pubmed_pubmed_citedin",
    "references": "pubmed_pubmed_refs",
}
_RETRYABLE = (httpx.TransportError, httpx.TimeoutException)


class PubMedClient:
    """Minimal async client over the NCBI E-utilities REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_EUTILS_URL,
        *,
        api_key: str | None = None,
        requests_per_second: float = 3.0,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
    ) -> None:
        """Create a client for *base_url* (default: the public E-utilities API)."""
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._min_interval = (
            1.0 / requests_per_second if requests_per_second > 0 else 0.0
        )
        self._next_allowed = 0.0
        self._throttle_lock = asyncio.Lock()
        self._timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client and its connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        # Respect NCBI's rate limit even under concurrency: reserve the next slot under
        # a lock (so parallel callers don't read the same timestamp and burst together),
        # then sleep until it's due.
        if self._min_interval <= 0:
            return
        async with self._throttle_lock:
            now = time.monotonic()
            wait = max(0.0, self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self._min_interval
        if wait:
            await asyncio.sleep(wait)

    @retry_with_backoff(retryable_exceptions=_RETRYABLE)
    async def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        if self._api_key:
            params = {**params, "api_key": self._api_key}
        return await self._get_client().get(
            f"{self._base_url}{path}", params=params, follow_redirects=True
        )

    async def _request(self, path: str, params: dict[str, Any]) -> httpx.Response:
        await self._throttle()
        try:
            response = await self._get(path, params)
        except _RETRYABLE as exc:
            raise StorageError(
                f"PubMed E-utilities transport error for {path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code != HTTPStatus.OK:
            raise StorageError(
                f"PubMed E-utilities request failed: HTTP {response.status_code} "
                f"for {path} — {response.text[:200]}"
            )
        return response

    async def _json(self, path: str, params: dict[str, Any]) -> Any:
        response = await self._request(path, params)
        try:
            return response.json()
        except ValueError as exc:
            raise StorageError(
                f"PubMed E-utilities response was not valid JSON for {path}: {exc}"
            ) from exc

    async def search_articles(
        self, query: str, *, retmax: int = 20, sort: str = "relevance"
    ) -> PubMedSearchResult:
        """Search PubMed for *query*; resolve the id list to article summaries.

        Raises:
            StorageError: on a transport error or a non-2xx / non-JSON response.
        """
        esearch = await self._json(
            "/esearch.fcgi",
            {
                "db": "pubmed",
                "term": query,
                "retmax": max(1, min(retmax, _MAX_RETMAX)),
                "sort": sort,
                "retmode": "json",
            },
        )
        pmids, total = _parse_esearch(esearch)
        if not pmids:
            return PubMedSearchResult(query=query, total=total, articles=[])
        summary = await self._json(
            "/esummary.fcgi",
            {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
        )
        return PubMedSearchResult(
            query=query, total=total, articles=_parse_esummary_docs(summary, pmids)
        )

    async def get_article(self, pmid: str) -> PubMedArticleDetail | None:
        """Fetch one article by PMID via EFetch, or None if PubMed returns no record.

        Raises:
            StorageError: on a transport error or a non-2xx response.
        """
        response = await self._request(
            "/efetch.fcgi",
            {"db": "pubmed", "id": pmid, "retmode": "xml"},
        )
        try:
            articles = parse_efetch_xml(response.text)
        except (ParseError, ValueError) as exc:
            # Upstream returned truncated / non-XML / entity-bearing content — an
            # upstream fault (→ 502), not a server error.
            raise StorageError(
                f"PubMed EFetch returned unparseable XML for {pmid}: {exc}"
            ) from exc
        return articles[0] if articles else None

    async def get_related_pmids(
        self, pmid: str, *, link_type: str = "similar", limit: int = 20
    ) -> RelatedArticlesResult:
        """Return related-article PMIDs for *pmid* via ELink.

        Raises:
            ValueError: if *link_type* is not one of similar/cited_by/references.
            StorageError: on a transport error or a non-2xx / non-JSON response.
        """
        linkname = _LINK_NAMES.get(link_type)
        if linkname is None:
            raise ValueError(f"Invalid related link_type: {link_type!r}")
        data = await self._json(
            "/elink.fcgi",
            {
                "db": "pubmed",
                "dbfrom": "pubmed",
                "id": pmid,
                "linkname": linkname,
                "retmode": "json",
            },
        )
        related = _extract_elink_pmids(data, linkname, source_pmid=pmid)
        return RelatedArticlesResult(
            pmid=pmid, link_type=link_type, related_pmids=related[:limit]
        )


def _parse_esearch(esearch: Any) -> tuple[list[str], int]:
    """Return (pmids, total) from an ESearch JSON document."""
    result = esearch.get("esearchresult", {}) if isinstance(esearch, dict) else {}
    idlist = result.get("idlist") or []
    pmids = [str(i) for i in idlist if isinstance(i, str)]
    total = int(result.get("count", len(pmids)) or 0)
    return pmids, total


def _parse_esummary_docs(summary: Any, pmids: list[str]) -> list[PubMedArticleSummary]:
    """Map an ESummary JSON document (keyed by uid) to article summaries."""
    docs = summary.get("result", {}) if isinstance(summary, dict) else {}
    uids = docs.get("uids") or pmids
    return [
        parse_esummary(str(uid), docs[uid])
        for uid in uids
        if isinstance(docs.get(uid), dict)
    ]


def _linkset_pmids(linkset: Any, linkname: str) -> list[str]:
    """Return the target PMIDs for *linkname* within one ELink linkset."""
    if not isinstance(linkset, dict):
        return []
    pmids: list[str] = []
    for db in linkset.get("linksetdbs", []):
        if isinstance(db, dict) and db.get("linkname") == linkname:
            pmids.extend(str(i) for i in db.get("links", []) if isinstance(i, str))
    return pmids


def _extract_elink_pmids(data: Any, linkname: str, *, source_pmid: str) -> list[str]:
    """Pull the target PMIDs for *linkname* out of an ELink JSON document."""
    if not isinstance(data, dict):
        return []
    pmids: list[str] = []
    for linkset in data.get("linksets", []):
        pmids.extend(_linkset_pmids(linkset, linkname))
    # A source article can appear in its own similar-articles set; drop it.
    return [p for p in pmids if p != source_pmid]
