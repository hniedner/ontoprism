from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Never

import pytest
from scripts.validation.run_agent_test import (
    AgentTestInputError,
    _subprocess_runner,
    build_pytest_invocation,
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
    for relative in (
        "frontend/src/lib/api.test.ts",
        "frontend/src/lib/components/card.spec.ts",
    ):
        frontend_test = owned_test_root / relative
        frontend_test.parent.mkdir(parents=True, exist_ok=True)
        frontend_test.write_text(
            "import { test } from 'vitest'; test('safe', () => {});\n"
        )
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
        tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
    )

    assert invocation.arguments == (
        str((complete_test_root / "frontend/node_modules/.bin/vitest").resolve()),
        "run",
        "src/lib/api.test.ts",
        "-t",
        "safe component",
    )
    assert invocation.cwd == (complete_test_root / "frontend").resolve()


def test_agent_test_builds_multi_file_frontend_invocation_from_tracked_inventory(
    complete_test_root: Path,
) -> None:
    invocation = build_pytest_invocation(
        [
            "--frontend",
            "frontend/src/lib/api.test.ts",
            "frontend/src/lib/components/card.spec.ts",
        ],
        complete_test_root,
        tracked_frontend_paths=frozenset(
            {
                "frontend/src/lib/api.test.ts",
                "frontend/src/lib/components/card.spec.ts",
            }
        ),
    )

    assert invocation.arguments[1:] == (
        "run",
        "src/lib/api.test.ts",
        "src/lib/components/card.spec.ts",
    )
    assert invocation.frontend_tests == (
        "frontend/src/lib/api.test.ts",
        "frontend/src/lib/components/card.spec.ts",
    )


def test_agent_test_rejects_untracked_frontend_file(complete_test_root: Path) -> None:
    with pytest.raises(AgentTestInputError, match="tracked"):
        build_pytest_invocation(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            tracked_frontend_paths=frozenset(),
        )


def test_agent_test_rejects_frontend_symlink(
    complete_test_root: Path,
) -> None:
    link = complete_test_root / "frontend/src/lib/link.test.ts"
    link.symlink_to(complete_test_root / "frontend/src/lib/api.test.ts")

    with pytest.raises(AgentTestInputError, match="symlink"):
        build_pytest_invocation(
            ["--frontend", "frontend/src/lib/link.test.ts"],
            complete_test_root,
            tracked_frontend_paths=frozenset({"frontend/src/lib/link.test.ts"}),
        )


def test_agent_test_rejects_duplicate_frontend_paths(
    complete_test_root: Path,
) -> None:
    with pytest.raises(AgentTestInputError, match="unique"):
        build_pytest_invocation(
            [
                "--frontend",
                "frontend/src/lib/api.test.ts",
                "frontend/src/lib/api.test.ts",
            ],
            complete_test_root,
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )


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
        stdout = None
        stderr = None

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
        stdout = None
        stderr = None

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
        stdout = b""
        stderr = b""

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
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 4
    )
    captured = capsys.readouterr()
    assert captured.err.strip() == "no frontend test matched the request"
    assert captured.out == ""


def _frontend_report(
    *,
    passed: int,
    failed_names: list[str],
    failed: int | None = None,
    file_name: str = "/repo/frontend/src/lib/api.test.ts",
    status: str = "passed",
) -> str:
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
    assertions.extend(
        {
            "ancestorTitles": ["showcase"],
            "fullName": f"showcase pass {index}",
            "status": status,
            "title": f"pass {index}",
            "failureMessages": [],
        }
        for index in range(passed)
    )
    return json.dumps(
        {
            "numPassedTests": passed,
            "numFailedTests": len(failed_names) if failed is None else failed,
            "testResults": [{"name": file_name, "assertionResults": assertions}],
        }
    )


def test_agent_test_accepts_valid_successful_frontend_report_without_output(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observed: dict[str, object] = {}

    class Result:
        returncode = 0
        stdout = b""
        stderr = b""

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
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert observed["stdout"] is subprocess.PIPE
    assert observed["stderr"] is subprocess.PIPE


@pytest.mark.parametrize("missing_status", ["skipped", "pending", "todo"])
@pytest.mark.parametrize(("child_status", "expected"), [(0, 4), (7, 7)])
def test_agent_test_rejects_success_when_any_requested_file_has_no_executed_assertion(
    complete_test_root: Path,
    capsys: pytest.CaptureFixture[str],
    missing_status: str,
    child_status: int,
    expected: int,
) -> None:
    class Result:
        returncode = child_status
        stdout = b""
        stderr = b""

    def write_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        report = {
            "numPassedTests": 1,
            "numFailedTests": 0,
            "testResults": [
                {
                    "name": "/repo/frontend/src/lib/api.test.ts",
                    "assertionResults": [{"status": "passed"}],
                },
                {
                    "name": "/repo/frontend/src/lib/components/card.spec.ts",
                    "assertionResults": [{"status": missing_status}],
                },
            ],
        }
        Path(output.partition("=")[2]).write_text(json.dumps(report), encoding="utf-8")
        return Result()

    assert (
        run_agent_test(
            [
                "--frontend",
                "frontend/src/lib/api.test.ts",
                "frontend/src/lib/components/card.spec.ts",
            ],
            complete_test_root,
            runner=write_report,
            tracked_frontend_paths=frozenset(
                {
                    "frontend/src/lib/api.test.ts",
                    "frontend/src/lib/components/card.spec.ts",
                }
            ),
        )
        == expected
    )
    assert (
        capsys.readouterr().err
        == "frontend test report did not execute every requested file\n"
    )


def test_agent_test_surfaces_stable_failed_frontend_test_names_and_child_status(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Result:
        returncode = 7
        stdout = b"raw assertion output must stay hidden"
        stderr = b"token=assertion-secret"

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
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
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
    assert "assertion-secret" not in captured.err


@pytest.mark.parametrize(
    ("failed_names", "failed", "expected_lines"),
    [
        ([], 2, ["- 2 unnamed frontend failures"]),
        (
            ["showcase reports the stable assertion"],
            3,
            [
                "- showcase reports the stable assertion",
                "- 2 unnamed frontend failures",
            ],
        ),
    ],
)
def test_agent_test_reports_named_and_unnamed_frontend_failures(
    complete_test_root: Path,
    capsys: pytest.CaptureFixture[str],
    failed_names: list[str],
    failed: int,
    expected_lines: list[str],
) -> None:
    class Result:
        returncode = 1
        stdout = b"assertion details must stay hidden"
        stderr = b"password=assertion-secret"

    def write_failure_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(
            _frontend_report(
                passed=1,
                failed_names=failed_names,
                failed=failed,
            ),
            encoding="utf-8",
        )
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_failure_report,
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        f"frontend tests failed: {failed}",
        *expected_lines,
    ]
    assert "assertion-secret" not in captured.err


def test_agent_test_reports_nonzero_frontend_process_without_failed_tests(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Result:
        returncode = 7
        stdout = b"\x1b[31mBuild   crashed\x1b[0m\n"
        stderr = b"Authorization: Bearer top-secret\nworker stopped"

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
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 7
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "frontend test process failed without a failed test\n"
        "frontend diagnostic:\n"
        "Build crashed\n"
        "Authorization=[REDACTED]\n"
        "worker stopped\n"
    )


def test_agent_test_preserves_nonzero_status_for_zero_test_pretest_crash(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Result:
        returncode = 7
        stdout = b""
        stderr = b"configuration crashed before collection"

    def write_empty_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(
            _frontend_report(passed=0, failed_names=[]), encoding="utf-8"
        )
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_empty_report,
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 7
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "frontend test process failed before tests executed\n"
        "frontend diagnostic:\n"
        "configuration crashed before collection\n"
    )


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
        stdout = b"token=stdout-secret"
        stderr = b"invalid reporter output"

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
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 3
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "frontend test report is invalid\n"
        "frontend diagnostic:\n"
        "token=[REDACTED]\n"
        "invalid reporter output\n"
    )


def test_agent_test_bounds_and_sanitizes_frontend_failure_names(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Result:
        returncode = 1
        stdout = b""
        stderr = b""

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
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 1
    )
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert lines[0] == "frontend tests failed: 12"
    assert len(lines) == 12
    assert lines[-1] == "- ... 2 more named frontend failures"
    assert [line[2:9] for line in lines[1:11]] == [
        f"test {index:02d}" for index in range(10)
    ]
    assert all(len(line) <= 162 for line in lines[1:11])
    assert "\x1b" not in captured.err
    assert captured.out == ""


def test_agent_test_bounds_and_redacts_frontend_diagnostic_tail(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worktree_path = complete_test_root / "frontend/src/lib/private.ts"
    long_lines = "".join(
        f"discard-{index} " + ("x" * 400) + "\n" for index in range(30)
    )

    class Result:
        returncode = 9

        def __init__(self, report_path: Path) -> None:
            self.stdout = (long_lines + f"at {worktree_path}\n").encode()
            self.stderr = (
                f"temp report {report_path}\napi_key=abc123 password: hunter2\n"
                "\x1b[31mfinal\x00 diagnostic\x1b[0m\n"
            ).encode()

    def write_pass_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(
            _frontend_report(passed=1, failed_names=[]), encoding="utf-8"
        )
        return Result(Path(output.partition("=")[2]))

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_pass_report,
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 9
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(
        "frontend test process failed without a failed test\nfrontend diagnostic:\n"
    )
    diagnostic = captured.err.partition("frontend diagnostic:\n")[2]
    assert len(diagnostic) <= 2_000
    assert len(diagnostic.splitlines()) <= 12
    assert "abc123" not in diagnostic
    assert "hunter2" not in diagnostic
    assert str(complete_test_root) not in diagnostic
    assert "ontoprism-agent-test-" not in diagnostic
    assert "\x1b" not in diagnostic
    assert "\x00" not in diagnostic
    assert "api_key=[REDACTED] password=[REDACTED]" in diagnostic
    assert "at <WORKTREE>/frontend/src/lib/private.ts" in diagnostic
    assert "temp report <TEMP>/vitest-report.json" in diagnostic
    assert "final diagnostic" in diagnostic


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
    def fail_to_start(arguments: object, **_kwargs: object) -> Never:
        del arguments
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


def test_frontend_timeout_is_sanitized_and_nonzero(
    complete_test_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def time_out(arguments: tuple[str, ...], **_kwargs: object) -> Never:
        raise subprocess.TimeoutExpired(arguments, 300)

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=time_out,
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 3
    )
    assert capsys.readouterr().err == "frontend test timed out\n"


def test_subprocess_timeout_tolerates_exited_group_and_drains_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 4321
        returncode = -9
        calls = 0

        def communicate(self, *, timeout: int | None = None) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(("vitest",), timeout or 0)
            return b"", b""

    process = Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    def already_exited(pid: int, sig: signal.Signals) -> Never:
        assert (pid, sig) == (4321, signal.SIGKILL)
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", already_exited)

    with pytest.raises(subprocess.TimeoutExpired):
        _subprocess_runner(
            ("vitest",),
            cwd=Path("."),
            env={},
            shell=False,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=1,
            start_new_session=True,
        )

    assert process.calls == 2


def test_agent_test_propagates_child_nonzero(owned_test_root: Path) -> None:
    class Result:
        returncode = 7
        stdout = None
        stderr = None

    def nonzero(arguments: object, **_kwargs: object) -> Result:
        del arguments
        return Result()

    assert (
        run_agent_test(["backend/tests/test_safe.py"], owned_test_root, runner=nonzero)
        == 7
    )


@pytest.mark.parametrize(
    "report",
    [
        "not-json",
        "[]",
        '{"numPassedTests": "one", "numFailedTests": 0}',
        '{"numPassedTests": 0}',
        '{"numPassedTests": -1, "numFailedTests": 1}',
        '{"numPassedTests": 0, "numFailedTests": 0, "testResults": {}}',
        (
            '{"numPassedTests": 1, "numFailedTests": 0, '
            '"testResults": [{"assertionResults": {}}]}'
        ),
        (
            '{"numPassedTests": 0, "numFailedTests": 1, '
            '"testResults": [{"assertionResults": '
            '[{"status": 1, "fullName": "bad"}]}]}'
        ),
    ],
)
def test_agent_test_rejects_malformed_frontend_report_structure(
    complete_test_root: Path,
    capsys: pytest.CaptureFixture[str],
    report: str,
) -> None:
    class Result:
        returncode = 1
        stdout = b""
        stderr = b""

    def write_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(report, encoding="utf-8")
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_report,
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 3
    )
    assert capsys.readouterr().err == "frontend test report is invalid\n"


@pytest.mark.parametrize(
    "failed_names",
    [
        ["same failure", "same failure"],
        ["one named failure", "another named failure"],
    ],
)
def test_agent_test_rejects_misleading_frontend_failure_names(
    complete_test_root: Path,
    capsys: pytest.CaptureFixture[str],
    failed_names: list[str],
) -> None:
    class Result:
        returncode = 1
        stdout = b""
        stderr = b""

    def write_report(arguments: tuple[str, ...], **_kwargs: object) -> Result:
        output = next(item for item in arguments if item.startswith("--outputFile="))
        Path(output.partition("=")[2]).write_text(
            _frontend_report(passed=0, failed_names=failed_names, failed=1),
            encoding="utf-8",
        )
        return Result()

    assert (
        run_agent_test(
            ["--frontend", "frontend/src/lib/api.test.ts"],
            complete_test_root,
            runner=write_report,
            tracked_frontend_paths=frozenset({"frontend/src/lib/api.test.ts"}),
        )
        == 3
    )
    assert capsys.readouterr().err == "frontend test report is invalid\n"
