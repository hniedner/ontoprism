"""Read-only inventory for local ignored data and immutable artifact records."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from ontolib.decomposition.run_artifacts import ArtifactManifest, load_artifact_record


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory", help="report local artifacts without mutation")
    return result


def _usage(path: Path) -> dict[str, int | bool]:
    logical = allocated = files = 0
    if not path.exists():
        return {
            "available": False,
            "logical_bytes": 0,
            "allocated_bytes": 0,
            "files": 0,
        }
    for root, directories, names in os.walk(path, followlinks=False):
        directories[:] = [
            name for name in directories if not (Path(root) / name).is_symlink()
        ]
        for name in names:
            details = (Path(root) / name).lstat()
            if stat.S_ISREG(details.st_mode):
                logical += details.st_size
                allocated += details.st_blocks * 512
                files += 1
    return {
        "available": True,
        "logical_bytes": logical,
        "allocated_bytes": allocated,
        "files": files,
    }


def _default_worktrees(root: Path) -> tuple[Path, ...]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required for artifact inventory")
    result = subprocess.run(  # noqa: S603
        [git, "worktree", "list", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(
        Path(line.removeprefix("worktree "))
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    )


def _default_compose_resources(root: Path) -> tuple[dict[str, str], ...]:
    docker = shutil.which("docker")
    if docker is None:
        return ()
    commands = (
        (
            "container",
            [
                docker,
                "ps",
                "-a",
                "--filter",
                "label=com.docker.compose.project=ontoprism-podman-poc",
                "--format",
                "{{.Names}}",
            ],
        ),
        (
            "volume",
            [
                docker,
                "volume",
                "ls",
                "--filter",
                "label=com.docker.compose.project=ontoprism-podman-poc",
                "--format",
                "{{.Name}}",
            ],
        ),
    )
    resources: list[dict[str, str]] = []
    for kind, command in commands:
        result = subprocess.run(  # noqa: S603
            command, cwd=root, check=False, capture_output=True, text=True
        )
        if result.returncode == 0:
            resources.extend(
                {"kind": kind, "name": name, "project": "ontoprism-podman-poc"}
                for name in result.stdout.splitlines()
                if name
            )
    return tuple(resources)


def _manifest_artifacts(
    root: Path, manifest_path: Path, record: ArtifactManifest
) -> tuple[list[dict[str, object]], set[Path]]:
    legacy_in_place = record.family == "legacy-in-place"
    artifacts: list[dict[str, object]] = []
    managed_paths: set[Path] = set()
    for artifact in record.artifact_records:
        artifact_path = (
            root / artifact.relative_path
            if legacy_in_place
            else manifest_path.parent / artifact.relative_path
        )
        managed_paths.add(artifact_path)
        try:
            details = artifact_path.lstat()
        except OSError:
            availability = "missing"
            size = None
        else:
            size = details.st_size
            if not stat.S_ISREG(details.st_mode):
                availability = "not-regular"
            elif size != artifact.size:
                availability = "size-differs"
            else:
                availability = "size-matches-manifest"
        artifacts.append(
            {
                "path": artifact.relative_path,
                "availability": availability,
                "size": size,
            }
        )
    return artifacts, managed_paths


def _managed_entry(
    root: Path, path: Path, record: ArtifactManifest
) -> tuple[dict[str, object], set[Path]]:
    artifacts, managed_paths = _manifest_artifacts(root, path, record)
    artifacts_available = all(
        item["availability"] == "size-matches-manifest" for item in artifacts
    )
    completion_present = (
        record.family == "legacy-in-place" or (path.parent / ".complete").is_file()
    )
    return (
        {
            "path": path.relative_to(root).as_posix(),
            "record_type": record.record_type,
            "availability": (
                "complete" if artifacts_available and completion_present else "partial"
            ),
            "manifest_identity": record.manifest_identity,
            "retention_class": record.retention.retention_class,
            "references": [parent.manifest_identity for parent in record.parents],
            "artifacts": artifacts,
        },
        managed_paths,
    )


def _partial_generation_entries(
    root: Path, artifacts_root: Path
) -> list[dict[str, object]]:
    generations_root = artifacts_root / "generations"
    if not generations_root.is_dir():
        return []
    entries: list[dict[str, object]] = []
    for family in sorted(generations_root.iterdir()):
        if not family.is_dir() or family.name.startswith("."):
            continue
        for generation in sorted(family.iterdir()):
            if (
                generation.is_dir()
                and not generation.name.startswith(".")
                and not (generation / "manifest.json").is_file()
            ):
                entries.append(
                    {
                        "path": generation.relative_to(root).as_posix(),
                        "record_type": "artifact-generation",
                        "availability": "partial",
                    }
                )
    return entries


def inventory_repository(
    root: Path,
    *,
    git_worktrees: tuple[Path, ...] | None = None,
    compose_resources: tuple[dict[str, str], ...] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    artifacts_root = root / "tmp/artifacts/v1"
    managed: list[dict[str, object]] = []
    managed_paths: set[Path] = set()
    if artifacts_root.exists():
        for path in sorted(artifacts_root.rglob("*.json")):
            try:
                record = load_artifact_record(path)
            except ValueError as exc:
                managed.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "availability": "invalid",
                        "error": str(exc),
                    }
                )
                continue
            entry: dict[str, object] = {
                "path": path.relative_to(root).as_posix(),
                "record_type": record.record_type,
                "availability": "unavailable",
            }
            if isinstance(record, ArtifactManifest):
                entry, paths = _managed_entry(root, path, record)
                managed_paths.update(paths)
            else:
                entry.update(
                    {
                        "run_id": record.run_id,
                        "expected_sha256": record.expected_sha256,
                        "references": list(record.references),
                    }
                )
            managed.append(entry)
        managed.extend(_partial_generation_entries(root, artifacts_root))
    unknown: list[str] = []
    for base_name in ("tmp", "data"):
        base = root / base_name
        if base.exists():
            for path in sorted(base.rglob("*")):
                if (
                    path.is_file()
                    and path not in managed_paths
                    and artifacts_root not in path.parents
                ):
                    unknown.append(path.relative_to(root).as_posix())
    worktrees = git_worktrees if git_worktrees is not None else _default_worktrees(root)
    resources = (
        compose_resources
        if compose_resources is not None
        else _default_compose_resources(root)
    )
    return {
        "mode": "read-only-inventory",
        "ignored_usage": {name: _usage(root / name) for name in ("tmp", "data")},
        "registered_worktrees": [str(path) for path in worktrees],
        "managed_records": managed,
        "unknown_unmanaged_paths": unknown,
        "compose_resources": list(resources),
    }


def main() -> int:
    arguments = parser().parse_args()
    if arguments.command == "inventory":
        print(json.dumps(inventory_repository(Path.cwd()), sort_keys=True, indent=2))
        return 0
    raise AssertionError("argparse accepted an unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())
