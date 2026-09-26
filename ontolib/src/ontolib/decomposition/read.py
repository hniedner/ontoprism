"""Pure assembly of SPARQL rows into a ``ConceptDecomposition`` (design §9 read layer).

Kept separate from query execution so every parsing rule is unit-tested without a store.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast, get_args

from ontolib.decomposition import vocab
from ontolib.decomposition.models import AxisSource, ConceptOutcome
from ontolib.decomposition.provenance_models import ConceptReviewFlag, ReviewFlagKind
from ontolib.decomposition.read_models import (
    ConceptDecomposition,
    DecompositionConstituent,
    PublicationProgress,
    UpstreamMapping,
)
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import Iterable

Row = Mapping[str, str | None]
_SHA256_LENGTH = 64
_OUTCOMES = tuple(get_args(ConceptOutcome))
_REVIEW_FLAGS = tuple(get_args(ReviewFlagKind))


def _local(iri: str) -> str:
    """Local name from an IRI (``…#C6135`` -> ``C6135``)."""
    return iri.rsplit("#", 1)[-1]


def _axis_code(iri: str) -> str:
    """Axis identifier: an ``op:`` axis (e.g. ``op:Morphology``) keeps its prefix; an
    NCIt role IRI reduces to its code (``R88``)."""
    if iri.startswith(vocab.ONTOPRISM_NS):
        return f"op:{iri[len(vocab.ONTOPRISM_NS) :]}"
    return _local(iri)


def _as_bool(value: str | None) -> bool:
    if value is None or value in {"false", "0"}:
        return False
    if value in {"true", "1"}:
        return True
    raise ValueError(f"persisted RDF boolean is invalid: {value!r}")


def _require_sha256(value: str) -> str:
    if len(value) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("source definition fact does not contain a SHA-256 ID")
    return value


def _source_definition_id(iri: str | None, concept_code: str) -> str | None:
    if iri is None:
        return None
    if not iri.startswith(vocab.DEFINITION_FACT_NS):
        raise ValueError("source definition fact is outside the OntoPrism namespace")
    occurrence = iri.removeprefix(vocab.DEFINITION_FACT_NS)
    try:
        root_code, fact_id = occurrence.split("/", 1)
    except ValueError as exc:
        raise ValueError("source definition fact is not root-scoped") from exc
    if root_code != concept_code:
        raise ValueError("source definition fact belongs to a different root concept")
    return _require_sha256(fact_id)


def _source_role(iri: str | None) -> str | None:
    if iri is None:
        return None
    if not iri.startswith(NCIT_NS):
        raise ValueError("source role is outside the NCIt namespace")
    role_code = iri.removeprefix(NCIT_NS)
    if not role_code.startswith("R") or not role_code[1:].isdigit():
        raise ValueError("source role is not an NCIt role code")
    return role_code


def _axis_source(value: str | None) -> AxisSource:
    """Reject an absent or unrecognised ``op:axisSource``.

    Defaulting to ``"role"`` would report NLP- or parent-derived provenance to API
    clients as role-derived, which is the one thing this field exists to state.
    """
    if value not in get_args(AxisSource):
        raise ValueError(f"axis source is not a known provenance: {value!r}")
    return cast("AxisSource", value)


def _constituent_from_row(
    concept_code: str,
    axis_iri: str,
    filler_iri: str,
    row: Row,
) -> DecompositionConstituent:
    source_id = _source_definition_id(row.get("sourceDefinitionFact"), concept_code)
    return DecompositionConstituent(
        axis=_axis_code(axis_iri),
        filler=_local(filler_iri),
        axis_source=_axis_source(row.get("axisSource")),
        source_roles=(
            (source_role,)
            if (source_role := _source_role(row.get("sourceRole")))
            else ()
        ),
        most_specific=_as_bool(row.get("mostSpecific")),
        needs_review=_as_bool(row.get("needsReview")),
        axis_ambiguous=_as_bool(row.get("axisAmbiguous")),
        source_group_ids=(
            (source_group,)
            if (source_group := row.get("sourceStructuralGroup"))
            else ()
        ),
        normalized_group_id=row.get("normalizedProjectionGroup"),
        normalized_group_label=row.get("normalizedProjectionGroupLabel"),
        source_definition_ids=((source_id,) if source_id is not None else ()),
    )


def _without_repeated_fields(
    constituent: DecompositionConstituent,
) -> DecompositionConstituent:
    return constituent.model_copy(
        update={"source_definition_ids": (), "source_roles": (), "source_group_ids": ()}
    )


def _merge_constituent(
    existing: DecompositionConstituent | None,
    candidate: DecompositionConstituent,
) -> DecompositionConstituent:
    if existing is None:
        return candidate
    if _without_repeated_fields(existing) != _without_repeated_fields(candidate):
        raise ValueError("one constituent resolved to conflicting persisted fields")
    source_ids = tuple(
        sorted(
            set(existing.source_definition_ids) | set(candidate.source_definition_ids)
        )
    )
    source_roles = tuple(
        sorted(set(existing.source_roles) | set(candidate.source_roles))
    )
    source_group_ids = tuple(
        sorted(set(existing.source_group_ids) | set(candidate.source_group_ids))
    )
    return DecompositionConstituent.model_validate(
        existing.model_dump()
        | {
            "source_definition_ids": source_ids,
            "source_roles": source_roles,
            "source_group_ids": source_group_ids,
        }
    )


def _record_review_flag(row: Row, flags: set[tuple[str, str]]) -> None:
    kind, reason = row.get("flagKind"), row.get("flagReason")
    if any(row.get(key) is not None for key in ("flag", "flagKind", "flagReason")):
        if not kind or not reason:
            raise ValueError("published review flag requires a kind and reason")
        flags.add((kind, reason))


def decomposition_from_rows(code: str, rows: Iterable[Row]) -> ConceptDecomposition:
    """Fold the (repeating) result rows into one decomposition for *code*.

    Status/date repeat on every row (SPARQL cross-product with the constituents); the
    constituents are de-duplicated by (axis, filler) and sorted for determinism.
    """
    scalar: dict[str, str | None] = dict.fromkeys(
        (
            "status",
            "run",
            "decomposedOn",
            "publicationStatus",
            "publicationNotice",
            "outcome",
            "outcomeReason",
        ),
        None,
    )
    flags: set[tuple[str, str]] = set()
    constituents: dict[tuple[str, str], DecompositionConstituent] = {}

    for row in rows:
        for name, current in scalar.items():
            scalar[name] = current or row.get(name)
        _record_review_flag(row, flags)
        _record_constituent(code, row, constituents)

    return ConceptDecomposition.model_validate(
        {
            "code": code,
            "run_id": scalar["run"],
            "publication_status": scalar["publicationStatus"],
            "publication_notice": scalar["publicationNotice"],
            "outcome": scalar["outcome"],
            "outcome_reason": scalar["outcomeReason"],
            "review_flags": [
                ConceptReviewFlag.model_validate({"kind": kind, "reason": reason})
                for kind, reason in sorted(flags)
            ],
            "is_legacy_precoordinated": (
                scalar["status"] == vocab.LEGACY_PRECOORDINATED
            ),
            "decomposed_on": scalar["decomposedOn"],
            "constituents": sorted(
                constituents.values(), key=lambda c: (c.axis, c.filler)
            ),
        }
    )


def publication_progress_from_rows(rows: Iterable[Row]) -> PublicationProgress | None:
    """Build published-run progress from backend query aggregates."""
    materialized = tuple(rows)
    if not materialized:
        return None
    run_id = _publication_progress_identity(materialized)
    outcome_counts = dict.fromkeys(_OUTCOMES, 0)
    flag_counts = dict.fromkeys(_REVIEW_FLAGS, 0)
    for row in materialized:
        _record_progress_count(row, outcome_counts, flag_counts)
    return PublicationProgress.model_validate(
        {
            "run_id": run_id,
            "publication_status": vocab.PROVISIONAL,
            "publication_notice": vocab.EXPERT_REVIEW_NOTICE,
            "total_concepts": sum(outcome_counts.values()),
            "outcome_counts": outcome_counts,
            "review_flag_counts": flag_counts,
        }
    )


def _publication_progress_identity(rows: tuple[Row, ...]) -> str:
    run_ids = {row.get("run") for row in rows}
    _require_single_progress_value(run_ids, "publication progress has no unique run")
    _require_exact_progress_value(
        rows,
        "publicationStatus",
        vocab.PROVISIONAL,
        "publication progress is not provisional",
    )
    _require_exact_progress_value(
        rows,
        "publicationNotice",
        vocab.EXPERT_REVIEW_NOTICE,
        "publication progress has an unexpected notice",
    )
    return cast("str", next(iter(run_ids)))


def _require_single_progress_value(values: set[str | None], message: str) -> None:
    if None in values or len(values) != 1:
        raise ValueError(message)


def _require_exact_progress_value(
    rows: tuple[Row, ...], field: str, expected: str, message: str
) -> None:
    if {row.get(field) for row in rows} != {expected}:
        raise ValueError(message)


def _record_progress_count(
    row: Row,
    outcomes: dict[ConceptOutcome, int],
    flags: dict[ReviewFlagKind, int],
) -> None:
    category = row.get("category")
    value = row.get("value")
    try:
        count = int(row.get("count") or "")
    except ValueError as exc:
        raise ValueError("publication progress contains an invalid count") from exc
    if category == "outcome" and value in outcomes:
        outcomes[cast("ConceptOutcome", value)] = count
        return
    if category == "review-flag" and value in flags:
        flags[cast("ReviewFlagKind", value)] = count
        return
    raise ValueError("publication progress contains an unknown D93 category")


def _record_constituent(
    code: str,
    row: Row,
    constituents: dict[tuple[str, str], DecompositionConstituent],
) -> None:
    axis_iri = row.get("axis")
    filler_iri = row.get("filler")
    if not axis_iri or not filler_iri:
        return
    key = (axis_iri, filler_iri)
    candidate = _constituent_from_row(code, axis_iri, filler_iri, row)
    constituents[key] = _merge_constituent(constituents.get(key), candidate)


def attach_upstream(
    decomp: ConceptDecomposition,
    upstream_by_filler: dict[str, list[UpstreamMapping]],
) -> ConceptDecomposition:
    new_constituents = [
        c.model_copy(update={"upstream": upstream_by_filler.get(c.filler, [])})
        for c in decomp.constituents
    ]
    return decomp.model_copy(update={"constituents": new_constituents})
