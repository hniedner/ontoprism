from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontolib.decomposition.mixed_chain_inventory import (
    MixedChainCandidate,
    MixedChainPathEdge,
    PersistedSelectorOccurrence,
    load_mixed_chain_inventory,
)
from ontolib.decomposition.mixed_chain_projection import (
    MixedChainCandidateProjection,
    MixedChainCorrectedProjection,
    create_corrected_projection,
    load_corrected_projection,
    project_mixed_chain_candidate,
)
from ontolib.decomposition.models import (
    Constituent,
    OccurrenceDisposition,
    SpecificityPathEdge,
)
from ontolib.decomposition.r101_conservation import load_r101_conservation_report

_SOURCE = "a" * 64
_BROAD_OCCURRENCE = "b" * 64
_TERMINAL_OCCURRENCE = "c" * 64
_BROAD_FACT = "d" * 64
_TERMINAL_FACT = "e" * 64


def _occurrence(
    *, occurrence_id: str, fact_id: str, filler: str
) -> PersistedSelectorOccurrence:
    return PersistedSelectorOccurrence(
        concept_code="C100",
        source_occurrence_id=occurrence_id,
        source_fact_id=fact_id,
        source_role="R100",
        anchoring_genus="C200",
        normalized_axis="op:PrimarySite",
        source_filler=filler,
        semantic_route="p106-organ",
        semantic_type="Neoplastic Process",
    )


@pytest.mark.unit
def test_projection_replaces_only_the_exact_mixed_chain_axis_outcome() -> None:
    path = (
        MixedChainPathEdge(
            kind="is-a",
            broader_code="C300",
            narrower_code="C301",
            source_identity=_SOURCE,
        ),
        MixedChainPathEdge(
            kind="r82",
            broader_code="C301",
            narrower_code="C302",
            source_identity=_SOURCE,
        ),
    )
    candidate = MixedChainCandidate(
        concept_code="C100",
        axis="op:PrimarySite",
        source_role="R100",
        broad_filler="C300",
        terminal_filler="C302",
        source_occurrence_ids=(_BROAD_OCCURRENCE,),
        specificity_path=path,
    )
    occurrences = (
        _occurrence(
            occurrence_id=_BROAD_OCCURRENCE,
            fact_id=_BROAD_FACT,
            filler="C300",
        ),
        _occurrence(
            occurrence_id=_TERMINAL_OCCURRENCE,
            fact_id=_TERMINAL_FACT,
            filler="C302",
        ),
    )
    before_constituents = (
        Constituent(
            axis="op:PrimarySite",
            filler_code="C300",
            axis_source="role",
            source_roles=("R100",),
            needs_review=True,
            group="op:PrimarySite",
            source_definition_ids=(_BROAD_FACT,),
            source_occurrence_ids=(_BROAD_OCCURRENCE,),
        ),
        Constituent(
            axis="op:PrimarySite",
            filler_code="C302",
            axis_source="role",
            source_roles=("R100",),
            needs_review=True,
            group="op:PrimarySite",
            source_definition_ids=(_TERMINAL_FACT,),
            source_occurrence_ids=(_TERMINAL_OCCURRENCE,),
        ),
        Constituent(
            axis="op:Morphology",
            filler_code="C400",
            axis_source="role",
            source_roles=("R101",),
        ),
    )
    before_dispositions = (
        OccurrenceDisposition(
            kind="retained-routed",
            source_occurrence_id=_BROAD_OCCURRENCE,
            source_fact_id=_BROAD_FACT,
            normalized_axis="op:PrimarySite",
            source_filler="C300",
            retained_filler="C300",
            semantic_route="p106-organ",
            semantic_type="Neoplastic Process",
        ),
        OccurrenceDisposition(
            kind="retained-routed",
            source_occurrence_id=_TERMINAL_OCCURRENCE,
            source_fact_id=_TERMINAL_FACT,
            normalized_axis="op:PrimarySite",
            source_filler="C302",
            retained_filler="C302",
            semantic_route="p106-organ",
            semantic_type="Neoplastic Process",
        ),
    )

    projection = project_mixed_chain_candidate(
        candidate=candidate,
        occurrences=occurrences,
        before_constituents=before_constituents,
        before_dispositions=before_dispositions,
        source_identity=_SOURCE,
    )

    assert projection.after_constituents == (
        Constituent(
            axis="op:Morphology",
            filler_code="C400",
            axis_source="role",
            source_roles=("R101",),
        ),
        Constituent(
            axis="op:PrimarySite",
            filler_code="C302",
            axis_source="role",
            source_roles=("R100",),
            most_specific=True,
            source_definition_ids=(_TERMINAL_FACT,),
            source_occurrence_ids=(_TERMINAL_OCCURRENCE,),
        ),
    )
    changed = {row.source_occurrence_id: row for row in projection.after_dispositions}
    assert changed[_BROAD_OCCURRENCE] == OccurrenceDisposition(
        kind="collapsed-mixed",
        source_occurrence_id=_BROAD_OCCURRENCE,
        source_fact_id=_BROAD_FACT,
        normalized_axis="op:PrimarySite",
        source_filler="C300",
        retained_filler="C302",
        semantic_route="p106-organ",
        semantic_type="Neoplastic Process",
        specificity_path=tuple(
            SpecificityPathEdge(
                kind=edge.kind,
                broader_code=edge.broader_code,
                narrower_code=edge.narrower_code,
                source_identity=edge.source_identity,
            )
            for edge in path
        ),
    )
    assert changed[_TERMINAL_OCCURRENCE] == before_dispositions[1]
    assert projection.constituent_transition_counts == {
        "added": 0,
        "removed": 1,
        "metadata-changed": 1,
    }

    artifact = create_corrected_projection(
        source_run_id="neoplasm-11111111-1111-1111-1111-111111111111",
        source_report_identity="1" * 64,
        source_identity=_SOURCE,
        selector_identity="2" * 64,
        inventory_identity="3" * 64,
        expected_candidate_codes=("C100",),
        projections=(projection,),
    )

    assert (
        MixedChainCorrectedProjection.model_validate_json(artifact.model_dump_json())
        == artifact
    )
    assert artifact.constituent_transition_counts.model_dump() == {
        "added": 0,
        "removed": 1,
        "metadata_changed": 1,
    }
    assert artifact.disposition_transition_count == 1
    assert artifact.metadata_transition_counts.model_dump() == {
        "most_specific_false_to_true": 1,
        "most_specific_true_to_false": 0,
        "needs_review_false_to_true": 0,
        "needs_review_true_to_false": 1,
        "group_changed": 1,
    }
    assert artifact.projections[0].constituent_transitions[1].changed_fields == (
        "group",
        "most_specific",
        "needs_review",
    )


@pytest.mark.unit
def test_tracked_projection_covers_the_exact_structural_inventory() -> None:
    inventory = load_mixed_chain_inventory(
        Path(
            "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
        )
    )
    report = load_r101_conservation_report(
        Path("ontolib/tests/decomposition/golden/neoplasm-r101-v5-conservation.json.gz")
    )
    artifact = load_corrected_projection(
        Path(
            "ontolib/tests/decomposition/golden/"
            "neoplasm-r101-v5-corrected-projection.json"
        )
    )

    assert artifact.projection_identity == (
        "9df530273eead6b10d4f78df875999076bf2a2fd974a5aa2718f2ebe87c522a6"
    )
    assert artifact.inventory_identity == inventory.identity
    assert artifact.source_report_identity == report.report_identity
    assert artifact.candidate_codes == inventory.candidate_codes
    assert artifact.constituent_transition_counts.model_dump() == {
        "added": 0,
        "removed": 39,
        "metadata_changed": 42,
    }
    assert artifact.metadata_transition_counts.model_dump() == {
        "most_specific_false_to_true": 0,
        "most_specific_true_to_false": 0,
        "needs_review_false_to_true": 0,
        "needs_review_true_to_false": 36,
        "group_changed": 6,
    }
    removed = {
        (projection.concept_code, transition.axis, transition.filler_code)
        for projection in artifact.projections
        for transition in projection.constituent_transitions
        if transition.kind == "removed"
    }
    report_structural = {
        (row.concept_code, row.axis, row.filler_code)
        for row in report.non_r101_delta_evidence.rows
    }
    assert removed == report_structural
    assert artifact.disposition_transition_count == 39
    for candidate, projection in zip(
        inventory.candidates, artifact.projections, strict=True
    ):
        before_keys = {
            (row.axis, row.filler_code) for row in projection.before_constituents
        }
        after_keys = {
            (row.axis, row.filler_code) for row in projection.after_constituents
        }
        assert (candidate.axis, candidate.broad_filler) in before_keys
        assert (candidate.axis, candidate.broad_filler) not in after_keys
        assert (candidate.axis, candidate.terminal_filler) in after_keys


@pytest.mark.unit
def test_projection_rejects_missing_candidate_occurrences() -> None:
    inventory = load_mixed_chain_inventory(
        Path(
            "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
        )
    )

    with pytest.raises(ValueError, match="candidate occurrences differ"):
        project_mixed_chain_candidate(
            candidate=inventory.candidates[0],
            occurrences=(),
            before_constituents=(),
            before_dispositions=(),
            source_identity=inventory.source_identity,
        )


@pytest.mark.unit
def test_projection_rejects_a_path_from_another_source() -> None:
    inventory = load_mixed_chain_inventory(
        Path(
            "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
        )
    )

    with pytest.raises(ValueError, match="path source identity differs"):
        project_mixed_chain_candidate(
            candidate=inventory.candidates[0],
            occurrences=(),
            before_constituents=(),
            before_dispositions=(),
            source_identity="0" * 64,
        )


@pytest.mark.unit
def test_projection_rejects_incomplete_expected_candidate_coverage() -> None:
    with pytest.raises(ValueError, match="exact expected candidate codes"):
        create_corrected_projection(
            source_run_id="neoplasm-11111111-1111-1111-1111-111111111111",
            source_report_identity="1" * 64,
            source_identity=_SOURCE,
            selector_identity="2" * 64,
            inventory_identity="3" * 64,
            expected_candidate_codes=("C100",),
            projections=(),
        )


@pytest.mark.unit
def test_projection_classifies_an_added_constituent() -> None:
    projection = MixedChainCandidateProjection(
        concept_code="C100",
        before_constituents=(),
        after_constituents=(
            Constituent(
                axis="op:Morphology",
                filler_code="C400",
                axis_source="role",
                source_roles=("R101",),
            ),
        ),
        before_dispositions=(),
        after_dispositions=(),
    )

    artifact = create_corrected_projection(
        source_run_id="neoplasm-11111111-1111-1111-1111-111111111111",
        source_report_identity="1" * 64,
        source_identity=_SOURCE,
        selector_identity="2" * 64,
        inventory_identity="3" * 64,
        expected_candidate_codes=("C100",),
        projections=(projection,),
    )

    assert artifact.constituent_transition_counts.model_dump() == {
        "added": 1,
        "removed": 0,
        "metadata_changed": 0,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_codes", ["C2", "C1"], "candidate codes are not canonical"),
        ("candidate_count", 38, "candidate count differs"),
        ("projections", [], "does not cover every candidate"),
        ("projection_identity", "0" * 64, "projection identity differs"),
    ],
)
def test_tracked_projection_rejects_corrupt_identity_or_coverage(
    field: str, value: object, message: str
) -> None:
    path = Path(
        "ontolib/tests/decomposition/golden/neoplasm-r101-v5-corrected-projection.json"
    )
    payload = json.loads(path.read_text())
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        MixedChainCorrectedProjection.model_validate_json(json.dumps(payload))
