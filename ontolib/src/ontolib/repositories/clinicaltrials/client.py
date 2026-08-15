"""Async client for the ClinicalTrials.gov API v2.

Transport + query-shaping only: builds the ``/studies`` query, applies the public
API's status/phase filters, and delegates JSON→model mapping to :mod:`parser`.
CT.gov v2 is public (no API key). Direct-search only — natural-language term
extraction and reranking (fairdata's LLM layer) are intentionally not ported.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Self, TypeIs

import httpx

from ontolib.common.error_handling import retry_with_backoff
from ontolib.core.logging_config import get_logger
from ontolib.repositories.clinicaltrials.models import (
    CTStudyDetail,
    CTStudySearchPage,
)
from ontolib.repositories.clinicaltrials.parser import (
    parse_study_detail,
    parse_study_summary,
)
from ontolib.repositories.upstream import (
    UpstreamRateLimitedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)

logger = get_logger(__name__)

DEFAULT_CT_API_URL = "https://clinicaltrials.gov/api/v2"
_NCT_ID_LEN = 11  # "NCT" + 8 digits
_PAGE_SIZE_MAX = 100
# Retryable transport failures (a returned HTTP error status is deterministic, not
# retried here — 5xx is surfaced as StorageError).
_RETRYABLE = (httpx.TransportError, httpx.TimeoutException)

# The v2 filter enums we accept — an out-of-range value is rejected before the call
# so a typo becomes a clear ValueError rather than a silently-empty result set.
VALID_STATUSES = frozenset(
    {
        "ACTIVE_NOT_RECRUITING",
        "COMPLETED",
        "ENROLLING_BY_INVITATION",
        "NOT_YET_RECRUITING",
        "RECRUITING",
        "SUSPENDED",
        "TERMINATED",
        "WITHDRAWN",
        "AVAILABLE",
        "NO_LONGER_AVAILABLE",
        "TEMPORARILY_NOT_AVAILABLE",
        "APPROVED_FOR_MARKETING",
        "WITHHELD",
        "UNKNOWN",
    }
)
# CT.gov v2 `aggFilters` phase buckets are NUMERIC ids, not the study-JSON enum names:
# sending `phase:PHASE2` returns HTTP 200 with zero results (a silent miss), whereas
# `phase:2` filters correctly. Map the caller-facing enum to the aggFilters id. "NA"
# (not-applicable) has no aggFilters phase bucket, so it is intentionally not accepted.
_PHASE_AGG = {
    "EARLY_PHASE1": "0",
    "PHASE1": "1",
    "PHASE2": "2",
    "PHASE3": "3",
    "PHASE4": "4",
}
VALID_PHASES = frozenset(_PHASE_AGG)


def is_valid_nct_id(nct_id: str) -> bool:
    """Return True if *nct_id* is the CT.gov ``NCT`` + 8-digit shape."""
    return (
        len(nct_id) == _NCT_ID_LEN and nct_id.startswith("NCT") and nct_id[3:].isdigit()
    )


def _has_valid_study_identity(study: dict[str, Any]) -> bool:
    protocol = study.get("protocolSection")
    if not isinstance(protocol, Mapping):
        return False
    identification = protocol.get("identificationModule")
    if not isinstance(identification, Mapping):
        return False
    nct_id = identification.get("nctId")
    return isinstance(nct_id, str) and is_valid_nct_id(nct_id)


def _invalid_search_response() -> UpstreamUnavailableError:
    return UpstreamUnavailableError(
        "clinicaltrials", "ClinicalTrials.gov returned an invalid response."
    )


def _valid_study_rows(value: object) -> TypeIs[list[dict[str, Any]]]:
    return isinstance(value, list) and all(
        isinstance(row, dict) and _has_valid_study_identity(row) for row in value
    )


def _validate_search_response(data: object) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(data, Mapping):
        raise _invalid_search_response()
    raw = data.get("studies")
    total = data.get("totalCount")
    if not _valid_study_rows(raw):
        raise _invalid_search_response()
    if not isinstance(total, int):
        raise _invalid_search_response()
    return raw, total


def _filter_params(status: str | None, phase: str | None) -> dict[str, str]:
    """Validate and shape the optional status/phase filter params.

    Raises:
        ValueError: if *status*/*phase* is not a valid CT.gov v2 enum value.
    """
    params: dict[str, str] = {}
    if status:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid trial status filter: {status!r}")
        params["filter.overallStatus"] = status
    if phase:
        agg = _PHASE_AGG.get(phase)
        if agg is None:
            raise ValueError(f"Invalid trial phase filter: {phase!r}")
        params["aggFilters"] = f"phase:{agg}"
    return params


class ClinicalTrialsClient:
    """Minimal async client over the ClinicalTrials.gov v2 REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_CT_API_URL,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
    ) -> None:
        """Create a client for *base_url* (default: the public CT.gov v2 API)."""
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, headers={"Accept": "application/json"}
            )
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client and its connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry_with_backoff(retryable_exceptions=_RETRYABLE)
    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        return await self._get_client().get(
            f"{self._base_url}{path}", params=params, follow_redirects=True
        )

    def _build_search_params(
        self,
        *,
        condition: str | None,
        intervention: str | None,
        term: str | None,
        status: str | None,
        phase: str | None,
        page_size: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pageSize": max(1, min(page_size, _PAGE_SIZE_MAX)),
            "countTotal": "true",
        }
        for key, value in (
            ("query.cond", condition),
            ("query.intr", intervention),
            ("query.term", term),
        ):
            if value:
                params[key] = value
        params.update(_filter_params(status, phase))
        return params

    async def search_studies(
        self,
        *,
        condition: str | None = None,
        intervention: str | None = None,
        term: str | None = None,
        status: str | None = None,
        phase: str | None = None,
        page_size: int = 20,
    ) -> CTStudySearchPage:
        """Search trials by condition / intervention / free term (+ optional filters).

        Raises:
            ValueError: if *status*/*phase* is not a valid CT.gov v2 enum value.
            StorageError: on a transport error or a non-2xx response.
        """
        params = self._build_search_params(
            condition=condition,
            intervention=intervention,
            term=term,
            status=status,
            phase=phase,
            page_size=page_size,
        )
        data = await self._request_json("/studies", params)
        studies, total = _validate_search_response(data)
        return CTStudySearchPage(
            condition=condition,
            intervention=intervention,
            term=term,
            total=total,
            studies=[
                parse_study_summary(s, index=i, total=max(len(studies), 1))
                for i, s in enumerate(studies)
            ],
        )

    async def get_study(self, nct_id: str) -> CTStudyDetail | None:
        """Fetch one trial by NCT id, or None if it does not exist (404).

        Raises:
            ValueError: if *nct_id* is not the ``NCT`` + 8-digit shape.
            StorageError: on a transport error or a non-2xx (non-404) response.
        """
        if not is_valid_nct_id(nct_id):
            raise ValueError(f"Invalid NCT id: {nct_id!r}")
        data = await self._request_json(f"/studies/{nct_id}", None, allow_404=True)
        if data is None:
            return None
        return parse_study_detail(data)

    async def _request_json(
        self, path: str, params: dict[str, Any] | None, *, allow_404: bool = False
    ) -> Any:
        try:
            response = await self._get(path, params)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(
                "clinicaltrials", "ClinicalTrials.gov request timed out."
            ) from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError(
                "clinicaltrials", "ClinicalTrials.gov is temporarily unavailable."
            ) from exc
        missing = _classify_response(response.status_code, allow_404=allow_404)
        if missing:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamUnavailableError(
                "clinicaltrials", "ClinicalTrials.gov returned an invalid response."
            ) from exc


def _classify_response(status_code: int, *, allow_404: bool) -> bool:
    if allow_404 and status_code == HTTPStatus.NOT_FOUND:
        return True
    if status_code == HTTPStatus.TOO_MANY_REQUESTS:
        raise UpstreamRateLimitedError(
            "clinicaltrials",
            "ClinicalTrials.gov rate limit reached; try again later.",
        )
    if status_code != HTTPStatus.OK:
        raise UpstreamUnavailableError(
            "clinicaltrials", "ClinicalTrials.gov is temporarily unavailable."
        )
    return False
