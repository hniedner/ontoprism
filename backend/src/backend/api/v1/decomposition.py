"""Decomposition provenance endpoints: run summary + minted concepts."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import ProvenanceReads
from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS, AxisContract
from ontolib.decomposition.provenance_models import (
    MintedConcept,
    RunSummary,
    WorkItemOutcome,
)

router = APIRouter(prefix="/api/v1/decomposition", tags=["decomposition"])


@router.get("/axes", response_model=list[AxisContract])
async def list_axis_contracts() -> list[AxisContract]:
    """Return the stable univocal relation catalogue without a store dependency."""
    return [AXIS_CONTRACTS[axis] for axis in sorted(AXIS_CONTRACTS)]


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    store: ProvenanceReads,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunSummary]:
    """List decomposition runs with coverage/decomposed/residual/minted counts."""
    try:
        return await store.list_runs(limit=limit, offset=offset)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/runs/{run_id}", response_model=RunSummary)
async def get_run(store: ProvenanceReads, run_id: str) -> RunSummary:
    """Return a single decomposition run summary by id; 404 if not found."""
    try:
        run = await store.get_run(run_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No decomposition run {run_id}")
    return run


@router.get("/runs/{run_id}/outcomes", response_model=list[WorkItemOutcome])
async def list_run_outcomes(
    store: ProvenanceReads,
    run_id: str,
) -> list[WorkItemOutcome]:
    """Return ordered, typed per-concept outcomes and observed source types."""
    try:
        return await store.work_item_outcomes(run_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/minted-concepts", response_model=list[MintedConcept])
async def list_minted_concepts(
    store: ProvenanceReads,
    run_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MintedConcept]:
    """List minted-concept proposals, optionally filtered by run_id and status."""
    try:
        return await store.list_minted_concepts(
            run_id=run_id, status=status_filter, limit=limit, offset=offset
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
