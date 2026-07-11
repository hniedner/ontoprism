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

# D23 first-class axis for the staging manual/system (AJCC v6/v7/v8/v9, FIGO, etc.)
STAGE_SYSTEM_AXIS = "op:StageSystem"

# D20 refinement 1 axis: genus-sense classification (lineage) carved from R101.
ASSOCIATED_LINEAGE_AXIS = "op:AssociatedLineageClassification"
# D20 refinement 2 axis: anatomical region carved from R101 residual.
ASSOCIATED_REGION_AXIS = "op:AssociatedRegion"

# The overloaded primary-site role that both refinements split.
PRIMARY_SITE_ROLE = "R101"

# Genera whose R101 restrictions convey lineage classification rather than literal
# primary site (D17/D20 §6.6, confirmed via C6135 analysis).
LINEAGE_GENERIC_GENERA: frozenset[str] = frozenset({"C3010", "C3809", "C3773"})

# Semantic type for literal primary-site fillers (D20 refinement 2).
ORGAN_SEMANTIC_TYPE = "Body Part, Organ, or Organ Component"

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


def is_lineage_generic(genus_code: str | None) -> bool:
    """True when *genus_code* is one of the lineage-generic genera whose R101
    restrictions should route to ``ASSOCIATED_LINEAGE_AXIS``."""
    return genus_code in LINEAGE_GENERIC_GENERA


# D23 SME decision: probabilistic/optional roles (R114 Clinical Finding,
# R115 Cell Origin) are non-defining and dropped from decomposition output.
# ``Has_*`` = defining; ``May_Have_*`` = probabilistic (SME distinction).
DROPPED_ROLES: frozenset[str] = frozenset({"R114", "R115"})


def is_dropped_role(role_code: str) -> bool:
    """True if the role is a probabilistic/optional role per SME (D23)."""
    return role_code in DROPPED_ROLES


def is_defining_role(restriction: RoleRestriction) -> bool:
    """True if the restriction contributes a decomposition axis (i.e. is not a
    negative ``Excludes_*`` axiom AND not a probabilistic/optional role per SME)."""
    return not is_excluded_role(restriction.role_label) and not is_dropped_role(
        restriction.role_code
    )
