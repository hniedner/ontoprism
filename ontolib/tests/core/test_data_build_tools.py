"""Supply-chain contracts for external executables used by ``data-build``."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ontolib.core import data_build_tools as tools
from ontolib.core.data_build_tools import (
    JENA_RIOT_ARTIFACT,
    ROBOT_ARTIFACT,
    DataBuildToolIdentity,
    PinnedArtifact,
    ToolIdentityError,
    identify_jena_installation,
    identify_robot_installation,
    install_jena,
    install_robot,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _jena_archive(*, unsafe_name: str | None = None) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        files = {
            "apache-jena-9.8.7/bin/riot": b"#!/bin/sh\n",
            "apache-jena-9.8.7/lib/jena-arq-9.8.7.jar": b"jar",
        }
        if unsafe_name is not None:
            files[unsafe_name] = b"escape"
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o755 if name.endswith("/riot") else 0o644
            archive.addfile(member, io.BytesIO(content))
    return payload.getvalue()


def _jena_artifact(payload: bytes) -> PinnedArtifact:
    return PinnedArtifact(
        identity=DataBuildToolIdentity(
            name="apache-jena-riot",
            source="https://example.test/apache-jena-9.8.7.tar.gz",
            version="9.8.7",
            digest=f"sha256:{hashlib.sha256(payload).hexdigest()}",
        ),
        filename="apache-jena-9.8.7.tar.gz",
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


def test_jena_release_has_official_immutable_identity() -> None:
    assert JENA_RIOT_ARTIFACT.identity == DataBuildToolIdentity(
        name="apache-jena-riot",
        source=(
            "https://archive.apache.org/dist/jena/binaries/apache-jena-6.1.0.tar.gz"
        ),
        version="6.1.0",
        digest=(
            "sha256:653108a91fd9b309a89bc756258bae0bca01587cef475942d11852e3beba2ae3"
        ),
    )


def test_jena_installation_records_and_revalidates_exact_riot(
    tmp_path: Path,
) -> None:
    payload = _jena_archive()
    artifact = _jena_artifact(payload)
    install_dir = tmp_path / "jena"

    installed = install_jena(
        install_dir,
        artifact=artifact,
        downloader=_downloader(payload),
    )
    observed = identify_jena_installation(
        install_dir,
        artifact=artifact,
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["riot", "--version"],
            returncode=0,
            stdout="Apache Jena RIOT version 9.8.7\n",
            stderr="",
        ),
    )

    assert installed == artifact.identity
    assert observed == artifact.identity
    assert (install_dir / "bin" / "riot").stat().st_mode & 0o111
    assert (install_dir / "jena-tool.json").read_text().endswith("\n")


def test_jena_archive_path_escape_is_never_published(tmp_path: Path) -> None:
    payload = _jena_archive(unsafe_name="../escape")

    with pytest.raises(ToolIdentityError, match="unsafe archive member"):
        install_jena(
            tmp_path / "jena",
            artifact=_jena_artifact(payload),
            downloader=_downloader(payload),
        )

    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "jena" / "bin" / "riot").exists()


def test_jena_version_drift_rejects_before_conversion(tmp_path: Path) -> None:
    payload = _jena_archive()
    artifact = _jena_artifact(payload)
    install_dir = tmp_path / "jena"
    install_jena(
        install_dir,
        artifact=artifact,
        downloader=_downloader(payload),
    )

    with pytest.raises(ToolIdentityError, match="version"):
        identify_jena_installation(
            install_dir,
            artifact=artifact,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                args=["riot", "--version"],
                returncode=0,
                stdout="Apache Jena RIOT version 9.8.6\n",
                stderr="",
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


@pytest.mark.parametrize("kind", ["jena", "robot"])
@pytest.mark.parametrize("damage", ["missing", "malformed", "wrong-version"])
def test_installed_tool_metadata_damage_refuses_execution(tmp_path, kind, damage):
    payload = _jena_archive() if kind == "jena" else b"test robot jar"
    artifact = _jena_artifact(payload) if kind == "jena" else _artifact(payload)
    install = install_jena if kind == "jena" else install_robot
    identify = (
        identify_jena_installation if kind == "jena" else identify_robot_installation
    )
    target = tmp_path / kind
    install(target, artifact=artifact, downloader=_downloader(payload))
    metadata = target / f"{kind}-tool.json"
    if damage == "missing":
        metadata.unlink()
    elif damage == "malformed":
        metadata.write_text("not json")
    else:
        document = json.loads(metadata.read_text())
        document["version"] = "0.0.0"
        metadata.write_text(json.dumps(document))

    def unexpected_probe(*args, **kwargs):
        pytest.fail("damaged installation must not execute")

    with pytest.raises(ToolIdentityError, match="metadata"):
        identify(target, artifact=artifact, runner=unexpected_probe)


@pytest.mark.parametrize("kind", ["jena", "robot"])
def test_failed_version_process_cannot_certify_tool(tmp_path, kind):
    payload = _jena_archive() if kind == "jena" else b"test robot jar"
    artifact = _jena_artifact(payload) if kind == "jena" else _artifact(payload)
    install = install_jena if kind == "jena" else install_robot
    identify = (
        identify_jena_installation if kind == "jena" else identify_robot_installation
    )
    target = tmp_path / kind
    install(target, artifact=artifact, downloader=_downloader(payload))
    with pytest.raises(ToolIdentityError, match="exited 7"):
        identify(
            target,
            artifact=artifact,
            runner=lambda *a, **k: subprocess.CompletedProcess(
                args=[], returncode=7, stdout="9.8.7", stderr="java failed"
            ),
        )


@pytest.mark.parametrize(
    "member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE]
)
def test_jena_special_archive_members_never_replace_existing_install(
    tmp_path, member_type
):
    target = tmp_path / "jena"
    target.mkdir()
    (target / "keep").write_text("working installation")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("apache-jena-9.8.7/bin/riot")
        member.type = member_type
        member.linkname = "outside"
        archive.addfile(member)
    payload = buffer.getvalue()
    with pytest.raises(ToolIdentityError, match="unsafe archive"):
        install_jena(
            target, artifact=_jena_artifact(payload), downloader=_downloader(payload)
        )
    assert (target / "keep").read_text() == "working installation"
    assert list(tmp_path.iterdir()) == [target]


def test_jena_directory_entries_and_reinstall_replace_old_files(tmp_path):
    target = tmp_path / "jena"
    target.mkdir()
    (target / "obsolete").write_text("old")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in (
            "apache-jena-9.8.7",
            "apache-jena-9.8.7/bin",
            "apache-jena-9.8.7/lib",
        ):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
        member = tarfile.TarInfo("apache-jena-9.8.7/bin/riot")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"new"))
    payload = buffer.getvalue()
    install_jena(
        target, artifact=_jena_artifact(payload), downloader=_downloader(payload)
    )
    assert (target / "bin/riot").read_bytes() == b"new"
    assert not (target / "obsolete").exists()
    assert list(tmp_path.iterdir()) == [target]


def test_jena_failed_publish_restores_existing_install(tmp_path, monkeypatch):

    target = tmp_path / "jena"
    target.mkdir()
    (target / "keep").write_text("working installation")
    replace = Path.replace

    def fail_staging(source, destination):
        if source.name.endswith(".staging"):
            raise OSError("disk refused publish")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_staging)
    payload = _jena_archive()
    with pytest.raises(OSError, match="disk refused"):
        install_jena(
            target, artifact=_jena_artifact(payload), downloader=_downloader(payload)
        )
    assert (target / "keep").read_text() == "working installation"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize(
    "redirect", ["https://cdn.example.test/tool", "http://cdn.example.test/tool"]
)
def test_download_accepts_https_chunks_but_rejects_insecure_redirect(
    tmp_path, monkeypatch, redirect
):
    class Response(io.BytesIO):
        def geturl(self):
            return redirect

    payload = b"x" * (1024 * 1024 + 17)
    monkeypatch.setattr(
        tools.urllib.request, "urlopen", lambda *a, **k: Response(payload)
    )
    destination = tmp_path / "tool"
    if redirect.startswith("http:"):
        with pytest.raises(ToolIdentityError, match="redirected away"):
            tools._download_https("https://example.test/tool", destination)
        assert destination.read_bytes() == b""
    else:
        tools._download_https("https://example.test/tool", destination)
        assert destination.read_bytes() == payload


def test_download_refuses_insecure_source_and_reports_transport_failure(
    tmp_path, monkeypatch
):
    with pytest.raises(ToolIdentityError, match="must use HTTPS"):
        tools._download_https("http://example.test/tool", tmp_path / "tool")
    assert not (tmp_path / "tool").exists()

    def failed_open(*a, **k):
        raise OSError("connection closed")

    monkeypatch.setattr(tools.urllib.request, "urlopen", failed_open)
    with pytest.raises(ToolIdentityError, match=r"download failed.*connection closed"):
        tools._download_https("https://example.test/tool", tmp_path / "tool")


@pytest.mark.parametrize("payload", [b"not a tar file", None])
def test_unusable_jena_archive_never_publishes(tmp_path, payload):
    if payload is None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz"):
            pass
        payload = buffer.getvalue()
    with pytest.raises(ToolIdentityError, match=r"cannot extract|no RIOT executable"):
        install_jena(
            tmp_path / "jena",
            artifact=_jena_artifact(payload),
            downloader=_downloader(payload),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "failure", [OSError("java missing"), subprocess.TimeoutExpired("riot", 30)]
)
def test_jena_probe_transport_errors_preserve_cause(tmp_path, failure):
    payload = _jena_archive()
    artifact = _jena_artifact(payload)
    target = tmp_path / "jena"
    install_jena(target, artifact=artifact, downloader=_downloader(payload))

    def failed_probe(*a, **k):
        raise failure

    with pytest.raises(ToolIdentityError, match="probe could not run") as caught:
        identify_jena_installation(target, artifact=artifact, runner=failed_probe)
    assert caught.value.__cause__ is failure


def test_absent_tool_artifact_is_not_an_installation(tmp_path):
    with pytest.raises(ToolIdentityError, match="cannot read pinned artifact"):
        identify_robot_installation(tmp_path, artifact=_artifact())


def test_unconfigured_robot_fails_before_build(monkeypatch):
    monkeypatch.delenv(tools.ROBOT_INSTALL_DIR_ENV, raising=False)
    with pytest.raises(ToolIdentityError, match="must name an installation"):
        tools.configured_robot_installation()
