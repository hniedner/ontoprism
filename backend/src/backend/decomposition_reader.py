"""Typed decomposition reads over the internal NCIt SPARQL transport."""

from collections.abc import Collection
from itertools import product
from typing import Protocol

from ontolib.decomposition import vocab
from ontolib.decomposition.read_queries import (
    build_compact_decomposition_query,
    build_publication_progress_query,
)


class _SelectClient(Protocol):
    async def select(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]: ...


class DecompositionReader:
    """Expose a code-based read without giving API routers a raw query method."""

    def __init__(self, client: _SelectClient) -> None:
        self._client = client

    async def rows_for(self, concept_code: str) -> list[dict[str, str]]:
        """Return decomposition rows for one injection-safe NCIt code."""
        rows = await self._client.select(
            build_compact_decomposition_query(concept_code),
            required_variables={"subject", "predicate", "value"},
        )
        return _expand_compact_rows(rows)

    async def publication_progress_rows(self) -> list[dict[str, str]]:
        """Return backend-computed D93 aggregates for the published graph."""
        return await self._client.select(
            build_publication_progress_query(),
            required_variables={
                "run",
                "publicationStatus",
                "publicationNotice",
                "category",
                "value",
                "count",
            },
        )


_FIELDS = {
    vocab.PUBLICATION_RUN: "run",
    vocab.PUBLICATION_STATUS: "publicationStatus",
    vocab.PUBLICATION_NOTICE: "publicationNotice",
    vocab.CONCEPT_OUTCOME: "outcome",
    vocab.OUTCOME_REASON: "outcomeReason",
    vocab.REPRESENTATION_STATUS: "status",
    vocab.DECOMPOSED_ON: "decomposedOn",
    vocab.AXIS: "axis",
    vocab.FILLER: "filler",
    vocab.AXIS_SOURCE: "axisSource",
    vocab.SOURCE_ROLE: "sourceRole",
    vocab.MOST_SPECIFIC: "mostSpecific",
    vocab.AXIS_AMBIGUOUS: "axisAmbiguous",
    vocab.SOURCE_STRUCTURAL_GROUP: "sourceStructuralGroup",
    vocab.NORMALIZED_PROJECTION_GROUP: "normalizedProjectionGroup",
    vocab.NORMALIZED_PROJECTION_GROUP_LABEL: "normalizedProjectionGroupLabel",
    vocab.NEEDS_REVIEW: "needsReview",
    vocab.SOURCE_DEFINITION_FACT: "sourceDefinitionFact",
    vocab.REVIEW_FLAG_KIND: "flagKind",
    vocab.REVIEW_FLAG_REASON: "flagReason",
}
_REPEATED = {"sourceRole", "sourceStructuralGroup", "sourceDefinitionFact"}


def _expand_compact_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    subjects: dict[str, dict[str, set[str]]] = {}
    linked: dict[str, str] = {}
    for row in rows:
        if row["predicate"] in {vocab.HAS_CONSTITUENT, vocab.HAS_REVIEW_FLAG}:
            linked[row["value"]] = row["predicate"]
        field = _FIELDS.get(row["predicate"])
        if field:
            subjects.setdefault(row["subject"], {}).setdefault(field, set()).add(
                row["value"]
            )
    _require_linked_fields(subjects, linked)
    return _expand_subject_fields(subjects)


def _require_linked_fields(
    subjects: dict[str, dict[str, set[str]]], linked: dict[str, str]
) -> None:
    for subject, predicate in linked.items():
        fields = subjects.setdefault(subject, {})
        if predicate == vocab.HAS_REVIEW_FLAG:
            fields["flag"] = {subject}
        elif not {"axis", "filler", "axisSource"}.issubset(fields):
            raise ValueError("published constituent is missing required fields")


def _expand_subject_fields(
    subjects: dict[str, dict[str, set[str]]],
) -> list[dict[str, str]]:
    result = []
    for subject, fields in subjects.items():
        if "flagKind" in fields or "flagReason" in fields:
            fields["flag"] = {subject}
        _require_single_scalars(fields)
        names = list(fields)
        result.extend(
            dict(zip(names, values, strict=True))
            for values in product(*(sorted(fields[name]) for name in names))
        )
    return result


def _require_single_scalars(fields: dict[str, set[str]]) -> None:
    if any(
        len(values) != 1 for name, values in fields.items() if name not in _REPEATED
    ):
        raise ValueError("published subject has conflicting scalar fields")
