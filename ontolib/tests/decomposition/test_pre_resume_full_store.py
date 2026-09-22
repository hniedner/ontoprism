from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from scripts.adjudication import main as adjudication_main
from scripts.decompose import _make_label_lookup
from scripts.research.current_evidence import CurrentEngineEvidence, _concepts

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.axis_diagnostics import read_axis_diagnostic_source
from ontolib.decomposition.collapse_policy import (
    NO_COLLAPSE_VETO_POLICY,
    load_packaged_collapse_veto_policy,
)
from ontolib.decomposition.complete_definition import read_complete_definition
from ontolib.decomposition.fanout_baseline import load_fanout_baseline
from ontolib.decomposition.normalized_group_policy import (
    load_packaged_normalized_group_policy,
)
from ontolib.decomposition.pre_resume import (
    acquire_candidate_evidence,
    affected_missing_p106,
)
from ontolib.decomposition.provenance_models import WorkItemOutcome
from ontolib.decomposition.run import _decompose_one
from ontolib.decomposition.sampling import load_sample_manifest
from ontolib.decomposition.semantic_identity import routing_implementation_identity
from ontolib.decomposition.source_preflight import run_source_preflight
from ontolib.decomposition.stated_queries import resolve_part_of_pairs
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.search_index import NcitSearchIndex
from ontolib.terminologies.ncit.sibling_store import validate_ncit_sibling_manifest

pytestmark = [pytest.mark.integration, pytest.mark.full_store]

if TYPE_CHECKING:
    from collections.abc import Collection

RUN_ID = "neoplasm-0e88b7c0-eba0-42e6-8836-fa10f2604f46"
COMPLETED_FULL_RUN = "completed-full-run"


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
                    detector_identity=expected.detector_identity,
                    walker_max_depth=7,
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
        policy_row = group_policy.by_code.get(expected_item.code)
        policy_expected_item = expected_item
        if policy_row is not None:
            policy_expected_item = expected_item.model_copy(
                update={
                    "constituents": tuple(
                        expected_row.model_copy(
                            update={
                                "normalized_group_id": block.normalized_group_id,
                                "normalized_group_label": block.normalized_group_label,
                            }
                        )
                        for expected_row in expected_item.constituents
                        for block in [
                            policy_row.block_for(
                                (expected_row.axis, expected_row.filler)
                            )
                        ]
                    )
                }
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
        assert actual_item.model_dump(mode="json") == policy_expected_item.model_dump(
            mode="json"
        ), actual_item.code


class _RemoveOneP106:
    def __init__(self, client, removed_code: str) -> None:
        self._client = client
        self._removed_code = removed_code

    async def select(self, query: str, *, required_variables=()):
        rows = await self._client.select(query, required_variables=required_variables)
        return [row for row in rows if row.get("code") != self._removed_code]


class _CountingClient:
    def __init__(self, client: Any) -> None:
        self._client = client
        self.select_count = 0
        self.select_once_count = 0

    async def select(self, query: str, *, required_variables=()):
        self.select_count += 1
        return await self._client.select(query, required_variables=required_variables)

    async def select_once(self, query: str, *, required_variables=()):
        self.select_once_count += 1
        return await self._client.select_once(
            query, required_variables=required_variables
        )


async def test_completed_full_run_candidate_denominator_matches_reachability() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with ncit_sparql_client("http://localhost:7888") as client:
            evidence = await acquire_candidate_evidence(engine, RUN_ID, client)
    finally:
        await dispose_engine(engine)

    assert (COMPLETED_FULL_RUN, evidence.production.counts) == (
        COMPLETED_FULL_RUN,
        (212, 316, 356, 11),
    )
    assert (COMPLETED_FULL_RUN, evidence.production.identity) == (
        COMPLETED_FULL_RUN,
        "06fb5053a129cbf64220df171ae22a9973bac1cfd7e27084d3da530cfd677193",
    )
    assert (COMPLETED_FULL_RUN, evidence.route_filter_sensitivity.counts) == (
        COMPLETED_FULL_RUN,
        (230, 398, 479, 13),
    )
    assert (COMPLETED_FULL_RUN, evidence.route_filter_sensitivity.identity) == (
        COMPLETED_FULL_RUN,
        "f0f8a813b12e469e40dc210a927177598ad7d921a3a37842f20d1562524b8319",
    )
    assert evidence.validation.affected_counts == (0, 0, 0, 0)
    assert evidence.postgres_reads > 0
    assert evidence.qlever_reads > 0


def test_completed_run_refuses_stale_pre_resume_proof(tmp_path) -> None:
    output = tmp_path / "proof.json"
    common = [
        "generate-pre-resume-proof",
        "--source-manifest",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "--run-id",
        RUN_ID,
        "--endpoint",
        "http://localhost:7888",
    ]

    with pytest.raises(ValueError, match="failure snapshot drift"):
        adjudication_main([*common, "--output", str(output)])
    assert not output.exists()


def test_completed_run_refusal_does_not_create_resume_dry_run_artifacts(
    tmp_path,
) -> None:
    proof = tmp_path / "proof.json"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    with pytest.raises(ValueError, match="failure snapshot drift"):
        adjudication_main(
            [
                "generate-pre-resume-proof",
                "--source-manifest",
                "data/qlever-ncit/.ontoprism-ncit-candidate.json",
                "--run-id",
                RUN_ID,
                "--endpoint",
                "http://localhost:7888",
                "--output",
                str(proof),
            ]
        )
    assert not proof.exists()
    assert not first.exists()
    assert not second.exists()


async def test_real_candidate_missing_p106_reject_matches_boundary_double() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with ncit_sparql_client("http://localhost:7888") as client:
            baseline = await acquire_candidate_evidence(engine, RUN_ID, client)
            removed_code = baseline.production.tuples[0].filler_code
            boundary = await acquire_candidate_evidence(
                engine, RUN_ID, _RemoveOneP106(client, removed_code)
            )
    finally:
        await dispose_engine(engine)

    semantic_double = dict(baseline.semantic_types)
    semantic_double[removed_code] = None
    affected_tuples = affected_missing_p106(baseline.production.tuples, semantic_double)
    affected_occurrences = tuple(
        item
        for item in baseline.production.occurrences
        if item.filler_code == removed_code
    )
    expected_counts = (
        len({item.concept_code for item in affected_tuples}),
        len(affected_tuples),
        len(affected_occurrences),
        1,
    )

    assert boundary.validation.affected_counts == expected_counts
    assert bool(affected_tuples) is True
    assert boundary.validation.authorizable is False


async def test_r101_highest_fanout_records_use_bounded_candidate_and_r82_queries() -> (
    None
):
    manifest = validate_ncit_sibling_manifest(
        Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")
    )
    baseline = load_fanout_baseline(
        Path("ontolib/tests/decomposition/golden/neoplasm-highest-fanout.json"),
        expected_source_identity=manifest.source_identity,
        expected_release=manifest.ontology_version,
    )
    definition_reads = 0
    async with ncit_sparql_client("http://localhost:7888") as client:

        async def counted_select(
            query: str, *, required_variables: Collection[str] = ()
        ):
            nonlocal definition_reads
            definition_reads += 1
            return await client.select(query, required_variables=required_variables)

        definitions = tuple(
            [
                await read_complete_definition(counted_select, code, max_depth=7)
                for code in ("C9379", "C9423")
            ]
        )
        filler_groups = tuple(
            tuple(
                sorted(
                    {
                        occurrence.filler_code
                        for occurrence in definition.occurrences
                        if occurrence.role_code == "R101"
                    }
                )
            )
            for definition in definitions
        )
        assert all(filler_groups)
        assert all(len(group) <= 256 for group in filler_groups)
        for group in filler_groups:
            await resolve_part_of_pairs(client, group)

    assert (
        0
        < definition_reads
        <= (len(definitions) * baseline.logical_select_count_budget)
    )

    async def no_label_match(_surface: str) -> str | None:
        return None

    async with ncit_sparql_client("http://localhost:7888") as client:
        diagnostic_source = await read_axis_diagnostic_source(
            client, manifest.source_identity
        )
        for code in baseline.concept_codes:
            counted = _CountingClient(client)
            result = await _decompose_one(
                code,
                cast("Any", counted),
                label=None,
                label_lookup=no_label_match,
                source_identity=manifest.source_identity,
                collapse_policy=NO_COLLAPSE_VETO_POLICY,
                diagnostic_source=diagnostic_source,
                detector_identity="0" * 64,
                walker_max_depth=7,
            )
            assert result.decomposition is not None
            assert counted.select_count <= baseline.logical_select_count_budget + 3
            assert counted.select_once_count <= baseline.select_once_r82_count_budget


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
                detector_identity="0" * 64,
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
