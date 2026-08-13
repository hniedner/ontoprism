"""Mappings + FHIR-style $translate endpoints (issue #82, design §8.4)."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.dependencies import RepositoryMetadataReads, XrefReads
from backend.repository_metadata import RepositoryUnhealthy
from ontolib.repositories.xref.models import (
    EndpointIdentity,
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
)

_LICENSED_PREFIXES = frozenset({"SNOMED", "ICD-O-3"})

_SKOS_TO_EQUIVALENCE: dict[str, str] = {
    EXACT_MATCH: "equivalent",
    CLOSE_MATCH: "close",
    BROAD_MATCH: "broad",
    NARROW_MATCH: "narrow",
}

_ACTIVE_LIFECYCLES = frozenset({"validated", "active"})


def _is_licensed(endpoint: EndpointIdentity) -> bool:
    prefix = (
        endpoint.identifier.split(":", maxsplit=1)[0]
        if ":" in endpoint.identifier
        else ""
    )
    return endpoint.system == "icdo" or prefix in _LICENSED_PREFIXES


router = APIRouter(prefix="/api/v1/mappings", tags=["mappings"])


class TranslateRequest(BaseModel):
    """A code to translate through the mapping layer.

    ``code`` is an NCIt code (``C12400``) or an upstream CURIE
    (``UBERON:0002046``).  The endpoint searches both directions.
    """

    code: str = Field(min_length=1)


class TranslateConcept(BaseModel):
    """The target concept in a translate result entry."""

    code: str
    system: str | None = None
    version: str | None = None


class TranslateEntry(BaseModel):
    """One translate result — the equivalence and target concept."""

    equivalence: str
    concept: TranslateConcept
    confidence: float = Field(ge=0.0, le=1.0)


class TranslateResponse(BaseModel):
    """Result of a ``$translate`` lookup."""

    result: list[TranslateEntry]


def _translate_entry(
    code: str,
    pred: str,
    confidence: float,
    *,
    system: str | None = None,
    version: str | None = None,
) -> TranslateEntry:
    return TranslateEntry(
        equivalence=_SKOS_TO_EQUIVALENCE.get(pred, "unmatched"),
        concept=TranslateConcept(code=code, system=system, version=version),
        confidence=confidence,
    )


def _is_eligible(
    row: MappingResult, *, target: EndpointIdentity, licensed_allowed: bool
) -> bool:
    return row.lifecycle in _ACTIVE_LIFECYCLES and (
        licensed_allowed or not _is_licensed(target)
    )


def _collect_entries(
    rows_by_key: dict[str, list[MappingResult]],
    *,
    reverse: bool,
    licensed_allowed: bool,
    seen: set[tuple[str, str, str, str]],
) -> list[TranslateEntry]:
    entries: list[TranslateEntry] = []
    for rows in rows_by_key.values():
        for row in rows:
            target = row.subject if reverse else row.object
            if not _is_eligible(
                row,
                target=target,
                licensed_allowed=licensed_allowed,
            ):
                continue
            key = (target.system, target.version, target.identifier, row.predicate)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                _translate_entry(
                    target.identifier,
                    row.predicate,
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
    icdo = await metadata.icdo("3.2", "morphology") if include_icdo else None
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
    )


@router.post("/$translate", response_model=TranslateResponse)
async def translate(
    xref_store: XrefReads,
    metadata: RepositoryMetadataReads,
    body: TranslateRequest,
) -> TranslateResponse:
    """FHIR-style ConceptMap ``$translate`` for NCIt↔upstream.

    Serves ``validated``/``active`` mappings, filtering
    ``proposed``, ``quarantined``, and other non-active lifecycles.  Licensed sources
    (SNOMED, ICD-O-3) are filtered out when
    ``enable_licensed_mappings`` is False (D26).  Returns ``unmatched``
    when no valid mapping exists.
    """
    settings = get_settings()
    code = body.code

    expected = await _read_policy(
        metadata, include_icdo=settings.enable_licensed_mappings
    )
    try:
        upstream = await xref_store.mappings_by_subjects({code}, expected=expected)
        reverse = await xref_store.mappings_by_objects({code}, expected=expected)
    except (StaleXrefGenerationError, UnavailableXrefGenerationError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    seen: set[tuple[str, str, str, str]] = set()
    entries = _collect_entries(
        upstream,
        reverse=False,
        licensed_allowed=settings.enable_licensed_mappings,
        seen=seen,
    )
    entries.extend(
        _collect_entries(
            reverse,
            reverse=True,
            licensed_allowed=settings.enable_licensed_mappings,
            seen=seen,
        )
    )

    if not entries:
        entries.append(_translate_entry(code, "", 0.0))

    return TranslateResponse(result=entries)
