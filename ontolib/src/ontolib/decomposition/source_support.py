"""Read-time stated filler support, distinct from validation of engine decisions."""

from typing import Literal

from pydantic import Field, computed_field

from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.decomposition.models import AxisSource

SupportKind = Literal["restriction-backed", "genus-backed", "not-source-backed"]
PolicyChoice = Literal["axis-assignment", "collapse", "grouping"]


class StatedFillerSource(StrictBoundaryModel):
    """A stored stated fact locator; not a claim of normalized-axis equivalence."""

    fact_id: str
    kind: Literal["restriction", "genus"]
    anchor_code: str
    group_id: str
    depth: int = Field(ge=0)
    role_code: str | None
    filler_code: str
    occurrence_id: str | None
    structural_path: list[int]


class ConstituentEvidence(StrictBoundaryModel):
    run_id: str
    concept_code: str
    axis: str
    filler_code: str
    axis_source: AxisSource
    sources: list[StatedFillerSource]
    policy_choices: list[PolicyChoice]

    @computed_field
    @property
    def support(self) -> SupportKind:
        if not self.sources:
            return "not-source-backed"
        if self.sources[0].kind == "restriction":
            return "restriction-backed"
        return "genus-backed"

    @computed_field
    @property
    def inferred_assertions(self) -> list[str]:
        if self.sources:
            return []
        if self.axis_source == "nlp":
            return ["NLP-derived filler has no linked stated assertion"]
        return ["Retained filler has no exact linked stated restriction or genus"]


# Materialize the run/concept slice before consulting the large provenance tables.
# Both branches require exact retained codes: a related or collapsed code is not
# evidence for this filler. Source role/definition lists alone are not occurrences.
SOURCE_SUPPORT_SQL = """
WITH selected AS MATERIALIZED (
    SELECT * FROM decomp_constituent
    WHERE run_id = :run_id AND concept_code = :concept_code
)
SELECT c.run_id, c.concept_code, c.axis, c.filler_code, c.axis_source,
       COALESCE(s.sources, '[]'::jsonb) AS sources,
       array_remove(ARRAY[
           CASE WHEN c.axis LIKE 'op:%' THEN 'axis-assignment' END,
           CASE WHEN c.most_specific OR EXISTS (
               SELECT 1 FROM decomp_occurrence_disposition d
               WHERE d.run_id = c.run_id AND d.concept_code = c.concept_code
                 AND d.normalized_axis = c.axis AND d.retained_filler = c.filler_code
                 AND d.source_filler <> d.retained_filler
           ) THEN 'collapse' END,
           CASE WHEN c.normalized_group_id IS NOT NULL THEN 'grouping' END
       ], NULL) AS policy_choices
FROM selected c
LEFT JOIN LATERAL (
    SELECT jsonb_agg(to_jsonb(facts) ORDER BY fact_id, occurrence_id) AS sources
    FROM (
        SELECT f.fact_id, f.fact_kind AS kind, f.anchor_code, f.group_id, f.depth,
               f.role_code, f.filler_code, o.occurrence_id, o.structural_path
        FROM decomp_constituent_occurrence l
        JOIN decomp_source_occurrence o
          ON o.run_id = l.run_id AND o.concept_code = l.concept_code
         AND o.occurrence_id = l.occurrence_id
        JOIN decomp_definition_fact f
          ON f.run_id = o.run_id AND f.concept_code = o.concept_code
         AND f.fact_id = o.source_fact_id
        WHERE l.run_id = c.run_id AND l.concept_code = c.concept_code
          AND l.axis = c.axis AND l.filler_code = c.filler_code
          AND c.axis_source = 'role' AND f.fact_kind = 'restriction'
          AND f.filler_code = c.filler_code AND o.filler_code = f.filler_code
          AND o.role_code = f.role_code AND o.anchor_code = f.anchor_code
          AND o.source_group_id = f.group_id AND o.depth = f.depth
          AND c.source_roles ? f.role_code AND c.source_definition_ids ? f.fact_id
        UNION ALL
        SELECT f.fact_id, f.fact_kind, f.anchor_code, f.group_id, f.depth,
               f.role_code, f.genus_code, NULL, ARRAY[]::integer[]
        FROM jsonb_array_elements_text(c.source_definition_ids) linked(fact_id)
        JOIN decomp_definition_fact f
          ON f.run_id = c.run_id AND f.concept_code = c.concept_code
         AND f.fact_id = linked.fact_id
        WHERE c.axis_source = 'parent' AND f.fact_kind = 'genus'
          AND f.genus_code = c.filler_code
    ) facts
) s ON true
ORDER BY c.axis, c.filler_code
"""
