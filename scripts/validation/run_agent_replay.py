#!/usr/bin/env python3
"""Run fixed operations, including mutating local container/tmp operations.

Operations return zero on success. Contract refusals and required-command failures raise
``AgentReplayInputError``; local filesystem setup failures may propagate as their native
environment exceptions. Cleanup failures are attached without replacing an earlier
operation failure.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, assert_never, cast

import yaml

from .docker_selectors import DOCKER_SELECTOR_VARIABLES

if TYPE_CHECKING:
    from collections.abc import Iterator

_RUN_ID = re.compile(
    r"neoplasm-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_FILLER = re.compile(r"(?:C[0-9]+|MINT-[0-9a-f]+)")
_MAX_FILLERS = 8
_DIAGNOSTIC_TIMEOUT_SECONDS = 20
_GATE_TIMEOUT_SECONDS = 3_600
_COMPOSE_TIMEOUT_SECONDS = 1_800
_MAX_DIAGNOSTIC_CHARS = 8_192
_POC_DIR = Path("tmp/podman-poc")
_PODMAN_PROJECT = "ontoprism-podman-poc"
_PODMAN_VOLUME = f"{_PODMAN_PROJECT}_ontoprism_pg_data"
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
_SECRET_VALUE = re.compile(
    r"(?i)([\"']?[A-Z0-9_-]*(?:PASSWORD|PASSWD|TOKEN|SECRET|API[_-]?KEY)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
_URL_CREDENTIALS = re.compile(
    r"((?:https?|postgresql(?:\+asyncpg)?)://[^\s:/]+:)[^@\s]+(@)", re.I
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_COLIMA_CONFIG_FIELDS = (
    "portForwarder",
    "vmType",
    "mountType",
    "mountInotify",
    "cpu",
    "memory",
    "disk",
    "runtime",
)
_COLIMA_TEXT_FIELDS = frozenset({"portForwarder", "vmType", "mountType", "runtime"})
_COLIMA_INTEGER_FIELDS = frozenset({"cpu", "memory", "disk"})
_COLIMA_TEXT_VALUE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_COLIMA_GUEST_DIAGNOSTICS = (
    ("date", "-u", "+%Y-%m-%dT%H:%M:%SZ"),
    ("cat", "/etc/os-release"),
    ("systemctl", "status", "docker", "--no-pager", "--full"),
    (
        "systemctl",
        "show",
        "docker",
        "--no-pager",
        "--property=ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,"
        "InactiveExitTimestamp,ActiveEnterTimestamp",
    ),
    ("rc-service", "docker", "status"),
    ("ps", "auxww"),
    (
        "sudo",
        "-n",
        "journalctl",
        "-u",
        "docker",
        "--since",
        "-2h",
        "--no-pager",
        "-n",
        "200",
        "-o",
        "short-iso",
    ),
    ("sudo", "-n", "tail", "-n", "200", "/var/log/docker.log"),
    (
        "sudo",
        "-n",
        "journalctl",
        "-u",
        "docker",
        "--since",
        "-2h",
        "--no-pager",
        "-n",
        "200",
        "-o",
        "short-iso",
        "--grep",
        "Stopping Docker|Stopped Docker|docker.service:|signal|shut down|"
        "no space left|permission denied",
        "--case-sensitive=no",
    ),
    (
        "sudo",
        "-n",
        "journalctl",
        "-k",
        "--since",
        "-2h",
        "--no-pager",
        "-n",
        "200",
        "-o",
        "short-iso",
    ),
    ("sudo", "-n", "dmesg", "-T"),
    (
        "sudo",
        "-n",
        "journalctl",
        "-k",
        "--since",
        "-2h",
        "--no-pager",
        "-n",
        "200",
        "-o",
        "short-iso",
        "--grep",
        "out of memory|oom-kill|killed process|invoked oom-killer",
        "--case-sensitive=no",
    ),
    ("free", "-h"),
    ("swapon", "--show"),
    ("df", "-h"),
    ("df", "-i"),
    ("stat", "-c", "%n %F %a %U %G", "/run/docker.sock"),
    ("stat", "-c", "%n %F %a %U %G", "/var/run/docker.sock"),
    (
        "curl",
        "--silent",
        "--show-error",
        "--fail",
        "--max-time",
        "5",
        "--unix-socket",
        "/var/run/docker.sock",
        "http://localhost/_ping",
    ),
    (
        "sudo",
        "-n",
        "journalctl",
        "--since",
        "-2h",
        "--no-pager",
        "-n",
        "200",
        "-o",
        "short-iso",
    ),
    ("sudo", "-n", "tail", "-n", "200", "/var/log/messages"),
    ("sudo", "-n", "tail", "-n", "200", "/var/log/syslog"),
)


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
    ) -> CapturedCommandResult: ...


class Operation(Protocol):
    def __call__(self, values: list[str], root: Path, runner: CommandRunner) -> int: ...


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping depth."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.MappingNode, *, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    keys: set[object] = set()
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in keys
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate key",
                key_node.start_mark,
            )
        keys.add(key)
    return cast(
        "dict[object, object]",
        yaml.SafeLoader.construct_mapping(loader, node, deep=deep),
    )


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


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
    command: list[str], root: Path, runner: CommandRunner
) -> bool:
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
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"collection-error: {_bounded_sanitized(str(exc))}")
        return False
    print(f"exit-code: {result.returncode}")
    stdout = _bounded_sanitized(result.stdout)
    stderr = _bounded_sanitized(result.stderr)
    if stdout:
        print("stdout:")
        print(stdout)
    if stderr:
        print("stderr:")
        print(stderr)
    return result.returncode == 0


def _colima_config_value_error(parsed: object) -> str | None:
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        return "invalid or duplicate-key YAML"
    for field in _COLIMA_CONFIG_FIELDS:
        if field not in parsed:
            continue
        value = parsed[field]
        if field in _COLIMA_TEXT_FIELDS:
            valid_type = isinstance(value, str)
        elif field in _COLIMA_INTEGER_FIELDS:
            valid_type = isinstance(value, int) and not isinstance(value, bool)
        else:
            valid_type = isinstance(value, bool)
        if not valid_type:
            return "allowlisted fields have invalid types"
    if any(
        field in parsed
        and field in _COLIMA_TEXT_FIELDS
        and _COLIMA_TEXT_VALUE.fullmatch(cast("str", parsed[field])) is None
        for field in _COLIMA_CONFIG_FIELDS
    ):
        return "allowlisted fields have invalid values"
    return None


def _report_colima_config() -> None:
    print("\n=== Colima config (allowlisted fields) ===")
    try:
        source = (Path.home() / ".colima/default/colima.yaml").read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeError):
        print("colima-config-error: unable to read authorized config")
        return
    try:
        parsed = yaml.load(source, Loader=_UniqueKeySafeLoader)  # noqa: S506
    except yaml.YAMLError:
        print("colima-config-error: invalid or duplicate-key YAML")
        return
    error = _colima_config_value_error(parsed)
    if error is not None:
        print(f"colima-config-error: {error}")
        return

    parsed = cast("dict[str, object]", parsed)
    for field in _COLIMA_CONFIG_FIELDS:
        if field not in parsed:
            print(f"{field}: <not set>")
            continue
        value = parsed[field]
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        print(f"{field}: {rendered}")


def _adjudication_inputs(root: Path) -> tuple[str, str, str, str, str]:
    return cast(
        "tuple[str, str, str, str, str]",
        tuple(
            _require_files(
                root,
                (
                    "scripts/adjudication.py",
                    "samples/ncit-26.07d-m1-current-replay.json",
                    "ontolib/tests/decomposition/golden/neoplasm-adjudicated.json",
                    "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json",
                    "ontolib/tests/decomposition/golden/proposal-registry.json",
                ),
            )
        ),
    )


def _read_issue(values: list[str], root: Path, runner: CommandRunner) -> int:
    if len(values) != 1 or not values[0].isdigit():
        raise AgentReplayInputError("issue number must be numeric")
    return _run(
        [
            "gh",
            "issue",
            "view",
            values[0],
            "--repo",
            "hniedner/ontoprism",
            "--json",
            "number,title,body,labels,milestone,state,url",
        ],
        root,
        runner,
    )


def _decompose_current(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("decompose-current accepts no arguments")
    script, source, sample = _require_files(
        root,
        (
            "scripts/decompose.py",
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "samples/ncit-26.07d-m1-current-replay.json",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "--source-manifest",
            source,
            "--branch",
            "neoplasm",
            "--sample-manifest",
            sample,
            "--out",
            str(root / "tmp/m1-6-current-replay.ttl"),
        ],
        root,
        runner,
    )


def _generate_current_evidence(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if len(values) != 1 or _RUN_ID.fullmatch(values[0]) is None:
        raise AgentReplayInputError("a persisted neoplasm run ID is required")
    script, sample, oracle, rows, registry = _adjudication_inputs(root)
    artifact = _require_files(root, ("tmp/m1-6-current-replay.ttl",))[0]
    golden = root / "ontolib/tests/decomposition/golden"
    return _run(
        [
            sys.executable,
            script,
            "generate-current-evidence",
            "--sample-manifest",
            sample,
            "--oracle",
            oracle,
            "--row-decisions",
            rows,
            "--proposal-registry",
            registry,
            "--run-id",
            values[0],
            "--artifact",
            artifact,
            "--engine-output",
            str(golden / "neoplasm-current-engine-evidence.json"),
            "--comparison-output",
            str(golden / "neoplasm-current-comparison.json"),
        ],
        root,
        runner,
    )


def _regenerate_current_comparison(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError(
            "regenerate-current-comparison accepts no arguments"
        )
    sys.path.insert(0, str(root))
    regenerate_current_comparison = importlib.import_module(
        "scripts.research.current_evidence"
    ).regenerate_current_comparison

    _script, _sample, oracle, rows, registry = _adjudication_inputs(root)
    golden = root / "ontolib/tests/decomposition/golden"
    evidence, _existing_output = _require_files(
        root,
        (
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        ),
    )
    regenerate_current_comparison(
        evidence_path=Path(evidence),
        oracle_path=Path(oracle),
        row_decisions_path=Path(rows),
        proposal_registry_path=Path(registry),
        output=golden / "neoplasm-current-comparison.json",
    )
    return 0


def _generate_axis_diagnostics(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if not values:
        raise AgentReplayInputError("at least one residual filler is required")
    if len(values) > _MAX_FILLERS:
        raise AgentReplayInputError("axis diagnostics accept at most 8 fillers")
    if len(values) != len(set(values)) or any(
        _FILLER.fullmatch(value) is None for value in values
    ):
        raise AgentReplayInputError("residual filler values are invalid")
    script, _sample, oracle, rows, registry = _adjudication_inputs(root)
    source, evidence, comparison = _require_files(
        root,
        (
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        ),
    )
    command = [
        sys.executable,
        script,
        "generate-axis-diagnostics",
        "--source-manifest",
        source,
        "--endpoint",
        "http://localhost:7888",
        "--oracle",
        oracle,
        "--row-decisions",
        rows,
        "--proposal-registry",
        registry,
        "--current-evidence",
        evidence,
        "--current-comparison",
        comparison,
    ]
    for filler in values:
        command.extend(("--residual-filler", filler))
    command.extend(("--output", str(root / "tmp/m1-6-axis-diagnostics.json")))
    return _run(command, root, runner)


def _generate_group_review(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("generate-group-review accepts no arguments")
    script, evidence, comparison, r101_report = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
            "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "generate-group-review-packet",
            "--current-evidence",
            evidence,
            "--current-comparison",
            comparison,
            "--r101-report",
            r101_report,
            "--output",
            str(root / "tmp/m1-6-group-review-packet.json"),
            "--workbook",
            str(root / "tmp/m1-6-group-review-workbook.xlsx"),
        ],
        root,
        runner,
    )


def _generate_r103_review(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("generate-r103-review accepts no arguments")
    script, owl, source, proposals = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "data/ncit-owl/Thesaurus-stated.owl",
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "ontolib/tests/decomposition/golden/proposal-registry.json",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "prepare-r103-review-packet",
            "--stated-owl",
            owl,
            "--source-manifest",
            source,
            "--proposal-registry",
            proposals,
            "--output-packet",
            str(root / "tmp/m1-6-r103-review-packet.json"),
            "--output-xlsx",
            str(root / "tmp/m1-6-r103-review-workbook.xlsx"),
        ],
        root,
        runner,
    )


def _validate_r101_current(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("validate-r101-current accepts no arguments")
    script, report, packet, registry = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
            "tmp/r101-review-packet-v3.json",
            "tmp/r101-review-registry-v3-SME.json",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "dry-run-r101-decision-expansion",
            "--report",
            report,
            "--packet",
            packet,
            "--registry",
            registry,
            "--output",
            str(root / "tmp/r101-review-current-validation.json"),
        ],
        root,
        runner,
    )


def _regenerate_r101_current_packet(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "regenerate-r101-current-packet accepts no arguments"
        )
    script, report, source = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "prepare-r101-review-packet",
            "--report",
            report,
            "--source-manifest",
            source,
            "--endpoint",
            "http://localhost:7888",
            "--output-packet",
            str(root / "tmp/r101-review-packet-current.json"),
            "--output-xlsx",
            str(root / "tmp/r101-review-workbook-current.xlsx"),
        ],
        root,
        runner,
    )


def _report_r101_current_reuse(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError("report-r101-current-reuse accepts no arguments")
    report, existing, current, registry = _require_files(
        root,
        (
            "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
            "tmp/r101-review-packet-v3.json",
            "tmp/r101-review-packet-current.json",
            "tmp/r101-review-registry-v3-SME.json",
        ),
    )
    generate = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).generate_r101_reuse_validation
    try:
        generate(
            report=Path(report),
            existing_packet=Path(existing),
            current_packet=Path(current),
            registry=Path(registry),
            output=root / "tmp/r101-review-current-validation.json",
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


def _audit_primary_sites(values: list[str], root: Path, runner: CommandRunner) -> int:
    del runner
    if values:
        raise AgentReplayInputError("audit-primary-sites accepts no arguments")
    source, baseline, artifact = _require_files(
        root,
        (
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
            "tmp/m1-6-current-full-corpus.ttl",
        ),
    )
    generate = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).generate_primary_site_audit
    try:
        generate(
            source_manifest=Path(source),
            baseline=Path(baseline),
            artifact=Path(artifact),
            output=root / "tmp/m1-6-primary-site-audit.json",
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


def _generate_pre_sme_readiness(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError("generate-pre-sme-readiness accepts no arguments")
    relatives = (
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
        "tmp/m1-6-current-full-corpus.ttl",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        "tmp/r101-review-current-validation.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "tmp/m1-6-primary-site-audit.json",
        "tmp/m1-6-group-review-packet.json",
        "tmp/m1-6-r103-review-packet.json",
        "tmp/m1-6-verify-evidence.json",
    )
    paths = tuple(Path(item) for item in _require_files(root, relatives))
    generate = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).generate_pre_sme_readiness
    names = (
        "source_manifest",
        "current_evidence",
        "current_comparison",
        "corpus_baseline",
        "corpus_artifact",
        "r101_report",
        "r101_validation",
        "proposal_registry",
        "primary_site_audit",
        "group_packet",
        "r103_packet",
        "verify_evidence",
    )
    try:
        git_head = _capture_required(["git", "rev-parse", "HEAD"], root, runner).strip()
        generate(
            **dict(zip(names, paths, strict=True)),
            expected_git_head=git_head,
            output=root / "tmp/m1-6-machine-readiness.json",
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


def _refresh_sparql_inventory(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError("refresh-sparql-inventory accepts no arguments")
    script = _require_files(root, ("scripts/validation/write_sparql_inventory.py",))[0]
    return _run(
        [
            sys.executable,
            script,
            "--root",
            str(root),
            "--output",
            str(root / "scripts/validation/sparql-inventory.json"),
        ],
        root,
        runner,
    )


def _diagnose_stack(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("diagnose-stack accepts no arguments")

    captured_now = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    _report_colima_config()
    json_status_supported = _collect_diagnostic_command(
        ["colima", "status", "--json"], root, runner
    )
    if not json_status_supported:
        _collect_diagnostic_command(["colima", "status"], root, runner)

    colima_root = Path.home() / ".colima"
    host_socket = colima_root / "default/docker.sock"
    commands = [
        [
            "/usr/bin/stat",
            "-f",
            "%N %HT %Sp %Su %Sg",
            str(host_socket),
        ],
        ["/usr/sbin/lsof", "-n", "-a", "-U", str(host_socket)],
        ["/usr/bin/pgrep", "-alf", "colima|lima"],
        [
            "/usr/bin/env",
            f"LIMA_HOME={colima_root / '_lima'}",
            "limactl",
            "list",
            "--json",
        ],
        ["docker", "context", "show"],
        [
            "docker",
            "context",
            "inspect",
            "--format",
            "{{json .Endpoints.docker.Host}}",
        ],
        *(
            ["/usr/bin/printenv", variable]
            for variable in (
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
            )
        ),
        ["docker", "info"],
        ["docker", "ps", "-a", "--no-trunc"],
        ["docker", "compose", "ps", "-a"],
        *(
            [
                "docker",
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
        ["docker", "events", "--since", "2h", "--until", captured_now],
        [
            "docker",
            "compose",
            "logs",
            "--since",
            "2h",
            "--no-color",
            "--tail",
            "200",
        ],
    ]
    log_paths = (
        colima_root / "_lima/colima/ha.stderr.log",
        colima_root / "_lima/colima/ha.stdout.log",
        colima_root / "_lima/colima/serial.log",
        colima_root / "default/daemon.log",
    )
    commands.extend(["/usr/bin/tail", "-n", "200", str(path)] for path in log_paths)
    commands.extend(
        [
            "/usr/bin/grep",
            "-E",
            "-i",
            "-n",
            "-m",
            "200",
            "docker\\.sock|socket|forward|stopp|clos|exit|fatal|error",
            str(path),
        ]
        for path in log_paths
    )
    commands.extend(
        ["colima", "ssh", "--", *guest_command]
        for guest_command in _COLIMA_GUEST_DIAGNOSTICS
    )
    for command in commands:
        _collect_diagnostic_command(command, root, runner)
    return 0


def _inspect_podman(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("inspect-podman accepts no arguments")
    commands = (
        [_PODMAN, "version", "--format", "json"],
        [_PODMAN, "info", "--format", "json"],
        [_PODMAN, "machine", "list", "--format", "json"],
        [_PODMAN, "machine", "inspect", _PODMAN_MACHINE],
        [_PODMAN, "system", "connection", "list", "--format", "json"],
        ["/usr/bin/which", "docker"],
        ["/usr/bin/which", "docker-compose"],
        [_DOCKER, "version"],
        [_DOCKER_COMPOSE, "version"],
        [_PODMAN, "compose", "version"],
    )
    for command in commands:
        _collect_diagnostic_command(command, root, runner)
    return 0


def _capture_required(
    command: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    environment: dict[str, str] | None = None,
    timeout: int = _DIAGNOSTIC_TIMEOUT_SECONDS,
    display_limit: int | None = _MAX_DIAGNOSTIC_CHARS,
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
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_stream_text(exc.stdout)
        stderr = _timeout_stream_text(exc.stderr)
        labelled = _labelled_streams(
            stdout or "", stderr or "", display_limit=display_limit
        )
        raise AgentReplayInputError(
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


def _podman_socket(root: Path, runner: CommandRunner) -> Path:
    output = _capture_required(
        [_PODMAN, "machine", "inspect", _PODMAN_MACHINE], root, runner
    )
    try:
        payload = json.loads(output)
        machine = payload[0]
        socket_path = Path(machine["ConnectionInfo"]["PodmanSocket"]["Path"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid Podman machine contract") from exc
    if (
        len(payload) != 1
        or machine.get("Name") != _PODMAN_MACHINE
        or machine.get("State") != "running"
        or machine.get("Rootful") is not False
        or not socket_path.is_absolute()
        or socket_path.name != "ontoprism-vm-api.sock"
        or socket_path.parent.name != "podman"
    ):
        raise AgentReplayInputError("invalid Podman machine contract")
    return socket_path


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
) -> int:
    if values:
        raise AgentReplayInputError(f"{operation} accepts no arguments")
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


def _capture_pre_sme_verify(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    _podman_gate(
        values,
        root,
        runner,
        operation="capture-pre-sme-verify",
        script="verify",
        routing="context",
    )
    git_head = _capture_required(["git", "rev-parse", "HEAD"], root, runner).strip()
    writer = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).write_verify_evidence
    try:
        writer(root / "tmp/m1-6-verify-evidence.json", git_head=git_head)
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


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
    with _reserved_fixed_ports(_DATA_PORTS):
        pass
    compose_file = _require_files(root, ("docker-compose.yml",))[0]
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
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
    output: str, *, root: Path, service: ComposeService
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
    if health != "healthy":
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
        labels.get("com.docker.compose.project") != _PODMAN_PROJECT
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


_OPERATIONS: dict[str, Operation] = {
    "read-issue": _read_issue,
    "decompose-current": _decompose_current,
    "generate-current-evidence": _generate_current_evidence,
    "regenerate-current-comparison": _regenerate_current_comparison,
    "generate-axis-diagnostics": _generate_axis_diagnostics,
    "generate-group-review": _generate_group_review,
    "generate-r103-review": _generate_r103_review,
    "validate-r101-current": _validate_r101_current,
    "regenerate-r101-current-packet": _regenerate_r101_current_packet,
    "report-r101-current-reuse": _report_r101_current_reuse,
    "audit-primary-sites": _audit_primary_sites,
    "generate-pre-sme-readiness": _generate_pre_sme_readiness,
    "refresh-sparql-inventory": _refresh_sparql_inventory,
    "diagnose-stack": _diagnose_stack,
    "inspect-podman": _inspect_podman,
    "activate-podman-docker-context": _activate_podman_docker_context,
    "check-podman-api": _check_podman_api,
    "podman-test-integration": _podman_test_integration,
    "podman-test-full-store": _podman_test_full_store,
    "podman-verify": _podman_verify,
    "capture-pre-sme-verify": _capture_pre_sme_verify,
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
