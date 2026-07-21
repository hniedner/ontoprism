"""SSSOM mapping record (Matentzoglu 2022) — one row per cross-ontology mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ontolib.repositories.xref.vocab import ALLOWED_PREDICATES, LIFECYCLE_STATES

if TYPE_CHECKING:
    from ontolib.repositories.xref.evidence import Evidence


@dataclass(frozen=True)
class SSSOMRecord:
    """NCIt<->upstream mapping with provenance.

    The five id/version fields are required; ``lifecycle_state``, ``review_status``,
    ``author`` and ``evidence`` carry defaults.
    """

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
    # The independent signals that promoted this bridge (#122, D36). Empty for a
    # candidate; a record acquires evidence only by being promoted — `promote_candidate`
    # is the sole writer, and it sets `evidence` in the same `replace()` that flips the
    # predicate to `exactMatch` and the lifecycle to `validated`. So evidence rides only
    # on validated bridges, by construction, not by convention.
    #
    # `compare=False` keeps it out of equality and hashing, because evidence is
    # provenance, not identity: the mapping is the same bridge whatever justified it.
    # Nothing currently compares whole records or keys a set/dict on one anyway (the
    # `_one_per_pair` dedup keys on an explicit `(subject_id, object_id)` tuple), so
    # this is a guard against a future caller doing so, not a fix for a live path.
    evidence: tuple[Evidence, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        for field_name in (
            "subject_id",
            "object_id",
            "mapping_justification",
            "subject_source_version",
            "object_source_version",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty")
        if self.predicate_id not in ALLOWED_PREDICATES:
            raise ValueError(f"predicate_id not allowed: {self.predicate_id}")
        if self.lifecycle_state not in LIFECYCLE_STATES:
            raise ValueError(f"lifecycle_state not allowed: {self.lifecycle_state}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")
