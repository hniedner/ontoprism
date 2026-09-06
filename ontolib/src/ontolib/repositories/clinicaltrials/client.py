"""Async client for the ClinicalTrials.gov API v2.

Transport + query-shaping only: builds the ``/studies`` query, applies the public
API's status/phase filters, and delegates JSON→model mapping to :mod:`parser`.
CT.gov v2 is public (no API key). This client performs direct searches without
term extraction or local reranking.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Self, TypeIs, get_args

import httpx

from ontolib.common.error_handling import retry_with_backoff
from ontolib.common.grid import PRODUCT_PAGE_SIZES, ProductPageSize
from ontolib.core.logging_config import get_logger
from ontolib.repositories.clinicaltrials.models import (
    CTPhase,
    CTStatus,
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
# Retryable transport failures (a returned HTTP error status is deterministic, not
# retried here — 5xx is surfaced as StorageError).
_RETRYABLE = (httpx.TransportError, httpx.TimeoutException)

# The v2 filter enums we accept — an out-of-range value is rejected before the call
# so a typo becomes a clear ValueError rather than a silently-empty result set.
VALID_STATUSES = frozenset(get_args(CTStatus))
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
VALID_PHASES = frozenset(get_args(CTPhase))


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


def _study_nct_id(study: dict[str, Any]) -> str:
    return study["protocolSection"]["identificationModule"]["nctId"]


def _invalid_search_response() -> UpstreamUnavailableError:
    return UpstreamUnavailableError(
        "clinicaltrials", "ClinicalTrials.gov returned an invalid response."
    )


def _valid_study_rows(value: object) -> TypeIs[list[dict[str, Any]]]:
    if not isinstance(value, list) or not all(
        isinstance(row, dict) and _has_valid_study_identity(row) for row in value
    ):
        return False
    identities = [_study_nct_id(row) for row in value]
    return len(identities) == len(set(identities))


def _valid_search_total(total: object, studies: list[dict[str, Any]]) -> TypeIs[int]:
    return (
        isinstance(total, int)
        and not isinstance(total, bool)
        and total >= 0
        and len(studies) <= total
        and (total == 0 or bool(studies))
    )


def _validate_search_response(
    data: object,
) -> tuple[list[dict[str, Any]], int, str | None]:
    if not isinstance(data, Mapping):
        raise _invalid_search_response()
    raw = data.get("studies")
    total = data.get("totalCount")
    if not _valid_study_rows(raw):
        raise _invalid_search_response()
    if not _valid_search_total(total, raw):
        raise _invalid_search_response()
    next_token = data.get("nextPageToken")
    if next_token is not None and (
        not isinstance(next_token, str) or not next_token.strip()
    ):
        raise _invalid_search_response()
    return raw, total, next_token


def _filter_params(
    status: tuple[CTStatus, ...], phase: tuple[CTPhase, ...]
) -> dict[str, str]:
    """Validate and shape the optional status/phase filter params.

    Raises:
        ValueError: if *status*/*phase* is not a valid CT.gov v2 enum value.
    """
    params: dict[str, str] = {}
    if status:
        invalid_statuses = set(status) - VALID_STATUSES
        if invalid_statuses:
            raise ValueError(
                f"Invalid trial status filter: {sorted(invalid_statuses)!r}"
            )
        params["filter.overallStatus"] = "|".join(status)
    if phase:
        invalid_phases = set(phase) - VALID_PHASES
        if invalid_phases:
            raise ValueError(f"Invalid trial phase filter: {sorted(invalid_phases)!r}")
        params["aggFilters"] = "phase:" + " ".join(_PHASE_AGG[value] for value in phase)
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
        status: tuple[CTStatus, ...],
        phase: tuple[CTPhase, ...],
        page_size: ProductPageSize,
        page_token: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pageSize": page_size,
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
        if page_token is not None:
            if not page_token.strip():
                raise ValueError("ClinicalTrials.gov page token must not be blank")
            params["pageToken"] = page_token
        return params

    async def _search_data_with_total(
        self, params: dict[str, Any], page_token: str | None
    ) -> Any:
        data = await self._request_json("/studies", params)
        if page_token is None or not isinstance(data, Mapping) or "totalCount" in data:
            return data
        count_params = {**params, "pageSize": 1}
        count_params.pop("pageToken")
        count_data = await self._request_json("/studies", count_params)
        if not isinstance(count_data, Mapping):
            raise _invalid_search_response()
        return {**data, "totalCount": count_data.get("totalCount")}

    async def search_studies(
        self,
        *,
        condition: str | None = None,
        intervention: str | None = None,
        term: str | None = None,
        status: tuple[CTStatus, ...] = (),
        phase: tuple[CTPhase, ...] = (),
        page_size: ProductPageSize = 25,
        page_token: str | None = None,
    ) -> CTStudySearchPage:
        """Search trials by condition / intervention / free term (+ optional filters).

        Raises:
            ValueError: if filters, page size, or a supplied page token are invalid.
            StorageError: on transport, HTTP, or invalid upstream response data.
        """
        if page_size not in PRODUCT_PAGE_SIZES:
            raise ValueError(f"Invalid ClinicalTrials.gov page size: {page_size!r}")
        canonical_status = tuple(dict.fromkeys(status))
        canonical_phase = tuple(dict.fromkeys(phase))
        params = self._build_search_params(
            condition=condition,
            intervention=intervention,
            term=term,
            status=canonical_status,
            phase=canonical_phase,
            page_size=page_size,
            page_token=page_token,
        )
        data = await self._search_data_with_total(params, page_token)
        studies, total, next_page_token = _validate_search_response(data)
        try:
            return CTStudySearchPage(
                condition=condition,
                intervention=intervention,
                term=term,
                status=list(canonical_status),
                phase=list(canonical_phase),
                total=total,
                page_size=page_size,
                page_token=page_token,
                next_page_token=next_page_token,
                studies=[
                    parse_study_summary(s, index=i, total=max(len(studies), 1))
                    for i, s in enumerate(studies)
                ],
            )
        except ValueError as exc:
            raise _invalid_search_response() from exc

    async def get_study(self, nct_id: str) -> CTStudyDetail | None:
        """Fetch one trial by NCT id, or None if it does not exist (404).

        Raises:
            ValueError: if *nct_id* is not the ``NCT`` + 8-digit shape.
            StorageError: on transport, HTTP, or invalid upstream response data.
        """
        if not is_valid_nct_id(nct_id):
            raise ValueError(f"Invalid NCT id: {nct_id!r}")
        data = await self._request_json(f"/studies/{nct_id}", None, allow_404=True)
        if data is None:
            return None
        if not isinstance(data, dict) or not _has_valid_study_identity(data):
            raise _invalid_search_response()
        try:
            detail = parse_study_detail(data)
        except ValueError as exc:
            raise _invalid_search_response() from exc
        if detail.nct_id != nct_id:
            raise _invalid_search_response()
        return detail

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
