from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import scripts.dev.cleanup_workspace as cleanup_module
from scripts.dev.cleanup_workspace import (
    CleanupCandidate,
    cleanup_workspace,
    workspace_cleanup_candidates,
)


class _DockerRunner:
    def __init__(self, data_dir: Path, nonce: str, *, owner_label: str | None = None):
        self.data_dir = data_dir
        self.nonce = nonce
        self.owner_label = nonce if owner_label is None else owner_label
        self.containers = {
            f"ontoprism-qlever-test-{nonce}": "a" * 64,
            f"ontoprism-postgres-test-{nonce}": "b" * 64,
        }
        self.removed_while_data_existed: list[str] = []

    def __call__(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        if args == ("ps", "--all", "--format", "{{.Names}}"):
            return subprocess.CompletedProcess(
                args, 0, "\n".join(self.containers) + "\n", ""
            )
        if args[:1] == ("inspect",):
            requested = args[1]
            name = next(
                (
                    name
                    for name, container_id in self.containers.items()
                    if requested in {name, container_id}
                ),
                requested,
            )
            container_id = self.containers[name]
            mounts = (
                [{"Source": str(self.data_dir.resolve()), "Destination": "/data"}]
                if "qlever" in name
                else []
            )
            payload = [
                {
                    "Id": container_id,
                    "Config": {
                        "Labels": {"org.ontoprism.test-owner": self.owner_label}
                    },
                    "Mounts": mounts,
                }
            ]
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        if args[:2] == ("rm", "--force"):
            container_id = args[2]
            name = next(
                name for name, value in self.containers.items() if value == container_id
            )
            assert self.data_dir.exists()
            self.removed_while_data_existed.append(name)
            del self.containers[name]
            return subprocess.CompletedProcess(args, 0, container_id, "")
        raise AssertionError(args)


class _EmptyDockerRunner:
    def __call__(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        if args == ("ps", "--all", "--format", "{{.Names}}"):
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)


@pytest.mark.unit
def test_workspace_cleanup_targets_only_known_leaks(tmp_path: Path) -> None:
    nonce = "1" * 32
    leaked_store = tmp_path / f"data/ontoprism-qlever-{nonce}-fixture"
    active_store = tmp_path / "data/qlever-ncit"
    root_coverage = tmp_path / ".coverage.worker"
    tmp_coverage = tmp_path / "tmp/.coverage.integration-0"
    preserved = tmp_path / "tmp/preserved-result.json"
    for directory in (leaked_store, active_store):
        directory.mkdir(parents=True)
        (directory / "data.bin").write_bytes(b"data")
    (leaked_store / ".ontoprism-test-owner").write_text(nonce, encoding="utf-8")
    for path in (root_coverage, tmp_coverage, preserved):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("data", encoding="utf-8")

    assert workspace_cleanup_candidates(tmp_path) == (
        CleanupCandidate("coverage", root_coverage),
        CleanupCandidate("qlever", leaked_store),
        CleanupCandidate("coverage", tmp_coverage),
    )

    report = cleanup_workspace(tmp_path, docker_run=_EmptyDockerRunner())

    assert report.removed == (root_coverage, leaked_store, tmp_coverage)
    assert report.skipped == ()
    assert not root_coverage.exists()
    assert not leaked_store.exists()
    assert not tmp_coverage.exists()
    assert (active_store / "data.bin").read_bytes() == b"data"
    assert preserved.read_text(encoding="utf-8") == "data"


@pytest.mark.unit
@pytest.mark.parametrize("marker", [None, "2" * 32])
def test_workspace_cleanup_keeps_unverified_qlever_directory(
    tmp_path: Path, marker: str | None
) -> None:
    nonce = "1" * 32
    leaked_store = tmp_path / f"data/ontoprism-qlever-{nonce}-fixture"
    leaked_store.mkdir(parents=True)
    if marker is not None:
        (leaked_store / ".ontoprism-test-owner").write_text(marker, encoding="utf-8")

    report = cleanup_workspace(tmp_path, docker_run=_EmptyDockerRunner())

    assert report.removed == ()
    assert len(report.skipped) == 1
    assert report.skipped[0].path == leaked_store
    assert "owner marker" in report.skipped[0].reason
    assert leaked_store.is_dir()


@pytest.mark.unit
def test_workspace_cleanup_keeps_malformed_qlever_directory(tmp_path: Path) -> None:
    malformed = tmp_path / "data/ontoprism-qlever-not-a-nonce"
    malformed.mkdir(parents=True)

    report = cleanup_workspace(tmp_path, docker_run=_EmptyDockerRunner())

    assert report.removed == ()
    assert report.skipped[0].path == malformed
    assert report.skipped[0].reason == "QLever directory name is invalid"
    assert malformed.is_dir()


@pytest.mark.unit
def test_workspace_cleanup_reports_non_file_coverage_candidate_and_continues(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / ".coverage.directory"
    valid = tmp_path / ".coverage.worker"
    invalid.mkdir()
    valid.write_text("data", encoding="utf-8")

    report = cleanup_workspace(tmp_path, docker_run=_EmptyDockerRunner())

    assert report.removed == (valid,)
    assert report.skipped == (
        cleanup_module.SkippedCleanup(invalid, "coverage shard is not a regular file"),
    )
    assert invalid.is_dir()
    assert not valid.exists()


@pytest.mark.unit
def test_workspace_cleanup_removes_verified_containers_before_their_data(
    tmp_path: Path,
) -> None:
    nonce = "1" * 32
    leaked_store = tmp_path / f"data/ontoprism-qlever-{nonce}-fixture"
    leaked_store.mkdir(parents=True)
    (leaked_store / ".ontoprism-test-owner").write_text(nonce, encoding="utf-8")
    docker = _DockerRunner(leaked_store, nonce)

    report = cleanup_workspace(tmp_path, docker_run=docker)

    assert docker.removed_while_data_existed == [
        f"ontoprism-postgres-test-{nonce}",
        f"ontoprism-qlever-test-{nonce}",
    ]
    assert report.removed_containers == tuple(docker.removed_while_data_existed)
    assert report.removed == (leaked_store,)
    assert report.skipped == ()


@pytest.mark.unit
def test_workspace_cleanup_keeps_unverified_container_and_data(tmp_path: Path) -> None:
    nonce = "1" * 32
    leaked_store = tmp_path / f"data/ontoprism-qlever-{nonce}-fixture"
    leaked_store.mkdir(parents=True)
    (leaked_store / ".ontoprism-test-owner").write_text(nonce, encoding="utf-8")
    docker = _DockerRunner(leaked_store, nonce, owner_label="2" * 32)

    report = cleanup_workspace(tmp_path, docker_run=docker)

    assert report.removed_containers == ()
    assert len(report.skipped_containers) == 2
    assert report.removed == ()
    assert len(report.skipped) == 1
    assert "container exists" in report.skipped[0].reason
    assert leaked_store.is_dir()


@pytest.mark.unit
def test_workspace_cleanup_holds_exclusive_lease_before_docker_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    @contextmanager
    def lease(*, exclusive: bool = False) -> Iterator[None]:
        assert exclusive is True
        events.append("lease-entered")
        yield
        events.append("lease-exited")

    class OrderedDocker(_EmptyDockerRunner):
        def __call__(
            self, *args: str, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            assert events[0] == "lease-entered"
            assert "lease-exited" not in events
            events.append("docker-access")
            return super().__call__(*args, check=check)

    monkeypatch.setattr(cleanup_module, "integration_resource_lease", lease)

    cleanup_workspace(tmp_path, docker_run=OrderedDocker())

    assert events == ["lease-entered", "docker-access", "docker-access", "lease-exited"]


@pytest.mark.unit
def test_workspace_cleanup_exit_is_nonzero_when_resources_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skipped_path = (
        Path(cleanup_module.__file__).resolve().parents[2] / "data/unverified"
    )
    skipped = cleanup_module.CleanupReport(
        removed=(),
        skipped=(cleanup_module.SkippedCleanup(skipped_path, "reason"),),
    )
    monkeypatch.setattr(cleanup_module, "cleanup_workspace", lambda _root: skipped)

    assert cleanup_module.main() == 1
    assert "skipped-paths=1" in capsys.readouterr().out


@pytest.mark.unit
def test_workspace_cleanup_exit_is_nonzero_when_container_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skipped = cleanup_module.CleanupReport(
        removed=(),
        skipped=(),
        skipped_containers=(
            cleanup_module.SkippedContainer("ontoprism-qlever-test-invalid", "reason"),
        ),
    )
    monkeypatch.setattr(cleanup_module, "cleanup_workspace", lambda _root: skipped)

    assert cleanup_module.main() == 1
    output = capsys.readouterr().out
    assert "skipped container: ontoprism-qlever-test-invalid: reason" in output
    assert "skipped-containers=1" in output


@pytest.mark.unit
def test_makefile_exposes_the_bounded_workspace_cleanup_target() -> None:
    root = Path(__file__).resolve().parents[1]
    makefile = (root / "Makefile").read_text(encoding="utf-8")

    assert "clean-workspace:" in makefile
    assert "pdm run python -m scripts.dev.cleanup_workspace" in makefile
