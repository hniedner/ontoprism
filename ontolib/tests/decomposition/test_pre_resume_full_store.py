from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.adjudication import main as adjudication_main

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.complete_definition import read_complete_definition
from ontolib.decomposition.pre_resume import (
    acquire_candidate_evidence,
    affected_missing_p106,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.r101_conservation import load_r101_conservation_report
from ontolib.decomposition.stated_queries import (
    resolve_part_of_pairs,
    resolve_part_of_paths,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client

if TYPE_CHECKING:
    from collections.abc import Collection

RUN_ID = "neoplasm-0e88b7c0-eba0-42e6-8836-fa10f2604f46"
COMPLETED_FULL_RUN = "completed-full-run"


class _RemoveOneP106:
    def __init__(self, client, removed_code: str) -> None:
        self._client = client
        self._removed_code = removed_code

    async def select(self, query: str, *, required_variables=()):
        rows = await self._client.select(query, required_variables=required_variables)
        return [row for row in rows if row.get("code") != self._removed_code]


@pytest.mark.integration
@pytest.mark.full_store
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


@pytest.mark.integration
@pytest.mark.full_store
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


@pytest.mark.integration
@pytest.mark.full_store
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


@pytest.mark.integration
@pytest.mark.full_store
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


@pytest.mark.integration
@pytest.mark.full_store
async def test_r101_highest_fanout_records_use_bounded_candidate_and_r82_queries() -> (
    None
):
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

    assert definition_reads == 50


@pytest.mark.integration
@pytest.mark.full_store
async def test_tied_highest_fanout_ledgers_and_paths_match_generated_report() -> None:
    report = load_r101_conservation_report(
        Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")
    )
    engine = make_engine(get_settings().database_url)
    try:
        source = await ProvenanceStore(
            make_sessionmaker(engine)
        ).r101_occurrence_ledger(report.old_run_id, report.new_run_id)
        async with ncit_sparql_client("http://localhost:7888") as client:
            for concept_code in ("C5356", "C5552"):
                expected = tuple(
                    item
                    for item in report.occurrences
                    if item.concept_code == concept_code
                )
                actual = tuple(
                    item
                    for item in source.occurrences
                    if item.old_occurrence.concept_code == concept_code
                )
                candidates = tuple(
                    sorted(
                        {
                            (retained.filler_code, old.filler_code)
                            for item in actual
                            if item.old_links and not item.new_links
                            for old in item.old_links
                            for retained in item.retained_new_r101_links
                            if old.axis == retained.axis
                        }
                    )
                )
                paths = await resolve_part_of_paths(
                    client, candidates, source_identity=report.source_identity
                )

                assert len(actual) == len(expected) == 16
                assert tuple(
                    item.old_occurrence.structural_key for item in actual
                ) == tuple(item.structural_key for item in expected)
                expected_paths = {
                    (
                        item.retained_r82_target.filler_code,
                        item.old_links[0].filler_code,
                    ): item.r82_path
                    for item in expected
                    if item.retained_r82_target is not None and len(item.old_links) == 1
                }
                assert {
                    key: value.edges for key, value in paths.paths.items()
                } == expected_paths
                assert paths.query_count <= 10
                assert paths.max_pair_batch_size <= 8
    finally:
        await dispose_engine(engine)
