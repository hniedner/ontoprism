from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest
import scripts.validation.run_agent_git as agent_git
from scripts.validation.run_agent_git import (
    AgentGitInputError,
    AgentGitProcessError,
    run_agent_git,
)

pytestmark = pytest.mark.unit


def test_operation_specs_derive_classification_from_command_kind() -> None:
    expected = {
        "switch-existing": "local-mutation",
        "switch-new": "local-mutation",
        "delete-merged": "local-mutation",
        "merge-no-ff": "local-mutation",
        "commit-staged": "local-mutation",
        "pull-origin": "remote-mutation",
        "push-origin": "remote-mutation",
    }

    assert [field.name for field in fields(agent_git.OperationSpec)] == [
        "command_kind",
        "failure",
    ]
    assert {
        operation: agent_git.operation_class_for(spec.command_kind)
        for operation, spec in agent_git.OPERATION_SPECS.items()
    } == expected


def test_git_invocation_requires_an_explicit_operation_class() -> None:
    parameter = inspect.signature(agent_git._invoke).parameters["operation_class"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize(
    "arguments",
    [
        ["switch-existing", "--discard-changes"],
        ["switch-existing", "../escape"],
        ["switch-existing", "bad..name"],
        ["switch-existing", "bad@{1}"],
        ["switch-existing", "bad\nmain"],
        ["switch-new", "feature", "--start", "main"],
        ["delete-force", "feature"],
        ["delete-merged", "main"],
        # the permission map also allows each subcommand bare (OpenCode's optional tail)
        ["switch-existing"],
        ["switch-new"],
        ["delete-merged"],
        ["pull-origin"],
        ["push-origin"],
        ["merge-no-ff", "feature", "--strategy=ours"],
        ["commit-staged", "message"],
        ["commit-staged", "--amend", "message"],
        ["commit-staged", "--message"],
        ["commit-staged", "--subject", "message"],
    ],
)
def test_agent_git_rejects_unsafe_operations(
    arguments: list[str], tmp_path: Path
) -> None:
    with pytest.raises(AgentGitInputError):
        run_agent_git(arguments, tmp_path)


def git(repository: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed Git test helper
        ["/usr/bin/git", *arguments],
        cwd=repository,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def initialize_repository(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "tracked.txt").write_text("main\n")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-m", "initial")


def test_switch_existing_returns_to_main_where_mutations_stay_refused(
    tmp_path: Path,
) -> None:
    initialize_repository(tmp_path)
    assert run_agent_git(["switch-new", "feat/safe"], tmp_path) == 0
    assert git(tmp_path, "branch", "--show-current") == "feat/safe"

    assert run_agent_git(["switch-existing", "main"], tmp_path) == 0

    assert git(tmp_path, "branch", "--show-current") == "main"
    (tmp_path / "tracked.txt").write_text("changed\n")
    git(tmp_path, "add", "tracked.txt")
    with pytest.raises(AgentGitInputError, match="protected branch"):
        run_agent_git(["commit-staged", "--message", "fix: no"], tmp_path)
    with pytest.raises(AgentGitInputError, match="protected branch"):
        run_agent_git(["merge-no-ff", "feat/safe"], tmp_path)


def test_commit_staged_rejects_main_and_commits_on_feature(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "tracked.txt").write_text("feature\n")
    git(tmp_path, "add", "tracked.txt")

    with pytest.raises(AgentGitInputError, match="protected branch"):
        run_agent_git(
            ["commit-staged", "--message", "test: forbidden on main"], tmp_path
        )
    assert run_agent_git(["switch-new", "feat/safe"], tmp_path) == 0
    assert (
        run_agent_git(
            ["commit-staged", "--message", "test: commit through wrapper"],
            tmp_path,
        )
        == 0
    )

    subject = subprocess.run(
        ["/usr/bin/git", "log", "-1", "--format=%s"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert subject.stdout.strip() == "test: commit through wrapper"


def test_merge_rejects_main_and_succeeds_on_milestone(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    git(tmp_path, "switch", "-c", "feat/safe")
    (tmp_path / "feature.txt").write_text("feature\n")
    git(tmp_path, "add", "feature.txt")
    git(tmp_path, "commit", "-m", "feature")
    git(tmp_path, "switch", "main")

    with pytest.raises(AgentGitInputError, match="protected branch"):
        run_agent_git(["merge-no-ff", "feat/safe"], tmp_path)

    git(tmp_path, "switch", "-c", "milestone/process")
    assert run_agent_git(["merge-no-ff", "feat/safe"], tmp_path) == 0
    assert (tmp_path / "feature.txt").read_text() == "feature\n"


@pytest.mark.parametrize("source_kind", ["commit", "tag", "remote"])
def test_merge_requires_an_exact_existing_local_branch(
    tmp_path: Path, source_kind: str
) -> None:
    initialize_repository(tmp_path)
    git(tmp_path, "switch", "-c", "feat/local")
    (tmp_path / "feature.txt").write_text("feature\n")
    git(tmp_path, "add", "feature.txt")
    git(tmp_path, "commit", "-m", "feature")
    feature_commit = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "tag", "release-candidate")
    git(tmp_path, "update-ref", "refs/remotes/origin/feat/remote", feature_commit)
    git(tmp_path, "switch", "main")
    git(tmp_path, "switch", "-c", "milestone/process")
    source = {
        "commit": feature_commit,
        "tag": "release-candidate",
        "remote": "origin/feat/remote",
    }[source_kind]

    with pytest.raises(AgentGitInputError, match=r"^local branch does not exist$"):
        run_agent_git(["merge-no-ff", source], tmp_path)

    assert not (tmp_path / "feature.txt").exists()


def test_merge_accepts_an_exact_existing_local_branch(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    git(tmp_path, "switch", "-c", "feat/local")
    (tmp_path / "feature.txt").write_text("feature\n")
    git(tmp_path, "add", "feature.txt")
    git(tmp_path, "commit", "-m", "feature")
    git(tmp_path, "switch", "main")
    git(tmp_path, "switch", "-c", "milestone/process")

    assert run_agent_git(["merge-no-ff", "feat/local"], tmp_path) == 0
    assert (tmp_path / "feature.txt").read_text() == "feature\n"


@pytest.mark.parametrize("operation", ["switch-existing", "delete-merged"])
def test_existing_branch_operations_reject_non_branch_refs(
    tmp_path: Path, operation: str
) -> None:
    initialize_repository(tmp_path)
    git(tmp_path, "tag", "release-candidate")
    git(tmp_path, "switch", "-c", "feat/current")

    with pytest.raises(AgentGitInputError, match=r"^local branch does not exist$"):
        run_agent_git([operation, "release-candidate"], tmp_path)


@pytest.mark.parametrize("operation", ["commit-staged", "merge-no-ff"])
def test_commit_and_merge_reject_detached_head(tmp_path: Path, operation: str) -> None:
    initialize_repository(tmp_path)
    git(tmp_path, "branch", "feat/safe")
    git(tmp_path, "switch", "--detach")

    arguments = (
        [operation, "--message", "test: detached"]
        if operation == "commit-staged"
        else [operation, "feat/safe"]
    )
    with pytest.raises(AgentGitInputError, match="attached"):
        run_agent_git(arguments, tmp_path)


@pytest.mark.parametrize(
    "message",
    [
        "",
        "   ",
        "-m override",
        "message; git push",
        "message\nsecond line",
        "x" * 201,
    ],
)
def test_commit_staged_rejects_unsafe_messages(tmp_path: Path, message: str) -> None:
    with pytest.raises(AgentGitInputError, match="commit message is invalid"):
        run_agent_git(["commit-staged", "--message", message], tmp_path)


def test_agent_git_reports_conflicted_merge_as_unknown_repository_state(
    tmp_path: Path,
) -> None:
    message = (
        "Git merge failed and may have changed repository state; inspect git status"
    )
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test User")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-m", "initial")
    git(tmp_path, "switch", "-c", "feat/conflict")
    tracked.write_text("feature\n")
    git(tmp_path, "commit", "-am", "feature")
    git(tmp_path, "switch", "main")
    git(tmp_path, "switch", "-c", "milestone/conflict")
    tracked.write_text("main\n")
    git(tmp_path, "commit", "-am", "main")

    try:
        with pytest.raises(
            AgentGitProcessError,
            match=f"^{re.escape(message)}$",
        ):
            run_agent_git(["merge-no-ff", "feat/conflict"], tmp_path)
        assert (tmp_path / ".git" / "MERGE_HEAD").is_file()
    finally:
        if (tmp_path / ".git" / "MERGE_HEAD").is_file():
            git(tmp_path, "merge", "--abort")

    assert not (tmp_path / ".git" / "MERGE_HEAD").exists()
    status = subprocess.run(
        ["/usr/bin/git", "status", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


class Result:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def scripted_runner(results: list[object]) -> object:
    remaining = iter(results)

    def run(_arguments: object, **_kwargs: object) -> object:
        result = next(remaining)
        if isinstance(result, BaseException):
            raise result
        return result

    return run


def test_agent_git_bounds_every_process(tmp_path: Path) -> None:
    timeouts: list[object] = []

    def runner(_arguments: object, **kwargs: object) -> Result:
        timeouts.append(kwargs.get("timeout"))
        assert "stdin" not in kwargs
        assert "env" not in kwargs
        return Result(0)

    assert run_agent_git(["switch-new", "feat/x"], tmp_path, runner=runner) == 0
    assert timeouts == [10, 600]


def test_branch_validation_uses_a_fixed_full_local_branch_ref(tmp_path: Path) -> None:
    commands: list[object] = []

    def runner(arguments: object, **_kwargs: object) -> Result:
        commands.append(arguments)
        return Result(0)

    assert run_agent_git(["switch-new", "feat/x"], tmp_path, runner=runner) == 0
    assert commands[0] == ["git", "check-ref-format", "refs/heads/feat/x"]


@pytest.mark.parametrize("branch", ["bad.lock", "feat/trailing."])
def test_agent_git_cli_reports_git_rejected_branch_as_invalid(branch: str) -> None:
    script = Path(__file__).parents[1] / "scripts" / "validation" / "run_agent_git.py"

    result = subprocess.run(  # noqa: S603 - fixed repository script
        [sys.executable, str(script), "switch-new", branch],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stderr.strip() == "branch name is invalid"


@pytest.mark.parametrize(
    ("returncode", "error_type", "message"),
    [
        (
            -9,
            AgentGitProcessError,
            "Git branch validation was interrupted; retry the operation",
        ),
        (1, AgentGitInputError, "branch name is invalid"),
        (2, AgentGitProcessError, "Git branch validation failed"),
        (128, AgentGitProcessError, "Git branch validation failed"),
    ],
)
def test_branch_validation_classifies_git_return_codes_without_raw_output(
    tmp_path: Path,
    returncode: int,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=f"^{re.escape(message)}$") as raised:
        run_agent_git(
            ["switch-new", "feat/x"],
            tmp_path,
            runner=scripted_runner([Result(returncode, "secret Git output")]),
        )

    assert "secret" not in str(raised.value)


def test_malformed_global_git_config_is_an_operational_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_repository(tmp_path)
    git(tmp_path, "branch", "feat/existing")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/")

    with pytest.raises(AgentGitProcessError, match="Git local branch check failed"):
        run_agent_git(["switch-existing", "feat/existing"], tmp_path)


@pytest.mark.parametrize(
    ("returncode", "error_type", "message"),
    [
        (-9, AgentGitProcessError, "Git local branch check failed"),
        (1, AgentGitInputError, "local branch does not exist"),
        (2, AgentGitProcessError, "Git local branch check failed"),
    ],
)
def test_local_branch_check_classifies_return_codes_without_raw_output(
    tmp_path: Path,
    returncode: int,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=f"^{re.escape(message)}$") as raised:
        run_agent_git(
            ["switch-existing", "feat/x"],
            tmp_path,
            runner=scripted_runner(
                [Result(0), Result(returncode, "secret Git output")]
            ),
        )

    assert "secret" not in str(raised.value)


def test_delete_merged_distinguishes_not_merged_from_operational_error(
    tmp_path: Path,
) -> None:
    not_merged = scripted_runner(
        [
            Result(0),
            Result(0),
            Result(0, "main\n"),
            Result(1),
            Result(0, f"{'a' * 40}\n"),
            Result(0, "[]"),
        ]
    )
    with pytest.raises(AgentGitInputError, match="not merged"):
        run_agent_git(["delete-merged", "feat/x"], tmp_path, runner=not_merged)

    operational = scripted_runner(
        [Result(0), Result(0), Result(0, "main\n"), Result(2)]
    )
    with pytest.raises(AgentGitProcessError, match="merge ancestry check failed"):
        run_agent_git(["delete-merged", "feat/x"], tmp_path, runner=operational)

    signaled = scripted_runner([Result(0), Result(0), Result(0, "main\n"), Result(-9)])
    with pytest.raises(AgentGitProcessError, match="merge ancestry check failed"):
        run_agent_git(["delete-merged", "feat/x"], tmp_path, runner=signaled)


def test_agent_git_classifies_decode_start_timeout_and_nonzero(
    tmp_path: Path,
) -> None:
    undecodable = UnicodeDecodeError("utf-8", b"\xffsecret", 0, 1, "invalid")
    with pytest.raises(AgentGitProcessError, match="produced undecodable output"):
        run_agent_git(
            ["switch-existing", "feat/x"],
            tmp_path,
            runner=scripted_runner([undecodable]),
        )

    mutation_decode = scripted_runner([Result(0), Result(0), undecodable])
    with pytest.raises(
        AgentGitProcessError, match="outcome is unknown; inspect git status"
    ):
        run_agent_git(["switch-existing", "feat/x"], tmp_path, runner=mutation_decode)

    with pytest.raises(AgentGitProcessError, match="process could not start") as raised:
        run_agent_git(
            ["switch-existing", "feat/x"],
            tmp_path,
            runner=scripted_runner([FileNotFoundError("secret")]),
        )
    assert "secret" not in str(raised.value)

    timeout = subprocess.TimeoutExpired(["git"], 10)
    with pytest.raises(AgentGitProcessError, match="timed out"):
        run_agent_git(
            ["switch-existing", "feat/x"],
            tmp_path,
            runner=scripted_runner([timeout]),
        )


def test_oserror_reports_distinct_sanitized_mutation_state(tmp_path: Path) -> None:
    read_only = OSError("read-only secret")
    with pytest.raises(
        AgentGitProcessError, match=r"^Git process could not start$"
    ) as read_error:
        run_agent_git(
            ["switch-new", "feat/x"],
            tmp_path,
            runner=scripted_runner([read_only]),
        )
    assert "secret" not in str(read_error.value)

    mutating = OSError("mutation secret")
    with pytest.raises(
        AgentGitProcessError,
        match=r"^Git operation outcome is unknown; inspect git status$",
    ) as mutation_error:
        run_agent_git(
            ["switch-new", "feat/x"],
            tmp_path,
            runner=scripted_runner([Result(0), mutating]),
        )
    assert "secret" not in str(mutation_error.value)


@pytest.mark.parametrize(
    ("arguments", "results", "message"),
    [
        (
            ["switch-existing", "feat/x"],
            [Result(0), Result(0), Result(9)],
            "Git switch failed and may have changed repository state; "
            "inspect git status",
        ),
        (
            ["switch-new", "feat/x"],
            [Result(0), Result(9)],
            "Git branch creation failed and may have changed repository state; "
            "inspect git status",
        ),
        (
            ["merge-no-ff", "feat/x"],
            [Result(0), Result(0), Result(0, "milestone/x\n"), Result(9)],
            "Git merge failed and may have changed repository state; "
            "inspect git status",
        ),
        (
            ["commit-staged", "--message", "test: safe commit"],
            [Result(0, "feat/x\n"), Result(9)],
            "Git commit failed and may have changed repository state; "
            "inspect git status",
        ),
        (
            ["delete-merged", "feat/x"],
            [Result(0), Result(0), Result(0, "main\n"), Result(0), Result(9)],
            "Git branch deletion failed and may have changed repository state; "
            "inspect git status",
        ),
    ],
)
def test_mutating_nonzero_reports_operation_specific_unknown_state(
    tmp_path: Path,
    arguments: list[str],
    results: list[object],
    message: str,
) -> None:
    with pytest.raises(AgentGitProcessError, match=f"^{re.escape(message)}$"):
        run_agent_git(arguments, tmp_path, runner=scripted_runner(results))


@pytest.mark.parametrize(
    ("operation", "origin", "expected_command"),
    [
        (
            "pull-origin",
            "https://github.com/hniedner/ontoprism",
            ["git", "pull", "--ff-only", "origin", "refs/heads/feat/x"],
        ),
        (
            "push-origin",
            "git@github.com:hniedner/ontoprism.git",
            [
                "git",
                "push",
                "--set-upstream",
                "origin",
                "refs/heads/feat/x:refs/heads/feat/x",
            ],
        ),
    ],
)
def test_remote_operations_use_only_fixed_origin_and_exact_refs(
    tmp_path: Path,
    operation: str,
    origin: str,
    expected_command: list[str],
) -> None:
    commands: list[list[str]] = []

    def runner(arguments: list[str], **_kwargs: object) -> Result:
        commands.append(arguments)
        responses = {
            ("git", "check-ref-format", "refs/heads/feat/x"): Result(0),
            ("git", "remote", "get-url", "origin"): Result(0, origin),
            ("git", "branch", "--show-current"): Result(0, "feat/x\n"),
            ("git", "status", "--porcelain"): Result(0, ""),
        }
        return responses.get(tuple(arguments), Result(0))

    assert run_agent_git([operation, "feat/x"], tmp_path, runner=runner) == 0
    assert commands[1] == ["git", "remote", "get-url", "origin"]
    assert commands[-1] == expected_command
    assert "--force" not in commands[-1]
    assert "--delete" not in commands[-1]


@pytest.mark.parametrize("operation", ["pull-origin", "push-origin"])
def test_remote_git_is_noninteractive_without_changing_local_invocations(
    tmp_path: Path, operation: str
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(arguments: list[str], **kwargs: object) -> Result:
        calls.append((arguments, kwargs))
        responses = {
            ("git", "remote", "get-url", "origin"): Result(
                0, "https://github.com/hniedner/ontoprism"
            ),
            ("git", "branch", "--show-current"): Result(0, "feat/x\n"),
        }
        return responses.get(tuple(arguments), Result(0))

    assert run_agent_git([operation, "feat/x"], tmp_path, runner=runner) == 0

    for _arguments, kwargs in calls[:-1]:
        assert "stdin" not in kwargs
        assert "env" not in kwargs
    remote_kwargs = calls[-1][1]
    assert remote_kwargs["stdin"] is subprocess.DEVNULL
    environment = remote_kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GCM_INTERACTIVE"] == "Never"
    assert environment.get("PATH") == os.environ.get("PATH")


@pytest.mark.parametrize("operation", ["pull-origin", "push-origin"])
def test_remote_nonzero_is_a_sanitized_known_failure(
    tmp_path: Path, operation: str
) -> None:
    runner = scripted_runner(
        [
            Result(0),
            Result(0, "https://github.com/hniedner/ontoprism"),
            Result(0, "feat/x\n"),
            Result(0),
            Result(128, "credential=secret"),
        ]
    )

    with pytest.raises(AgentGitProcessError) as raised:
        run_agent_git([operation, "feat/x"], tmp_path, runner=runner)

    assert str(raised.value) == (
        f"Git {'pull' if operation == 'pull-origin' else 'push'} failed; "
        "inspect repository and remote state before retrying"
    )
    assert "unknown" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_pull_origin_permits_attached_clean_main(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(arguments: list[str], **_kwargs: object) -> Result:
        commands.append(arguments)
        if arguments == ["git", "remote", "get-url", "origin"]:
            return Result(0, "https://github.com/hniedner/ontoprism\n")
        if arguments == ["git", "branch", "--show-current"]:
            return Result(0, "main\n")
        return Result(0)

    assert run_agent_git(["pull-origin", "main"], tmp_path, runner=runner) == 0
    assert commands[-1] == [
        "git",
        "pull",
        "--ff-only",
        "origin",
        "refs/heads/main",
    ]


@pytest.mark.parametrize("operation", ["pull-origin", "push-origin"])
@pytest.mark.parametrize(
    ("results", "message"),
    [
        (
            [
                Result(0),
                Result(0, "https://github.com/hniedner/ontoprism"),
                Result(0, "other\n"),
            ],
            "requested branch is not the current branch",
        ),
        (
            [
                Result(0),
                Result(0, "https://github.com/hniedner/ontoprism"),
                Result(0, ""),
            ],
            "attached branch",
        ),
        (
            [
                Result(0),
                Result(0, "https://github.com/hniedner/ontoprism"),
                Result(0, "feat/x\n"),
                Result(0, " M tracked.txt\n"),
            ],
            "clean worktree",
        ),
    ],
)
def test_remote_operations_require_matching_attached_clean_branch(
    tmp_path: Path, operation: str, results: list[object], message: str
) -> None:
    with pytest.raises(AgentGitInputError, match=message):
        run_agent_git([operation, "feat/x"], tmp_path, runner=scripted_runner(results))


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/attacker/hniedner/ontoprism",
        "https://github.com/hniedner/ontoprism.evil",
        "git@github.com:hniedner/ontoprism.git/extra",
        "ssh://evil.example/hniedner/ontoprism.git",
        "",
    ],
)
def test_remote_operations_reject_wrong_or_malformed_origin(
    tmp_path: Path, origin: str
) -> None:
    with pytest.raises(AgentGitInputError, match="origin"):
        run_agent_git(
            ["pull-origin", "feat/x"],
            tmp_path,
            runner=scripted_runner([Result(0), Result(0, origin)]),
        )


@pytest.mark.parametrize(
    ("results", "message"),
    [
        ([Result(0), Result(2)], "origin could not be determined"),
        (
            [Result(0), Result(0, "https://github.com/hniedner/ontoprism"), Result(2)],
            "current branch could not be determined",
        ),
        (
            [
                Result(0),
                Result(0, "https://github.com/hniedner/ontoprism"),
                Result(0, "feat/x\n"),
                Result(2),
            ],
            "worktree status could not be determined",
        ),
    ],
)
def test_remote_operations_fail_closed_when_preconditions_cannot_be_determined(
    tmp_path: Path, results: list[object], message: str
) -> None:
    with pytest.raises(AgentGitProcessError, match=message):
        run_agent_git(
            ["pull-origin", "feat/x"], tmp_path, runner=scripted_runner(results)
        )


@pytest.mark.parametrize("branch", ["main", "master"])
def test_push_origin_rejects_protected_branches(tmp_path: Path, branch: str) -> None:
    with pytest.raises(AgentGitInputError, match="protected branch"):
        run_agent_git(["push-origin", branch], tmp_path)


@pytest.mark.parametrize("operation", ["pull-origin", "push-origin"])
def test_remote_mutation_timeout_requires_repository_and_remote_inspection(
    tmp_path: Path, operation: str
) -> None:
    timeout = subprocess.TimeoutExpired(["git"], 600)
    runner = scripted_runner(
        [
            Result(0),
            Result(0, "https://github.com/hniedner/ontoprism"),
            Result(0, "feat/x\n"),
            Result(0, ""),
            timeout,
        ]
    )

    with pytest.raises(
        AgentGitProcessError,
        match="outcome is unknown; inspect repository and remote state before retrying",
    ):
        run_agent_git([operation, "feat/x"], tmp_path, runner=runner)


@pytest.mark.parametrize(
    "arguments",
    [
        ["pull-origin"],
        ["pull-origin", "feat/x", "--rebase"],
        ["push-origin", "feat/x", "--force"],
        ["push-origin", "feat/x", "--delete"],
    ],
)
def test_remote_operations_reject_arbitrary_arguments_before_git(
    tmp_path: Path, arguments: list[str]
) -> None:
    def must_not_run(_arguments: list[str], **_kwargs: object) -> Result:
        raise AssertionError("invalid arguments must fail before Git")

    with pytest.raises(AgentGitInputError):
        run_agent_git(arguments, tmp_path, runner=must_not_run)


def _squash_repository(tmp_path: Path) -> str:
    """Leave an unmerged-by-ancestry feature branch and return its tip."""
    initialize_repository(tmp_path)
    git(tmp_path, "switch", "-c", "feat/squashed")
    (tmp_path / "tracked.txt").write_text("feature\n")
    git(tmp_path, "commit", "-am", "feature")
    tip = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "switch", "main")
    return tip


def _github_answers(pulls: object, calls: list[list[str]], returncode: int = 0):
    """Run Git for real; answer only the GitHub pull-request query."""

    def run(arguments: list[str], **kwargs: object) -> object:
        if arguments[0] == "gh":
            calls.append(arguments)
            return Result(returncode, json.dumps(pulls))
        return subprocess.run(arguments, **kwargs)  # noqa: PLW1510, S603

    return run


def _pull(tip: str, *, merged: bool = True, base: str = "main") -> dict[str, object]:
    return {
        "merged_at": "2026-09-22T19:08:16Z" if merged else None,
        "head": {"sha": tip, "ref": "feat/squashed"},
        "base": {"ref": base},
    }


def test_delete_merged_deletes_a_branch_whose_exact_tip_github_squash_merged(
    tmp_path: Path,
) -> None:
    tip = _squash_repository(tmp_path)
    calls: list[list[str]] = []

    result = run_agent_git(
        ["delete-merged", "feat/squashed"],
        tmp_path,
        runner=_github_answers([_pull(tip)], calls),
    )

    assert result == 0
    assert git(tmp_path, "branch", "--list", "feat/squashed") == ""
    assert calls == [
        [
            "gh",
            "api",
            "repos/hniedner/ontoprism/pulls?state=closed&base=main"
            "&head=hniedner:feat/squashed&per_page=100",
        ]
    ]


@pytest.mark.parametrize(
    "pulls",
    [
        [],
        [_pull("b" * 40)],
        [_pull("TIP", merged=False)],
        [_pull("TIP", base="feat/m0-other")],
    ],
    ids=["no-pull", "tip-moved-after-merge", "closed-unmerged", "other-base"],
)
def test_delete_merged_keeps_a_branch_github_did_not_merge_at_its_tip(
    tmp_path: Path, pulls: list[dict[str, object]]
) -> None:
    tip = _squash_repository(tmp_path)
    answered = json.loads(json.dumps(pulls).replace("TIP", tip))

    with pytest.raises(AgentGitInputError, match="not merged"):
        run_agent_git(
            ["delete-merged", "feat/squashed"],
            tmp_path,
            runner=_github_answers(answered, []),
        )

    assert git(tmp_path, "branch", "--list", "feat/squashed").strip("* ") == (
        "feat/squashed"
    )


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        '{"message": "Not Found"}',
        '[{"merged_at": "x", "head": null, "base": {"ref": "main"}}]',
        '[{"merged_at": "x", "head": {"sha": "s"}, "base": "main"}]',
    ],
)
def test_delete_merged_fails_closed_when_github_cannot_answer(
    tmp_path: Path, answer: str
) -> None:
    tip = _squash_repository(tmp_path)
    del tip

    def run(arguments: list[str], **kwargs: object) -> object:
        if arguments[0] == "gh":
            return Result(0, answer)
        return subprocess.run(arguments, **kwargs)  # noqa: PLW1510, S603

    with pytest.raises(AgentGitProcessError, match="GitHub"):
        run_agent_git(["delete-merged", "feat/squashed"], tmp_path, runner=run)
    with pytest.raises(AgentGitProcessError, match="GitHub"):
        run_agent_git(
            ["delete-merged", "feat/squashed"],
            tmp_path,
            runner=_github_answers([], [], returncode=1),
        )
    assert git(tmp_path, "branch", "--list", "feat/squashed") != ""


def test_delete_merged_reports_the_github_cli_exit_code(tmp_path: Path) -> None:
    _squash_repository(tmp_path)

    with pytest.raises(AgentGitProcessError, match=r"gh exit 4"):
        run_agent_git(
            ["delete-merged", "feat/squashed"],
            tmp_path,
            runner=_github_answers([], [], returncode=4),
        )


def test_delete_merged_refuses_when_the_branch_moved_after_the_github_check(
    tmp_path: Path,
) -> None:
    tip = _squash_repository(tmp_path)

    def run(arguments: list[str], **kwargs: object) -> object:
        if arguments[0] == "gh":
            # Another session commits to the branch between the check and delete.
            git(tmp_path, "switch", "feat/squashed")
            (tmp_path / "tracked.txt").write_text("later\n")
            git(tmp_path, "commit", "-am", "later")
            git(tmp_path, "switch", "main")
            return Result(0, json.dumps([_pull(tip)]))
        return subprocess.run(arguments, **kwargs)  # noqa: PLW1510, S603

    with pytest.raises(
        AgentGitInputError, match="changed after the GitHub merge check"
    ):
        run_agent_git(["delete-merged", "feat/squashed"], tmp_path, runner=run)
    assert git(tmp_path, "log", "-1", "--format=%s", "feat/squashed") == "later"


def test_delete_merged_keeps_a_squash_merged_branch_checked_out_elsewhere(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    tip = _squash_repository(repository)
    git(repository, "worktree", "add", str(tmp_path / "other"), "feat/squashed")

    with pytest.raises(AgentGitProcessError):
        run_agent_git(
            ["delete-merged", "feat/squashed"],
            repository,
            runner=_github_answers([_pull(tip)], []),
        )

    assert git(repository, "rev-parse", "feat/squashed") == tip
    assert git(tmp_path / "other", "branch", "--show-current") == "feat/squashed"


def test_delete_merged_removes_the_branch_configuration_with_the_branch(
    tmp_path: Path,
) -> None:
    tip = _squash_repository(tmp_path)
    git(tmp_path, "config", "branch.feat/squashed.remote", "origin")

    run_agent_git(
        ["delete-merged", "feat/squashed"],
        tmp_path,
        runner=_github_answers([_pull(tip)], []),
    )

    assert git(tmp_path, "config", "--get-regexp", "^branch", check=False) == ""
