"""Axis catalogue: which concepts are in decomposition scope, and which role
restrictions are *defining* axes vs. ``Excludes_*`` negative axioms.

The curated projection routes defining NCIt source roles to univocal ``op:`` axes
(design §4.2). Morphology is carried by the taxonomic parent rather than a role.
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
STAGE_VALUE_AXIS = "op:StageValue"
PRIMARY_SITE_AXIS = "op:PrimarySite"
PRIMARY_SUBSITE_AXIS = "op:PrimarySubsite"

# D20 refinement 1 axis: genus-sense classification (lineage) carved from R101.
ASSOCIATED_LINEAGE_AXIS = "op:AssociatedLineageClassification"
# D20 refinement 2 axis: anatomical region carved from R101 residual.
ASSOCIATED_REGION_AXIS = "op:AssociatedRegion"

# The overloaded primary-site role that both refinements split.
PRIMARY_SITE_ROLE = "R101"

# Genera whose R101 restrictions convey lineage classification rather than literal
# primary site (D17/D20 §6.6, confirmed via C6135 analysis).
LINEAGE_GENERIC_GENERA: frozenset[str] = frozenset(
    {"C3010", "C3809", "C3773", "C215715"}
)

# Fillers inherited by essentially every concept in the applicable hierarchy (policy
# 5.1). Membership is measured as inherited coverage over the complete stated record,
# never as raw assertion frequency and never across roles: a filler may be generic on
# one role and discriminating on another.
#
# v2 removed R108 C36122 (Benign Cellular Infiltrate). It was admitted in v1 on its
# apparent cohort frequency, but that frequency came from R142
# (Disease_Excludes_Finding) assertions on malignant concepts. As a positive R108
# finding it is asserted only on the benign genera C3677, C4776 and C5111, giving 5.6%
# inherited coverage (1 of 18) in the adjudicated cohort against 61-100% for every
# retained entry. It is discriminating for benign neoplasms, so suppressing it deleted a
# true constituent (C4791 Left Atrial Myxoma). Full-corpus impact is confined to the
# benign-neoplasm subtree.
GENERIC_SUPPRESSION_VERSION = "contracted-role-generic-v2"
GENERIC_FILLERS_BY_ROLE: dict[str, frozenset[str]] = {
    "R103": frozenset({"C45714"}),
    "R104": frozenset({"C12578"}),
    "R105": frozenset({"C12917", "C12922", "C36779"}),
    "R108": frozenset({"C36115", "C53596", "C54172"}),
}

# Source-reviewed R126 assertions that have a ratified univocal sense. Every other
# R126 assertion remains raw and review-required; there is deliberately no generic
# associated-disease fallback (Issue #57 adjudication Q5/Q6).
ASSOCIATED_PRIOR_DISEASE: frozenset[tuple[str, str]] = frozenset({("C100051", "C3270")})

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


def is_generic_filler(role_code: str, filler_code: str) -> bool:
    """True when a reviewed source-role filler is non-discriminating in projection."""
    return filler_code in GENERIC_FILLERS_BY_ROLE.get(role_code, ())


# NCIt 26.07d's probabilistic ``May_Have_*`` roles are non-defining and excluded
# from the curated projection. D50's complete definition still retains them.
DROPPED_ROLES: frozenset[str] = frozenset(
    {"R89", "R111", "R112", "R113", "R114", "R115", "R116"}
)


def is_dropped_role(role_code: str) -> bool:
    """True if the role is a probabilistic/optional role per SME (D23)."""
    return role_code in DROPPED_ROLES


def is_defining_role(restriction: RoleRestriction) -> bool:
    """True if the restriction contributes a decomposition axis (i.e. is not a
    negative ``Excludes_*`` axiom AND not a probabilistic/optional role per SME)."""
    return not is_excluded_role(restriction.role_label) and not is_dropped_role(
        restriction.role_code
    )
