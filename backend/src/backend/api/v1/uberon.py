"""Certified Uberon/CL list, search, detail, and neighborhood endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy.exc import SQLAlchemyError

from backend.api.v1.alignment import mapping_relative_to
from backend.dependencies import (
    RepositoryMetadataReads,
    UberonSearch,
    UberonStore,
    XrefReads,
)
from backend.repository_metadata import RepositoryUnhealthy, UberonRepositoryReady
from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.repositories.xref.models import (
    StaleXrefGenerationError,
    UberonReadIdentity,
    UnavailableXrefGenerationError,
    XrefReadPolicy,
)
from ontolib.repositories.xref.vocab import MappingLifecycle, MappingPredicate
from ontolib.terminologies.uberon.graph_store import InvalidUberonCurieError
from ontolib.terminologies.uberon.models import (
    UberonConceptDetail,
    UberonNeighborhood,
    UberonSearchPage,
    UberonSource,
)

router = APIRouter(prefix="/api/v1/uberon", tags=["uberon"])
logger = get_logger(__name__)


class NcitAlignment(StrictBoundaryModel):
    code: str
    system: Literal["ncit"] = "ncit"
    version: str
    predicate: MappingPredicate
    lifecycle: MappingLifecycle


class UberonAlignments(StrictBoundaryModel):
    code: str
    repository_source_identity: str
    repository_serving_identity: str
    alignments: list[NcitAlignment]


async def _ready(metadata: RepositoryMetadataReads) -> UberonRepositoryReady:
    repository = await metadata.uberon()
    if isinstance(repository, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            repository.model_dump(mode="json"),
        )
    return repository


def _repository_failure(exc: StorageError) -> HTTPException:
    logger.exception("Uberon/CL repository read failed")
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        "Uberon/CL repository returned an invalid or unavailable response.",
    )


@router.get("/search", response_model=UberonSearchPage)
async def search(
    store: UberonStore,
    index: UberonSearch,
    metadata: RepositoryMetadataReads,
    q: Annotated[str, Query(min_length=1)],
    source: UberonSource | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UberonSearchPage:
    repository = await _ready(metadata)
    try:
        if await index.is_populated(
            repository.source_identity, repository.observation.serving.sha256
        ):
            return await index.search(q, source=source, limit=limit, offset=offset)
    except SQLAlchemyError as exc:
        logger.exception("Uberon/CL FTS read failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Uberon/CL search cache is unavailable.",
        ) from exc
    try:
        return await store.search(q, source=source, limit=limit, offset=offset)
    except StorageError as exc:
        raise _repository_failure(exc) from exc


@router.get("/list", response_model=UberonSearchPage)
async def list_concepts(
    store: UberonStore,
    metadata: RepositoryMetadataReads,
    source: UberonSource | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UberonSearchPage:
    await _ready(metadata)
    try:
        return await store.list_concepts(source=source, limit=limit, offset=offset)
    except StorageError as exc:
        raise _repository_failure(exc) from exc


@router.get("/concepts/{code}", response_model=UberonConceptDetail)
async def concept_detail(
    store: UberonStore,
    metadata: RepositoryMetadataReads,
    code: Annotated[str, Path(pattern=r"^(UBERON|CL):[0-9]+$")],
) -> UberonConceptDetail:
    await _ready(metadata)
    try:
        detail = await store.get_concept_detail(code)
    except (InvalidUberonCurieError, LookupError) as exc:
        message = (
            "Invalid code"
            if isinstance(exc, InvalidUberonCurieError)
            else "Concept not found"
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{message}: {code}") from exc
    except StorageError as exc:
        raise _repository_failure(exc) from exc
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Concept not found: {code}")
    return detail


@router.get("/concepts/{code}/neighborhood", response_model=UberonNeighborhood)
async def neighborhood(
    store: UberonStore,
    metadata: RepositoryMetadataReads,
    code: Annotated[str, Path(pattern=r"^(UBERON|CL):[0-9]+$")],
    depth: Annotated[int, Query(ge=1, le=1)] = 1,
) -> UberonNeighborhood:
    await _ready(metadata)
    try:
        return await store.get_neighborhood(code, depth=depth)
    except (InvalidUberonCurieError, LookupError) as exc:
        message = (
            "Invalid code"
            if isinstance(exc, InvalidUberonCurieError)
            else "Concept not found"
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{message}: {code}") from exc
    except StorageError as exc:
        raise _repository_failure(exc) from exc


@router.get("/concepts/{code}/alignments", response_model=UberonAlignments)
async def alignments(
    xref_store: XrefReads,
    metadata: RepositoryMetadataReads,
    code: Annotated[str, Path(pattern=r"^(UBERON|CL):[0-9]+$")],
) -> UberonAlignments:
    repository = await _ready(metadata)
    ncit = await metadata.ncit()
    if isinstance(ncit, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, ncit.model_dump(mode="json")
        )
    try:
        rows = await xref_store.mappings_for_identifiers(
            {code},
            expected=XrefReadPolicy(
                uberon=UberonReadIdentity(
                    ncit_source_identity=ncit.source_identity,
                    uberon_source_identity=repository.source_identity,
                    uberon_serving_identity=repository.observation.serving.sha256,
                )
            ),
        )
    except (StaleXrefGenerationError, UnavailableXrefGenerationError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return UberonAlignments(
        code=code,
        repository_source_identity=repository.source_identity,
        repository_serving_identity=repository.observation.serving.sha256,
        alignments=[
            NcitAlignment(
                code=target.identifier,
                system=target.system,
                version=target.version,
                predicate=predicate,
                lifecycle=row.lifecycle,
            )
            for row in rows.get(code, [])
            for target, predicate in [mapping_relative_to(row, code)]
            if target.system == "ncit"
        ],
    )
