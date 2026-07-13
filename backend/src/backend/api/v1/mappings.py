"""Mappings + FHIR-style $translate endpoints (issue #82, design §8.4)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.dependencies import XrefReads
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


def _is_licensed(object_id: str) -> bool:
    prefix = object_id.split(":", maxsplit=1)[0] if ":" in object_id else ""
    return prefix in _LICENSED_PREFIXES


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


class TranslateEntry(BaseModel):
    """One translate result — the equivalence and target concept."""

    equivalence: str
    concept: TranslateConcept
    confidence: float = Field(ge=0.0, le=1.0)


class TranslateResponse(BaseModel):
    """Result of a ``$translate`` lookup."""

    result: list[TranslateEntry]


def _translate_entry(code: str, pred: str, confidence: float) -> TranslateEntry:
    return TranslateEntry(
        equivalence=_SKOS_TO_EQUIVALENCE.get(pred, "unmatched"),
        concept=TranslateConcept(code=code),
        confidence=confidence,
    )


def _collect_entries(
    rows_by_key: dict[str, list[tuple[str, str, str, float]]],
    *,
    licensed_allowed: bool,
    seen: set[tuple[str, str]],
) -> list[TranslateEntry]:
    entries: list[TranslateEntry] = []
    for rows in rows_by_key.values():
        for target_id, pred, lifecycle, confidence in rows:
            if lifecycle not in _ACTIVE_LIFECYCLES:
                continue
            if not licensed_allowed and _is_licensed(target_id):
                continue
            key = (target_id, pred)
            if key in seen:
                continue
            seen.add(key)
            entries.append(_translate_entry(target_id, pred, confidence))
    return entries


@router.post("/$translate", response_model=TranslateResponse)
async def translate(
    xref_store: XrefReads,
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

    upstream = await xref_store.mappings_by_subjects({code})
    reverse = await xref_store.mappings_by_objects({code})

    seen: set[tuple[str, str]] = set()
    entries = _collect_entries(
        upstream,
        licensed_allowed=settings.enable_licensed_mappings,
        seen=seen,
    )
    entries.extend(
        _collect_entries(
            reverse,
            licensed_allowed=settings.enable_licensed_mappings,
            seen=seen,
        )
    )

    if not entries:
        entries.append(_translate_entry(code, "", 0.0))

    return TranslateResponse(result=entries)
