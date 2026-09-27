"""Read-only #61 partition of stated occurrences, not a new effective ontology."""

from typing import Literal

from pydantic import Field, computed_field

from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS

DeltaCategory = Literal[
    "projected",
    "represented-through-collapse",
    "not-projected",
    "not-considered",
    "unclassified",
]
RETAINED = frozenset({"retained-routed", "retained-unknown", "retained-policy-veto"})
COLLAPSED = frozenset({"collapsed-is-a", "collapsed-r82", "collapsed-mixed"})
_DECOMPOSITION_ROLES = frozenset(
    role for contract in AXIS_CONTRACTS.values() for role in contract.source_roles
) | {"R176", "R126", "R174"}


class DeltaLink(StrictBoundaryModel):
    axis: str
    filler_code: str


class DeltaOccurrence(StrictBoundaryModel):
    occurrence_id: str
    source_fact_id: str
    source_group_id: str
    anchor_code: str
    depth: int = Field(ge=0)
    walker_max_depth: int = Field(gt=0)
    structural_path: list[int]
    role_code: str
    filler_code: str
    disposition: str | None
    normalized_axis: str | None
    retained_filler: str | None
    target_exists: bool
    links: list[DeltaLink]
    conservation_category: str | None
    conservation_reason: str | None

    @computed_field
    @property
    def category(self) -> DeltaCategory:
        # Same exact-pair semantics as r101_run_conservation; unresolved path
        # evidence does not negate an existing collapsed projection target.
        if self.disposition in RETAINED:
            if any(
                (link.axis, link.filler_code)
                == (self.normalized_axis, self.retained_filler)
                for link in self.links
            ):
                return "projected"
            return "not-projected"
        if self.disposition in COLLAPSED:
            return (
                "represented-through-collapse"
                if self.target_exists
                else "not-projected"
            )
        return self._without_disposition()

    def _without_disposition(self) -> DeltaCategory:
        if self.disposition is not None or self.links:
            return "unclassified"
        states: dict[tuple[str | None, str | None], DeltaCategory] = {
            ("unresolved", "missing-disposition"): "not-projected",
            ("unchanged-unprojected", "concept-not-decomposed"): "not-projected",
            (None, None): (
                "not-projected"
                if self.role_code in _DECOMPOSITION_ROLES
                else "not-considered"
            ),
        }
        return states.get(
            (self.conservation_category, self.conservation_reason), "unclassified"
        )

    @computed_field
    @property
    def reason(self) -> str:
        if self.category == "unclassified":
            return "unclassified"
        if self.category == "not-considered":
            return "stated in NCIt; not part of the decomposition's axes"
        if self.conservation_reason is not None:
            return self.conservation_reason
        if self.disposition is not None:
            return self.disposition
        if self.depth >= self.walker_max_depth:
            return "beyond the walker depth bound (D58)"
        return (
            "dropped by a projection rule (generic filler, held role or inherited "
            "non-core role); this engine version records no per-fact reason"
        )


DELTA_SQL = """
SELECT o.occurrence_id, o.source_fact_id, o.source_group_id, o.anchor_code,
       o.depth, o.structural_path, o.role_code, o.filler_code,
       CAST(run.fingerprint->>'walker_max_depth' AS integer) AS walker_max_depth,
       d.disposition, d.normalized_axis, d.retained_filler,
       r.category AS conservation_category, r.reason AS conservation_reason,
       EXISTS (SELECT 1 FROM decomp_constituent c WHERE c.run_id=o.run_id
         AND c.concept_code=o.concept_code AND c.axis=d.normalized_axis
         AND c.filler_code=d.retained_filler) AS target_exists,
       COALESCE((SELECT jsonb_agg(jsonb_build_object(
           'axis',l.axis,'filler_code',l.filler_code) ORDER BY l.axis,l.filler_code)
         FROM decomp_constituent_occurrence l WHERE l.run_id=o.run_id
         AND l.concept_code=o.concept_code AND l.occurrence_id=o.occurrence_id),
         '[]'::jsonb) AS links
FROM decomp_source_occurrence o
JOIN decomp_run run ON run.id=o.run_id
LEFT JOIN decomp_occurrence_disposition d USING(run_id,concept_code,occurrence_id)
LEFT JOIN decomp_r101_conservation r USING(run_id,concept_code,occurrence_id)
WHERE o.run_id=:run_id AND o.concept_code=:concept_code ORDER BY o.occurrence_id
"""
