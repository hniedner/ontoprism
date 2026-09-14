"""Bounded inventory and managed immutable-generation cleanup."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import shutil
import stat
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from ontolib.decomposition.artifact_contract import COMPOSE_PROJECT
from ontolib.decomposition.run_artifacts import (
    ArtifactManifest,
    load_artifact_record,
    resolve_parent_manifest,
)

_PLAN_SCHEMA = 1
_DEFAULT_TOP_N = 5
_MAX_SCAN_ERRORS = 10
_GENERATION_PATH_PARTS = 2


@dataclass(frozen=True)
class RetentionClassPolicy:
    owner: str
    expiry: str
    cleanup_eligible: bool


@dataclass(frozen=True)
class RetentionPolicy:
    generation_root: str
    cleanup_plan_root: str
    classes: dict[str, RetentionClassPolicy]
    budgets: dict[str, object]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory", help="report local artifacts without mutation")
    commands.add_parser("plan", help="write a deterministic managed-generation plan")
    apply = commands.add_parser("apply", help="apply an exact immutable cleanup plan")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--identity", required=True)
    return result


def load_retention_policy(path: Path) -> RetentionPolicy:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"retention policy cannot be read: {path}") from exc
    if payload.get("schema_version") != 1:
        raise ValueError("retention policy schema version is unsupported")
    managed = payload.get("managed", {})
    generation_root = managed.get("generation_root", "tmp/artifacts/v1/generations")
    if generation_root != "tmp/artifacts/v1/generations":
        raise ValueError("managed generation root must be the v1 generation registry")
    cleanup_plan_root = managed.get(
        "cleanup_plan_root", "tmp/artifacts/v1/cleanup-plans"
    )
    if cleanup_plan_root != "tmp/artifacts/v1/cleanup-plans":
        raise ValueError("managed cleanup plan root must be the v1 plan registry")
    classes = _parse_class_policies(payload.get("classes"))
    budgets = {
        name: payload.get("budgets", {}).get(name, "not-declared")
        for name in ("tmp", "data")
    }
    return RetentionPolicy(
        generation_root=generation_root,
        cleanup_plan_root=cleanup_plan_root,
        classes=classes,
        budgets=budgets,
    )


def _parse_class_policies(value: object) -> dict[str, RetentionClassPolicy]:
    if not isinstance(value, dict):
        raise ValueError("retention classes must be declared")
    result: dict[str, RetentionClassPolicy] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            raise ValueError("retention class declaration is invalid")
        owner = raw.get("owner")
        expiry = raw.get("expiry")
        cleanup = raw.get("cleanup_eligible", False)
        if not isinstance(owner, str) or not isinstance(expiry, str):
            raise ValueError(f"retention class {name} owner or expiry is invalid")
        if not isinstance(cleanup, bool):
            raise ValueError(f"retention class {name} cleanup eligibility is invalid")
        result[name] = RetentionClassPolicy(owner, expiry, cleanup)
    return result


def _scan_error_payload(errors: list[dict[str, str]], count: int) -> dict[str, object]:
    return {
        "count": count,
        "entries": errors,
        "truncated": count > len(errors),
    }


def _record_scan_error(errors: list[dict[str, str]], path: Path, exc: OSError) -> None:
    if len(errors) < _MAX_SCAN_ERRORS:
        message = f"{type(exc).__name__}: {exc}"
        errors.append({"path": str(path)[:500], "error": message[:500]})


class _TreeScan:
    __slots__ = (
        "allocated",
        "base",
        "error_count",
        "errors",
        "files",
        "largest",
        "logical",
        "top_n",
    )

    def __init__(self, base: Path, top_n: int) -> None:
        self.base = base
        self.top_n = top_n
        self.logical = 0
        self.allocated = 0
        self.files = 0
        self.error_count = 0
        self.errors: list[dict[str, str]] = []
        self.largest: list[tuple[int, str]] = []

    def record_error(self, path: Path, exc: OSError) -> None:
        self.error_count += 1
        _record_scan_error(self.errors, path, exc)

    def add_metadata(self, details: os.stat_result, relative: str) -> None:
        if not stat.S_ISREG(details.st_mode):
            return
        self.logical += details.st_size
        self.allocated += details.st_blocks * 512
        self.files += 1
        item = (details.st_size, relative)
        if len(self.largest) < self.top_n:
            heapq.heappush(self.largest, item)
        elif item > self.largest[0]:
            heapq.heapreplace(self.largest, item)

    def add_file(self, candidate: Path, relative: str) -> None:
        try:
            details = candidate.lstat()
        except OSError as exc:
            self.record_error(candidate, exc)
            return
        self.add_metadata(details, relative)

    def walk(self) -> None:
        for root, directories, names in os.walk(
            self.base, followlinks=False, onerror=self._walk_error
        ):
            directories[:] = self._regular_directories(Path(root), directories)
            for name in names:
                candidate = Path(root) / name
                self.add_file(candidate, candidate.relative_to(self.base).as_posix())

    def _walk_error(self, exc: OSError) -> None:
        self.record_error(Path(exc.filename or self.base), exc)

    def _regular_directories(self, root: Path, names: list[str]) -> list[str]:
        retained: list[str] = []
        for name in names:
            candidate = root / name
            try:
                details = candidate.lstat()
            except OSError as exc:
                self.record_error(candidate, exc)
                continue
            if not stat.S_ISLNK(details.st_mode):
                retained.append(name)
        return retained

    def result(self) -> dict[str, object]:
        top = [
            {"path": relative, "logical_bytes": size}
            for size, relative in sorted(self.largest, reverse=True)
        ]
        result: dict[str, object] = {
            "available": True,
            "logical_bytes": self.logical,
            "allocated_bytes": self.allocated,
            "files": self.files,
            "top_files": top,
            "top_files_truncated": self.files > len(top),
        }
        if self.error_count:
            result["status"] = "error"
            result["scan_errors"] = _scan_error_payload(self.errors, self.error_count)
        return result


def _tree_summary(path: Path, *, top_n: int = _DEFAULT_TOP_N) -> dict[str, object]:
    scan = _TreeScan(path, top_n)
    try:
        root_details = path.lstat()
    except FileNotFoundError:
        return {
            "available": False,
            "logical_bytes": 0,
            "allocated_bytes": 0,
            "files": 0,
            "top_files": [],
            "top_files_truncated": False,
        }
    except OSError as exc:
        scan.record_error(path, exc)
        return {
            "available": False,
            "logical_bytes": 0,
            "allocated_bytes": 0,
            "files": 0,
            "top_files": [],
            "top_files_truncated": False,
            "status": "error",
            "scan_errors": _scan_error_payload(scan.errors, scan.error_count),
        }
    if stat.S_ISREG(root_details.st_mode):
        scan.add_metadata(root_details, path.name)
    elif stat.S_ISDIR(root_details.st_mode):
        scan.walk()
    return scan.result()


def _run_command(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command, cwd=root, check=False, capture_output=True, text=True
    )


def _default_worktrees(root: Path) -> dict[str, object]:
    git = shutil.which("git")
    if git is None:
        return {"status": "unavailable", "reason": "git-not-found", "entries": []}
    listing = _run_command([git, "worktree", "list", "--porcelain"], root)
    if listing.returncode != 0:
        return {"status": "error", "error": listing.stderr.strip(), "entries": []}
    paths = [
        Path(line.removeprefix("worktree "))
        for line in listing.stdout.splitlines()
        if line.startswith("worktree ")
    ]
    entries = [_audit_worktree(git, root, path) for path in paths]
    return {"status": "empty" if not entries else "ok", "entries": entries}


def _audit_worktree(git: str, repository: Path, path: Path) -> dict[str, object]:
    common = _run_command(
        [git, "-C", str(path), "rev-parse", "--git-common-dir"], repository
    )
    head = _run_command([git, "-C", str(path), "rev-parse", "HEAD"], repository)
    branch = _run_command(
        [git, "-C", str(path), "symbolic-ref", "-q", "HEAD"], repository
    )
    status = _run_command(
        [git, "-C", str(path), "status", "--porcelain=v1", "--ignored"], repository
    )
    if common.returncode != 0 or head.returncode != 0 or status.returncode != 0:
        return {
            "path": str(path),
            "status": "error",
            "error": (common.stderr or head.stderr or status.stderr).strip(),
            "resolution": "operator-action-required",
        }
    lines = status.stdout.splitlines()
    branch_name = branch.stdout.strip() if branch.returncode == 0 else None
    common_path = Path(common.stdout.strip())
    if not common_path.is_absolute():
        common_path = (path / common_path).resolve()
    return {
        "path": str(path),
        "common_git_dir": str(common_path),
        "head": head.stdout.strip(),
        "branch": branch_name,
        "state": "branch" if branch_name is not None else "detached",
        "dirty": sum(not line.startswith(("??", "!!")) for line in lines),
        "untracked": sum(line.startswith("??") for line in lines),
        "ignored": sum(line.startswith("!!") for line in lines),
        "unique_commit_status": _unique_commit_status(
            git, repository, head.stdout.strip()
        ),
        "resolution": "operator-action-required",
    }


def _unique_commit_status(git: str, root: Path, head: str) -> str:
    result = _run_command([git, "branch", "--all", "--contains", head], root)
    if result.returncode != 0:
        return "unknown"
    branches = [
        line.strip().lstrip("* ") for line in result.stdout.splitlines() if line.strip()
    ]
    return "unique" if len(branches) <= 1 else "reachable-from-multiple-branches"


def _default_compose_resources(root: Path) -> dict[str, object]:
    docker = shutil.which("docker")
    if docker is None:
        return {
            "status": "unavailable",
            "reason": "docker-not-found",
            "project": COMPOSE_PROJECT,
            "items": [],
        }
    results = [
        _compose_listing(docker, root, "container"),
        _compose_listing(docker, root, "volume"),
    ]
    errors = [str(item["error"]) for item in results if item["status"] == "error"]
    items: list[object] = []
    for item in results:
        listed = item["items"]
        if isinstance(listed, list):
            items.extend(listed)
    if errors:
        return {
            "status": "error",
            "error": "; ".join(errors),
            "project": COMPOSE_PROJECT,
            "items": items,
        }
    return {
        "status": "empty" if not items else "ok",
        "project": COMPOSE_PROJECT,
        "items": items,
    }


def _compose_listing(docker: str, root: Path, kind: str) -> dict[str, object]:
    if kind == "container":
        command = [
            docker,
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={COMPOSE_PROJECT}",
            "--format",
            "{{.Names}}|{{.State}}",
        ]
    else:
        command = [
            docker,
            "volume",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={COMPOSE_PROJECT}",
            "--format",
            "{{.Name}}",
        ]
    result = _run_command(command, root)
    if result.returncode != 0:
        return {
            "status": "error",
            "error": f"{kind}: {result.stderr.strip()}",
            "items": [],
        }
    items: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        name, _, state = line.partition("|")
        if name:
            items.append(
                {
                    "kind": kind,
                    "name": name,
                    "state": state or "active",
                    "protected": True,
                }
            )
    return {"status": "ok", "items": items}


def _manifest_artifacts(
    root: Path, manifest_path: Path, record: ArtifactManifest
) -> tuple[list[dict[str, object]], set[Path]]:
    legacy = record.family == "legacy-in-place"
    artifacts: list[dict[str, object]] = []
    managed_paths: set[Path] = set()
    for artifact in record.artifact_records:
        artifact_path = (
            root / artifact.relative_path
            if legacy
            else manifest_path.parent / artifact.relative_path
        )
        managed_paths.add(artifact_path)
        try:
            details = artifact_path.lstat()
        except OSError:
            availability, size = "missing", None
        else:
            size = details.st_size
            availability = (
                "size-matches-manifest"
                if stat.S_ISREG(details.st_mode) and size == artifact.size
                else "size-or-type-differs"
            )
        artifacts.append(
            {"path": artifact.relative_path, "availability": availability, "size": size}
        )
    return artifacts, managed_paths


def _managed_entry(
    root: Path, path: Path, record: ArtifactManifest
) -> tuple[dict[str, object], set[Path]]:
    artifacts, managed_paths = _manifest_artifacts(root, path, record)
    available = all(
        item["availability"] == "size-matches-manifest" for item in artifacts
    )
    completion = (
        record.family == "legacy-in-place" or (path.parent / ".complete").is_file()
    )
    return (
        {
            "path": path.relative_to(root).as_posix(),
            "record_type": record.record_type,
            "availability": "complete" if available and completion else "partial",
            "manifest_identity": record.manifest_identity,
            "retention_class": record.retention.retention_class,
            "owner": record.retention.owner,
            "parents": [parent.manifest_identity for parent in record.parents],
            "artifacts": artifacts,
        },
        managed_paths,
    )


def _record_paths(artifacts_root: Path) -> list[Path]:
    paths = list((artifacts_root / "legacy-in-place").glob("*.json"))
    paths.extend((artifacts_root / "unavailable").glob("*.json"))
    paths.extend((artifacts_root / "generations").glob("*/*/manifest.json"))
    return sorted(paths)


def _managed_records(
    root: Path, artifacts_root: Path
) -> tuple[list[dict[str, object]], set[Path]]:
    records: list[dict[str, object]] = []
    managed_paths: set[Path] = set()
    for path in _record_paths(artifacts_root):
        try:
            record = load_artifact_record(path)
        except ValueError as exc:
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "availability": "invalid",
                    "error": str(exc),
                }
            )
            continue
        if isinstance(record, ArtifactManifest):
            entry, paths = _managed_entry(root, path, record)
            records.append(entry)
            managed_paths.update(paths)
        else:
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "record_type": record.record_type,
                    "availability": "unavailable",
                    "run_id": record.run_id,
                    "expected_sha256": record.expected_sha256,
                    "references": list(record.references),
                }
            )
    records.extend(_partial_generation_entries(root, artifacts_root))
    return records, managed_paths


def _cleanup_plan_entry(
    root: Path, artifacts_root: Path, policy: RetentionPolicy, top_n: int
) -> dict[str, object] | None:
    path = artifacts_root / "cleanup-plans"
    if not path.exists():
        return None
    declared = policy.classes.get("cleanup-plan")
    if declared is None:
        raise ValueError("cleanup-plan retention class is not declared")
    return {
        "path": path.relative_to(root).as_posix(),
        "record_type": "managed-cleanup-plan-root",
        **_tree_summary(path, top_n=top_n),
        "retention_class": "cleanup-plan",
        "owner": declared.owner,
        "expiry_policy": declared.expiry,
    }


def _partial_generation_entries(
    root: Path, artifacts_root: Path
) -> list[dict[str, object]]:
    generations = artifacts_root / "generations"
    if not generations.is_dir():
        return []
    entries: list[dict[str, object]] = []
    for family in sorted(generations.iterdir()):
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


def _unmanaged_summaries(
    root: Path, artifacts_root: Path, managed_paths: set[Path], top_n: int
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    summaries: list[dict[str, object]] = []
    usage: dict[str, dict[str, object]] = {}
    for base_name in ("tmp", "data"):
        base = root / base_name
        if not base.is_dir():
            usage[base_name] = {
                "available": False,
                "logical_bytes": 0,
                "allocated_bytes": 0,
                "files": 0,
            }
            continue
        top_level_files: list[tuple[Path, dict[str, object]]] = []
        root_totals: dict[str, object] = {
            "available": True,
            "logical_bytes": 0,
            "allocated_bytes": 0,
            "files": 0,
        }
        root_errors: list[dict[str, str]] = []
        root_error_count = 0
        for path in sorted(base.iterdir()):
            summary = _tree_summary(path, top_n=top_n)
            root_error_count += _merge_root_summary(root_totals, root_errors, summary)
            if (
                path == artifacts_root
                or path in artifacts_root.parents
                or path in managed_paths
            ):
                continue
            if path.is_file() and not path.is_symlink():
                top_level_files.append((path, summary))
                continue
            summaries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    **summary,
                    "retention_class": "unknown",
                    "owner": "unknown",
                    "reference_status": "unknown",
                }
            )
        if top_level_files:
            summaries.append(
                _top_level_file_summary(root, base, top_level_files, top_n)
            )
        if root_error_count:
            root_totals["status"] = "error"
            root_totals["scan_errors"] = _scan_error_payload(
                root_errors, root_error_count
            )
        usage[base_name] = root_totals
    return summaries, usage


def _merge_root_summary(
    totals: dict[str, object],
    errors: list[dict[str, str]],
    summary: dict[str, object],
) -> int:
    for field_name in ("logical_bytes", "allocated_bytes", "files"):
        totals[field_name] = _required_int(
            totals[field_name], field_name
        ) + _required_int(summary[field_name], field_name)
    scan_errors = summary.get("scan_errors")
    if not isinstance(scan_errors, dict):
        return 0
    entries = scan_errors.get("entries")
    if isinstance(entries, list):
        errors.extend(item for item in entries if isinstance(item, dict))
        del errors[_MAX_SCAN_ERRORS:]
    return _required_int(scan_errors.get("count"), "scan error count")


def _top_level_file_summary(
    root: Path,
    base: Path,
    paths: list[tuple[Path, dict[str, object]]],
    top_n: int,
) -> dict[str, object]:
    largest = sorted(
        (
            (_required_int(summary["logical_bytes"], "logical bytes"), path.name)
            for path, summary in paths
        ),
        reverse=True,
    )[:top_n]
    return {
        "path": f"{base.relative_to(root).as_posix()}/[top-level-files]",
        "available": True,
        "logical_bytes": sum(
            _required_int(summary["logical_bytes"], "logical bytes")
            for _path, summary in paths
        ),
        "allocated_bytes": sum(
            _required_int(summary["allocated_bytes"], "allocated bytes")
            for _path, summary in paths
        ),
        "files": len(paths),
        "top_files": [
            {"path": relative, "logical_bytes": size} for size, relative in largest
        ],
        "top_files_truncated": len(paths) > len(largest),
        "retention_class": "unknown",
        "owner": "unknown",
        "reference_status": "unknown",
    }


def inventory_repository(
    root: Path,
    *,
    git_worktrees: dict[str, object] | None = None,
    compose_resources: dict[str, object] | None = None,
    top_n: int = _DEFAULT_TOP_N,
) -> dict[str, Any]:
    root = root.resolve()
    policy = load_retention_policy(root / "artifact-retention.toml")
    artifacts_root = root / "tmp/artifacts/v1"
    managed, managed_paths = _managed_records(root, artifacts_root)
    cleanup_plans = _cleanup_plan_entry(root, artifacts_root, policy, top_n)
    if cleanup_plans is not None:
        managed.append(cleanup_plans)
    unmanaged, ignored_usage = _unmanaged_summaries(
        root, artifacts_root, managed_paths, top_n
    )
    return {
        "mode": "read-only-inventory",
        "managed_generation_root": policy.generation_root,
        "ignored_usage": ignored_usage,
        "budgets": policy.budgets,
        "managed_records": managed,
        "unmanaged_root_summaries": unmanaged,
        "summary_counts": {
            "managed_records": len(managed),
            "partial_generations": sum(
                item.get("availability") == "partial" for item in managed
            ),
            "unavailable_records": sum(
                item.get("availability") == "unavailable" for item in managed
            ),
            "unmanaged_roots": len(unmanaged),
            "top_n_per_root": top_n,
        },
        "worktrees": git_worktrees
        if git_worktrees is not None
        else _default_worktrees(root),
        "compose": compose_resources
        if compose_resources is not None
        else _default_compose_resources(root),
    }


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _required_int(value: object, label: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def bind_plan_identity(payload: dict[str, object]) -> dict[str, Any]:
    identity = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return {**payload, "plan_identity": identity}


def _generation_manifests(
    root: Path, policy: RetentionPolicy
) -> list[tuple[Path, ArtifactManifest]]:
    generation_root = root / policy.generation_root
    results: list[tuple[Path, ArtifactManifest]] = []
    if not generation_root.exists():
        return results
    for family in sorted(generation_root.iterdir()):
        if not family.is_dir() or family.name.startswith("."):
            continue
        for generation in sorted(family.iterdir()):
            if not generation.is_dir() or generation.name.startswith("."):
                continue
            manifest_path = generation / "manifest.json"
            if not manifest_path.is_file() or not (generation / ".complete").is_file():
                raise ValueError(
                    f"partial generation refuses cleanup planning: {generation}"
                )
            record = resolve_parent_manifest(manifest_path)
            results.append((generation, record))
    return results


def _validate_retention(
    record: ArtifactManifest, policy: RetentionPolicy
) -> RetentionClassPolicy:
    name = record.retention.retention_class
    declared = policy.classes.get(name)
    if declared is None:
        raise ValueError(f"unknown retention class refuses cleanup: {name}")
    if declared.owner != record.retention.owner:
        raise ValueError(f"retention owner differs from policy for {name}")
    if declared.expiry == "manifest-expires-at":
        if record.retention.expires_at is None:
            raise ValueError(f"retention expiry differs from policy for {name}")
        _parse_expiry(record.retention.expires_at)
    elif record.retention.expires_at is not None:
        raise ValueError(f"retention expiry differs from policy for {name}")
    if name == "licensed-source":
        raise ValueError(
            "licensed-source cleanup is blocked pending #335 certification"
        )
    if name == "critical-full-corpus":
        raise ValueError("critical/full-corpus artifacts are protected")
    return declared


def _parse_expiry(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("manifest retention expiry is not RFC 3339") from exc
    if parsed.tzinfo is None:
        raise ValueError("manifest retention expiry must include a timezone")
    return parsed.astimezone(UTC)


def _managed_reference_index(
    artifacts_root: Path,
) -> tuple[set[str], dict[str, object]]:
    incoming: set[str] = set()
    for path in _record_paths(artifacts_root):
        try:
            record = load_artifact_record(path)
        except ValueError as exc:
            raise ValueError(
                f"managed reference coverage is incomplete: {path}"
            ) from exc
        if isinstance(record, ArtifactManifest):
            incoming.update(parent.manifest_identity for parent in record.parents)
    return incoming, {
        "coverage": "all-managed-record-roots",
        "incoming_generation_parents": [],
    }


def _directory_logical_bytes(path: Path) -> int:
    total = 0
    for root, directories, names in os.walk(path, followlinks=False):
        for name in directories:
            if (Path(root) / name).is_symlink():
                raise ValueError(f"symlink refuses cleanup: {Path(root) / name}")
        for name in names:
            candidate = Path(root) / name
            details = candidate.lstat()
            if stat.S_ISLNK(details.st_mode):
                raise ValueError(f"symlink refuses cleanup: {candidate}")
            if not stat.S_ISREG(details.st_mode):
                raise ValueError(f"non-regular path refuses cleanup: {candidate}")
            total += details.st_size
    return total


def build_cleanup_plan(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    root = root.resolve()
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise ValueError("cleanup planning time must include a timezone")
    policy = load_retention_policy(root / "artifact-retention.toml")
    manifests = _generation_manifests(root, policy)
    incoming, reference_contract = _managed_reference_index(root / "tmp/artifacts/v1")
    actions: list[dict[str, object]] = []
    for directory, record in manifests:
        declared = _validate_retention(record, policy)
        expires_at = record.retention.expires_at
        if (
            not declared.cleanup_eligible
            or declared.expiry != "manifest-expires-at"
            or expires_at is None
            or _parse_expiry(expires_at) > current_time.astimezone(UTC)
            or record.parents
            or record.manifest_identity in incoming
        ):
            continue
        actions.append(
            {
                "path": directory.relative_to(root).as_posix(),
                "manifest_path": (directory / "manifest.json")
                .relative_to(root)
                .as_posix(),
                "manifest_identity": record.manifest_identity,
                "logical_bytes": _directory_logical_bytes(directory),
                "owner": record.retention.owner,
                "retention_class": record.retention.retention_class,
                "references": reference_contract,
                "reason": "retention-expired-and-no-incoming-generation-parent",
            }
        )
    payload: dict[str, object] = {
        "schema_version": _PLAN_SCHEMA,
        "record_type": "managed-generation-cleanup-plan",
        "managed_generation_root": policy.generation_root,
        "actions": actions,
        "total_logical_bytes": sum(
            _required_int(item["logical_bytes"], "action logical bytes")
            for item in actions
        ),
    }
    return bind_plan_identity(payload)


def write_cleanup_plan(root: Path, plan: dict[str, object]) -> Path:
    identity = plan.get("plan_identity")
    if not isinstance(identity, str):
        raise ValueError("cleanup plan identity is missing")
    directory = root.resolve() / "tmp/artifacts/v1/cleanup-plans"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identity}.json"
    data = _canonical_bytes(plan)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("existing cleanup plan differs") from None
        return path
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(directory)
    return path


def _load_plan(path: Path, expected_identity: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cleanup plan cannot be read") from exc
    if not isinstance(payload, dict):
        raise ValueError("cleanup plan must be an object")
    observed = payload.pop("plan_identity", None)
    rebound = bind_plan_identity(payload)
    if observed != expected_identity or rebound["plan_identity"] != expected_identity:
        raise ValueError("cleanup plan identity differs")
    return rebound


def _validate_plan_location(root: Path, path: Path, identity: str) -> None:
    expected = root / f"tmp/artifacts/v1/cleanup-plans/{identity}.json"
    if path.resolve() != expected.resolve() or path.is_symlink():
        raise ValueError("cleanup plan path is outside the managed plan registry")


def _validate_no_overlaps(actions: list[dict[str, object]]) -> None:
    paths = sorted(Path(str(action.get("path"))).parts for action in actions)
    for previous, current in pairwise(paths):
        if len(previous) <= len(current) and current[: len(previous)] == previous:
            raise ValueError("cleanup action paths overlap")


def _prevalidate_apply(root: Path, plan: dict[str, Any]) -> list[dict[str, object]]:
    actions = plan.get("actions")
    if not isinstance(actions, list) or any(
        not isinstance(item, dict) for item in actions
    ):
        raise ValueError("cleanup plan actions are invalid")
    typed_actions: list[dict[str, object]] = actions
    _validate_no_overlaps(typed_actions)
    try:
        current = build_cleanup_plan(root)
    except ValueError as exc:
        raise ValueError(f"cleanup plan drift detected: {exc}") from exc
    if current["actions"] != typed_actions or current[
        "total_logical_bytes"
    ] != plan.get("total_logical_bytes"):
        raise ValueError("cleanup plan drift or reference change detected")
    return typed_actions


def _validated_generation_path(path: Path, managed_root: Path) -> Path:
    managed_root = managed_root.resolve()
    try:
        relative = path.relative_to(managed_root)
    except ValueError as exc:
        raise ValueError("cleanup path is outside the managed generation root") from exc
    if len(relative.parts) != _GENERATION_PATH_PARTS:
        raise ValueError("cleanup path is not an exact managed generation")
    for candidate, label in (
        (managed_root, "managed generation root"),
        (managed_root / relative.parts[0], "managed generation family"),
        (path, "managed generation"),
    ):
        try:
            details = candidate.lstat()
        except OSError as exc:
            raise ValueError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(details.st_mode):
            raise ValueError(f"{label} is a symlink")
        if not stat.S_ISDIR(details.st_mode):
            raise ValueError(f"{label} is not a directory")
    if path.resolve() != path or path.resolve().parent.parent != managed_root:
        raise ValueError("cleanup path escapes the managed generation root")
    return path


def _remove_generation_tree(path: Path, managed_root: Path) -> None:
    path = _validated_generation_path(path, managed_root)
    completion = path / ".complete"
    completion_details = completion.lstat()
    if stat.S_ISLNK(completion_details.st_mode) or not stat.S_ISREG(
        completion_details.st_mode
    ):
        raise ValueError("generation completion marker is not a regular file")
    completion.unlink()
    _fsync_directory(path)
    for root, directories, names in os.walk(path, topdown=False, followlinks=False):
        current = Path(root)
        for name in names:
            candidate = current / name
            details = candidate.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise ValueError(f"unsafe path refuses cleanup: {candidate}")
            candidate.unlink()
        for name in directories:
            candidate = current / name
            details = candidate.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise ValueError(f"unsafe path refuses cleanup: {candidate}")
            candidate.rmdir()
    path.rmdir()
    _fsync_directory(path.parent)


def apply_cleanup_plan(
    root: Path,
    plan_path: Path,
    expected_identity: str,
    *,
    remover: Callable[[Path, Path], None] = _remove_generation_tree,
) -> dict[str, Any]:
    root = root.resolve()
    plan = _load_plan(plan_path, expected_identity)
    _validate_plan_location(root, plan_path, expected_identity)
    actions = _prevalidate_apply(root, plan)
    policy = load_retention_policy(root / "artifact-retention.toml")
    managed_root = root / policy.generation_root
    results: list[dict[str, object]] = []
    reclaimed = 0
    failed = False
    for action in actions:
        path = root / str(action["path"])
        planned_bytes = _required_int(action["logical_bytes"], "action logical bytes")
        try:
            _validated_generation_path(path, managed_root)
            remover(path, managed_root)
        except OSError as exc:
            failed = True
            remaining = _directory_logical_bytes(path) if path.exists() else 0
            changed = remaining != planned_bytes
            action_reclaimed = planned_bytes - remaining
            reclaimed += action_reclaimed
            results.append(
                {
                    "path": action["path"],
                    "status": "partially-removed" if changed else "failed",
                    "error": str(exc),
                    "reclaimed_logical_bytes": action_reclaimed,
                    "remaining_logical_bytes": remaining,
                }
            )
        else:
            reclaimed += planned_bytes
            results.append(
                {
                    "path": action["path"],
                    "status": "removed",
                    "logical_bytes": action["logical_bytes"],
                }
            )
    return {
        "plan_identity": expected_identity,
        "status": "partial-failure" if failed else "complete",
        "reclaimed_logical_bytes": reclaimed,
        "actions": results,
    }


def main() -> int:
    arguments = parser().parse_args()
    root = Path.cwd()
    if arguments.command == "inventory":
        print(json.dumps(inventory_repository(root), sort_keys=True, indent=2))
    elif arguments.command == "plan":
        plan = build_cleanup_plan(root)
        path = write_cleanup_plan(root, plan)
        print(
            json.dumps(
                {"plan_path": path.relative_to(root).as_posix(), **plan},
                sort_keys=True,
                indent=2,
            )
        )
    elif arguments.command == "apply":
        print(
            json.dumps(
                apply_cleanup_plan(root, Path(arguments.plan), arguments.identity),
                sort_keys=True,
                indent=2,
            )
        )
    else:
        raise AssertionError("argparse accepted an unsupported command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
