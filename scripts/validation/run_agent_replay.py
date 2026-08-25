#!/usr/bin/env python3
"""Run declared source-bound M1.6 replay and diagnostic commands without a shell."""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

import yaml

_RUN_ID = re.compile(
    r"neoplasm-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_FILLER = re.compile(r"(?:C[0-9]+|MINT-[0-9a-f]+)")
_MAX_FILLERS = 8
_DIAGNOSTIC_TIMEOUT_SECONDS = 20
_MAX_DIAGNOSTIC_CHARS = 8_192
_SECRET_VALUE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)"
)
_URL_CREDENTIALS = re.compile(r"(https?://[^\s:/]+:)[^@\s]+(@)")
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


class CommandResult(Protocol):
    returncode: int


class CapturedCommandResult(CommandResult, Protocol):
    stdout: str | bytes | None
    stderr: str | bytes | None


class CommandRunner(Protocol):
    def __call__(
        self, arguments: list[str], **kwargs: object
    ) -> CommandResult | CapturedCommandResult: ...


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


def _subprocess_runner(arguments: list[str], **kwargs: object) -> CommandResult:
    return subprocess.run(  # noqa: S603, PLW1510
        arguments,
        **kwargs,  # type: ignore[arg-type,return-value]
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
    )
    return result.returncode


def _bounded_sanitized(value: object) -> str:
    if value is None:
        return ""
    text = value.decode(errors="replace") if isinstance(value, bytes) else str(value)
    text = _ANSI_ESCAPE.sub("", text).replace("\x00", "")
    text = _SECRET_VALUE.sub(r"\1\2[REDACTED]", text)
    text = _URL_CREDENTIALS.sub(r"\1[REDACTED]\2", text)
    if len(text) <= _MAX_DIAGNOSTIC_CHARS:
        return text
    omitted = len(text) - _MAX_DIAGNOSTIC_CHARS
    retained_head = _MAX_DIAGNOSTIC_CHARS // 2
    retained_tail = _MAX_DIAGNOSTIC_CHARS - retained_head
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
        print(f"collection-error: {_bounded_sanitized(exc)}")
        return False
    print(f"exit-code: {result.returncode}")
    captured = cast("CapturedCommandResult", result)
    stdout = _bounded_sanitized(captured.stdout)
    stderr = _bounded_sanitized(captured.stderr)
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


_OPERATIONS: dict[str, Operation] = {
    "read-issue": _read_issue,
    "decompose-current": _decompose_current,
    "generate-current-evidence": _generate_current_evidence,
    "generate-axis-diagnostics": _generate_axis_diagnostics,
    "generate-group-review": _generate_group_review,
    "generate-r103-review": _generate_r103_review,
    "refresh-sparql-inventory": _refresh_sparql_inventory,
    "diagnose-stack": _diagnose_stack,
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
        return 2


if __name__ == "__main__":
    sys.exit(main())
