"""Pure data models for the NCIt decomposition engine (Issue #4 / M5).

No FastAPI or DB coupling — these are the deterministic value objects the detector,
filler-selection, and (later) writer/provenance layers exchange. See
``docs/design/ncit-decomposition-engine.md`` §4.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal

# How an axis/constituent was recovered — the ``op:axisSource`` provenance value.
AxisSource = Literal["role", "nlp", "parent"]
_CONCEPT_CODE = re.compile(r"C[0-9]+")
_ROLE_CODE = re.compile(r"R[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require_code(value: str, pattern: re.Pattern[str], field_name: str) -> None:
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid: {value!r}")


def _require_sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 value")


@dataclass(frozen=True, slots=True, kw_only=True)
class GenusDefinitionFact:
    """One named genus edge in a stated equivalent-class intersection."""

    fact_id: str
    anchor_code: str
    group_id: str
    depth: int
    genus_code: str
    is_defined: bool

    def __post_init__(self) -> None:
        _require_sha256(self.fact_id, "fact_id")
        _require_sha256(self.group_id, "group_id")
        _require_code(self.anchor_code, _CONCEPT_CODE, "anchor_code")
        _require_code(self.genus_code, _CONCEPT_CODE, "genus_code")
        if self.depth < 0:
            raise ValueError("depth must be non-negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class RestrictionDefinitionFact:
    """One stated existential restriction in an equivalent-class intersection."""

    fact_id: str
    anchor_code: str
    group_id: str
    depth: int
    role_code: str
    filler_code: str

    def __post_init__(self) -> None:
        _require_sha256(self.fact_id, "fact_id")
        _require_sha256(self.group_id, "group_id")
        _require_code(self.anchor_code, _CONCEPT_CODE, "anchor_code")
        _require_code(self.role_code, _ROLE_CODE, "role_code")
        _require_code(self.filler_code, _CONCEPT_CODE, "filler_code")
        if self.depth < 0:
            raise ValueError("depth must be non-negative")


DefinitionFact = GenusDefinitionFact | RestrictionDefinitionFact


@dataclass(frozen=True, slots=True, kw_only=True)
class CompleteDefinition:
    """Canonical stated definition DAG for one decomposed source concept."""

    root_code: str
    facts: tuple[DefinitionFact, ...]

    def __post_init__(self) -> None:
        _require_code(self.root_code, _CONCEPT_CODE, "root_code")
        canonical = tuple(sorted(self.facts, key=lambda fact: fact.fact_id))
        if len({fact.fact_id for fact in canonical}) != len(canonical):
            raise ValueError("complete-definition fact IDs must be unique")
        object.__setattr__(self, "facts", canonical)

    @property
    def identity(self) -> str:
        """Stable identity independent of row order and store blank-node labels."""
        payload = {
            "root_code": self.root_code,
            "facts": [
                {
                    field_name: getattr(fact, field_name)
                    for field_name in fact.__dataclass_fields__
                }
                for fact in self.facts
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RoleRestriction:
    """One OWL ``someValuesFrom`` role restriction read from the stated graph.

    ``role_code`` is the NCIt property code (e.g. ``R105``); ``role_label`` is its
    human-readable name (e.g. ``Disease_Has_Abnormal_Cell``) when resolvable — the
    label is what the ``Excludes_*`` / defining classification keys on.
    ``anchoring_genus`` is the genus code on which this restriction was found during
    the DAG walk (populated by PR-B; ``None`` on the flat path).
    """

    role_code: str
    filler_code: str
    role_label: str | None = None
    anchoring_genus: str | None = None


@dataclass(frozen=True, slots=True)
class Constituent:
    """A single decomposed constituent: an axis and the concept that fills it.

    ``axis`` is the NCIt role code (reused as the axis identifier) or an ``op:`` axis
    such as ``op:Morphology``. ``most_specific`` records that the filler was chosen over
    a strictly broader is-a/R82 candidate; ``needs_review`` flags an unresolved ordinary
    axis for curation. ``group`` is a D19 relationship-group id shared by ambiguous
    fillers on the same routed axis, including hierarchy-related lineage values.
    """

    axis: str
    filler_code: str
    axis_source: AxisSource
    most_specific: bool = False
    needs_review: bool = False
    group: str | None = None
    source_definition_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        canonical = tuple(sorted(set(self.source_definition_ids)))
        for source_id in canonical:
            _require_sha256(source_id, "source_definition_ids item")
        object.__setattr__(self, "source_definition_ids", canonical)


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """The detector's verdict for one concept."""

    code: str
    is_precoordinated: bool
    defining_role_count: int
    semantic_type: str | None
    label_multi_aspect: bool = False


def _referenced_source_ids(constituents: list[Constituent]) -> set[str]:
    return {
        source_id
        for constituent in constituents
        for source_id in constituent.source_definition_ids
    }


def _validate_definition_link(
    code: str,
    constituents: list[Constituent],
    complete_definition: CompleteDefinition | None,
) -> None:
    referenced = _referenced_source_ids(constituents)
    if complete_definition is None:
        if referenced:
            raise ValueError(
                "constituent source-definition references require a complete definition"
            )
        return
    if complete_definition.root_code != code:
        raise ValueError("complete-definition root does not match decomposition code")
    known = {fact.fact_id for fact in complete_definition.facts}
    unknown = referenced - known
    if unknown:
        raise ValueError(f"unknown complete-definition fact referenced: {min(unknown)}")


@dataclass(frozen=True, slots=True)
class Decomposition:
    """A decomposed concept: its source code and its constituents (roles-first)."""

    code: str
    semantic_type: str | None
    constituents: list[Constituent] = field(default_factory=list)
    complete_definition: CompleteDefinition | None = None

    def __post_init__(self) -> None:
        _validate_definition_link(
            self.code,
            self.constituents,
            self.complete_definition,
        )

    @property
    def axes(self) -> set[str]:
        """The distinct axes covered by this decomposition."""
        return {c.axis for c in self.constituents}

    @property
    def complete_fact_count(self) -> int:
        return len(self.complete_definition.facts) if self.complete_definition else 0

    @property
    def projected_fact_count(self) -> int:
        return len(
            {
                source_id
                for constituent in self.constituents
                for source_id in constituent.source_definition_ids
            }
        )

    @property
    def projection_loss_count(self) -> int:
        return self.complete_fact_count - self.projected_fact_count
