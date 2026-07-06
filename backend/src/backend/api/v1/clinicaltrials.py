"""ClinicalTrials.gov repository endpoints: trial search + trial detail.

A thin pass-through to the async :class:`ClinicalTrialsClient`. Direct-search only
(condition / intervention / free term + optional status & phase filters); the
natural-language / LLM term-extraction layer from fairdata is not ported.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.dependencies import ClinicalTrials
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.repositories.clinicaltrials.models import (
    CTStudyDetail,
    CTStudySearchPage,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/clinicaltrials", tags=["clinicaltrials"])


class CTSearchRequest(BaseModel):
    """Search parameters for ClinicalTrials.gov (at least one query field required)."""

    condition: str | None = Field(default=None, max_length=500)
    intervention: str | None = Field(default=None, max_length=500)
    term: str | None = Field(default=None, max_length=500)
    status: str | None = None
    phase: str | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20


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
            status=body.status,
            phase=body.phase,
            page_size=body.limit,
        )
    except ValueError as exc:
        # Invalid status/phase enum — a client error, not an upstream failure.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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
    except StorageError as exc:
        logger.warning("ClinicalTrials.gov detail fetch failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "ClinicalTrials.gov request failed."
        ) from exc
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Trial not found: {nct_id}")
    return detail
