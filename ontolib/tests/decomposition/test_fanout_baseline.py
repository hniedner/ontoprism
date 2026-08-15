from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontolib.decomposition.fanout_baseline import (
    BASELINE_SCHEMA_VERSION,
    DISCOVERY_ALGORITHM,
    FanoutObservation,
    baseline_identity,
    discovery_query_identity,
    highest_fanout_from_discovery_rows,
    load_fanout_baseline,
    observe_highest_fanout,
)
from ontolib.decomposition.models import (
    CompleteDefinition,
    DefinitionGroup,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
    canonical_definition_fact_id,
    canonical_definition_group_id,
    canonical_source_occurrence_id,
)


def _complete(code: str, facts: int, occurrences: int) -> CompleteDefinition:
    signatures = [f"restriction:R101:C{90000 + index}" for index in range(facts)]
    group_id = canonical_definition_group_id(code, signatures)
    restriction_facts = tuple(
        RestrictionDefinitionFact(
            fact_id=canonical_definition_fact_id(
                code,
                group_id,
                "restriction",
                "R101",
                f"C{90000 + index}",
            ),
            anchor_code=code,
            group_id=group_id,
            depth=0,
            role_code="R101",
            filler_code=f"C{90000 + index}",
        )
        for index in range(facts)
    )
    source_occurrences = tuple(
        SourceDefinitionOccurrence(
            occurrence_id=canonical_source_occurrence_id(
                code,
                restriction_facts[index % facts].fact_id,
                (0, index),
            ),
            root_code=code,
            source_fact_id=restriction_facts[index % facts].fact_id,
            source_group_id=group_id,
            anchor_code=code,
            depth=0,
            role_code="R101",
            filler_code=restriction_facts[index % facts].filler_code,
            structural_path=(0, index),
            member_position=index,
        )
        for index in range(occurrences)
    )
    return CompleteDefinition(
        root_code=code,
        facts=restriction_facts,
        groups=(
            DefinitionGroup(
                group_id=group_id,
                anchor_code=code,
                depth=0,
                child_group_ids=(),
            ),
        ),
        root_group_ids=(group_id,),
        occurrences=source_occurrences,
    )


@pytest.mark.unit
async def test_observer_retains_all_exact_ties_and_occurrence_count() -> None:
    definitions = {
        "C10": _complete("C10", facts=2, occurrences=3),
        "C11": _complete("C11", facts=3, occurrences=4),
        "C12": _complete("C12", facts=3, occurrences=4),
    }

    async def read(code: str) -> CompleteDefinition:
        return definitions[code]

    observation = await observe_highest_fanout(
        ("C10", "C12", "C11"),
        read_definition=read,
    )

    assert observation == FanoutObservation(
        concept_codes=("C11", "C12"),
        restriction_fact_count=3,
        restriction_occurrence_count=4,
        scanned_concept_count=3,
    )


@pytest.mark.unit
async def test_observer_uses_fact_count_to_break_occurrence_tie() -> None:
    definitions = {
        "C10": _complete("C10", facts=2, occurrences=4),
        "C11": _complete("C11", facts=3, occurrences=4),
    }

    async def read(code: str) -> CompleteDefinition:
        return definitions[code]

    observation = await observe_highest_fanout(
        definitions,
        read_definition=read,
    )

    assert observation.concept_codes == ("C11",)
    assert observation.restriction_occurrence_count == 4
    assert observation.restriction_fact_count == 3


@pytest.mark.unit
async def test_observer_names_the_source_concept_that_fails() -> None:
    async def read(_code: str) -> CompleteDefinition:
        raise ValueError("malformed definition")

    with pytest.raises(RuntimeError, match="C10 fanout observation failed"):
        await observe_highest_fanout(("C10",), read_definition=read)


@pytest.mark.unit
def test_baseline_loader_rejects_source_or_discovery_drift(tmp_path: Path) -> None:
    baseline = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "source_identity": "a" * 64,
        "ontology_release": "26.07d",
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "concept_codes": ["C11"],
        "restriction_fact_count": 3,
        "restriction_occurrence_count": 4,
        "scanned_concept_count": 3,
        "discovery_algorithm": DISCOVERY_ALGORITHM,
        "discovery_query_identity": discovery_query_identity(),
        "logical_select_count_budget": 8,
        "select_once_r82_count_budget": 4,
    }
    baseline["baseline_identity"] = baseline_identity(baseline)
    path = tmp_path / "fanout.json"
    path.write_text(json.dumps(baseline))

    loaded = load_fanout_baseline(
        path,
        expected_source_identity="a" * 64,
        expected_release="26.07d",
    )
    assert loaded.concept_codes == ("C11",)

    baseline["source_identity"] = "c" * 64
    baseline["baseline_identity"] = baseline_identity(baseline)
    path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match="source identity"):
        load_fanout_baseline(
            path,
            expected_source_identity="a" * 64,
            expected_release="26.07d",
        )

    baseline["source_identity"] = "a" * 64
    baseline["discovery_algorithm"] = "direct-equivalent-class-v0"
    baseline["baseline_identity"] = baseline_identity(baseline)
    path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match="discovery algorithm"):
        load_fanout_baseline(
            path,
            expected_source_identity="a" * 64,
            expected_release="26.07d",
        )


@pytest.mark.unit
def test_baseline_loader_rejects_measured_value_drift(tmp_path: Path) -> None:
    source = Path(__file__).with_name("golden") / "neoplasm-highest-fanout.json"
    document = json.loads(source.read_text())
    document["logical_select_count_budget"] += 1
    path = tmp_path / "drifted.json"
    path.write_text(json.dumps(document))

    with pytest.raises(ValueError, match="baseline identity"):
        load_fanout_baseline(
            path,
            expected_source_identity=document["source_identity"],
            expected_release=document["ontology_release"],
        )


@pytest.mark.unit
def test_discovery_counts_repeated_and_inherited_restriction_occurrences() -> None:
    rows = [
        {"kind": "restriction", "anchor": "C10", "occurrence": "a"},
        {"kind": "restriction", "anchor": "C10", "occurrence": "b"},
        {"kind": "definedGenus", "anchor": "C10", "genus": "C20"},
        {"kind": "restriction", "anchor": "C20", "occurrence": "c"},
        {"kind": "definedGenus", "anchor": "C11", "genus": "C20"},
    ]

    observation = highest_fanout_from_discovery_rows(("C10", "C11"), rows)

    assert observation.concept_codes == ("C10",)
    assert observation.restriction_occurrence_count == 3
    assert observation.scanned_concept_count == 2


@pytest.mark.unit
def test_discovery_fails_closed_on_defined_genus_cycle() -> None:
    rows = [
        {"kind": "definedGenus", "anchor": "C10", "genus": "C20"},
        {"kind": "definedGenus", "anchor": "C20", "genus": "C10"},
    ]

    with pytest.raises(ValueError, match="cycle"):
        highest_fanout_from_discovery_rows(("C10",), rows)
