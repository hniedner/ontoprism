from pathlib import Path
from typing import Any, cast

import pytest

from ontolib.decomposition.collapse_policy import NO_COLLAPSE_VETO_POLICY
from ontolib.decomposition.fanout_baseline import _CountingClient
from ontolib.decomposition.mixed_chain_inventory import load_mixed_chain_inventory
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
        for code, (broad, _terminal, _kinds) in expected.items():
            before = counted.logical_select_count
            result = await _decompose_one(
                code,
                cast("Any", counted),
                label=None,
                label_lookup=no_label_match,
                source_identity=inventory.source_identity,
                collapse_policy=NO_COLLAPSE_VETO_POLICY,
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
