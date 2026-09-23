from __future__ import annotations

from itertools import pairwise

import pytest
from pydantic import ValidationError

from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    DefinitionGroup,
    OccurrenceDisposition,
    ResolvedR82PathEdge,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
    canonical_definition_fact_id,
    canonical_definition_group_id,
    canonical_source_occurrence_id,
)
from ontolib.decomposition.r101_run_conservation import (
    R101ConservationCounts,
    R101ConservationOccurrence,
    R101RunConservation,
    classify_r101_conservation,
)

pytestmark = pytest.mark.unit


def test_a_dropped_r101_occurrence_is_counted_as_unresolved() -> None:
    code = "C1"
    group_id = canonical_definition_group_id(code, ("restriction:R101:C2",))
    fact_id = canonical_definition_fact_id(code, group_id, "restriction", "R101", "C2")
    occurrence_id = canonical_source_occurrence_id(code, fact_id, (0, 1))
    definition = CompleteDefinition(
        root_code=code,
        groups=(DefinitionGroup(group_id=group_id, anchor_code=code, depth=0),),
        root_group_ids=(group_id,),
        facts=(
            RestrictionDefinitionFact(
                fact_id=fact_id,
                anchor_code=code,
                group_id=group_id,
                depth=0,
                role_code="R101",
                filler_code="C2",
            ),
        ),
        occurrences=(
            SourceDefinitionOccurrence(
                occurrence_id=occurrence_id,
                root_code=code,
                source_fact_id=fact_id,
                source_group_id=group_id,
                anchor_code=code,
                depth=0,
                role_code="R101",
                filler_code="C2",
                structural_path=(0, 1),
                member_position=1,
            ),
        ),
    )

    conservation = classify_r101_conservation(
        definition=definition,
        constituents=(),
        dispositions=(),
    )

    assert conservation.counts.model_dump() == {
        "total": 1,
        "projected": 0,
        "unchanged_unprojected": 0,
        "one_step_r82": 0,
        "closure_only_r82": 0,
        "unresolved": 1,
        "explained_unresolved": 0,
    }
    assert tuple(
        item.model_dump(exclude={"category", "r82_path"})
        for item in conservation.unresolved_occurrences
    ) == (
        {
            "concept_code": code,
            "occurrence_id": occurrence_id,
            "source_fact_id": fact_id,
            "source_filler": "C2",
            "reason": "missing-disposition",
            "explanation": None,
        },
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path_length", "category"),
    [(1, "one-step-r82"), (2, "closure-only-r82")],
)
def test_r82_collapses_keep_one_step_and_closure_only_categories_distinct(
    path_length: int, category: str
) -> None:
    code = "C1"
    group_id = canonical_definition_group_id(code, ("restriction:R101:C9",))
    fact_id = canonical_definition_fact_id(code, group_id, "restriction", "R101", "C9")
    occurrence_id = canonical_source_occurrence_id(code, fact_id, (0, 1))
    occurrence = SourceDefinitionOccurrence(
        occurrence_id=occurrence_id,
        root_code=code,
        source_fact_id=fact_id,
        source_group_id=group_id,
        anchor_code=code,
        depth=0,
        role_code="R101",
        filler_code="C9",
        structural_path=(0, 1),
        member_position=1,
    )
    definition = CompleteDefinition(
        root_code=code,
        groups=(DefinitionGroup(group_id=group_id, anchor_code=code, depth=0),),
        root_group_ids=(group_id,),
        facts=(
            RestrictionDefinitionFact(
                fact_id=fact_id,
                anchor_code=code,
                group_id=group_id,
                depth=0,
                role_code="R101",
                filler_code="C9",
            ),
        ),
        occurrences=(occurrence,),
    )
    nodes = ("C2", "C5", "C9") if path_length == 2 else ("C2", "C9")
    path = tuple(
        ResolvedR82PathEdge(
            part_code=part,
            asserted_part_code=part,
            whole_code=whole,
            restriction_node_id=f"urn:r82:{part}:{whole}",
            fact_identity="a" * 64,
            source_identity="b" * 64,
        )
        for part, whole in pairwise(nodes)
    )
    disposition = OccurrenceDisposition(
        kind="collapsed-r82",
        source_occurrence_id=occurrence_id,
        source_fact_id=fact_id,
        normalized_axis="op:PrimarySite",
        source_filler="C9",
        retained_filler="C2",
        semantic_route="p106-organ",
        semantic_type="Body Part, Organ, or Organ Component",
        r82_part="C2",
        r82_whole="C9",
        r82_path=path,
    )
    constituent = Constituent(
        axis="op:PrimarySite",
        filler_code="C2",
        axis_source="role",
        source_roles=("R101",),
        source_definition_ids=(fact_id,),
        source_occurrence_ids=(occurrence_id,),
    )

    conservation = classify_r101_conservation(
        definition=definition,
        constituents=(constituent,),
        dispositions=(disposition,),
    )

    assert conservation.occurrences[0].category == category


@pytest.mark.unit
def test_collapsed_occurrences_use_the_concept_projection_not_direct_links() -> None:
    code = "C1"
    occurrences = []
    facts = []
    dispositions = []
    for index in range(11):
        source_filler = f"C{100 + index}"
        group_id = canonical_definition_group_id(
            code, (f"restriction:R101:{source_filler}",)
        )
        fact_id = canonical_definition_fact_id(
            code, group_id, "restriction", "R101", source_filler
        )
        occurrence_id = canonical_source_occurrence_id(code, fact_id, (index,))
        occurrences.append(
            SourceDefinitionOccurrence(
                occurrence_id=occurrence_id,
                root_code=code,
                source_fact_id=fact_id,
                source_group_id=group_id,
                anchor_code=code,
                depth=0,
                role_code="R101",
                filler_code=source_filler,
                structural_path=(index,),
                member_position=index,
            )
        )
        facts.append(
            RestrictionDefinitionFact(
                fact_id=fact_id,
                anchor_code=code,
                group_id=group_id,
                depth=0,
                role_code="R101",
                filler_code=source_filler,
            )
        )
        dispositions.append(
            OccurrenceDisposition(
                kind="collapsed-r82",
                source_occurrence_id=occurrence_id,
                source_fact_id=fact_id,
                normalized_axis="op:PrimarySite",
                source_filler=source_filler,
                retained_filler="C2",
                semantic_route="p106-organ",
                semantic_type="Body Part, Organ, or Organ Component",
                r82_part="C2",
                r82_whole=source_filler,
                r82_path=(
                    ResolvedR82PathEdge(
                        part_code="C2",
                        asserted_part_code="C2",
                        whole_code=source_filler,
                        restriction_node_id=f"urn:r82:C2:{source_filler}",
                        fact_identity="a" * 64,
                        source_identity="b" * 64,
                    ),
                ),
            )
        )
    groups = tuple(
        DefinitionGroup(
            group_id=occurrence.source_group_id,
            anchor_code=code,
            depth=0,
        )
        for occurrence in occurrences
    )
    definition = CompleteDefinition(
        root_code=code,
        groups=groups,
        root_group_ids=tuple(group.group_id for group in groups),
        facts=tuple(facts),
        occurrences=tuple(occurrences),
    )
    retained = Constituent(
        axis="op:PrimarySite",
        filler_code="C2",
        axis_source="role",
        source_roles=("R101",),
    )

    conservation = classify_r101_conservation(
        definition=definition,
        constituents=(retained,),
        dispositions=tuple(dispositions),
    )

    assert conservation.counts.one_step_r82 == 11
    assert conservation.counts.unresolved == 0


@pytest.mark.unit
def test_conservation_models_reject_inconsistent_counts_and_evidence() -> None:
    with pytest.raises(ValidationError, match="do not sum"):
        R101ConservationCounts(
            total=1,
            projected=0,
            unchanged_unprojected=0,
            one_step_r82=0,
            closure_only_r82=0,
            unresolved=0,
            explained_unresolved=0,
        )
    with pytest.raises(ValidationError, match="explained unresolved"):
        R101ConservationCounts(
            total=1,
            projected=0,
            unchanged_unprojected=0,
            one_step_r82=0,
            closure_only_r82=0,
            unresolved=1,
            explained_unresolved=2,
        )
    with pytest.raises(ValidationError, match="invalid R82 path"):
        R101ConservationOccurrence(
            concept_code="C1",
            occurrence_id="a" * 64,
            source_fact_id="b" * 64,
            source_filler="C2",
            category="one-step-r82",
            reason="missing path",
        )
    with pytest.raises(ValidationError, match="only unresolved"):
        R101ConservationOccurrence(
            concept_code="C1",
            occurrence_id="a" * 64,
            source_fact_id="b" * 64,
            source_filler="C2",
            category="projected",
            reason="retained-routed",
            explanation="not allowed",
        )
    counts = R101ConservationCounts(
        total=0,
        projected=0,
        unchanged_unprojected=0,
        one_step_r82=0,
        closure_only_r82=0,
        unresolved=0,
        explained_unresolved=0,
    )
    with pytest.raises(ValidationError, match="rows differ"):
        R101RunConservation(
            counts=counts,
            occurrences=(
                R101ConservationOccurrence(
                    concept_code="C1",
                    occurrence_id="a" * 64,
                    source_fact_id="b" * 64,
                    source_filler="C2",
                    category="unresolved",
                    reason="missing-disposition",
                ),
            ),
        )


@pytest.mark.unit
def test_missing_or_invalid_r82_evidence_remains_unresolved() -> None:
    code = "C1"
    group_id = canonical_definition_group_id(code, ("restriction:R101:C9",))
    fact_id = canonical_definition_fact_id(code, group_id, "restriction", "R101", "C9")
    occurrence_id = canonical_source_occurrence_id(code, fact_id, (0,))
    occurrence = SourceDefinitionOccurrence(
        occurrence_id=occurrence_id,
        root_code=code,
        source_fact_id=fact_id,
        source_group_id=group_id,
        anchor_code=code,
        depth=0,
        role_code="R101",
        filler_code="C9",
        structural_path=(0,),
        member_position=0,
    )
    definition = CompleteDefinition(
        root_code=code,
        groups=(DefinitionGroup(group_id=group_id, anchor_code=code, depth=0),),
        root_group_ids=(group_id,),
        facts=(
            RestrictionDefinitionFact(
                fact_id=fact_id,
                anchor_code=code,
                group_id=group_id,
                depth=0,
                role_code="R101",
                filler_code="C9",
            ),
        ),
        occurrences=(occurrence,),
    )
    retained = Constituent(
        axis="op:PrimarySite",
        filler_code="C2",
        axis_source="role",
        source_roles=("R101",),
    )

    def disposition(
        *, path: tuple[ResolvedR82PathEdge, ...] = ()
    ) -> OccurrenceDisposition:
        return OccurrenceDisposition(
            kind="collapsed-r82",
            source_occurrence_id=occurrence_id,
            source_fact_id=fact_id,
            normalized_axis="op:PrimarySite",
            source_filler="C9",
            retained_filler="C2",
            semantic_route="p106-organ",
            semantic_type="Body Part, Organ, or Organ Component",
            r82_part="C2",
            r82_whole="C9",
            r82_path=path,
        )

    without_path = classify_r101_conservation(
        definition=definition,
        constituents=(retained,),
        dispositions=(disposition(),),
    )
    wrong_path = (
        ResolvedR82PathEdge(
            part_code="C2",
            asserted_part_code="C2",
            whole_code="C8",
            restriction_node_id="urn:r82:C2:C8",
            fact_identity="a" * 64,
            source_identity="b" * 64,
        ),
    )
    invalid_path = classify_r101_conservation(
        definition=definition,
        constituents=(retained,),
        dispositions=(disposition(path=wrong_path),),
    )
    missing_target = classify_r101_conservation(
        definition=definition,
        constituents=(),
        dispositions=(disposition(path=wrong_path),),
    )

    assert without_path.unresolved_occurrences[0].reason == "missing-r82-path"
    assert invalid_path.unresolved_occurrences[0].reason == "missing-r82-path"
    assert missing_target.unresolved_occurrences[0].reason == "r82-target-not-projected"


@pytest.mark.unit
def test_retained_occurrence_without_its_projection_is_unresolved() -> None:
    code = "C1"
    group_id = canonical_definition_group_id(code, ("restriction:R101:C2",))
    fact_id = canonical_definition_fact_id(code, group_id, "restriction", "R101", "C2")
    occurrence_id = canonical_source_occurrence_id(code, fact_id, (0,))
    definition = CompleteDefinition(
        root_code=code,
        groups=(DefinitionGroup(group_id=group_id, anchor_code=code, depth=0),),
        root_group_ids=(group_id,),
        facts=(
            RestrictionDefinitionFact(
                fact_id=fact_id,
                anchor_code=code,
                group_id=group_id,
                depth=0,
                role_code="R101",
                filler_code="C2",
            ),
        ),
        occurrences=(
            SourceDefinitionOccurrence(
                occurrence_id=occurrence_id,
                root_code=code,
                source_fact_id=fact_id,
                source_group_id=group_id,
                anchor_code=code,
                depth=0,
                role_code="R101",
                filler_code="C2",
                structural_path=(0,),
                member_position=0,
            ),
        ),
    )
    retained = OccurrenceDisposition(
        kind="retained-routed",
        source_occurrence_id=occurrence_id,
        source_fact_id=fact_id,
        normalized_axis="op:PrimarySite",
        source_filler="C2",
        retained_filler="C2",
        semantic_route="p106-organ",
        semantic_type="Body Part, Organ, or Organ Component",
    )

    conservation = classify_r101_conservation(
        definition=definition,
        constituents=(),
        dispositions=(retained,),
    )

    assert (
        conservation.unresolved_occurrences[0].reason == "retained-without-projection"
    )
