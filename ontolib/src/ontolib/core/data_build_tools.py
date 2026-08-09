"""Immutable identities and fail-closed installers for data-build executables."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_ROBOT_VERSION_PREFIX = "ROBOT version "
ROBOT_INSTALL_DIR_ENV = "ONTOPRISM_ROBOT_DIR"


class ToolIdentityError(RuntimeError):
    """A build executable does not match its pinned identity."""


@dataclass(frozen=True, slots=True)
class DataBuildToolIdentity:
    """Source, version, and content digest persisted for one build executable."""

    name: str
    source: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.source.strip() or not self.version.strip():
            raise ValueError("tool identity fields must be non-empty")
        if _DIGEST.fullmatch(self.digest) is None:
            raise ValueError("tool digest must be canonical lowercase sha256:<hex>")

    def as_dict(self) -> dict[str, str]:
        """Return the stable JSON object embedded in build provenance."""
        return {
            "name": self.name,
            "source": self.source,
            "version": self.version,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class PinnedArtifact:
    """One downloadable file bound to the executable identity it contains."""

    identity: DataBuildToolIdentity
    filename: str

    def __post_init__(self) -> None:
        if not self.filename or Path(self.filename).name != self.filename:
            raise ValueError("artifact filename must be one plain path component")
        if urlsplit(self.identity.source).scheme != "https":
            raise ValueError("artifact source must use HTTPS")


OXIGRAPH_TOOL = DataBuildToolIdentity(
    name="oxigraph-cli",
    source="ghcr.io/oxigraph/oxigraph",
    version="0.5.3",
    digest=("sha256:cc943499d4724fbb348c75c623335c69a047de71c59852413b0d0467d3caebe3"),
)
OXIGRAPH_IMAGE = f"{OXIGRAPH_TOOL.source}@{OXIGRAPH_TOOL.digest}"
POSTGRES_IMAGE = (
    "pgvector/pgvector@sha256:"
    "7f5681e45237acdf546cf7cdc0dfc0ed7752ede857fda6e54f6ea21b936f8742"
)

ROBOT_ARTIFACT = PinnedArtifact(
    identity=DataBuildToolIdentity(
        name="robot-elk",
        source=("https://github.com/ontodev/robot/releases/download/v1.9.10/robot.jar"),
        version="1.9.10",
        digest=(
            "sha256:16a73c074f3df359a7338a84b4e0788785fe06117f931bb9796e9619ea776105"
        ),
    ),
    filename="robot.jar",
)


class CommandRunner(Protocol):
    """The subprocess boundary used to identify an installed ROBOT executable."""

    def __call__(
        self,
        args: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _verify_artifact(path: Path, artifact: PinnedArtifact) -> None:
    try:
        observed = _sha256(path)
    except OSError as exc:
        raise ToolIdentityError(f"cannot read pinned artifact {path}: {exc}") from exc
    if observed != artifact.identity.digest:
        raise ToolIdentityError(
            f"{artifact.identity.name} digest mismatch: "
            f"{observed} != {artifact.identity.digest}"
        )


def _launcher_text(jar_path: Path) -> str:
    quoted = shlex.quote(str(jar_path.resolve()))
    return f'#!/usr/bin/env bash\nexec java -Xmx4g -jar {quoted} "$@"\n'


def _write_atomic(path: Path, content: str, *, mode: int | None = None) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content)
        if mode is not None:
            temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _download_https(source: str, destination: Path) -> None:
    if urlsplit(source).scheme != "https":
        raise ToolIdentityError("artifact download source must use HTTPS")
    try:
        with (
            urllib.request.urlopen(source, timeout=120) as response,  # noqa: S310
            destination.open("wb") as output,
        ):
            if urlsplit(response.geturl()).scheme != "https":
                raise ToolIdentityError("artifact download redirected away from HTTPS")
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except ToolIdentityError:
        raise
    except OSError as exc:
        raise ToolIdentityError(f"artifact download failed: {exc}") from exc


def install_robot(
    install_dir: Path,
    *,
    artifact: PinnedArtifact = ROBOT_ARTIFACT,
    downloader: Callable[[str, Path], None] = _download_https,
) -> DataBuildToolIdentity:
    """Download ROBOT, verify it, then atomically publish its JAR and launcher."""
    destination = install_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    jar_path = destination / artifact.filename
    temporary_jar = destination / f".{artifact.filename}.{uuid4().hex}.download"
    try:
        downloader(artifact.identity.source, temporary_jar)
        _verify_artifact(temporary_jar, artifact)
        temporary_jar.replace(jar_path)
        _write_atomic(destination / "robot", _launcher_text(jar_path), mode=0o755)
        _write_atomic(
            destination / "robot-tool.json",
            json.dumps(artifact.identity.as_dict(), indent=2, sort_keys=True) + "\n",
        )
    finally:
        temporary_jar.unlink(missing_ok=True)
    return artifact.identity


def identify_robot_installation(
    install_dir: Path,
    *,
    artifact: PinnedArtifact = ROBOT_ARTIFACT,
    runner: CommandRunner = subprocess.run,
) -> DataBuildToolIdentity:
    """Revalidate the JAR, launcher, metadata, and observed ROBOT version."""
    destination = install_dir.resolve()
    jar_path = destination / artifact.filename
    launcher = destination / "robot"
    metadata = destination / "robot-tool.json"
    _verify_artifact(jar_path, artifact)

    try:
        launcher_text = launcher.read_text()
        recorded = json.loads(metadata.read_text())
    except (OSError, ValueError) as exc:
        raise ToolIdentityError(
            f"ROBOT installation metadata is unreadable: {exc}"
        ) from exc
    if launcher_text != _launcher_text(jar_path):
        raise ToolIdentityError("ROBOT launcher does not match the pinned installation")
    if recorded != artifact.identity.as_dict():
        raise ToolIdentityError("ROBOT metadata does not match the pinned identity")

    try:
        result = runner(
            [str(launcher), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolIdentityError(
            f"ROBOT version probe could not run: {type(exc).__name__}: {exc}"
        ) from exc
    expected = f"{_ROBOT_VERSION_PREFIX}{artifact.identity.version}"
    if result.returncode != 0:
        raise ToolIdentityError(
            f"ROBOT version probe exited {result.returncode}: {result.stderr.strip()}"
        )
    if result.stdout.strip() != expected:
        raise ToolIdentityError(
            f"ROBOT version mismatch: {result.stdout.strip()!r} != {expected!r}"
        )
    return artifact.identity


def configured_robot_installation() -> tuple[Path, DataBuildToolIdentity]:
    """Return the required, revalidated ROBOT installation configured for a build."""
    raw = os.environ.get(ROBOT_INSTALL_DIR_ENV)
    if not raw:
        raise ToolIdentityError(
            f"{ROBOT_INSTALL_DIR_ENV} must name an installation created by "
            "scripts/install_robot.py"
        )
    install_dir = Path(raw).resolve()
    return install_dir, identify_robot_installation(install_dir)
