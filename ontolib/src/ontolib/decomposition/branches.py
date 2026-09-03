"""Closed decomposition branch catalogue and executable branch contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from ontolib.decomposition import axes

ScopeRoot = Literal["C3262", "C2991"]
ScopeVersion = Literal["stated-genus-subclass-v1"]


class DecompositionBranch(StrEnum):
    """Implemented decomposition kinds accepted at run boundaries."""

    NEOPLASM = "neoplasm"
    DISEASE = "disease"


class DecompositionAlgorithm(StrEnum):
    """Semantically distinct algorithms that a branch may dispatch."""

    AXIS_QUALIFIED = "axis-qualified"


@dataclass(frozen=True)
class BranchSpec:
    """The scope and algorithm selected by one supported branch."""

    root_code: ScopeRoot
    scope_version: ScopeVersion
    semantic_types: tuple[str, ...]
    algorithm: DecompositionAlgorithm
    algorithm_version: str


_SCOPE_VERSION: ScopeVersion = "stated-genus-subclass-v1"
_AXIS_SEMANTIC_TYPES = tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES))
_BRANCH_SPECS = {
    DecompositionBranch.NEOPLASM: BranchSpec(
        root_code="C3262",
        scope_version=_SCOPE_VERSION,
        semantic_types=_AXIS_SEMANTIC_TYPES,
        algorithm=DecompositionAlgorithm.AXIS_QUALIFIED,
        algorithm_version="decomposition-v4",
    ),
    DecompositionBranch.DISEASE: BranchSpec(
        root_code="C2991",
        scope_version=_SCOPE_VERSION,
        semantic_types=_AXIS_SEMANTIC_TYPES,
        algorithm=DecompositionAlgorithm.AXIS_QUALIFIED,
        algorithm_version="decomposition-v4",
    ),
}


def parse_branch(value: DecompositionBranch | str) -> DecompositionBranch:
    """Return a supported branch or fail before any run state can be created."""
    try:
        return DecompositionBranch(value)
    except ValueError:
        supported = ", ".join(branch.value for branch in DecompositionBranch)
        raise ValueError(
            f"unsupported decomposition branch {value!r}; supported: {supported}. "
            "The regimen remains unimplemented."
        ) from None


def branch_spec(branch: DecompositionBranch) -> BranchSpec:
    """Return the executable contract for an already validated branch."""
    return _BRANCH_SPECS[branch]
