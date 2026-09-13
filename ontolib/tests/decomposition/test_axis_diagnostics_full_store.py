from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS
from ontolib.decomposition.axis_diagnostics import (
    build_disjoint_pairs_query,
    disjoint_pairs_from_rows,
    read_axis_diagnostic_source,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    validate_ncit_sibling_manifest,
)

pytestmark = [pytest.mark.integration, pytest.mark.full_store]

_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST = _ROOT / "data" / "qlever-ncit" / CANDIDATE_MANIFEST_FILENAME
_EVIDENCE = Path(__file__).with_name("golden") / "neoplasm-current-engine-evidence.json"


async def test_real_axis_diagnostics_are_source_bound_live_and_batched() -> None:
    manifest = validate_ncit_sibling_manifest(_MANIFEST)
    url = os.environ.get(
        "NCIT_STATED_SPARQL_URL",
        os.environ.get("NCIT_SPARQL_URL", "http://localhost:7888"),
    )
    reads = 0

    async with ncit_sparql_client(url, query_timeout=180.0) as client:
        original = client.select_once

        async def counted(query: str, **kwargs):
            nonlocal reads
            reads += 1
            return await original(query, **kwargs)

        client.select_once = counted  # type: ignore[method-assign]
        source = await read_axis_diagnostic_source(client, manifest.source_identity)

    assert reads == 9
    assert (
        source.classify(axis="op:Morphology", filler_code="C12218").status == "invalid"
    )
    assert (
        source.classify(axis="op:PrimarySite", filler_code="C12431").status == "valid"
    )

    evidence = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    verdicts = [
        source.classify(axis=item["axis"], filler_code=item["filler"])
        for concept in evidence["concepts"]
        for item in concept["constituents"]
        if item["axis"] in AXIS_CONTRACTS and item["filler"].startswith("C")
    ]
    assert verdicts
    assert not [verdict for verdict in verdicts if verdict.status == "invalid"]


async def test_ncit_26_07d_stated_all_disjoint_classes_shape_is_complete() -> None:
    manifest = validate_ncit_sibling_manifest(_MANIFEST)
    url = os.environ.get(
        "NCIT_STATED_SPARQL_URL",
        os.environ.get("NCIT_SPARQL_URL", "http://localhost:7888"),
    )
    async with ncit_sparql_client(url, query_timeout=180.0) as client:
        rows = await client.select_once(
            build_disjoint_pairs_query(),
            required_variables={
                "left",
                "right",
                "set",
                "head",
                "node",
                "first",
                "rest",
            },
        )

    binary_rows = [row for row in rows if "set" not in row]
    list_rows = [row for row in rows if "set" in row]
    sets = {row["set"] for row in list_rows}
    member_counts = tuple(
        sorted(
            len(
                {
                    row["first"]
                    for row in list_rows
                    if row["set"] == set_id and "first" in row
                }
            )
            for set_id in sets
        )
    )
    observed = (
        manifest.ontology_version,
        manifest.source_identity,
        len(binary_rows),
        len(sets),
        len(list_rows),
        member_counts,
        len(disjoint_pairs_from_rows(rows)),
    )
    assert observed == (
        "26.07d",
        "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092",
        171,
        0,
        0,
        (),
        171,
    )
