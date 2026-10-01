"""Certified Uberon/CL list, search, detail, and neighborhood endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from backend.api.v1.alignment import mapping_relative_to
from backend.api.v1.grid import GridService, PageSize, declared_grid, present_text
from backend.dependencies import (
    RepositoryMetadataReads,
    UberonSearch,
    UberonStore,
    XrefReads,
)
from backend.repository_metadata import RepositoryUnhealthy, UberonRepositoryReady
from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.common.grid import ColumnText
from ontolib.repositories.xref.models import (
    StaleXrefGenerationError,
    UberonReadIdentity,
    UnavailableXrefGenerationError,
    XrefReadPolicy,
)
from ontolib.repositories.xref.vocab import MappingLifecycle, MappingPredicate
from ontolib.terminologies.uberon.models import (
    UberonBrowsePage,
    UberonBrowseSort,
    UberonConceptDetail,
    UberonNeighborhood,
    UberonSearchPage,
    UberonSearchSort,
    UberonSource,
)

router = APIRouter(prefix="/api/v1/uberon", tags=["uberon"])


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


def _column_text(
    code_text: ColumnText | None = None,
    label_text: ColumnText | None = None,
    source_text: ColumnText | None = None,
) -> dict[str, str]:
    return present_text(code=code_text, label=label_text, source=source_text)


UberonColumnText = Annotated[dict[str, str], Depends(_column_text)]


def _grid(metadata: RepositoryMetadataReads) -> GridService[UberonRepositoryReady]:
    return declared_grid("uberon", metadata.uberon)


UberonGrid = Annotated[GridService[UberonRepositoryReady], Depends(_grid)]


@router.get("/search", response_model=UberonSearchPage)
async def search(
    index: UberonSearch,
    grid: UberonGrid,
    q: Annotated[str, Query(min_length=1)],
    column_text: UberonColumnText,
    source: Annotated[list[UberonSource] | None, Query()] = None,
    limit: PageSize = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: UberonSearchSort = "relevance",
) -> UberonSearchPage:
    sources = source or []
    await grid.validate("search", sort, column_text, {"source": sources})
    return await grid.read(
        lambda _: index.search(
            q,
            sources=sources,
            limit=limit,
            offset=offset,
            sort=sort,
            column_text=column_text,
        ),
        available=lambda repository: index.is_populated(
            repository.source_identity, repository.observation.serving.sha256
        ),
    )


@router.get("/list", response_model=UberonBrowsePage)
async def list_concepts(
    store: UberonStore,
    grid: UberonGrid,
    column_text: UberonColumnText,
    source: Annotated[list[UberonSource] | None, Query()] = None,
    limit: PageSize = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: UberonBrowseSort = "source",
) -> UberonBrowsePage:
    sources = source or []
    await grid.validate("list", sort, column_text, {"source": sources})
    return await grid.read(
        lambda _: store.list_concepts(
            sources=sources,
            limit=limit,
            offset=offset,
            sort=sort,
            column_text=column_text,
        )
    )


@router.get("/concepts/{code}", response_model=UberonConceptDetail)
async def concept_detail(
    store: UberonStore,
    grid: UberonGrid,
    code: Annotated[str, Path(pattern=r"^(UBERON|CL):[0-9]+$")],
) -> UberonConceptDetail:
    return await grid.read_detail(lambda _: store.get_concept_detail(code), detail=code)


@router.get("/concepts/{code}/neighborhood", response_model=UberonNeighborhood)
async def neighborhood(
    store: UberonStore,
    grid: UberonGrid,
    code: Annotated[str, Path(pattern=r"^(UBERON|CL):[0-9]+$")],
    depth: Annotated[int, Query(ge=1, le=1)] = 1,
) -> UberonNeighborhood:
    try:
        return await grid.read(lambda _: store.get_neighborhood(code, depth=depth))
    except LookupError as exc:
        raise HTTPException(404, f"Concept not found: {code}") from exc


@router.get("/concepts/{code}/alignments", response_model=UberonAlignments)
async def alignments(
    xref_store: XrefReads,
    metadata: RepositoryMetadataReads,
    grid: UberonGrid,
    code: Annotated[str, Path(pattern=r"^(UBERON|CL):[0-9]+$")],
) -> UberonAlignments:
    repository = await grid.ready()
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
