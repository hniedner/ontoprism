"""Source-bound inspection report models; this module never publishes mappings."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from ontolib.repositories.icdo.models import CanonicalDataset, IcdoRecord

Classification = Literal[
    "one-supported-candidate",
    "multiple-candidates",
    "no-candidate",
    "broader-narrower-mismatch",
    "intentionally-unresolved",
    "source-data-anomaly",
]
EvidenceKind = Literal[
    "exact-code-provenance", "normalized-preferred", "normalized-synonym", "hierarchy"
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CongruenceEvidence(_Model):
    kind: EvidenceKind
    candidate: str
    value: str


class CongruenceRow(_Model):
    code: str
    classification: Classification
    reason: str
    candidates: tuple[str, ...] = ()
    evidence: tuple[CongruenceEvidence, ...] = ()


def _validate_coverage(
    source_codes: tuple[str, ...], rows: tuple[CongruenceRow, ...]
) -> None:
    row_codes = tuple(row.code for row in rows)
    if len(row_codes) != len(set(row_codes)) or set(row_codes) != set(source_codes):
        raise ValueError("every source code must appear exactly once")


def _report_payload(
    icdo_identity: str, uberon_identity: str, rows: tuple[CongruenceRow, ...]
) -> bytes:
    return json.dumps(
        {
            "icdo": icdo_identity,
            "uberon": uberon_identity,
            "rows": [row.model_dump(mode="json") for row in rows],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class CongruenceReport(_Model):
    report_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    icdo_serving_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    uberon_serving_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    total: int
    counts: dict[str, int]
    rows: tuple[CongruenceRow, ...]

    @classmethod
    def build(
        cls,
        *,
        icdo_serving_identity: str,
        uberon_serving_identity: str,
        source_codes: tuple[str, ...],
        rows: tuple[CongruenceRow, ...],
    ) -> CongruenceReport:
        _validate_coverage(source_codes, rows)
        ordered = tuple(sorted(rows, key=lambda row: row.code))
        counts = dict(sorted(Counter(row.classification for row in ordered).items()))
        payload = _report_payload(
            icdo_serving_identity, uberon_serving_identity, ordered
        )
        return cls(
            report_identity=hashlib.sha256(payload).hexdigest(),
            icdo_serving_identity=icdo_serving_identity,
            uberon_serving_identity=uberon_serving_identity,
            total=len(ordered),
            counts=counts,
            rows=ordered,
        )


def _normalize(value: str) -> str:
    return " ".join(
        "".join(
            character if character.isalnum() else " " for character in value.casefold()
        ).split()
    )


def _classify_record(
    record: IcdoRecord,
    inventory: dict[str, set[tuple[str, EvidenceKind, str]]],
    parents: dict[str, set[tuple[str, EvidenceKind, str]]],
) -> CongruenceRow:
    special = _special_row(record)
    if special is not None:
        return special
    preferred = record.preferred or ""
    normalized = _normalize(preferred)
    matches = _candidate_matches(record, normalized, inventory, parents)
    candidates = tuple(sorted({match[0] for match in matches}))
    evidence = tuple(
        CongruenceEvidence(kind=kind, candidate=code, value=value)
        for code, kind, value in sorted(matches)
    )
    classification, reason = _verdict(record.level, candidates)
    return CongruenceRow(
        code=record.code,
        classification=classification,
        reason=reason,
        candidates=candidates,
        evidence=evidence,
    )


def _special_row(record: IcdoRecord) -> CongruenceRow | None:
    if record.preferred is None:
        return CongruenceRow(
            code=record.code,
            classification="source-data-anomaly",
            reason="source record has no category/preferred term",
        )
    lowered = record.preferred.casefold()
    if record.code != "C80.9" and not any(
        marker in lowered for marker in ("unknown primary", "unspecified")
    ):
        return None
    return CongruenceRow(
        code=record.code,
        classification="intentionally-unresolved",
        reason="publisher category does not identify an anatomical site",
    )


def _candidate_matches(
    record: IcdoRecord,
    normalized: str,
    inventory: dict[str, set[tuple[str, EvidenceKind, str]]],
    parents: dict[str, set[tuple[str, EvidenceKind, str]]],
) -> set[tuple[str, EvidenceKind, str]]:
    terms = (normalized, normalized.removesuffix(" nos"))
    matches = next(
        (inventory[term] for term in terms if term in inventory),
        set(),
    )
    category_matches = _contained_matches(normalized, inventory)
    matches = matches or (category_matches if record.level == "category" else set())
    return matches | parents.get(normalized, set())


def _contained_matches(
    normalized: str,
    inventory: dict[str, set[tuple[str, EvidenceKind, str]]],
) -> set[tuple[str, EvidenceKind, str]]:
    return set().union(
        *(values for term, values in inventory.items() if term and term in normalized)
    )


def _verdict(level: str, candidates: tuple[str, ...]) -> tuple[Classification, str]:
    if len(candidates) > 1:
        return (
            "multiple-candidates",
            "multiple candidates retained; category granularity may differ",
        )
    if level == "category" and candidates:
        return (
            "broader-narrower-mismatch",
            "category candidate has a different granularity",
        )
    if candidates:
        return "one-supported-candidate", "one candidate retained for inspection"
    return "no-candidate", "no normalized preferred or synonym candidate"


def build_congruence_report(
    topography: CanonicalDataset,
    *,
    icdo_serving_identity: str,
    uberon_serving_identity: str,
    uberon_records: tuple[dict[str, str], ...],
) -> CongruenceReport:
    inventory: dict[str, set[tuple[str, EvidenceKind, str]]] = {}
    parent_inventory: dict[str, set[tuple[str, EvidenceKind, str]]] = {}
    for record in uberon_records:
        code = record["code"]
        label = record["label"]
        inventory.setdefault(_normalize(label), set()).add(
            (code, "normalized-preferred", label)
        )
        for synonym in filter(None, record.get("synonyms", "").split("||")):
            inventory.setdefault(_normalize(synonym), set()).add(
                (code, "normalized-synonym", synonym)
            )
        for parent in filter(None, record.get("parents", "").split("||")):
            parent_inventory.setdefault(_normalize(parent), set()).add(
                (code, "hierarchy", parent)
            )
    rows = tuple(
        _classify_record(record, inventory, parent_inventory)
        for record in topography.records
    )
    return CongruenceReport.build(
        icdo_serving_identity=icdo_serving_identity,
        uberon_serving_identity=uberon_serving_identity,
        source_codes=tuple(record.code for record in topography.records),
        rows=rows,
    )
