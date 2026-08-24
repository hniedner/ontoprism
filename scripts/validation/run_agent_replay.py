#!/usr/bin/env python3
"""Run the fixed, source-bound M1.6 current replay commands without a shell."""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

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


class CommandRunner(Protocol):
    def __call__(self, arguments: list[str], **kwargs: object) -> CommandResult: ...


class Operation(Protocol):
    def __call__(self, values: list[str], root: Path, runner: CommandRunner) -> int: ...


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
    stdout = _bounded_sanitized(getattr(result, "stdout", ""))
    stderr = _bounded_sanitized(getattr(result, "stderr", ""))
    if stdout:
        print("stdout:")
        print(stdout)
    if stderr:
        print("stderr:")
        print(stderr)
    return result.returncode == 0


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
    script, evidence, comparison = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
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
            "--output",
            str(root / "tmp/m1-6-group-review-packet.json"),
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
    json_status_supported = _collect_diagnostic_command(
        ["colima", "status", "--json"], root, runner
    )
    if not json_status_supported:
        _collect_diagnostic_command(["colima", "status"], root, runner)

    commands = [
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
    colima_root = Path.home() / ".colima"
    commands.extend(
        ["/usr/bin/tail", "-n", "200", str(path)]
        for path in (
            colima_root / "_lima/colima/ha.stderr.log",
            colima_root / "_lima/colima/ha.stdout.log",
            colima_root / "_lima/colima/serial.log",
            colima_root / "default/daemon.log",
        )
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
