#!/usr/bin/env python3
"""Run repository-scoped GitHub issue, milestone, and read operations."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

REPOSITORY = "hniedner/ontoprism"
API_ROOT = f"repos/{REPOSITORY}"
READ_OPERATIONS = frozenset(
    {"issue-view", "issue-list", "milestone-list", "pr-view", "run-list"}
)
MUTATION_OPERATIONS = frozenset(
    {
        "issue-create",
        "issue-edit",
        "issue-comment",
        "issue-close",
        "issue-reopen",
        "milestone-create",
        "milestone-edit",
        "milestone-close",
        "milestone-reopen",
        "pr-create",
        "pr-edit",
    }
)
PROCESS_TIMEOUT_SECONDS = 30
MUTATION_TIMEOUT_SECONDS = 120
MAX_BODY_BYTES = 1_000_000
MAX_TITLE_LENGTH = 256
MAX_NAME_LENGTH = 100
MAX_LIST_LIMIT = 100
MAX_GITHUB_NUMBER = 2_147_483_647
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
SAFE_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")


class AgentGitHubInputError(ValueError):
    """The requested GitHub operation is outside the repository contract."""


class AgentGitHubProcessError(RuntimeError):
    """A fixed, sanitized GitHub CLI or response failure."""


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(self, arguments: list[str], **kwargs: object) -> CommandResult: ...


def _subprocess_runner(arguments: list[str], **kwargs: object) -> CommandResult:
    return subprocess.run(  # noqa: S603, PLW1510 - fixed gh argv, shell disabled
        arguments,
        **kwargs,  # type: ignore[arg-type,return-value]
    )


def _validate_text(value: str, label: str, *, maximum: int) -> str:
    if (
        not value.strip()
        or len(value) > maximum
        or any(not character.isprintable() for character in value)
    ):
        raise AgentGitHubInputError(f"{label} is invalid")
    return value


def _positive_number(value: str, label: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise AgentGitHubInputError(f"{label} is invalid")
    number = int(value)
    if number < 1 or number > MAX_GITHUB_NUMBER:
        raise AgentGitHubInputError(f"{label} is invalid")
    return number


def _safe_name(value: str, label: str) -> str:
    if SAFE_NAME.fullmatch(value) is None:
        raise AgentGitHubInputError(f"{label} is invalid")
    return value


def _safe_branch(value: str, label: str) -> str:
    components = value.split("/")
    if (
        SAFE_BRANCH.fullmatch(value) is None
        or ".." in value
        or "@{" in value
        or value in {"main", "master"}
        or any(
            not component
            or component.endswith(".")
            or component.casefold().endswith(".lock")
            for component in components
        )
    ):
        raise AgentGitHubInputError(f"{label} is invalid")
    return value


def _body_file(root: Path, value: str) -> str:
    candidate = Path(value)
    if not value or candidate.is_symlink():
        raise AgentGitHubInputError("body file is invalid")
    plans = (root / "tmp" / "plans").resolve()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(plans)
    except ValueError as exc:
        raise AgentGitHubInputError("body file must be under tmp/plans") from exc
    try:
        if not resolved.is_file() or resolved.stat().st_size > MAX_BODY_BYTES:
            raise AgentGitHubInputError("body file is invalid")
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentGitHubInputError("body file is invalid") from exc


def _flags(
    arguments: list[str],
    *,
    singles: frozenset[str] = frozenset(),
    multiples: frozenset[str] = frozenset(),
    switches: frozenset[str] = frozenset(),
) -> dict[str, object]:
    parsed: dict[str, object] = {}
    index = 0
    while index < len(arguments):
        flag = arguments[index]
        if flag in switches:
            if flag in parsed:
                raise AgentGitHubInputError(f"duplicate option {flag}")
            parsed[flag] = True
            index += 1
            continue
        if flag not in singles and flag not in multiples:
            raise AgentGitHubInputError("GitHub operation arguments are invalid")
        if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
            raise AgentGitHubInputError(f"{flag} requires one value")
        value = arguments[index + 1]
        if flag in multiples:
            existing = parsed.setdefault(flag, [])
            if not isinstance(existing, list):
                raise AgentGitHubInputError("GitHub operation arguments are invalid")
            existing.append(value)
        elif flag in parsed:
            raise AgentGitHubInputError(f"duplicate option {flag}")
        else:
            parsed[flag] = value
        index += 2
    return parsed


def _multiple(options: dict[str, object], name: str) -> list[str]:
    value = options.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AgentGitHubInputError("GitHub operation arguments are invalid")
    return value


def _invoke(
    arguments: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    payload: dict[str, object] | None = None,
    mutating: bool = False,
) -> Any:
    kwargs: dict[str, object] = {
        "cwd": root,
        "capture_output": True,
        "text": True,
        "shell": False,
        "check": False,
        "timeout": MUTATION_TIMEOUT_SECONDS if mutating else PROCESS_TIMEOUT_SECONDS,
    }
    if payload is not None:
        kwargs["input"] = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    try:
        result = runner(arguments, **kwargs)
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        message = (
            "GitHub mutation outcome is unknown; inspect the repository before retrying"
            if mutating
            else "GitHub read operation failed"
        )
        raise AgentGitHubProcessError(message) from exc
    if result.returncode != 0:
        message = (
            "GitHub mutation failed; inspect the repository before retrying"
            if mutating
            else "GitHub read operation failed"
        )
        raise AgentGitHubProcessError(message)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        message = (
            "GitHub mutation returned an invalid response; inspect the repository"
            if mutating
            else "GitHub read returned an invalid response"
        )
        raise AgentGitHubProcessError(message) from exc


def _api(
    method: str,
    endpoint: str,
    root: Path,
    runner: CommandRunner,
    *,
    fields: tuple[tuple[str, str], ...] = (),
    paginate: bool = False,
    payload: dict[str, object] | None = None,
) -> Any:
    arguments = ["gh", "api", "--method", method, endpoint]
    for name, value in fields:
        arguments.extend(("-f", f"{name}={value}"))
    if paginate:
        arguments.extend(("--paginate", "--slurp"))
    if payload is not None:
        arguments.extend(("--input", "-"))
    return _invoke(
        arguments,
        root,
        runner,
        payload=payload,
        mutating=method != "GET",
    )


def _flatten_pages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AgentGitHubProcessError("GitHub list returned an invalid response")
    values = value
    if values and all(isinstance(page, list) for page in values):
        values = [item for page in values for item in page]
    if not all(isinstance(item, dict) for item in values):
        raise AgentGitHubProcessError("GitHub list returned an invalid response")
    return values  # type: ignore[return-value]


def _get_issue(number: int, root: Path, runner: CommandRunner) -> dict[str, Any]:
    value = _api("GET", f"{API_ROOT}/issues/{number}", root, runner)
    if not isinstance(value, dict) or "pull_request" in value:
        raise AgentGitHubProcessError("GitHub issue response is invalid")
    return value


def _get_milestone(number: int, root: Path, runner: CommandRunner) -> dict[str, Any]:
    value = _api("GET", f"{API_ROOT}/milestones/{number}", root, runner)
    if not isinstance(value, dict):
        raise AgentGitHubProcessError("GitHub milestone response is invalid")
    return value


def _get_pull(number: int, root: Path, runner: CommandRunner) -> dict[str, Any]:
    value = _api("GET", f"{API_ROOT}/pulls/{number}", root, runner)
    base = value.get("base") if isinstance(value, dict) else None
    repo = base.get("repo") if isinstance(base, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("number") != number
        or not isinstance(repo, dict)
        or repo.get("full_name") != REPOSITORY
    ):
        raise AgentGitHubProcessError("GitHub pull request response is invalid")
    return value


def _validate_label(label: str, root: Path, runner: CommandRunner) -> str:
    label = _validate_text(label, "label", maximum=MAX_NAME_LENGTH)
    value = _api("GET", f"{API_ROOT}/labels/{quote(label, safe='')}", root, runner)
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        raise AgentGitHubProcessError("GitHub label response is invalid")
    return str(value["name"])


def _validate_assignee(login: str, root: Path, runner: CommandRunner) -> str:
    login = _safe_name(login, "assignee")
    value = _api("GET", f"{API_ROOT}/assignees/{quote(login, safe='')}", root, runner)
    if not isinstance(value, dict) or not isinstance(value.get("login"), str):
        raise AgentGitHubProcessError("GitHub assignee response is invalid")
    return str(value["login"])


def _list_issues(root: Path, runner: CommandRunner) -> list[dict[str, Any]]:
    return [
        item
        for item in _flatten_pages(
            _api(
                "GET",
                f"{API_ROOT}/issues",
                root,
                runner,
                fields=(("state", "all"), ("per_page", "100")),
                paginate=True,
            )
        )
        if "pull_request" not in item
    ]


def _list_milestones(root: Path, runner: CommandRunner) -> list[dict[str, Any]]:
    return _flatten_pages(
        _api(
            "GET",
            f"{API_ROOT}/milestones",
            root,
            runner,
            fields=(("state", "all"), ("per_page", "100")),
            paginate=True,
        )
    )


def _require_unique_title(
    title: str, values: list[dict[str, Any]], kind: str, *, exclude: int | None = None
) -> None:
    for value in values:
        if value.get("title") == title and value.get("number") != exclude:
            raise AgentGitHubInputError(f"{kind} title already exists")


def _due_on(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise AgentGitHubInputError("due date is invalid") from exc
    if parsed.isoformat() != value:
        raise AgentGitHubInputError("due date is invalid")
    return f"{value}T23:59:59Z"


def _selected(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, object]:
    selected = {field: value[field] for field in fields if field in value}
    if isinstance(value.get("html_url"), str):
        selected["url"] = value["html_url"]
    return selected


def _sanitize_list(operation: str, value: Any) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise AgentGitHubProcessError("GitHub list response is invalid")
    fields = (
        ("number", "title", "state")
        if operation == "issue-list"
        else ("number", "title", "state", "due_on")
    )
    return [
        _selected(item, fields)
        for item in value
        if isinstance(item, dict)
        and (operation != "issue-list" or "pull_request" not in item)
    ]


def _sanitize_issue(value: dict[str, Any]) -> dict[str, object]:
    selected = _selected(value, ("number", "title", "state", "body"))
    for source, target, field in (
        (value.get("labels"), "labels", "name"),
        (value.get("assignees"), "assignees", "login"),
    ):
        if isinstance(source, list):
            selected[target] = [
                item[field]
                for item in source
                if isinstance(item, dict) and isinstance(item.get(field), str)
            ]
    milestone = value.get("milestone")
    if isinstance(milestone, dict):
        selected["milestone"] = _selected(milestone, ("number", "title"))
    return selected


def _sanitize_pr(value: dict[str, Any]) -> dict[str, object]:
    selected = _selected(value, ("number", "title", "state", "mergeable"))
    for name in ("base", "head"):
        ref = value.get(name)
        if isinstance(ref, dict):
            selected[name] = _selected(ref, ("ref", "sha"))
    return selected


def _sanitize_runs(value: dict[str, Any]) -> dict[str, object]:
    runs = value.get("workflow_runs")
    if not isinstance(runs, list):
        raise AgentGitHubProcessError("GitHub run-list response is invalid")
    return {
        "runs": [
            _selected(
                item,
                (
                    "id",
                    "name",
                    "event",
                    "head_branch",
                    "head_sha",
                    "status",
                    "conclusion",
                    "created_at",
                ),
            )
            for item in runs
            if isinstance(item, dict)
        ]
    }


def _sanitize_read(operation: str, value: Any) -> Any:
    if operation in {"issue-list", "milestone-list"}:
        return _sanitize_list(operation, value)
    if not isinstance(value, dict):
        raise AgentGitHubProcessError("GitHub read response is invalid")
    if operation == "issue-view":
        return _sanitize_issue(value)
    if operation == "pr-view":
        return _sanitize_pr(value)
    return _sanitize_runs(value)


def _emit(operation: str, value: Any) -> None:
    if operation in READ_OPERATIONS:
        print(json.dumps(_sanitize_read(operation, value), sort_keys=True))
        return
    if isinstance(value, dict) and isinstance(value.get("html_url"), str):
        selected: dict[str, object] = {"url": value["html_url"]}
        if isinstance(value.get("number"), int):
            selected = {"number": value["number"], **selected}
        print(json.dumps(selected, sort_keys=True))
        return
    print(json.dumps(value, sort_keys=True))


def _run_number_read(
    operation: str, arguments: list[str], root: Path, runner: CommandRunner
) -> Any:
    if len(arguments) != 1:
        raise AgentGitHubInputError(f"{operation} requires exactly one number")
    number = _positive_number(arguments[0], operation)
    endpoint = "issues" if operation == "issue-view" else "pulls"
    return _api("GET", f"{API_ROOT}/{endpoint}/{number}", root, runner)


def _run_list_read(
    operation: str, arguments: list[str], root: Path, runner: CommandRunner
) -> Any:
    options = _flags(arguments, singles=frozenset({"--state", "--limit"}))
    state = str(options.get("--state", "open"))
    if state not in {"open", "closed", "all"}:
        raise AgentGitHubInputError("state is invalid")
    limit = _positive_number(str(options.get("--limit", MAX_LIST_LIMIT)), "limit")
    if limit > MAX_LIST_LIMIT:
        raise AgentGitHubInputError("limit is invalid")
    endpoint = "issues" if operation == "issue-list" else "milestones"
    values = _api(
        "GET",
        f"{API_ROOT}/{endpoint}",
        root,
        runner,
        fields=(("state", state), ("per_page", str(limit))),
    )
    return _flatten_pages(values)


def _run_runs_read(arguments: list[str], root: Path, runner: CommandRunner) -> Any:
    options = _flags(
        arguments,
        singles=frozenset({"--branch", "--event", "--workflow", "--limit"}),
    )
    fields: list[tuple[str, str]] = []
    branch = options.get("--branch")
    if branch is not None:
        if SAFE_BRANCH.fullmatch(str(branch)) is None or ".." in str(branch):
            raise AgentGitHubInputError("branch is invalid")
        fields.append(("branch", str(branch)))
    event = options.get("--event")
    if event is not None:
        if event not in {"push", "pull_request", "workflow_dispatch"}:
            raise AgentGitHubInputError("event is invalid")
        fields.append(("event", str(event)))
    workflow = options.get("--workflow")
    endpoint = f"{API_ROOT}/actions/runs"
    if workflow is not None:
        workflow_name = quote(_safe_name(str(workflow), "workflow"), safe="")
        endpoint = f"{API_ROOT}/actions/workflows/{workflow_name}/runs"
    limit = _positive_number(str(options.get("--limit", 30)), "limit")
    if limit > MAX_LIST_LIMIT:
        raise AgentGitHubInputError("limit is invalid")
    fields.append(("per_page", str(limit)))
    return _api("GET", endpoint, root, runner, fields=tuple(fields))


def _run_read(
    operation: str, arguments: list[str], root: Path, runner: CommandRunner
) -> Any:
    if operation in {"issue-view", "pr-view"}:
        return _run_number_read(operation, arguments, root, runner)
    if operation in {"issue-list", "milestone-list"}:
        return _run_list_read(operation, arguments, root, runner)
    return _run_runs_read(arguments, root, runner)


def _issue_create(arguments: list[str], root: Path, runner: CommandRunner) -> Any:
    options = _flags(
        arguments,
        singles=frozenset({"--title", "--body-file", "--milestone"}),
        multiples=frozenset({"--label", "--assignee"}),
    )
    if "--title" not in options or "--body-file" not in options:
        raise AgentGitHubInputError("issue-create requires --title and --body-file")
    title = _validate_text(
        str(options["--title"]), "issue title", maximum=MAX_TITLE_LENGTH
    )
    body = _body_file(root, str(options["--body-file"]))
    _require_unique_title(title, _list_issues(root, runner), "issue")
    payload: dict[str, object] = {
        "title": title,
        "body": body,
    }
    labels = [
        _validate_label(str(label), root, runner)
        for label in _multiple(options, "--label")
    ]
    if labels:
        payload["labels"] = labels
    assignees = [
        _validate_assignee(str(login), root, runner)
        for login in _multiple(options, "--assignee")
    ]
    if assignees:
        payload["assignees"] = assignees
    if "--milestone" in options:
        milestone = _positive_number(str(options["--milestone"]), "milestone")
        _get_milestone(milestone, root, runner)
        payload["milestone"] = milestone
    return _api("POST", f"{API_ROOT}/issues", root, runner, payload=payload)


def _issue_basic_edits(
    options: dict[str, object],
    number: int,
    root: Path,
    runner: CommandRunner,
    *,
    body: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if "--title" in options:
        title = _validate_text(
            str(options["--title"]), "issue title", maximum=MAX_TITLE_LENGTH
        )
        _require_unique_title(
            title, _list_issues(root, runner), "issue", exclude=number
        )
        payload["title"] = title
    if "--body-file" in options:
        payload["body"] = body
    if "--milestone" in options:
        value = str(options["--milestone"])
        if value == "none":
            payload["milestone"] = None
        else:
            milestone = _positive_number(value, "milestone")
            _get_milestone(milestone, root, runner)
            payload["milestone"] = milestone
    return payload


def _updated_labels(
    options: dict[str, object],
    current: dict[str, Any],
    root: Path,
    runner: CommandRunner,
) -> list[str]:
    labels = {
        str(item["name"])
        for item in current.get("labels", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    removals = {
        _validate_text(str(label), "label", maximum=MAX_NAME_LENGTH)
        for label in _multiple(options, "--remove-label")
    }
    if not removals <= labels:
        raise AgentGitHubInputError("cannot remove a label absent from the issue")
    labels -= removals
    labels.update(
        _validate_label(str(label), root, runner)
        for label in _multiple(options, "--add-label")
    )
    return sorted(labels)


def _updated_assignees(
    options: dict[str, object],
    current: dict[str, Any],
    root: Path,
    runner: CommandRunner,
) -> list[str]:
    assignees = {
        str(item["login"])
        for item in current.get("assignees", [])
        if isinstance(item, dict) and isinstance(item.get("login"), str)
    }
    removals = {
        _safe_name(str(login), "assignee")
        for login in _multiple(options, "--remove-assignee")
    }
    if not removals <= assignees:
        raise AgentGitHubInputError("cannot remove an assignee absent from the issue")
    assignees -= removals
    assignees.update(
        _validate_assignee(str(login), root, runner)
        for login in _multiple(options, "--add-assignee")
    )
    return sorted(assignees)


def _issue_edit(arguments: list[str], root: Path, runner: CommandRunner) -> Any:
    if not arguments:
        raise AgentGitHubInputError("issue-edit requires an issue number")
    number = _positive_number(arguments[0], "issue number")
    options = _flags(
        arguments[1:],
        singles=frozenset({"--title", "--body-file", "--milestone"}),
        multiples=frozenset(
            {"--add-label", "--remove-label", "--add-assignee", "--remove-assignee"}
        ),
    )
    if not options:
        raise AgentGitHubInputError("issue-edit requires at least one change")
    body = (
        _body_file(root, str(options["--body-file"]))
        if "--body-file" in options
        else None
    )
    current = _get_issue(number, root, runner)
    payload = _issue_basic_edits(options, number, root, runner, body=body)
    if "--add-label" in options or "--remove-label" in options:
        payload["labels"] = _updated_labels(options, current, root, runner)
    if "--add-assignee" in options or "--remove-assignee" in options:
        payload["assignees"] = _updated_assignees(options, current, root, runner)
    return _api("PATCH", f"{API_ROOT}/issues/{number}", root, runner, payload=payload)


def _issue_mutation(
    operation: str, arguments: list[str], root: Path, runner: CommandRunner
) -> Any:
    if operation == "issue-create":
        return _issue_create(arguments, root, runner)
    if operation == "issue-edit":
        return _issue_edit(arguments, root, runner)
    if not arguments:
        raise AgentGitHubInputError(f"{operation} requires an issue number")
    number = _positive_number(arguments[0], "issue number")
    if operation == "issue-comment":
        options = _flags(arguments[1:], singles=frozenset({"--body-file"}))
        if set(options) != {"--body-file"}:
            raise AgentGitHubInputError("issue-comment requires --body-file")
        body = _body_file(root, str(options["--body-file"]))
        _get_issue(number, root, runner)
        return _api(
            "POST",
            f"{API_ROOT}/issues/{number}/comments",
            root,
            runner,
            payload={"body": body},
        )
    if len(arguments) != 1:
        raise AgentGitHubInputError(f"{operation} accepts only an issue number")
    _get_issue(number, root, runner)
    state = "closed" if operation == "issue-close" else "open"
    return _api(
        "PATCH", f"{API_ROOT}/issues/{number}", root, runner, payload={"state": state}
    )


def _pr_create(arguments: list[str], root: Path, runner: CommandRunner) -> Any:
    options = _flags(arguments, singles=frozenset({"--title", "--body-file", "--head"}))
    if set(options) != {"--title", "--body-file", "--head"}:
        raise AgentGitHubInputError(
            "pr-create requires --title, --body-file, and --head"
        )
    title = _validate_text(
        str(options["--title"]), "pull request title", maximum=MAX_TITLE_LENGTH
    )
    body = _body_file(root, str(options["--body-file"]))
    head = _safe_branch(str(options["--head"]), "head branch")
    existing = _flatten_pages(
        _api(
            "GET",
            f"{API_ROOT}/pulls",
            root,
            runner,
            fields=(
                ("state", "open"),
                ("head", f"hniedner:{head}"),
                ("per_page", "100"),
            ),
            paginate=True,
        )
    )
    if existing:
        raise AgentGitHubInputError("open pull request already exists for head branch")
    result = _api(
        "POST",
        f"{API_ROOT}/pulls",
        root,
        runner,
        payload={"title": title, "body": body, "head": head, "base": "main"},
    )
    return _validate_pr_mutation_result(result)


def _pr_edit(arguments: list[str], root: Path, runner: CommandRunner) -> Any:
    if not arguments:
        raise AgentGitHubInputError("pr-edit requires a pull request number")
    number = _positive_number(arguments[0], "pull request number")
    options = _flags(arguments[1:], singles=frozenset({"--title", "--body-file"}))
    if not options:
        raise AgentGitHubInputError("pr-edit requires at least one change")
    payload: dict[str, object] = {}
    if "--title" in options:
        payload["title"] = _validate_text(
            str(options["--title"]),
            "pull request title",
            maximum=MAX_TITLE_LENGTH,
        )
    if "--body-file" in options:
        payload["body"] = _body_file(root, str(options["--body-file"]))
    _get_pull(number, root, runner)
    result = _api("PATCH", f"{API_ROOT}/pulls/{number}", root, runner, payload=payload)
    return _validate_pr_mutation_result(result, expected_number=number)


def _validate_pr_mutation_result(
    value: Any, *, expected_number: int | None = None
) -> dict[str, Any]:
    number = value.get("number") if isinstance(value, dict) else None
    url = value.get("html_url") if isinstance(value, dict) else None
    if (
        type(number) is not int
        or number < 1
        or (expected_number is not None and number != expected_number)
        or url != f"https://github.com/{REPOSITORY}/pull/{number}"
    ):
        raise AgentGitHubProcessError(
            "GitHub mutation response is invalid; inspect the repository before "
            "retrying"
        )
    return value


def _milestone_create(arguments: list[str], root: Path, runner: CommandRunner) -> Any:
    options = _flags(
        arguments,
        singles=frozenset({"--title", "--description-file", "--due-on"}),
    )
    if "--title" not in options:
        raise AgentGitHubInputError("milestone-create requires --title")
    title = _validate_text(
        str(options["--title"]), "milestone title", maximum=MAX_TITLE_LENGTH
    )
    description = (
        _body_file(root, str(options["--description-file"]))
        if "--description-file" in options
        else None
    )
    _require_unique_title(title, _list_milestones(root, runner), "milestone")
    payload: dict[str, object] = {"title": title}
    if "--description-file" in options:
        payload["description"] = description
    if "--due-on" in options:
        payload["due_on"] = _due_on(str(options["--due-on"]))
    return _api("POST", f"{API_ROOT}/milestones", root, runner, payload=payload)


def _milestone_edit(arguments: list[str], root: Path, runner: CommandRunner) -> Any:
    if not arguments:
        raise AgentGitHubInputError("milestone-edit requires a milestone number")
    number = _positive_number(arguments[0], "milestone number")
    options = _flags(
        arguments[1:],
        singles=frozenset({"--title", "--description-file", "--due-on"}),
        switches=frozenset({"--remove-due"}),
    )
    if not options or ("--due-on" in options and "--remove-due" in options):
        raise AgentGitHubInputError("milestone-edit arguments are invalid")
    description = (
        _body_file(root, str(options["--description-file"]))
        if "--description-file" in options
        else None
    )
    _get_milestone(number, root, runner)
    payload: dict[str, object] = {}
    if "--title" in options:
        title = _validate_text(
            str(options["--title"]), "milestone title", maximum=MAX_TITLE_LENGTH
        )
        _require_unique_title(
            title, _list_milestones(root, runner), "milestone", exclude=number
        )
        payload["title"] = title
    if "--description-file" in options:
        payload["description"] = description
    if "--due-on" in options:
        payload["due_on"] = _due_on(str(options["--due-on"]))
    if "--remove-due" in options:
        payload["due_on"] = None
    return _api(
        "PATCH", f"{API_ROOT}/milestones/{number}", root, runner, payload=payload
    )


def _milestone_mutation(
    operation: str, arguments: list[str], root: Path, runner: CommandRunner
) -> Any:
    if operation == "milestone-create":
        return _milestone_create(arguments, root, runner)
    if operation == "milestone-edit":
        return _milestone_edit(arguments, root, runner)
    if len(arguments) != 1:
        raise AgentGitHubInputError(f"{operation} requires one milestone number")
    number = _positive_number(arguments[0], "milestone number")
    _get_milestone(number, root, runner)
    state = "closed" if operation == "milestone-close" else "open"
    return _api(
        "PATCH",
        f"{API_ROOT}/milestones/{number}",
        root,
        runner,
        payload={"state": state},
    )


def run_agent_github(
    arguments: list[str],
    root: Path,
    *,
    read_only: bool = False,
    runner: CommandRunner | None = None,
) -> int:
    """Validate and run one fixed GitHub operation without a shell."""
    if not arguments:
        raise AgentGitHubInputError("GitHub operation is unsupported")
    operation = arguments[0]
    if operation not in READ_OPERATIONS | MUTATION_OPERATIONS:
        raise AgentGitHubInputError("GitHub operation is unsupported")
    if read_only and operation not in READ_OPERATIONS:
        raise AgentGitHubInputError("GitHub mutation is unavailable in read-only mode")
    resolved_root = root.resolve()
    command_runner = runner or _subprocess_runner
    if operation in READ_OPERATIONS:
        value = _run_read(operation, arguments[1:], resolved_root, command_runner)
    elif operation.startswith("issue-"):
        value = _issue_mutation(operation, arguments[1:], resolved_root, command_runner)
    elif operation == "pr-create":
        value = _pr_create(arguments[1:], resolved_root, command_runner)
    elif operation == "pr-edit":
        value = _pr_edit(arguments[1:], resolved_root, command_runner)
    else:
        value = _milestone_mutation(
            operation, arguments[1:], resolved_root, command_runner
        )
    _emit(operation, value)
    return 0


def main() -> int:
    arguments = sys.argv[1:]
    read_only = bool(arguments and arguments[0] == "--read-only")
    if read_only:
        arguments = arguments[1:]
    root = Path(__file__).resolve().parents[2]
    try:
        return run_agent_github(arguments, root, read_only=read_only)
    except AgentGitHubInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except AgentGitHubProcessError as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
