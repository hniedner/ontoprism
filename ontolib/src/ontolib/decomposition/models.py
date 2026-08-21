"""Pure data models for the NCIt decomposition engine (Issue #4 / M5).

No FastAPI or DB coupling — these are the deterministic value objects the detector,
filler-selection, and (later) writer/provenance layers exchange. See
``docs/design/ncit-decomposition-engine.md`` §4.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

# How an axis/constituent was recovered — the ``op:axisSource`` provenance value.
AxisSource = Literal["role", "nlp", "parent"]
ConceptOutcome = Literal[
    "decomposed",
    "residual",
    "semantic-excluded",
    "atomic-no-op",
    "unknown",
]
_CONCEPT_CODE = re.compile(r"C[0-9]+")
_ROLE_CODE = re.compile(r"R[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# A constituent axis is either a normalized op: relation or an unknown NCIt role
# carried through verbatim for downstream review.
_AXIS_OR_ROLE = re.compile(r"op:[A-Za-z][A-Za-z0-9]*|R[0-9]+")
# A filler is an NCIt code or a minted proposal id (see decomposition.minting).
_FILLER_CODE = re.compile(r"C[0-9]+|MINT-[0-9a-f]{12}")


def _require_code(value: str, pattern: re.Pattern[str], field_name: str) -> None:
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid: {value!r}")


def _require_sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 value")


def _canonical_source_roles(
    axis: str,
    axis_source: AxisSource,
    source_roles: tuple[str, ...],
) -> tuple[str, ...]:
    canonical = tuple(sorted(set(source_roles)))
    if axis_source == "role":
        if not canonical and _ROLE_CODE.fullmatch(axis):
            return (axis,)
        if not canonical:
            raise ValueError("role-derived constituent requires source_roles")
        return canonical
    if canonical:
        raise ValueError("parent/NLP constituents must have empty source_roles")
    return ()


def _require_source_roles(values: tuple[str, ...]) -> None:
    if any(_ROLE_CODE.fullmatch(value) is None for value in values):
        raise ValueError("source_roles must contain only NCIt role codes")


def _definition_digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def canonical_definition_fact_id(
    anchor_code: str,
    group_id: str,
    kind: Literal["genus", "restriction"],
    *values: str,
) -> str:
    """Return the content-derived identity for one stated definition fact."""
    return _definition_digest(anchor_code, group_id, kind, *values)


def canonical_definition_group_id(
    anchor_code: str,
    member_signatures: Sequence[str],
) -> str:
    """Return the content-derived identity for one stated intersection group."""
    return _definition_digest(anchor_code, *sorted(set(member_signatures)))


def canonical_source_occurrence_id(
    root_code: str,
    source_fact_id: str,
    structural_path: Sequence[int],
) -> str:
    """Return the stable identity for one structural source-fact occurrence."""
    return _definition_digest(
        root_code,
        source_fact_id,
        *(str(position) for position in structural_path),
    )


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
class SourceDefinitionOccurrence:
    """One restriction occurrence at a normalized stated-definition path."""

    occurrence_id: str
    root_code: str
    source_fact_id: str
    source_group_id: str
    anchor_code: str
    depth: int
    role_code: str
    filler_code: str
    structural_path: tuple[int, ...]
    member_position: int

    def __post_init__(self) -> None:
        _validate_occurrence_fields(self)
        _validate_occurrence_path(self)
        _validate_occurrence_identity(self)


def _validate_occurrence_fields(occurrence: SourceDefinitionOccurrence) -> None:
    _require_sha256(occurrence.occurrence_id, "occurrence_id")
    _require_code(occurrence.root_code, _CONCEPT_CODE, "root_code")
    _require_sha256(occurrence.source_fact_id, "source_fact_id")
    _require_sha256(occurrence.source_group_id, "source_group_id")
    _require_code(occurrence.anchor_code, _CONCEPT_CODE, "anchor_code")
    _require_code(occurrence.role_code, _ROLE_CODE, "role_code")
    _require_code(occurrence.filler_code, _CONCEPT_CODE, "filler_code")
    if occurrence.depth < 0:
        raise ValueError("depth must be non-negative")


def _validate_occurrence_path(occurrence: SourceDefinitionOccurrence) -> None:
    if not occurrence.structural_path or any(
        item < 0 for item in occurrence.structural_path
    ):
        raise ValueError("structural_path must contain non-negative positions")
    if (
        occurrence.member_position < 0
        or occurrence.structural_path[-1] != occurrence.member_position
    ):
        raise ValueError("member_position must end structural_path")


def _validate_occurrence_identity(occurrence: SourceDefinitionOccurrence) -> None:
    if occurrence.occurrence_id != canonical_source_occurrence_id(
        occurrence.root_code,
        occurrence.source_fact_id,
        occurrence.structural_path,
    ):
        raise ValueError("source occurrence ID is not canonical")


@dataclass(frozen=True, slots=True, kw_only=True)
class CompleteDefinition:
    """Canonical stated definition DAG for one decomposed source concept."""

    root_code: str
    facts: tuple[DefinitionFact, ...]
    groups: tuple[DefinitionGroup, ...] = ()
    root_group_ids: tuple[str, ...] = ()
    occurrences: tuple[SourceDefinitionOccurrence, ...] = ()

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
        _validate_definition_roots(roots, group_by_id, child_group_ids)
        _validate_definition_fact_groups(canonical, group_by_id)
        _validate_canonical_definition_ids(canonical, groups)
        occurrences = _canonical_definition_occurrences(
            self.root_code, self.occurrences, canonical
        )
        object.__setattr__(self, "facts", canonical)
        object.__setattr__(self, "groups", groups)
        object.__setattr__(self, "root_group_ids", roots)
        object.__setattr__(self, "occurrences", occurrences)

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


def _canonical_definition_occurrences(
    root_code: str,
    occurrences: tuple[SourceDefinitionOccurrence, ...],
    facts: tuple[DefinitionFact, ...],
) -> tuple[SourceDefinitionOccurrence, ...]:
    canonical = tuple(
        sorted(occurrences, key=lambda item: (item.anchor_code, item.structural_path))
    )
    if len({item.occurrence_id for item in canonical}) != len(canonical):
        raise ValueError("complete-definition occurrence IDs must be unique")
    if len({(item.anchor_code, item.structural_path) for item in canonical}) != len(
        canonical
    ):
        raise ValueError("complete-definition occurrence paths must be unique")
    _validate_occurrence_semantics(root_code, canonical, facts)
    return canonical


def _validate_occurrence_semantics(
    root_code: str,
    occurrences: tuple[SourceDefinitionOccurrence, ...],
    facts: tuple[DefinitionFact, ...],
) -> None:
    facts_by_id = {fact.fact_id: fact for fact in facts}
    for occurrence in occurrences:
        if occurrence.root_code != root_code:
            raise ValueError(
                "source occurrence root does not match complete definition"
            )
        fact = facts_by_id.get(occurrence.source_fact_id)
        if not isinstance(fact, RestrictionDefinitionFact):
            raise ValueError("source occurrence references an unknown restriction fact")
        if (
            occurrence.source_group_id,
            occurrence.anchor_code,
            occurrence.depth,
            occurrence.role_code,
            occurrence.filler_code,
        ) != (
            fact.group_id,
            fact.anchor_code,
            fact.depth,
            fact.role_code,
            fact.filler_code,
        ):
            raise ValueError("source occurrence does not match its restriction fact")


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
    expected_roots = group_by_id.keys() - child_group_ids
    if not roots and groups:
        roots = tuple(sorted(expected_roots))
    if set(roots) - group_by_id.keys():
        raise ValueError("complete-definition root references an unknown group")
    return roots


def _validate_definition_roots(
    roots: tuple[str, ...],
    group_by_id: dict[str, DefinitionGroup],
    child_group_ids: set[str],
) -> None:
    if set(roots) != group_by_id.keys() - child_group_ids:
        raise ValueError("complete-definition roots must equal the parentless groups")


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


def _fact_member_signature(fact: DefinitionFact) -> str:
    if isinstance(fact, GenusDefinitionFact):
        definition_status = "defined" if fact.is_defined else "primitive"
        return f"genus:{fact.genus_code}:{definition_status}"
    return f"restriction:{fact.role_code}:{fact.filler_code}"


def _expected_definition_fact_id(fact: DefinitionFact) -> str:
    if isinstance(fact, GenusDefinitionFact):
        return canonical_definition_fact_id(
            fact.anchor_code,
            fact.group_id,
            "genus",
            fact.genus_code,
            "defined" if fact.is_defined else "primitive",
        )
    return canonical_definition_fact_id(
        fact.anchor_code,
        fact.group_id,
        "restriction",
        fact.role_code,
        fact.filler_code,
    )


def _expected_definition_group_id(
    group: DefinitionGroup,
    facts: list[DefinitionFact],
) -> str:
    signatures = [_fact_member_signature(fact) for fact in facts]
    signatures.extend(f"group:{child_id}" for child_id in group.child_group_ids)
    return canonical_definition_group_id(group.anchor_code, signatures)


def _validate_canonical_definition_ids(
    facts: tuple[DefinitionFact, ...],
    groups: tuple[DefinitionGroup, ...],
) -> None:
    facts_by_group: dict[str, list[DefinitionFact]] = {}
    for fact in facts:
        facts_by_group.setdefault(fact.group_id, []).append(fact)
        if fact.fact_id != _expected_definition_fact_id(fact):
            raise ValueError("complete-definition fact ID is not canonical")
    for group in groups:
        if group.group_id != _expected_definition_group_id(
            group,
            facts_by_group.get(group.group_id, []),
        ):
            raise ValueError("complete-definition group ID is not canonical")


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
    the DAG walk (populated by PR-B; ``None`` on the flat path). Source IDs bind a
    projected restriction to its canonical stated fact and occurrences when available.
    """

    role_code: str
    filler_code: str
    role_label: str | None = None
    anchoring_genus: str | None = None
    source_definition_ids: tuple[str, ...] = ()
    source_occurrence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("source_definition_ids", "source_occurrence_ids"):
            canonical = tuple(sorted(set(getattr(self, field_name))))
            for source_id in canonical:
                _require_sha256(source_id, f"{field_name} item")
            object.__setattr__(self, field_name, canonical)
        if self.source_occurrence_ids and not self.source_definition_ids:
            raise ValueError("source occurrence IDs require source definition IDs")


@dataclass(frozen=True, slots=True)
class Constituent:
    """A single decomposed constituent: an axis and the concept that fills it.

    ``axis`` is the normalized ``op:`` relation (or an unknown legacy NCIt role);
    ``source_roles`` preserves every defining NCIt role independently. ``most_specific``
    records that the filler was chosen over a strictly broader is-a candidate;
    ``needs_review`` flags an unresolved ordinary axis for curation. ``group`` is a D19
    relationship-group id shared by ambiguous fillers on the same routed axis.
    """

    axis: str
    filler_code: str
    axis_source: AxisSource
    source_roles: tuple[str, ...] = ()
    most_specific: bool = False
    needs_review: bool = False
    group: str | None = None
    source_definition_ids: tuple[str, ...] = ()
    source_occurrence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_code(self.axis, _AXIS_OR_ROLE, "axis")
        _require_code(self.filler_code, _FILLER_CODE, "filler_code")
        source_roles = _canonical_source_roles(
            self.axis,
            self.axis_source,
            self.source_roles,
        )
        _require_source_roles(source_roles)
        object.__setattr__(self, "source_roles", source_roles)
        canonical = tuple(sorted(set(self.source_definition_ids)))
        for source_id in canonical:
            _require_sha256(source_id, "source_definition_ids item")
        object.__setattr__(self, "source_definition_ids", canonical)
        occurrence_ids = tuple(sorted(set(self.source_occurrence_ids)))
        for occurrence_id in occurrence_ids:
            _require_sha256(occurrence_id, "source_occurrence_ids item")
        object.__setattr__(self, "source_occurrence_ids", occurrence_ids)


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """The detector's verdict for one concept."""

    code: str
    is_precoordinated: bool
    defining_role_count: int
    semantic_type: str | None
    label_multi_aspect: bool = False


def _referenced_source_ids(constituents: Sequence[Constituent]) -> set[str]:
    return {
        source_id
        for constituent in constituents
        for source_id in constituent.source_definition_ids
    }


def _validate_referenced_fact(constituent: Constituent, fact: DefinitionFact) -> None:
    if constituent.axis_source == "nlp":
        raise ValueError("NLP constituents cannot reference definition facts")
    if constituent.axis_source == "parent":
        _validate_parent_fact(constituent, fact)
        return
    _validate_role_fact(constituent, fact)


def _validate_parent_fact(constituent: Constituent, fact: DefinitionFact) -> None:
    if not isinstance(fact, GenusDefinitionFact) or (
        fact.genus_code != constituent.filler_code
    ):
        raise ValueError("parent constituent references an unrelated genus fact")


def _validate_role_fact(constituent: Constituent, fact: DefinitionFact) -> None:
    if not isinstance(fact, RestrictionDefinitionFact) or (
        fact.filler_code != constituent.filler_code
    ):
        raise ValueError("role constituent references an unrelated restriction")
    if fact.role_code not in constituent.source_roles:
        raise ValueError("role constituent references a different source role")


def _require_no_definition_references(constituents: Sequence[Constituent]) -> None:
    if _referenced_source_ids(constituents) or any(
        constituent.source_occurrence_ids for constituent in constituents
    ):
        raise ValueError(
            "constituent source-definition references require a complete definition"
        )


def _validate_definition_link(
    code: str,
    constituents: Sequence[Constituent],
    complete_definition: CompleteDefinition | None,
) -> None:
    if complete_definition is None:
        _require_no_definition_references(constituents)
        return
    if complete_definition.root_code != code:
        raise ValueError("complete-definition root does not match decomposition code")
    known = {fact.fact_id: fact for fact in complete_definition.facts}
    unknown = _referenced_source_ids(constituents) - known.keys()
    if unknown:
        raise ValueError(f"unknown complete-definition fact referenced: {min(unknown)}")
    known_occurrences = {
        occurrence.occurrence_id: occurrence
        for occurrence in complete_definition.occurrences
    }
    for constituent in constituents:
        _validate_constituent_definition_links(constituent, known, known_occurrences)


def _validate_constituent_definition_links(
    constituent: Constituent,
    facts: dict[str, DefinitionFact],
    occurrences: dict[str, SourceDefinitionOccurrence],
) -> None:
    for source_id in constituent.source_definition_ids:
        _validate_referenced_fact(constituent, facts[source_id])
    for occurrence_id in constituent.source_occurrence_ids:
        occurrence = occurrences.get(occurrence_id)
        if occurrence is None:
            raise ValueError(f"unknown source occurrence referenced: {occurrence_id}")
        if occurrence.source_fact_id not in constituent.source_definition_ids:
            raise ValueError("source occurrence link requires its semantic fact link")
        _validate_referenced_fact(constituent, facts[occurrence.source_fact_id])


def _validate_axis_cardinality(constituents: Sequence[Constituent]) -> None:
    primary_sites = sum(
        item.axis == "op:PrimarySite" and not item.needs_review for item in constituents
    )
    if primary_sites > 1:
        raise ValueError("resolved op:PrimarySite cardinality is 0..1")


@dataclass(frozen=True, slots=True)
class Decomposition:
    """A decomposed concept: its source code and its constituents (roles-first)."""

    code: str
    semantic_type: str | None
    constituents: Sequence[Constituent] = ()
    complete_definition: CompleteDefinition | None = None

    def __post_init__(self) -> None:
        _require_code(self.code, _CONCEPT_CODE, "code")
        object.__setattr__(self, "constituents", tuple(self.constituents))
        _validate_axis_cardinality(self.constituents)
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
