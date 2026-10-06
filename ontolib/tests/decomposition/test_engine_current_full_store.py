from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from scripts.decompose import _make_label_lookup
from scripts.research.current_evidence import (
    CurrentConceptEvidence,
    CurrentEngineEvidence,
    _concepts,
)

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.axes import ASSOCIATED_PRIOR_DISEASE, MORPHOLOGY_AXIS
from ontolib.decomposition.axis_diagnostics import read_axis_diagnostic_source
from ontolib.decomposition.collapse_policy import (
    NO_COLLAPSE_VETO_POLICY,
    load_packaged_collapse_veto_policy,
)
from ontolib.decomposition.complete_definition import read_complete_definition
from ontolib.decomposition.morphology_qualifier_policy import (
    MORPHOLOGY_QUALIFIER_CODES,
)
from ontolib.decomposition.normalized_group_policy import (
    ActiveNormalizedGroupPolicy,
    load_packaged_normalized_group_policy,
)
from ontolib.decomposition.provenance_models import WorkItemOutcome
from ontolib.decomposition.run import RunConfig, _decompose_one
from ontolib.decomposition.sampling import load_sample_manifest
from ontolib.decomposition.semantic_identity import routing_implementation_identity
from ontolib.decomposition.source_preflight import run_source_preflight
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.search_index import NcitSearchIndex
from ontolib.terminologies.ncit.sibling_store import validate_ncit_sibling_manifest

pytestmark = [pytest.mark.integration, pytest.mark.full_store]


def _project_expected_concept_to_active_policies(
    expected: CurrentConceptEvidence,
    group_policy: ActiveNormalizedGroupPolicy,
) -> CurrentConceptEvidence:
    constituents = []
    for expected_row in expected.constituents:
        pair = (expected_row.axis, expected_row.filler)
        projected_row = expected_row
        if (
            expected_row.axis == MORPHOLOGY_AXIS
            and expected_row.filler in MORPHOLOGY_QUALIFIER_CODES
        ):
            continue
        if (
            expected_row.axis == "op:WithFinding"
            and (
                expected.code,
                expected_row.filler,
            )
            in ASSOCIATED_PRIOR_DISEASE
        ):
            projected_row = projected_row.model_copy(
                update={
                    "source_group_ids": (),
                    "source_definition_ids": (),
                    "source_facts": (),
                    "source_occurrence_ids": (),
                    "source_occurrences": (),
                }
            )
        policy_row = group_policy.by_code.get(expected.code)
        if policy_row is not None:
            block = policy_row.block_for(pair)
            projected_row = projected_row.model_copy(
                update={
                    "normalized_group_id": block.normalized_group_id,
                    "normalized_group_label": block.normalized_group_label,
                }
            )
        constituents.append(projected_row)
    return expected.model_copy(update={"constituents": tuple(constituents)})


async def test_c36081_constructor_preflight_is_typed_unknown_not_malformed() -> None:
    manifest = validate_ncit_sibling_manifest(
        Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")
    )
    async with ncit_sparql_client("http://localhost:7888") as client:

        async def read_definition(code: str):
            return await read_complete_definition(client.select, code, max_depth=7)

        result = await run_source_preflight(
            ("C36081",),
            read_definition=read_definition,
            source_identity=manifest.source_identity,
            reader_identity=routing_implementation_identity(),
            query_identity=routing_implementation_identity(),
            tool_identity=await client.version() or "missing-version",
            walker_max_depth=7,
            max_nodes=4096,
        )

    assert result.unsupported_codes == ("C36081",)
    assert "owl:unionOf" in result.unsupported_reasons["C36081"]
    assert result.malformed_codes == ()
    assert result.overflow_codes == ()


async def test_twenty_code_replay_matches_active_groups_and_tracked_semantics() -> None:
    manifest = validate_ncit_sibling_manifest(
        Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")
    )
    sample = load_sample_manifest(Path("samples/ncit-26.07d-m1-current-replay.json"))
    expected = CurrentEngineEvidence.model_validate_json(
        Path(
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json"
        ).read_bytes()
    )
    group_policy = load_packaged_normalized_group_policy()
    engine = make_engine(get_settings().database_url)
    outcomes: list[WorkItemOutcome] = []
    decompositions = []
    try:
        label_lookup = _make_label_lookup(NcitSearchIndex(make_sessionmaker(engine)))
        async with ncit_sparql_client("http://localhost:7888") as client:
            labels = await NcitGraphStore(client).labels_for(list(sample.codes))
            diagnostic_source = await read_axis_diagnostic_source(
                client, manifest.source_identity
            )
            for ordinal, code in enumerate(sample.codes):
                result = await _decompose_one(
                    code,
                    cast("Any", client),
                    label=labels.get(code),
                    label_lookup=label_lookup,
                    source_identity=manifest.source_identity,
                    collapse_policy=load_packaged_collapse_veto_policy(),
                    diagnostic_source=diagnostic_source,
                    walker_max_depth=RunConfig(branch="neoplasm").walker_max_depth,
                )
                decomposition = result.decomposition
                if decomposition is not None:
                    decompositions.append(decomposition)
                outcomes.append(
                    WorkItemOutcome(
                        run_id="bounded-current-replay",
                        concept_code=code,
                        ordinal=ordinal,
                        state="complete",
                        outcome=result.outcome,
                        semantic_type=(
                            decomposition.semantic_type
                            if decomposition is not None
                            else next(iter(result.semantic_types), None)
                        ),
                        semantic_types=result.semantic_types,
                        is_decomposed=result.outcome == "decomposed",
                        is_residual=result.outcome == "residual",
                        constituent_count=(
                            len(decomposition.constituents)
                            if decomposition is not None
                            else 0
                        ),
                        minted_count=len(result.minted),
                    )
                )
    finally:
        await dispose_engine(engine)

    actual = _concepts(outcomes, decompositions)
    assert tuple(item.code for item in actual) == tuple(
        item.code for item in expected.concepts
    )
    for actual_item, expected_item in zip(actual, expected.concepts, strict=True):
        policy_expected_item = _project_expected_concept_to_active_policies(
            expected_item, group_policy
        )
        assert len(actual_item.constituents) == len(policy_expected_item.constituents)
        for actual_row, expected_row in zip(
            actual_item.constituents, policy_expected_item.constituents, strict=True
        ):
            actual_fields = actual_row.model_dump(mode="json")
            expected_fields = expected_row.model_dump(mode="json")
            assert actual_row == expected_row, {
                "code": actual_item.code,
                "axis": actual_row.axis,
                "field_diff": {
                    field: {
                        "expected": expected_fields.get(field),
                        "actual": actual_fields.get(field),
                    }
                    for field in expected_fields.keys() | actual_fields.keys()
                    if expected_fields.get(field) != actual_fields.get(field)
                },
            }
        assert len(actual_item.occurrence_dispositions) == len(
            policy_expected_item.occurrence_dispositions
        )
        for actual_disposition, expected_disposition in zip(
            actual_item.occurrence_dispositions,
            policy_expected_item.occurrence_dispositions,
            strict=True,
        ):
            assert actual_disposition.model_dump(
                mode="json", exclude={"specificity_path"}
            ) == expected_disposition.model_dump(
                mode="json", exclude={"specificity_path"}
            ), actual_item.code
            if expected_disposition.specificity_path:
                assert (
                    actual_disposition.specificity_path
                    == expected_disposition.specificity_path
                ), actual_item.code
            if actual_disposition.kind == "collapsed-is-a":
                assert actual_disposition.specificity_path, actual_item.code
        assert actual_item.model_dump(
            mode="json", exclude={"occurrence_dispositions"}
        ) == policy_expected_item.model_dump(
            mode="json", exclude={"occurrence_dispositions"}
        ), actual_item.code


async def test_packaged_group_policy_applies_at_the_run_default_depth() -> None:
    manifest = validate_ncit_sibling_manifest(
        Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")
    )
    policy = load_packaged_normalized_group_policy()
    engine = make_engine(get_settings().database_url)
    try:
        label_lookup = _make_label_lookup(NcitSearchIndex(make_sessionmaker(engine)))
        async with ncit_sparql_client("http://localhost:7888") as client:
            labels = await NcitGraphStore(client).labels_for(list(policy.by_code))
            diagnostic_source = await read_axis_diagnostic_source(
                client, manifest.source_identity
            )
            for code in policy.by_code:
                result = await _decompose_one(
                    code,
                    cast("Any", client),
                    label=labels.get(code),
                    label_lookup=label_lookup,
                    source_identity=manifest.source_identity,
                    collapse_policy=load_packaged_collapse_veto_policy(),
                    normalized_group_policy=policy,
                    diagnostic_source=diagnostic_source,
                    walker_max_depth=RunConfig(branch="neoplasm").walker_max_depth,
                )
                assert result.outcome == "decomposed"
                assert result.decomposition is not None
    finally:
        await dispose_engine(engine)


async def test_r101_route_before_r82_collapse_cohort_uses_engine_dispositions() -> None:
    manifest = validate_ncit_sibling_manifest(
        Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")
    )
    expected = {
        "C6135": ("C12418", "C13063", "op:AssociatedRegion", "C13063"),
        "C101539": ("C12418", "C13063", "op:AssociatedRegion", "C13063"),
        "C4791": ("C12727", "C13004", "op:PrimarySite", "C12869"),
    }

    async def no_label_match(_surface: str) -> str | None:
        return None

    async with ncit_sparql_client("http://localhost:7888") as client:
        diagnostic_source = await read_axis_diagnostic_source(
            client, manifest.source_identity
        )
        no_group_policy = load_packaged_normalized_group_policy().model_copy(
            update={"rows": ()}
        )
        for code, (
            broader,
            retained_region,
            collapsed_axis,
            collapse_retained,
        ) in expected.items():
            result = await _decompose_one(
                code,
                cast("Any", client),
                label=None,
                label_lookup=no_label_match,
                source_identity=manifest.source_identity,
                collapse_policy=NO_COLLAPSE_VETO_POLICY,
                normalized_group_policy=no_group_policy,
                diagnostic_source=diagnostic_source,
                walker_max_depth=7,
            )
            decomposition = result.decomposition
            assert decomposition is not None
            definition = decomposition.complete_definition
            assert definition is not None
            r101_occurrence_ids = {
                row.occurrence_id
                for row in definition.occurrences
                if row.role_code == "R101"
            }
            disposition_ids = {
                row.source_occurrence_id
                for row in decomposition.occurrence_dispositions
                if row.source_occurrence_id in r101_occurrence_ids
            }
            assert disposition_ids == r101_occurrence_ids
            assert ("op:AssociatedRegion", retained_region) in {
                (row.axis, row.filler_code) for row in decomposition.constituents
            }
            assert ("op:AssociatedRegion", broader) not in {
                (row.axis, row.filler_code) for row in decomposition.constituents
            }
            collapsed = [
                row
                for row in decomposition.occurrence_dispositions
                if row.source_filler == broader
                and row.normalized_axis == collapsed_axis
            ]
            assert collapsed
            assert [
                (
                    row.kind,
                    row.normalized_axis,
                    row.retained_filler,
                    row.r82_part,
                    row.r82_whole,
                )
                for row in collapsed
            ] == [
                (
                    "collapsed-r82",
                    collapsed_axis,
                    collapse_retained,
                    collapse_retained,
                    broader,
                )
            ] * len(collapsed)
