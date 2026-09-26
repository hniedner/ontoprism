"""Policy tests for the narrowly scoped current-replay agent wrapper."""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
import scripts.validation.run_agent_replay as replay
from scripts.validation.docker_selectors import DOCKER_SELECTOR_VARIABLES
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


@pytest.mark.unit
@pytest.mark.parametrize(
    "operation",
    [
        "activate-enhanced-ncit-showcase",
        "verify-enhanced-ncit-showcase",
    ],
)
def test_retired_showcase_operations_are_refused(
    tmp_path: Path,
    operation: str,
) -> None:
    runner = _Runner()
    with pytest.raises(AgentReplayInputError, match="unsupported"):
        run_agent_replay([operation], tmp_path, runner=runner)
    assert runner.calls == []


class _PodmanDiagnosticRunner:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-vm",
                            "State": "running",
                            "Rootful": False,
                            "SSHConfig": {
                                "Port": 49969,
                                "RemoteUsername": "core",
                            },
                            "ConnectionInfo": {
                                "PodmanSocket": {"Path": str(self.socket_path)}
                            },
                        }
                    ]
                ),
            )
        if arguments == ["/opt/homebrew/bin/docker", "info"]:
            return _Result(1, stderr="Cannot connect to API password=hunter2")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "compose",
            "logs",
            "--since",
            "2h",
            "--no-color",
            "--tail",
            "200",
        ]:
            return _Result(0, stdout="x" * 20_000 + " RECENT-END")
        return _Result(0, stdout="podman diagnostic evidence")


class _PodmanApiRunner:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments[:2] == ["/opt/homebrew/bin/docker", "ps"]:
            return _Result(0)
        if arguments[:2] == ["/opt/homebrew/bin/docker", "inspect"]:
            return _Result(1, stderr="no such object")
        if arguments[:3] == ["/opt/homebrew/bin/docker", "volume", "inspect"]:
            return _Result(1, stderr="no such volume")
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-vm",
                            "State": "running",
                            "Rootful": False,
                            "SSHConfig": {
                                "Port": 49969,
                                "RemoteUsername": "core",
                            },
                            "ConnectionInfo": {
                                "PodmanSocket": {"Path": str(self.socket_path)}
                            },
                        }
                    ]
                ),
            )
        return _Result(0, stdout="compatible")


class _DockerContextRunner(_PodmanApiRunner):
    def __init__(
        self,
        socket_path: Path,
        *,
        contexts: tuple[str, ...] = ("default",),
        current: str = "default",
    ) -> None:
        super().__init__(socket_path)
        self.contexts = contexts
        self.current = current

    def __call__(  # noqa: PLR0911 - fixed command-result table for the fake CLI
        self, arguments: list[str], **kwargs: object
    ) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-vm",
                            "State": "running",
                            "Rootful": False,
                            "SSHConfig": {
                                "Port": 49969,
                                "RemoteUsername": "core",
                            },
                            "ConnectionInfo": {
                                "PodmanSocket": {"Path": str(self.socket_path)}
                            },
                        }
                    ]
                ),
            )
        if arguments == ["/opt/homebrew/bin/docker", "context", "show"]:
            return _Result(0, stdout=f"{self.current}\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "context",
            "ls",
            "--format",
            "{{.Name}}",
        ]:
            return _Result(0, stdout="\n".join(self.contexts) + "\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "context",
            "inspect",
            "ontoprism-podman",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-podman",
                            "Metadata": {
                                "Description": "OntoPrism rootless Podman machine"
                            },
                            "Endpoints": {
                                "docker": {
                                    "Host": f"unix://{self.socket_path}",
                                    "SkipTLSVerify": False,
                                }
                            },
                        }
                    ]
                ),
            )
        if arguments == [
            "/opt/homebrew/bin/docker",
            "context",
            "use",
            "ontoprism-podman",
        ]:
            self.current = "ontoprism-podman"
            return _Result(0, stdout="ontoprism-podman\n")
        if arguments == ["/opt/homebrew/bin/docker", "version"]:
            return _Result(0, stdout="Server:\n Podman Engine:\n  Version: 6.1.0\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "info",
            "--format",
            "{{json .}}",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    {
                        "OSType": "linux",
                        "ServerVersion": "6.1.0",
                        "DockerRootDir": (
                            "/var/home/core/.local/share/containers/storage"
                        ),
                        "SecurityOptions": [
                            "name=seccomp,profile=default",
                            "name=rootless",
                        ],
                        "ProductLicense": "Apache-2.0",
                    }
                ),
            )
        return _Result(0)


def _write_compose_inputs(root: Path, *, app: bool = False) -> None:
    (root / "docker-compose.yml").touch()
    if app:
        (root / "docker-compose.app.yml").touch()
        (root / "Caddyfile").touch()


@pytest.mark.unit
def test_wrapper_rejects_unlisted_operations(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="unsupported"):
        run_agent_replay(["import-workbook"], tmp_path)


@pytest.mark.unit
def test_wrapper_rejects_retired_issue_127_experiment_operations(
    tmp_path: Path,
) -> None:
    retired = {
        "decompose-current",
        "read-issue",
        "inspect-current-replay",
        "record-artifact-registry",
        "generate-current-evidence",
        "generate-current-evidence-candidate",
        "regenerate-current-comparison",
        "generate-axis-diagnostics",
        "generate-group-review-rev2-candidate",
        "generate-grouping-detector-candidate",
        "generate-normalized-group-policy-candidate",
        "promote-normalized-group-policy-candidate",
        "generate-specialist-literature-context",
        "generate-specialist-cadsr-usage",
        "generate-specialist-review-packets",
        "validate-specialist-review-generation",
        "generate-r103-review",
        "generate-r103-evidence-application",
        "transcribe-r103-specificity-selection",
        "validate-r101-current",
        "regenerate-r101-current-packet",
        "report-r101-current-reuse",
        "audit-primary-sites",
        "generate-pre-sme-readiness",
        "capture-pre-sme-verify",
        "inspect-decomposition-runs",
        "generate-current-r101-conservation",
        "generate-current-corpus-baseline",
        "qualify-current-r101-comparator",
        "promote-current-r101-evidence",
        "record-current-r101-diagnostic",
        "inspect-r101-report",
        "generate-mixed-chain-inventory",
        "record-mixed-chain-inventory",
        "generate-mixed-chain-corrected-projection",
        "record-mixed-chain-corrected-projection",
    }

    assert retired.isdisjoint(replay._OPERATIONS)
    for operation in retired:
        with pytest.raises(AgentReplayInputError, match="unsupported"):
            run_agent_replay([operation], tmp_path)


@pytest.mark.unit
def test_inspect_podman_runs_only_fixed_bounded_read_only_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanDiagnosticRunner(socket_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "PATH": "inherited",
            **dict.fromkeys(DOCKER_SELECTOR_VARIABLES, "unsafe"),
        },
    )

    assert run_agent_replay(["inspect-podman"], tmp_path, runner=runner) == 0

    commands = [command for command, _options in runner.calls]
    events = next(
        command for command in commands if command[1:3] == ["events", "--since"]
    )
    assert events[:4] == ["/opt/homebrew/bin/docker", "events", "--since", "2h"]
    assert events[4] == "--until"
    assert events[5].endswith("Z")
    assert commands == [
        [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ],
        ["/opt/homebrew/bin/podman", "version", "--format", "json"],
        ["/opt/homebrew/bin/podman", "info", "--format", "json"],
        ["/opt/homebrew/bin/podman", "machine", "list", "--format", "json"],
        [
            "/opt/homebrew/bin/podman",
            "system",
            "connection",
            "list",
            "--format",
            "json",
        ],
        ["/usr/bin/stat", "-f", "%N %HT %Sp %Su %Sg", str(socket_path)],
        ["/usr/sbin/lsof", "-n", "-a", "-U", str(socket_path)],
        ["/opt/homebrew/bin/docker", "context", "show"],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "inspect",
            "ontoprism-podman",
        ],
        *(["/usr/bin/printenv", variable] for variable in DOCKER_SELECTOR_VARIABLES),
        ["/opt/homebrew/bin/docker", "version"],
        ["/opt/homebrew/bin/docker", "info"],
        ["/opt/homebrew/bin/docker-compose", "version"],
        ["/opt/homebrew/bin/podman", "compose", "version"],
        ["/opt/homebrew/bin/docker", "compose", "config", "--services"],
        ["/opt/homebrew/bin/docker", "compose", "ps", "-a"],
        *(
            [
                "/opt/homebrew/bin/docker",
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
        events,
        [
            "/opt/homebrew/bin/docker",
            "compose",
            "logs",
            "--since",
            "2h",
            "--no-color",
            "--tail",
            "200",
        ],
    ]
    assert all(options["cwd"] == tmp_path for _command, options in runner.calls)
    assert all(options["shell"] is False for _command, options in runner.calls)
    assert all(options["timeout"] == 20 for _command, options in runner.calls)
    assert all(options["capture_output"] is True for _command, options in runner.calls)
    assert all(options["text"] is True for _command, options in runner.calls)
    assert runner.calls[0][1]["env"] is None
    for _command, options in runner.calls[1:]:
        assert options["env"] == {"PATH": "inherited"}
    output = capsys.readouterr().out
    assert "podman diagnostic evidence" in output
    assert "hunter2" not in output
    assert "[REDACTED]" in output
    assert "[TRUNCATED" in output
    assert "RECENT-END" in output
    assert len(output) < 30_000


@pytest.mark.unit
def test_diagnostic_command_reports_failure_as_evidence_not_a_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def runner(arguments: list[str], **_kwargs: object) -> _Result:
        return _Result(17, stderr="diagnostic failed")

    completion = replay._collect_diagnostic_command(
        ["diagnostic", "status"], tmp_path, runner
    )

    assert completion is None
    assert "exit-code: 17" in capsys.readouterr().out


@pytest.mark.unit
def test_inspect_podman_rejects_all_user_arguments(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["inspect-podman", "--url", "unsafe"], tmp_path)


@pytest.mark.unit
def test_check_podman_api_pins_socket_cli_and_compose_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanApiRunner(socket_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "PATH": "inherited",
            **dict.fromkeys(DOCKER_SELECTOR_VARIABLES, "unsafe"),
        },
    )

    assert run_agent_replay(["check-podman-api"], tmp_path, runner=runner) == 0

    assert [command for command, _options in runner.calls] == [
        [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ],
        ["/opt/homebrew/bin/docker", "version"],
        ["/opt/homebrew/bin/docker", "info", "--format", "{{json .}}"],
        ["/opt/homebrew/bin/docker-compose", "version"],
        ["/opt/homebrew/bin/podman", "compose", "version"],
    ]
    for _command, options in runner.calls[1:]:
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment["DOCKER_HOST"] == f"unix://{socket_path}"
        assert environment["PODMAN_COMPOSE_PROVIDER"] == (
            "/opt/homebrew/bin/docker-compose"
        )
        assert set(environment).intersection(DOCKER_SELECTOR_VARIABLES) == {
            "DOCKER_HOST",
            "PODMAN_COMPOSE_PROVIDER",
        }
        assert environment["PATH"].startswith(f"{tmp_path}/.venv/bin:/opt/homebrew/bin")
        assert options["shell"] is False
        assert options["timeout"] == 20


@pytest.mark.unit
def test_activate_podman_context_creates_uses_and_verifies_exact_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(socket_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "PATH": "inherited",
            "DOCKER_HOST": "tcp://unsafe",
            "DOCKER_CONTEXT": "unsafe",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": "/unsafe",
        },
    )

    assert (
        run_agent_replay(["activate-podman-docker-context"], tmp_path, runner=runner)
        == 0
    )

    assert [command for command, _options in runner.calls] == [
        [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ],
        ["/opt/homebrew/bin/docker", "context", "show"],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "ls",
            "--format",
            "{{.Name}}",
        ],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "create",
            "ontoprism-podman",
            "--description",
            "OntoPrism rootless Podman machine",
            "--docker",
            f"host=unix://{socket_path}",
        ],
        ["/opt/homebrew/bin/docker", "context", "use", "ontoprism-podman"],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "inspect",
            "ontoprism-podman",
        ],
        ["/opt/homebrew/bin/docker", "context", "show"],
        ["/opt/homebrew/bin/docker", "version"],
        [
            "/opt/homebrew/bin/docker",
            "info",
            "--format",
            "{{json .}}",
        ],
    ]
    assert all(options["shell"] is False for _command, options in runner.calls)
    assert all(options["timeout"] == 20 for _command, options in runner.calls)
    assert runner.calls[0][1]["env"] is None
    for _command, options in runner.calls[1:]:
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment == {"PATH": "inherited"}
    output = capsys.readouterr().out
    assert "prior-docker-context=default" in output
    assert "active-docker-context=ontoprism-podman" in output
    assert f"podman-docker-endpoint=unix://{socket_path}" in output
    assert "docker-server=Podman" in output
    assert "podman-api-contract=rootless+containers-storage+apache-2.0" in output


@pytest.mark.unit
def test_activate_podman_context_updates_only_safe_exact_existing_context(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(
        socket_path,
        contexts=("default", "ontoprism-podman"),
    )

    assert (
        run_agent_replay(["activate-podman-docker-context"], tmp_path, runner=runner)
        == 0
    )

    commands = [command for command, _options in runner.calls]
    assert [
        "/opt/homebrew/bin/docker",
        "context",
        "update",
        "ontoprism-podman",
        "--description",
        "OntoPrism rootless Podman machine",
        "--docker",
        f"host=unix://{socket_path}",
    ] in commands
    assert not any("create" in command for command in commands)


@pytest.mark.unit
def test_activate_podman_context_refuses_unsafe_existing_context_before_mutation(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _UnsafeContext(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[-3:] == ["context", "inspect", "ontoprism-podman"]:
                payload = json.loads(result.stdout)
                payload[0]["Endpoints"]["kubernetes"] = {"Host": "unsafe"}
                result.stdout = json.dumps(payload)
            return result

    runner = _UnsafeContext(
        socket_path,
        contexts=("default", "ontoprism-podman"),
    )
    with pytest.raises(AgentReplayInputError, match="safe Docker context contract"):
        run_agent_replay(["activate-podman-docker-context"], tmp_path, runner=runner)
    assert not any(
        "update" in command or "use" in command for command, _options in runner.calls
    )


@pytest.mark.unit
def test_activate_podman_context_rejects_arguments_and_non_podman_server(
    tmp_path: Path,
) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["activate-podman-docker-context", "unsafe"], tmp_path)

    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _DockerServer(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["/opt/homebrew/bin/docker", "version"]:
                result.stdout = "Server: Docker Engine\n"
            return result

    with pytest.raises(AgentReplayInputError, match="Podman server predicate"):
        run_agent_replay(
            ["activate-podman-docker-context"],
            tmp_path,
            runner=_DockerServer(socket_path),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("Name", "decoy-vm"),
        ("State", "stopped"),
        ("Rootful", True),
    ],
)
def test_check_podman_api_rejects_wrong_machine_contract(
    changed_field: str, changed_value: object, tmp_path: Path
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _InvalidRunner(_PodmanApiRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[:3] == [
                "/opt/homebrew/bin/podman",
                "machine",
                "inspect",
            ]:
                payload = json.loads(result.stdout)
                payload[0][changed_field] = changed_value
                result.stdout = json.dumps(payload)
            return result

    with pytest.raises(AgentReplayInputError, match="machine contract"):
        run_agent_replay(
            ["check-podman-api"], tmp_path, runner=_InvalidRunner(socket_path)
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operation", "command"),
    [
        (
            "podman-test-integration",
            ["/opt/homebrew/bin/pdm", "run", "test-integration"],
        ),
        (
            "podman-test-full-store",
            ["/opt/homebrew/bin/pdm", "run", "test-integration-full-store"],
        ),
    ],
)
def test_podman_gate_operations_use_fixed_commands_and_controlled_runtime(
    operation: str,
    command: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanApiRunner(socket_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "SAFE_SETTING": "retained",
            "PATH": "unsafe",
            "ONTOPRISM_PODMAN_STACK_ENSURED": "1",
        },
    )

    assert run_agent_replay([operation], tmp_path, runner=runner) == 0

    assert runner.calls[-1][0] == command
    options = runner.calls[-1][1]
    assert options["shell"] is False
    assert options["timeout"] == 3600
    environment = options["env"]
    assert environment == {
        "SAFE_SETTING": "retained",
        "PATH": (
            f"{tmp_path}/.venv/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:unsafe"
        ),
        "DOCKER_HOST": f"unix://{socket_path}",
        "PODMAN_COMPOSE_PROVIDER": "/opt/homebrew/bin/docker-compose",
        "ONTOPRISM_PODMAN_STACK_ENSURED": "1",
    }


@pytest.mark.unit
def test_podman_verify_requires_selected_exact_context_and_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(socket_path)
    runner.current = "ontoprism-podman"
    monkeypatch.setattr(
        os,
        "environ",
        {
            "SAFE_SETTING": "retained",
            "PATH": "safe",
            "ONTOPRISM_PODMAN_STACK_ENSURED": "1",
        },
    )

    assert run_agent_replay(["podman-verify"], tmp_path, runner=runner) == 0

    assert [command for command, _options in runner.calls[-3:]] == [
        ["/opt/homebrew/bin/docker", "context", "show"],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "inspect",
            "ontoprism-podman",
        ],
        ["/opt/homebrew/bin/pdm", "run", "verify"],
    ]
    gate_environment = runner.calls[-1][1]["env"]
    assert gate_environment == {
        "SAFE_SETTING": "retained",
        "PATH": "safe",
        "ONTOPRISM_PODMAN_STACK_ENSURED": "1",
    }


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["wrong-context", "wrong-endpoint"])
def test_podman_verify_refuses_non_podman_selected_context(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONTOPRISM_PODMAN_STACK_ENSURED", "1")
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(socket_path)
    runner.current = "default" if failure == "wrong-context" else "ontoprism-podman"

    if failure == "wrong-endpoint":

        class _WrongEndpoint(_DockerContextRunner):
            def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
                result = super().__call__(arguments, **kwargs)
                if arguments[-3:] == ["context", "inspect", "ontoprism-podman"]:
                    payload = json.loads(result.stdout)
                    payload[0]["Endpoints"]["docker"]["Host"] = (
                        "unix:///tmp/podman/decoy-api.sock"
                    )
                    result.stdout = json.dumps(payload)
                return result

        runner = _WrongEndpoint(socket_path, current="ontoprism-podman")

    with pytest.raises(
        AgentReplayInputError,
        match=r"active (Docker context|Podman endpoint)",
    ):
        run_agent_replay(["podman-verify"], tmp_path, runner=runner)
    assert ["/opt/homebrew/bin/pdm", "run", "verify"] not in [
        command for command, _options in runner.calls
    ]


@pytest.mark.unit
def test_podman_gate_operations_reject_all_user_arguments(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["podman-verify", "--skip", "tests"], tmp_path)


@pytest.mark.unit
def test_podman_gate_failure_reports_labelled_stdout_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTOPRISM_PODMAN_STACK_ENSURED", "1")
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _FailedGate(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["/opt/homebrew/bin/pdm", "run", "verify"]:
                return _Result(
                    1,
                    stdout='{"api_token":"tests-secret"}',
                    stderr='lint failed PASSWORD="lint-secret"',
                )
            return result

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-verify"],
            tmp_path,
            runner=_FailedGate(socket_path, current="ontoprism-podman"),
        )

    message = str(raised.value)
    assert (
        "required command exited nonzero (1): "
        "/opt/homebrew/bin/pdm run verify" in message
    )
    assert 'stdout: {"api_token":"[REDACTED]"}' in message
    assert 'stderr: lint failed PASSWORD="[REDACTED]"' in message
    assert "tests-secret" not in message
    assert "lint-secret" not in message


@pytest.mark.unit
def test_podman_gate_timeout_names_command_and_preserves_sanitized_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTOPRISM_PODMAN_STACK_ENSURED", "1")
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _TimedOutGate(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == ["/opt/homebrew/bin/pdm", "run", "verify"]:
                raise subprocess.TimeoutExpired(
                    arguments,
                    3600,
                    output='{"secret":"timeout-secret"}',
                    stderr="timed stderr",
                )
            return super().__call__(arguments, **kwargs)

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-verify"],
            tmp_path,
            runner=_TimedOutGate(socket_path, current="ontoprism-podman"),
        )

    message = str(raised.value)
    assert (
        "required command timed out after 3600s: "
        "/opt/homebrew/bin/pdm run verify" in message
    )
    assert 'stdout: {"secret":"[REDACTED]"}' in message
    assert "stderr: timed stderr" in message
    assert "timeout-secret" not in message


@pytest.mark.unit
def test_podman_compose_up_uses_exact_project_files_provider_and_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in ("docker-compose.yml", "docker-compose.app.yml"):
        (tmp_path / relative).touch()
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanApiRunner(socket_path)
    reserved: list[int] = []

    class _AvailableSocket:
        def bind(self, address: tuple[str, int]) -> None:
            reserved.append(address[1])

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _AvailableSocket())

    assert run_agent_replay(["podman-compose-up"], tmp_path, runner=runner) == 0

    compose = [
        "/opt/homebrew/bin/docker-compose",
        "--project-name",
        "ontoprism",
        "--file",
        str(tmp_path / "docker-compose.yml"),
    ]
    assert [call[0] for call in runner.calls[-2:]] == [
        [*compose, "config"],
        [*compose, "up", "--detach", "--wait"],
    ]
    assert reserved == [5433, 7888, 7889]
    for _command, options in runner.calls[-2:]:
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment["DOCKER_HOST"] == f"unix://{socket_path}"
        assert environment["PODMAN_COMPOSE_PROVIDER"] == (
            "/opt/homebrew/bin/docker-compose"
        )
        assert options["shell"] is False
        assert options["timeout"] == 1800


@pytest.mark.unit
def test_port_preflight_names_the_port_and_operating_system_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_compose_inputs(tmp_path)

    class _OccupiedSocket:
        def bind(self, address: tuple[str, int]) -> None:
            raise OSError(48, "Address already in use")

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _OccupiedSocket())
    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-compose-up"],
            tmp_path,
            runner=_PodmanApiRunner(tmp_path / "podman/ontoprism-vm-api.sock"),
        )

    assert "port 5433" in str(raised.value)
    assert "Address already in use" in str(raised.value)


class _ComposeCheckRunner(_PodmanApiRunner):
    def __init__(self, socket_path: Path, *, health: str = "healthy") -> None:
        super().__init__(socket_path)
        self.health = health

    def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
        if arguments[:2] == ["/opt/homebrew/bin/docker", "ps"]:
            self.calls.append((arguments, kwargs))
            return _Result(0, stdout="postgres\nqlever-ncit\nqlever-uberon\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "volume",
            "inspect",
            "ontoprism-podman-poc_ontoprism_pg_data",
        ]:
            self.calls.append((arguments, kwargs))
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-podman-poc_ontoprism_pg_data",
                            "Labels": {
                                "com.docker.compose.project": "ontoprism-podman-poc",
                                "com.docker.compose.volume": "ontoprism_pg_data",
                            },
                        }
                    ]
                ),
            )
        if arguments[:2] == ["/opt/homebrew/bin/docker", "inspect"]:
            name = arguments[2]
            service = name.removeprefix("ontoprism-")
            destination = (
                "/var/lib/postgresql/data" if service == "postgres" else "/data"
            )
            source = (
                "ontoprism-podman-poc_ontoprism_pg_data"
                if service == "postgres"
                else str(self.socket_path.parents[1] / f"data/{service}")
            )
            target_port = "5432/tcp" if service == "postgres" else "7001/tcp"
            host_port = {
                "postgres": "5433",
                "qlever-ncit": "7888",
                "qlever-uberon": "7889",
            }[service]
            labels = {
                "com.docker.compose.project": "ontoprism",
                "com.docker.compose.service": service,
            }
            self.calls.append((arguments, kwargs))
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Id": "a" * 64,
                            "Config": {"Labels": labels},
                            "State": {"Health": {"Status": self.health}},
                            "Mounts": [
                                {
                                    "Type": (
                                        "volume" if service == "postgres" else "bind"
                                    ),
                                    "Name": source if service == "postgres" else "",
                                    "Source": source,
                                    "Destination": destination,
                                }
                            ],
                            "NetworkSettings": {
                                "Ports": {
                                    target_port: [
                                        {"HostIp": "127.0.0.1", "HostPort": host_port}
                                    ]
                                }
                            },
                            "LargeRuntimeMetadata": "x" * 20_000,
                        }
                    ]
                ),
            )
        return super().__call__(arguments, **kwargs)


class _PodmanRecoveryRunner(_ComposeCheckRunner):
    def __init__(
        self,
        socket_path: Path,
        *,
        machine_state: str = "running",
        stale: bool = False,
        stack_state: str = "healthy",
        restart_succeeds: bool = True,
        machine_command_seconds: int = 0,
        stop_outlives_timeout: bool = False,
        inspections_until_stopped: int | None = None,
    ) -> None:
        super().__init__(socket_path)
        # `State` is unvalidated JSON to the script, so the fake may hold anything.
        self.machine_state: object = machine_state
        self.stale = stale
        self.stack_state = stack_state
        self.restart_succeeds = restart_succeeds
        # How long `podman machine stop/start` runs; the caller's timeout must cover it.
        self.machine_command_seconds = machine_command_seconds
        # The CLI is killed at its timeout while the guest keeps powering off; the
        # machine reports `stopped` on this many-th inspection, counting the entry
        # check (or never).
        self.stop_outlives_timeout = stop_outlives_timeout
        self.inspections_until_stopped = inspections_until_stopped
        self.before_machine_stop: Callable[[], None] | None = None
        self.before_sleep: Callable[[], None] | None = None
        self.state_after_stop_timeout: str | None = None
        self.inspect_fails_after_stop_timeout = False
        self.machine_name = "ontoprism-vm"
        self._stop_timed_out = False
        self.contexts = ("default", "ontoprism-podman")
        self.current = "ontoprism-podman"

    def __call__(  # noqa: C901, PLR0911, PLR0912 - fixed recovery CLI fake
        self, arguments: list[str], **kwargs: object
    ) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments[:1] == ["/bin/sleep"] and self.before_sleep is not None:
            self.before_sleep()
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ]:
            if self._stop_timed_out and self.inspect_fails_after_stop_timeout:
                return _Result(125, stderr="machine inspect: connection lost")
            if self.inspections_until_stopped is not None:
                self.inspections_until_stopped -= 1
                if self.inspections_until_stopped <= 0:
                    self.machine_state = "stopped"
                    self.inspections_until_stopped = None
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": self.machine_name,
                            "State": self.machine_state,
                            "Rootful": False,
                            "SSHConfig": {
                                "Port": 49969,
                                "RemoteUsername": "core",
                            },
                            "ConnectionInfo": {
                                "PodmanSocket": {"Path": str(self.socket_path)}
                            },
                        }
                    ]
                ),
            )
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "ssh",
            "ontoprism-vm",
            "true",
        ]:
            return _Result(1 if self.stale else 0, stderr="PASSWORD=hunter2")
        if arguments == [
            "/opt/homebrew/bin/podman",
            "system",
            "connection",
            "list",
            "--format",
            "json",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-vm",
                            "URI": "ssh://core@127.0.0.1:49969/run/user/501/podman/podman.sock",
                            "IsMachine": True,
                            "ReadWrite": True,
                        }
                    ]
                ),
            )
        if arguments[-3:] == ["machine", "stop", "ontoprism-vm"]:
            if self.before_machine_stop is not None:
                self.before_machine_stop()
            if self.stop_outlives_timeout:
                self._stop_timed_out = True
                if self.state_after_stop_timeout is not None:
                    self.machine_state = self.state_after_stop_timeout
                raise subprocess.TimeoutExpired(arguments, 300)
            self._run_machine_command(arguments, kwargs)
            self.machine_state = "stopped"
            return _Result(0)
        if arguments[-3:] == ["machine", "start", "ontoprism-vm"]:
            self._run_machine_command(arguments, kwargs)
            if not self.restart_succeeds:
                return _Result(1, stderr="start failed TOKEN=abc123")
            self.machine_state = "running"
            self.stale = False
            return _Result(0)
        if arguments == ["/opt/homebrew/bin/docker", "context", "show"]:
            return _Result(0, stdout=f"{self.current}\n")
        if arguments[1:4] == ["context", "ls", "--format"]:
            return _Result(0, stdout="default\nontoprism-podman\n")
        if arguments[-3:] == ["context", "inspect", "ontoprism-podman"]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-podman",
                            "Metadata": {
                                "Description": "OntoPrism rootless Podman machine"
                            },
                            "Endpoints": {
                                "docker": {
                                    "Host": f"unix://{self.socket_path}",
                                    "SkipTLSVerify": False,
                                }
                            },
                        }
                    ]
                ),
            )
        if arguments[-3:] == ["context", "use", "ontoprism-podman"]:
            self.current = "ontoprism-podman"
            return _Result(0)
        if arguments == ["/opt/homebrew/bin/docker", "version"]:
            if self.stale:
                return _Result(1, stderr="API unavailable")
            return _Result(0, stdout="Server:\n Podman Engine:\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "info",
            "--format",
            "{{json .}}",
        ]:
            if self.stale:
                return _Result(1, stderr="API unavailable")
            return _Result(
                0,
                stdout=json.dumps(
                    {
                        "OSType": "linux",
                        "ServerVersion": "6.1.0",
                        "DockerRootDir": "/home/core/.local/share/containers/storage",
                        "SecurityOptions": ["name=rootless"],
                        "ProductLicense": "Apache-2.0",
                    }
                ),
            )
        if arguments[:2] == ["/opt/homebrew/bin/docker", "ps"]:
            if self.stack_state == "absent":
                return _Result(0)
            if self.stack_state == "partial":
                return _Result(0, stdout="postgres\nqlever-ncit\n")
        if arguments[:2] == ["/opt/homebrew/bin/docker", "inspect"]:
            service = arguments[2].removeprefix("ontoprism-")
            if self.stack_state == "absent" or (
                self.stack_state == "partial" and service == "qlever-uberon"
            ):
                return _Result(1, stderr="no such object")
        if arguments[-3:] == ["up", "--detach", "--wait"]:
            self.stack_state = "healthy"
            return _Result(0)
        self.calls.pop()
        return super().__call__(arguments, **kwargs)

    def _run_machine_command(
        self, arguments: list[str], kwargs: dict[str, object]
    ) -> None:
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        if timeout < self.machine_command_seconds:
            raise subprocess.TimeoutExpired(arguments, timeout)


@pytest.mark.unit
def test_ensure_podman_stack_outlasts_a_guest_shutdown_and_boot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """On 2026-09-18 a half-dead VM (gvproxy gone, vfkit alive) needed well over
    the 20 s diagnostic timeout to power off, so the one sanctioned recovery failed."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(socket_path, stale=True, machine_command_seconds=90)

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert "machine-action=restarted-stale" in capsys.readouterr().out


@pytest.mark.unit
def test_redaction_is_linear_on_a_long_word_run() -> None:
    """Real ``docker inspect`` output carries long hashes and base64; a key prefix
    that may start anywhere rescans such a run from every position (#369; before the
    fix one 40 000-character run took 22 s, quadratic, on every command)."""
    text = "x" * 200_000 + " xPASSWORD=hunter2 ok"

    started = time.perf_counter()
    sanitized = replay._bounded_sanitized(text, limit=None)
    elapsed = time.perf_counter() - started

    assert sanitized.endswith(" xPASSWORD=[REDACTED] ok")
    assert elapsed < 2


@pytest.mark.unit
def test_a_quoted_key_after_a_word_character_is_still_redacted() -> None:
    """The match may not start inside a word run; it must still start at the quote
    that follows one, or `x"PASSWORD":"v"` leaks."""
    assert replay._bounded_sanitized('x"PASSWORD":"hunter2"') == (
        'x"PASSWORD":"[REDACTED]"'
    )
    assert replay._bounded_sanitized("x'SECRET'='hunter2'") == "x'SECRET'='[REDACTED]'"


@pytest.mark.unit
@pytest.mark.parametrize("new_session", [True, False])
def test_the_real_runner_gives_a_command_its_own_session_on_request(
    new_session: bool, tmp_path: Path
) -> None:
    """The recovery double accepts any option; the runner that reaches the real
    ``podman`` must actually detach the machine's processes from the caller."""
    result = replay._subprocess_runner(
        [sys.executable, "-c", "import os; print(os.getsid(0) == os.getpid())"],
        cwd=tmp_path,
        shell=False,
        check=False,
        timeout=30,
        capture_output=True,
        text=True,
        start_new_session=new_session,
    )

    assert result.stdout.strip() == str(new_session)


def _exited_process_id() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pid_file", "observed"),
    [
        (_exited_process_id, True),
        (os.getpid, False),
        (lambda: 0, False),
        (lambda: -1, False),
        (lambda: -_exited_process_id(), False),
        (lambda: 1, False),
        (lambda: 10**30, False),
        (lambda: "not-a-pid", False),
        (None, False),
    ],
)
def test_ensure_podman_stack_reports_a_dead_gvproxy_only_when_it_saw_one(
    pid_file: Callable[[], object] | None,
    observed: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The recurring stale state is gvproxy gone while vfkit runs on. The line states
    what was seen (a pid that names no process); a missing or unreadable pid file,
    a pid that is not a positive process id, or a live pid, is no observation and
    says nothing."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    pid_path = socket_path.parent / "gvproxy.pid"
    content = None if pid_file is None else pid_file()
    if content is not None:
        pid_path.write_text(f"{content}\n")
    runner = _PodmanRecoveryRunner(socket_path, stale=True)

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    output = capsys.readouterr().out
    expected = (
        f"stale-machine-cause=gvproxy pid {content} from {pid_path} names no process"
    )
    assert (expected in output) is observed
    assert ("stale-machine-cause=" in output) is observed
    assert "machine-action=restarted-stale" in output


@pytest.mark.unit
@pytest.mark.parametrize("gvproxy_died", [True, False])
def test_the_stale_diagnosis_is_visible_before_the_machine_stop_blocks(
    gvproxy_died: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop may block for minutes under a harness that kills the run at its own
    timeout; a diagnosis still sitting in a block buffer is lost with the process.
    The case without a dead gvproxy pins the diagnostic's own flush: there the cause
    line, whose flush would carry the diagnostic out with it, is never printed."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    if gvproxy_died:
        (socket_path.parent / "gvproxy.pid").write_text(f"{_exited_process_id()}\n")
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, write_through=False))
    written_before_stop: list[str] = []
    runner = _PodmanRecoveryRunner(socket_path, stale=True)
    runner.before_machine_stop = lambda: written_before_stop.append(
        raw.getvalue().decode()
    )

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    (written,) = written_before_stop
    assert "stale-machine-diagnostic=" in written
    assert ("stale-machine-cause=" in written) is gvproxy_died


@pytest.mark.unit
def test_a_stop_that_outlives_its_timeout_is_waited_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Killing the `machine stop` CLI does not stop the guest's shutdown (seen on
    2026-09-18); recovery waits for `stopped` instead of failing."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(
        socket_path,
        stale=True,
        stop_outlives_timeout=True,
        inspections_until_stopped=3,
    )

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    output = capsys.readouterr().out
    assert "machine-stop-outlived-timeout=300s" in output
    assert "machine-action=restarted-stale" in output


@pytest.mark.unit
def test_the_outlived_stop_is_announced_before_the_wait_begins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Up to 300 s of waiting follows; a harness that ends the run in that window
    must already have been told why the run was waiting."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, write_through=False))
    written_before_sleep: list[str] = []
    runner = _PodmanRecoveryRunner(
        socket_path,
        stale=True,
        stop_outlives_timeout=True,
        inspections_until_stopped=2,
    )
    runner.before_sleep = lambda: written_before_sleep.append(raw.getvalue().decode())

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert "machine-stop-outlived-timeout=300s" in written_before_sleep[0]


@pytest.mark.unit
@pytest.mark.parametrize("stop_outlives_timeout", [False, True])
def test_a_reported_machine_state_is_redacted_like_any_other_output(
    stop_outlives_timeout: bool, tmp_path: Path
) -> None:
    """The state is unvalidated JSON from the machine; both messages that quote it
    (the entry refusal and the stop wait giving up) must not leak what it holds."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(
        socket_path, stale=True, stop_outlives_timeout=stop_outlives_timeout
    )
    if stop_outlives_timeout:
        runner.state_after_stop_timeout = "TOKEN=abc123"
    else:
        runner.machine_state = "TOKEN=abc123"

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    assert "abc123" not in str(raised.value)
    assert "TOKEN=[REDACTED]" in str(raised.value)


@pytest.mark.unit
def test_a_machine_that_never_stops_fails_after_a_bounded_wait(tmp_path: Path) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, stale=True, stop_outlives_timeout=True)

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    commands = [command for command, _options in runner.calls]
    inspect = ["/opt/homebrew/bin/podman", "machine", "inspect", "ontoprism-vm"]
    start = ["/opt/homebrew/bin/podman", "machine", "start", "ontoprism-vm"]
    stop = ["/opt/homebrew/bin/podman", "machine", "stop", "ontoprism-vm"]
    assert "about 300s later the machine reports 'running'" in str(raised.value)
    assert "may still be shutting down" in str(raised.value)
    assert "rerun" in str(raised.value)
    assert commands[commands.index(stop) + 1] == ["/bin/sleep", "10"]
    assert commands.count(["/bin/sleep", "10"]) == 30
    assert commands.count(inspect) == 31
    assert start not in commands


@pytest.mark.unit
def test_giving_up_on_a_state_in_between_does_not_blame_a_slow_shutdown(
    tmp_path: Path,
) -> None:
    """The slow-shutdown advice is for a machine that still reports `running`; for
    any other state the rerun's own refusal says what to check."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, stale=True, stop_outlives_timeout=True)
    runner.state_after_stop_timeout = "unknown"

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    message = str(raised.value)
    assert "about 300s later the machine reports 'unknown'" in message
    assert "shutting down" not in message
    assert "says what to check" in message


@pytest.mark.unit
def test_the_wait_for_a_stop_tolerates_a_state_in_between(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A machine being polled mid-shutdown is in transition by construction; every
    other look keeps the strict running-or-stopped contract, this poll does not."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(
        socket_path,
        stale=True,
        stop_outlives_timeout=True,
        inspections_until_stopped=3,
    )
    runner.state_after_stop_timeout = "unknown"

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert "machine-action=restarted-stale" in capsys.readouterr().out


@pytest.mark.unit
def test_a_failure_while_waiting_for_the_stop_says_what_it_waited_for(
    tmp_path: Path,
) -> None:
    """The wait can end on an inspect error; alone, that error hides that the
    machine stop had already timed out and the guest may still be shutting down."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, stale=True, stop_outlives_timeout=True)
    runner.inspect_fails_after_stop_timeout = True

    with pytest.raises(AgentReplayInputError, match="machine inspect") as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    assert any(
        "machine stop ontoprism-vm" in note and "timed out after 300s" in note
        for note in raised.value.__notes__
    )


@pytest.mark.unit
def test_a_wrong_machine_is_not_reported_as_a_state_problem(tmp_path: Path) -> None:
    """Rerunning cannot fix a payload that is not our machine; the named-state
    refusal is for our machine in an unexpected state only."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, machine_state="starting")
    runner.machine_name = "someone-elses-vm"

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    assert str(raised.value) == "invalid Podman machine contract"


@pytest.mark.unit
def test_a_state_that_is_not_even_a_string_is_named_too(tmp_path: Path) -> None:
    """The inspected state is unvalidated JSON; a list must reach the refusal that
    names it, not die as an unhashable set member."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path)
    runner.machine_state = ["running"]

    with pytest.raises(AgentReplayInputError, match=r"state \['running'\]"):
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)


@pytest.mark.unit
def test_an_unexpected_machine_state_is_named(tmp_path: Path) -> None:
    """An interrupted `machine start` can leave a transitional state; the refusal
    must say which, not only that the contract is invalid."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, machine_state="starting")

    with pytest.raises(AgentReplayInputError, match="state 'starting'"):
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)


@pytest.mark.unit
@pytest.mark.parametrize("machine_state", ["stopped", "running"])
def test_ensure_podman_stack_starts_the_machine_in_its_own_session(
    machine_state: str, tmp_path: Path
) -> None:
    """vfkit and gvproxy keep the process group of whatever ran `machine start`;
    a new session keeps a harness or terminal signal away from the VM. The machine
    timeout belongs to `machine stop`/`start` and to nothing else."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(
        socket_path, machine_state=machine_state, stale=machine_state == "running"
    )

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert [
        command for command, options in runner.calls if options.get("start_new_session")
    ] == [["/opt/homebrew/bin/podman", "machine", "start", "ontoprism-vm"]]
    assert {
        tuple(command[1:3])
        for command, options in runner.calls
        if options["timeout"] == 300
    } == (
        {("machine", "stop"), ("machine", "start")}
        if machine_state == "running"
        else {("machine", "start")}
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("machine_state", "stale", "expected_action"),
    [
        ("running", False, "machine-action=no-op"),
        ("stopped", False, "machine-action=started"),
        ("running", True, "machine-action=restarted-stale"),
    ],
)
def test_ensure_podman_stack_recovers_machine_once_and_reports_action(
    machine_state: str,
    stale: bool,
    expected_action: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(
        socket_path, machine_state=machine_state, stale=stale
    )

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    commands = [command for command, _options in runner.calls]
    assert commands.count(
        ["/opt/homebrew/bin/podman", "machine", "stop", "ontoprism-vm"]
    ) == int(stale)
    assert commands.count(
        ["/opt/homebrew/bin/podman", "machine", "start", "ontoprism-vm"]
    ) == int(stale or machine_state == "stopped")
    assert not any(
        forbidden in command
        for command in commands
        for forbidden in ("reset", "rm", "init", "set", "volume")
    )
    output = capsys.readouterr().out
    assert expected_action in output
    assert "active-docker-context=ontoprism-podman" in output
    assert "stack-health=healthy" in output


@pytest.mark.unit
def test_ensure_podman_stack_failed_restart_is_bounded_and_redacted(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, stale=True, restart_succeeds=False)

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    commands = [command for command, _options in runner.calls]
    assert (
        commands.count(["/opt/homebrew/bin/podman", "machine", "stop", "ontoprism-vm"])
        == 1
    )
    assert (
        commands.count(["/opt/homebrew/bin/podman", "machine", "start", "ontoprism-vm"])
        == 1
    )
    assert len(commands) < 10
    assert "abc123" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize("stack_state", ["absent", "partial"])
def test_ensure_podman_stack_starts_or_reconciles_owned_stack(
    stack_state: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(socket_path, stack_state=stack_state)

    class _AvailableSocket:
        def bind(self, _address: tuple[str, int]) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _AvailableSocket())

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert any(
        command[-3:] == ["up", "--detach", "--wait"] for command, _ in runner.calls
    )
    assert "stack-action=started-or-reconciled" in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("owner", "project owner predicate failed"),
        ("port", "port binding predicate failed"),
        ("volume", "mount source failed"),
    ],
)
def test_ensure_podman_stack_refuses_uncertain_existing_resources(
    failure: str, message: str, tmp_path: Path
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()

    class _Uncertain(_PodmanRecoveryRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == [
                "/opt/homebrew/bin/docker",
                "inspect",
                "ontoprism-postgres",
            ]:
                payload = json.loads(result.stdout)
                if failure == "owner":
                    payload[0]["Config"]["Labels"]["com.docker.compose.project"] = (
                        "decoy"
                    )
                elif failure == "port":
                    payload[0]["NetworkSettings"]["Ports"]["5432/tcp"] = [
                        {"HostIp": "0.0.0.0", "HostPort": "5433"}  # noqa: S104
                    ]
                else:
                    payload[0]["Mounts"][0]["Name"] = "decoy-volume"
                result.stdout = json.dumps(payload)
            return result

    runner = _Uncertain(socket_path, stack_state="partial")
    with pytest.raises(AgentReplayInputError, match=message):
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)
    assert not any(
        command[-3:] == ["up", "--detach", "--wait"] for command, _ in runner.calls
    )


@pytest.mark.unit
def test_ensure_podman_stack_rejects_arguments(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["ensure-podman-stack", "unsafe"], tmp_path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("service", "source"),
    [
        ("postgres", "ontoprism-podman-poc_ontoprism_pg_data-backup"),
        ("qlever-ncit", "decoy/qlever-ncit"),
        ("qlever-uberon", "data/not-qlever-uberon"),
    ],
)
def test_podman_compose_check_rejects_mount_source_decoys(
    service: str, source: str, tmp_path: Path
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    decoy_source = source if service == "postgres" else str(tmp_path / source)

    class _MountDecoyRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == [
                "/opt/homebrew/bin/docker",
                "inspect",
                f"ontoprism-{service}",
            ]:
                payload = json.loads(result.stdout)
                payload[0]["Mounts"][0]["Source"] = decoy_source
                payload[0]["Mounts"][0]["Name"] = decoy_source
                result.stdout = json.dumps(payload)
            return result

    with pytest.raises(AgentReplayInputError, match=f"{service} mount source"):
        run_agent_replay(
            ["podman-compose-check"],
            tmp_path,
            runner=_MountDecoyRunner(socket_path),
        )


@pytest.mark.unit
def test_podman_compose_check_validates_health_labels_mounts_ports_and_dns(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _ComposeCheckRunner(socket_path)

    assert run_agent_replay(["podman-compose-check"], tmp_path, runner=runner) == 0

    assert [call[0] for call in runner.calls[-5:]] == [
        [
            "/opt/homebrew/bin/docker",
            "ps",
            "--all",
            "--filter",
            "label=com.docker.compose.project=ontoprism",
            "--format",
            '{{.Label "com.docker.compose.service"}}',
        ],
        ["/opt/homebrew/bin/docker", "inspect", "ontoprism-postgres"],
        ["/opt/homebrew/bin/docker", "inspect", "ontoprism-qlever-ncit"],
        ["/opt/homebrew/bin/docker", "inspect", "ontoprism-qlever-uberon"],
        [
            "/opt/homebrew/bin/docker",
            "exec",
            "ontoprism-postgres",
            "getent",
            "hosts",
            "qlever-ncit",
            "qlever-uberon",
        ],
    ]


@pytest.mark.unit
def test_podman_compose_check_rejects_broken_health(tmp_path: Path) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    with pytest.raises(AgentReplayInputError, match="postgres health predicate"):
        run_agent_replay(
            ["podman-compose-check"],
            tmp_path,
            runner=_ComposeCheckRunner(socket_path, health="unhealthy"),
        )


@pytest.mark.unit
def test_podman_compose_check_rejects_non_list_mounts_with_named_predicate(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _MalformedMountsRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == [
                "/opt/homebrew/bin/docker",
                "inspect",
                "ontoprism-postgres",
            ]:
                payload = json.loads(result.stdout)
                payload[0]["Mounts"] = "not-a-list"
                result.stdout = json.dumps(payload)
            return result

    with pytest.raises(
        AgentReplayInputError, match="postgres mounts shape predicate failed"
    ):
        run_agent_replay(
            ["podman-compose-check"],
            tmp_path,
            runner=_MalformedMountsRunner(socket_path),
        )


@pytest.mark.unit
def test_podman_compose_check_rejects_extra_project_service(tmp_path: Path) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _ExtraServiceRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[:2] == ["/opt/homebrew/bin/docker", "ps"]:
                result.stdout += "decoy\n"
            return result

    with pytest.raises(AgentReplayInputError, match="service inventory predicate"):
        run_agent_replay(
            ["podman-compose-check"],
            tmp_path,
            runner=_ExtraServiceRunner(socket_path),
        )


@pytest.mark.unit
def test_podman_compose_down_checks_exact_ownership_before_scoped_cleanup(
    tmp_path: Path,
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _ComposeCheckRunner(socket_path)

    assert run_agent_replay(["podman-compose-down"], tmp_path, runner=runner) == 0

    assert runner.calls[-2][0] == [
        "/opt/homebrew/bin/docker-compose",
        "--project-name",
        "ontoprism",
        "--file",
        str(tmp_path / "docker-compose.yml"),
        "down",
    ]


@pytest.mark.unit
def test_podman_compose_down_preserves_a_decoy_with_wrong_owner_label(
    tmp_path: Path,
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _DecoyRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[:2] == ["/opt/homebrew/bin/docker", "inspect"]:
                payload = json.loads(result.stdout)
                payload[0]["Config"]["Labels"]["com.docker.compose.project"] = "decoy"
                result.stdout = json.dumps(payload)
            return result

    runner = _DecoyRunner(socket_path)
    with pytest.raises(AgentReplayInputError, match="cleanup ownership"):
        run_agent_replay(["podman-compose-down"], tmp_path, runner=runner)
    assert all(call[0][-1] != "down" for call in runner.calls)


@pytest.mark.unit
def test_podman_compose_down_accepts_partial_owned_stack_and_verifies_volume(
    tmp_path: Path,
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _PartialRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == [
                "/opt/homebrew/bin/docker",
                "inspect",
                "ontoprism-qlever-ncit",
            ]:
                self.calls.append((arguments, kwargs))
                return _Result(1, stderr="error: no such object: ontoprism-qlever-ncit")
            if arguments[:3] == ["/opt/homebrew/bin/docker", "volume", "inspect"]:
                self.calls.append((arguments, kwargs))
                return _Result(
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "Name": "ontoprism-podman-poc_ontoprism_pg_data",
                                "Labels": {
                                    "com.docker.compose.project": (
                                        "ontoprism-podman-poc"
                                    ),
                                    "com.docker.compose.volume": "ontoprism_pg_data",
                                },
                            }
                        ]
                    ),
                )
            return super().__call__(arguments, **kwargs)

    runner = _PartialRunner(socket_path)
    assert run_agent_replay(["podman-compose-down"], tmp_path, runner=runner) == 0
    assert runner.calls[-1][0] == [
        "/opt/homebrew/bin/docker",
        "volume",
        "inspect",
        "ontoprism-podman-poc_ontoprism_pg_data",
    ]


@pytest.mark.unit
def test_podman_compose_up_preserves_primary_failure_when_rollback_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _AvailableSocket:
        def bind(self, _address: tuple[str, int]) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _AvailableSocket())

    class _FailedRollback(_PodmanApiRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[-3:] == ["up", "--detach", "--wait"]:
                return _Result(1, stderr="primary up failure")
            if arguments[-1:] == ["down"]:
                return _Result(1, stderr="rollback down failure")
            return result

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-compose-up"], tmp_path, runner=_FailedRollback(socket_path)
        )

    assert "primary up failure" in str(raised.value)
    assert any("rollback down failure" in note for note in raised.value.__notes__)


@pytest.mark.unit
def test_main_prints_cleanup_notes_to_cli_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failure = AgentReplayInputError("primary operation failed")
    failure.add_note("cleanup failure: generated override could not be removed")

    def fail_replay(_arguments: list[str], _root: Path) -> int:
        raise failure

    monkeypatch.setattr(replay, "run_agent_replay", fail_replay)
    monkeypatch.setattr(replay.sys, "argv", ["run_agent_replay.py", "podman-app-smoke"])

    assert replay.main() == 2
    assert capsys.readouterr().err == (
        "primary operation failed\n"
        "cleanup failure: generated override could not be removed\n"
    )


@pytest.mark.unit
def test_structural_inspect_redaction_covers_env_keys_and_asyncpg_urls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _SecretInspect(_PodmanApiRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == [
                "/opt/homebrew/bin/docker",
                "info",
                "--format",
                "{{json .}}",
            ]:
                return _Result(
                    0,
                    stdout=json.dumps(
                        {
                            "Config": {
                                "Env": [
                                    "POSTGRES_PASSWORD=hunter2",
                                    "DATABASE_URL=postgresql+asyncpg://user:swordfish@db/app",
                                ]
                            }
                        }
                    ),
                )
            return super().__call__(arguments, **kwargs)

    assert (
        run_agent_replay(
            ["check-podman-api"], tmp_path, runner=_SecretInspect(socket_path)
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "hunter2" not in output
    assert "swordfish" not in output
    assert output.count("[REDACTED]") >= 2


@pytest.mark.unit
def test_health_rejection_matches_raw_combined_streams_and_always_removes_paths(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _SplitHealthFailure(_PodmanApiRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[-5:] == ["up", "--detach", "--wait", "--wait-timeout", "30"]:
                return _Result(
                    1,
                    stdout="ontoprism-podman-health-reject-broken-1",
                    stderr="container is unhealthy",
                )
            if arguments[-1:] == ["down"]:
                return _Result(1, stderr="cleanup failed")
            return result

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-health-reject"], tmp_path, runner=_SplitHealthFailure(socket_path)
        )

    assert "cleanup failed" in str(raised.value)
    assert not (tmp_path / "tmp/podman-poc/broken-health.override.yml").exists()
    assert not (tmp_path / "tmp/podman-poc/broken-health-postgres").exists()


@pytest.mark.unit
def test_health_rejection_removes_paths_when_override_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    original_write_text = Path.write_text

    def failed_override_write(
        path: Path, data: str, *, encoding: str | None = None, errors: str | None = None
    ) -> int:
        if path.name == "broken-health.override.yml":
            raise OSError("injected override write failure")
        return original_write_text(path, data, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "write_text", failed_override_write)
    with pytest.raises(OSError, match="injected override write failure"):
        run_agent_replay(
            ["podman-health-reject"], tmp_path, runner=_PodmanApiRunner(socket_path)
        )

    assert not (tmp_path / "tmp/podman-poc/broken-health.override.yml").exists()
    assert not (tmp_path / "tmp/podman-poc/broken-health-postgres").exists()


@pytest.mark.unit
def test_app_smoke_preflights_8080_and_owned_primary_volume_before_writing_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_compose_inputs(tmp_path, app=True)

    class _OccupiedSocket:
        def bind(self, address: tuple[str, int]) -> None:
            if address[1] == 8080:
                raise OSError(48, "Address already in use")

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _OccupiedSocket())
    with pytest.raises(AgentReplayInputError, match="port 8080"):
        run_agent_replay(["podman-app-smoke"], tmp_path, runner=_Runner())

    assert not (tmp_path / "tmp/podman-poc/app-podman.override.yml").exists()


@pytest.mark.unit
def test_poc_acceptance_operations_are_fixed_and_reject_arguments(
    tmp_path: Path,
) -> None:
    for operation in ("podman-health-reject", "podman-app-smoke"):
        with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
            run_agent_replay([operation, "unsafe"], tmp_path, runner=_Runner())
