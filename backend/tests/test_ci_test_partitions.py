"""Behavioral contracts for the collection-driven Python CI partitions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.validation.test_partitions import (
    ALGORITHM_VERSION,
    CollectionRecord,
    IntegrationClassification,
    PartitionReceipt,
    assign_backend_modules,
    build_receipt,
    validate_partition_receipts,
)


def _records(*paths: str) -> tuple[CollectionRecord, ...]:
    return tuple(
        CollectionRecord(
            nodeid=f"{path}::test_contract",
            path=path,
            markers=frozenset({"integration"}),
            fixtures=frozenset(),
        )
        for path in paths
    )


def test_backend_sha256_module_partition_is_stable_disjoint_and_complete() -> None:
    nodeids = (
        "backend/tests/test_alpha.py::test_a",
        "backend/tests/test_alpha.py::test_b",
        "ontolib/tests/test_beta.py::test_c",
        "backend/tests/test_gamma.py::test_d[param]",
        "ontolib/tests/test_delta.py::test_e",
    )

    first = assign_backend_modules(nodeids, shard_index=0, shard_count=2)
    second = assign_backend_modules(
        tuple(reversed(nodeids)), shard_index=1, shard_count=2
    )

    assert first
    assert second
    assert set(first).isdisjoint(second)
    assert set(first) | set(second) == set(nodeids)
    assert {nodeid.partition("::")[0] for nodeid in first}.isdisjoint(
        nodeid.partition("::")[0] for nodeid in second
    )
    assert first == tuple(sorted(first))
    assert second == tuple(sorted(second))


def test_backend_partition_rejects_unsupported_count_index_and_empty_shard() -> None:
    with pytest.raises(ValueError, match="exactly two"):
        assign_backend_modules(("tests/test_a.py::test_a",), 0, 3)
    with pytest.raises(ValueError, match="shard index"):
        assign_backend_modules(("tests/test_a.py::test_a",), 2, 2)
    with pytest.raises(ValueError, match="empty backend shard"):
        assign_backend_modules(("tests/test_a.py::test_a",), 1, 2)


def test_integration_boundary_uses_manifest_declarations_and_collected_inventory(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "integration_mutators.toml"
    manifest.write_text(
        """
[[mutator]]
path = "tests/test_mixed.py"
fixtures = ["isolated_postgres_settings", "isolated_qlever_settings"]

[[mutator]]
path = "tests/test_pg.py"
fixtures = ["isolated_postgres_settings"]
""".lstrip()
    )
    records = _records(
        "tests/test_pg.py", "tests/test_mixed.py", "tests/test_nonmutating.py"
    )

    classification = IntegrationClassification.from_collection(records, manifest)

    assert classification.qlever_files == ("tests/test_mixed.py",)
    assert classification.postgres_files == (
        "tests/test_nonmutating.py",
        "tests/test_pg.py",
    )
    assert classification.qlever_files + classification.postgres_files != tuple(
        record.path for record in records
    )  # classification is deterministic, not collection-order dependent
    assert set(classification.qlever_files + classification.postgres_files) == {
        record.path for record in records
    }
    assert classification.evidence_sha256


def _receipt(
    *,
    lane: str,
    shard_id: str,
    shard_index: int,
    full: tuple[str, ...],
    selected: tuple[str, ...],
) -> PartitionReceipt:
    return build_receipt(
        lane=lane,
        shard_id=shard_id,
        shard_index=shard_index,
        shard_count=2,
        full_inventory=full,
        selected_nodeids=selected,
        classification=None,
    )


def test_receipt_aggregate_requires_exact_indices_digests_disjoint_union() -> None:
    full = ("tests/test_a.py::test_a", "tests/test_b.py::test_b")
    receipts = (
        _receipt(
            lane="backend", shard_id="0", shard_index=0, full=full, selected=full[:1]
        ),
        _receipt(
            lane="backend", shard_id="1", shard_index=1, full=full, selected=full[1:]
        ),
    )
    validate_partition_receipts(receipts, lane="backend", expected_shards=("0", "1"))

    mutations = (
        receipts[:1],
        (receipts[0], receipts[0]),
        (receipts[0], receipts[1].model_copy(update={"selected_nodeids": full[:1]})),
        (
            receipts[0],
            receipts[1].model_copy(update={"full_inventory_sha256": "0" * 64}),
        ),
        (receipts[0], receipts[1].model_copy(update={"shard_index": 0})),
    )
    for invalid in mutations:
        with pytest.raises(
            ValueError, match=r"partition|shard|inventory|overlap|indices"
        ):
            validate_partition_receipts(
                invalid, lane="backend", expected_shards=("0", "1")
            )


def test_real_pytest_xdist_workers_collect_the_same_file_level_shard(
    tmp_path: Path,
) -> None:
    """Pinned pytest+xdist rejects divergent collections and executes one whole file."""
    suite = tmp_path / "suite"
    suite.mkdir()
    for name in ("alpha", "beta", "gamma", "delta"):
        (suite / f"test_{name}.py").write_text(
            "def test_one(): assert True\ndef test_two(): assert True\n"
        )
    receipt = tmp_path / "receipt.json"
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "ONTOPRISM_TEST_PARTITION_LANE": "backend",
        "ONTOPRISM_TEST_PARTITION_SHARD": "0",
        "ONTOPRISM_TEST_PARTITION_COUNT": "2",
        "ONTOPRISM_TEST_PARTITION_RECEIPT": str(receipt),
        "ONTOPRISM_TEST_PARTITION_PHASE": "execute",
        "ONTOPRISM_TEST_PARTITION_FIXED_ROOTS": "0",
    }
    completed = subprocess.run(  # noqa: S603 - pinned environment executable
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.validation.test_partitions",
            str(suite),
            "-n",
            "2",
            "-q",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "different tests were collected" not in completed.stdout
    selected = json.loads(receipt.read_text())["selected_nodeids"]
    selected_files = {nodeid.partition("::")[0] for nodeid in selected}
    assert selected_files
    assert all(
        sum(nodeid.startswith(f"{path}::") for nodeid in selected) == 2
        for path in selected_files
    )
    assert ALGORITHM_VERSION == "sha256-mod-v1"
