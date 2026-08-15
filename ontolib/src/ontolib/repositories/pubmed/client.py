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
from ontolib.core.logging_config import get_logger
from ontolib.repositories.pubmed.models import (
    PubMedArticleDetail,
    PubMedArticleSummary,
    PubMedSearchResult,
    RelatedArticlesResult,
)
from ontolib.repositories.pubmed.parser import parse_efetch_xml, parse_esummary
from ontolib.repositories.upstream import (
    UpstreamRateLimitedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)

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
            if wait:
                await asyncio.sleep(wait)
            self._next_allowed = time.monotonic() + self._min_interval

    @retry_with_backoff(retryable_exceptions=_RETRYABLE)
    async def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        if self._api_key:
            params = {**params, "api_key": self._api_key}
        return await self._get_client().get(
            f"{self._base_url}{path}", params=params, follow_redirects=True
        )

    async def _request(self, path: str, params: dict[str, Any]) -> httpx.Response:
        self._get_client()
        await self._throttle()
        try:
            response = await self._get(path, params)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError("pubmed", "PubMed request timed out.") from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError(
                "pubmed", "PubMed is temporarily unavailable."
            ) from exc
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise UpstreamRateLimitedError(
                "pubmed", "PubMed rate limit reached; try again later."
            )
        if response.status_code != HTTPStatus.OK:
            raise UpstreamUnavailableError(
                "pubmed", "PubMed is temporarily unavailable."
            )
        return response

    async def _json(self, path: str, params: dict[str, Any]) -> Any:
        response = await self._request(path, params)
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamUnavailableError(
                "pubmed", "PubMed returned an invalid response."
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
            raise UpstreamUnavailableError(
                "pubmed", "PubMed returned an invalid response."
            ) from exc
        if not articles:
            return None
        if len(articles) != 1 or articles[0].pmid != pmid:
            raise UpstreamUnavailableError(
                "pubmed", "PubMed returned a mismatched article identity."
            )
        return articles[0]

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
    if not isinstance(esearch, dict) or not isinstance(
        esearch.get("esearchresult"), dict
    ):
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    result = esearch["esearchresult"]
    pmids = _string_list(result.get("idlist"))
    count = result.get("count")
    if not isinstance(count, str) or not count.isdigit():
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    total = int(count)
    return pmids, total


def _valid_summary_docs(docs: dict[str, Any], uids: list[str]) -> bool:
    return all(
        isinstance(docs.get(uid), dict) and str(docs[uid].get("uid", "")) == uid
        for uid in uids
    )


def _parse_esummary_docs(summary: Any, pmids: list[str]) -> list[PubMedArticleSummary]:
    """Map an ESummary JSON document (keyed by uid) to article summaries."""
    if not isinstance(summary, dict) or not isinstance(summary.get("result"), dict):
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    docs = summary["result"]
    uids = _string_list(docs.get("uids"))
    if uids != pmids or not _valid_summary_docs(docs, uids):
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    return [parse_esummary(uid, docs[uid]) for uid in uids]


def _linkset_pmids(linkset: Any, linkname: str) -> list[str]:
    """Return the target PMIDs for *linkname* within one ELink linkset."""
    if not isinstance(linkset, dict):
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    databases = _mapping_list(linkset.get("linksetdbs"))
    pmids: list[str] = []
    for db in databases:
        links = db.get("links")
        parsed_links = [] if links is None else _string_list(links)
        if db.get("linkname") == linkname:
            pmids.extend(parsed_links)
    return pmids


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    return value


def _extract_elink_pmids(data: Any, linkname: str, *, source_pmid: str) -> list[str]:
    """Pull the target PMIDs for *linkname* out of an ELink JSON document."""
    if not isinstance(data, dict):
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    linksets = data.get("linksets")
    if not isinstance(linksets, list):
        raise UpstreamUnavailableError("pubmed", "PubMed returned an invalid response.")
    pmids: list[str] = []
    for linkset in linksets:
        pmids.extend(_linkset_pmids(linkset, linkname))
    # A source article can appear in its own similar-articles set; drop it.
    return [p for p in pmids if p != source_pmid]
