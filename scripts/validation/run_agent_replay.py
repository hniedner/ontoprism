#!/usr/bin/env python3
"""Run fixed operations, including mutating local container/tmp operations.

Operations return zero on success. Contract refusals and required-command failures raise
``AgentReplayInputError``; local filesystem setup failures may propagate as their native
environment exceptions. Cleanup failures are attached without replacing an earlier
operation failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, assert_never, cast

import yaml

from ontolib.decomposition.artifact_contract import (
    COMPOSE_PROJECT,
    POSTGRES_VOLUME,
    POSTGRES_VOLUME_PROJECT,
)

from .docker_selectors import DOCKER_SELECTOR_VARIABLES

if TYPE_CHECKING:
    from collections.abc import Iterator

_DIAGNOSTIC_TIMEOUT_SECONDS = 20
# A guest shutdown or boot, not a diagnostic: on 2026-09-18 a half-dead VM needed well
# over the 20 s limit (on the order of a minute) to power off, so the recovery failed
# every time.
_PODMAN_MACHINE_TIMEOUT_SECONDS = 300
# Killing the `machine stop` CLI at its timeout does not stop the guest's shutdown; this
# many looks at the machine state, ten seconds apart, follow before the recovery gives
# up.
_PODMAN_STOP_WAIT_ATTEMPTS = 30
_GATE_TIMEOUT_SECONDS = 3_600
_COMPOSE_TIMEOUT_SECONDS = 1_800
_PODMAN_READINESS_ATTEMPTS = 3
_PODMAN_ENSURED_ENV = "ONTOPRISM_PODMAN_STACK_ENSURED"
_MAX_TCP_PORT = 65_535
_MAX_DIAGNOSTIC_CHARS = 8_192
_POC_DIR = Path("tmp/podman-poc")
_PODMAN_PROJECT = COMPOSE_PROJECT
_PODMAN_VOLUME = POSTGRES_VOLUME
_PODMAN_MACHINE = "ontoprism-vm"
_PODMAN_DOCKER_CONTEXT = "ontoprism-podman"
_PODMAN_DOCKER_CONTEXT_DESCRIPTION = "OntoPrism rootless Podman machine"
_PODMAN = "/opt/homebrew/bin/podman"
_DOCKER = "/opt/homebrew/bin/docker"
_DOCKER_COMPOSE = "/opt/homebrew/bin/docker-compose"
_PDM = "/opt/homebrew/bin/pdm"
type ComposeService = Literal["postgres", "qlever-ncit", "qlever-uberon"]
_COMPOSE_SERVICES: tuple[ComposeService, ...] = (
    "postgres",
    "qlever-ncit",
    "qlever-uberon",
)
_POSTGRES_IMAGE = (
    "pgvector/pgvector@sha256:"
    "a947c45cdc5906a1bc951f20a8709e321256343ee0f251e4ae00b5e7def4e6da"
)
# The lookbehind pins the key prefix to the start of its word run. Without it the
# prefix may start at every character of a long run (a hash, base64), and each start
# rescans the rest of the run: quadratic on real `docker inspect` output (#369).
_SECRET_VALUE = re.compile(
    r"(?i)(?<![A-Z0-9_-])([\"']?[A-Z0-9_-]*(?:PASSWORD|PASSWD|TOKEN|SECRET|API[_-]?KEY)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
_URL_CREDENTIALS = re.compile(
    r"((?:https?|postgresql(?:\+asyncpg)?)://[^\s:/]+:)[^@\s]+(@)", re.I
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_CODEPOINT_LIMIT = 32
_CONSOLIDATION_VALUE_COUNT = 3


class AgentReplayInputError(ValueError):
    """The requested operation is outside the fixed replay contract."""


class CapturedCommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        shell: Literal[False],
        check: Literal[False],
        timeout: float | None,
        capture_output: bool,
        text: Literal[True],
        env: dict[str, str] | None = None,
        start_new_session: bool = False,
    ) -> CapturedCommandResult: ...


class Operation(Protocol):
    def __call__(self, values: list[str], root: Path, runner: CommandRunner) -> int: ...


@dataclass(frozen=True)
class ArtifactInventory:
    entries: tuple[dict[str, object], ...]
    identity: str
    logical_bytes: int
    allocated_bytes: int


@dataclass(frozen=True)
class ConsolidationEntry:
    source_relative: str
    source: Path
    destination_relative: str
    destination: Path
    duplicate_of_relative: str | None
    inventory: ArtifactInventory


@dataclass(frozen=True)
class ConsolidationContext:
    manifest_relative: str
    manifest_path: Path
    report_relative: str
    report_path: Path
    manifest_bytes: bytes
    manifest_digest: str
    source_specs: tuple[dict[str, object], ...]


def _subprocess_runner(
    arguments: list[str],
    *,
    cwd: Path,
    shell: Literal[False],
    check: Literal[False],
    timeout: float | None,
    capture_output: bool,
    text: Literal[True],
    env: dict[str, str] | None = None,
    start_new_session: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        arguments,
        cwd=cwd,
        shell=shell,
        check=check,
        timeout=timeout,
        capture_output=capture_output,
        text=text,
        env=env,
        start_new_session=start_new_session,
    )


def _require_files(root: Path, relatives: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    for relative in relatives:
        path = root / relative
        if not path.is_file():
            raise AgentReplayInputError(f"required input does not exist: {relative}")
        print(f"verified input: {relative}", file=sys.stderr)
        paths.append(str(path))
    return paths


def _run(command: list[str], root: Path, runner: CommandRunner) -> int:
    result = runner(
        command,
        cwd=root,
        shell=False,
        check=False,
        timeout=None,
        capture_output=False,
        text=True,
    )
    return result.returncode


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AgentReplayInputError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_strict_json(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(data, object_pairs_hook=_strict_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError(f"{label} is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise AgentReplayInputError(f"{label} must be a JSON object")
    return payload


def _validated_repository_relative(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AgentReplayInputError(f"{label} must be a non-empty repository path")
    path = Path(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < _CONTROL_CODEPOINT_LIMIT for character in value)
        or any(character in value for character in "*?[]{}")
        or path.as_posix() != value
    ):
        raise AgentReplayInputError(f"{label} must be a normalized relative path")
    return value


def _require_no_symlink_components(path: Path, *, root: Path, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise AgentReplayInputError(f"{label} escapes the repository") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise AgentReplayInputError(f"{label} contains a symlink: {current}")


def _artifact_inventory(path: Path) -> ArtifactInventory:
    entries: list[dict[str, object]] = []
    logical_bytes = 0
    allocated_bytes = 0

    def visit(current: Path, relative: str) -> None:
        nonlocal allocated_bytes, logical_bytes
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise AgentReplayInputError(f"artifact tree contains a symlink: {current}")
        allocated_bytes += metadata.st_blocks * 512
        if current.is_file():
            digest = hashlib.sha256()
            with current.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            logical_bytes += metadata.st_size
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "sha256": digest.hexdigest(),
                    "bytes": metadata.st_size,
                }
            )
            return
        if not current.is_dir():
            raise AgentReplayInputError(f"artifact has unsupported kind: {current}")
        entries.append({"path": relative, "kind": "directory"})
        with os.scandir(current) as children:
            for child in sorted(children, key=lambda item: item.name):
                visit(
                    Path(child.path),
                    child.name if relative == "." else f"{relative}/{child.name}",
                )

    visit(path, ".")
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return ArtifactInventory(
        entries=tuple(entries),
        identity=hashlib.sha256(encoded).hexdigest(),
        logical_bytes=logical_bytes,
        allocated_bytes=allocated_bytes,
    )


def _git_result(
    arguments: list[str], root: Path, runner: CommandRunner
) -> CapturedCommandResult:
    try:
        return runner(
            arguments,
            cwd=root,
            shell=False,
            check=False,
            timeout=_DIAGNOSTIC_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentReplayInputError(
            f"Git preflight failed: {' '.join(arguments)}"
        ) from exc


def _require_untracked_ignored(
    relative: str, root: Path, runner: CommandRunner
) -> None:
    tracked = _git_result(["git", "ls-files", "--", relative], root, runner)
    if tracked.returncode != 0:
        raise AgentReplayInputError(f"Git tracked-source check failed: {relative}")
    if tracked.stdout.strip():
        raise AgentReplayInputError(f"source is tracked by Git: {relative}")
    ignored = _git_result(["git", "check-ignore", "-q", "--", relative], root, runner)
    if ignored.returncode == 1:
        raise AgentReplayInputError(f"source is not ignored by Git: {relative}")
    if ignored.returncode != 0:
        raise AgentReplayInputError(f"Git ignored-source check failed: {relative}")


def _path_contains(parent: Path, child: Path) -> bool:
    return child == parent or parent in child.parents


def _destination_for(source_relative: str) -> str:
    within_tmp = Path(source_relative).relative_to("tmp")
    if len(within_tmp.parts) == 1:
        return (Path("tmp/obsolete/root") / within_tmp).as_posix()
    return (Path("tmp/obsolete") / within_tmp).as_posix()


def _read_manifest_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _rename_artifact(source: Path, destination: Path) -> None:
    os.rename(source, destination)


def _same_filesystem(source: Path, destination_parent: Path) -> bool:
    return source.stat().st_dev == destination_parent.stat().st_dev


def _inventory_payload(inventory: ArtifactInventory) -> dict[str, object]:
    return {
        "identity": inventory.identity,
        "logical_bytes": inventory.logical_bytes,
        "allocated_bytes": inventory.allocated_bytes,
        "entries": list(inventory.entries),
    }


def _write_report(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(serialized, encoding="utf-8")


def _valid_completed_totals(
    report: dict[str, Any],
    *,
    sources: int,
    logical_bytes: int,
    allocated_bytes: int,
) -> bool:
    return (
        report.get("totals")
        == {
            "sources": sources,
            "logical_bytes": logical_bytes,
            "allocated_bytes": allocated_bytes,
        }
        and re.fullmatch(r"[0-9a-f]{40,64}", str(report.get("git_head"))) is not None
    )


def _completed_report_mappings(
    report: dict[str, Any],
    *,
    manifest_relative: str,
    manifest_digest: str,
    source_count: int,
) -> list[object]:
    if set(report) != {
        "schema_version",
        "status",
        "manifest",
        "git_head",
        "mappings",
        "totals",
    }:
        raise AgentReplayInputError(
            "existing consolidation report has an invalid schema"
        )
    if (
        type(report.get("schema_version")) is not int
        or report.get("schema_version") != 1
        or report.get("status") != "completed"
        or report.get("manifest")
        != {"path": manifest_relative, "sha256": manifest_digest}
    ):
        raise AgentReplayInputError(
            "existing consolidation report conflicts with manifest"
        )
    mappings = report.get("mappings")
    if not isinstance(mappings, list) or len(mappings) != source_count:
        raise AgentReplayInputError(
            "existing consolidation report mapping count differs"
        )
    return mappings


def _validate_completed_rerun(
    report_path: Path,
    *,
    manifest_relative: str,
    manifest_digest: str,
    source_specs: list[dict[str, object]],
    root: Path,
) -> bool:
    if not report_path.exists():
        return False
    report = _load_strict_json(report_path.read_bytes(), label="consolidation report")
    mappings = _completed_report_mappings(
        report,
        manifest_relative=manifest_relative,
        manifest_digest=manifest_digest,
        source_count=len(source_specs),
    )
    expected_logical = 0
    expected_allocated = 0
    for spec, mapping in zip(source_specs, mappings, strict=True):
        if not isinstance(mapping, dict) or set(mapping) != {
            "source",
            "destination",
            "duplicate_of",
            "pre",
            "post",
        }:
            raise AgentReplayInputError(
                "existing consolidation report mapping is invalid"
            )
        source_relative = cast("str", spec["path"])
        destination_relative = _destination_for(source_relative)
        if (
            mapping.get("source") != source_relative
            or mapping.get("destination") != destination_relative
            or mapping.get("duplicate_of") != spec.get("duplicate_of")
            or (root / source_relative).exists()
            or (root / source_relative).is_symlink()
        ):
            raise AgentReplayInputError(
                "completed consolidation state conflicts with manifest"
            )
        destination = root / destination_relative
        if not destination.exists() or destination.is_symlink():
            raise AgentReplayInputError(
                "completed consolidation destination is missing"
            )
        inventory = _artifact_inventory(destination)
        if mapping.get("pre") != _inventory_payload(inventory) or mapping.get(
            "post"
        ) != _inventory_payload(inventory):
            raise AgentReplayInputError(
                "completed consolidation destination differs from report"
            )
        expected_logical += inventory.logical_bytes
        expected_allocated += inventory.allocated_bytes
    if not _valid_completed_totals(
        report,
        sources=len(mappings),
        logical_bytes=expected_logical,
        allocated_bytes=expected_allocated,
    ):
        raise AgentReplayInputError("existing consolidation report totals are invalid")
    return True


def _parse_consolidation_sources(
    manifest_bytes: bytes,
) -> tuple[dict[str, object], ...]:
    manifest = _load_strict_json(manifest_bytes, label="consolidation manifest")
    valid_schema = (
        set(manifest) == {"schema_version", "sources"}
        and type(manifest.get("schema_version")) is int
        and manifest.get("schema_version") == 1
    )
    if not valid_schema:
        raise AgentReplayInputError("consolidation manifest has an invalid schema")
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise AgentReplayInputError("manifest sources must be a non-empty ordered list")
    source_specs: list[dict[str, object]] = []
    for index, raw in enumerate(raw_sources):
        valid_keys = (
            isinstance(raw, dict)
            and set(raw) <= {"path", "duplicate_of"}
            and "path" in raw
        )
        if not valid_keys:
            raise AgentReplayInputError(
                f"manifest source {index} has an invalid schema"
            )
        source_relative = _validated_repository_relative(
            raw["path"], label=f"manifest source {index}"
        )
        spec: dict[str, object] = {"path": source_relative}
        if "duplicate_of" in raw:
            spec["duplicate_of"] = _validated_repository_relative(
                raw["duplicate_of"],
                label=f"manifest source {index} duplicate_of",
            )
        source_specs.append(spec)
    return tuple(source_specs)


def _parse_consolidation_request(values: list[str], root: Path) -> ConsolidationContext:
    if len(values) != _CONSOLIDATION_VALUE_COUNT or values[1] != "--report":
        raise AgentReplayInputError(
            "consolidate-obsolete requires <manifest> --report <report>"
        )
    manifest_relative = _validated_repository_relative(values[0], label="manifest")
    report_relative = _validated_repository_relative(values[2], label="report")
    manifest_path = root / manifest_relative
    report_path = root / report_relative
    if (
        not _path_contains(root / "tmp/plans", manifest_path)
        or not manifest_path.is_file()
    ):
        raise AgentReplayInputError("manifest must be a file under tmp/plans")
    _require_no_symlink_components(manifest_path, root=root, label="manifest path")
    if report_path.parent != manifest_path.parent or report_path == manifest_path:
        raise AgentReplayInputError("report must be a distinct sibling of the manifest")
    _require_no_symlink_components(report_path.parent, root=root, label="report path")
    manifest_bytes = _read_manifest_bytes(manifest_path)
    return ConsolidationContext(
        manifest_relative=manifest_relative,
        manifest_path=manifest_path,
        report_relative=report_relative,
        report_path=report_path,
        manifest_bytes=manifest_bytes,
        manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
        source_specs=_parse_consolidation_sources(manifest_bytes),
    )


def _preflight_consolidation_entry(
    spec: dict[str, object],
    *,
    request: ConsolidationContext,
    root: Path,
    runner: CommandRunner,
) -> ConsolidationEntry:
    source_relative = cast("str", spec["path"])
    source = root / source_relative
    if not _path_contains(root / "tmp", source):
        raise AgentReplayInputError(f"source must be under tmp: {source_relative}")
    if _path_contains(root / "tmp/obsolete", source):
        raise AgentReplayInputError(f"source is already obsolete: {source_relative}")
    if _path_contains(request.manifest_path.parent, source):
        raise AgentReplayInputError(
            f"source is inside the cleanup-plan directory: {source_relative}"
        )
    _require_no_symlink_components(source, root=root, label="source path")
    if not source.exists() or (not source.is_file() and not source.is_dir()):
        raise AgentReplayInputError(
            f"source is missing or has wrong kind: {source_relative}"
        )
    _require_untracked_ignored(source_relative, root, runner)
    destination_relative = _destination_for(source_relative)
    destination = root / destination_relative
    _require_no_symlink_components(
        destination.parent, root=root, label="destination path"
    )
    if destination.exists() or destination.is_symlink():
        raise AgentReplayInputError(
            f"destination already exists: {destination_relative}"
        )
    inventory = _artifact_inventory(source)
    duplicate_relative = cast("str | None", spec.get("duplicate_of"))
    if duplicate_relative is not None:
        duplicate = root / duplicate_relative
        _require_no_symlink_components(duplicate, root=root, label="duplicate_of path")
        if not duplicate.exists() or (
            not duplicate.is_file() and not duplicate.is_dir()
        ):
            raise AgentReplayInputError(
                f"duplicate_of is missing or has wrong kind: {duplicate_relative}"
            )
        if _artifact_inventory(duplicate).identity != inventory.identity:
            raise AgentReplayInputError(
                f"duplicate_of differs from source: {source_relative}"
            )
    return ConsolidationEntry(
        source_relative,
        source,
        destination_relative,
        destination,
        duplicate_relative,
        inventory,
    )


def _validate_consolidation_relationships(entries: list[ConsolidationEntry]) -> None:
    for index, left in enumerate(entries):
        for right in entries[index + 1 :]:
            if _path_contains(left.source, right.source) or _path_contains(
                right.source, left.source
            ):
                raise AgentReplayInputError("manifest sources duplicate or overlap")
    for entry in entries:
        if any(
            _path_contains(entry.source, other.destination)
            or _path_contains(other.destination, entry.source)
            for other in entries
        ):
            raise AgentReplayInputError("source and destination overlap")
        existing_parent = entry.destination.parent
        while not existing_parent.exists():
            existing_parent = existing_parent.parent
        if not _same_filesystem(entry.source, existing_parent):
            raise AgentReplayInputError(
                f"cross-device move refused: {entry.source_relative}"
            )


def _create_destination_parent(destination: Path, created_parents: list[Path]) -> None:
    missing: list[Path] = []
    parent = destination.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir()
        created_parents.append(directory)


def _rollback_consolidation(
    moved: list[ConsolidationEntry], created_parents: list[Path], root: Path
) -> list[str]:
    rollback_errors: list[str] = []
    for entry in reversed(moved):
        try:
            _rename_artifact(entry.destination, entry.source)
        except OSError as exc:
            rollback_errors.append(f"{entry.destination_relative}: {exc}")
    for directory in reversed(created_parents):
        try:
            directory.rmdir()
        except OSError as exc:
            if directory.exists():
                rollback_errors.append(f"{directory.relative_to(root)}: {exc}")
    return rollback_errors


def _verified_consolidation_mappings(
    entries: list[ConsolidationEntry],
) -> list[dict[str, object]]:
    mappings: list[dict[str, object]] = []
    for entry in entries:
        if entry.source.exists() or entry.source.is_symlink():
            raise AgentReplayInputError(
                f"source remains after movement: {entry.source_relative}"
            )
        post = _artifact_inventory(entry.destination)
        if post != entry.inventory:
            raise AgentReplayInputError(
                f"destination verification failed: {entry.destination_relative}"
            )
        mappings.append(
            {
                "source": entry.source_relative,
                "destination": entry.destination_relative,
                "duplicate_of": entry.duplicate_of_relative,
                "pre": _inventory_payload(entry.inventory),
                "post": _inventory_payload(post),
            }
        )
    return mappings


def _execute_consolidation(
    entries: list[ConsolidationEntry],
    *,
    request: ConsolidationContext,
    head: str,
    root: Path,
) -> None:
    created_parents: list[Path] = []
    moved: list[ConsolidationEntry] = []
    try:
        for entry in entries:
            _create_destination_parent(entry.destination, created_parents)
            _rename_artifact(entry.source, entry.destination)
            moved.append(entry)
        mappings = _verified_consolidation_mappings(entries)
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": "completed",
            "manifest": {
                "path": request.manifest_relative,
                "sha256": request.manifest_digest,
            },
            "git_head": head,
            "mappings": mappings,
            "totals": {
                "sources": len(entries),
                "logical_bytes": sum(item.inventory.logical_bytes for item in entries),
                "allocated_bytes": sum(
                    item.inventory.allocated_bytes for item in entries
                ),
            },
        }
        _write_report(request.report_path, payload)
    except BaseException as primary:
        rollback_errors = _rollback_consolidation(moved, created_parents, root)
        if rollback_errors:
            primary.add_note("rollback incomplete: " + "; ".join(rollback_errors))
        raise


def _consolidate_obsolete(values: list[str], root: Path, runner: CommandRunner) -> int:
    """Quarantine reviewed ignored artifacts for manual deletion; never delete them."""
    if values == ["--help"]:
        print(
            "usage: agent-replay consolidate-obsolete <manifest> --report <report>\n"
            "Quarantines explicitly reviewed ignored tmp artifacts under tmp/obsolete "
            "for manual deletion; it does not delete artifacts."
        )
        return 0
    request = _parse_consolidation_request(values, root)
    if _validate_completed_rerun(
        request.report_path,
        manifest_relative=request.manifest_relative,
        manifest_digest=request.manifest_digest,
        source_specs=list(request.source_specs),
        root=root,
    ):
        print(f"consolidation already completed: {request.report_relative}")
        return 0
    entries = [
        _preflight_consolidation_entry(spec, request=request, root=root, runner=runner)
        for spec in request.source_specs
    ]
    _validate_consolidation_relationships(entries)
    head = _capture_required(["git", "rev-parse", "HEAD"], root, runner).strip()
    if _read_manifest_bytes(request.manifest_path) != request.manifest_bytes:
        raise AgentReplayInputError("manifest changed during preflight")
    _execute_consolidation(entries, request=request, head=head, root=root)
    print(f"consolidated {len(entries)} artifacts; report: {request.report_relative}")
    return 0


def _redact_structural_environment(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text

    def redact(value: object, *, key: str | None = None) -> object:
        if isinstance(value, dict):
            return {str(k): redact(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            if key == "Env":
                return [
                    _SECRET_VALUE.sub(r"\1[REDACTED]", item)
                    if isinstance(item, str)
                    else redact(item)
                    for item in value
                ]
            return [redact(item) for item in value]
        return value

    return json.dumps(redact(payload), separators=(",", ":"))


def _bounded_sanitized(value: str, *, limit: int | None = _MAX_DIAGNOSTIC_CHARS) -> str:
    text = _redact_structural_environment(value)
    text = _ANSI_ESCAPE.sub("", text).replace("\x00", "")
    text = _SECRET_VALUE.sub(r"\1[REDACTED]", text)
    text = _URL_CREDENTIALS.sub(r"\1[REDACTED]\2", text)
    if limit is None or len(text) <= limit:
        return text
    omitted = len(text) - limit
    retained_head = limit // 2
    retained_tail = limit - retained_head
    return (
        f"{text[:retained_head]}\n[TRUNCATED {omitted} CHARS]\n{text[-retained_tail:]}"
    )


def _collect_diagnostic_command(
    command: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    environment: dict[str, str] | None = None,
) -> None:
    """Collect one diagnostic; its output and exit code are evidence, not a verdict.

    Returning means only that collection completed. ``inspect-podman`` is best-effort
    diagnosis, so the overall operation does not aggregate command success.
    """
    print(f"\n=== {' '.join(command)} ===")
    try:
        result = runner(
            command,
            cwd=root,
            shell=False,
            check=False,
            timeout=_DIAGNOSTIC_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"collection-error: {_bounded_sanitized(str(exc))}")
        return
    print(f"exit-code: {result.returncode}")
    stdout = _bounded_sanitized(result.stdout)
    stderr = _bounded_sanitized(result.stderr)
    if stdout:
        print("stdout:")
        print(stdout)
    if stderr:
        print("stderr:")
        print(stderr)


def _inspect_podman(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("inspect-podman accepts no arguments")
    socket_path = _podman_socket(root, runner)
    captured_now = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    environment = _docker_context_environment()
    commands = (
        [_PODMAN, "version", "--format", "json"],
        [_PODMAN, "info", "--format", "json"],
        [_PODMAN, "machine", "list", "--format", "json"],
        [_PODMAN, "system", "connection", "list", "--format", "json"],
        ["/usr/bin/stat", "-f", "%N %HT %Sp %Su %Sg", str(socket_path)],
        ["/usr/sbin/lsof", "-n", "-a", "-U", str(socket_path)],
        [_DOCKER, "context", "show"],
        [_DOCKER, "context", "inspect", _PODMAN_DOCKER_CONTEXT],
        *(["/usr/bin/printenv", variable] for variable in DOCKER_SELECTOR_VARIABLES),
        [_DOCKER, "version"],
        [_DOCKER, "info"],
        [_DOCKER_COMPOSE, "version"],
        [_PODMAN, "compose", "version"],
        [_DOCKER, "compose", "config", "--services"],
        [_DOCKER, "compose", "ps", "-a"],
        *(
            [
                _DOCKER,
                "inspect",
                "--format",
                "{{json .State}} {{json .RestartCount}}",
                container,
            ]
            for container in (
                "ontoprism-qlever-ncit",
                "ontoprism-qlever-uberon",
                "ontoprism-postgres",
            )
        ),
        [_DOCKER, "events", "--since", "2h", "--until", captured_now],
        [
            _DOCKER,
            "compose",
            "logs",
            "--since",
            "2h",
            "--no-color",
            "--tail",
            "200",
        ],
    )
    for command in commands:
        _collect_diagnostic_command(command, root, runner, environment=environment)
    return 0


class _CommandTimedOutError(AgentReplayInputError):
    """The command was killed at its timeout; what it started may still be running."""


def _capture_required(
    command: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    environment: dict[str, str] | None = None,
    timeout: int = _DIAGNOSTIC_TIMEOUT_SECONDS,
    display_limit: int | None = _MAX_DIAGNOSTIC_CHARS,
    new_session: bool = False,
) -> str:
    rendered_command = " ".join(command)
    try:
        result = runner(
            command,
            cwd=root,
            shell=False,
            check=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=environment,
            start_new_session=new_session,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_stream_text(exc.stdout)
        stderr = _timeout_stream_text(exc.stderr)
        labelled = _labelled_streams(
            stdout or "", stderr or "", display_limit=display_limit
        )
        raise _CommandTimedOutError(
            f"required command timed out after {timeout}s: {rendered_command}"
            f"{f': {labelled}' if labelled else ''}"
        ) from exc
    except OSError as exc:
        raise AgentReplayInputError(
            f"required command could not start: {rendered_command}: "
            f"{_bounded_sanitized(str(exc), limit=display_limit)}"
        ) from exc
    raw_stdout = result.stdout
    raw_stderr = result.stderr
    labelled = _labelled_streams(raw_stdout, raw_stderr, display_limit=display_limit)
    if result.returncode != 0:
        raise AgentReplayInputError(
            f"required command exited nonzero ({result.returncode}): {rendered_command}"
            f"{f': {labelled}' if labelled else ''}"
        )
    if labelled:
        print(labelled)
    return raw_stdout


def _timeout_stream_text(value: bytes | str | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


def _labelled_streams(stdout: str, stderr: str, *, display_limit: int | None) -> str:
    displayed_stdout = _bounded_sanitized(stdout, limit=display_limit)
    displayed_stderr = _bounded_sanitized(stderr, limit=display_limit)
    return "\n".join(
        line
        for line in (
            f"stdout: {displayed_stdout}" if displayed_stdout else "",
            f"stderr: {displayed_stderr}" if displayed_stderr else "",
        )
        if line
    )


@dataclass(frozen=True)
class PodmanMachine:
    state: Literal["running", "stopped"]
    socket_path: Path
    ssh_port: int


def _inspected_podman_machine(
    root: Path, runner: CommandRunner
) -> tuple[object, Path, int]:
    """Our machine's reported state, API socket and SSH port, whatever the state is."""
    output = _capture_required(
        [_PODMAN, "machine", "inspect", _PODMAN_MACHINE], root, runner
    )
    try:
        payload = json.loads(output)
        machine = payload[0]
        socket_path = Path(machine["ConnectionInfo"]["PodmanSocket"]["Path"])
        ssh = machine["SSHConfig"]
        ssh_port = ssh["Port"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid Podman machine contract") from exc
    if (
        len(payload) != 1
        or machine.get("Name") != _PODMAN_MACHINE
        or machine.get("Rootful") is not False
        or not isinstance(ssh, dict)
        or ssh.get("RemoteUsername") != "core"
        or not isinstance(ssh_port, int)
        or not 0 < ssh_port <= _MAX_TCP_PORT
        or not socket_path.is_absolute()
        or socket_path.name != "ontoprism-vm-api.sock"
        or socket_path.parent.name != "podman"
    ):
        raise AgentReplayInputError("invalid Podman machine contract")
    return machine.get("State"), socket_path, ssh_port


def _podman_machine(root: Path, runner: CommandRunner) -> PodmanMachine:
    state, socket_path, ssh_port = _inspected_podman_machine(root, runner)
    if state not in ("running", "stopped"):  # a tuple: the state may be unhashable
        raise AgentReplayInputError(
            "invalid Podman machine contract: state "
            f"{_bounded_sanitized(repr(state))} is neither 'running' nor 'stopped'. "
            "'starting' is what an interrupted `podman machine start` can leave; if "
            "a rerun reports the same state, inspect the machine (docs/DATA_SETUP.md)"
        )
    return PodmanMachine(
        cast("Literal['running', 'stopped']", state), socket_path, ssh_port
    )


def _podman_socket(root: Path, runner: CommandRunner) -> Path:
    machine = _podman_machine(root, runner)
    if machine.state != "running":
        raise AgentReplayInputError("invalid Podman machine contract")
    return machine.socket_path


def _docker_context_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for variable in DOCKER_SELECTOR_VARIABLES:
        environment.pop(variable, None)
    return environment


def _validate_safe_podman_context(output: str) -> None:
    try:
        contexts = json.loads(output)
        context = contexts[0]
        metadata = context["Metadata"]
        endpoints = context["Endpoints"]
        docker_endpoint = endpoints["docker"]
        host = docker_endpoint["Host"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid safe Docker context contract") from exc
    if (
        len(contexts) != 1
        or context.get("Name") != _PODMAN_DOCKER_CONTEXT
        or not isinstance(metadata, dict)
        or metadata.get("Description") != _PODMAN_DOCKER_CONTEXT_DESCRIPTION
        or set(endpoints) != {"docker"}
        or not isinstance(docker_endpoint, dict)
        or docker_endpoint.get("SkipTLSVerify") is not False
        or not isinstance(host, str)
        or not host.startswith("unix:///")
        or not Path(host.removeprefix("unix://")).is_absolute()
    ):
        raise AgentReplayInputError("invalid safe Docker context contract")


def _validate_active_podman_context(output: str, socket_path: Path) -> None:
    _validate_safe_podman_context(output)
    context = json.loads(output)[0]
    if context["Endpoints"]["docker"]["Host"] != f"unix://{socket_path}":
        raise AgentReplayInputError("active Podman endpoint predicate failed")


def _validate_podman_api_info(output: str) -> None:
    try:
        info = json.loads(output)
        security_options = info["SecurityOptions"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid Podman API contract") from exc
    if (
        not isinstance(info, dict)
        or info.get("OSType") != "linux"
        or not isinstance(info.get("ServerVersion"), str)
        or not cast("str", info["ServerVersion"])
        or not isinstance(info.get("DockerRootDir"), str)
        or not cast("str", info["DockerRootDir"]).endswith("/containers/storage")
        or not isinstance(security_options, list)
        or "name=rootless" not in security_options
        or info.get("ProductLicense") != "Apache-2.0"
    ):
        raise AgentReplayInputError("invalid Podman API contract")


def _validate_machine_connection(output: str, machine: PodmanMachine) -> None:
    try:
        connections = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AgentReplayInputError("invalid Podman connection contract") from exc
    expected_uri = re.compile(
        rf"ssh://core@127\.0\.0\.1:{machine.ssh_port}/run/user/[0-9]+/podman/podman\.sock"
    )
    matches = [
        connection
        for connection in connections
        if isinstance(connection, dict) and connection.get("Name") == _PODMAN_MACHINE
    ]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("URI"), str)
        or expected_uri.fullmatch(cast("str", matches[0]["URI"])) is None
        or matches[0].get("IsMachine") is not True
        or matches[0].get("ReadWrite") is not True
    ):
        raise AgentReplayInputError("invalid Podman connection contract")


def _probe_machine_api(
    machine: PodmanMachine, root: Path, runner: CommandRunner
) -> None:
    if machine.state != "running":
        raise AgentReplayInputError("Podman machine is not running")
    connections = _capture_required(
        [_PODMAN, "system", "connection", "list", "--format", "json"],
        root,
        runner,
    )
    _validate_machine_connection(connections, machine)
    _capture_required(
        [_PODMAN, "machine", "ssh", _PODMAN_MACHINE, "true"], root, runner
    )
    if not machine.socket_path.exists():
        raise AgentReplayInputError(
            f"Podman API socket is absent: {machine.socket_path}"
        )
    info = _capture_required(
        [_DOCKER, "info", "--format", "{{json .}}"],
        root,
        runner,
        environment=_podman_environment(root, machine.socket_path),
    )
    _validate_podman_api_info(info)


def _wait_for_machine_api(root: Path, runner: CommandRunner) -> PodmanMachine:
    failures: list[str] = []
    for attempt in range(1, _PODMAN_READINESS_ATTEMPTS + 1):
        machine = _podman_machine(root, runner)
        try:
            _probe_machine_api(machine, root, runner)
        except AgentReplayInputError as exc:
            failures.append(f"attempt {attempt}: {exc}")
            if attempt < _PODMAN_READINESS_ATTEMPTS:
                _capture_required(["/bin/sleep", "2"], root, runner)
            continue
        print(f"podman-readiness-attempt={attempt}/{_PODMAN_READINESS_ATTEMPTS}")
        return machine
    raise AgentReplayInputError(
        "Podman API readiness failed after "
        f"{_PODMAN_READINESS_ATTEMPTS} bounded attempts: " + " | ".join(failures)
    )


def _activate_podman_docker_context(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "activate-podman-docker-context accepts no arguments"
        )
    socket_path = _podman_socket(root, runner)
    endpoint = f"unix://{socket_path}"
    environment = _docker_context_environment()
    prior_context = _capture_required(
        [_DOCKER, "context", "show"],
        root,
        runner,
        environment=environment,
    ).strip()
    if not prior_context or "\n" in prior_context:
        raise AgentReplayInputError("invalid current Docker context contract")
    print(f"prior-docker-context={prior_context}")

    context_lines = _capture_required(
        [_DOCKER, "context", "ls", "--format", "{{.Name}}"],
        root,
        runner,
        environment=environment,
    ).splitlines()
    contexts = [name.strip() for name in context_lines if name.strip()]
    if len(contexts) != len(set(contexts)):
        raise AgentReplayInputError("invalid Docker context inventory contract")

    context_command: list[str]
    if _PODMAN_DOCKER_CONTEXT in contexts:
        existing = _capture_required(
            [_DOCKER, "context", "inspect", _PODMAN_DOCKER_CONTEXT],
            root,
            runner,
            environment=environment,
        )
        _validate_safe_podman_context(existing)
        context_command = [_DOCKER, "context", "update", _PODMAN_DOCKER_CONTEXT]
    else:
        context_command = [_DOCKER, "context", "create", _PODMAN_DOCKER_CONTEXT]
    _capture_required(
        [
            *context_command,
            "--description",
            _PODMAN_DOCKER_CONTEXT_DESCRIPTION,
            "--docker",
            f"host={endpoint}",
        ],
        root,
        runner,
        environment=environment,
    )
    _capture_required(
        [_DOCKER, "context", "use", _PODMAN_DOCKER_CONTEXT],
        root,
        runner,
        environment=environment,
    )

    inspected = _capture_required(
        [_DOCKER, "context", "inspect", _PODMAN_DOCKER_CONTEXT],
        root,
        runner,
        environment=environment,
    )
    _validate_active_podman_context(inspected, socket_path)
    active_context = _capture_required(
        [_DOCKER, "context", "show"],
        root,
        runner,
        environment=environment,
    ).strip()
    if active_context != _PODMAN_DOCKER_CONTEXT:
        raise AgentReplayInputError("active Docker context predicate failed")
    version = _capture_required(
        [_DOCKER, "version"], root, runner, environment=environment
    )
    if re.search(r"(?m)^\s*Podman Engine:\s*$", version) is None:
        raise AgentReplayInputError("Docker client Podman server predicate failed")
    info = _capture_required(
        [_DOCKER, "info", "--format", "{{json .}}"],
        root,
        runner,
        environment=environment,
    )
    _validate_podman_api_info(info)
    print(f"active-docker-context={active_context}")
    print(f"podman-docker-endpoint={endpoint}")
    print("docker-server=Podman")
    print("podman-api-contract=rootless+containers-storage+apache-2.0")
    return 0


def _check_podman_api(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("check-podman-api accepts no arguments")
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    commands = (
        [_DOCKER, "version"],
        [_DOCKER, "info", "--format", "{{json .}}"],
        [_DOCKER_COMPOSE, "version"],
        [_PODMAN, "compose", "version"],
    )
    for command in commands:
        _capture_required(command, root, runner, environment=environment)
    return 0


def _podman_environment(root: Path, socket_path: Path) -> dict[str, str]:
    environment = dict(os.environ)
    inherited_path = environment.get("PATH", "")
    for variable in DOCKER_SELECTOR_VARIABLES:
        environment.pop(variable, None)
    environment.update(
        {
            "PATH": (
                f"{root / '.venv/bin'}:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
                f"{f':{inherited_path}' if inherited_path else ''}"
            ),
            "DOCKER_HOST": f"unix://{socket_path}",
            "PODMAN_COMPOSE_PROVIDER": _DOCKER_COMPOSE,
        }
    )
    return environment


def _podman_gate(
    values: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    operation: str,
    script: Literal["test-integration", "test-integration-full-store", "verify"],
    routing: Literal["environment", "context"],
) -> Literal[0]:
    if values:
        raise AgentReplayInputError(f"{operation} accepts no arguments")
    if os.environ.get(_PODMAN_ENSURED_ENV) != "1":
        _ensure_podman_stack([], root, runner)
    socket_path = _podman_socket(root, runner)
    if routing == "environment":
        environment = _podman_environment(root, socket_path)
    else:
        environment = _docker_context_environment()
        active_context = _capture_required(
            [_DOCKER, "context", "show"],
            root,
            runner,
            environment=environment,
        ).strip()
        if active_context != _PODMAN_DOCKER_CONTEXT:
            raise AgentReplayInputError("active Docker context predicate failed")
        inspected = _capture_required(
            [_DOCKER, "context", "inspect", _PODMAN_DOCKER_CONTEXT],
            root,
            runner,
            environment=environment,
        )
        _validate_active_podman_context(inspected, socket_path)
    environment[_PODMAN_ENSURED_ENV] = "1"
    _capture_required(
        [_PDM, "run", script],
        root,
        runner,
        environment=environment,
        timeout=_GATE_TIMEOUT_SECONDS,
        display_limit=None,
    )
    return 0


def _podman_test_integration(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    return _podman_gate(
        values,
        root,
        runner,
        operation="podman-test-integration",
        script="test-integration",
        routing="environment",
    )


def _podman_test_full_store(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    return _podman_gate(
        values,
        root,
        runner,
        operation="podman-test-full-store",
        script="test-integration-full-store",
        routing="environment",
    )


def _podman_verify(values: list[str], root: Path, runner: CommandRunner) -> int:
    return _podman_gate(
        values,
        root,
        runner,
        operation="podman-verify",
        script="verify",
        routing="context",
    )


@contextmanager
def _reserved_fixed_ports(ports: tuple[int, ...]) -> Iterator[None]:
    listeners: list[socket.socket] = []
    try:
        for port in ports:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listeners.append(listener)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                message = exc.strerror or str(exc)
                raise AgentReplayInputError(
                    f"fixed port {port} is unavailable: errno {exc.errno}: {message}"
                ) from exc
        yield None
    finally:
        for listener in listeners:
            listener.close()


def _compose_command(compose_file: str) -> list[str]:
    return [
        _DOCKER_COMPOSE,
        "--project-name",
        _PODMAN_PROJECT,
        "--file",
        compose_file,
    ]


def _add_cleanup_note(primary: BaseException, cleanup: AgentReplayInputError) -> None:
    primary.add_note(f"cleanup failure: {cleanup}")


def _podman_compose_up(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-compose-up accepts no arguments")
    compose_file = _require_files(root, ("docker-compose.yml",))[0]
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    inventory = _owned_compose_inventory(root, runner, environment)
    for service in inventory:
        output = _capture_required(
            [_DOCKER, "inspect", f"ontoprism-{service}"],
            root,
            runner,
            environment=environment,
        )
        _validate_compose_resource(
            output, root=root, service=service, require_healthy=False
        )
    missing_ports = tuple(
        int(_SERVICE_EXPECTATIONS[service].host_port)
        for service in _COMPOSE_SERVICES
        if service not in inventory
    )
    with _reserved_fixed_ports(missing_ports):
        pass
    compose = _compose_command(compose_file)
    _capture_required(
        [*compose, "config"],
        root,
        runner,
        environment=environment,
        timeout=_COMPOSE_TIMEOUT_SECONDS,
    )
    try:
        _capture_required(
            [*compose, "up", "--detach", "--wait"],
            root,
            runner,
            environment=environment,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
        )
    except AgentReplayInputError as primary:
        try:
            _capture_required(
                [*compose, "down"],
                root,
                runner,
                environment=environment,
                timeout=_COMPOSE_TIMEOUT_SECONDS,
            )
        except AgentReplayInputError as cleanup:
            _add_cleanup_note(primary, cleanup)
        raise primary
    return 0


@dataclass(frozen=True)
class NamedVolume:
    name: str


@dataclass(frozen=True)
class BindPath:
    relative: Path


@dataclass(frozen=True)
class ServiceExpectation:
    destination: str
    target_port: str
    host_port: str
    source: NamedVolume | BindPath


ServiceExpectations = TypedDict(
    "ServiceExpectations",
    {
        "postgres": ServiceExpectation,
        "qlever-ncit": ServiceExpectation,
        "qlever-uberon": ServiceExpectation,
    },
)


_SERVICE_EXPECTATIONS: ServiceExpectations = {
    "postgres": ServiceExpectation(
        "/var/lib/postgresql/data", "5432/tcp", "5433", NamedVolume(_PODMAN_VOLUME)
    ),
    "qlever-ncit": ServiceExpectation(
        "/data", "7001/tcp", "7888", BindPath(Path("data/qlever-ncit"))
    ),
    "qlever-uberon": ServiceExpectation(
        "/data", "7001/tcp", "7889", BindPath(Path("data/qlever-uberon"))
    ),
}
_DATA_PORTS = tuple(
    int(_SERVICE_EXPECTATIONS[service].host_port) for service in _COMPOSE_SERVICES
)
_APP_PORTS = (*_DATA_PORTS, 8080)


def _mount_source_is_valid(
    mount: dict[str, object], expectation: ServiceExpectation, root: Path
) -> bool:
    if isinstance(expectation.source, NamedVolume):
        return (
            mount.get("Type") == "volume"
            and mount.get("Name") == expectation.source.name
        )
    if isinstance(expectation.source, BindPath):
        mount_source = mount.get("Source")
        return (
            mount.get("Type") == "bind"
            and isinstance(mount_source, str)
            and Path(mount_source).resolve()
            == (root / expectation.source.relative).resolve()
        )
    assert_never(expectation.source)


def _expected_mount(
    mounts: object, expectation: ServiceExpectation, service: ComposeService
) -> dict[str, object]:
    if not isinstance(mounts, list) or any(
        not isinstance(mount, dict) for mount in mounts
    ):
        raise AgentReplayInputError(f"{service} mounts shape predicate failed")
    matching_mounts = [
        mount for mount in mounts if mount.get("Destination") == expectation.destination
    ]
    if len(matching_mounts) != 1:
        raise AgentReplayInputError(f"{service} mount cardinality failed")
    return cast("dict[str, object]", matching_mounts[0])


def _validate_compose_resource(
    output: str,
    *,
    root: Path,
    service: ComposeService,
    require_healthy: bool = True,
) -> None:
    expectation = _SERVICE_EXPECTATIONS[service]
    try:
        resource = json.loads(output)[0]
        labels = resource["Config"]["Labels"]
        health = resource["State"]["Health"]["Status"]
        mounts = resource["Mounts"]
        bindings = resource["NetworkSettings"]["Ports"][expectation.target_port]
        identifier = resource["Id"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid compose resource contract") from exc
    if not isinstance(labels, dict):
        raise AgentReplayInputError(f"{service} owner labels predicate failed")
    mount = _expected_mount(mounts, expectation, service)
    if not _mount_source_is_valid(mount, expectation, root):
        raise AgentReplayInputError(f"{service} mount source failed")
    if (
        not isinstance(identifier, str)
        or re.fullmatch(r"[0-9a-f]{64}", identifier) is None
    ):
        raise AgentReplayInputError(f"{service} identity predicate failed")
    if labels.get("com.docker.compose.project") != _PODMAN_PROJECT:
        raise AgentReplayInputError(f"{service} project owner predicate failed")
    if labels.get("com.docker.compose.service") != service:
        raise AgentReplayInputError(f"{service} service label predicate failed")
    if require_healthy and health != "healthy":
        raise AgentReplayInputError(f"{service} health predicate failed")
    if bindings != [{"HostIp": "127.0.0.1", "HostPort": expectation.host_port}]:
        raise AgentReplayInputError(f"{service} port binding predicate failed")


def _podman_compose_check(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-compose-check accepts no arguments")
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    inventory = _capture_required(
        [
            _DOCKER,
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={_PODMAN_PROJECT}",
            "--format",
            '{{.Label "com.docker.compose.service"}}',
        ],
        root,
        runner,
        environment=environment,
    ).splitlines()
    if len(inventory) != len(_COMPOSE_SERVICES) or set(inventory) != set(
        _COMPOSE_SERVICES
    ):
        raise AgentReplayInputError("service inventory predicate failed")
    for service in _COMPOSE_SERVICES:
        output = _capture_required(
            [_DOCKER, "inspect", f"ontoprism-{service}"],
            root,
            runner,
            environment=environment,
        )
        _validate_compose_resource(output, root=root, service=service)
    _capture_required(
        [
            _DOCKER,
            "exec",
            "ontoprism-postgres",
            "getent",
            "hosts",
            "qlever-ncit",
            "qlever-uberon",
        ],
        root,
        runner,
        environment=environment,
    )
    return 0


def _owned_compose_inventory(
    root: Path, runner: CommandRunner, environment: dict[str, str]
) -> tuple[ComposeService, ...]:
    present: list[ComposeService] = []
    for service in _COMPOSE_SERVICES:
        output = _capture_optional_absent(
            [_DOCKER, "inspect", f"ontoprism-{service}"],
            root,
            runner,
            environment,
            absent_phrase="no such object",
        )
        if output is None:
            continue
        _validate_compose_resource(
            output, root=root, service=service, require_healthy=False
        )
        present.append(service)
    if "postgres" not in present:
        volume = _capture_optional_absent(
            [_DOCKER, "volume", "inspect", _PODMAN_VOLUME],
            root,
            runner,
            environment,
            absent_phrase="no such volume",
        )
        if volume is not None:
            _validate_owned_volume(volume)
    return tuple(present)


def _capture_optional_absent(
    command: list[str],
    root: Path,
    runner: CommandRunner,
    environment: dict[str, str],
    *,
    absent_phrase: str,
) -> str | None:
    try:
        result = runner(
            command,
            cwd=root,
            shell=False,
            check=False,
            timeout=_DIAGNOSTIC_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentReplayInputError(
            f"resource inspection failed: {' '.join(command)}: "
            f"{_bounded_sanitized(str(exc))}"
        ) from exc
    if result.returncode == 0:
        return result.stdout
    detail = _labelled_streams(
        result.stdout, result.stderr, display_limit=_MAX_DIAGNOSTIC_CHARS
    )
    if absent_phrase in f"{result.stdout}\n{result.stderr}".lower():
        return None
    raise AgentReplayInputError(
        f"resource inspection failed ({result.returncode}): {' '.join(command)}"
        f"{f': {detail}' if detail else ''}"
    )


def _dead_gvproxy_cause(machine: PodmanMachine) -> str | None:
    """Report a gvproxy pid that names no process, or nothing.

    Podman writes gvproxy's pid beside the machine's API socket (one ``gvproxy.pid``
    for the directory, not per machine). A missing or unreadable file, a pid that is
    not a positive process id, or a live pid is no observation and reports nothing.
    """
    pid_path = machine.socket_path.parent / "gvproxy.pid"
    pid: int | None = None
    try:
        pid = int(pid_path.read_text())
        if pid <= 0:
            return None
        os.kill(pid, 0)
    except ProcessLookupError:
        if pid is None:
            return None
        return (
            f"gvproxy pid {pid} from {pid_path} names no process while the machine "
            "reports running; "
            "the usual cause is a signal (a kill by port on 5433/7888/7889, or one to "
            "the process group that started the machine); see docs/DATA_SETUP.md"
        )
    except OSError, ValueError, OverflowError:
        return None
    return None


def _start_podman_machine(root: Path, runner: CommandRunner) -> None:
    """Start the VM in its own session.

    vfkit and gvproxy outlive ``podman machine start`` but keep its process group.
    Started from a harness tool or a terminal, a signal to that group (a tool
    timeout, Ctrl-C, a closed terminal) reaches both. gvproxy exits on it, and
    whenever vfkit survives, the machine still reports ``running`` and nothing can
    reach it.
    """
    _capture_required(
        [_PODMAN, "machine", "start", _PODMAN_MACHINE],
        root,
        runner,
        timeout=_PODMAN_MACHINE_TIMEOUT_SECONDS,
        new_session=True,
    )


def _stop_podman_machine(root: Path, runner: CommandRunner) -> None:
    """Stop the VM, waiting out a guest shutdown that outlives the stop command."""
    try:
        _capture_required(
            [_PODMAN, "machine", "stop", _PODMAN_MACHINE],
            root,
            runner,
            timeout=_PODMAN_MACHINE_TIMEOUT_SECONDS,
        )
    except _CommandTimedOutError as slow:
        print(
            f"machine-stop-outlived-timeout={_PODMAN_MACHINE_TIMEOUT_SECONDS}s",
            flush=True,
        )
        state: object = "running"
        try:
            # A machine polled mid-shutdown is in transition by construction: any state
            # but `stopped` means "not yet". Every other look (`_podman_machine`) keeps
            # the strict contract; only this poll tolerates a state in between.
            for _attempt in range(_PODMAN_STOP_WAIT_ATTEMPTS):
                _capture_required(["/bin/sleep", "10"], root, runner)
                state, _socket_path, _ssh_port = _inspected_podman_machine(root, runner)
                if state == "stopped":
                    return
        except AgentReplayInputError as waiting:
            waiting.add_note(f"while waiting for the machine to stop after: {slow}")
            raise
        advice = (
            "The guest may still be shutting down: rerun ensure-podman-stack, which "
            "is safe to repeat"
            if state == "running"
            else "Rerun ensure-podman-stack, which is safe to repeat and says what "
            "to check for a state that is neither 'running' nor 'stopped'"
        )
        raise AgentReplayInputError(
            f"{slow}; about {_PODMAN_STOP_WAIT_ATTEMPTS * 10}s later the machine "
            f"reports {_bounded_sanitized(repr(state))}. {advice}"
        ) from slow


def _ensure_podman_stack(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("ensure-podman-stack accepts no arguments")
    machine = _podman_machine(root, runner)
    machine_action = "no-op"
    if machine.state == "stopped":
        _start_podman_machine(root, runner)
        machine_action = "started"
    else:
        try:
            _probe_machine_api(machine, root, runner)
        except AgentReplayInputError as stale:
            # Flushed: the stop below can block for minutes, and a harness that ends
            # the run at its own timeout would take a buffered diagnosis with it.
            print(
                f"stale-machine-diagnostic={_bounded_sanitized(str(stale))}",
                flush=True,
            )
            cause = _dead_gvproxy_cause(machine)
            if cause is not None:
                print(f"stale-machine-cause={cause}", flush=True)
            _stop_podman_machine(root, runner)
            _start_podman_machine(root, runner)
            machine_action = "restarted-stale"

    machine = _wait_for_machine_api(root, runner)
    _activate_podman_docker_context([], root, runner)
    environment = _podman_environment(root, machine.socket_path)
    stack_action = "no-op"
    try:
        _podman_compose_check([], root, runner)
    except AgentReplayInputError as unhealthy:
        print(f"stack-reconcile-reason={_bounded_sanitized(str(unhealthy))}")
        inventory = _owned_compose_inventory(root, runner, environment)
        for service in inventory:
            output = _capture_required(
                [_DOCKER, "inspect", f"ontoprism-{service}"],
                root,
                runner,
                environment=environment,
            )
            _validate_compose_resource(
                output, root=root, service=service, require_healthy=False
            )
        _podman_compose_up([], root, runner)
        stack_action = "started-or-reconciled"

    _check_podman_api([], root, runner)
    _podman_compose_check([], root, runner)
    print(f"machine-action={machine_action}")
    print(f"stack-action={stack_action}")
    print(f"final-endpoint=unix://{machine.socket_path}")
    print(f"active-docker-context={_PODMAN_DOCKER_CONTEXT}")
    print("stack-health=healthy")
    return 0


def _podman_compose_down(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-compose-down accepts no arguments")
    compose_file = _require_files(root, ("docker-compose.yml",))[0]
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    for service in _COMPOSE_SERVICES:
        try:
            output = _capture_required(
                [_DOCKER, "inspect", f"ontoprism-{service}"],
                root,
                runner,
                environment=environment,
            )
        except AgentReplayInputError as exc:
            if "no such object" in str(exc).lower():
                continue
            raise
        try:
            resource = json.loads(output)[0]
            labels = resource["Config"]["Labels"]
            identifier = resource["Id"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AgentReplayInputError("invalid cleanup ownership contract") from exc
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[0-9a-f]{64}", identifier) is None
            or labels.get("com.docker.compose.project") != _PODMAN_PROJECT
            or labels.get("com.docker.compose.service") != service
        ):
            raise AgentReplayInputError("invalid cleanup ownership contract")
    _capture_required(
        [
            *_compose_command(compose_file),
            "down",
        ],
        root,
        runner,
        environment=environment,
        timeout=_COMPOSE_TIMEOUT_SECONDS,
    )
    volume_output = _capture_required(
        [_DOCKER, "volume", "inspect", _PODMAN_VOLUME],
        root,
        runner,
        environment=environment,
    )
    _validate_owned_volume(volume_output)
    return 0


def _validate_owned_volume(output: str) -> None:
    try:
        volume = json.loads(output)[0]
        name = volume["Name"]
        labels = volume["Labels"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("named volume identity predicate failed") from exc
    if not isinstance(labels, dict):
        raise AgentReplayInputError("named volume labels predicate failed")
    if name != _PODMAN_VOLUME:
        raise AgentReplayInputError("named volume identity predicate failed")
    if (
        labels.get("com.docker.compose.project") != POSTGRES_VOLUME_PROJECT
        or labels.get("com.docker.compose.volume") != "ontoprism_pg_data"
    ):
        raise AgentReplayInputError("named volume ownership predicate failed")


def _write_fixed_override(path: Path, content: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(content, sort_keys=True), encoding="utf-8")


def _remove_operation_paths(*paths: Path) -> list[AgentReplayInputError]:
    errors: list[AgentReplayInputError] = []
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(
                AgentReplayInputError(
                    f"temporary path cleanup failed: {path.name}: {exc}"
                )
            )
    return errors


def _finish_cleanup(
    primary: BaseException | None,
    cleanup_errors: list[AgentReplayInputError],
) -> None:
    if primary is not None:
        for cleanup in cleanup_errors:
            _add_cleanup_note(primary, cleanup)
        raise primary
    if cleanup_errors:
        first, *rest = cleanup_errors
        for cleanup in rest:
            _add_cleanup_note(first, cleanup)
        raise first


def _podman_health_reject(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-health-reject accepts no arguments")
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    override = root / _POC_DIR / "broken-health.override.yml"
    data_dir = root / _POC_DIR / "broken-health-postgres"
    compose = [
        _DOCKER_COMPOSE,
        "--project-name",
        "ontoprism-podman-health-reject",
        "--file",
        str(override),
    ]
    primary: BaseException | None = None
    cleanup_errors: list[AgentReplayInputError] = []
    compose_attempted = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _write_fixed_override(
            override,
            {
                "services": {
                    "broken": {
                        "image": _POSTGRES_IMAGE,
                        "environment": {
                            "POSTGRES_USER": "ontoprism",
                            "POSTGRES_PASSWORD": "ontoprism",
                            "POSTGRES_DB": "ontoprism",
                        },
                        "volumes": [f"{data_dir}:/var/lib/postgresql/data"],
                        "healthcheck": {
                            "test": ["CMD", "/bin/false"],
                            "interval": "1s",
                            "timeout": "1s",
                            "retries": 1,
                        },
                    }
                }
            },
        )
        compose_attempted = True
        result = runner(
            [*compose, "up", "--detach", "--wait", "--wait-timeout", "30"],
            cwd=root,
            shell=False,
            check=False,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode == 0:
            raise AgentReplayInputError("broken-health Compose project was accepted")
        raw_detail = f"stdout: {result.stdout}\nstderr: {result.stderr}"
        detail = _bounded_sanitized(raw_detail)
        if (
            "unhealthy" not in raw_detail.lower()
            or "ontoprism-podman-health-reject-broken-1" not in raw_detail
        ):
            raise AgentReplayInputError(
                "broken-health Compose failed for an unexpected reason"
            )
        print(f"broken-health-rejected exit={result.returncode} detail={detail}")
    except AgentReplayInputError as exc:
        primary = exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        primary = exc
    finally:
        if compose_attempted:
            try:
                _capture_required(
                    [*compose, "down"],
                    root,
                    runner,
                    environment=environment,
                    timeout=_COMPOSE_TIMEOUT_SECONDS,
                )
            except AgentReplayInputError as cleanup:
                cleanup_errors.append(cleanup)
        cleanup_errors.extend(_remove_operation_paths(override, data_dir))
    _finish_cleanup(primary, cleanup_errors)
    return 0


@dataclass(frozen=True)
class AppSmokePrecondition:
    environment: dict[str, str]
    volume: NamedVolume


def _app_smoke_precondition(root: Path, runner: CommandRunner) -> AppSmokePrecondition:
    with _reserved_fixed_ports(_APP_PORTS):
        pass
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    volume_output = _capture_required(
        [_DOCKER, "volume", "inspect", _PODMAN_VOLUME],
        root,
        runner,
        environment=environment,
    )
    _validate_owned_volume(volume_output)
    for service in _COMPOSE_SERVICES:
        try:
            _capture_required(
                [_DOCKER, "inspect", f"ontoprism-{service}"],
                root,
                runner,
                environment=environment,
            )
        except AgentReplayInputError as exc:
            if "no such object" in str(exc).lower():
                continue
            raise
        raise AgentReplayInputError(
            f"app-smoke precondition failed: existing resource ontoprism-{service}"
        )
    return AppSmokePrecondition(environment, NamedVolume(_PODMAN_VOLUME))


def _podman_app_smoke(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-app-smoke accepts no arguments")
    _require_files(
        root,
        (
            "docker-compose.yml",
            "docker-compose.app.yml",
            "Caddyfile",
        ),
    )
    precondition = _app_smoke_precondition(root, runner)
    environment = precondition.environment
    override = root / _POC_DIR / "app-podman.override.yml"
    refresh_dir = root / _POC_DIR / "app-refresh"
    compose = [
        _DOCKER_COMPOSE,
        "--project-name",
        "ontoprism-podman-app",
        "--file",
        str(root / "docker-compose.yml"),
        "--file",
        str(root / "docker-compose.app.yml"),
        "--file",
        str(override),
    ]
    primary: BaseException | None = None
    cleanup_errors: list[AgentReplayInputError] = []
    compose_attempted = False
    try:
        refresh_dir.mkdir(parents=True, exist_ok=True)
        _write_fixed_override(
            override,
            {
                "services": {
                    "api": {
                        "volumes": [
                            "./data/cadsr:/app/data/cadsr:ro",
                            f"{refresh_dir}:/app/refresh",
                        ]
                    }
                },
                "volumes": {
                    "ontoprism_pg_data": {
                        "external": True,
                        "name": precondition.volume.name,
                    }
                },
            },
        )
        compose_attempted = True
        _capture_required(
            [*compose, "up", "--detach", "--wait", "--build"],
            root,
            runner,
            environment=environment,
            timeout=_GATE_TIMEOUT_SECONDS,
        )
        root_page = _capture_required(
            [
                "/usr/bin/curl",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "10",
                "--retry-all-errors",
                "--retry-delay",
                "0",
                "--max-time",
                "180",
                "http://127.0.0.1:8080/",
            ],
            root,
            runner,
            environment=environment,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
        )
        bff = _capture_required(
            [
                "/usr/bin/curl",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "10",
                "--retry-all-errors",
                "--retry-delay",
                "0",
                "--max-time",
                "180",
                "http://127.0.0.1:8080/api/v1/ncit/concepts/C3262",
            ],
            root,
            runner,
            environment=environment,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
        )
        if "<html" not in root_page.lower() or '"code":"C3262"' not in re.sub(
            r"\s+", "", bff
        ):
            raise AgentReplayInputError("full-app Caddy/BFF smoke contract failed")
        dns = _capture_required(
            [
                _DOCKER,
                "exec",
                "ontoprism-api",
                "python",
                "-c",
                "import socket;[socket.getaddrinfo(n,None) for n in "
                "('web','postgres','qlever-ncit','qlever-uberon')]",
            ],
            root,
            runner,
            environment=environment,
        )
        if dns.strip():
            raise AgentReplayInputError("service DNS check emitted unexpected output")
        print("app-smoke=caddy-root+bff-C3262+service-dns")
    except AgentReplayInputError as exc:
        primary = exc
    except OSError as exc:
        primary = exc
    finally:
        if compose_attempted:
            try:
                _capture_required(
                    [*compose, "down"],
                    root,
                    runner,
                    environment=environment,
                    timeout=_COMPOSE_TIMEOUT_SECONDS,
                )
            except AgentReplayInputError as cleanup:
                cleanup_errors.append(cleanup)
        cleanup_errors.extend(_remove_operation_paths(override, refresh_dir))
    _finish_cleanup(primary, cleanup_errors)
    return 0


# Frozen command registry: remove retired one-off operations; never add new ones.
_OPERATIONS: dict[str, Operation] = {
    "consolidate-obsolete": _consolidate_obsolete,
    "inspect-podman": _inspect_podman,
    "ensure-podman-stack": _ensure_podman_stack,
    "activate-podman-docker-context": _activate_podman_docker_context,
    "check-podman-api": _check_podman_api,
    "podman-test-integration": _podman_test_integration,
    "podman-test-full-store": _podman_test_full_store,
    "podman-verify": _podman_verify,
    "podman-compose-up": _podman_compose_up,
    "podman-compose-check": _podman_compose_check,
    "podman-compose-down": _podman_compose_down,
    "podman-health-reject": _podman_health_reject,
    "podman-app-smoke": _podman_app_smoke,
}


def run_agent_replay(
    arguments: list[str],
    root: Path,
    *,
    runner: CommandRunner | None = None,
) -> int:
    """Validate and run one fixed replay operation without shell interpretation."""
    runner = runner or _subprocess_runner
    root = root.resolve()
    if not arguments:
        raise AgentReplayInputError("replay operation is unsupported")
    operation, *values = arguments
    handler = _OPERATIONS.get(operation)
    if handler is None:
        raise AgentReplayInputError("replay operation is unsupported")
    return handler(values, root, runner)


def main() -> int:
    try:
        return run_agent_replay(sys.argv[1:], Path(__file__).resolve().parents[2])
    except AgentReplayInputError as exc:
        print(str(exc), file=sys.stderr)
        for note in getattr(exc, "__notes__", ()):
            print(note, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
