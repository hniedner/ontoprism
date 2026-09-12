from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ontolib.decomposition import atomic_write
from ontolib.decomposition.mixed_chain_inventory import (
    HISTORICAL_MIXED_CHAIN_SELECTOR_IDENTITY,
    HistoricalMixedChainRunBinding,
    MixedChainCandidate,
    MixedChainInventory,
    load_historical_mixed_chain_source_report,
    load_mixed_chain_inventory,
    require_mixed_chain_preflight,
    write_mixed_chain_inventory,
)
from ontolib.decomposition.models import SpecificityPathEdge


def _candidate(code: str, broad: str, terminal: str) -> MixedChainCandidate:
    return MixedChainCandidate(
        concept_code=code,
        axis="op:AssociatedSite",
        source_role="R100",
        broad_filler=broad,
        terminal_filler=terminal,
        source_occurrence_ids=("1" * 64,),
        specificity_path=(  # type: ignore[arg-type] - validates engine edge serialization
            SpecificityPathEdge(
                kind="is-a",
                broader_code=broad,
                narrower_code="C2",
                source_identity="a" * 64,
            ),
            SpecificityPathEdge(
                kind="r82",
                broader_code="C2",
                narrower_code=terminal,
                source_identity="a" * 64,
            ),
        ),
    )


def _inventory() -> MixedChainInventory:
    return MixedChainInventory.create(
        source_identity="a" * 64,
        worklist_identity="b" * 64,
        worklist_count=15_633,
        selector_identity=HISTORICAL_MIXED_CHAIN_SELECTOR_IDENTITY,
        source_run_id="neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722",
        source_report_identity="d" * 64,
        candidates=tuple(
            _candidate(code, broad, terminal)
            for code, broad, terminal in (
                ("C102570", "C137974", "C1"),
                ("C161649", "C12841", "C3"),
                ("C175329", "C32574", "C4"),
                ("C27381", "C12704", "C5"),
            )
        ),
    )


@pytest.mark.unit
def test_mixed_chain_inventory_is_content_addressed_and_preflight_bound() -> None:
    inventory = _inventory()

    require_mixed_chain_preflight(
        inventory,
        source_identity="a" * 64,
        worklist_identity="b" * 64,
        worklist_count=15_633,
    )
    assert inventory.candidate_count == 4
    assert inventory.candidate_codes == (
        "C102570",
        "C161649",
        "C175329",
        "C27381",
    )
    assert len(inventory.identity) == 64

    with pytest.raises(ValueError, match="worklist identity"):
        require_mixed_chain_preflight(
            inventory,
            source_identity="a" * 64,
            worklist_identity="e" * 64,
            worklist_count=15_633,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_identity", "e" * 64, "source identity"),
        ("worklist_count", 15_632, "worklist count"),
    ],
)
def test_mixed_chain_preflight_rejects_each_run_binding_drift(
    field: str, value: str | int, message: str
) -> None:
    arguments: dict[str, str | int] = {
        "source_identity": "a" * 64,
        "worklist_identity": "b" * 64,
        "worklist_count": 15_633,
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        require_mixed_chain_preflight(_inventory(), **arguments)  # type: ignore[arg-type]


@pytest.mark.unit
def test_mixed_chain_preflight_rejects_unclassified_or_missing_canaries() -> None:
    unclassified = MixedChainInventory.create(
        source_identity="a" * 64,
        worklist_identity="b" * 64,
        worklist_count=15_633,
        selector_identity=HISTORICAL_MIXED_CHAIN_SELECTOR_IDENTITY,
        source_run_id="neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722",
        source_report_identity="d" * 64,
        candidates=_inventory().candidates,
        unclassified_codes=("C99",),
    )
    missing_canaries = MixedChainInventory.create(
        source_identity="a" * 64,
        worklist_identity="b" * 64,
        worklist_count=15_633,
        selector_identity=HISTORICAL_MIXED_CHAIN_SELECTOR_IDENTITY,
        source_run_id="neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722",
        source_report_identity="d" * 64,
        candidates=(_candidate("C102570", "C137974", "C1"),),
    )
    expected = {
        "source_identity": "a" * 64,
        "worklist_identity": "b" * 64,
        "worklist_count": 15_633,
    }

    with pytest.raises(ValueError, match="unclassified mixed-chain candidates"):
        require_mixed_chain_preflight(unclassified, **expected)
    with pytest.raises(ValueError, match="required canaries"):
        require_mixed_chain_preflight(missing_canaries, **expected)


@pytest.mark.unit
def test_mixed_chain_preflight_rejects_foreign_historical_selector() -> None:
    inventory = MixedChainInventory.create(
        source_identity="a" * 64,
        worklist_identity="b" * 64,
        worklist_count=15_633,
        selector_identity="e" * 64,
        source_run_id="neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722",
        source_report_identity="d" * 64,
        candidates=_inventory().candidates,
    )

    with pytest.raises(ValueError, match="historical selector identity"):
        require_mixed_chain_preflight(
            inventory,
            source_identity="a" * 64,
            worklist_identity="b" * 64,
            worklist_count=15_633,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"source_occurrence_ids": ("2" * 64, "1" * 64)}, "not canonical"),
        ({"source_occurrence_ids": ()}, "occurrences are invalid"),
        ({"source_occurrence_ids": ("x" * 64,)}, "occurrences are invalid"),
        (
            {"specificity_path.0.broader_code": "C9"},
            "does not start at broad filler",
        ),
        (
            {"specificity_path.1.narrower_code": "C9"},
            "does not end at terminal filler",
        ),
        ({"specificity_path.1.kind": "is-a"}, "both relation kinds"),
        ({"specificity_path.1.broader_code": "C9"}, "not contiguous"),
    ],
)
def test_mixed_chain_candidate_rejects_noncanonical_source_evidence(
    mutation: dict[str, object], message: str
) -> None:
    payload = _candidate("C102570", "C137974", "C1").model_dump()
    for field, value in mutation.items():
        if field.startswith("specificity_path."):
            _, index, edge_field = field.split(".")
            payload["specificity_path"][int(index)][edge_field] = value
        else:
            payload[field] = value

    with pytest.raises(ValueError, match=message):
        MixedChainCandidate.model_validate(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_codes", ("C102570",), "candidate codes differ"),
        ("candidate_count", 3, "candidate count differs"),
        ("inventory_identity", "0" * 64, "inventory identity differs"),
    ],
)
def test_mixed_chain_inventory_rejects_tampered_shape_or_identity(
    field: str, value: object, message: str
) -> None:
    payload = _inventory().model_dump()
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        MixedChainInventory.model_validate(payload)


@pytest.mark.unit
def test_mixed_chain_inventory_rejects_duplicate_concepts_and_foreign_paths() -> None:
    duplicate_payload = _inventory().model_dump()
    duplicate_payload["candidates"] = (
        *duplicate_payload["candidates"],
        duplicate_payload["candidates"][0],
    )

    with pytest.raises(ValueError, match="not one per concept"):
        MixedChainInventory.model_validate(duplicate_payload)

    foreign_path_payload = _inventory().model_dump()
    foreign_path_payload["candidates"][0]["specificity_path"][0]["source_identity"] = (
        "e" * 64
    )
    foreign_path_payload["candidates"][0]["specificity_path"][1]["source_identity"] = (
        "e" * 64
    )

    with pytest.raises(ValueError, match="path source identity differs"):
        MixedChainInventory.model_validate(foreign_path_payload)


@pytest.mark.unit
def test_mixed_chain_inventory_round_trips_canonical_json(tmp_path: Path) -> None:
    target = tmp_path / "inventory.json"

    write_mixed_chain_inventory(target, _inventory())

    assert load_mixed_chain_inventory(target) == _inventory()
    assert target.read_text(encoding="utf-8").endswith("\n")


@pytest.mark.unit
def test_mixed_chain_inventory_write_failure_preserves_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "inventory.json"
    target.write_text("historical evidence\n")

    def interrupted_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("interrupted")

    monkeypatch.setattr(atomic_write.os, "replace", interrupted_replace)

    with pytest.raises(OSError, match="interrupted"):
        write_mixed_chain_inventory(target, _inventory())

    assert target.read_bytes() == b"historical evidence\n"


@pytest.mark.unit
def test_packaged_mixed_chain_inventory_is_exact_and_complete() -> None:
    inventory = load_mixed_chain_inventory(
        Path(
            "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
        )
    )

    assert inventory.identity == (
        "3fc473e9ce049c2c619be8b714f5bfb4cbf2dd7297a10ff8e1bfa00feb1ba0cd"
    )
    assert inventory.candidate_count == 39
    assert inventory.unclassified_codes == ()
    assert {"C102570", "C161649", "C175329", "C27381"} <= set(inventory.candidate_codes)
    assert all(
        tuple(edge.kind for edge in candidate.specificity_path) == ("is-a", "r82")
        for candidate in inventory.candidates
    )
    require_mixed_chain_preflight(
        inventory,
        source_identity=inventory.source_identity,
        worklist_identity=inventory.worklist_identity,
        worklist_count=inventory.worklist_count,
    )
    assert inventory.selector_identity == HISTORICAL_MIXED_CHAIN_SELECTOR_IDENTITY


@pytest.mark.unit
def test_historical_mixed_chain_inventory_binds_available_report_evidence() -> None:
    inventory = load_mixed_chain_inventory(
        Path(
            "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
        )
    )
    report = load_historical_mixed_chain_source_report(
        Path(
            "ontolib/tests/decomposition/golden/"
            "neoplasm-r101-v5-2b39-historical-conservation.json.gz"
        )
    )

    assert report.new_run_id == inventory.source_run_id
    assert report.report_identity == inventory.source_report_identity
    assert report.report_identity == (
        "25ed41375bc633505031a1e69327c41ac02a76f3f0759f86c899357b4fd4d6ba"
    )


@pytest.mark.unit
def test_historical_mixed_chain_run_binding_rejects_noncanonical_source_rows() -> None:
    fingerprint = {
        "schema_version": 4,
        "source_identity": "a" * 64,
        "collapse_policy_identity": "b" * 64,
        "routing_implementation_identity": "c" * 64,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": ["Neoplastic Process"],
        "worklist": ["C1"],
        "total_limit": None,
        "sample_manifest_identity": None,
        "algorithm_version": "decomposition-v5",
        "config_version": "nested-definition-v2",
        "walker_max_depth": 7,
        "output_mode": "file",
        "load_mode": "none",
        "emitted_at": "2026-09-08T00:00:00Z",
    }
    binding = {
        "run_id": "neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722",
        "fingerprint": fingerprint,
        "fingerprint_identity": "d" * 64,
        "materialized_worklist": ["C1"],
    }

    with pytest.raises(ValueError, match="semantic types are not canonical"):
        HistoricalMixedChainRunBinding.model_validate_json(
            json.dumps(
                {
                    **binding,
                    "fingerprint": {
                        **fingerprint,
                        "semantic_types": ["Z", "A"],
                    },
                }
            )
        )
    with pytest.raises(ValueError, match="worklist is not unique"):
        HistoricalMixedChainRunBinding.model_validate_json(
            json.dumps(
                {
                    **binding,
                    "fingerprint": {**fingerprint, "worklist": ["C1", "C1"]},
                }
            )
        )
    with pytest.raises(ValueError, match="historical fingerprint identity differs"):
        HistoricalMixedChainRunBinding.model_validate_json(json.dumps(binding))
    fingerprint_identity = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="historical materialized worklist differs"):
        HistoricalMixedChainRunBinding.model_validate_json(
            json.dumps(
                {
                    **binding,
                    "fingerprint_identity": fingerprint_identity,
                    "materialized_worklist": ["C2"],
                }
            )
        )
