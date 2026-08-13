"""Entitlement-gated ICD-O edition/axis repository endpoints."""

import base64
import binascii
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status

from backend.dependencies import IcdoReads, RepositoryMetadataReads, UberonStore
from backend.repository_metadata import RepositoryUnhealthy
from backend.security import RequireIcdoEntitlement
from ontolib.repositories.icdo.congruence import (
    CongruenceReport,
    build_congruence_report,
)
from ontolib.repositories.icdo.models import (
    MorphologyCode32,
    MorphologyCode40,
    TopographyCode40,
)

router = APIRouter(
    prefix="/api/v1/icdo", tags=["icdo"], dependencies=[RequireIcdoEntitlement]
)
Edition = Literal["3.2", "4.0"]
Axis = Literal["morphology", "topography"]


def _dataset(edition: Edition, axis: Axis) -> None:
    if edition == "3.2" and axis == "topography":
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "ICD-O-3.2 topography is not served."
        )


def _decode_code(segment: str, edition: Edition, axis: Axis) -> str:
    try:
        code = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)).decode(
            "ascii"
        )
        if axis == "topography":
            TopographyCode40(value=code)
        elif edition == "3.2":
            MorphologyCode32(value=code)
        else:
            MorphologyCode40(value=code)
        return code
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ICD-O code not found.") from exc


@router.get("/{edition}/{axis}/metadata")
async def metadata(
    repository_metadata: RepositoryMetadataReads, edition: Edition, axis: Axis
) -> object:
    _dataset(edition, axis)
    result = await repository_metadata.icdo(edition, axis)
    if isinstance(result, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "state": "unhealthy",
                "reason": result.reason,
                "message": result.message,
            },
        )
    return result.model_dump(mode="json")


@router.get("/4.0/topography/congruence", response_model=CongruenceReport)
async def congruence_report(
    repository: IcdoReads,
    uberon: UberonStore,
    repository_metadata: RepositoryMetadataReads,
) -> CongruenceReport:
    icdo_metadata = await repository_metadata.icdo("4.0", "topography")
    uberon_metadata = await repository_metadata.uberon()
    if isinstance(icdo_metadata, RepositoryUnhealthy) or isinstance(
        uberon_metadata, RepositoryUnhealthy
    ):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Congruence sources are unavailable.",
        )
    topography = await repository.dataset("4.0", "topography")
    if topography is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Congruence sources are unavailable.",
        )
    records = await _uberon_congruence_records(uberon)
    return build_congruence_report(
        topography,
        icdo_serving_identity=icdo_metadata.serving_identity,
        uberon_serving_identity=uberon_metadata.observation.serving.sha256,
        uberon_records=records,
    )


async def _uberon_congruence_records(
    uberon: UberonStore,
) -> tuple[dict[str, str], ...]:
    records: list[dict[str, str]] = []
    offset = 0
    batch = await uberon.congruence_records(limit=5000, offset=offset)
    while batch:
        records.extend(
            {
                "code": row.get("code") or "",
                "label": row.get("label") or "",
                "synonyms": row.get("synonyms") or "",
                "parents": row.get("parents") or "",
            }
            for row in batch
        )
        offset += 5000
        batch = await uberon.congruence_records(limit=5000, offset=offset)
    return tuple(records)


@router.get("/{edition}/{axis}/list")
async def list_records(
    repository: IcdoReads,
    edition: Edition,
    axis: Axis,
    behaviour: str | None = None,
    level: Literal["category", "leaf"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> object:
    _dataset(edition, axis)
    return await repository.search(
        edition,
        axis,
        query="",
        behaviour=behaviour,
        level=level,
        limit=limit,
        offset=offset,
    )


@router.get("/{edition}/{axis}/search")
async def search(
    repository: IcdoReads,
    edition: Edition,
    axis: Axis,
    q: Annotated[str, Query(min_length=1)],
    behaviour: str | None = None,
    level: Literal["category", "leaf"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> object:
    _dataset(edition, axis)
    return await repository.search(
        edition,
        axis,
        query=q,
        behaviour=behaviour,
        level=level,
        limit=limit,
        offset=offset,
    )


@router.get("/{edition}/{axis}/concepts/{code}")
async def detail(
    repository: IcdoReads,
    edition: Edition,
    axis: Axis,
    code: Annotated[str, Path(min_length=1)],
) -> object:
    _dataset(edition, axis)
    canonical = _decode_code(code, edition, axis)
    result = await repository.detail(edition, axis, canonical)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ICD-O code not found.")
    return result
