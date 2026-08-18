from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.adjudication import main as adjudication_main

from backend.config import get_settings
from backend.db import dispose_engine, make_engine
from ontolib.decomposition.complete_definition import read_complete_definition
from ontolib.decomposition.pre_resume import (
    acquire_candidate_evidence,
    affected_missing_p106,
    canonical_pre_resume_json,
)
from ontolib.decomposition.resume_dry_run import canonical_resume_dry_run_json
from ontolib.decomposition.stated_queries import resolve_part_of_pairs
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
def test_pre_resume_cli_is_repeatable_and_reports_freshness(tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    common = [
        "generate-pre-resume-proof",
        "--source-manifest",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "--run-id",
        RUN_ID,
        "--endpoint",
        "http://localhost:7888",
    ]

    adjudication_main([*common, "--output", str(first)])
    adjudication_main([*common, "--output", str(second)])

    first_payload = __import__("json").loads(first.read_text())
    second_payload = __import__("json").loads(second.read_text())
    assert canonical_pre_resume_json(first_payload) == canonical_pre_resume_json(
        second_payload
    )
    assert first_payload["proof_identity"] == second_payload["proof_identity"]
    assert first_payload["postgres_reads"] > 0
    assert first_payload["qlever_reads"] > 0


@pytest.mark.integration
@pytest.mark.full_store
def test_protected_resume_dry_run_is_repeatable_read_only_and_proof_bound(
    tmp_path,
) -> None:
    proof = tmp_path / "proof.json"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
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
    common = [
        "dry-run-resume",
        "--source-manifest",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "--proof",
        str(proof),
        "--run-id",
        RUN_ID,
        "--endpoint",
        "http://localhost:7888",
        "--branch",
        "neoplasm",
        "--walker-max-depth",
        "7",
        "--out",
        "tmp/neoplasm-r101-v4-full.ttl",
    ]

    adjudication_main([*common, "--output", str(first)])
    adjudication_main([*common, "--output", str(second)])

    first_payload = __import__("json").loads(first.read_text())
    second_payload = __import__("json").loads(second.read_text())
    assert canonical_resume_dry_run_json(
        first_payload
    ) == canonical_resume_dry_run_json(second_payload)
    assert first_payload["identity"] == second_payload["identity"]
    assert first_payload["pending_count"] == 9733
    assert first_payload["pending_attempt_count"] == 0
    assert first_payload["pending_digest"] == (
        "8ebd90a8e143c6676d0ac70cdd58b0921724e81037e74ec03a93a175e6acabf1"
    )
    assert first_payload["completed_exclusion_count"] == 5900
    assert first_payload["selected_complete_count"] == 0
    assert first_payload["postgres_reads"] == 3
    assert first_payload["qlever_reads"] > 0
    assert first_payload["status"] == "failed"
    assert first_payload["error_type"] == "BrokenPipeError"


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
