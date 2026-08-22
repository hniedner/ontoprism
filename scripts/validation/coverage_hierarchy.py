#!/usr/bin/env python3
"""Validate production coverage surfaces and emit report-only hierarchy artifacts."""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tokenize
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "coverage-surfaces.toml"
STRICT_FLOOR = 90.0
COVERAGE_JSON_FORMAT = 3
_CLASSIFICATIONS = {"production", "research", "test", "dev", "non-executable"}
_LANGUAGES = {"python", "frontend", "workflow"}
_MEASUREMENTS = {"coverage.py", "vitest", "playwright-behavior", "none"}
_EXEMPTION_KINDS = {
    "pragma-no-cover",
    "measurement-exclusion",
    "coverage-exclude-regex",
    "coverage-partial-regex",
}
_IGNORE_DIRS = {"__pycache__", ".git", ".svelte-kit", "build", "node_modules"}
_IGNORE_MARKERS = ("pragma: no cover", "istanbul ignore", "v8 ignore", "c8 ignore")
MetricKind = Literal["lines", "branches"]


class _Document(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class Metric(_Document):
    """One covered/total metric; ``None`` means the surface was not measured."""

    covered: int
    total: int | None
    kind: MetricKind = "branches"

    @property
    def percent(self) -> float:
        if self.total is None:
            return 0.0
        if self.total == 0:
            return 100.0
        return self.covered * 100.0 / self.total

    @property
    def display(self) -> str:
        if self.total is None:
            return "0.00% (unmeasured)"
        if self.total == 0:
            opportunity = "branches" if self.kind == "branches" else "executable lines"
            return f"N/A (no {opportunity})"
        return f"{self.percent:.2f}% ({self.covered}/{self.total})"

    @property
    def is_deficit(self) -> bool:
        return self.total is None or (self.total > 0 and self.percent <= STRICT_FLOOR)

    def as_dict(self) -> dict[str, int | float | str | None]:
        return {
            "covered": self.covered,
            "total": self.total,
            "percent": None if self.total == 0 else self.percent,
            "display": self.display,
        }


class ArtifactIdentity(_Document):
    """Commit and configuration identity carried beside a coverage layer."""

    schema_version: int
    commit: str
    config_sha256: str
    manifest_sha256: str
    tool: str
    tool_version: str
    layer: str
    source_sha256: str = ""
    worktree_dirty: bool = False

    def as_dict(self) -> dict[str, str | int | bool]:
        return {
            "schema_version": self.schema_version,
            "commit": self.commit,
            "config_sha256": self.config_sha256,
            "manifest_sha256": self.manifest_sha256,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "layer": self.layer,
            "source_sha256": self.source_sha256,
            "worktree_dirty": self.worktree_dirty,
        }


@dataclass(frozen=True, slots=True)
class LayerCompatibility:
    """Fields that must match before two raw coverage layers can be combined."""

    config_sha256: str
    manifest_sha256: str
    source_sha256: str
    tool: str
    tool_version: str

    @classmethod
    def from_identity(cls, identity: ArtifactIdentity) -> LayerCompatibility:
        return cls(
            config_sha256=identity.config_sha256,
            manifest_sha256=identity.manifest_sha256,
            source_sha256=identity.source_sha256,
            tool=identity.tool,
            tool_version=identity.tool_version,
        )


class Group(_Document):
    name: str
    classification: str
    language: str
    measurement: str
    tree: str
    kind: str
    executable: bool


class Inventory(_Document):
    root: str
    extensions: tuple[str, ...]
    default_group: str


class Assignment(_Document):
    pattern: str
    group: str


class Exemption(_Document):
    path: str
    line: int
    kind: str
    owner: str
    rationale: str
    behavioral_test: str
    review_issue: int
    review_after: str
    configured_in: str = ""


class Surface(_Document):
    path: str
    group: Group


class Manifest(_Document):
    path: Path
    schema_version: int
    report_only: bool
    limitations: tuple[str, ...]
    required_production_paths: tuple[str, ...]
    required_test_paths: tuple[str, ...]
    groups: tuple[Group, ...]
    inventories: tuple[Inventory, ...]
    assignments: tuple[Assignment, ...]
    exemptions: tuple[Exemption, ...]


@dataclass(frozen=True, slots=True)
class CoverageConfig:
    """Coverage exclusions that alter executable line or branch denominators."""

    exclude_also: tuple[str, ...]
    partial_also: tuple[str, ...]


class Scope(_Document):
    scope_id: str
    level: str
    name: str
    path: str
    group: str
    tree: str
    lines: Metric
    branches: Metric

    @property
    def is_deficit(self) -> bool:
        return self.lines.is_deficit or self.branches.is_deficit

    def as_dict(self) -> dict[str, object]:
        return {
            "scope_id": self.scope_id,
            "level": self.level,
            "name": self.name,
            "path": self.path,
            "group": self.group,
            "tree": self.tree,
            "lines": self.lines.as_dict(),
            "branches": self.branches.as_dict(),
            "deficit": self.is_deficit,
        }


class HierarchyReport(_Document):
    schema_version: int
    report_only: bool
    language: str
    identity: ArtifactIdentity
    limitations: tuple[str, ...]
    scopes: tuple[Scope, ...]

    @property
    def deficits(self) -> tuple[Scope, ...]:
        return tuple(scope for scope in self.scopes if scope.is_deficit)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "report_only": self.report_only,
            "language": self.language,
            "strict_floor": STRICT_FLOOR,
            "identity": self.identity.as_dict(),
            "limitations": list(self.limitations),
            "summary": {
                "scope_count": len(self.scopes),
                "deficit_count": len(self.deficits),
            },
            "scopes": [scope.as_dict() for scope in self.scopes],
            "deficits": [scope.as_dict() for scope in self.deficits],
        }


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a table")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{context} keys must be strings")
    return value


def _tables(raw: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array of tables")
    return tuple(_mapping(item, key) for item in value)


def _string(raw: Mapping[str, object], key: str, default: str = "") -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _integer(raw: Mapping[str, object], key: str, default: int = 0) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _boolean(raw: Mapping[str, object], key: str, default: bool = False) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _strings(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be an array of strings")
    return tuple(value)


def _assert_known_enums(
    groups: Sequence[Group], exemptions: Sequence[Exemption]
) -> None:
    """Fail closed at parse time so no subcommand consumes an invalid group.

    Report subcommands never call ``validate_manifest`` first, so enum validity
    (classification/language/measurement/kind) and the executable invariant must be
    guaranteed here, not only on the ``validate`` path — otherwise a typo silently
    drops a surface or skips a guard. ``language`` is a routing enum
    (``_production_surfaces`` filters on it) and report emitters require
    ``executable``, so an unguarded typo or a ``production``/non-``executable`` group
    would omit a surface — even a required one — from every report.
    """
    for group in groups:
        if group.classification not in _CLASSIFICATIONS:
            raise ValueError(
                f"group {group.name!r} has unknown classification "
                f"{group.classification!r}"
            )
        if group.language not in _LANGUAGES:
            raise ValueError(
                f"group {group.name!r} has unknown language {group.language!r}"
            )
        if group.measurement not in _MEASUREMENTS:
            raise ValueError(
                f"group {group.name!r} has unknown measurement {group.measurement!r}"
            )
        # A group is executable iff it is not the non-executable classification.
        # Report emitters gate on ``executable``; a ``production`` group flagged
        # non-executable would be silently dropped from every report.
        if (group.classification == "non-executable") != (not group.executable):
            raise ValueError(
                f"group {group.name!r} executable flag disagrees with its "
                f"classification {group.classification!r}"
            )
    for exemption in exemptions:
        if exemption.kind not in _EXEMPTION_KINDS:
            raise ValueError(
                f"exemption {exemption.path!r} has unknown kind {exemption.kind!r}"
            )


def load_manifest(path: Path, root: Path = REPO_ROOT) -> Manifest:
    """Load the typed production-surface manifest."""
    del root  # Kept explicit at the call site because validation is root-relative.
    with path.open("rb") as stream:
        raw = _mapping(tomllib.load(stream), "manifest")
    groups = tuple(
        Group(
            name=_string(item, "name"),
            classification=_string(item, "classification"),
            language=_string(item, "language"),
            measurement=_string(item, "measurement"),
            tree=_string(item, "tree"),
            kind=_string(item, "kind"),
            executable=_boolean(item, "executable"),
        )
        for item in _tables(raw, "group")
    )
    inventories = tuple(
        Inventory(
            root=_string(item, "root"),
            extensions=_strings(item, "extensions"),
            default_group=_string(item, "default_group"),
        )
        for item in _tables(raw, "inventory")
    )
    assignments = tuple(
        Assignment(pattern=_string(item, "pattern"), group=_string(item, "group"))
        for item in _tables(raw, "assignment")
    )
    exemptions = tuple(
        Exemption(
            path=_string(item, "path"),
            line=_integer(item, "line"),
            kind=_string(item, "kind"),
            owner=_string(item, "owner"),
            rationale=_string(item, "rationale"),
            behavioral_test=_string(item, "behavioral_test"),
            review_issue=_integer(item, "review_issue"),
            review_after=_string(item, "review_after"),
            configured_in=_string(item, "configured_in"),
        )
        for item in _tables(raw, "exemption")
    )
    _assert_known_enums(groups, exemptions)
    return Manifest(
        path=path,
        schema_version=_integer(raw, "schema_version"),
        report_only=_boolean(raw, "report_only"),
        limitations=_strings(raw, "limitations"),
        required_production_paths=_strings(raw, "required_production_paths"),
        required_test_paths=_strings(raw, "required_test_paths"),
        groups=groups,
        inventories=inventories,
        assignments=assignments,
        exemptions=exemptions,
    )


def load_coverage_config(path: Path) -> CoverageConfig:
    """Load custom Coverage.py exclusion and partial-branch expressions."""
    with path.open("rb") as stream:
        raw = _mapping(tomllib.load(stream), str(path))
    coverage = _mapping(raw.get("tool", {}), "tool")
    coverage = _mapping(coverage.get("coverage", {}), "tool.coverage")
    report = _mapping(coverage.get("report", {}), "tool.coverage.report")
    return CoverageConfig(
        exclude_also=_strings(report, "exclude_also"),
        partial_also=_strings(report, "partial_also"),
    )


def _inventory_paths(inventory: Inventory, root: Path) -> tuple[str, ...]:
    inventory_root = root / inventory.root
    if not inventory_root.is_dir():
        return ()
    paths = []
    for path in inventory_root.rglob("*"):
        if not path.is_file() or any(part in _IGNORE_DIRS for part in path.parts):
            continue
        if path.suffix in inventory.extensions:
            paths.append(path.relative_to(root).as_posix())
    return tuple(sorted(paths))


def discover_surfaces(
    manifest: Manifest, root: Path = REPO_ROOT
) -> tuple[Surface, ...]:
    """Resolve every inventoried path to exactly one declared classification."""
    groups = {group.name: group for group in manifest.groups}
    surfaces: dict[str, Surface] = {}
    for inventory in manifest.inventories:
        for path in _inventory_paths(inventory, root):
            matches = [
                assignment.group
                for assignment in manifest.assignments
                if fnmatch.fnmatchcase(path, assignment.pattern)
            ]
            if len(matches) > 1:
                raise ValueError(f"{path} matches multiple assignments: {matches}")
            group_name = matches[0] if matches else inventory.default_group
            if group_name not in groups:
                raise ValueError(f"{path} names unknown group {group_name!r}")
            if path in surfaces:
                raise ValueError(f"{path} is inventoried more than once")
            surfaces[path] = Surface(path=path, group=groups[group_name])
    return tuple(surfaces[path] for path in sorted(surfaces))


def _validate_groups(manifest: Manifest) -> list[str]:
    errors: list[str] = []
    names = [group.name for group in manifest.groups]
    if len(names) != len(set(names)):
        errors.append("group names must be unique")
    for group in manifest.groups:
        prefix = f"group {group.name!r}"
        if not group.name or not group.language or not group.tree or not group.kind:
            errors.append(f"{prefix} has an empty required field")
    return errors


def _validate_exemption_metadata(exemption: Exemption, root: Path) -> list[str]:
    errors: list[str] = []
    label = f"exemption {exemption.path}:{exemption.line}"
    is_config_regex = exemption.kind in {
        "coverage-exclude-regex",
        "coverage-partial-regex",
    }
    if not is_config_regex and any(char in exemption.path for char in "*?[]"):
        errors.append(f"{label} must be an exact path, not a glob")
    source = root / exemption.path
    if not is_config_regex and not source.is_file():
        errors.append(f"{label} source does not exist")
    if exemption.line < 1:
        errors.append(f"{label} line must be positive")
    required_text = {
        "owner": exemption.owner,
        "rationale": exemption.rationale,
    }
    errors.extend(
        f"{label} is missing {field}"
        for field, value in required_text.items()
        if not value
    )
    behavioral_test = root / exemption.behavioral_test
    if not exemption.behavioral_test or not behavioral_test.is_file():
        errors.append(f"{label} behavioral_test must name an existing file")
    errors.extend(
        [f"{label} review_issue must be positive"] if exemption.review_issue < 1 else []
    )
    try:
        review_after = datetime.date.fromisoformat(exemption.review_after)
    except ValueError:
        errors.append(f"{label} review_after must be an ISO date")
    else:
        if review_after < datetime.date.today():
            errors.append(f"{label} review_after has expired")
    return errors


def _validate_pragma_exemption(exemption: Exemption, root: Path) -> list[str]:
    if exemption.kind != "pragma-no-cover":
        return []
    source = root / exemption.path
    if not source.is_file():
        return []
    lines = source.read_text(encoding="utf-8").splitlines()
    line_exists = exemption.line <= len(lines)
    if line_exists and "pragma: no cover" in lines[exemption.line - 1]:
        return []
    return [
        f"exemption {exemption.path}:{exemption.line} "
        "does not point at a pragma: no cover"
    ]


def _validate_measurement_exemption(exemption: Exemption, root: Path) -> list[str]:
    if exemption.kind != "measurement-exclusion":
        return []
    label = f"exemption {exemption.path}:{exemption.line}"
    configured = root / exemption.configured_in
    if not configured.is_file():
        return [f"{label} configured_in must name an existing file"]
    configured_text = configured.read_text(encoding="utf-8")
    configured_parent = Path(exemption.configured_in).parent.as_posix()
    configured_path = exemption.path.removeprefix(f"{configured_parent}/")
    if exemption.path in configured_text or configured_path in configured_text:
        return []
    return [f"{label} is not named by {exemption.configured_in}"]


def _validate_config_exemption(
    exemption: Exemption, coverage_config: CoverageConfig, root: Path
) -> list[str]:
    if exemption.kind not in {"coverage-exclude-regex", "coverage-partial-regex"}:
        return []
    expressions = (
        coverage_config.exclude_also
        if exemption.kind == "coverage-exclude-regex"
        else coverage_config.partial_also
    )
    errors = []
    if exemption.path not in expressions:
        errors.append(
            f"exemption {exemption.path!r} does not match configured coverage regex"
        )
    configured = root / exemption.configured_in
    if not configured.is_file():
        errors.append(
            f"exemption {exemption.path!r} configured_in must name an existing file"
        )
    return errors


def _validate_exemption(
    exemption: Exemption, root: Path, coverage_config: CoverageConfig
) -> list[str]:
    return [
        *_validate_exemption_metadata(exemption, root),
        *_validate_pragma_exemption(exemption, root),
        *_validate_measurement_exemption(exemption, root),
        *_validate_config_exemption(exemption, coverage_config, root),
    ]


def _unowned_ignore_markers(
    manifest: Manifest, surfaces: Sequence[Surface], root: Path
) -> list[str]:
    owned = {(item.path, item.line) for item in manifest.exemptions}
    errors: list[str] = []
    for surface in surfaces:
        path = root / surface.path
        if path.suffix not in {".py", ".js", ".mjs", ".ts", ".svelte"}:
            continue
        if path.suffix == ".py":
            with path.open("rb") as stream:
                comments = (
                    (token.start[0], token.string.lower())
                    for token in tokenize.tokenize(stream.readline)
                    if token.type == tokenize.COMMENT
                )
                markers = tuple(
                    line_number
                    for line_number, comment in comments
                    if any(marker in comment for marker in _IGNORE_MARKERS)
                )
        else:
            markers = tuple(
                line_number
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1
                )
                if any(marker in line.lower() for marker in _IGNORE_MARKERS)
                and any(prefix in line for prefix in ("//", "/*", "<!--"))
            )
        for line_number in markers:
            if (surface.path, line_number) not in owned:
                errors.append(
                    f"unowned pragma/ignore marker: {surface.path}:{line_number}"
                )
    return errors


def _validate_manifest_header(manifest: Manifest) -> list[str]:
    errors: list[str] = []
    if manifest.schema_version != 1:
        errors.append("schema_version must be 1")
    if not manifest.report_only:
        errors.append("#170 hierarchy must remain report_only")
    if not manifest.limitations:
        errors.append("limitations must not be empty")
    return errors


def _validate_manifest_references(manifest: Manifest, root: Path) -> list[str]:
    errors: list[str] = []
    group_names = {group.name for group in manifest.groups}
    for inventory in manifest.inventories:
        if inventory.default_group not in group_names:
            errors.append(f"inventory {inventory.root!r} has an unknown default_group")
        if not (root / inventory.root).is_dir():
            errors.append(f"inventory root {inventory.root!r} does not exist")
    for assignment in manifest.assignments:
        if assignment.group not in group_names:
            errors.append(f"assignment {assignment.pattern!r} has an unknown group")
    return errors


def _validate_required_surfaces(
    manifest: Manifest, surfaces: Sequence[Surface]
) -> list[str]:
    errors: list[str] = []
    by_path = {surface.path: surface for surface in surfaces}
    for path in manifest.required_production_paths:
        surface = by_path.get(path)
        if surface is None or surface.group.classification != "production":
            errors.append(
                f"required production path {path!r} is omitted or non-production"
            )
    for path in manifest.required_test_paths:
        surface = by_path.get(path)
        if surface is None or surface.group.classification != "test":
            errors.append(f"required test path {path!r} is omitted or non-test")
    return errors


def validate_manifest(manifest: Manifest, root: Path = REPO_ROOT) -> list[str]:
    """Return all hermetic manifest/configuration violations."""
    errors = [
        *_validate_groups(manifest),
        *_validate_manifest_header(manifest),
        *_validate_manifest_references(manifest, root),
    ]
    try:
        surfaces = discover_surfaces(manifest, root)
    except ValueError as exc:
        errors.append(str(exc))
        surfaces = ()
    errors.extend(_validate_required_surfaces(manifest, surfaces))
    exemption_keys = [(item.path, item.line, item.kind) for item in manifest.exemptions]
    if len(exemption_keys) != len(set(exemption_keys)):
        errors.append("exemptions must be unique")
    coverage_config = load_coverage_config(root / "pyproject.toml")
    for exemption in manifest.exemptions:
        errors.extend(_validate_exemption(exemption, root, coverage_config))
    configured_exemptions = {
        ("coverage-exclude-regex", expression)
        for expression in coverage_config.exclude_also
    } | {
        ("coverage-partial-regex", expression)
        for expression in coverage_config.partial_also
    }
    owned_config_exemptions = {
        (exemption.kind, exemption.path)
        for exemption in manifest.exemptions
        if exemption.kind in {"coverage-exclude-regex", "coverage-partial-regex"}
    }
    for kind, expression in sorted(configured_exemptions - owned_config_exemptions):
        errors.append(f"unowned {kind}: {expression}")
    errors.extend(_unowned_ignore_markers(manifest, surfaces, root))
    return sorted(set(errors))


def _json_mapping(path: Path) -> Mapping[str, object]:
    return _mapping(json.loads(path.read_text(encoding="utf-8")), str(path))


def _summary_metric(summary: Mapping[str, object], covered: str, total: str) -> Metric:
    covered_value = summary.get(covered)
    total_value = summary.get(total)
    if not isinstance(covered_value, int) or not isinstance(total_value, int):
        raise ValueError(f"coverage summary is missing integer {covered}/{total}")
    kind: MetricKind = "branches" if "branch" in total else "lines"
    return Metric(covered=covered_value, total=total_value, kind=kind)


def _unmeasured_scope(surface: Surface) -> Scope:
    return Scope(
        scope_id=f"module:{surface.path}",
        level="module",
        name=Path(surface.path).stem,
        path=surface.path,
        group=surface.group.name,
        tree=surface.group.tree,
        lines=Metric(covered=0, total=None, kind="lines"),
        branches=Metric(covered=0, total=None, kind="branches"),
    )


def _python_region_scopes(
    surface: Surface, file_data: Mapping[str, object], plural: str, level: str
) -> list[Scope]:
    regions = _mapping(file_data.get(plural, {}), plural)
    scopes: list[Scope] = []
    for name, value in regions.items():
        if not name:
            continue
        region = _mapping(value, f"{plural}.{name}")
        summary = _mapping(region.get("summary", {}), f"{plural}.{name}.summary")
        start_line = region.get("start_line", 0)
        if not isinstance(start_line, int):
            raise ValueError(f"{plural}.{name}.start_line must be an integer")
        scopes.append(
            Scope(
                scope_id=f"{level}:{surface.path}:{name}@{start_line}",
                level=level,
                name=name,
                path=surface.path,
                group=surface.group.name,
                tree=surface.group.tree,
                lines=_summary_metric(summary, "covered_lines", "num_statements"),
                branches=_summary_metric(summary, "covered_branches", "num_branches"),
            )
        )
    return scopes


def _python_module_scope(surface: Surface, file_data: Mapping[str, object]) -> Scope:
    summary = _mapping(file_data.get("summary", {}), f"{surface.path}.summary")
    return Scope(
        scope_id=f"module:{surface.path}",
        level="module",
        name=Path(surface.path).stem,
        path=surface.path,
        group=surface.group.name,
        tree=surface.group.tree,
        lines=_summary_metric(summary, "covered_lines", "num_statements"),
        branches=_summary_metric(summary, "covered_branches", "num_branches"),
    )


def _sum_metrics(metrics: Iterable[Metric]) -> Metric:
    values = tuple(metrics)
    kind: MetricKind = values[0].kind if values else "branches"
    if any(metric.total is None for metric in values):
        return Metric(covered=0, total=None, kind=kind)
    return Metric(
        covered=sum(metric.covered for metric in values),
        total=sum(metric.total or 0 for metric in values),
        kind=kind,
    )


def _aggregate_scopes(
    modules: Sequence[Scope], groups: Mapping[str, Group]
) -> list[Scope]:
    aggregates: list[Scope] = []
    for group_name in sorted({module.group for module in modules}):
        group_modules = [module for module in modules if module.group == group_name]
        group = groups[group_name]
        aggregates.append(
            Scope(
                scope_id=f"group:{group_name}",
                level=group.kind,
                name=group_name,
                path="",
                group=group_name,
                tree=group.tree,
                lines=_sum_metrics(module.lines for module in group_modules),
                branches=_sum_metrics(module.branches for module in group_modules),
            )
        )
    for tree in sorted({module.tree for module in modules}):
        tree_modules = [module for module in modules if module.tree == tree]
        aggregates.append(
            Scope(
                scope_id=f"tree:{tree}",
                level="source-tree",
                name=tree,
                path="",
                group="",
                tree=tree,
                lines=_sum_metrics(module.lines for module in tree_modules),
                branches=_sum_metrics(module.branches for module in tree_modules),
            )
        )
    return aggregates


def _production_surfaces(
    manifest: Manifest, root: Path, language: str
) -> tuple[Surface, ...]:
    return tuple(
        surface
        for surface in discover_surfaces(manifest, root)
        if surface.group.classification == "production"
        and surface.group.executable
        and surface.group.language == language
    )


def _unmeasured_workflow_scopes(manifest: Manifest, root: Path) -> tuple[Scope, ...]:
    """Emit workflow-language production surfaces (release-workflow YAML) as unmeasured.

    Reports partition on ``language`` (``_production_surfaces`` routes python and
    frontend), so this bucket must also route on ``language == "workflow"`` — not on
    ``measurement``. Routing on ``measurement == "none"`` would make the two routing
    dimensions independent, silently dropping a ``workflow`` group that declares a
    measured tool and leaking a ``none``-measured frontend surface into this bucket.
    """
    return tuple(
        _unmeasured_scope(surface)
        for surface in discover_surfaces(manifest, root)
        if surface.group.classification == "production"
        and surface.group.executable
        and surface.group.language == "workflow"
    )


def build_python_report(
    manifest: Manifest,
    raw: Mapping[str, object],
    identity: ArtifactIdentity,
    root: Path = REPO_ROOT,
) -> HierarchyReport:
    """Build native Python function/class/module/group/tree measurements."""
    meta = _mapping(raw.get("meta", {}), "coverage meta")
    if (
        meta.get("format") != COVERAGE_JSON_FORMAT
        or meta.get("branch_coverage") is not True
    ):
        raise ValueError("Coverage.py format 3 with branch coverage is required")
    files = _mapping(raw.get("files", {}), "coverage files")
    modules: list[Scope] = []
    regions: list[Scope] = []
    surfaces = _production_surfaces(manifest, root, "python")
    for surface in surfaces:
        value = files.get(surface.path)
        if value is None:
            modules.append(_unmeasured_scope(surface))
            continue
        file_data = _mapping(value, surface.path)
        modules.append(_python_module_scope(surface, file_data))
        regions.extend(
            _python_region_scopes(surface, file_data, "functions", "function")
        )
        regions.extend(_python_region_scopes(surface, file_data, "classes", "class"))
    groups = {group.name: group for group in manifest.groups}
    workflow_modules = list(_unmeasured_workflow_scopes(manifest, root))
    all_modules = modules + workflow_modules
    scopes = regions + all_modules + _aggregate_scopes(all_modules, groups)
    return HierarchyReport(
        schema_version=1,
        report_only=True,
        language="python",
        identity=identity,
        limitations=manifest.limitations,
        scopes=tuple(sorted(scopes, key=lambda scope: scope.scope_id)),
    )


def _relative_istanbul_path(path: str, root: Path) -> str | None:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            return None
    normalized = path.removeprefix("./")
    return normalized if not normalized.startswith("../") else None


def _istanbul_module_scope(surface: Surface, file_data: Mapping[str, object]) -> Scope:
    statement_map = _mapping(file_data.get("statementMap", {}), "statementMap")
    statement_hits = _mapping(file_data.get("s", {}), "statement hits")
    line_hits: dict[int, int] = {}
    for key, location_value in statement_map.items():
        location = _mapping(location_value, "statement location")
        start = _mapping(location.get("start", {}), "statement start")
        line = start.get("line")
        hits = statement_hits.get(key)
        if isinstance(line, int) and isinstance(hits, int):
            line_hits[line] = max(line_hits.get(line, 0), hits)
    branch_hits = _mapping(file_data.get("b", {}), "branch hits")
    flattened = [
        hit
        for values in branch_hits.values()
        if isinstance(values, list)
        for hit in values
        if isinstance(hit, int)
    ]
    return Scope(
        scope_id=f"module:{surface.path}",
        level="module",
        name=Path(surface.path).stem,
        path=surface.path,
        group=surface.group.name,
        tree=surface.group.tree,
        lines=Metric(
            covered=sum(hit > 0 for hit in line_hits.values()),
            total=len(line_hits),
            kind="lines",
        ),
        branches=Metric(
            covered=sum(hit > 0 for hit in flattened),
            total=len(flattened),
            kind="branches",
        ),
    )


def _position(location: Mapping[str, object], key: str) -> tuple[int, int]:
    point = _mapping(location.get(key, {}), f"location.{key}")
    line = point.get("line")
    column = point.get("column", 0)
    if not isinstance(line, int):
        raise ValueError(f"location.{key} must contain an integer line")
    if column is None:
        column = sys.maxsize
    if not isinstance(column, int):
        raise ValueError(f"location.{key} column must be an integer or null")
    return line, column


def _location_start(location: Mapping[str, object]) -> tuple[int, int]:
    """Return a location's start point across the Istanbul statement/branch shapes.

    Statements carry a top-level ``start``; branches carry ``loc`` plus per-branch
    ``locations``. A value matching none of these known shapes is a broken
    coverage-format assumption and is raised loudly rather than silently dropped.
    """
    try:
        return _position(location, "start")
    except ValueError:
        pass
    loc = location.get("loc")
    if isinstance(loc, Mapping):
        return _position(_mapping(loc, "location.loc"), "start")
    locations = location.get("locations")
    if isinstance(locations, list) and locations:
        return _position(_mapping(locations[0], "location.locations[0]"), "start")
    raise ValueError(f"unrecognized Istanbul location shape: {location!r}")


def _inside(location: Mapping[str, object], container: Mapping[str, object]) -> bool:
    location_start = _location_start(location)
    return (
        _position(container, "start") <= location_start <= _position(container, "end")
    )


def _istanbul_function_scopes(
    surface: Surface, file_data: Mapping[str, object]
) -> list[Scope]:
    function_map = _mapping(file_data.get("fnMap", {}), "fnMap")
    function_hits = _mapping(file_data.get("f", {}), "function hits")
    statement_map = _mapping(file_data.get("statementMap", {}), "statementMap")
    statement_hits = _mapping(file_data.get("s", {}), "statement hits")
    branch_map = _mapping(file_data.get("branchMap", {}), "branchMap")
    branch_hits = _mapping(file_data.get("b", {}), "branch hits")
    scopes: list[Scope] = []
    for key, function_value in function_map.items():
        function = _mapping(function_value, f"fnMap.{key}")
        location = _mapping(function.get("loc", {}), f"fnMap.{key}.loc")
        name = function.get("name", "")
        if not isinstance(name, str):
            raise ValueError(f"fnMap.{key}.name must be a string")
        line_hits: dict[int, int] = {}
        for statement_key, statement_value in statement_map.items():
            statement = _mapping(statement_value, f"statementMap.{statement_key}")
            hits = statement_hits.get(statement_key)
            if _inside(statement, location) and isinstance(hits, int):
                line = _position(statement, "start")[0]
                line_hits[line] = max(line_hits.get(line, 0), hits)
        function_branch_hits: list[int] = []
        for branch_key, branch_value in branch_map.items():
            branch = _mapping(branch_value, f"branchMap.{branch_key}")
            hits = branch_hits.get(branch_key, [])
            if _inside(branch, location) and isinstance(hits, list):
                function_branch_hits.extend(hit for hit in hits if isinstance(hit, int))
        function_hit = function_hits.get(key)
        if isinstance(function_hit, int) and not line_hits:
            line_hits[_position(location, "start")[0]] = function_hit
        start_line = _position(location, "start")[0]
        scopes.append(
            Scope(
                scope_id=f"function:{surface.path}:{name}@{start_line}",
                level="function",
                name=name,
                path=surface.path,
                group=surface.group.name,
                tree=surface.group.tree,
                lines=Metric(
                    covered=sum(hit > 0 for hit in line_hits.values()),
                    total=len(line_hits),
                    kind="lines",
                ),
                branches=Metric(
                    covered=sum(hit > 0 for hit in function_branch_hits),
                    total=len(function_branch_hits),
                    kind="branches",
                ),
            )
        )
    return scopes


def build_frontend_report(
    manifest: Manifest,
    raw: Mapping[str, object],
    identity: ArtifactIdentity,
    root: Path = REPO_ROOT,
) -> HierarchyReport:
    """Build native Istanbul function/module/group/tree measurements for frontend."""
    normalized: dict[str, Mapping[str, object]] = {}
    for path, value in raw.items():
        relative = _relative_istanbul_path(path, root)
        if relative is not None:
            normalized[relative] = _mapping(value, path)
    modules: list[Scope] = []
    functions: list[Scope] = []
    surfaces = _production_surfaces(manifest, root, "frontend")
    for surface in surfaces:
        file_data = normalized.get(surface.path)
        if file_data is None:
            modules.append(_unmeasured_scope(surface))
            continue
        modules.append(_istanbul_module_scope(surface, file_data))
        functions.extend(_istanbul_function_scopes(surface, file_data))
    groups = {group.name: group for group in manifest.groups}
    scopes = functions + modules + _aggregate_scopes(modules, groups)
    return HierarchyReport(
        schema_version=1,
        report_only=True,
        language="frontend",
        identity=identity,
        limitations=manifest.limitations,
        scopes=tuple(sorted(scopes, key=lambda scope: scope.scope_id)),
    )


def _sha256(paths: Iterable[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        relative = (
            path.relative_to(root).as_posix()
            if path.is_relative_to(root)
            else str(path)
        )
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _git(*args: str, root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to identify coverage artifacts")
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def make_identity(
    manifest: Manifest,
    *,
    layer: str,
    tool: str,
    tool_version: str,
    root: Path = REPO_ROOT,
) -> ArtifactIdentity:
    """Bind one collected layer to its commit, config, manifest, and source content."""
    surfaces = discover_surfaces(manifest, root)
    source_paths = [root / surface.path for surface in surfaces]
    source_paths.append(Path(__file__).resolve())
    config_paths = [root / "pyproject.toml"]
    if tool == "vitest":
        config_paths = [
            root / "frontend" / "vite.config.ts",
            root / "frontend" / "package-lock.json",
        ]
    config_sha256 = _sha256(config_paths, root)
    config_set = os.environ.get("COVERAGE_CONFIG_SET", layer)
    config_sha256 = hashlib.sha256(f"{config_sha256}:{config_set}".encode()).hexdigest()
    return ArtifactIdentity(
        schema_version=1,
        commit=_git("rev-parse", "HEAD", root=root),
        config_sha256=config_sha256,
        manifest_sha256=_sha256([manifest.path], root),
        tool=tool,
        tool_version=tool_version,
        layer=layer,
        source_sha256=_sha256(source_paths, root),
        worktree_dirty=bool(_git("status", "--porcelain", root=root)),
    )


def identity_from_mapping(raw: Mapping[str, object]) -> ArtifactIdentity:
    return ArtifactIdentity(
        schema_version=_integer(raw, "schema_version"),
        commit=_string(raw, "commit"),
        config_sha256=_string(raw, "config_sha256"),
        manifest_sha256=_string(raw, "manifest_sha256"),
        tool=_string(raw, "tool"),
        tool_version=_string(raw, "tool_version"),
        layer=_string(raw, "layer"),
        source_sha256=_string(raw, "source_sha256"),
        worktree_dirty=_boolean(raw, "worktree_dirty"),
    )


def verify_identities(identities: Sequence[ArtifactIdentity]) -> None:
    """Refuse to merge coverage layers collected from different inputs."""
    if not identities:
        raise ValueError("at least one artifact identity is required")
    baseline = identities[0]
    if baseline.worktree_dirty:
        raise ValueError("coverage layer was collected from a dirty worktree")
    baseline_compatibility = LayerCompatibility.from_identity(baseline)
    for identity in identities[1:]:
        if identity.worktree_dirty:
            raise ValueError("coverage layer was collected from a dirty worktree")
        if identity.commit != baseline.commit:
            raise ValueError("commit identity mismatch between coverage layers")
        compatibility = LayerCompatibility.from_identity(identity)
        for field in dataclasses.fields(LayerCompatibility):
            if getattr(compatibility, field.name) != getattr(
                baseline_compatibility, field.name
            ):
                raise ValueError(
                    f"{field.name} identity mismatch between coverage layers"
                )


def verify_identities_against_current(
    identities: Sequence[ArtifactIdentity],
    current: ArtifactIdentity,
) -> None:
    """Reject layers stale against current tracked inputs.

    Coverage artifacts are downloaded into the verification checkout, so unrelated
    untracked output may make ``git status`` dirty. The current identity's explicit
    commit/config/manifest/source hashes are authoritative; collected layers themselves
    must still have been produced from clean worktrees.
    """
    # Run the collected-layer check outside the wrapper so its own diagnostics (dirty
    # worktree, missing identities) are not relabelled as a staleness mismatch.
    verify_identities(identities)
    try:
        current_inputs = current.model_copy(update={"worktree_dirty": False})
        verify_identities((*identities, current_inputs))
    except ValueError as error:
        raise ValueError(
            f"coverage artifacts are stale against current checkout: {error}"
        ) from error


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def render_terminal(report: HierarchyReport) -> str:
    lines = [
        f"{report.language} native coverage hierarchy (report-only)",
        f"commit: {report.identity.commit}",
        f"scopes: {len(report.scopes)}; deficits at <= {STRICT_FLOOR:.0f}%: "
        f"{len(report.deficits)}",
    ]
    for scope in report.deficits:
        lines.append(
            f"  {scope.scope_id}: lines {scope.lines.display}; "
            f"branches {scope.branches.display}"
        )
    return "\n".join(lines) + "\n"


def _write_report(report: HierarchyReport, output: Path, text_output: Path) -> None:
    _write_json(output, report.as_dict())
    text_output.parent.mkdir(parents=True, exist_ok=True)
    text = render_terminal(report)
    text_output.write_text(text, encoding="utf-8")
    print(text, end="")


def _python_raw_report(
    manifest: Manifest, data_file: Path, raw_output: Path, root: Path
) -> Mapping[str, object]:
    from coverage import Coverage as CoverageRuntime  # noqa: PLC0415

    paths = [
        str(root / surface.path)
        for surface in _production_surfaces(manifest, root, "python")
        if Path(surface.path).suffix == ".py"
    ]
    coverage = CoverageRuntime(
        data_file=str(data_file), config_file=str(root / "pyproject.toml")
    )
    coverage.load()
    if not coverage.get_data().measured_files():
        raise ValueError(
            f"coverage data file {data_file} has no measured files; "
            "the hierarchy report must run on collected coverage, not empty data"
        )
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    coverage.json_report(morfs=paths, outfile=str(raw_output), pretty_print=True)
    return _json_mapping(raw_output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    identity = subparsers.add_parser("identity")
    identity.add_argument("--layer", required=True)
    identity.add_argument("--tool", required=True)
    identity.add_argument("--tool-version", required=True)
    identity.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify-identities")
    verify.add_argument("identities", type=Path, nargs="+")
    verify.add_argument("--against-current", action="store_true")
    python_report = subparsers.add_parser("python-report")
    python_report.add_argument("--coverage-data", type=Path, required=True)
    python_report.add_argument("--identity", type=Path, required=True)
    python_report.add_argument("--raw-output", type=Path, required=True)
    python_report.add_argument("--output", type=Path, required=True)
    python_report.add_argument("--text-output", type=Path, required=True)
    frontend_report = subparsers.add_parser("frontend-report")
    frontend_report.add_argument("--coverage-json", type=Path, required=True)
    frontend_report.add_argument("--tool-version", required=True)
    frontend_report.add_argument("--output", type=Path, required=True)
    frontend_report.add_argument("--text-output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root: Path = args.root.resolve()
    manifest_path: Path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest = load_manifest(manifest_path.resolve(), root)
    if args.command == "validate":
        errors = validate_manifest(manifest, root)
        if errors:
            print("Coverage surface manifest violations:", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            return 1
        count = len(discover_surfaces(manifest, root))
        print(f"coverage surface manifest OK ({count} paths)")
        return 0
    if args.command == "identity":
        identity = make_identity(
            manifest,
            layer=args.layer,
            tool=args.tool,
            tool_version=args.tool_version,
            root=root,
        )
        _write_json(args.output, identity.as_dict())
        return 0
    if args.command == "verify-identities":
        identities = tuple(
            identity_from_mapping(_json_mapping(path)) for path in args.identities
        )
        verify_identities(identities)
        if args.against_current:
            baseline = identities[0]
            current = make_identity(
                manifest,
                layer="current-checkout",
                tool=baseline.tool,
                tool_version=baseline.tool_version,
                root=root,
            )
            verify_identities_against_current(identities, current)
        return 0
    if args.command == "python-report":
        identity = identity_from_mapping(_json_mapping(args.identity))
        raw = _python_raw_report(manifest, args.coverage_data, args.raw_output, root)
        report = build_python_report(manifest, raw, identity, root)
        _write_report(report, args.output, args.text_output)
        return 0
    if args.command == "frontend-report":
        identity = make_identity(
            manifest,
            layer="frontend-vitest",
            tool="vitest",
            tool_version=args.tool_version,
            root=root,
        )
        report = build_frontend_report(
            manifest, _json_mapping(args.coverage_json), identity, root
        )
        _write_report(report, args.output, args.text_output)
        return 0
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
