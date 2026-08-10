"""Supply-chain contracts for external executables used by ``data-build``."""

from __future__ import annotations

import hashlib
import subprocess
from typing import TYPE_CHECKING

import pytest

from ontolib.core.data_build_tools import (
    ROBOT_ARTIFACT,
    DataBuildToolIdentity,
    PinnedArtifact,
    ToolIdentityError,
    identify_robot_installation,
    install_robot,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _artifact(payload: bytes = b"test robot jar") -> PinnedArtifact:
    return PinnedArtifact(
        identity=DataBuildToolIdentity(
            name="robot-elk",
            source="https://example.test/releases/v9.8.7/robot.jar",
            version="9.8.7",
            digest=f"sha256:{hashlib.sha256(payload).hexdigest()}",
        ),
        filename="robot.jar",
    )


def _downloader(payload: bytes) -> Callable[[str, Path], None]:
    def download(_source: str, destination: Path) -> None:
        destination.write_bytes(payload)

    return download


def _completed(version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["robot", "--version"], returncode=0, stdout=version, stderr=""
    )


def test_robot_release_has_official_immutable_identity() -> None:
    assert ROBOT_ARTIFACT.identity == DataBuildToolIdentity(
        name="robot-elk",
        source=("https://github.com/ontodev/robot/releases/download/v1.9.10/robot.jar"),
        version="1.9.10",
        digest=(
            "sha256:16a73c074f3df359a7338a84b4e0788785fe06117f931bb9796e9619ea776105"
        ),
    )


def test_corrupt_robot_download_is_never_published(tmp_path: Path) -> None:
    install_dir = tmp_path / "robot"

    with pytest.raises(ToolIdentityError, match="digest"):
        install_robot(
            install_dir,
            artifact=_artifact(),
            downloader=_downloader(b"tampered"),
        )

    assert not (install_dir / "robot.jar").exists()
    assert not (install_dir / "robot").exists()


def test_robot_installation_records_and_revalidates_exact_tool(
    tmp_path: Path,
) -> None:
    artifact = _artifact()
    install_dir = tmp_path / "robot"
    installed = install_robot(
        install_dir,
        artifact=artifact,
        downloader=_downloader(b"test robot jar"),
    )

    observed = identify_robot_installation(
        install_dir,
        artifact=artifact,
        runner=lambda *args, **kwargs: _completed("ROBOT version 9.8.7\n"),
    )

    assert installed == artifact.identity
    assert observed == artifact.identity
    assert (install_dir / "robot-tool.json").read_text().endswith("\n")


def test_robot_version_drift_rejects_before_build_provenance(tmp_path: Path) -> None:
    artifact = _artifact()
    install_dir = tmp_path / "robot"
    install_robot(
        install_dir,
        artifact=artifact,
        downloader=_downloader(b"test robot jar"),
    )

    with pytest.raises(ToolIdentityError, match="version"):
        identify_robot_installation(
            install_dir,
            artifact=artifact,
            runner=lambda *args, **kwargs: _completed("ROBOT version 9.8.6\n"),
        )


def test_robot_probe_execution_failure_is_an_identity_error(tmp_path: Path) -> None:
    artifact = _artifact()
    install_dir = tmp_path / "robot"
    install_robot(
        install_dir,
        artifact=artifact,
        downloader=_downloader(b"test robot jar"),
    )

    def unavailable(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise OSError("java unavailable")

    with pytest.raises(ToolIdentityError, match="version probe could not run"):
        identify_robot_installation(
            install_dir,
            artifact=artifact,
            runner=unavailable,
        )


def test_modified_robot_launcher_rejects_before_execution(tmp_path: Path) -> None:
    artifact = _artifact()
    install_dir = tmp_path / "robot"
    install_robot(
        install_dir,
        artifact=artifact,
        downloader=_downloader(b"test robot jar"),
    )
    (install_dir / "robot").write_text("#!/bin/sh\nexit 0\n")
    called = False

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return _completed("ROBOT version 9.8.7\n")

    with pytest.raises(ToolIdentityError, match="launcher"):
        identify_robot_installation(install_dir, artifact=artifact, runner=runner)

    assert called is False


@pytest.mark.parametrize(
    "digest",
    ["", "sha256:not-hex", "md5:" + "0" * 32, "sha256:" + "A" * 64],
)
def test_tool_identity_rejects_noncanonical_digest(digest: str) -> None:
    with pytest.raises(ValueError, match="digest"):
        DataBuildToolIdentity(
            name="tool", source="https://example.test/tool", version="1", digest=digest
        )
