"""Repository refresh / reload.

``POST /api/v1/refresh`` re-probes each repository and returns current version/counts
— a live status refresh. ``POST /api/v1/refresh/ncit/reload`` bulk-loads an RDF file
into Oxigraph via the local Graph Store Protocol (no container/ECS restart). The full
NCI FTP/EVS download pipeline is intentionally out of scope (an operational task).
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.config import get_settings
from backend.dependencies import CadsrRepo, NcitClient, NcitStore
from backend.security import RequireApiKey
from ontolib.core.exceptions import StorageError

router = APIRouter(prefix="/api/v1/refresh", tags=["refresh"])

_RDF_CONTENT_TYPES = {
    ".ttl": "text/turtle",
    ".nt": "application/n-triples",
    ".nq": "application/n-quads",
    ".rdf": "application/rdf+xml",
    ".owl": "application/rdf+xml",
}


class RepoStatus(BaseModel):
    """Live status of one repository after a refresh probe."""

    name: str
    healthy: bool
    version: str | None = None
    item_count: int | None = None
    error: str | None = None


class RefreshReport(BaseModel):
    """Result of a repository refresh: per-repository status."""

    refreshed_at: str
    repositories: list[RepoStatus]


class ReloadRequest(BaseModel):
    """A server-side RDF file to bulk-load into the NCIt store."""

    source_path: str
    replace: bool = True


class ReloadResponse(BaseModel):
    """Triple counts before/after a reload."""

    triples_before: int
    triples_after: int


def _resolve_allowed(source_path: str) -> Path:
    """Resolve *source_path* and require it to live inside the reload allowlist dir."""
    allowed_root = Path(get_settings().reload_allowed_dir).resolve()
    resolved = Path(source_path).resolve()
    if not resolved.is_relative_to(allowed_root):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"source_path must resolve within the reload allowlist directory "
            f"({allowed_root}).",
        )
    return resolved


@router.post("", response_model=RefreshReport, dependencies=[RequireApiKey])
async def refresh(
    store: NcitStore, client: NcitClient, cadsr: CadsrRepo
) -> RefreshReport:
    """Re-probe NCIt and caDSR and return their current version/counts."""
    repos = [await _ncit_status(client), _cadsr_status(cadsr)]
    _ = store  # store is wired for symmetry / future cache rebuilds
    return RefreshReport(refreshed_at=datetime.now(UTC).isoformat(), repositories=repos)


async def _ncit_status(client: NcitClient) -> RepoStatus:
    try:
        count = await client.count()
        version = await client.version()
    except StorageError as exc:
        return RepoStatus(name="ncit", healthy=False, error=str(exc))
    return RepoStatus(name="ncit", healthy=True, version=version, item_count=count)


def _cadsr_status(cadsr: CadsrRepo) -> RepoStatus:
    try:
        count = cadsr.count()
    except sqlite3.OperationalError as exc:
        return RepoStatus(name="cadsr", healthy=False, error=str(exc))
    return RepoStatus(name="cadsr", healthy=True, item_count=count)


@router.post(
    "/ncit/reload", response_model=ReloadResponse, dependencies=[RequireApiKey]
)
async def reload_ncit(client: NcitClient, body: ReloadRequest) -> ReloadResponse:
    """Bulk-load a server-side RDF file into the NCIt Oxigraph store.

    The source file must resolve inside the configured reload allowlist directory —
    an arbitrary host path (``../../etc/passwd``) is rejected, so this endpoint cannot
    be used to ingest or exfiltrate files outside the managed data area.
    """
    path = _resolve_allowed(body.source_path)
    content_type = _RDF_CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported RDF extension {path.suffix}; "
            f"expected one of {sorted(_RDF_CONTENT_TYPES)}",
        )
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File not found: {path}")
    try:
        before = await client.count()
        await client.load(
            path.read_bytes(), content_type=content_type, replace=body.replace
        )
        after = await client.count()
    except StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ReloadResponse(triples_before=before, triples_after=after)
