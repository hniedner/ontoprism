"""Policy tests for the narrowly scoped current-replay agent wrapper."""

from __future__ import annotations

import json
import os
import socket
import subprocess
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


class _PodmanDiagnosticRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
        self.calls.append((arguments, kwargs))
        return _Result(0, stdout="podman diagnostic evidence")


class _PodmanApiRunner:
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
        contexts: tuple[str, ...] = ("colima",),
        current: str = "colima",
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
def test_r101_current_validation_uses_only_existing_sme_artifacts(
    tmp_path: Path,
) -> None:
    for relative in (
        "scripts/adjudication.py",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        "tmp/r101-review-packet-v3.json",
        "tmp/r101-review-registry-v3-SME.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = _Runner()

    assert run_agent_replay(["validate-r101-current"], tmp_path, runner=runner) == 0

    command, options = runner.calls[0]
    assert command[1:] == [
        str(tmp_path / "scripts/adjudication.py"),
        "dry-run-r101-decision-expansion",
        "--report",
        str(
            tmp_path
            / "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz"
        ),
        "--packet",
        str(tmp_path / "tmp/r101-review-packet-v3.json"),
        "--registry",
        str(tmp_path / "tmp/r101-review-registry-v3-SME.json"),
        "--output",
        str(tmp_path / "tmp/r101-review-current-validation.json"),
    ]
    assert options["shell"] is False


@pytest.mark.unit
def test_r101_packet_regeneration_uses_current_report_and_source(
    tmp_path: Path,
) -> None:
    for relative in (
        "scripts/adjudication.py",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = _Runner()

    assert (
        run_agent_replay(["regenerate-r101-current-packet"], tmp_path, runner=runner)
        == 0
    )

    command, _options = runner.calls[0]
    assert command[1:] == [
        str(tmp_path / "scripts/adjudication.py"),
        "prepare-r101-review-packet",
        "--report",
        str(
            tmp_path
            / "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz"
        ),
        "--source-manifest",
        str(tmp_path / "data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        "--endpoint",
        "http://localhost:7888",
        "--output-packet",
        str(tmp_path / "tmp/r101-review-packet-current.json"),
        "--output-xlsx",
        str(tmp_path / "tmp/r101-review-workbook-current.xlsx"),
    ]


@pytest.mark.unit
def test_r101_reuse_report_uses_both_packets_and_existing_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in (
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        "tmp/r101-review-packet-v3.json",
        "tmp/r101-review-packet-current.json",
        "tmp/r101-review-registry-v3-SME.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    calls: list[dict[str, Path]] = []
    module = __import__(
        "scripts.research.pre_sme_readiness",
        fromlist=["generate_r101_reuse_validation"],
    )
    monkeypatch.setattr(
        module, "generate_r101_reuse_validation", lambda **values: calls.append(values)
    )

    assert run_agent_replay(["report-r101-current-reuse"], tmp_path) == 0

    assert calls == [
        {
            "report": tmp_path
            / (
                "ontolib/tests/decomposition/golden/"
                "neoplasm-r101-v4-conservation.json.gz"
            ),
            "existing_packet": tmp_path / "tmp/r101-review-packet-v3.json",
            "current_packet": tmp_path / "tmp/r101-review-packet-current.json",
            "registry": tmp_path / "tmp/r101-review-registry-v3-SME.json",
            "output": tmp_path / "tmp/r101-review-current-validation.json",
        }
    ]


@pytest.mark.unit
def test_pre_sme_artifact_operations_use_only_fixed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = (
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
        "tmp/m1-6-current-full-corpus.ttl",
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        "tmp/r101-review-current-validation.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "tmp/m1-6-primary-site-audit.json",
        "tmp/m1-6-group-review-packet.json",
        "tmp/m1-6-r103-review-packet.json",
        "tmp/m1-6-verify-evidence.json",
    )
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    calls: list[dict[str, Path]] = []
    module = __import__(
        "scripts.research.pre_sme_readiness", fromlist=["generate_primary_site_audit"]
    )
    monkeypatch.setattr(
        module, "generate_primary_site_audit", lambda **values: calls.append(values)
    )
    monkeypatch.setattr(
        module, "generate_pre_sme_readiness", lambda **values: calls.append(values)
    )

    class Runner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["git", "rev-parse", "HEAD"]:
                result.stdout = "a" * 40 + "\n"
                result.stderr = ""
            return result

    runner = Runner()
    assert run_agent_replay(["audit-primary-sites"], tmp_path, runner=runner) == 0
    assert (
        run_agent_replay(["generate-pre-sme-readiness"], tmp_path, runner=runner) == 0
    )

    assert calls[0] == {
        "source_manifest": tmp_path / "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "baseline": tmp_path
        / "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
        "artifact": tmp_path / "tmp/m1-6-current-full-corpus.ttl",
        "output": tmp_path / "tmp/m1-6-primary-site-audit.json",
    }
    assert calls[1]["current_evidence"] == (
        tmp_path
        / "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json"
    )
    assert calls[1]["r101_validation"] == (
        tmp_path / "tmp/r101-review-current-validation.json"
    )
    assert calls[1]["output"] == tmp_path / "tmp/m1-6-machine-readiness.json"
    assert calls[1]["expected_git_head"] == "a" * 40


@pytest.mark.unit
def test_pre_sme_verify_evidence_is_written_only_after_exact_podman_gate(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()

    class Runner(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == ["git", "rev-parse", "HEAD"]:
                self.calls.append((arguments, kwargs))
                return _Result(0, stdout="a" * 40 + "\n")
            return super().__call__(arguments, **kwargs)

    runner = Runner(
        socket_path,
        contexts=("ontoprism-podman",),
        current="ontoprism-podman",
    )
    (tmp_path / "tmp").mkdir()

    assert run_agent_replay(["capture-pre-sme-verify"], tmp_path, runner=runner) == 0

    commands = [command for command, _options in runner.calls]
    assert ["/opt/homebrew/bin/pdm", "run", "verify"] in commands
    assert commands[-1] == ["git", "rev-parse", "HEAD"]
    evidence = json.loads((tmp_path / "tmp/m1-6-verify-evidence.json").read_text())
    assert evidence["command"] == "pdm run verify"
    assert evidence["status"] == "passed"
    assert evidence["git_head"] == "a" * 40
    assert evidence["writes_performed"] is False


@pytest.mark.unit
def test_wrapper_rejects_unlisted_operations(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="unsupported"):
        run_agent_replay(["import-workbook"], tmp_path)


@pytest.mark.unit
def test_inspect_podman_runs_only_fixed_bounded_read_only_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _PodmanDiagnosticRunner()

    assert run_agent_replay(["inspect-podman"], tmp_path, runner=runner) == 0

    assert [command for command, _options in runner.calls] == [
        ["/opt/homebrew/bin/podman", "version", "--format", "json"],
        ["/opt/homebrew/bin/podman", "info", "--format", "json"],
        ["/opt/homebrew/bin/podman", "machine", "list", "--format", "json"],
        [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ],
        [
            "/opt/homebrew/bin/podman",
            "system",
            "connection",
            "list",
            "--format",
            "json",
        ],
        ["/usr/bin/which", "docker"],
        ["/usr/bin/which", "docker-compose"],
        ["/opt/homebrew/bin/docker", "version"],
        ["/opt/homebrew/bin/docker-compose", "version"],
        ["/opt/homebrew/bin/podman", "compose", "version"],
    ]
    assert all(options["cwd"] == tmp_path for _command, options in runner.calls)
    assert all(options["shell"] is False for _command, options in runner.calls)
    assert all(options["timeout"] == 20 for _command, options in runner.calls)
    assert all(options["capture_output"] is True for _command, options in runner.calls)
    assert all(options["text"] is True for _command, options in runner.calls)
    assert "podman diagnostic evidence" in capsys.readouterr().out


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
    assert "prior-docker-context=colima" in output
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
        contexts=("colima", "ontoprism-podman"),
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
        contexts=("colima", "ontoprism-podman"),
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
    monkeypatch.setattr(os, "environ", {"SAFE_SETTING": "retained", "PATH": "unsafe"})

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
    }


@pytest.mark.unit
def test_podman_verify_requires_selected_exact_context_and_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(socket_path)
    runner.current = "ontoprism-podman"
    monkeypatch.setattr(os, "environ", {"SAFE_SETTING": "retained", "PATH": "safe"})

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
    assert gate_environment == {"SAFE_SETTING": "retained", "PATH": "safe"}


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["wrong-context", "wrong-endpoint"])
def test_podman_verify_refuses_non_podman_selected_context(
    failure: str,
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(socket_path)
    runner.current = "colima" if failure == "wrong-context" else "ontoprism-podman"

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
    tmp_path: Path,
) -> None:
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
    tmp_path: Path,
) -> None:
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
        "ontoprism-podman-poc",
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
        run_agent_replay(["podman-compose-up"], tmp_path, runner=_Runner())

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
                "com.docker.compose.project": "ontoprism-podman-poc",
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
            "label=com.docker.compose.project=ontoprism-podman-poc",
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
        "ontoprism-podman-poc",
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
def test_colima_config_is_resolved_from_the_current_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".colima/default/colima.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("cpu: 7\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    run_agent_replay(["diagnose-stack"], tmp_path, runner=_DiagnosticRunner())

    assert "cpu: 7" in capsys.readouterr().out


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


@pytest.mark.unit
def test_diagnose_stack_reports_only_allowlisted_colima_config_from_exact_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_path = Path("/Users/hannes/.colima/default/colima.yaml")
    read_paths: list[Path] = []

    def read_text(path: Path, *, encoding: str | None = None) -> str:
        read_paths.append(path)
        assert encoding == "utf-8"
        return """
portForwarder: ssh
vmType: vz
mountType: virtiofs
mountInotify: true
cpu: 8
memory: 16
disk: 100
runtime: docker
password: never-print-this
docker:
  registry-token: also-never-print-this
"""

    monkeypatch.setattr(Path, "read_text", read_text)

    assert (
        run_agent_replay(["diagnose-stack"], tmp_path, runner=_DiagnosticRunner()) == 0
    )

    assert read_paths == [expected_path]
    output = capsys.readouterr().out
    assert (
        """=== Colima config (allowlisted fields) ===
portForwarder: ssh
vmType: vz
mountType: virtiofs
mountInotify: true
cpu: 8
memory: 16
disk: 100
runtime: docker
"""
        in output
    )
    config_output = output.partition("=== colima status --json ===")[0]
    assert "password" not in config_output.lower()
    assert "never-print-this" not in config_output
    assert "registry-token" not in config_output
    assert "also-never-print-this" not in config_output


@pytest.mark.unit
@pytest.mark.parametrize(
    "config",
    [
        "runtime: docker\nruntime: containerd\n",
        "runtime: docker\ndocker:\n  token: first\n  token: second\n",
    ],
)
def test_diagnose_stack_rejects_duplicate_yaml_keys_without_leaking_values(
    config: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "read_text", lambda _path, *, encoding=None: config)

    assert (
        run_agent_replay(["diagnose-stack"], tmp_path, runner=_DiagnosticRunner()) == 0
    )

    output = capsys.readouterr().out
    assert "colima-config-error: invalid or duplicate-key YAML" in output
    assert "docker\nruntime" not in output
    assert "containerd" not in output
    assert "first" not in output
    assert "second" not in output


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["malformed", "unreadable"])
def test_diagnose_stack_sanitizes_malformed_or_unreadable_config_errors(
    failure: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def read_text(_path: Path, *, encoding: str | None = None) -> str:
        if failure == "unreadable":
            raise PermissionError("password=hunter2")
        return "runtime: [token=secret-value"

    monkeypatch.setattr(Path, "read_text", read_text)

    assert (
        run_agent_replay(["diagnose-stack"], tmp_path, runner=_DiagnosticRunner()) == 0
    )

    output = capsys.readouterr().out
    expected = (
        "colima-config-error: unable to read authorized config"
        if failure == "unreadable"
        else "colima-config-error: invalid or duplicate-key YAML"
    )
    assert expected in output
    assert "hunter2" not in output
    assert "secret-value" not in output


@pytest.mark.unit
def test_diagnose_stack_rejects_non_scalar_allowlisted_config_without_leaking(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _path, *, encoding=None: "runtime:\n  token: secret-value\n",
    )

    assert (
        run_agent_replay(["diagnose-stack"], tmp_path, runner=_DiagnosticRunner()) == 0
    )

    output = capsys.readouterr().out
    assert "colima-config-error: allowlisted fields have invalid types" in output
    assert "token" not in output.lower()
    assert "secret-value" not in output


@pytest.mark.unit
def test_diagnose_stack_rejects_arbitrary_text_in_allowlisted_config_fields(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _path, *, encoding=None: 'runtime: "password=hunter2"\n',
    )

    assert (
        run_agent_replay(["diagnose-stack"], tmp_path, runner=_DiagnosticRunner()) == 0
    )

    output = capsys.readouterr().out
    assert "colima-config-error: allowlisted fields have invalid values" in output
    assert "hunter2" not in output
