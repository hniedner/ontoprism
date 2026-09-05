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
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validation.coverage_hierarchy import (  # noqa: E402
    ArtifactIdentity,
    load_manifest,
    make_identity,
    verify_identities,
)
from scripts.validation.test_partitions import (  # noqa: E402
    LANE_SELECTORS,
    PARTITION_SPECS,
    Lane,
    PartitionSpec,
    load_receipt,
    partition_spec,
    validate_artifacts,
)

EXPECTED_LAYERS = tuple(spec.layer for spec in PARTITION_SPECS)


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


def _partition_contract(lane: str, shard: str) -> PartitionSpec:
    if lane not in {"backend", "integration"}:
        raise ValueError(f"invalid fixed partition {lane}/{shard}")
    return partition_spec(cast("Lane", lane), shard)


def _pytest_command(
    lane: str, *, collect_only: bool, coverage_xml: Path | None
) -> list[str]:
    pytest = shutil.which("pytest")
    if pytest is None:
        raise RuntimeError("pytest console script is required")
    selector = next(selector for selector in LANE_SELECTORS if selector.lane == lane)
    marker = selector.marker_expression
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
    spec = _partition_contract(lane, shard)
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
    _write_identity(identity, spec.layer)
    return time.monotonic() - started


def validate_identity_layers(
    paths: Sequence[Path], identities: Sequence[ArtifactIdentity]
) -> None:
    """Bind every identity path to the exact layer assigned to that artifact."""
    actual = tuple(
        ("/".join(path.parts[-2:]), identity.layer)
        for path, identity in zip(paths, identities, strict=True)
    )
    expected = tuple(
        (f"{spec.artifact_name}/{spec.identity_name}", layer)
        for spec, layer in zip(PARTITION_SPECS, EXPECTED_LAYERS, strict=True)
    )
    if len(paths) != len(set(paths)) or {path for path, _layer in actual} != {
        path for path, _layer in expected
    }:
        raise ValueError("coverage identity path set is not exact")
    expected_layers = dict(expected)
    for path, layer in actual:
        if expected_layers[path] != layer:
            raise ValueError(f"coverage identity layer mismatch for {path}")


def validate_local_identities(
    paths: Sequence[Path], identities: Sequence[ArtifactIdentity]
) -> None:
    validate_identity_layers(paths, identities)
    # Local TDD runs before commit. Source/config hashes still bind all four artifacts;
    # only CI requires the checkout itself to be clean while they are collected.
    verify_identities(
        tuple(
            identity.model_copy(update={"worktree_dirty": False})
            for identity in identities
        )
    )


def _validate_local_outputs(output: Path) -> tuple[ArtifactIdentity, ...]:
    validate_artifacts(output)
    identity_paths = tuple(
        output / spec.artifact_name / spec.identity_name for spec in PARTITION_SPECS
    )
    identities = tuple(
        ArtifactIdentity.model_validate_json(path.read_text())
        for path in identity_paths
    )
    validate_local_identities(identity_paths, identities)
    return identities


def run_all() -> int:
    """Run CI's children sequentially; parity is semantic, not a speed claim."""
    root_coverage = ROOT / ".coverage"
    pending_coverage = ROOT / ".coverage.pending"
    root_coverage.unlink(missing_ok=True)
    pending_coverage.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="ontoprism-python-ci-") as directory:
        output = Path(directory)
        durations: dict[str, float] = {}
        coverage_paths: list[Path] = []
        for spec in PARTITION_SPECS:
            artifact = output / spec.artifact_name
            artifact.mkdir()
            coverage_file = artifact / spec.coverage_name
            coverage_paths.append(coverage_file)
            durations[f"{spec.lane}/{spec.shard_id}"] = run_partition(
                spec.lane,
                spec.shard_id,
                receipt=artifact / spec.receipt_name,
                coverage_file=coverage_file,
                coverage_xml=(
                    artifact / spec.coverage_xml_name
                    if spec.coverage_xml_name is not None
                    else None
                ),
                identity=artifact / spec.identity_name,
            )
        identities = _validate_local_outputs(output)
        combined = output / ".coverage"
        # Local equivalent of CI's explicit four-path coverage combine.
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
                str(
                    output
                    / PARTITION_SPECS[0].artifact_name
                    / PARTITION_SPECS[0].identity_name
                ),
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
        print("partition inventory and durations:")
        for spec, identity in zip(PARTITION_SPECS, identities, strict=True):
            receipt = load_receipt(output / spec.artifact_name / spec.receipt_name)
            modules = {nodeid.partition("::")[0] for nodeid in receipt.selected_nodeids}
            print(
                f"  {spec.lane}/{spec.shard_id}: {receipt.selected_count} nodeids, "
                f"{len(modules)} modules, "
                f"{durations[f'{spec.lane}/{spec.shard_id}']:.1f}s, "
                f"layer={identity.layer}"
            )
        try:
            shutil.copyfile(combined, pending_coverage)
            pending_coverage.replace(root_coverage)
        finally:
            pending_coverage.unlink(missing_ok=True)
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
