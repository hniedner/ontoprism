"""SSSOM mapping record (Matentzoglu 2022) — one row per cross-ontology mapping."""

from __future__ import annotations

from dataclasses import dataclass

from ontolib.repositories.xref.vocab import ALLOWED_PREDICATES, LIFECYCLE_STATES


@dataclass(frozen=True)
class SSSOMRecord:
    """NCIt<->upstream mapping with provenance (all fields required except author)."""

    subject_id: str
    predicate_id: str
    object_id: str
    mapping_justification: str
    confidence: float
    subject_source_version: str
    object_source_version: str
    lifecycle_state: str = "proposed"
    review_status: str = "unreviewed"
    author: str = ""

    def __post_init__(self) -> None:
        for field in (
            "subject_id",
            "object_id",
            "mapping_justification",
            "subject_source_version",
            "object_source_version",
        ):
            if not getattr(self, field):
                raise ValueError(f"{field} must be non-empty")
        if self.predicate_id not in ALLOWED_PREDICATES:
            raise ValueError(f"predicate_id not allowed: {self.predicate_id}")
        if self.lifecycle_state not in LIFECYCLE_STATES:
            raise ValueError(f"lifecycle_state not allowed: {self.lifecycle_state}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")
