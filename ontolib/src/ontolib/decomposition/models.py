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
class DefinitionGroup:
    """One canonical stated ``owl:intersectionOf`` expression.

    ``child_group_ids`` preserves nested anonymous intersections without relying on
    store-local blank-node labels. A group belongs to one named definition anchor and
    one named-genus DAG depth; several parent groups may reference the same canonical
    child when the stated RDF graph reuses an equivalent anonymous expression.
    """

    group_id: str
    anchor_code: str
    depth: int
    child_group_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_sha256(self.group_id, "group_id")
        _require_code(self.anchor_code, _CONCEPT_CODE, "anchor_code")
        if self.depth < 0:
            raise ValueError("depth must be non-negative")
        canonical = tuple(sorted(set(self.child_group_ids)))
        for child_group_id in canonical:
            _require_sha256(child_group_id, "child_group_ids item")
        object.__setattr__(self, "child_group_ids", canonical)


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
    groups: tuple[DefinitionGroup, ...] = ()
    root_group_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_code(self.root_code, _CONCEPT_CODE, "root_code")
        canonical = _canonical_definition_facts(self.facts)
        groups, group_by_id, child_group_ids = _canonical_definition_groups(
            self.groups or _groups_from_facts(canonical)
        )
        roots = _canonical_definition_roots(
            self.root_group_ids,
            groups,
            group_by_id,
            child_group_ids,
        )
        _validate_definition_group_graph(group_by_id, roots)
        _validate_definition_fact_groups(canonical, group_by_id)
        object.__setattr__(self, "facts", canonical)
        object.__setattr__(self, "groups", groups)
        object.__setattr__(self, "root_group_ids", roots)

    @property
    def identity(self) -> str:
        """Stable identity independent of row order and store blank-node labels."""
        payload = {
            "root_code": self.root_code,
            "root_group_ids": self.root_group_ids,
            "groups": [
                {
                    field_name: getattr(group, field_name)
                    for field_name in group.__dataclass_fields__
                }
                for group in self.groups
            ],
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


def _canonical_definition_facts(
    facts: tuple[DefinitionFact, ...],
) -> tuple[DefinitionFact, ...]:
    canonical = tuple(sorted(facts, key=lambda fact: fact.fact_id))
    if len({fact.fact_id for fact in canonical}) != len(canonical):
        raise ValueError("complete-definition fact IDs must be unique")
    return canonical


def _canonical_definition_groups(
    groups: tuple[DefinitionGroup, ...],
) -> tuple[
    tuple[DefinitionGroup, ...],
    dict[str, DefinitionGroup],
    set[str],
]:
    canonical = tuple(sorted(groups, key=lambda group: group.group_id))
    group_by_id = {group.group_id: group for group in canonical}
    if len(group_by_id) != len(canonical):
        raise ValueError("complete-definition group IDs must be unique")
    child_group_ids = {
        child_group_id
        for group in canonical
        for child_group_id in group.child_group_ids
    }
    if child_group_ids - group_by_id.keys():
        raise ValueError("complete-definition group references an unknown child")
    return canonical, group_by_id, child_group_ids


def _canonical_definition_roots(
    root_group_ids: tuple[str, ...],
    groups: tuple[DefinitionGroup, ...],
    group_by_id: dict[str, DefinitionGroup],
    child_group_ids: set[str],
) -> tuple[str, ...]:
    roots = tuple(sorted(set(root_group_ids)))
    if not roots and groups:
        roots = tuple(sorted(group_by_id.keys() - child_group_ids))
    if set(roots) - group_by_id.keys():
        raise ValueError("complete-definition root references an unknown group")
    return roots


def _validate_definition_fact_groups(
    facts: tuple[DefinitionFact, ...],
    group_by_id: dict[str, DefinitionGroup],
) -> None:
    for fact in facts:
        group = group_by_id.get(fact.group_id)
        if group is None:
            raise ValueError("complete-definition fact references an unknown group")
        if (fact.anchor_code, fact.depth) != (group.anchor_code, group.depth):
            raise ValueError(
                "complete-definition fact and group anchors/depths must agree"
            )


def _groups_from_facts(
    facts: tuple[DefinitionFact, ...],
) -> tuple[DefinitionGroup, ...]:
    """Build flat groups for historical/manual records created before nested groups."""
    group_shapes: dict[str, tuple[str, int]] = {}
    for fact in facts:
        shape = (fact.anchor_code, fact.depth)
        previous = group_shapes.setdefault(fact.group_id, shape)
        if previous != shape:
            raise ValueError(
                "complete-definition group cannot span anchors or DAG depths"
            )
    return tuple(
        DefinitionGroup(
            group_id=group_id,
            anchor_code=anchor_code,
            depth=depth,
        )
        for group_id, (anchor_code, depth) in group_shapes.items()
    )


def _validate_definition_group_graph(
    groups: dict[str, DefinitionGroup],
    roots: tuple[str, ...],
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(group_id: str) -> None:
        if group_id in visiting:
            raise ValueError("complete-definition group graph contains a cycle")
        if group_id in visited:
            return
        visiting.add(group_id)
        group = groups[group_id]
        for child_group_id in group.child_group_ids:
            child = groups[child_group_id]
            if (child.anchor_code, child.depth) != (group.anchor_code, group.depth):
                raise ValueError(
                    "nested definition groups must share an anchor and DAG depth"
                )
            visit(child_group_id)
        visiting.remove(group_id)
        visited.add(group_id)

    for root_group_id in roots:
        visit(root_group_id)
    if visited != groups.keys():
        raise ValueError("complete-definition group graph has no reachable root")


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

    ``axis`` is the normalized ``op:`` relation (or an unknown legacy NCIt role);
    ``source_role`` preserves the defining NCIt role independently. ``most_specific``
    records that the filler was chosen over a strictly broader is-a/R82 candidate;
    ``needs_review`` flags an unresolved ordinary axis for curation. ``group`` is a D19
    relationship-group id shared by ambiguous fillers on the same routed axis.
    """

    axis: str
    filler_code: str
    axis_source: AxisSource
    source_role: str | None = None
    most_specific: bool = False
    needs_review: bool = False
    group: str | None = None
    source_definition_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_role is not None and (
            not self.source_role.startswith("R") or not self.source_role[1:].isdigit()
        ):
            raise ValueError("source_role must be an NCIt role code")
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
