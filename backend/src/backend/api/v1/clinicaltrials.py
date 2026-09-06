"""ClinicalTrials.gov repository endpoints: trial search + trial detail.

A thin pass-through to the async :class:`ClinicalTrialsClient` for condition,
intervention, and free-term search with optional status and phase filters.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import Field, field_validator

from backend.api.upstream import upstream_http_exception
from backend.dependencies import ClinicalTrials
from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.common.grid import ProductPageSize
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.repositories.clinicaltrials.models import (
    CTFilterPhase,
    CTStatus,
    CTStudyDetail,
    CTStudySearchPage,
)
from ontolib.repositories.upstream import UpstreamFailureError

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/clinicaltrials", tags=["clinicaltrials"])


class CTSearchRequest(StrictBoundaryModel):
    """Search parameters for ClinicalTrials.gov (at least one query field required)."""

    condition: str | None = Field(default=None, max_length=500)
    intervention: str | None = Field(default=None, max_length=500)
    term: str | None = Field(default=None, max_length=500)
    status: list[CTStatus] = Field(default_factory=list)
    phase: list[CTFilterPhase] = Field(default_factory=list)
    limit: ProductPageSize = 25
    page_token: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("status", "phase")
    @classmethod
    def deduplicate_filters(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


@router.post("/search", response_model=CTStudySearchPage)
async def search(client: ClinicalTrials, body: CTSearchRequest) -> CTStudySearchPage:
    """Search clinical trials by condition, intervention, and/or free term."""
    if not (body.condition or body.intervention or body.term):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Provide at least one of: condition, intervention, term.",
        )
    try:
        return await client.search_studies(
            condition=body.condition,
            intervention=body.intervention,
            term=body.term,
            status=tuple(body.status),
            phase=tuple(body.phase),
            page_size=body.limit,
            page_token=body.page_token,
        )
    except ValueError as exc:
        # Request-shaped values rejected by the client, such as a blank page token,
        # remain client errors rather than upstream failures.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except UpstreamFailureError as exc:
        logger.warning("ClinicalTrials.gov search failed: %s", exc.state)
        raise upstream_http_exception(exc) from exc
    except StorageError as exc:
        logger.warning("ClinicalTrials.gov search failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "ClinicalTrials.gov request failed."
        ) from exc


@router.get("/{nct_id}", response_model=CTStudyDetail)
async def trial_detail(client: ClinicalTrials, nct_id: str) -> CTStudyDetail:
    """Return one trial by NCT id (404 if unknown, 400 if the id is malformed)."""
    try:
        detail = await client.get_study(nct_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except UpstreamFailureError as exc:
        logger.warning("ClinicalTrials.gov detail fetch failed: %s", exc.state)
        raise upstream_http_exception(exc) from exc
    except StorageError as exc:
        logger.warning("ClinicalTrials.gov detail fetch failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "ClinicalTrials.gov request failed."
        ) from exc
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Trial not found: {nct_id}")
    return detail
