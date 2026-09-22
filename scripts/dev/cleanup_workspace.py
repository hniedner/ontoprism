#!/usr/bin/env python3
"""Remove owner-verified test containers before their data and coverage shards."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal, NamedTuple

from test_support.integration_resources import (
    DockerRun,
    IntegrationResourceOwner,
    ResourceOwnershipError,
    inspect_owned_container,
    integration_resource_lease,
    run_docker,
    verify_qlever_data_dir,
    verify_qlever_owner,
)

_QLEVER_DIRECTORY = re.compile(r"ontoprism-qlever-([0-9a-f]{32})-.+")
_TEST_CONTAINER = re.compile(r"ontoprism-(qlever|postgres)-test-([0-9a-f]{32})")


class SkippedCleanup(NamedTuple):
    path: Path
    reason: str


class SkippedContainer(NamedTuple):
    name: str
    reason: str


class CleanupCandidate(NamedTuple):
    kind: Literal["coverage", "qlever"]
    path: Path


class CleanupReport(NamedTuple):
    removed: tuple[Path, ...]
    skipped: tuple[SkippedCleanup, ...]
    removed_containers: tuple[str, ...] = ()
    skipped_containers: tuple[SkippedContainer, ...] = ()


def workspace_cleanup_candidates(root: Path) -> tuple[CleanupCandidate, ...]:
    """Return bounded coverage shards and test QLever directories under *root*."""
    root = root.resolve()
    candidates = [
        *(CleanupCandidate("coverage", path) for path in root.glob(".coverage.*")),
        *(
            CleanupCandidate("qlever", path)
            for path in (root / "data").glob("ontoprism-qlever-*")
        ),
        *(
            CleanupCandidate("coverage", path)
            for path in (root / "tmp").glob(".coverage.*")
        ),
    ]
    return tuple(sorted(candidates, key=lambda candidate: candidate.path))


def _container_names(docker_run: DockerRun) -> frozenset[str]:
    listed = docker_run("ps", "--all", "--format", "{{.Names}}")
    return frozenset(line for line in listed.stdout.splitlines() if line)


def _verified_container_data_dir(
    owner: IntegrationResourceOwner,
    container_id: str,
    *,
    docker_run: DockerRun,
) -> Path:
    details = inspect_owned_container(owner, container_id, docker_run=docker_run)
    mounts = details.get("Mounts")
    if not isinstance(mounts, list):
        raise ResourceOwnershipError("QLever container mounts are malformed")
    source = next(
        (
            mount.get("Source")
            for mount in mounts
            if isinstance(mount, dict) and mount.get("Destination") == "/data"
        ),
        None,
    )
    if not isinstance(source, str):
        raise ResourceOwnershipError("QLever container data mount is missing")
    data_dir = Path(source)
    verify_qlever_owner(owner, container_id, data_dir, docker_run=docker_run)
    return data_dir.resolve()


def _remove_verified_test_containers(
    root: Path,
    names: frozenset[str],
    docker_run: DockerRun,
) -> tuple[tuple[str, ...], tuple[SkippedContainer, ...]]:
    removed: list[str] = []
    skipped: list[SkippedContainer] = []
    data_root = (root / "data").resolve()
    for name in sorted(names):
        match = _TEST_CONTAINER.fullmatch(name)
        if match is None:
            continue
        service, nonce = match.groups()
        owner = IntegrationResourceOwner(nonce=nonce)
        try:
            inspected = docker_run("inspect", name)
            details = json.loads(inspected.stdout)[0]
            container_id = details["Id"]
            if not isinstance(container_id, str):
                raise ResourceOwnershipError("container ID is malformed")
            if service == "qlever":
                data_dir = _verified_container_data_dir(
                    owner, container_id, docker_run=docker_run
                )
                if data_dir.parent != data_root:
                    raise ResourceOwnershipError(
                        "QLever container data mount is outside repository data"
                    )
            else:
                inspect_owned_container(owner, container_id, docker_run=docker_run)
            docker_run("rm", "--force", container_id)
        except (
            KeyError,
            IndexError,
            OSError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            skipped.append(SkippedContainer(name, str(error)))
        else:
            removed.append(name)
    return tuple(removed), tuple(skipped)


def cleanup_workspace(
    root: Path,
    *,
    docker_run: DockerRun = run_docker,
) -> CleanupReport:
    """Remove verified test containers first, then their marked inactive data."""
    root = root.resolve()
    with integration_resource_lease(exclusive=True):
        names = _container_names(docker_run)
        removed_containers, skipped_containers = _remove_verified_test_containers(
            root, names, docker_run
        )
        remaining_names = _container_names(docker_run)

        removed: list[Path] = []
        skipped: list[SkippedCleanup] = []
        for candidate in workspace_cleanup_candidates(root):
            path = candidate.path
            if candidate.kind == "coverage":
                path.unlink()
                removed.append(path)
                continue
            match = _QLEVER_DIRECTORY.fullmatch(path.name)
            if match is None:
                skipped.append(SkippedCleanup(path, "QLever directory name is invalid"))
                continue
            nonce = match.group(1)
            owner = IntegrationResourceOwner(nonce=nonce)
            active = sorted(
                name
                for name in remaining_names
                if name in {owner.qlever_container_name, owner.postgres_container_name}
            )
            if active:
                skipped.append(SkippedCleanup(path, f"container exists: {active[0]}"))
                continue
            try:
                if path.is_symlink():
                    raise ResourceOwnershipError("QLever data directory is a symlink")
                verify_qlever_data_dir(owner, path)
            except (OSError, ResourceOwnershipError) as error:
                skipped.append(SkippedCleanup(path, f"owner marker invalid: {error}"))
                continue
            shutil.rmtree(path)
            removed.append(path)
    return CleanupReport(
        removed=tuple(removed),
        skipped=tuple(skipped),
        removed_containers=removed_containers,
        skipped_containers=skipped_containers,
    )


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    report = cleanup_workspace(root)
    for name in report.removed_containers:
        print(f"removed container: {name}")
    for item in report.skipped_containers:
        print(f"skipped container: {item.name}: {item.reason}")
    for path in report.removed:
        print(f"removed path: {path.relative_to(root)}")
    for item in report.skipped:
        print(f"skipped path: {item.path.relative_to(root)}: {item.reason}")
    print(
        f"removed-containers={len(report.removed_containers)} "
        f"skipped-containers={len(report.skipped_containers)} "
        f"removed-paths={len(report.removed)} skipped-paths={len(report.skipped)}"
    )
    return int(bool(report.skipped or report.skipped_containers))


if __name__ == "__main__":
    raise SystemExit(main())
