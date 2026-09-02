from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts.validation.run_agent_test import (
    AgentTestInputError,
    build_pytest_invocation,
    parse_vitest_execution_count,
    run_agent_test,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def owned_test_root(tmp_path: Path) -> Path:
    for relative in ("backend/tests/test_safe.py", "ontolib/tests/test_safe.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_safe():\n    assert True\n")
    return tmp_path


@pytest.fixture
def complete_test_root(owned_test_root: Path) -> Path:
    frontend_test = owned_test_root / "frontend/src/lib/api.test.ts"
    frontend_test.parent.mkdir(parents=True)
    frontend_test.write_text("import { test } from 'vitest'; test('safe', () => {});\n")
    vitest = owned_test_root / "frontend/node_modules/.bin/vitest"
    vitest.parent.mkdir(parents=True)
    vitest.write_text("#!/bin/sh\n")
    vitest.chmod(0o755)
    manifest = owned_test_root / "test_support/integration_mutators.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '[[mutator]]\npath = "backend/tests/test_safe.py"\n'
        'tests = ["test_safe"]\nfixtures = ["isolated_postgres_settings"]\n'
    )
    return owned_test_root


@pytest.mark.parametrize(
    "arguments",
    [
        ["backend/tests/test_safe.py", "-c=other.ini"],
        ["backend/tests/test_safe.py", "-cmalicious.ini"],
        ["backend/tests/test_safe.py", "-p=malicious"],
        ["backend/tests/test_safe.py", "-pmalicious"],
        ["backend/tests/test_safe.py", "--rootdir=other"],
        ["backend/tests/test_safe.py", "--override-ini=addopts=-pbad"],
        ["backend/tests/test_safe.py", "--import-mode=append"],
        ["backend/tests/test_safe.py", "--unknown"],
        ["backend/tests/test_safe.py", "&&", "gh", "pr", "merge"],
        ["backend/tests/../outside.py"],
    ],
)
def test_agent_test_rejects_unsafe_arguments(
    owned_test_root: Path, arguments: list[str]
) -> None:
    with pytest.raises(AgentTestInputError):
        build_pytest_invocation(arguments, owned_test_root)


def test_agent_test_rejects_absolute_outside_path(owned_test_root: Path) -> None:
    outside = owned_test_root.parent / "outside.py"
    outside.write_text("def test_outside(): pass\n")

    with pytest.raises(AgentTestInputError):
        build_pytest_invocation([str(outside)], owned_test_root)


def test_agent_test_rejects_symlink_escape(owned_test_root: Path) -> None:
    outside = owned_test_root.parent / "outside.py"
    outside.write_text("def test_outside(): pass\n")
    (owned_test_root / "backend/tests/test_link.py").symlink_to(outside)

    with pytest.raises(AgentTestInputError):
        build_pytest_invocation(["backend/tests/test_link.py"], owned_test_root)


def test_agent_test_accepts_owned_nodes_and_safe_flags(owned_test_root: Path) -> None:
    invocation = build_pytest_invocation(
        [
            "backend/tests/test_safe.py::test_safe",
            "ontolib/tests/test_safe.py",
            "-q",
            "-x",
            "--maxfail=2",
            "-k",
            "safe and not slow",
        ],
        owned_test_root,
    )

    assert invocation.arguments == (
        "pytest",
        "backend/tests/test_safe.py::test_safe",
        "ontolib/tests/test_safe.py",
        "-q",
        "-x",
        "--maxfail=2",
        "-k",
        "safe and not slow",
        "-m",
        "not integration and not mutating_integration and not full_store",
    )
    assert invocation.cwd == owned_test_root.resolve()


def test_agent_test_builds_fixed_frontend_vitest_invocation(
    complete_test_root: Path,
) -> None:
    invocation = build_pytest_invocation(
        ["--frontend", "frontend/src/lib/api.test.ts", "-t", "safe component"],
        complete_test_root,
    )

    assert invocation.arguments == (
        str((complete_test_root / "frontend/node_modules/.bin/vitest").resolve()),
        "run",
        "src/lib/api.test.ts",
        "-t",
        "safe component",
    )
    assert invocation.cwd == (complete_test_root / "frontend").resolve()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--frontend", "frontend/src/lib/api.ts"],
        ["--frontend", "frontend/src/lib/missing.test.ts"],
        ["--frontend", "../outside.test.ts"],
        ["--frontend", "frontend/src/lib/api.test.ts", "--config=bad"],
        ["--frontend", "frontend/src/lib/api.test.ts", "--reporter=json"],
        ["--frontend", "frontend/src/lib/api.test.ts", "-t", "--help"],
        ["--frontend", "frontend/src/lib/api.test.ts", "-t", "bad\ngh pr merge"],
    ],
)
def test_agent_test_rejects_unsafe_frontend_requests(
    complete_test_root: Path, arguments: list[str]
) -> None:
    with pytest.raises(AgentTestInputError):
        build_pytest_invocation(arguments, complete_test_root)


def test_direct_mode_forces_nonintegration_marker_filter(owned_test_root: Path) -> None:
    invocation = build_pytest_invocation(
        ["backend/tests/test_safe.py"], owned_test_root
    )

    assert invocation.arguments[-2:] == (
        "-m",
        "not integration and not mutating_integration and not full_store",
    )


def test_full_store_mode_builds_fixed_read_only_invocation(
    owned_test_root: Path,
) -> None:
    invocation = build_pytest_invocation(
        [
            "--full-store",
            "ontolib/tests/test_safe.py::test_safe",
            "backend/tests/test_safe.py",
            "-v",
            "-x",
            "--maxfail=2",
            "-k",
            "safe and not missing",
        ],
        owned_test_root,
    )

    assert invocation.arguments == (
        "pytest",
        "--require-full-store",
        "ontolib/tests/test_safe.py::test_safe",
        "backend/tests/test_safe.py",
        "-v",
        "-x",
        "--maxfail=2",
        "-k",
        "safe and not missing",
        "-m",
        "integration and full_store",
    )
    assert invocation.cwd == owned_test_root.resolve()
    assert invocation.mode == "full-store"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--full-store"],
        ["--full-store", "--frontend", "frontend/src/lib/api.test.ts"],
        ["--full-store", "--safe-integration", "backend/tests/test_safe.py::test_safe"],
        ["--full-store", "backend/tests/test_safe.py", "--require-full-store"],
        ["--full-store", "backend/tests/test_safe.py", "-m", "full_build"],
        ["--full-store", "backend/tests/test_safe.py", "-m", "mutating_integration"],
        ["--full-store", "backend/tests/test_safe.py", "--rootdir=other"],
        ["--full-store", "backend/tests/test_safe.py", "-c=other.ini"],
        ["--full-store", "backend/tests/test_safe.py", "-p=malicious"],
        ["--full-store", "backend/tests/test_safe.py", "&&", "git", "push"],
        ["--full-store", "frontend/tests/test_safe.py"],
        ["--full-store", "backend/tests/missing.py"],
    ],
)
def test_full_store_mode_rejects_unsafe_requests(
    owned_test_root: Path, arguments: list[str]
) -> None:
    with pytest.raises(AgentTestInputError):
        build_pytest_invocation(arguments, owned_test_root)


def test_full_store_mode_requires_a_python_test_path(owned_test_root: Path) -> None:
    non_python = owned_test_root / "backend/tests/test_data.txt"
    non_python.write_text("not a Python test")

    with pytest.raises(AgentTestInputError, match="Python test path"):
        build_pytest_invocation(
            ["--full-store", "backend/tests/test_data.txt"], owned_test_root
        )


def test_safe_integration_requires_registration_and_uses_fixed_runner(
    complete_test_root: Path,
) -> None:
    invocation = build_pytest_invocation(
        ["--safe-integration", "backend/tests/test_safe.py::test_safe", "-v"],
        complete_test_root,
    )

    assert invocation.arguments == (
        "pdm",
        "run",
        "python",
        "scripts/run_safe_integration.py",
        "backend/tests/test_safe.py::test_safe",
        "-v",
    )
    assert invocation.cwd == complete_test_root.resolve()


def test_safe_integration_rejects_unregistered_node(complete_test_root: Path) -> None:
    with pytest.raises(AgentTestInputError, match="not registered"):
        build_pytest_invocation(
            [
                "--safe-integration",
                "ontolib/tests/test_safe.py::test_safe",
            ],
            complete_test_root,
        )


@pytest.mark.parametrize(
    "registry",
    [
        'mutator = "not-a-list"\n',
        'mutator = ["not-a-mapping"]\n',
        '[[mutator]]\npath = "backend/tests/test_safe.py"\n',
        '[[mutator]]\npath = "backend/tests/test_safe.py"\nfixtures = "bad"\n',
        '[[mutator]]\npath = "backend/tests/test_safe.py"\nfixtures = []\n',
        '[[mutator]]\npath = "backend/tests/test_safe.py"\nfixtures = [""]\n',
        (
            '[[mutator]]\npath = "backend/tests/test_safe.py"\n'
            'fixtures = ["owned"]\ntests = "bad"\n'
        ),
        (
            '[[mutator]]\npath = "backend/tests/test_safe.py"\n'
            'fixtures = ["owned"]\ntests = []\n'
        ),
        (
            '[[mutator]]\npath = "backend/tests/test_safe.py"\n'
            'fixtures = ["owned"]\ntests = ["not_a_test"]\n'
        ),
        '[[mutator]]\npath = "../outside.py"\nfixtures = ["owned"]\n',
        '[[mutator]]\npath = "backend/tests/missing.py"\nfixtures = ["owned"]\n',
        (
            '[[mutator]]\npath = "backend/tests/test_safe.py::test_safe"\n'
            'fixtures = ["owned"]\n'
        ),
        (
            '[[mutator]]\npath = "backend/tests/test_safe.py"\n'
            'fixtures = ["owned"]\nextra = true\n'
        ),
    ],
)
def test_safe_integration_rejects_any_invalid_registry_entry(
    complete_test_root: Path, registry: str
) -> None:
    manifest = complete_test_root / "test_support/integration_mutators.toml"
    manifest.write_text(registry)

    with pytest.raises(
        AgentTestInputError, match="safe integration registry is invalid"
    ):
        build_pytest_invocation(
            ["--safe-integration", "backend/tests/test_safe.py::test_safe"],
            complete_test_root,
        )


def test_safe_integration_rejects_undecodable_registry(
    complete_test_root: Path,
) -> None:
    manifest = complete_test_root / "test_support/integration_mutators.toml"
    manifest.write_bytes(b"\xff")

    with pytest.raises(
        AgentTestInputError, match="safe integration registry is invalid"
    ):
        build_pytest_invocation(
            ["--safe-integration", "backend/tests/test_safe.py::test_safe"],
            complete_test_root,
        )


def test_safe_integration_rejects_registry_read_error(
    complete_test_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = complete_test_root / "test_support/integration_mutators.toml"
    original = Path.read_text

    def fail_manifest(path: Path, *args: object, **kwargs: object) -> str:
        if path == manifest:
            raise PermissionError("token=do-not-reflect")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_manifest)

    with pytest.raises(AgentTestInputError) as failure:
        build_pytest_invocation(
            ["--safe-integration", "backend/tests/test_safe.py::test_safe"],
            complete_test_root,
        )
    assert str(failure.value) == "safe integration registry is invalid"
    assert "do-not-reflect" not in str(failure.value)


def test_agent_test_errors_are_actionable_without_reflecting_input(
    owned_test_root: Path,
) -> None:
    malicious = "../../token=do-not-reflect"

    with pytest.raises(AgentTestInputError) as failure:
        build_pytest_invocation([malicious], owned_test_root)

    assert str(failure.value) == "test path must stay within repository test roots"
    assert malicious not in str(failure.value)
    assert "do-not-reflect" not in str(failure.value)


def test_agent_test_invokes_fixed_command_without_shell(
    owned_test_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    class Result:
        returncode = 0

    def fake_run(arguments: object, **kwargs: object) -> Result:
        observed["arguments"] = arguments
        observed.update(kwargs)
        return Result()

    monkeypatch.setenv("PYTEST_ADDOPTS", "-p malicious")
    monkeypatch.setenv("PYTEST_PLUGINS", "malicious")

    assert (
        run_agent_test(
            ["backend/tests/test_safe.py", "-v"], owned_test_root, runner=fake_run
        )
        == 0
    )
    assert observed["arguments"] == (
        "pytest",
        "backend/tests/test_safe.py",
        "-v",
        "-m",
        "not integration and not mutating_integration and not full_store",
    )
    assert observed["cwd"] == owned_test_root.resolve()
    assert observed["shell"] is False
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert "PYTEST_ADDOPTS" not in environment
    assert "PYTEST_PLUGINS" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert os.environ["PYTEST_ADDOPTS"] == "-p malicious"


def test_full_store_mode_invokes_fixed_command_without_shell(
    owned_test_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    class Result:
        returncode = 0

    def fake_run(arguments: object, **kwargs: object) -> Result:
        observed["arguments"] = arguments
        observed.update(kwargs)
        return Result()

    monkeypatch.setenv("PYTEST_ADDOPTS", "-m mutating_integration")
    monkeypatch.setenv("PYTEST_PLUGINS", "malicious")

    assert (
        run_agent_test(
            ["--full-store", "ontolib/tests/test_safe.py::test_safe", "-v"],
            owned_test_root,
            runner=fake_run,
        )
        == 0
    )
    assert observed["arguments"] == (
        "pytest",
        "--require-full-store",
        "ontolib/tests/test_safe.py::test_safe",
        "-v",
        "-m",
        "integration and full_store",
    )
    assert observed["cwd"] == owned_test_root.resolve()
    assert observed["shell"] is False
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert "PYTEST_ADDOPTS" not in environment
    assert "PYTEST_PLUGINS" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_agent_test_rejects_successful_frontend_run_with_no_executed_tests(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Result:
        returncode = 0

    def write_empty_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output_argument = next(
            argument for argument in arguments if argument.startswith("--outputFile=")
        )
        report_path = Path(output_argument.partition("=")[2])
        report_path.write_text(
            '{"numPassedTests": 0, "numFailedTests": 0, "testResults": []}',
            encoding="utf-8",
        )
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_empty_report,
        )
        == 4
    )
    captured = capsys.readouterr()
    assert captured.err.strip() == "no frontend test matched the request"
    assert captured.out == ""


def _frontend_report(*, passed: int, failed_names: list[str]) -> str:
    assertions = [
        {
            "ancestorTitles": ["showcase"],
            "fullName": name,
            "status": "failed",
            "title": name,
            "failureMessages": ["raw assertion details must not be emitted"],
        }
        for name in failed_names
    ]
    return json.dumps(
        {
            "numPassedTests": passed,
            "numFailedTests": len(failed_names),
            "testResults": [{"assertionResults": assertions}],
        }
    )


def test_agent_test_accepts_valid_successful_frontend_report_without_output(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observed: dict[str, object] = {}

    class Result:
        returncode = 0

    def write_pass_report(arguments: tuple[str, ...], **kwargs: object) -> Result:
        observed.update(kwargs)
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(
            _frontend_report(passed=1, failed_names=[]), encoding="utf-8"
        )
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_pass_report,
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert observed["stdout"] is subprocess.PIPE
    assert observed["stderr"] is subprocess.PIPE


def test_agent_test_surfaces_stable_failed_frontend_test_names_and_child_status(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Result:
        returncode = 7

    def write_failure_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(
            _frontend_report(
                passed=3,
                failed_names=[
                    "showcase renders the failed 404 response",
                    "showcase keeps the existing decomposition",
                ],
            ),
            encoding="utf-8",
        )
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_failure_report,
        )
        == 7
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "frontend tests failed: 2\n"
        "- showcase keeps the existing decomposition\n"
        "- showcase renders the failed 404 response\n"
    )


def test_agent_test_reports_nonzero_frontend_process_without_failed_tests(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Result:
        returncode = 7

    def write_pass_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(
            _frontend_report(passed=1, failed_names=[]), encoding="utf-8"
        )
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_pass_report,
        )
        == 7
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "frontend test process failed without a failed test\n"


@pytest.mark.parametrize("report", ["missing", "malformed"])
@pytest.mark.parametrize("child_status", [0, 7])
def test_agent_test_fails_closed_for_unusable_frontend_report(
    complete_test_root: Path,
    capsys: pytest.CaptureFixture[str],
    report: str,
    child_status: int,
) -> None:
    class Result:
        returncode = child_status

    def write_unusable_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        if report == "malformed":
            output = next(
                item for item in arguments if item.startswith("--outputFile=")
            )
            Path(output.partition("=")[2]).write_text("not-json", encoding="utf-8")
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_unusable_report,
        )
        == 3
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "frontend test report is invalid\n"


def test_agent_test_bounds_and_sanitizes_frontend_failure_names(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Result:
        returncode = 1

    names = [f"test {index:02d} \x1b[31m" + ("x" * 300) for index in range(12)]

    def write_large_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(
            _frontend_report(passed=0, failed_names=list(reversed(names))),
            encoding="utf-8",
        )
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_large_report,
        )
        == 1
    )
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert lines[0] == "frontend tests failed: 12"
    assert len(lines) == 12
    assert lines[-1] == "- ... 2 more failed tests"
    assert [line[2:9] for line in lines[1:11]] == [
        f"test {index:02d}" for index in range(10)
    ]
    assert all(len(line) <= 162 for line in lines[1:11])
    assert "\x1b" not in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (FileNotFoundError("token=secret"), "required test executable is unavailable"),
        (PermissionError("token=secret"), "required test executable is unavailable"),
        (OSError("token=secret"), "test process could not start"),
    ],
)
def test_agent_test_process_start_failures_are_sanitized(
    owned_test_root: Path,
    capsys: pytest.CaptureFixture[str],
    failure: OSError,
    expected: str,
) -> None:
    def fail_to_start(_arguments: object, **_kwargs: object) -> object:
        raise failure

    assert (
        run_agent_test(
            ["backend/tests/test_safe.py"], owned_test_root, runner=fail_to_start
        )
        == 3
    )
    captured = capsys.readouterr()
    assert captured.err.strip() == expected
    assert "token=secret" not in captured.err
    assert captured.out == ""


def test_agent_test_propagates_child_nonzero(owned_test_root: Path) -> None:
    class Result:
        returncode = 7

    def nonzero(_arguments: object, **_kwargs: object) -> Result:
        return Result()

    assert (
        run_agent_test(["backend/tests/test_safe.py"], owned_test_root, runner=nonzero)
        == 7
    )


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"numPassedTests": "one", "numFailedTests": 0}',
        '{"numPassedTests": 0}',
        '{"numPassedTests": -1, "numFailedTests": 1}',
    ],
)
def test_vitest_report_parser_rejects_malformed_payload(payload: str) -> None:
    with pytest.raises(AgentTestInputError, match="frontend test report is invalid"):
        parse_vitest_execution_count(payload)


def test_vitest_report_parser_distinguishes_zero_and_executed_tests() -> None:
    assert (
        parse_vitest_execution_count('{"numPassedTests": 0, "numFailedTests": 0}') == 0
    )
    assert (
        parse_vitest_execution_count('{"numPassedTests": 2, "numFailedTests": 1}') == 3
    )
