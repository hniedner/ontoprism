"""Pure, source-bound classification of NCIt axis range evidence."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS
from ontolib.decomposition.scope import read_scope_hierarchy_edges
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Awaitable, Collection, Mapping, Sequence


class AxisDiagnosticClient(Protocol):
    def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Awaitable[Sequence[Mapping[str, str | None]]]: ...


_AXIS = re.compile(r"op:[A-Za-z][A-Za-z0-9]*")
_CODE = re.compile(r"C[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class AxisDiagnosticError(ValueError):
    """Axis evidence cannot be interpreted without ambiguity."""


def _require(value: str, pattern: re.Pattern[str], name: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid: {value!r}")


@dataclass(frozen=True, slots=True, order=True, kw_only=True)
class HierarchyEdge:
    child: str
    parent: str

    def __post_init__(self) -> None:
        _require(self.child, _CODE, "hierarchy child")
        _require(self.parent, _CODE, "hierarchy parent")


@dataclass(frozen=True, slots=True, order=True, kw_only=True)
class DisjointPair:
    left: str
    right: str

    def __post_init__(self) -> None:
        _require(self.left, _CODE, "disjoint left")
        _require(self.right, _CODE, "disjoint right")
        if self.left == self.right:
            raise AxisDiagnosticError("self-disjoint evidence is malformed")
        if self.left > self.right:
            left, right = self.right, self.left
            object.__setattr__(self, "left", left)
            object.__setattr__(self, "right", right)


def _reject_cycles(edges: tuple[HierarchyEdge, ...]) -> None:
    parents: dict[str, tuple[str, ...]] = {}
    for edge in edges:
        parents[edge.child] = (*parents.get(edge.child, ()), edge.parent)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(code: str) -> None:
        if code in visiting:
            raise AxisDiagnosticError("hierarchy edges contain a cycle")
        if code in visited:
            return
        visiting.add(code)
        for parent in parents.get(code, ()):
            visit(parent)
        visiting.remove(code)
        visited.add(code)

    for code in parents:
        visit(code)


@dataclass(frozen=True, slots=True, kw_only=True)
class AxisHierarchyEvidence:
    source_identity: str
    edges: tuple[HierarchyEdge, ...]
    disjoint_pairs: tuple[DisjointPair, ...]

    def __post_init__(self) -> None:
        _require(self.source_identity, _SHA256, "source_identity")
        edges = tuple(sorted(self.edges))
        if len(edges) != len(set(edges)):
            raise AxisDiagnosticError("duplicate hierarchy edge")
        pairs = tuple(sorted(self.disjoint_pairs))
        if len(pairs) != len(set(pairs)):
            raise AxisDiagnosticError("duplicate disjoint pair")
        _reject_cycles(edges)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "disjoint_pairs", pairs)


ValidReason = Literal["filler-is-range-or-descendant"]
InvalidReason = Literal["disjoint-ancestor-pair"]
UnknownReason = Literal[
    "no-positive-or-negative-proof",
    "contradictory-valid-and-invalid-evidence",
    "unknown-axis",
    "range-does-not-match-axis-contract",
]


def _validate_common(
    status: object,
    expected_status: str,
    axis: str,
    filler_code: str,
    range_code: str,
    source_identity: str,
) -> None:
    if status != expected_status:
        raise ValueError(f"status must be {expected_status!r}")
    _require(axis, _AXIS, "axis")
    _require(filler_code, _CODE, "filler_code")
    _require(range_code, _CODE, "range_code")
    _require(source_identity, _SHA256, "source_identity")


def _validate_path(
    path: tuple[str, ...],
    *,
    start: str,
    end: str | None,
    name: str,
) -> None:
    if not path or path[0] != start or (end is not None and path[-1] != end):
        raise ValueError(f"{name} does not bind its required endpoints")
    if len(set(path)) != len(path):
        raise ValueError(f"{name} contains a cycle")
    for code in path:
        _require(code, _CODE, f"{name} code")


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidAxisEvidence:
    status: Literal["valid"]
    axis: str
    filler_code: str
    range_code: str
    source_identity: str
    reason: ValidReason
    structural_path: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_common(
            self.status,
            "valid",
            self.axis,
            self.filler_code,
            self.range_code,
            self.source_identity,
        )
        _validate_path(
            self.structural_path,
            start=self.filler_code,
            end=self.range_code,
            name="structural_path",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidAxisEvidence:
    status: Literal["invalid"]
    axis: str
    filler_code: str
    range_code: str
    source_identity: str
    reason: InvalidReason
    disjoint_pair: DisjointPair
    filler_ancestor_path: tuple[str, ...]
    range_ancestor_path: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_common(
            self.status,
            "invalid",
            self.axis,
            self.filler_code,
            self.range_code,
            self.source_identity,
        )
        _validate_path(
            self.filler_ancestor_path,
            start=self.filler_code,
            end=None,
            name="filler_ancestor_path",
        )
        _validate_path(
            self.range_ancestor_path,
            start=self.range_code,
            end=None,
            name="range_ancestor_path",
        )
        endpoints = {
            self.filler_ancestor_path[-1],
            self.range_ancestor_path[-1],
        }
        if endpoints != {self.disjoint_pair.left, self.disjoint_pair.right}:
            raise ValueError("disjoint_pair does not bind ancestor paths")


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownAxisEvidence:
    status: Literal["unknown"]
    axis: str
    filler_code: str
    range_code: str
    source_identity: str
    reason: UnknownReason

    def __post_init__(self) -> None:
        _validate_common(
            self.status,
            "unknown",
            self.axis,
            self.filler_code,
            self.range_code,
            self.source_identity,
        )
        allowed = {
            "no-positive-or-negative-proof",
            "contradictory-valid-and-invalid-evidence",
            "unknown-axis",
            "range-does-not-match-axis-contract",
        }
        if self.reason not in allowed:
            raise ValueError("reason is invalid for unknown evidence")


type AxisRangeEvidence = ValidAxisEvidence | InvalidAxisEvidence | UnknownAxisEvidence


class AxisDiagnosticSource:
    """Behavioral adapter over one immutable domain snapshot."""

    def __init__(self, snapshot: AxisHierarchyEvidence) -> None:
        self.snapshot = snapshot

    def classify(self, *, axis: str, filler_code: str) -> AxisRangeEvidence:
        contract = AXIS_CONTRACTS.get(axis)
        range_code = contract.range_code if contract is not None else filler_code
        return classify_axis_range(axis, filler_code, range_code, self.snapshot)


def build_disjoint_pairs_query() -> str:
    """Read binary NCIt disjointness from the certified stated graph."""
    return f"""PREFIX owl: <{OWL_NS}>
SELECT DISTINCT ?left ?right WHERE {{
  GRAPH <{STATED_GRAPH_IRI}> {{
    ?left owl:disjointWith ?right .
    FILTER(isIRI(?left) && isIRI(?right))
    FILTER(STRSTARTS(STR(?left), "{NCIT_NS}"))
    FILTER(STRSTARTS(STR(?right), "{NCIT_NS}"))
  }}
}}
"""


def _row_code(value: str | None, binding: str) -> str:
    if value is None or not value.startswith(NCIT_NS):
        raise AxisDiagnosticError(f"disjoint row has invalid {binding} NCIt IRI")
    code = value.removeprefix(NCIT_NS)
    try:
        _require(code, _CODE, f"disjoint {binding}")
    except ValueError as error:
        raise AxisDiagnosticError(str(error)) from error
    return code


def disjoint_pairs_from_rows(
    rows: Sequence[Mapping[str, str | None]],
) -> tuple[DisjointPair, ...]:
    """Parse every row strictly; missing and duplicate evidence fail closed."""
    pairs = tuple(
        DisjointPair(
            left=_row_code(row.get("left"), "left"),
            right=_row_code(row.get("right"), "right"),
        )
        for row in rows
    )
    if len(pairs) != len(set(pairs)):
        raise AxisDiagnosticError("duplicate disjoint pair")
    return tuple(sorted(pairs))


async def read_axis_diagnostic_source(
    client: AxisDiagnosticClient,
    source_identity: str,
) -> AxisDiagnosticSource:
    """Read one fixed hierarchy/disjointness snapshot for all classifications."""
    scope_edges = await read_scope_hierarchy_edges(client)
    rows = await client.select_once(
        build_disjoint_pairs_query(),
        required_variables={"left", "right"},
    )
    return AxisDiagnosticSource(
        snapshot=AxisHierarchyEvidence(
            source_identity=source_identity,
            edges=tuple(
                HierarchyEdge(child=edge.child, parent=edge.parent)
                for edge in scope_edges
            ),
            disjoint_pairs=disjoint_pairs_from_rows(rows),
        )
    )


def _ancestor_paths(
    code: str, edges: tuple[HierarchyEdge, ...]
) -> dict[str, tuple[str, ...]]:
    parents: dict[str, list[str]] = {}
    for edge in edges:
        parents.setdefault(edge.child, []).append(edge.parent)
    paths: dict[str, tuple[str, ...]] = {code: (code,)}
    queue: deque[tuple[str, ...]] = deque([(code,)])
    while queue:
        path = queue.popleft()
        for parent in sorted(parents.get(path[-1], ())):
            candidate = (*path, parent)
            previous = paths.get(parent)
            if previous is None or (len(candidate), candidate) < (
                len(previous),
                previous,
            ):
                paths[parent] = candidate
                queue.append(candidate)
    return paths


def _negative_evidence(
    filler_paths: dict[str, tuple[str, ...]],
    range_paths: dict[str, tuple[str, ...]],
    pairs: tuple[DisjointPair, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], DisjointPair] | None:
    candidates = [
        (filler_paths[left], range_paths[right], pair)
        for pair in pairs
        for left, right in ((pair.left, pair.right), (pair.right, pair.left))
        if left in filler_paths and right in range_paths
    ]
    return min(
        candidates,
        key=lambda item: (len(item[0]) + len(item[1]), item),
        default=None,
    )


def classify_axis_range(
    axis: str,
    filler_code: str,
    range_code: str,
    snapshot: AxisHierarchyEvidence,
) -> AxisRangeEvidence:
    """Classify an axis filler using only explicit source-bound structural facts."""
    _require(axis, _AXIS, "axis")
    _require(filler_code, _CODE, "filler_code")
    _require(range_code, _CODE, "range_code")
    contract = AXIS_CONTRACTS.get(axis)
    common = {
        "axis": axis,
        "filler_code": filler_code,
        "range_code": range_code,
        "source_identity": snapshot.source_identity,
    }
    if contract is None:
        return UnknownAxisEvidence(status="unknown", reason="unknown-axis", **common)
    if range_code != contract.range_code:
        return UnknownAxisEvidence(
            status="unknown",
            reason="range-does-not-match-axis-contract",
            **(common | {"range_code": contract.range_code}),
        )

    filler_paths = _ancestor_paths(filler_code, snapshot.edges)
    range_paths = _ancestor_paths(range_code, snapshot.edges)
    valid_path = filler_paths.get(range_code)
    negative = _negative_evidence(filler_paths, range_paths, snapshot.disjoint_pairs)
    if valid_path is not None and negative is not None:
        return UnknownAxisEvidence(
            status="unknown",
            reason="contradictory-valid-and-invalid-evidence",
            **common,
        )
    if valid_path is not None:
        return ValidAxisEvidence(
            status="valid",
            reason="filler-is-range-or-descendant",
            structural_path=valid_path,
            **common,
        )
    if negative is not None:
        filler_path, range_path, pair = negative
        return InvalidAxisEvidence(
            status="invalid",
            reason="disjoint-ancestor-pair",
            disjoint_pair=pair,
            filler_ancestor_path=filler_path,
            range_ancestor_path=range_path,
            **common,
        )
    return UnknownAxisEvidence(
        status="unknown", reason="no-positive-or-negative-proof", **common
    )
