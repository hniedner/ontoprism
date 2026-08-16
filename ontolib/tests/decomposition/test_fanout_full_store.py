from __future__ import annotations

import os
from pathlib import Path

import pytest

from ontolib.decomposition.fanout_baseline import (
    load_fanout_baseline,
    rerun_fanout_concept,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    validate_ncit_sibling_manifest,
)

pytestmark = [pytest.mark.integration, pytest.mark.full_store]

_ROOT = Path(__file__).resolve().parents[3]
_BASELINE = Path(__file__).with_name("golden") / "neoplasm-highest-fanout.json"
_DEFAULT_MANIFEST = _ROOT / "data" / "qlever-ncit" / CANDIDATE_MANIFEST_FILENAME


async def test_observed_highest_fanout_matches_source_and_fixed_budgets() -> None:
    manifest_path = Path(
        os.environ.get("NCIT_CANDIDATE_MANIFEST", str(_DEFAULT_MANIFEST))
    )
    manifest = validate_ncit_sibling_manifest(manifest_path)
    baseline = load_fanout_baseline(
        _BASELINE,
        expected_source_identity=manifest.source_identity,
        expected_release=manifest.ontology_version,
    )
    url = os.environ.get(
        "NCIT_STATED_SPARQL_URL",
        os.environ.get("NCIT_SPARQL_URL", "http://localhost:7888"),
    )

    async with ncit_sparql_client(url, query_timeout=180.0) as client:
        assert await client.version() == baseline.ontology_release
        observations = [
            await rerun_fanout_concept(client, code) for code in baseline.concept_codes
        ]

    assert all(
        item.restriction_fact_count == baseline.restriction_fact_count
        and item.restriction_occurrence_count == baseline.restriction_occurrence_count
        and item.logical_select_count <= baseline.logical_select_count_budget
        and item.select_once_r82_count <= baseline.select_once_r82_count_budget
        for item in observations
    )
