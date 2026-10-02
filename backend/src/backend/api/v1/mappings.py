"""Mappings + FHIR-style $translate endpoints (issue #82, design §8.4)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import Field

from backend.api.v1.alignment import mapping_relative_to
from backend.config import get_settings
from backend.dependencies import RepositoryMetadataReads, XrefReads
from backend.icdo_datasets import ServedIcdoDataset
from backend.repository_metadata import RepositoryUnhealthy
from backend.security import has_icdo_entitlement
from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.repositories.xref.models import (
    IcdoReadIdentity,
    MappingResult,
    StaleXrefGenerationError,
    UberonReadIdentity,
    UnavailableXrefGenerationError,
    XrefReadPolicy,
)
from ontolib.repositories.xref.vocab import (
    BROAD_MATCH,
    CLOSE_MATCH,
    EXACT_MATCH,
    NARROW_MATCH,
    RELATED_MATCH,
    MappingPredicate,
)

FhirR4ConceptMapEquivalence = Literal[
    "relatedto",
    "equivalent",
    "equal",
    "wider",
    "subsumes",
    "narrower",
    "specializes",
    "inexact",
    "unmatched",
    "disjoint",
]

_SKOS_TO_EQUIVALENCE: dict[MappingPredicate, FhirR4ConceptMapEquivalence] = {
    EXACT_MATCH: "equivalent",
    CLOSE_MATCH: "inexact",
    BROAD_MATCH: "wider",
    NARROW_MATCH: "narrower",
    RELATED_MATCH: "relatedto",
}

router = APIRouter(prefix="/api/v1/mappings", tags=["mappings"])


class TranslateRequest(StrictBoundaryModel):
    """A code to translate through the mapping layer.

    ``code`` is an NCIt code (``C12400``) or an upstream CURIE
    (``UBERON:0002046``).  The endpoint searches both directions.
    """

    code: str = Field(min_length=1)


class TranslateConcept(StrictBoundaryModel):
    """The target concept in a translate result entry."""

    code: str
    system: str | None = None
    version: str | None = None


class TranslateEntry(StrictBoundaryModel):
    """One translate result using FHIR R4 ConceptMap equivalence codes."""

    equivalence: FhirR4ConceptMapEquivalence
    concept: TranslateConcept
    confidence: float = Field(ge=0.0, le=1.0)


class TranslateResponse(StrictBoundaryModel):
    """Result of a ``$translate`` lookup."""

    fhir_release: Literal["R4"] = "R4"
    result: list[TranslateEntry]


def _translate_entry(
    code: str,
    predicate: MappingPredicate | None,
    confidence: float,
    *,
    system: str | None = None,
    version: str | None = None,
) -> TranslateEntry:
    return TranslateEntry(
        equivalence=(
            _SKOS_TO_EQUIVALENCE[predicate] if predicate is not None else "unmatched"
        ),
        concept=TranslateConcept(code=code, system=system, version=version),
        confidence=confidence,
    )


def _collect_entries(
    rows_by_key: dict[str, list[MappingResult]],
    *,
    seen: set[tuple[str, str, str, str]],
) -> list[TranslateEntry]:
    entries: list[TranslateEntry] = []
    for requested_identifier, rows in rows_by_key.items():
        for row in rows:
            target, predicate = mapping_relative_to(row, requested_identifier)
            key = (target.system, target.version, target.identifier, predicate)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                _translate_entry(
                    target.identifier,
                    predicate,
                    row.confidence,
                    system=target.system,
                    version=target.version,
                )
            )
    return entries


async def _read_policy(
    metadata: RepositoryMetadataReads, *, include_icdo: bool
) -> XrefReadPolicy:
    ncit = await metadata.ncit()
    uberon = await metadata.uberon()
    icdo = (
        await metadata.icdo(ServedIcdoDataset.ICDO_32_MORPHOLOGY)
        if include_icdo
        else None
    )
    if isinstance(ncit, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Mapping sources are unavailable."
        )
    if isinstance(uberon, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Mapping sources are unavailable."
        )
    if isinstance(icdo, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Mapping sources are unavailable."
        )
    return XrefReadPolicy(
        uberon=UberonReadIdentity(
            ncit_source_identity=ncit.source_identity,
            uberon_source_identity=uberon.source_identity,
            uberon_serving_identity=uberon.observation.serving.sha256,
        ),
        icdo=(
            IcdoReadIdentity(
                ncit_source_identity=ncit.source_identity,
                icdo_generation_identity=icdo.activation_identity,
                icdo_serving_identity=icdo.serving_identity,
            )
            if icdo is not None
            else None
        ),
        allow_licensed=include_icdo,
    )


@router.post("/$translate", response_model=TranslateResponse)
async def translate(
    xref_store: XrefReads,
    metadata: RepositoryMetadataReads,
    body: TranslateRequest,
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> TranslateResponse:
    """FHIR-style ConceptMap ``$translate`` for NCIt↔upstream.

    Serves ``validated``/``active`` mappings, filtering
    ``proposed``, ``quarantined``, and other non-active lifecycles.  Licensed sources
    (SNOMED, ICD-O-3) require both server capability and valid consumer
    entitlement (D26, D71). Returns ``unmatched`` when no valid mapping exists.
    """
    settings = get_settings()
    code = body.code
    licensed_allowed = settings.enable_licensed_mappings and has_icdo_entitlement(
        x_icdo_entitlement
    )

    expected = await _read_policy(metadata, include_icdo=licensed_allowed)
    try:
        mappings = await xref_store.mappings_for_identifiers({code}, expected=expected)
    except (StaleXrefGenerationError, UnavailableXrefGenerationError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    seen: set[tuple[str, str, str, str]] = set()
    entries = _collect_entries(
        mappings,
        seen=seen,
    )

    if not entries:
        entries.append(_translate_entry(code, None, 0.0))

    return TranslateResponse(result=entries)
