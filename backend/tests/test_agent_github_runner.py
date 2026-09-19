from __future__ import annotations

import inspect
import json
import subprocess
from typing import TYPE_CHECKING

import pytest
import scripts.validation.run_agent_github as agent_github
from scripts.validation.run_agent_github import (
    PROTECTED_BRANCHES,
    AgentGitHubInputError,
    AgentGitHubProcessError,
    run_agent_github,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_github_runner_docstring_includes_pull_request_mutations() -> None:
    normalized = " ".join((agent_github.__doc__ or "").split())

    assert "pull-request create/edit mutations" in normalized


def test_github_runner_requires_an_explicit_read_only_boundary() -> None:
    parameter = inspect.signature(run_agent_github).parameters["read_only"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_github_protected_branch_contract_is_explicit() -> None:
    assert frozenset({"main", "master"}) == PROTECTED_BRANCHES


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
            read_only=False,
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
            read_only=False,
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
            read_only=False,
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
            read_only=False,
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
        run_agent_github(arguments, tmp_path, read_only=False)


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

    assert run_agent_github(arguments, tmp_path, read_only=False, runner=runner) == 0

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
            read_only=False,
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
            read_only=False,
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
        run_agent_github(["issue-view", "1"], tmp_path, read_only=False, runner=runner)
    assert sensitive_output not in str(failed.value)

    def timeout(_arguments: list[str], **_kwargs: object) -> Result:
        raise subprocess.TimeoutExpired(
            ["gh"], 10, output=sensitive_output, stderr=sensitive_output
        )

    with pytest.raises(AgentGitHubProcessError) as timed_out:
        run_agent_github(["issue-view", "1"], tmp_path, read_only=False, runner=timeout)
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
                        "milestone": {
                            "number": 16,
                            "title": "R0",
                            "html_url": "https://example.invalid/milestone/16",
                            "creator": {"token": "must-not-leak"},
                        },
                    }
                ),
            )
        ],
        [],
    )

    assert (
        run_agent_github(["issue-view", "4"], tmp_path, read_only=False, runner=runner)
        == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "body": "Public issue body",
        "milestone": {
            "number": 16,
            "title": "R0",
            "url": "https://example.invalid/milestone/16",
        },
        "number": 4,
        "state": "open",
        "title": "Visible",
        "url": "https://example.invalid/4",
    }


def test_list_reads_expose_what_placing_an_issue_in_a_milestone_needs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fixtures copy two traits of GitHub's responses: every issue and milestone
    object, the nested milestone included, carries ``html_url``, and fields outside the
    documented set sit beside the documented ones (here made-up credential-shaped
    values). The output is the documented fields only."""
    milestone = {
        "number": 16,
        "title": "R0",
        "state": "open",
        "due_on": None,
        "description": "Order: #389, then #340.",
        "html_url": "https://example.invalid/milestone/16",
        "creator": {"token": "must-not-leak"},
    }
    placed = {
        "number": 7,
        "title": "Placed",
        "state": "open",
        "created_at": "2026-09-01T10:00:00Z",
        "html_url": "https://example.invalid/7",
        "milestone": milestone,
        "labels": [{"name": "epic", "color": "ededed"}],
        "user": {"token": "must-not-leak"},
    }
    unplaced = {
        **placed,
        "number": 9,
        "title": "Unlabelled",
        "milestone": None,
        "labels": None,
    }

    for operation, items in (
        ("issue-list", [placed, unplaced]),
        ("milestone-list", [milestone]),
    ):
        runner = recording_runner([Result(0, json.dumps([items]))], [])
        assert (
            run_agent_github([operation], tmp_path, read_only=True, runner=runner) == 0
        )

    issues, milestones = (
        json.loads(line) for line in capsys.readouterr().out.splitlines()
    )
    assert issues == [
        {
            "number": 7,
            "title": "Placed",
            "state": "open",
            "created_at": "2026-09-01T10:00:00Z",
            "url": "https://example.invalid/7",
            "labels": ["epic"],
            "milestone": {
                "number": 16,
                "title": "R0",
                "url": "https://example.invalid/milestone/16",
            },
        },
        {
            "number": 9,
            "title": "Unlabelled",
            "state": "open",
            "created_at": "2026-09-01T10:00:00Z",
            "url": "https://example.invalid/7",
            "milestone": None,
        },
    ]
    assert milestones == [
        {
            "number": 16,
            "title": "R0",
            "state": "open",
            "due_on": None,
            "description": "Order: #389, then #340.",
            "url": "https://example.invalid/milestone/16",
        }
    ]


def test_a_list_read_returns_every_page_and_counts_a_limit_in_issues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A search for an existing issue must see the whole tracker: GitHub pages its
    lists and mixes pull requests into the issue list."""
    pull = {"number": 3, "title": "PR", "state": "open", "pull_request": {}}
    pages = [
        [pull, {"number": 2, "title": "second", "state": "open"}],
        [{"number": 1, "title": "first", "state": "open"}],
    ]
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [Result(0, json.dumps(pages)), Result(0, json.dumps(pages))], calls
    )

    assert (
        run_agent_github(["issue-list"], tmp_path, read_only=True, runner=runner) == 0
    )
    assert (
        run_agent_github(
            ["issue-list", "--limit", "1"], tmp_path, read_only=True, runner=runner
        )
        == 0
    )

    assert calls[0][0][-2:] == ["--paginate", "--slurp"]
    everything, limited = (
        [item["number"] for item in json.loads(line)]
        for line in capsys.readouterr().out.splitlines()
    )
    assert everything == [2, 1]
    assert limited == [2]


def test_a_limit_shortens_a_milestone_list_and_has_no_upper_bound(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    milestones = [
        {"number": n, "title": f"M{n}", "state": "open"} for n in range(1, 151)
    ]
    pages = json.dumps([milestones[:100], milestones[100:]])
    runner = recording_runner([Result(0, pages), Result(0, pages)], [])

    for limit in ("2", "120"):
        assert (
            run_agent_github(
                ["milestone-list", "--limit", limit],
                tmp_path,
                read_only=True,
                runner=runner,
            )
            == 0
        )

    short, long = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert [item["number"] for item in short] == [1, 2]
    assert len(long) == 120


@pytest.mark.parametrize(
    "arguments",
    [
        ["issue-list", "--limit", "0"],
        ["issue-list", "--limit", "-1"],
        ["milestone-list", "--limit", "x"],
        ["issue-list", "--state", "merged"],
        ["issue-comments"],
        ["issue-comments", "397", "398"],
        ["issue-comments", "0"],
        ["issue-comments", "-3"],
    ],
)
def test_list_and_comment_reads_refuse_invalid_arguments_before_any_call(
    tmp_path: Path, arguments: list[str]
) -> None:
    """``issue-list --limit 0`` printing ``[]`` would read as "no matching issue" to a
    duplicate search."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    with pytest.raises(AgentGitHubInputError):
        run_agent_github(
            arguments, tmp_path, read_only=True, runner=recording_runner([], calls)
        )

    assert calls == []


@pytest.mark.parametrize("state", ["open", "closed", "all"])
def test_a_list_read_asks_github_for_the_requested_state(
    tmp_path: Path, state: str
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner([Result(0, "[]")], calls)

    assert (
        run_agent_github(
            ["issue-list", "--state", state], tmp_path, read_only=True, runner=runner
        )
        == 0
    )

    assert f"state={state}" in calls[0][0]


@pytest.mark.parametrize("operation", [["issue-list"], ["issue-view", "4"]])
@pytest.mark.parametrize(
    "labels", ["epic", {"name": "epic"}, ["epic"], [{"color": "x"}]]
)
def test_an_issue_whose_labels_are_malformed_is_refused(
    tmp_path: Path, labels: object, operation: list[str]
) -> None:
    """A dropped label would make an epic look like an ordinary issue to the steward;
    malformed source data fails closed instead, naming the issue."""
    issue = {"number": 4, "title": "x", "state": "open", "labels": labels}
    payload = [[issue]] if operation[0] == "issue-list" else issue
    runner = recording_runner([Result(0, json.dumps(payload))], [])

    with pytest.raises(AgentGitHubProcessError, match="issue 4 labels are invalid"):
        run_agent_github(operation, tmp_path, read_only=True, runner=runner)


@pytest.mark.parametrize(
    ("key", "listed", "edit"),
    [
        ("labels", [{"name": "epic"}, {"color": "x"}], ["--remove-label", "epic"]),
        ("labels", None, ["--add-label", "bug"]),
        ("assignees", [{"login": "a"}, {"id": 1}], ["--remove-assignee", "a"]),
        ("assignees", None, ["--add-assignee", "octocat"]),
    ],
)
def test_an_edit_never_rewrites_a_list_it_could_not_read(
    tmp_path: Path, key: str, listed: object, edit: list[str]
) -> None:
    """A label or assignee edit sends the full list back; an entry it could not read,
    or a null list, would be deleted from the issue, so it refuses before any write."""
    current = {"number": 8, key: listed}
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner([Result(0, json.dumps(current))], calls)

    with pytest.raises(AgentGitHubProcessError, match=f"issue 8 {key} are invalid"):
        run_agent_github(
            ["issue-edit", "8", *edit], tmp_path, read_only=False, runner=runner
        )

    assert [call[0][3] for call in calls] == ["GET"]


def test_an_edit_refuses_to_remove_a_label_the_issue_does_not_have(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [Result(0, '{"number":8,"labels":[{"name":"a"}],"assignees":[]}')], calls
    )

    with pytest.raises(AgentGitHubInputError, match="label absent from the issue"):
        run_agent_github(
            ["issue-edit", "8", "--remove-label", "b"],
            tmp_path,
            read_only=False,
            runner=runner,
        )

    assert [call[0][3] for call in calls] == ["GET"]


def test_an_edit_that_changes_no_list_does_not_need_one(tmp_path: Path) -> None:
    """Only a label or assignee edit needs those lists; a title edit on an issue
    without them still goes through."""
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [
            Result(0, '{"number":8,"labels":null,"assignees":null}'),
            Result(0, "[[]]"),
            Result(0, '{"html_url":"https://example.invalid/8","number":8}'),
        ],
        calls,
    )

    assert (
        run_agent_github(
            ["issue-edit", "8", "--title", "x"],
            tmp_path,
            read_only=False,
            runner=runner,
        )
        == 0
    )
    assert json.loads(str(calls[-1][1]["input"])) == {"title": "x"}


def test_an_issue_view_leaves_out_a_list_github_did_not_send(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = {"number": 4, "title": "x", "state": "open", "labels": None}
    runner = recording_runner([Result(0, json.dumps(issue))], [])

    assert (
        run_agent_github(["issue-view", "4"], tmp_path, read_only=True, runner=runner)
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "number": 4,
        "title": "x",
        "state": "open",
        "milestone": None,
    }


@pytest.mark.parametrize("assignees", ["a", {"login": "a"}, ["a"], [{"id": 1}]])
def test_an_issue_view_refuses_malformed_assignees(
    tmp_path: Path, assignees: object
) -> None:
    issue = {"number": 4, "title": "x", "state": "open", "assignees": assignees}
    runner = recording_runner([Result(0, json.dumps(issue))], [])

    with pytest.raises(AgentGitHubProcessError, match="issue 4 assignees are invalid"):
        run_agent_github(["issue-view", "4"], tmp_path, read_only=True, runner=runner)


def test_an_issue_whose_milestone_is_not_an_object_is_refused(tmp_path: Path) -> None:
    runner = recording_runner(
        [Result(0, '{"number":4,"title":"x","state":"open","milestone":"R0"}')], []
    )

    with pytest.raises(AgentGitHubProcessError, match="milestone"):
        run_agent_github(["issue-view", "4"], tmp_path, read_only=True, runner=runner)


@pytest.mark.parametrize(
    "arguments",
    [
        ["issue-view", "390"],
        ["issue-comments", "390"],
        ["issue-comment", "390", "--body-file", "tmp/plans/body.md"],
        ["issue-close", "390"],
        ["issue-edit", "390", "--title", "x"],
    ],
)
def test_issue_operations_refuse_a_pull_request_number_before_any_write(
    tmp_path: Path, arguments: list[str]
) -> None:
    """GitHub serves a pull request on the issues endpoint; a mistyped number must not
    hand an agent a PR as "the issue the PR implements", nor write to one."""
    write_body(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [Result(0, '{"number":390,"title":"x","state":"open","pull_request":{}}')],
        calls,
    )

    with pytest.raises(AgentGitHubInputError, match="#390 is a pull request"):
        run_agent_github(arguments, tmp_path, read_only=False, runner=runner)

    assert [call[0][3] for call in calls] == ["GET"]


def test_issue_comments_are_readable_because_findings_are_parked_there(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    comment = {
        "body": "Also: the seal is not shielded.",
        "created_at": "2026-09-19T08:00:00Z",
        "html_url": "https://example.invalid/397#c1",
        "user": {"login": "someone", "token": "must-not-leak"},
    }
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [Result(0, '{"number":397}'), Result(0, json.dumps([[comment], [comment]]))],
        calls,
    )

    assert (
        run_agent_github(
            ["issue-comments", "397"], tmp_path, read_only=True, runner=runner
        )
        == 0
    )

    assert calls[1][0][:5] == [
        "gh",
        "api",
        "--method",
        "GET",
        "repos/hniedner/ontoprism/issues/397/comments",
    ]
    assert calls[1][0][-2:] == ["--paginate", "--slurp"]
    assert (
        json.loads(capsys.readouterr().out)
        == [
            {
                "body": "Also: the seal is not shielded.",
                "created_at": "2026-09-19T08:00:00Z",
                "url": "https://example.invalid/397#c1",
            }
        ]
        * 2
    )


def test_pr_create_checks_duplicate_head_then_posts_fixed_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = write_body(tmp_path, "pr.md")
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [
            Result(0, "[]"),
            Result(
                0,
                json.dumps(
                    {
                        "number": 42,
                        "html_url": "https://github.com/hniedner/ontoprism/pull/42",
                    }
                ),
            ),
        ],
        calls,
    )

    assert (
        run_agent_github(
            [
                "pr-create",
                "--title",
                "chore: govern remote operations",
                "--body-file",
                str(body.relative_to(tmp_path)),
                "--head",
                "chore/remote-operations",
            ],
            tmp_path,
            read_only=False,
            runner=runner,
        )
        == 0
    )

    endpoint = calls[0][0][4]
    owner = endpoint.split("/")[1]
    assert calls[0][0] == [
        "gh",
        "api",
        "--method",
        "GET",
        "repos/hniedner/ontoprism/pulls",
        "-f",
        "state=open",
        "-f",
        f"head={owner}:chore/remote-operations",
        "-f",
        "per_page=100",
        "--paginate",
        "--slurp",
    ]
    assert calls[1][0] == [
        "gh",
        "api",
        "--method",
        "POST",
        "repos/hniedner/ontoprism/pulls",
        "--input",
        "-",
    ]
    assert json.loads(str(calls[1][1]["input"])) == {
        "title": "chore: govern remote operations",
        "body": "Acceptance body\n",
        "head": "chore/remote-operations",
        "base": "main",
    }
    assert json.loads(capsys.readouterr().out) == {
        "number": 42,
        "url": "https://github.com/hniedner/ontoprism/pull/42",
    }


def test_pr_create_refuses_duplicate_open_pr_for_head(tmp_path: Path) -> None:
    body = write_body(tmp_path, "pr.md")
    runner = recording_runner(
        [
            Result(
                0,
                json.dumps(
                    [
                        [
                            {
                                "number": 41,
                                "state": "open",
                                "merged_at": None,
                                "base": {"repo": {"full_name": "hniedner/ontoprism"}},
                                "head": {
                                    "ref": "feat/x",
                                    "repo": {"full_name": "hniedner/ontoprism"},
                                },
                            }
                        ]
                    ]
                ),
            )
        ],
        [],
    )

    with pytest.raises(AgentGitHubInputError, match="open pull request already exists"):
        run_agent_github(
            [
                "pr-create",
                "--title",
                "feat: x",
                "--body-file",
                str(body.relative_to(tmp_path)),
                "--head",
                "feat/x",
            ],
            tmp_path,
            read_only=False,
            runner=runner,
        )


def test_pr_create_targets_a_milestone_branch_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = write_body(tmp_path, "pr.md")
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [
            Result(0, "[]"),
            Result(
                0,
                json.dumps(
                    {
                        "number": 43,
                        "html_url": "https://github.com/hniedner/ontoprism/pull/43",
                    }
                ),
            ),
        ],
        calls,
    )

    assert (
        run_agent_github(
            [
                "pr-create",
                "--title",
                "feat(decomposition): preflight full runs",
                "--body-file",
                str(body.relative_to(tmp_path)),
                "--head",
                "feat/fail-fast-full-runs-344",
                "--base",
                "feat/m1-6-1-provisional-publication",
            ],
            tmp_path,
            read_only=False,
            runner=runner,
        )
        == 0
    )

    assert json.loads(str(calls[1][1]["input"]))["base"] == (
        "feat/m1-6-1-provisional-publication"
    )
    assert json.loads(capsys.readouterr().out)["number"] == 43


@pytest.mark.parametrize(
    "base",
    ["dev", "feat/other-branch", "feat/milestone-x", "feat/m1-6-1/../main", "MAIN"],
)
def test_pr_create_rejects_a_base_that_is_not_main_or_a_milestone_branch(
    tmp_path: Path, base: str
) -> None:
    body = write_body(tmp_path, "pr.md")

    def must_not_run(_arguments: list[str], **_kwargs: object) -> Result:
        raise AssertionError("invalid base must fail before GitHub access")

    with pytest.raises(AgentGitHubInputError, match="base branch"):
        run_agent_github(
            [
                "pr-create",
                "--title",
                "feat: x",
                "--body-file",
                str(body.relative_to(tmp_path)),
                "--head",
                "feat/x",
                "--base",
                base,
            ],
            tmp_path,
            read_only=False,
            runner=must_not_run,
        )


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "master",
        "bad..branch",
        "bad\nbranch",
        "feat/trailing/",
        "feat/double//slash",
        "feat/name.lock",
        "feat/trailing.",
    ],
)
def test_pr_create_rejects_protected_or_invalid_head_before_network(
    tmp_path: Path, branch: str
) -> None:
    body = write_body(tmp_path, "pr.md")

    def must_not_run(_arguments: list[str], **_kwargs: object) -> Result:
        raise AssertionError("invalid local input must fail before GitHub access")

    with pytest.raises(AgentGitHubInputError, match="head branch"):
        run_agent_github(
            [
                "pr-create",
                "--title",
                "feat: x",
                "--body-file",
                str(body.relative_to(tmp_path)),
                "--head",
                branch,
            ],
            tmp_path,
            read_only=False,
            runner=must_not_run,
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ["pr-create", "--title", "feat: x", "--head", "feat/x"],
        ["pr-create", "--title", "feat: x", "--body-file", "x", "--base", "dev"],
        ["pr-edit", "12"],
        ["pr-edit", "12", "--state", "closed"],
        ["pr-edit", "12", "--head", "other"],
        ["pr-edit", "12", "--base", "dev"],
        ["pr-edit", "12", "--repo", "other/repo"],
    ],
)
def test_pr_mutations_reject_missing_or_arbitrary_arguments_before_network(
    tmp_path: Path, arguments: list[str]
) -> None:
    def must_not_run(_arguments: list[str], **_kwargs: object) -> Result:
        raise AssertionError("invalid arguments must fail before GitHub access")

    with pytest.raises(AgentGitHubInputError):
        run_agent_github(arguments, tmp_path, read_only=False, runner=must_not_run)


@pytest.mark.parametrize(
    ("arguments", "payload"),
    [
        (["pr-edit", "12", "--title", "fix: corrected"], {"title": "fix: corrected"}),
        (
            ["pr-edit", "12", "--body-file", "tmp/plans/pr.md"],
            {"body": "Acceptance body\n"},
        ),
    ],
)
def test_pr_edit_verifies_repo_target_then_patches_only_title_or_body(
    tmp_path: Path, arguments: list[str], payload: dict[str, str]
) -> None:
    write_body(tmp_path, "pr.md")
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner(
        [
            Result(
                0,
                json.dumps(
                    {
                        "number": 12,
                        "state": "open",
                        "merged": False,
                        "merged_at": None,
                        "base": {"repo": {"full_name": "hniedner/ontoprism"}},
                    }
                ),
            ),
            Result(
                0,
                '{"number":12,"html_url":"https://github.com/hniedner/ontoprism/pull/12"}',
            ),
        ],
        calls,
    )

    assert run_agent_github(arguments, tmp_path, read_only=False, runner=runner) == 0
    expected_endpoint = "repos/hniedner/ontoprism/pulls/12"
    assert calls[0][0] == ["gh", "api", "--method", "GET", expected_endpoint]
    assert calls[1][0] == [
        "gh",
        "api",
        "--method",
        "PATCH",
        expected_endpoint,
        "--input",
        "-",
    ]
    assert json.loads(str(calls[1][1]["input"])) == payload


@pytest.mark.parametrize(
    "pull",
    [
        {
            "number": 12,
            "state": "closed",
            "merged": False,
            "merged_at": None,
            "base": {"repo": {"full_name": "hniedner/ontoprism"}},
        },
        {
            "number": 12,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-09-04T12:00:00Z",
            "base": {"repo": {"full_name": "hniedner/ontoprism"}},
        },
    ],
)
def test_pr_edit_refuses_closed_or_merged_pr_before_patch(
    tmp_path: Path, pull: dict[str, object]
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    runner = recording_runner([Result(0, json.dumps(pull))], calls)

    with pytest.raises(AgentGitHubInputError, match="open pull request"):
        run_agent_github(
            ["pr-edit", "12", "--title", "fix: corrected"],
            tmp_path,
            read_only=False,
            runner=runner,
        )

    assert len(calls) == 1


def test_pr_create_rejects_malformed_duplicate_response_shape(tmp_path: Path) -> None:
    body = write_body(tmp_path, "pr.md")
    runner = recording_runner([Result(0, '[[{"number":41}]]')], [])

    with pytest.raises(
        AgentGitHubProcessError, match="pull request response is invalid"
    ):
        run_agent_github(
            [
                "pr-create",
                "--title",
                "feat: x",
                "--body-file",
                str(body.relative_to(tmp_path)),
                "--head",
                "feat/x",
            ],
            tmp_path,
            read_only=False,
            runner=runner,
        )


def test_pr_edit_refuses_response_from_unrelated_repository(tmp_path: Path) -> None:
    runner = recording_runner(
        [Result(0, '{"number":12,"base":{"repo":{"full_name":"other/repo"}}}')], []
    )

    with pytest.raises(
        AgentGitHubProcessError, match="pull request response is invalid"
    ):
        run_agent_github(
            ["pr-edit", "12", "--title", "fix: corrected"],
            tmp_path,
            read_only=False,
            runner=runner,
        )


@pytest.mark.parametrize("operation", ["pr-create", "pr-edit"])
def test_read_only_entrypoint_rejects_pr_mutations(
    tmp_path: Path, operation: str
) -> None:
    arguments = (
        [operation, "1", "--title", "fix: x"]
        if operation == "pr-edit"
        else [operation, "--title", "feat: x", "--body-file", "x", "--head", "feat/x"]
    )
    with pytest.raises(AgentGitHubInputError, match="read-only"):
        run_agent_github(arguments, tmp_path, read_only=True)


def test_pr_mutation_timeout_fails_closed_with_inspection_instruction(
    tmp_path: Path,
) -> None:
    calls = 0

    def runner(_arguments: list[str], **_kwargs: object) -> Result:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Result(
                0,
                '{"number":12,"state":"open","merged":false,"merged_at":null,'
                '"base":{"repo":{"full_name":"hniedner/ontoprism"}}}',
            )
        raise subprocess.TimeoutExpired(["gh"], 120)

    with pytest.raises(
        AgentGitHubProcessError,
        match="outcome is unknown; inspect the repository before retrying",
    ):
        run_agent_github(
            ["pr-edit", "12", "--title", "fix: corrected"],
            tmp_path,
            read_only=False,
            runner=runner,
        )


@pytest.mark.parametrize("operation", ["pr-create", "pr-edit"])
def test_pr_mutations_reject_malformed_success_response(
    tmp_path: Path, operation: str
) -> None:
    body = write_body(tmp_path, "pr.md")
    results = (
        [Result(0, "[]"), Result(0, "{}")]
        if operation == "pr-create"
        else [
            Result(
                0,
                '{"number":12,"state":"open","merged":false,"merged_at":null,'
                '"base":{"repo":{"full_name":"hniedner/ontoprism"}}}',
            ),
            Result(0, "{}"),
        ]
    )
    arguments = (
        [operation, "12", "--title", "fix: x"]
        if operation == "pr-edit"
        else [
            operation,
            "--title",
            "feat: x",
            "--body-file",
            str(body.relative_to(tmp_path)),
            "--head",
            "feat/x",
        ]
    )

    with pytest.raises(
        AgentGitHubProcessError,
        match="mutation response is invalid; inspect the repository before retrying",
    ):
        run_agent_github(
            arguments,
            tmp_path,
            read_only=False,
            runner=recording_runner(results, []),
        )
