"""Behavioral contracts for the collection-driven Python CI partitions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from scripts import run_ci_test_partitions as runner
from scripts.validation import test_partitions as partitions
from scripts.validation.test_partitions import (
    BACKEND_ALGORITHM_VERSION,
    INTEGRATION_ALGORITHM_VERSION,
    CollectionRecord,
    IntegrationClassification,
    IntegrationWeightEvidence,
    Lane,
    PartitionReceipt,
    ShardId,
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


def _record_with(
    path: str,
    *,
    markers: frozenset[str] = frozenset({"integration"}),
    fixtures: frozenset[str] = frozenset(),
) -> CollectionRecord:
    return CollectionRecord(
        nodeid=f"{path}::test_contract",
        path=path,
        markers=markers,
        fixtures=fixtures,
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
    records = (
        _record_with("tests/test_pg.py"),
        _record_with("tests/test_mixed.py"),
        _record_with("tests/test_nonmutating.py"),
    )

    classification = IntegrationClassification.from_collection(records, manifest)
    reordered = IntegrationClassification.from_collection(
        tuple(reversed(records)), manifest
    )

    assert classification.qlever_files == ("tests/test_mixed.py",)
    assert classification.non_qlever_files == (
        "tests/test_nonmutating.py",
        "tests/test_pg.py",
    )
    assert reordered == classification
    assert set(classification.qlever_files + classification.non_qlever_files) == {
        record.path for record in records
    }
    assert classification.evidence_sha256


def test_integration_weighted_partition_is_stable_balanced_and_file_level(
    tmp_path: Path,
) -> None:
    weights = tmp_path / "weights.toml"
    weights.write_text(
        """
schema_version = 1
measured_commit = "ee654e792b31789933a757092a47214a1226ff40"
measurement_date = "2026-09-05"
measurement_worktree_dirty = false
selected_count = 4
module_count = 3
measurement_command = "pdm run ci-test-measure-integration --output tmp/t.toml"
default_weight_seconds = 5.0

[weights]
"backend/tests/test_heavy.py" = 10.0
"backend/tests/test_medium.py" = 7.0
"backend/tests/test_light.py" = 3.0
""".lstrip()
    )
    records = _records(
        "backend/tests/test_new.py",
        "backend/tests/test_light.py",
        "backend/tests/test_heavy.py",
        "backend/tests/test_medium.py",
    )

    first = partitions.assign_integration_modules(records, weights, shard_index=0)
    second = partitions.assign_integration_modules(
        tuple(reversed(records)), weights, shard_index=1
    )

    assert first.selected_files == (
        "backend/tests/test_heavy.py",
        "backend/tests/test_light.py",
    )
    assert second.selected_files == (
        "backend/tests/test_medium.py",
        "backend/tests/test_new.py",
    )
    assert first.total_weight_seconds == 13.0
    assert second.total_weight_seconds == 12.0
    assert (
        first.unweighted_files
        == second.unweighted_files
        == ("backend/tests/test_new.py",)
    )
    assert first.weights_sha256 == second.weights_sha256
    assert set(first.selected_files).isdisjoint(second.selected_files)
    assert set(first.selected_files + second.selected_files) == {
        record.path for record in records
    }


def test_integration_weights_reject_stale_paths_and_multiple_unweighted_files(
    tmp_path: Path,
) -> None:
    weights = tmp_path / "weights.toml"
    weights.write_text(
        """
schema_version = 1
measured_commit = "ee654e792b31789933a757092a47214a1226ff40"
measurement_date = "2026-09-05"
measurement_worktree_dirty = false
selected_count = 1
module_count = 1
measurement_command = "pdm run ci-test-measure-integration --output tmp/t.toml"
default_weight_seconds = 5.0

[weights]
"backend/tests/test_stale.py" = 1.0
""".lstrip()
    )

    with pytest.raises(ValueError, match="stale integration weight"):
        partitions.assign_integration_modules(
            _records("backend/tests/test_current.py"), weights, shard_index=0
        )

    weights.write_text(weights.read_text().replace("test_stale", "test_current"))
    with pytest.raises(ValueError, match="more than one unweighted"):
        partitions.assign_integration_modules(
            _records(
                "backend/tests/test_current.py",
                "backend/tests/test_new_a.py",
                "backend/tests/test_new_b.py",
            ),
            weights,
            shard_index=0,
        )


def test_duration_capture_requires_clean_complete_calls_and_writes_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "integration-file-durations.toml"
    monkeypatch.setenv("ONTOPRISM_TEST_TIMINGS_OUTPUT", str(output))
    partitions.pytest_sessionstart(SimpleNamespace())
    for nodeid, when, duration in (
        ("tests/test_b.py::test_two", "setup", 2.0),
        ("tests/test_a.py::test_one", "call", 1.25),
        ("tests/test_b.py::test_two", "call", 3.0),
        ("tests/test_b.py::test_two", "teardown", 0.5),
    ):
        partitions.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid=nodeid,
                when=when,
                duration=duration,
                passed=True,
                wasxfail=None,
            )
        )
    monkeypatch.setattr(partitions, "_current_commit", lambda: "a" * 40)
    monkeypatch.setattr(partitions, "_measurement_date", lambda: "2026-09-05")
    monkeypatch.setattr(partitions, "_worktree_dirty", lambda: False)
    partitions._timing_selected_nodeids.update(
        {"tests/test_a.py::test_one", "tests/test_b.py::test_two"}
    )

    partitions.pytest_sessionfinish(SimpleNamespace(), 0)

    generated = tomllib.loads(output.read_text())
    assert generated["measured_commit"] == "a" * 40
    assert generated["measurement_date"] == "2026-09-05"
    assert generated["measurement_worktree_dirty"] is False
    assert generated["selected_count"] == 2
    assert generated["module_count"] == 2
    assert generated["measurement_command"].startswith(
        "pdm run ci-test-measure-integration --output "
    )
    assert generated["weights"] == {
        "tests/test_a.py": 1.25,
        "tests/test_b.py": 5.5,
    }
    assert generated["default_weight_seconds"] == 3.375


@pytest.mark.parametrize("outcome", ["skipped", "xfailed", "failed"])
def test_duration_capture_refuses_degraded_or_failed_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    output = tmp_path / "integration-file-durations.toml"
    monkeypatch.setenv("ONTOPRISM_TEST_TIMINGS_OUTPUT", str(output))
    monkeypatch.setattr(partitions, "_worktree_dirty", lambda: False)
    partitions.pytest_sessionstart(SimpleNamespace())
    partitions._timing_selected_nodeids.add("tests/test_a.py::test_one")
    partitions.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="tests/test_a.py::test_one",
            when="call",
            duration=1.0,
            passed=False,
            failed=outcome == "failed",
            skipped=outcome in {"skipped", "xfailed"},
            wasxfail="reason" if outcome == "xfailed" else None,
        )
    )

    with pytest.raises(RuntimeError, match="complete successful call"):
        partitions.pytest_sessionfinish(SimpleNamespace(), 0)
    assert not output.exists()


def test_every_integration_partition_preflights_both_external_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, str]] = []

    def reject_missing_tools(environment: dict[str, str]) -> dict[str, str]:
        calls.append(environment)
        raise RuntimeError("ROBOT and Jena are required")

    monkeypatch.setattr(runner, "_integration_tool_environment", reject_missing_tools)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("collection ran before tool preflight"),
    )

    for shard in ("0", "1"):
        with pytest.raises(RuntimeError, match="ROBOT and Jena"):
            runner.run_partition(
                "integration",
                shard,
                output_dir=tmp_path / f"partition-{shard}",
            )

    assert len(calls) == 2


def test_integration_tool_preflight_validates_pinned_installs_and_exports_robot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    robot = tmp_path / "robot"
    jena = tmp_path / "jena"
    observed: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        runner,
        "identify_robot_installation",
        lambda path: observed.append(("robot", path)),
    )
    monkeypatch.setattr(
        runner,
        "identify_jena_installation",
        lambda path: observed.append(("jena", path)),
    )

    environment = runner._integration_tool_environment(
        {
            "PATH": "/existing/bin",
            "ONTOPRISM_ROBOT_DIR": str(robot),
            "ONTOPRISM_JENA_DIR": str(jena),
        }
    )

    assert observed == [("robot", robot), ("jena", jena)]
    assert environment["PATH"] == f"{robot}{os.pathsep}/existing/bin"


def test_integration_classification_rejects_unsorted_overlap(
    tmp_path: Path,
) -> None:
    del tmp_path
    with pytest.raises(ValidationError, match=r"sorted|disjoint"):
        IntegrationClassification(
            manifest_sha256="a" * 64,
            qlever_files=("tests/z.py", "tests/a.py"),
            non_qlever_files=("tests/z.py",),
            evidence_sha256="b" * 64,
        )


def test_partition_specs_are_the_single_correlated_four_partition_contract() -> None:
    specs = partitions.PARTITION_SPECS

    assert tuple(
        (
            spec.lane,
            spec.shard_id,
            spec.shard_index,
            spec.shard_count,
            spec.layer,
            spec.coverage_stem,
            spec.artifact_name,
        )
        for spec in specs
    ) == (
        (
            "backend",
            "0",
            0,
            2,
            "python-unit-0",
            "unit-0",
            "coverage-backend-1",
        ),
        (
            "backend",
            "1",
            1,
            2,
            "python-unit-1",
            "unit-1",
            "coverage-backend-2",
        ),
        (
            "integration",
            "0",
            0,
            2,
            "python-integration-0",
            "integration-0",
            "coverage-integration-1",
        ),
        (
            "integration",
            "1",
            1,
            2,
            "python-integration-1",
            "integration-1",
            "coverage-integration-2",
        ),
    )
    with pytest.raises(ValidationError, match="correlation"):
        partitions.PartitionSpec(
            lane="backend",
            shard_id="1",
            shard_index=0,
            shard_count=2,
            layer="python-unit-0",
            coverage_stem="unit-0",
            artifact_name="coverage-backend-1",
        )


def test_runner_uses_exported_fixed_roots_and_rejects_checkout_outputs() -> None:
    command = runner._pytest_command("backend", collect_only=True, coverage_xml=None)
    assert command[1:3] == list(partitions.FIXED_TEST_ROOTS)
    with pytest.raises(ValueError, match="outside the checkout"):
        runner.run_partition(
            "backend",
            "0",
            output_dir=Path(__file__).resolve().parents[2] / "tmp/partition",
        )


def test_lane_selectors_match_eligibility_for_all_marker_combinations() -> None:
    marker_names = ("integration", "full_store", "full_build", "slow")
    marker_sets = tuple(
        frozenset(values)
        for count in range(len(marker_names) + 1)
        for values in combinations(marker_names, count)
    )
    for selector in partitions.LANE_SELECTORS:
        assert selector.marker_expression in runner._pytest_command(
            selector.lane, collect_only=True, coverage_xml=None
        )
        for markers in marker_sets:
            record = _record_with("tests/test.py", markers=markers)
            expected = (
                "integration" not in markers
                if selector.lane == "backend"
                else "integration" in markers
                and not markers.intersection({"full_store", "full_build", "slow"})
            )
            assert partitions._eligible(record, selector.lane) is expected


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"ONTOPRISM_TEST_PARTITION_PHASE": "other"}, "phase"),
        ({"ONTOPRISM_TEST_PARTITION_FIXED_ROOTS": "true"}, "FIXED_ROOTS"),
        ({"ONTOPRISM_TEST_PARTITION_FIXED_ROOTS": "2"}, "FIXED_ROOTS"),
        ({"ONTOPRISM_TEST_PARTITION_NESTED_BYPASS": "true"}, "NESTED_BYPASS"),
    ],
)
def test_partition_environment_rejects_invalid_phase_and_boolean(
    environment: dict[str, str], message: str
) -> None:
    values = {
        "ONTOPRISM_TEST_PARTITION_LANE": "backend",
        "ONTOPRISM_TEST_PARTITION_SHARD": "0",
        "ONTOPRISM_TEST_PARTITION_COUNT": "2",
        "ONTOPRISM_TEST_PARTITION_RECEIPT": "receipt.json",
        "ONTOPRISM_TEST_PARTITION_PHASE": "collect",
        "ONTOPRISM_TEST_PARTITION_FIXED_ROOTS": "0",
        **environment,
    }

    with pytest.raises(ValueError, match=message):
        partitions.PartitionEnvironment.from_environ(values)


def test_receipt_rejects_lane_shard_and_index_mismatch() -> None:
    full = ("tests/test_a.py::test_a",)

    with pytest.raises(ValueError, match="invalid fixed partition"):
        partitions.partition_spec("backend", "qlever")
    with pytest.raises(ValidationError, match="correlation"):
        _receipt(
            lane="backend",
            shard_id="0",
            shard_index=1,
            full=full,
            selected=full,
        )


def _receipt(
    *,
    lane: Lane,
    shard_id: ShardId,
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
    validate_partition_receipts(receipts, lane="backend")

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
            validate_partition_receipts(invalid, lane="backend")


def _run_plugin_suite(
    suite: Path,
    receipt: Path,
    *,
    phase: str,
    fixed_roots: str = "0",
    xdist: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - pinned environment executable
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.validation.test_partitions",
            str(suite),
            *(["-n", "2"] if xdist else []),
            "-q",
            *(["--collect-only"] if phase == "collect" else []),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "ONTOPRISM_TEST_PARTITION_LANE": "backend",
            "ONTOPRISM_TEST_PARTITION_SHARD": "0",
            "ONTOPRISM_TEST_PARTITION_COUNT": "2",
            "ONTOPRISM_TEST_PARTITION_RECEIPT": str(receipt),
            "ONTOPRISM_TEST_PARTITION_PHASE": phase,
            "ONTOPRISM_TEST_PARTITION_FIXED_ROOTS": fixed_roots,
        },
        check=False,
        capture_output=True,
        text=True,
    )


def test_execute_phase_requires_an_existing_receipt(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    for name in ("alpha", "beta", "gamma", "delta"):
        (suite / f"test_{name}.py").write_text("def test_one(): assert True\n")

    completed = _run_plugin_suite(
        suite, tmp_path / "missing.json", phase="execute", xdist=False
    )

    assert completed.returncode != 0
    assert "execute phase requires an existing partition receipt" in (
        completed.stdout + completed.stderr
    )


def test_fixed_root_collect_cannot_be_silently_suppressed(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_alpha.py").write_text("def test_one(): assert True\n")

    completed = _run_plugin_suite(
        suite, tmp_path / "receipt.json", phase="collect", fixed_roots="1"
    )

    assert completed.returncode != 0
    assert "fixed partition collect requires repository test roots" in (
        completed.stdout + completed.stderr
    )


def test_fixed_root_execute_cannot_be_silently_suppressed(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_alpha.py").write_text("def test_one(): assert True\n")

    completed = _run_plugin_suite(
        suite,
        tmp_path / "receipt.json",
        phase="execute",
        fixed_roots="1",
        xdist=False,
    )

    assert completed.returncode != 0
    assert "fixed partition execute requires repository test roots" in (
        completed.stdout + completed.stderr
    )


def test_execute_phase_rejects_a_different_valid_receipt(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    for name in ("alpha", "beta", "gamma", "delta"):
        (suite / f"test_{name}.py").write_text("def test_one(): assert True\n")
    receipt_path = tmp_path / "receipt.json"
    collected = _run_plugin_suite(suite, receipt_path, phase="collect", xdist=False)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    collected_receipt = partitions.load_receipt(receipt_path)
    different_receipt = build_receipt(
        lane=collected_receipt.lane,
        shard_id=collected_receipt.shard_id,
        shard_index=collected_receipt.shard_index,
        shard_count=collected_receipt.shard_count,
        full_inventory=collected_receipt.full_inventory,
        selected_nodeids=collected_receipt.full_inventory,
        classification=collected_receipt.integration_classification,
    )
    receipt_path.write_text(different_receipt.model_dump_json())

    executed = _run_plugin_suite(suite, receipt_path, phase="execute", xdist=False)

    assert executed.returncode != 0
    assert "execution collection differs from partition receipt" in (
        executed.stdout + executed.stderr
    )


def test_real_pytest_xdist_workers_collect_the_same_file_level_shard(
    tmp_path: Path,
) -> None:
    """Collect then execute the receipt-bound whole-file shard with pinned xdist."""
    suite = tmp_path / "suite"
    suite.mkdir()
    for name in ("alpha", "beta", "gamma", "delta"):
        (suite / f"test_{name}.py").write_text(
            "def test_one(): assert True\ndef test_two(): assert True\n"
        )
    receipt = tmp_path / "receipt.json"
    receipt.write_text("stale receipt")
    collected = _run_plugin_suite(suite, receipt, phase="collect")
    assert collected.returncode == 0, collected.stdout + collected.stderr
    receipt.write_text(
        json.dumps(json.loads(receipt.read_text()), separators=(",", ":"))
    )
    collected_receipt = receipt.read_bytes()

    completed = _run_plugin_suite(suite, receipt, phase="execute")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "different tests were collected" not in completed.stdout
    assert receipt.read_bytes() == collected_receipt
    selected = json.loads(receipt.read_text())["selected_nodeids"]
    selected_files = {nodeid.partition("::")[0] for nodeid in selected}
    assert selected_files
    assert all(
        sum(nodeid.startswith(f"{path}::") for nodeid in selected) == 2
        for path in selected_files
    )
    assert BACKEND_ALGORITHM_VERSION == "sha256-mod-v1"
    assert INTEGRATION_ALGORITHM_VERSION == "greedy-weighted-lpt-v1"


def test_artifact_validation_rejects_missing_unexpected_and_non_file_entries(
    tmp_path: Path,
) -> None:
    expected = {
        "coverage-backend-1": {
            ".coverage.unit-0",
            "coverage-unit-0.identity.json",
            "coverage-unit-0.xml",
            "partition-backend-0.json",
        },
        "coverage-backend-2": {
            ".coverage.unit-1",
            "coverage-unit-1.identity.json",
            "coverage-unit-1.xml",
            "partition-backend-1.json",
        },
        "coverage-integration-1": {
            ".coverage.integration-0",
            "coverage-integration-0.identity.json",
            "partition-integration-0.json",
        },
        "coverage-integration-2": {
            ".coverage.integration-1",
            "coverage-integration-1.identity.json",
            "partition-integration-1.json",
        },
    }
    for directory, names in expected.items():
        artifact = tmp_path / directory
        artifact.mkdir()
        for name in names:
            (artifact / name).write_text("evidence")

    partitions.validate_artifacts(
        tmp_path, validate_receipts=False, validate_identities=False
    )
    with pytest.raises(ValidationError):
        partitions.validate_artifacts(tmp_path)

    missing = tmp_path / "coverage-backend-1" / ".coverage.unit-0"
    missing.unlink()
    with pytest.raises(ValueError, match="files mismatch"):
        partitions.validate_artifacts(
            tmp_path, validate_receipts=False, validate_identities=False
        )
    missing.write_text("evidence")
    unexpected = tmp_path / "coverage-extra"
    unexpected.mkdir()
    with pytest.raises(ValueError, match="unexpected coverage artifact"):
        partitions.validate_artifacts(
            tmp_path, validate_receipts=False, validate_identities=False
        )
    unexpected.rmdir()
    missing.unlink()
    missing.mkdir()
    with pytest.raises(ValueError, match="files mismatch"):
        partitions.validate_artifacts(
            tmp_path, validate_receipts=False, validate_identities=False
        )


def test_artifact_validation_loads_and_aggregates_receipt_files(tmp_path: Path) -> None:
    classification = IntegrationClassification(
        manifest_sha256="a" * 64,
        qlever_files=("tests/test_qlever.py",),
        non_qlever_files=("tests/test_postgres.py",),
        evidence_sha256="b" * 64,
    )
    backend_full = ("tests/test_a.py::test_a", "tests/test_b.py::test_b")
    integration_full = (
        "tests/test_postgres.py::test_contract",
        "tests/test_qlever.py::test_contract",
    )
    for spec in partitions.PARTITION_SPECS:
        artifact = tmp_path / spec.artifact_name
        artifact.mkdir()
        for name in spec.artifact_file_names:
            (artifact / name).write_text("")
        selected = (
            backend_full[spec.shard_index : spec.shard_index + 1]
            if spec.lane == "backend"
            else integration_full[spec.shard_index : spec.shard_index + 1]
        )
        receipt = build_receipt(
            lane=spec.lane,
            shard_id=spec.shard_id,
            shard_index=spec.shard_index,
            shard_count=spec.shard_count,
            full_inventory=(
                backend_full if spec.lane == "backend" else integration_full
            ),
            selected_nodeids=selected,
            classification=classification if spec.lane == "integration" else None,
            integration_weight_evidence=(
                IntegrationWeightEvidence(
                    weights_sha256="c" * 64,
                    default_weight_seconds=5.0,
                    unweighted_files=(),
                    selected_weight_seconds=1.0,
                )
                if spec.lane == "integration"
                else None
            ),
        )
        (artifact / spec.receipt_name).write_text(receipt.model_dump_json())
        (artifact / spec.identity_name).write_text(json.dumps({"layer": spec.layer}))

    partitions.validate_artifacts(tmp_path)

    receipt_path = tmp_path / "coverage-backend-2" / "partition-backend-1.json"
    receipt_path.write_text(
        receipt_path.read_text().replace('"shard_id":"1"', '"shard_id":"0"')
    )
    with pytest.raises((ValidationError, ValueError), match=r"correlation|shard"):
        partitions.validate_artifacts(tmp_path)


def test_identity_paths_require_their_exact_partition_layers() -> None:
    identities = tuple(
        runner.ArtifactIdentity(
            schema_version=1,
            commit="a" * 40,
            config_sha256="b" * 64,
            manifest_sha256="c" * 64,
            tool="coverage.py",
            tool_version="7.16.0",
            layer=spec.layer,
            source_sha256="d" * 64,
        )
        for spec in partitions.PARTITION_SPECS
    )
    paths = tuple(
        Path(spec.artifact_name) / spec.identity_name
        for spec in partitions.PARTITION_SPECS
    )

    runner.validate_identity_layers(paths, identities)
    swapped = (identities[1], identities[0], *identities[2:])
    with pytest.raises(ValueError, match="identity layer mismatch"):
        runner.validate_identity_layers(paths, swapped)
    with pytest.raises(ValueError, match="identity path set"):
        runner.validate_identity_layers(paths[:-1], identities[:-1])
    with pytest.raises(ValueError, match="identity path set"):
        runner.validate_identity_layers((paths[0], paths[0], *paths[2:]), identities)


def test_local_identity_validation_rejects_source_mismatch() -> None:
    identities = tuple(
        runner.ArtifactIdentity(
            schema_version=1,
            commit="a" * 40,
            config_sha256="b" * 64,
            manifest_sha256="c" * 64,
            tool="coverage.py",
            tool_version="7.16.0",
            layer=spec.layer,
            source_sha256="d" * 64,
            worktree_dirty=True,
        )
        for spec in partitions.PARTITION_SPECS
    )
    incompatible = (
        *identities[:-1],
        identities[-1].model_copy(update={"source_sha256": "e" * 64}),
    )

    runner.validate_local_identities(
        tuple(
            Path(spec.artifact_name) / spec.identity_name
            for spec in partitions.PARTITION_SPECS
        ),
        identities,
    )
    with pytest.raises(ValueError, match="source_sha256"):
        runner.validate_local_identities(
            tuple(
                Path(spec.artifact_name) / spec.identity_name
                for spec in partitions.PARTITION_SPECS
            ),
            incompatible,
        )


def test_run_all_removes_stale_root_coverage_and_uses_exact_combine_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / ".coverage"
    stale.write_text("prior success")
    commands: list[list[str]] = []

    def fake_partition(
        lane: Lane,
        shard: ShardId,
        *,
        output_dir: Path,
    ) -> float:
        spec = partitions.partition_spec(lane, shard)
        coverage_file = output_dir / spec.coverage_name
        coverage_file.parent.mkdir(parents=True, exist_ok=True)
        coverage_file.write_text("coverage")
        return 0.1

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "combine" in command:
            data_argument = next(
                value for value in command if value.startswith("--data-file=")
            )
            Path(data_argument.partition("=")[2]).write_text("combined")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "run_partition", fake_partition)
    monkeypatch.setattr(
        runner,
        "_validate_local_outputs",
        lambda _output: tuple(
            SimpleNamespace(layer=spec.layer) for spec in partitions.PARTITION_SPECS
        ),
    )
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runner,
        "load_receipt",
        lambda _path: SimpleNamespace(
            selected_nodeids=("a.py::test",), selected_count=1
        ),
    )

    assert runner.run_all() == 0

    combine = next(command for command in commands if "combine" in command)
    assert combine[3:5] == ["combine", "--keep"]
    assert [Path(path).name for path in combine[-4:]] == [
        ".coverage.unit-0",
        ".coverage.unit-1",
        ".coverage.integration-0",
        ".coverage.integration-1",
    ]
    report = next(command for command in commands if "python-report" in command)
    identity_index = report.index("--identity") + 1
    assert Path(report[identity_index]).parts[-2:] == (
        "coverage-backend-1",
        "coverage-unit-0.identity.json",
    )
    assert stale.read_text() == "combined"


def test_run_all_failure_leaves_no_stale_root_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / ".coverage"
    stale.write_text("prior success")
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    def fail_partition(*_args: object, **_kwargs: object) -> float:
        raise subprocess.CalledProcessError(1, ["partition"])

    monkeypatch.setattr(runner, "run_partition", fail_partition)

    with pytest.raises(subprocess.CalledProcessError):
        runner.run_all()
    assert not stale.exists()
