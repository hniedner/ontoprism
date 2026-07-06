"""Axis catalogue: which concepts are in decomposition scope, and which role
restrictions are *defining* axes vs. ``Excludes_*`` negative axioms.

The engine reuses the NCIt role code itself as the axis identifier (design §4.2), so
this module classifies roles rather than renaming them. Morphology is the exception —
it is carried by the taxonomic parent, not a role — so it gets an ``op:`` axis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ontolib.decomposition.models import RoleRestriction

# The disease/neoplasm families where pre-coordination drives concept explosion
# (design §2 / assessment §3.3). The molecular-biology families (Gene, Protein, …) are
# deliberately excluded — their roles express biology, not label-level aggregation.
IN_SCOPE_SEMANTIC_TYPES = frozenset(
    {
        "Neoplastic Process",
        "Disease or Syndrome",
        "Cell or Molecular Dysfunction",
    }
)

# Morphology is not a role filler; it is derived from the taxonomic parent (design §6).
MORPHOLOGY_AXIS = "op:Morphology"

# NCIt encodes disjointness as ``*_Excludes_*`` restrictions (e.g.
# ``Disease_Excludes_Abnormal_Cell``). These are negative axioms, not constituents, and
# must never be counted as defining axes (assessment §4.2).
_EXCLUDES_MARKER = "Excludes"


def is_in_scope(semantic_type: str | None) -> bool:
    """True if a concept's semantic type is a decomposition target family."""
    return semantic_type in IN_SCOPE_SEMANTIC_TYPES


def is_excluded_role(role_label: str | None) -> bool:
    """True if the role is an ``*_Excludes_*`` negative axiom (not a constituent)."""
    return role_label is not None and _EXCLUDES_MARKER in role_label


def is_defining_role(restriction: RoleRestriction) -> bool:
    """True if the restriction contributes a decomposition axis (i.e. is not a
    negative ``Excludes_*`` axiom)."""
    return not is_excluded_role(restriction.role_label)
