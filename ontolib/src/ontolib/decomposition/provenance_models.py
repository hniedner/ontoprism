"""Read models for Postgres decomposition provenance tables (run summary + mints)."""

from datetime import datetime

from pydantic import BaseModel


class RunSummary(BaseModel):
    """One decomposition run's manifest + (if finished) its metric counts."""

    id: str
    branch: str
    status: str
    ncit_version: str
    started_at: datetime
    finished_at: datetime | None = None
    total_in_scope: int | None = None
    decomposed: int | None = None
    residual: int | None = None
    minted_count: int | None = None
    pct_decomposed: float | None = None
    roundtrip_fidelity: float | None = None


class MintedConcept(BaseModel):
    """A minted-concept proposal awaiting curator approval."""

    id: str
    run_id: str
    axis: str
    label: str
    source_signal: str
    status: str
