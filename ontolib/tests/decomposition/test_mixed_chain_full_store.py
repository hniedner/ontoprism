from pathlib import Path
from typing import Any, cast

import pytest
from scripts.validation.run_agent_replay import (
    _generate_mixed_chain_corrected_projection_async,
    _generate_mixed_chain_inventory_async,
)

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.axis_diagnostics import read_axis_diagnostic_source
from ontolib.decomposition.collapse_policy import NO_COLLAPSE_VETO_POLICY
from ontolib.decomposition.fanout_baseline import _CountingClient
from ontolib.decomposition.mixed_chain_inventory import load_mixed_chain_inventory
from ontolib.decomposition.mixed_chain_projection import (
    create_corrected_projection,
    load_corrected_projection,
    project_mixed_chain_candidate,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.run import _decompose_one
from ontolib.terminologies.ncit.client import ncit_sparql_client


@pytest.mark.integration
@pytest.mark.full_store
async def test_mixed_chain_canaries_collapse_with_real_source_paths() -> None:
    inventory = load_mixed_chain_inventory(
        Path(
            "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
        )
    )
    expected = {
        "C102570": ("C137974", "C12318", ("is-a", "r82")),
        "C161649": ("C12841", "C12787", ("is-a", "r82")),
        "C175329": ("C32574", "C12346", ("is-a", "r82")),
        "C27381": ("C12704", "C12396", ("is-a", "r82")),
    }

    async def no_label_match(_surface: str) -> str | None:
        return None

    observed: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    query_counts: dict[str, int] = {}
    async with ncit_sparql_client("http://localhost:7888") as client:
        counted = _CountingClient(client)
        diagnostic_source = await read_axis_diagnostic_source(
            client, inventory.source_identity
        )
        for code, (broad, _terminal, _kinds) in expected.items():
            before = counted.logical_select_count
            result = await _decompose_one(
                code,
                cast("Any", counted),
                label=None,
                label_lookup=no_label_match,
                source_identity=inventory.source_identity,
                collapse_policy=NO_COLLAPSE_VETO_POLICY,
                diagnostic_source=diagnostic_source,
                detector_identity="0" * 64,
                walker_max_depth=7,
            )
            assert result.decomposition is not None
            disposition = next(
                row
                for row in result.decomposition.occurrence_dispositions
                if row.source_filler == broad
            )
            observed[code] = (
                disposition.source_filler,
                disposition.retained_filler,
                tuple(edge.kind for edge in disposition.specificity_path),
            )
            query_counts[code] = counted.logical_select_count - before

    assert observed == expected
    assert query_counts["C27381"] == max(query_counts.values()) == 46


@pytest.mark.integration
@pytest.mark.full_store
async def test_corrected_projection_replays_from_bounded_persisted_state() -> None:
    inventory = load_mixed_chain_inventory(
        Path(
            "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
        )
    )
    expected = load_corrected_projection(
        Path(
            "ontolib/tests/decomposition/golden/"
            "neoplasm-r101-v5-corrected-projection.json"
        )
    )
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        store = ProvenanceStore(make_sessionmaker(engine))
        occurrences = await store.selector_occurrences_for_codes(
            inventory.source_run_id, inventory.candidate_codes
        )
        states = await store.projection_state_for_codes(
            inventory.source_run_id, inventory.candidate_codes
        )
    finally:
        await dispose_engine(engine)
    occurrences_by_code: dict[str, list[Any]] = {}
    for row in occurrences:
        occurrences_by_code.setdefault(row.concept_code, []).append(row)
    states_by_code = {row.concept_code: row for row in states}
    projections = tuple(
        project_mixed_chain_candidate(
            candidate=candidate,
            occurrences=tuple(occurrences_by_code[candidate.concept_code]),
            before_constituents=states_by_code[candidate.concept_code].constituents,
            before_dispositions=states_by_code[candidate.concept_code].dispositions,
            source_identity=inventory.source_identity,
        )
        for candidate in inventory.candidates
    )

    actual = create_corrected_projection(
        source_run_id=inventory.source_run_id,
        source_report_identity=inventory.source_report_identity,
        source_identity=inventory.source_identity,
        selector_identity=inventory.selector_identity,
        inventory_identity=inventory.identity,
        expected_candidate_codes=inventory.candidate_codes,
        projections=projections,
    )

    assert actual == expected
    assert actual.source_report_identity == inventory.source_report_identity
    assert actual.projection_identity == (
        "4cce6fd1e8ee4f7dade200a4331e833bf5e435535ee3f550e3573076b61770ce"
    )


@pytest.mark.integration
@pytest.mark.full_store
async def test_corrected_projection_generator_binds_exact_historical_report(
    tmp_path: Path,
) -> None:
    output = tmp_path / "projection.json"
    inventory = Path(
        "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
    )
    report = Path(
        "ontolib/tests/decomposition/golden/"
        "neoplasm-r101-v5-2b39-historical-conservation.json.gz"
    )

    await _generate_mixed_chain_corrected_projection_async(inventory, report, output)

    assert load_corrected_projection(output) == load_corrected_projection(
        Path(
            "ontolib/tests/decomposition/golden/"
            "neoplasm-r101-v5-corrected-projection.json"
        )
    )


@pytest.mark.integration
@pytest.mark.full_store
async def test_historical_inventory_generator_replays_from_exact_report(
    tmp_path: Path,
) -> None:
    output = tmp_path / "inventory.json"
    report = Path(
        "ontolib/tests/decomposition/golden/"
        "neoplasm-r101-v5-2b39-historical-conservation.json.gz"
    )

    await _generate_mixed_chain_inventory_async(report, output)

    assert load_mixed_chain_inventory(output) == load_mixed_chain_inventory(
        Path(
            "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
        )
    )
