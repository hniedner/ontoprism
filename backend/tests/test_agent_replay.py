"""Policy tests for the narrowly scoped current-replay agent wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts.validation.run_agent_replay import (
    AgentReplayInputError,
    run_agent_replay,
)


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _DiagnosticRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments == ["colima", "status", "--json"]:
            return _Result(1, stderr="unknown flag: --json")
        if arguments[:2] == ["docker", "info"]:
            return _Result(1, stderr="Cannot connect to Docker daemon")
        if arguments[:3] == ["docker", "ps", "-a"]:
            return _Result(
                0,
                stdout=(
                    "ontoprism-postgres Exited (137) Password=hunter2 "
                    + "x" * 20_000
                    + " RECENT-END"
                ),
            )
        return _Result(0, stdout="diagnostic evidence")


@pytest.mark.unit
def test_current_replay_uses_only_the_documented_fixed_inputs(tmp_path: Path) -> None:
    for relative in (
        "scripts/decompose.py",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "samples/ncit-26.07d-m1-current-replay.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = _Runner()

    assert run_agent_replay(["decompose-current"], tmp_path, runner=runner) == 0

    command, options = runner.calls[0]
    assert command[1:] == [
        str(tmp_path / "scripts/decompose.py"),
        "--source-manifest",
        str(tmp_path / "data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        "--branch",
        "neoplasm",
        "--sample-manifest",
        str(tmp_path / "samples/ncit-26.07d-m1-current-replay.json"),
        "--out",
        str(tmp_path / "tmp/m1-6-current-replay.ttl"),
    ]
    assert options["shell"] is False


@pytest.mark.unit
def test_evidence_generation_requires_a_real_neoplasm_run_id(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="run ID"):
        run_agent_replay(["generate-current-evidence", "guessed-run"], tmp_path)


@pytest.mark.unit
def test_axis_diagnostics_reject_unsafe_or_unbounded_fillers(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="filler"):
        run_agent_replay(
            ["generate-axis-diagnostics", "C35501", "../../unsafe"], tmp_path
        )
    with pytest.raises(AgentReplayInputError, match="at most 8"):
        run_agent_replay(
            ["generate-axis-diagnostics", *(f"C{index}" for index in range(9))],
            tmp_path,
        )


@pytest.mark.unit
def test_wrapper_rejects_unlisted_operations(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="unsupported"):
        run_agent_replay(["import-workbook"], tmp_path)


@pytest.mark.unit
def test_inventory_refresh_uses_only_the_repository_generator(tmp_path: Path) -> None:
    script = tmp_path / "scripts/validation/write_sparql_inventory.py"
    script.parent.mkdir(parents=True)
    script.touch()
    runner = _Runner()

    assert run_agent_replay(["refresh-sparql-inventory"], tmp_path, runner=runner) == 0

    command, _options = runner.calls[0]
    assert command[1:] == [
        str(script),
        "--root",
        str(tmp_path),
        "--output",
        str(tmp_path / "scripts/validation/sparql-inventory.json"),
    ]


@pytest.mark.unit
def test_diagnose_stack_runs_only_fixed_bounded_read_only_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _DiagnosticRunner()

    assert run_agent_replay(["diagnose-stack"], tmp_path, runner=runner) == 0

    commands = [command for command, _options in runner.calls]
    events = next(
        command for command in commands if command[:2] == ["docker", "events"]
    )
    assert events[:4] == ["docker", "events", "--since", "2h"]
    assert events[4] == "--until"
    assert events[5].endswith("Z")
    inspect_commands = [
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
    ]
    guest_commands = [
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
        ["cat", "/etc/os-release"],
        ["systemctl", "status", "docker", "--no-pager", "--full"],
        [
            "systemctl",
            "show",
            "docker",
            "--no-pager",
            "--property=ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,InactiveExitTimestamp,ActiveEnterTimestamp",
        ],
        ["rc-service", "docker", "status"],
        ["ps", "auxww"],
        [
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
        ],
        ["sudo", "-n", "tail", "-n", "200", "/var/log/docker.log"],
        [
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
        ],
        [
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
        ],
        ["sudo", "-n", "dmesg", "-T"],
        [
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
        ],
        ["free", "-h"],
        ["swapon", "--show"],
        ["df", "-h"],
        ["df", "-i"],
        ["stat", "-c", "%n %F %a %U %G", "/run/docker.sock"],
        ["stat", "-c", "%n %F %a %U %G", "/var/run/docker.sock"],
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--max-time",
            "5",
            "--unix-socket",
            "/var/run/docker.sock",
            "http://localhost/_ping",
        ],
        [
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
        ],
        ["sudo", "-n", "tail", "-n", "200", "/var/log/messages"],
        ["sudo", "-n", "tail", "-n", "200", "/var/log/syslog"],
    ]
    colima_root = Path.home() / ".colima"
    host_socket = colima_root / "default/docker.sock"
    host_agent_pattern = "colima|lima"
    forwarding_pattern = "docker\\.sock|socket|forward|stopp|clos|exit|fatal|error"
    host_commands = [
        [
            "/usr/bin/stat",
            "-f",
            "%N %HT %Sp %Su %Sg",
            str(host_socket),
        ],
        ["/usr/sbin/lsof", "-n", "-a", "-U", str(host_socket)],
        ["/usr/bin/pgrep", "-alf", host_agent_pattern],
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
    ]
    log_paths = (
        colima_root / "_lima/colima/ha.stderr.log",
        colima_root / "_lima/colima/ha.stdout.log",
        colima_root / "_lima/colima/serial.log",
        colima_root / "default/daemon.log",
    )
    assert commands == [
        ["colima", "status", "--json"],
        ["colima", "status"],
        *host_commands,
        ["docker", "info"],
        ["docker", "ps", "-a", "--no-trunc"],
        ["docker", "compose", "ps", "-a"],
        *inspect_commands,
        events,
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
        *(["/usr/bin/tail", "-n", "200", str(path)] for path in log_paths),
        *(
            [
                "/usr/bin/grep",
                "-E",
                "-i",
                "-n",
                "-m",
                "200",
                forwarding_pattern,
                str(path),
            ]
            for path in log_paths
        ),
        *(["colima", "ssh", "--", *command] for command in guest_commands),
    ]
    assert all(options["cwd"] == tmp_path for _command, options in runner.calls)
    assert all(options["shell"] is False for _command, options in runner.calls)
    assert all(options["timeout"] == 20 for _command, options in runner.calls)
    assert all(options["capture_output"] is True for _command, options in runner.calls)
    assert all(options["text"] is True for _command, options in runner.calls)

    output = capsys.readouterr().out
    assert "unknown flag: --json" in output
    assert "Cannot connect to Docker daemon" in output
    assert "Exited (137)" in output
    assert "hunter2" not in output
    assert "[REDACTED]" in output
    assert "[TRUNCATED" in output
    assert "RECENT-END" in output
    assert len(output) < 30_000


@pytest.mark.unit
def test_diagnose_stack_rejects_all_user_arguments(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["diagnose-stack", "--since", "24h"], tmp_path)
