"""Decomposition provenance endpoints: run summary + minted concepts."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import ProvenanceReads
from ontolib.decomposition.provenance_models import MintedConcept, RunSummary

router = APIRouter(prefix="/api/v1/decomposition", tags=["decomposition"])


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    store: ProvenanceReads,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunSummary]:
    try:
        return await store.list_runs(limit=limit, offset=offset)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/runs/{run_id}", response_model=RunSummary)
async def get_run(store: ProvenanceReads, run_id: str) -> RunSummary:
    try:
        run = await store.get_run(run_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No decomposition run {run_id}")
    return run


@router.get("/minted-concepts", response_model=list[MintedConcept])
async def list_minted_concepts(
    store: ProvenanceReads,
    run_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MintedConcept]:
    try:
        return await store.list_minted_concepts(
            run_id=run_id, status=status_filter, limit=limit, offset=offset
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
