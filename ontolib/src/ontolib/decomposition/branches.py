"""Closed decomposition branch catalogue and executable branch contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ontolib.decomposition import axes


class DecompositionBranch(StrEnum):
    """Implemented decomposition kinds accepted at run boundaries."""

    NEOPLASM = "neoplasm"


class DecompositionAlgorithm(StrEnum):
    """Semantically distinct algorithms that a branch may dispatch."""

    AXIS_QUALIFIED = "axis-qualified"


@dataclass(frozen=True)
class BranchSpec:
    """The scope and algorithm selected by one supported branch."""

    semantic_types: tuple[str, ...]
    algorithm: DecompositionAlgorithm
    algorithm_version: str


_BRANCH_SPECS = {
    DecompositionBranch.NEOPLASM: BranchSpec(
        semantic_types=tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES)),
        algorithm=DecompositionAlgorithm.AXIS_QUALIFIED,
        algorithm_version="decomposition-v2",
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
            "The disease label was cosmetic and regimen remains unimplemented."
        ) from None


def branch_spec(branch: DecompositionBranch) -> BranchSpec:
    """Return the executable contract for an already validated branch."""
    return _BRANCH_SPECS[branch]
