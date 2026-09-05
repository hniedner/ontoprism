#!/usr/bin/env python3
"""Run fixed Python CI partitions or the sequential local parity gate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validation.coverage_hierarchy import (  # noqa: E402
    ArtifactIdentity,
    load_manifest,
    make_identity,
    verify_identities,
    verify_layer_set,
)
from scripts.validation.test_partitions import (  # noqa: E402
    load_receipt,
    validate_partition_receipts,
)

EXPECTED_LAYERS = (
    "python-unit-0",
    "python-unit-1",
    "python-integration-qlever",
    "python-integration-postgres",
)
_PARTITIONS = (
    ("backend", "0"),
    ("backend", "1"),
    ("integration", "qlever"),
    ("integration", "postgres"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    partition = subparsers.add_parser("partition")
    partition.add_argument("--lane", required=True, choices=("backend", "integration"))
    partition.add_argument("--shard", required=True)
    partition.add_argument("--receipt", type=Path, required=True)
    partition.add_argument("--coverage-file", type=Path, required=True)
    partition.add_argument("--coverage-xml", type=Path)
    partition.add_argument("--identity", type=Path, required=True)
    subparsers.add_parser("all")
    return parser


def _partition_contract(lane: str, shard: str) -> tuple[int, str, str]:
    if lane == "backend" and shard in {"0", "1"}:
        return int(shard), f"python-unit-{shard}", f"unit-{shard}"
    if lane == "integration" and shard in {"qlever", "postgres"}:
        return (
            0 if shard == "qlever" else 1,
            f"python-integration-{shard}",
            f"integration-{shard}",
        )
    raise ValueError(f"invalid fixed partition {lane}/{shard}")


def _pytest_command(
    lane: str, *, collect_only: bool, coverage_xml: Path | None
) -> list[str]:
    pytest = shutil.which("pytest")
    if pytest is None:
        raise RuntimeError("pytest console script is required")
    marker = (
        "not integration"
        if lane == "backend"
        else "integration and not full_store and not full_build and not slow"
    )
    arguments = ["ontolib/tests", "backend/tests", "-m", marker]
    if collect_only:
        arguments.append("--collect-only")
    else:
        arguments.extend(
            [
                "--cov=ontolib/src",
                "--cov=backend/src",
                "--cov-branch",
                "--cov-report=term-missing" if lane == "backend" else "--cov-report=",
            ]
        )
        if coverage_xml is not None:
            arguments.append(f"--cov-report=xml:{coverage_xml}")
        if lane == "backend":
            arguments.extend(("-n", "auto"))
    if lane == "integration":
        return [
            sys.executable,
            str(ROOT / "scripts/run_safe_integration.py"),
            *arguments,
        ]
    return [pytest, *arguments]


def _write_identity(path: Path, layer: str) -> ArtifactIdentity:
    import coverage  # noqa: PLC0415

    manifest = load_manifest(ROOT / "coverage-surfaces.toml", ROOT)
    prior_config_set = os.environ.get("COVERAGE_CONFIG_SET")
    os.environ["COVERAGE_CONFIG_SET"] = "python-combined"
    try:
        identity = make_identity(
            manifest,
            layer=layer,
            tool="coverage.py",
            tool_version=coverage.__version__,
            root=ROOT,
        )
    finally:
        if prior_config_set is None:
            os.environ.pop("COVERAGE_CONFIG_SET", None)
        else:
            os.environ["COVERAGE_CONFIG_SET"] = prior_config_set
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(identity.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return identity


def run_partition(
    lane: str,
    shard: str,
    *,
    receipt: Path,
    coverage_file: Path,
    coverage_xml: Path | None,
    identity: Path,
) -> float:
    """Collect, receipt, then execute one validated fixed partition."""
    _index, layer, _stem = _partition_contract(lane, shard)
    for output in (receipt, coverage_file, identity, coverage_xml):
        if output is not None and output.exists():
            output.unlink()
    environment = {
        **os.environ,
        "ONTOPRISM_TEST_PARTITION_LANE": lane,
        "ONTOPRISM_TEST_PARTITION_SHARD": shard,
        "ONTOPRISM_TEST_PARTITION_COUNT": "2",
        "ONTOPRISM_TEST_PARTITION_RECEIPT": str(receipt.resolve()),
        "ONTOPRISM_TEST_PARTITION_PHASE": "collect",
        "ONTOPRISM_TEST_PARTITION_FIXED_ROOTS": "1",
    }
    started = time.monotonic()
    subprocess.run(  # noqa: S603 - fixed repository test command
        _pytest_command(lane, collect_only=True, coverage_xml=None),
        cwd=ROOT,
        env=environment,
        check=True,
    )
    load_receipt(receipt)
    execution_environment = {
        **environment,
        "ONTOPRISM_TEST_PARTITION_PHASE": "execute",
        "COVERAGE_FILE": str(coverage_file.resolve()),
    }
    subprocess.run(  # noqa: S603 - fixed repository test command
        _pytest_command(lane, collect_only=False, coverage_xml=coverage_xml),
        cwd=ROOT,
        env=execution_environment,
        check=True,
    )
    _write_identity(identity, layer)
    return time.monotonic() - started


def _validate_local_outputs(output: Path) -> tuple[ArtifactIdentity, ...]:
    backend_receipts = tuple(
        load_receipt(output / f"partition-backend-{shard}.json") for shard in ("0", "1")
    )
    integration_receipts = tuple(
        load_receipt(output / f"partition-integration-{shard}.json")
        for shard in ("qlever", "postgres")
    )
    validate_partition_receipts(
        backend_receipts, lane="backend", expected_shards=("0", "1")
    )
    validate_partition_receipts(
        integration_receipts,
        lane="integration",
        expected_shards=("qlever", "postgres"),
    )
    identities = tuple(
        ArtifactIdentity.model_validate_json(
            (output / f"coverage-{lane}-{shard}.identity.json").read_text()
        )
        for lane, shard in _PARTITIONS
    )
    verify_layer_set(identities, EXPECTED_LAYERS)
    # Local TDD runs before commit. Exact source/config hashes still bind all
    # four same-process artifacts; only CI requires the collected checkout to be clean.
    verify_identities(
        tuple(
            identity.model_copy(update={"worktree_dirty": False})
            for identity in identities
        )
    )
    return identities


def run_all() -> int:
    """Run CI's children sequentially; parity is semantic, not a speed claim."""
    with tempfile.TemporaryDirectory(prefix="ontoprism-python-ci-") as directory:
        output = Path(directory)
        durations: dict[str, float] = {}
        coverage_paths: list[Path] = []
        for lane, shard in _PARTITIONS:
            coverage_file = output / f".coverage.{lane}-{shard}"
            coverage_paths.append(coverage_file)
            durations[f"{lane}/{shard}"] = run_partition(
                lane,
                shard,
                receipt=output / f"partition-{lane}-{shard}.json",
                coverage_file=coverage_file,
                coverage_xml=(
                    output / f"coverage-unit-{shard}.xml" if lane == "backend" else None
                ),
                identity=output / f"coverage-{lane}-{shard}.identity.json",
            )
        identities = _validate_local_outputs(output)
        combined = output / ".coverage"
        # Exact local equivalent of `coverage combine --keep <four paths>` in CI.
        subprocess.run(  # noqa: S603 - fixed coverage command
            [
                sys.executable,
                "-m",
                "coverage",
                "combine",
                "--keep",
                f"--data-file={combined}",
                *(str(path) for path in coverage_paths),
            ],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(  # noqa: S603 - fixed repository validation command
            [
                sys.executable,
                str(ROOT / "scripts/validation/strict_coverage_gate.py"),
                "python",
                "--coverage-data",
                str(combined),
                "--root",
                str(ROOT),
            ],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(  # noqa: S603 - fixed repository report command
            [
                sys.executable,
                str(ROOT / "scripts/validation/coverage_hierarchy.py"),
                "python-report",
                "--coverage-data",
                str(combined),
                "--identity",
                str(output / "coverage-backend-0.identity.json"),
                "--raw-output",
                str(output / "python-native.json"),
                "--output",
                str(output / "python-hierarchy.json"),
                "--text-output",
                str(output / "python-hierarchy.txt"),
            ],
            cwd=ROOT,
            check=True,
        )
        shutil.copyfile(combined, ROOT / ".coverage")
        print("partition inventory and durations:")
        for (lane, shard), identity in zip(_PARTITIONS, identities, strict=True):
            receipt = load_receipt(output / f"partition-{lane}-{shard}.json")
            modules = {nodeid.partition("::")[0] for nodeid in receipt.selected_nodeids}
            print(
                f"  {lane}/{shard}: {receipt.selected_count} nodeids, "
                f"{len(modules)} modules, {durations[f'{lane}/{shard}']:.1f}s, "
                f"layer={identity.layer}"
            )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "all":
        return run_all()
    run_partition(
        args.lane,
        args.shard,
        receipt=args.receipt,
        coverage_file=args.coverage_file,
        coverage_xml=args.coverage_xml,
        identity=args.identity,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
