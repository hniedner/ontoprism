"""Entitlement-gated ICD-O edition/axis repository endpoints."""

import base64
import binascii
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel

from backend.api.v1.alignment import mapping_relative_to
from backend.dependencies import (
    IcdoReads,
    RepositoryMetadataReads,
    UberonStore,
    XrefReads,
)
from backend.icdo_datasets import ServedIcdoDataset
from backend.repository_metadata import IcdoRepositoryReady, RepositoryUnhealthy
from backend.security import RequireIcdoEntitlement
from ontolib.repositories.icdo.congruence import (
    CongruenceReport,
    build_congruence_report,
)
from ontolib.repositories.icdo.models import (
    IcdoRecord,
    MorphologyCode32,
    MorphologyCode40,
    TopographyCode40,
)
from ontolib.repositories.xref.models import (
    IcdoReadIdentity,
    MappingResult,
    StaleXrefGenerationError,
    UnavailableXrefGenerationError,
    XrefReadPolicy,
)
from ontolib.repositories.xref.vocab import MappingLifecycle, MappingPredicate

router = APIRouter(
    prefix="/api/v1/icdo", tags=["icdo"], dependencies=[RequireIcdoEntitlement]
)
Edition = Literal["3.2", "4.0"]
Axis = Literal["morphology", "topography"]


class NcitAlignment(BaseModel):
    code: str
    system: Literal["ncit"] = "ncit"
    version: str
    predicate: MappingPredicate
    lifecycle: MappingLifecycle


class IcdoAccessReport(BaseModel):
    """Opaque consumer access state after all served datasets are certified."""

    status: Literal["ready-and-entitled"] = "ready-and-entitled"


class _RecordBase(BaseModel):
    code: str
    preferred: str | None = None
    synonyms: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    code_references: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()
    see_notes: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    other_text: tuple[str, ...] = ()


class Morphology32Record(_RecordBase):
    level: Literal["morphology"]
    parent_code: Literal[None] = None
    base_morphology: str
    specificity: Literal[None] = None
    behaviour: str


class Morphology40Record(_RecordBase):
    level: Literal["morphology"]
    parent_code: Literal[None] = None
    base_morphology: str
    specificity: str
    behaviour: str


class TopographyCategoryRecord(_RecordBase):
    level: Literal["category"]
    parent_code: Literal[None] = None
    base_morphology: Literal[None] = None
    specificity: Literal[None] = None
    behaviour: Literal[None] = None


class TopographyLeafRecord(_RecordBase):
    level: Literal["leaf"]
    parent_code: str
    base_morphology: Literal[None] = None
    specificity: Literal[None] = None
    behaviour: Literal[None] = None


type TopographyRecord = TopographyCategoryRecord | TopographyLeafRecord


class _IcdoDetail(BaseModel):
    activation_identity: str
    serving_identity: str
    ncit_alignments: list[NcitAlignment]


class Morphology32Detail(_IcdoDetail):
    edition: Literal["3.2"] = "3.2"
    axis: Literal["morphology"] = "morphology"
    record: Morphology32Record


class Morphology40Detail(_IcdoDetail):
    edition: Literal["4.0"] = "4.0"
    axis: Literal["morphology"] = "morphology"
    record: Morphology40Record


class Topography40Detail(_IcdoDetail):
    edition: Literal["4.0"] = "4.0"
    axis: Literal["topography"] = "topography"
    record: TopographyRecord


type IcdoDetail = Morphology32Detail | Morphology40Detail | Topography40Detail


class _IcdoPage(BaseModel):
    activation_identity: str
    serving_identity: str
    query: str
    total: int
    limit: int
    offset: int


class Morphology32Page(_IcdoPage):
    edition: Literal["3.2"]
    axis: Literal["morphology"]
    hits: list[Morphology32Record]


class Morphology40Page(_IcdoPage):
    edition: Literal["4.0"]
    axis: Literal["morphology"]
    hits: list[Morphology40Record]


class Topography40Page(_IcdoPage):
    edition: Literal["4.0"]
    axis: Literal["topography"]
    hits: list[TopographyRecord]


type IcdoPage = Morphology32Page | Morphology40Page | Topography40Page


def _ncit_alignments(
    code: str, edition: str, rows: list[MappingResult]
) -> list[NcitAlignment]:
    alignments: list[NcitAlignment] = []
    for row in rows:
        target, predicate = mapping_relative_to(row, code)
        if target.system != "ncit" or not any(
            endpoint.system == "icdo" and endpoint.version == edition
            for endpoint in (row.subject, row.object)
        ):
            continue
        alignments.append(
            NcitAlignment(
                code=target.identifier,
                version=target.version,
                predicate=predicate,
                lifecycle=row.lifecycle,
            )
        )
    return sorted(alignments, key=lambda alignment: alignment.code)


def _dataset(edition: Edition, axis: Axis) -> ServedIcdoDataset:
    dataset = ServedIcdoDataset.parse(edition, axis)
    if dataset is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "ICD-O-3.2 topography is not served."
        )
    return dataset


async def _ready(
    repository_metadata: RepositoryMetadataReads, edition: Edition, axis: Axis
) -> IcdoRepositoryReady:
    result = await repository_metadata.icdo(edition, axis)
    if isinstance(result, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, result.model_dump(mode="json")
        )
    return result


@router.get("/access", response_model=IcdoAccessReport)
async def access_status(
    repository_metadata: RepositoryMetadataReads,
) -> IcdoAccessReport:
    """Confirm entitlement and all served datasets without exposing metadata."""
    results = await repository_metadata.icdo_access()
    unhealthy = next(
        (result for result in results if isinstance(result, RepositoryUnhealthy)), None
    )
    if unhealthy is not None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, unhealthy.model_dump(mode="json")
        )
    return IcdoAccessReport()


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
    dataset = _dataset(edition, axis)
    result = await _ready(repository_metadata, dataset.edition, dataset.axis)
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
    topography = await repository.dataset(
        "4.0", "topography", generation_id=icdo_metadata.activation_identity
    )
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


@router.get("/{edition}/{axis}/list", response_model=IcdoPage)
async def list_records(
    repository: IcdoReads,
    repository_metadata: RepositoryMetadataReads,
    edition: Edition,
    axis: Axis,
    behaviour: str | None = None,
    level: Literal["category", "leaf"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> object:
    dataset = _dataset(edition, axis)
    ready = await _ready(repository_metadata, dataset.edition, dataset.axis)
    try:
        result = await repository.search(
            dataset.edition,
            dataset.axis,
            query="",
            behaviour=behaviour,
            level=level,
            limit=limit,
            offset=offset,
            generation_id=ready.activation_identity,
        )
        return {
            **result,
            "activation_identity": ready.activation_identity,
            "serving_identity": ready.serving_identity,
        }
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "ICD-O generation is invalid."
        ) from exc


@router.get("/{edition}/{axis}/search", response_model=IcdoPage)
async def search(
    repository: IcdoReads,
    repository_metadata: RepositoryMetadataReads,
    edition: Edition,
    axis: Axis,
    q: Annotated[str, Query(min_length=1)],
    behaviour: str | None = None,
    level: Literal["category", "leaf"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> object:
    dataset = _dataset(edition, axis)
    ready = await _ready(repository_metadata, dataset.edition, dataset.axis)
    try:
        result = await repository.search(
            dataset.edition,
            dataset.axis,
            query=q,
            behaviour=behaviour,
            level=level,
            limit=limit,
            offset=offset,
            generation_id=ready.activation_identity,
        )
        return {
            **result,
            "activation_identity": ready.activation_identity,
            "serving_identity": ready.serving_identity,
        }
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "ICD-O generation is invalid."
        ) from exc


@router.get("/{edition}/{axis}/concepts/{code}", response_model=IcdoDetail)
async def detail(
    repository: IcdoReads,
    xref_store: XrefReads,
    repository_metadata: RepositoryMetadataReads,
    edition: Edition,
    axis: Axis,
    code: Annotated[str, Path(min_length=1)],
) -> object:
    dataset = _dataset(edition, axis)
    ready = await _ready(repository_metadata, dataset.edition, dataset.axis)
    canonical = _decode_code(code, dataset.edition, dataset.axis)
    try:
        result = await repository.detail(
            dataset.edition,
            dataset.axis,
            canonical,
            generation_id=ready.activation_identity,
        )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "ICD-O generation is invalid."
        ) from exc
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ICD-O code not found.")
    rows: dict[str, list[MappingResult]] = {}
    if dataset is ServedIcdoDataset.ICDO_32_MORPHOLOGY:
        ncit = await repository_metadata.ncit()
        if isinstance(ncit, RepositoryUnhealthy):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, ncit.model_dump(mode="json")
            )
        try:
            rows = await xref_store.mappings_for_identifiers(
                {canonical},
                expected=XrefReadPolicy(
                    icdo=IcdoReadIdentity(
                        ncit_source_identity=ncit.source_identity,
                        icdo_generation_identity=ready.activation_identity,
                        icdo_serving_identity=ready.serving_identity,
                    )
                ),
            )
        except (StaleXrefGenerationError, UnavailableXrefGenerationError) as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    detail_type = {
        ("3.2", "morphology"): Morphology32Detail,
        ("4.0", "morphology"): Morphology40Detail,
        ("4.0", "topography"): Topography40Detail,
    }[(edition, axis)]
    return detail_type(
        edition=edition,
        axis=axis,
        activation_identity=ready.activation_identity,
        serving_identity=ready.serving_identity,
        record=IcdoRecord.model_validate(result).model_dump(),
        ncit_alignments=_ncit_alignments(canonical, edition, rows.get(canonical, [])),
    )
