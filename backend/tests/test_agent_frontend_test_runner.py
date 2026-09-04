from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from scripts.validation.run_agent_frontend_test import (
    AgentFrontendTestInputError,
    FrontendTestInvocation,
    _subprocess_runner,
    build_frontend_test_invocation,
    run_agent_frontend_test,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def frontend_root(tmp_path: Path) -> Path:
    for relative in (
        "frontend/src/lib/alpha.test.ts",
        "frontend/src/lib/beta.spec.js",
        "frontend/src/lib/not-a-test.ts",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import { test } from 'vitest'; test('safe', () => {});\n")
    return tmp_path


@pytest.fixture
def tracked_tests() -> frozenset[str]:
    return frozenset(
        {
            "frontend/src/lib/alpha.test.ts",
            "frontend/src/lib/beta.spec.js",
            "frontend/src/lib/not-a-test.ts",
        }
    )


def test_frontend_runner_builds_only_the_fixed_npm_invocation(
    frontend_root: Path, tracked_tests: frozenset[str]
) -> None:
    invocation = build_frontend_test_invocation(
        ["frontend/src/lib/alpha.test.ts", "frontend/src/lib/beta.spec.js"],
        frontend_root,
        tracked_paths=tracked_tests,
    )

    assert invocation.arguments == (
        "npm",
        "--prefix",
        "frontend",
        "run",
        "test:unit",
        "--",
        "--run",
        "--reporter=json",
        "src/lib/alpha.test.ts",
        "src/lib/beta.spec.js",
    )
    assert invocation.cwd == frontend_root.resolve()


def test_frontend_invocation_cannot_be_constructed_with_arbitrary_argv() -> None:
    with pytest.raises(TypeError):
        FrontendTestInvocation(("npm", "publish"), Path("."))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["frontend/src/lib/not-a-test.ts"],
        ["frontend/src/lib/missing.test.ts"],
        ["frontend/src/lib/alpha.test.ts", "--config=other.ts"],
        ["frontend/src/lib/alpha.test.ts", "--reporter=json"],
        ["frontend/src/lib/alpha.test.ts", "--update"],
        ["frontend/src/lib/alpha.test.ts", "&&", "npm", "publish"],
        ["frontend/src/lib/alpha.test.ts; npm publish"],
        ["frontend/src/lib/alpha.test.ts\nnpm publish"],
        ["frontend/src/lib/../alpha.test.ts"],
        ["../frontend/src/lib/alpha.test.ts"],
        ["/frontend/src/lib/alpha.test.ts"],
        ["-frontend/src/lib/alpha.test.ts"],
        ["frontend/src/lib"],
    ],
)
def test_frontend_runner_rejects_non_file_and_adaptive_requests(
    frontend_root: Path,
    tracked_tests: frozenset[str],
    arguments: list[str],
) -> None:
    with pytest.raises(AgentFrontendTestInputError):
        build_frontend_test_invocation(
            arguments, frontend_root, tracked_paths=tracked_tests
        )


def test_frontend_runner_rejects_untracked_file(
    frontend_root: Path, tracked_tests: frozenset[str]
) -> None:
    untracked = frontend_root / "frontend/src/lib/untracked.test.ts"
    untracked.write_text("test('unsafe', () => {});\n")

    with pytest.raises(AgentFrontendTestInputError, match="tracked"):
        build_frontend_test_invocation(
            ["frontend/src/lib/untracked.test.ts"],
            frontend_root,
            tracked_paths=tracked_tests,
        )


def test_frontend_runner_rejects_symlink_file(
    frontend_root: Path, tracked_tests: frozenset[str]
) -> None:
    target = frontend_root / "frontend/src/lib/alpha.test.ts"
    link = frontend_root / "frontend/src/lib/link.test.ts"
    link.symlink_to(target)

    with pytest.raises(AgentFrontendTestInputError, match="symlink"):
        build_frontend_test_invocation(
            ["frontend/src/lib/link.test.ts"],
            frontend_root,
            tracked_paths=tracked_tests | {"frontend/src/lib/link.test.ts"},
        )


class _Result:
    returncode = 7
    stdout = b'{"numPassedTests":0,"testResults":[]}'
    stderr = b"safe stderr\n"


def test_frontend_runner_uses_no_shell_timeout_and_preserves_status(
    frontend_root: Path,
    tracked_tests: frozenset[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}
    observed_env: dict[str, str] = {}
    monkeypatch.setenv("NODE_OPTIONS", "--require=malicious.js")
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", str(tmp_path / "malicious-npmrc"))

    def runner(
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        shell: Literal[False],
        check: Literal[False],
        capture_output: Literal[True],
        timeout: int,
        env: dict[str, str],
        start_new_session: Literal[True],
    ) -> _Result:
        observed_env.update(env)
        observed.update(
            arguments=arguments,
            cwd=cwd,
            shell=shell,
            check=check,
            capture_output=capture_output,
            timeout=timeout,
            start_new_session=start_new_session,
        )
        return _Result()

    status = run_agent_frontend_test(
        ["frontend/src/lib/alpha.test.ts"],
        frontend_root,
        tracked_paths=tracked_tests,
        runner=runner,
    )

    assert status == 7
    assert observed == {
        "arguments": (
            "npm",
            "--prefix",
            "frontend",
            "run",
            "test:unit",
            "--",
            "--run",
            "--reporter=json",
            "src/lib/alpha.test.ts",
        ),
        "cwd": frontend_root.resolve(),
        "shell": False,
        "check": False,
        "capture_output": True,
        "timeout": 300,
        "start_new_session": True,
    }
    assert set(observed_env) == {
        "PATH",
        "HOME",
        "NPM_CONFIG_CACHE",
        "NPM_CONFIG_USERCONFIG",
    }
    assert Path(observed_env["HOME"]).name.startswith("ontoprism-agent-frontend-")
    assert observed_env["NPM_CONFIG_CACHE"] == observed_env["HOME"]
    assert observed_env["NPM_CONFIG_USERCONFIG"] == "/dev/null"
    assert "NODE_OPTIONS" not in observed_env
    captured = capsys.readouterr()
    assert '"numPassedTests":0' in captured.out
    assert captured.err == "safe stderr\n"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (subprocess.TimeoutExpired(("npm",), 300), "timed out"),
        (OSError("secret path and token"), "could not start"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), "invalid output"),
    ],
)
def test_frontend_runner_failures_are_fixed_and_sanitized(
    frontend_root: Path,
    tracked_tests: frozenset[str],
    capsys: pytest.CaptureFixture[str],
    failure: BaseException,
    expected: str,
) -> None:
    def runner(*args: object, **kwargs: object) -> _Result:
        raise failure

    assert (
        run_agent_frontend_test(
            ["frontend/src/lib/alpha.test.ts"],
            frontend_root,
            tracked_paths=tracked_tests,
            runner=runner,
        )
        == 2
    )
    captured = capsys.readouterr()
    assert expected in captured.err
    assert "secret" not in captured.err


class _PassingResult:
    returncode = 0
    stdout = (
        b"> frontend test:unit\n> vitest --run --reporter=json\n"
        b'{"numPassedTests":1,"numFailedTests":0,"testResults":'
        b'[{"name":"/repo/frontend/src/lib/alpha.test.ts",'
        b'"assertionResults":[{"status":"passed"}]}]}'
    )
    stderr = b""


def test_frontend_runner_requires_nonzero_execution_for_every_requested_file(
    frontend_root: Path,
    tracked_tests: frozenset[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _ZeroResult:
        returncode = 0
        stdout = b'{"numPassedTests":0,"numFailedTests":0,"testResults":[]}'
        stderr = b""

    def runner(*args: object, **kwargs: object) -> _ZeroResult:
        return _ZeroResult()

    assert (
        run_agent_frontend_test(
            ["frontend/src/lib/alpha.test.ts"],
            frontend_root,
            tracked_paths=tracked_tests,
            runner=runner,
        )
        == 2
    )
    assert "did not execute every requested test file" in capsys.readouterr().err


def test_frontend_runner_accepts_report_with_executed_requested_file(
    frontend_root: Path, tracked_tests: frozenset[str]
) -> None:
    def runner(*args: object, **kwargs: object) -> _PassingResult:
        return _PassingResult()

    assert (
        run_agent_frontend_test(
            ["frontend/src/lib/alpha.test.ts"],
            frontend_root,
            tracked_paths=tracked_tests,
            runner=runner,
        )
        == 0
    )


def test_subprocess_timeout_kills_the_new_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[tuple[int, signal.Signals]] = []

    class Process:
        pid = 4321
        returncode = -9
        calls = 0

        def communicate(self, *, timeout: int | None = None) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(("npm",), timeout or 0)
            return b"", b""

    process = Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(subprocess.TimeoutExpired):
        _subprocess_runner(
            ("npm",),
            cwd=Path("."),
            shell=False,
            check=False,
            capture_output=True,
            timeout=1,
            env={},
            start_new_session=True,
        )

    assert killed == [(4321, signal.SIGKILL)]
    assert process.calls == 2
