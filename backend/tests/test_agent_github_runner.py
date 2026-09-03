from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest
from scripts.validation.run_agent_github import (
    AgentGitHubInputError,
    AgentGitHubProcessError,
    run_agent_github,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


class Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def recording_runner(
    results: list[Result], calls: list[tuple[list[str], dict[str, object]]]
) -> object:
    remaining = iter(results)

    def run(arguments: list[str], **kwargs: object) -> Result:
        calls.append((arguments, kwargs))
        return next(remaining)

    return run


def write_body(root: Path, name: str = "body.md") -> Path:
    plans = root / "tmp" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    body = plans / name
    body.write_text("Acceptance body\n", encoding="utf-8")
    return body


def test_issue_create_checks_duplicates_and_labels_then_uses_fixed_api(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = write_body(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [
            Result(0, "[]"),
            Result(0, json.dumps({"name": "governance"})),
            Result(0, json.dumps({"number": 3, "title": "M1"})),
            Result(
                0,
                json.dumps(
                    {
                        "html_url": ("https://github.com/hniedner/ontoprism/issues/7"),
                        "number": 7,
                    }
                ),
            ),
        ],
        calls,
    )

    assert (
        run_agent_github(
            [
                "issue-create",
                "--title",
                "Governed lifecycle",
                "--body-file",
                str(body.relative_to(tmp_path)),
                "--label",
                "governance",
                "--milestone",
                "3",
            ],
            tmp_path,
            runner=runner,
        )
        == 0
    )

    assert calls[0][0] == [
        "gh",
        "api",
        "--method",
        "GET",
        "repos/hniedner/ontoprism/issues",
        "-f",
        "state=all",
        "-f",
        "per_page=100",
        "--paginate",
        "--slurp",
    ]
    assert calls[1][0] == [
        "gh",
        "api",
        "--method",
        "GET",
        "repos/hniedner/ontoprism/labels/governance",
    ]
    assert calls[2][0] == [
        "gh",
        "api",
        "--method",
        "GET",
        "repos/hniedner/ontoprism/milestones/3",
    ]
    assert calls[3][0] == [
        "gh",
        "api",
        "--method",
        "POST",
        "repos/hniedner/ontoprism/issues",
        "--input",
        "-",
    ]
    assert json.loads(str(calls[3][1]["input"])) == {
        "title": "Governed lifecycle",
        "body": "Acceptance body\n",
        "labels": ["governance"],
        "milestone": 3,
    }
    assert all(call[1]["shell"] is False for call in calls)
    assert json.loads(capsys.readouterr().out) == {
        "number": 7,
        "url": "https://github.com/hniedner/ontoprism/issues/7",
    }


def test_issue_create_refuses_an_exact_existing_title(tmp_path: Path) -> None:
    body = write_body(tmp_path)
    runner = recording_runner(
        [Result(0, '[[{"title":"Governed lifecycle","number":4}]]')], []
    )

    with pytest.raises(AgentGitHubInputError, match="issue title already exists"):
        run_agent_github(
            [
                "issue-create",
                "--title",
                "Governed lifecycle",
                "--body-file",
                str(body.relative_to(tmp_path)),
            ],
            tmp_path,
            runner=runner,
        )


@pytest.mark.parametrize(
    "body_path",
    ["README.md", "tmp/plans/missing.md", "../outside.md"],
)
def test_body_files_are_confined_to_existing_regular_tmp_plans_files(
    tmp_path: Path, body_path: str
) -> None:
    (tmp_path / "README.md").write_text("not a plan", encoding="utf-8")

    def must_not_run(_arguments: list[str], **_kwargs: object) -> Result:
        raise AssertionError("invalid local input must fail before GitHub access")

    with pytest.raises(AgentGitHubInputError, match="body file"):
        run_agent_github(
            ["issue-comment", "4", "--body-file", body_path],
            tmp_path,
            runner=must_not_run,
        )


def test_body_file_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    plans = tmp_path / "tmp" / "plans"
    plans.mkdir(parents=True)
    (plans / "escape.md").symlink_to(outside)

    with pytest.raises(AgentGitHubInputError, match="body file"):
        run_agent_github(
            ["issue-comment", "4", "--body-file", "tmp/plans/escape.md"],
            tmp_path,
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ["issue-delete", "1"],
        ["milestone-delete", "1"],
        ["api", "repos/hniedner/ontoprism/hooks"],
        ["issue-close", "0"],
        ["issue-reopen", "-1"],
        ["issue-edit", "1", "--unknown", "x"],
        ["milestone-create", "--title", "bad\ntitle"],
        ["issue-create", "--title", "x"],
    ],
)
def test_wrapper_rejects_deletion_arbitrary_endpoints_and_invalid_arguments(
    tmp_path: Path, arguments: list[str]
) -> None:
    with pytest.raises(AgentGitHubInputError):
        run_agent_github(arguments, tmp_path)


@pytest.mark.parametrize(
    "arguments",
    [
        ["issue-create", "--title", "x", "--body-file", "tmp/plans/x.md"],
        ["issue-edit", "1", "--title", "x"],
        ["issue-comment", "1", "--body-file", "tmp/plans/x.md"],
        ["issue-close", "1"],
        ["issue-reopen", "1"],
        ["milestone-create", "--title", "x"],
        ["milestone-edit", "1", "--title", "x"],
        ["milestone-close", "1"],
        ["milestone-reopen", "1"],
    ],
)
def test_read_only_entrypoint_rejects_every_mutation(
    tmp_path: Path, arguments: list[str]
) -> None:
    with pytest.raises(AgentGitHubInputError, match="read-only"):
        run_agent_github(arguments, tmp_path, read_only=True)


@pytest.mark.parametrize(
    ("arguments", "endpoint"),
    [
        (["issue-view", "12"], "repos/hniedner/ontoprism/issues/12"),
        (["issue-list", "--state", "open"], "repos/hniedner/ontoprism/issues"),
        (["milestone-list", "--state", "all"], "repos/hniedner/ontoprism/milestones"),
        (["pr-view", "9"], "repos/hniedner/ontoprism/pulls/9"),
        (["run-list", "--branch", "main"], "repos/hniedner/ontoprism/actions/runs"),
    ],
)
def test_read_only_entrypoint_supports_fixed_repository_reads(
    tmp_path: Path, arguments: list[str], endpoint: str
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    if arguments[0] in {"issue-view", "pr-view"}:
        response = "{}"
    elif arguments[0] == "run-list":
        response = '{"workflow_runs":[]}'
    else:
        response = "[]"
    runner = recording_runner([Result(0, response)], calls)

    assert run_agent_github(arguments, tmp_path, read_only=True, runner=runner) == 0

    assert calls[0][0][:5] == ["gh", "api", "--method", "GET", endpoint]
    assert calls[0][1]["shell"] is False


@pytest.mark.parametrize(
    ("arguments", "method", "endpoint", "payload"),
    [
        (
            ["issue-close", "8"],
            "PATCH",
            "repos/hniedner/ontoprism/issues/8",
            {"state": "closed"},
        ),
        (
            ["issue-reopen", "8"],
            "PATCH",
            "repos/hniedner/ontoprism/issues/8",
            {"state": "open"},
        ),
        (
            ["milestone-close", "2"],
            "PATCH",
            "repos/hniedner/ontoprism/milestones/2",
            {"state": "closed"},
        ),
        (
            ["milestone-reopen", "2"],
            "PATCH",
            "repos/hniedner/ontoprism/milestones/2",
            {"state": "open"},
        ),
    ],
)
def test_close_and_reopen_use_only_fixed_lifecycle_payloads(
    tmp_path: Path,
    arguments: list[str],
    method: str,
    endpoint: str,
    payload: dict[str, str],
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [
            Result(0, '{"html_url":"https://example.invalid/item","number":8}'),
            Result(0, '{"html_url":"https://example.invalid/item","number":8}'),
        ],
        calls,
    )

    assert run_agent_github(arguments, tmp_path, runner=runner) == 0

    assert calls[1][0] == ["gh", "api", "--method", method, endpoint, "--input", "-"]
    assert json.loads(str(calls[1][1]["input"])) == payload


def test_issue_edit_supports_labels_assignees_and_milestone_removal(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [
            Result(
                0,
                json.dumps(
                    {
                        "number": 8,
                        "labels": [{"name": "stale"}],
                        "assignees": [{"login": "old-user"}],
                    }
                ),
            ),
            Result(0, '{"name":"bug"}'),
            Result(0, '{"login":"octocat"}'),
            Result(0, '{"html_url":"https://example.invalid/8","number":8}'),
        ],
        calls,
    )

    assert (
        run_agent_github(
            [
                "issue-edit",
                "8",
                "--add-label",
                "bug",
                "--remove-label",
                "stale",
                "--add-assignee",
                "octocat",
                "--remove-assignee",
                "old-user",
                "--milestone",
                "none",
            ],
            tmp_path,
            runner=runner,
        )
        == 0
    )

    assert json.loads(str(calls[-1][1]["input"])) == {
        "labels": ["bug"],
        "assignees": ["octocat"],
        "milestone": None,
    }


def test_milestone_create_checks_duplicate_before_mutation(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [
            Result(0, "[[]]"),
            Result(0, '{"html_url":"https://example.invalid/milestone/3","number":3}'),
        ],
        calls,
    )

    assert (
        run_agent_github(
            ["milestone-create", "--title", "M2", "--due-on", "2026-09-30"],
            tmp_path,
            runner=runner,
        )
        == 0
    )
    assert calls[0][0][4] == "repos/hniedner/ontoprism/milestones"
    assert json.loads(str(calls[1][1]["input"])) == {
        "title": "M2",
        "due_on": "2026-09-30T23:59:59Z",
    }


def test_subprocess_failure_and_timeout_do_not_expose_gh_output(tmp_path: Path) -> None:
    sensitive_output = "credential-like subprocess output"
    runner = recording_runner([Result(1, sensitive_output, sensitive_output)], [])

    with pytest.raises(AgentGitHubProcessError) as failed:
        run_agent_github(["issue-view", "1"], tmp_path, runner=runner)
    assert sensitive_output not in str(failed.value)

    def timeout(_arguments: list[str], **_kwargs: object) -> Result:
        raise subprocess.TimeoutExpired(
            ["gh"], 10, output=sensitive_output, stderr=sensitive_output
        )

    with pytest.raises(AgentGitHubProcessError) as timed_out:
        run_agent_github(["issue-view", "1"], tmp_path, runner=timeout)
    assert sensitive_output not in str(timed_out.value)


def test_read_output_exposes_only_the_documented_issue_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = recording_runner(
        [
            Result(
                0,
                json.dumps(
                    {
                        "number": 4,
                        "title": "Visible",
                        "state": "open",
                        "html_url": "https://example.invalid/4",
                        "body": "Public issue body",
                        "authorization": "must-not-leak",
                        "user": {"token": "must-not-leak"},
                    }
                ),
            )
        ],
        [],
    )

    assert run_agent_github(["issue-view", "4"], tmp_path, runner=runner) == 0

    assert json.loads(capsys.readouterr().out) == {
        "body": "Public issue body",
        "number": 4,
        "state": "open",
        "title": "Visible",
        "url": "https://example.invalid/4",
    }
