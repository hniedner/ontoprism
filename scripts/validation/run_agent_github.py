#!/usr/bin/env python3
"""Run repository-scoped issue/milestone mutations, pull-request create/edit
mutations, a squash merge pinned to the reviewed head, and reads.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

REPOSITORY = "hniedner/ontoprism"
OWNER = REPOSITORY.split("/", maxsplit=1)[0]
API_ROOT = f"repos/{REPOSITORY}"
PROTECTED_BRANCHES = frozenset({"main", "master"})
READ_OPERATIONS = frozenset(
    {
        "issue-view",
        "issue-comments",
        "issue-list",
        "milestone-list",
        "pr-view",
        "run-list",
    }
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
        "pr-merge",
        "main-required-checks",
    }
)
PROCESS_TIMEOUT_SECONDS = 30
MUTATION_TIMEOUT_SECONDS = 120
MAX_BODY_BYTES = 1_000_000
MAX_TITLE_LENGTH = 256
MAX_NAME_LENGTH = 100
MAX_LIST_LIMIT = 100
MAX_GITHUB_NUMBER = 2_147_483_647
MAIN_RULESET_ID = 18_832_085
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
SAFE_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")


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


@dataclass(frozen=True)
class PullIdentity:
    number: int
    state: str
    merged: bool


@dataclass(frozen=True)
class PullMutationResult:
    number: int
    url: str


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
        or value in PROTECTED_BRANCHES
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
    empty_ok: bool = False,
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
    if empty_ok and not result.stdout.strip():
        return None
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
        # a successful DELETE answers with an empty body; nothing else may
        empty_ok=method == "DELETE",
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
    if not isinstance(value, dict):
        raise AgentGitHubProcessError("GitHub issue response is invalid")
    if "pull_request" in value:
        raise AgentGitHubInputError(f"#{number} is a pull request, not an issue")
    return value


def _get_milestone(number: int, root: Path, runner: CommandRunner) -> dict[str, Any]:
    value = _api("GET", f"{API_ROOT}/milestones/{number}", root, runner)
    if not isinstance(value, dict):
        raise AgentGitHubProcessError("GitHub milestone response is invalid")
    return value


def _pull_identity(
    value: object,
    *,
    expected_number: int | None = None,
    expected_head: str | None = None,
) -> PullIdentity:
    base = value.get("base") if isinstance(value, dict) else None
    repo = base.get("repo") if isinstance(base, dict) else None
    number = value.get("number") if isinstance(value, dict) else None
    state = value.get("state") if isinstance(value, dict) else None
    merged_value = value.get("merged") if isinstance(value, dict) else None
    merged_at = value.get("merged_at") if isinstance(value, dict) else None
    has_merged = isinstance(value, dict) and "merged" in value
    has_merged_at = isinstance(value, dict) and "merged_at" in value
    head = value.get("head") if isinstance(value, dict) else None
    head_repo = head.get("repo") if isinstance(head, dict) else None
    invalid_head = expected_head is not None and (
        not isinstance(head, dict)
        or head.get("ref") != expected_head
        or not isinstance(head_repo, dict)
        or head_repo.get("full_name") != REPOSITORY
    )
    if (
        type(number) is not int
        or number < 1
        or (expected_number is not None and number != expected_number)
        or state not in {"open", "closed"}
        or not isinstance(repo, dict)
        or repo.get("full_name") != REPOSITORY
        or (has_merged and type(merged_value) is not bool)
        or (has_merged_at and merged_at is not None and not isinstance(merged_at, str))
        or not (has_merged or has_merged_at)
        or invalid_head
    ):
        raise AgentGitHubProcessError("GitHub pull request response is invalid")
    merged = bool(merged_value) if has_merged else merged_at is not None
    if has_merged and has_merged_at and merged != (merged_at is not None):
        raise AgentGitHubProcessError("GitHub pull request response is invalid")
    return PullIdentity(number=number, state=state, merged=merged)


def _get_pull(number: int, root: Path, runner: CommandRunner) -> PullIdentity:
    value = _api("GET", f"{API_ROOT}/pulls/{number}", root, runner)
    return _pull_identity(value, expected_number=number)


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


def _read_pages(
    endpoint: str,
    root: Path,
    runner: CommandRunner,
    *,
    fields: tuple[tuple[str, str], ...] = (),
) -> list[dict[str, Any]]:
    return _flatten_pages(
        _api(
            "GET",
            endpoint,
            root,
            runner,
            fields=(*fields, ("per_page", str(MAX_LIST_LIMIT))),
            paginate=True,
        )
    )


def _list_issues(root: Path, runner: CommandRunner) -> list[dict[str, Any]]:
    issues = _read_pages(f"{API_ROOT}/issues", root, runner, fields=(("state", "all"),))
    return [item for item in issues if "pull_request" not in item]


def _list_milestones(root: Path, runner: CommandRunner) -> list[dict[str, Any]]:
    return _read_pages(
        f"{API_ROOT}/milestones", root, runner, fields=(("state", "all"),)
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


def _selected_issue(
    value: dict[str, Any], fields: tuple[str, ...]
) -> dict[str, object]:
    """The output always carries ``milestone``: ``None`` when GitHub reports none (a
    null or absent key); anything but an object or null is refused. Label names come
    along in a list or view read when GitHub sends labels, so an epic is visible;
    labels that are not a list of named objects are refused, because a dropped label
    would make an epic look like an ordinary issue."""
    milestone = value.get("milestone")
    if milestone is not None and not isinstance(milestone, dict):
        raise AgentGitHubProcessError(
            f"GitHub issue {value.get('number')} milestone is invalid"
        )
    selected = _selected(value, fields)
    selected["milestone"] = (
        None if milestone is None else _selected(milestone, ("number", "title"))
    )
    if value.get("labels") is not None:
        selected["labels"] = _names(value, "labels", "name")
    return selected


def _names(value: dict[str, Any], key: str, attribute: str) -> list[str]:
    """The ``attribute`` of each object in the issue's ``key`` list; anything else,
    including a null or absent list, is refused. Reads skip a null or absent list and
    call it otherwise, so a malformed label list cannot hide an epic; a label or
    assignee edit, which sends the full list back, calls it even for a null or absent
    list, because it would delete what it could not read."""
    items = value.get(key)
    if not isinstance(items, list) or not all(
        isinstance(item, dict) and isinstance(item.get(attribute), str)
        for item in items
    ):
        raise AgentGitHubProcessError(
            f"GitHub issue {value.get('number')} {key} are invalid"
        )
    return [item[attribute] for item in items]


_LIST_FIELDS = {
    "issue-list": ("number", "title", "state", "created_at"),
    "issue-comments": ("body", "created_at"),
    "milestone-list": ("number", "title", "state", "due_on", "description"),
}


def _sanitize_list(
    operation: str, value: list[dict[str, Any]]
) -> list[dict[str, object]]:
    project = _selected_issue if operation == "issue-list" else _selected
    return [project(item, _LIST_FIELDS[operation]) for item in value]


def _sanitize_issue(value: dict[str, Any]) -> dict[str, object]:
    selected = _selected_issue(value, ("number", "title", "state", "body"))
    if value.get("assignees") is not None:
        selected["assignees"] = _names(value, "assignees", "login")
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
    if operation in _LIST_FIELDS:
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
    if isinstance(value, PullMutationResult):
        print(json.dumps({"number": value.number, "url": value.url}, sort_keys=True))
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
    if operation == "issue-view":
        return _get_issue(number, root, runner)
    return _api("GET", f"{API_ROOT}/pulls/{number}", root, runner)


def _run_list_read(
    operation: str, arguments: list[str], root: Path, runner: CommandRunner
) -> list[dict[str, Any]]:
    """Every page of the list; ``--limit`` then keeps that many issues or milestones.

    The limit only shortens a complete list, so it has no upper bound. ``run-list``
    differs: it sends its limit to GitHub as the page size, so it caps it at
    ``MAX_LIST_LIMIT``."""
    options = _flags(arguments, singles=frozenset({"--state", "--limit"}))
    state = str(options.get("--state", "open"))
    if state not in {"open", "closed", "all"}:
        raise AgentGitHubInputError("state is invalid")
    limit = None
    if "--limit" in options:
        limit = _positive_number(str(options["--limit"]), "limit")
    endpoint = "issues" if operation == "issue-list" else "milestones"
    values = _read_pages(
        f"{API_ROOT}/{endpoint}", root, runner, fields=(("state", state),)
    )
    return [item for item in values if "pull_request" not in item][:limit]


def _run_comments_read(
    arguments: list[str], root: Path, runner: CommandRunner
) -> list[dict[str, Any]]:
    if len(arguments) != 1:
        raise AgentGitHubInputError("issue-comments requires exactly one number")
    number = _positive_number(arguments[0], "issue-comments")
    _get_issue(number, root, runner)
    return _read_pages(f"{API_ROOT}/issues/{number}/comments", root, runner)


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
    if operation == "issue-comments":
        return _run_comments_read(arguments, root, runner)
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
    labels = set(_names(current, "labels", "name"))
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
    assignees = set(_names(current, "assignees", "login"))
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


# Milestone PRs and changes belonging to no milestone target main. A milestone-branch
# base is still accepted, though since D92 issues merge into the milestone branch
# locally rather than through a PR. Anything else is not a base this repository uses.
MILESTONE_BRANCH = re.compile(r"feat/m[0-9][0-9A-Za-z.-]*(?:-[0-9A-Za-z.-]+)*")


def _pr_base(value: str) -> str:
    if value == "main":
        return value
    if MILESTONE_BRANCH.fullmatch(value) is None or ".." in value:
        raise AgentGitHubInputError(
            "base branch must be main or a milestone branch feat/m<number>-<slug>"
        )
    return _safe_branch(value, "base branch")


def _pr_create(
    arguments: list[str], root: Path, runner: CommandRunner
) -> PullMutationResult:
    options = _flags(
        arguments, singles=frozenset({"--title", "--body-file", "--head", "--base"})
    )
    if not {"--title", "--body-file", "--head"} <= set(options):
        raise AgentGitHubInputError(
            "pr-create requires --title, --body-file, and --head"
        )
    title = _validate_text(
        str(options["--title"]), "pull request title", maximum=MAX_TITLE_LENGTH
    )
    body = _body_file(root, str(options["--body-file"]))
    head = _safe_branch(str(options["--head"]), "head branch")
    base = _pr_base(str(options.get("--base", "main")))
    existing = _flatten_pages(
        _api(
            "GET",
            f"{API_ROOT}/pulls",
            root,
            runner,
            fields=(
                ("state", "open"),
                ("head", f"{OWNER}:{head}"),
                ("per_page", "100"),
            ),
            paginate=True,
        )
    )
    identities = [_pull_identity(item, expected_head=head) for item in existing]
    if any(identity.state == "open" for identity in identities):
        raise AgentGitHubInputError("open pull request already exists for head branch")
    result = _api(
        "POST",
        f"{API_ROOT}/pulls",
        root,
        runner,
        payload={"title": title, "body": body, "head": head, "base": base},
    )
    return _validate_pr_mutation_result(result)


def _pr_edit(
    arguments: list[str], root: Path, runner: CommandRunner
) -> PullMutationResult:
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
    current = _get_pull(number, root, runner)
    if current.state != "open" or current.merged:
        raise AgentGitHubInputError("pr-edit requires an open pull request")
    result = _api("PATCH", f"{API_ROOT}/pulls/{number}", root, runner, payload=payload)
    return _validate_pr_mutation_result(result, expected_number=number)


def _merge_arguments(arguments: list[str]) -> tuple[int, str, str]:
    if not arguments:
        raise AgentGitHubInputError("pr-merge requires a pull request number")
    number = _positive_number(arguments[0], "pull request number")
    options = _flags(arguments[1:], singles=frozenset({"--head", "--base"}))
    if set(options) != {"--head", "--base"}:
        raise AgentGitHubInputError("pr-merge requires --head and --base")
    head = str(options["--head"])
    if FULL_SHA.fullmatch(head) is None:
        raise AgentGitHubInputError("--head must be a full 40-character commit SHA")
    base = str(options["--base"])
    if SAFE_BRANCH.fullmatch(base) is None or ".." in base:
        raise AgentGitHubInputError("--base is invalid")
    return number, head, base


def _reviewed_pull(
    number: int, head: str, base: str, root: Path, runner: CommandRunner
) -> tuple[str, str]:
    """The head branch and merge subject of the open PR whose head and base are the
    ones reviewed; anything else is refused before a write."""
    pull = _api("GET", f"{API_ROOT}/pulls/{number}", root, runner)
    identity = _pull_identity(pull, expected_number=number)
    if identity.state != "open" or identity.merged:
        raise AgentGitHubInputError("pr-merge requires an open pull request")
    pull_head, pull_base, title = pull.get("head"), pull.get("base"), pull.get("title")
    if (
        not isinstance(pull_head, dict)
        or not isinstance(pull_base, dict)
        or not isinstance(pull_head.get("sha"), str)
        or not isinstance(pull_head.get("ref"), str)
        or not isinstance(pull_base.get("ref"), str)
        or not isinstance(title, str)
    ):
        raise AgentGitHubProcessError("GitHub pull request response is invalid")
    head_repo = pull_head.get("repo")
    if not isinstance(head_repo, dict) or head_repo.get("full_name") != REPOSITORY:
        raise AgentGitHubInputError(f"#{number} head branch is not in {REPOSITORY}")
    if pull_head["sha"] != head:
        raise AgentGitHubInputError(
            f"#{number} head moved: reviewed {head}, GitHub has {pull_head['sha']}"
        )
    if pull_base["ref"] != base:
        raise AgentGitHubInputError(f"#{number} base is not {base}")
    head_ref = _safe_branch(pull_head["ref"], "head branch")
    title = _validate_text(title, "title", maximum=MAX_TITLE_LENGTH)
    return head_ref, f"{title} (#{number})"


def _pr_merge(
    arguments: list[str], root: Path, runner: CommandRunner
) -> dict[str, object]:
    """Squash-merge the reviewed head of an open PR into its expected base, titled
    ``<PR title> (#<n>)`` with an empty body. The head branch is left to GitHub when
    the repository deletes merged branches (the wrapper does not confirm it), and is
    deleted by the wrapper otherwise.
    Every check runs before the first write, and GitHub itself refuses the merge if the
    head moved after the check. Once merged, the result or the error says so, and names
    the merge commit whenever GitHub returned a readable one."""
    number, head, base = _merge_arguments(arguments)
    head_ref, subject = _reviewed_pull(number, head, base, root, runner)
    repository = _api("GET", API_ROOT, root, runner)
    github_deletes = (
        repository.get("delete_branch_on_merge")
        if isinstance(repository, dict)
        else None
    )
    if type(github_deletes) is not bool:
        raise AgentGitHubProcessError("GitHub repository response is invalid")
    result = _api(
        "PUT",
        f"{API_ROOT}/pulls/{number}/merge",
        root,
        runner,
        payload={
            "merge_method": "squash",
            "sha": head,
            "commit_title": subject,
            "commit_message": "",
        },
    )
    if not isinstance(result, dict) or result.get("merged") is not True:
        raise AgentGitHubProcessError(
            f"GitHub did not merge #{number}; inspect the repository before retrying"
        )
    merge_commit = result.get("sha")
    if not isinstance(merge_commit, str) or FULL_SHA.fullmatch(merge_commit) is None:
        left = "" if github_deletes else f"; {head_ref} was not deleted, ask the owner"
        raise AgentGitHubProcessError(
            f"GitHub merged #{number}, but its merge commit is unreadable; do not "
            f"retry the merge, read it with gh pr view{left}"
        )
    if github_deletes:
        # GitHub removes the branch itself (unless a protection rule or ruleset stops
        # it); a second DELETE would race that and fail on a merge that succeeded
        return {
            "number": number,
            "merge_commit": merge_commit,
            "branch_deletion": "github",
        }
    try:
        _api(
            "DELETE",
            f"{API_ROOT}/git/refs/heads/{quote(head_ref, safe='/')}",
            root,
            runner,
        )
    except AgentGitHubProcessError as exc:
        raise AgentGitHubProcessError(
            f"merged #{number} as {merge_commit}; deleting {head_ref} failed, so ask "
            "the owner to delete it; do not retry the merge"
        ) from exc
    return {
        "number": number,
        "merge_commit": merge_commit,
        "branch_deletion": "wrapper",
    }


def _validate_pr_mutation_result(
    value: Any, *, expected_number: int | None = None
) -> PullMutationResult:
    number = value.get("number") if isinstance(value, dict) else None
    url = value.get("html_url") if isinstance(value, dict) else None
    if (
        type(number) is not int
        or number < 1
        or (expected_number is not None and number != expected_number)
        or not isinstance(url, str)
        or url != f"https://github.com/{REPOSITORY}/pull/{number}"
    ):
        raise AgentGitHubProcessError(
            "GitHub mutation response is invalid; inspect the repository before "
            "retrying"
        )
    return PullMutationResult(number=number, url=url)


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


def _validate_main_ruleset_scope(current: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "id": MAIN_RULESET_ID,
        "name": "main integrity",
        "target": "branch",
        "enforcement": "active",
    }
    if any(current.get(key) != value for key, value in expected.items()):
        raise AgentGitHubInputError("main ruleset identity or scope changed")
    if current.get("bypass_actors", []) != []:
        raise AgentGitHubInputError("main ruleset identity or scope changed")
    desired_conditions = {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}
    normalized_conditions = {
        "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
    }
    if current.get("conditions") not in (desired_conditions, normalized_conditions):
        raise AgentGitHubInputError("main ruleset identity or scope changed")
    return desired_conditions


def _main_required_checks(
    arguments: list[str], root: Path, runner: CommandRunner
) -> Any:
    if arguments:
        raise AgentGitHubInputError("main-required-checks accepts no arguments")
    current = _api("GET", f"{API_ROOT}/rulesets/{MAIN_RULESET_ID}", root, runner)
    if not isinstance(current, dict):
        raise AgentGitHubProcessError("main ruleset response is invalid")
    desired_conditions = _validate_main_ruleset_scope(current)
    required_parameters = {
        "strict_required_status_checks_policy": False,
        "do_not_enforce_on_create": False,
        "required_status_checks": [
            {"context": "CI summary"},
            {"context": "quality (pre-commit parity)"},
            {"context": "conventional commit subject"},
            {"context": "dependency review"},
            {"context": "CodeQL"},
        ],
    }
    desired_rules = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_status_checks", "parameters": required_parameters},
    ]
    rules = current.get("rules")
    if not isinstance(rules, list):
        raise AgentGitHubProcessError("main ruleset rules are invalid")
    if not all(isinstance(rule, dict) for rule in rules):
        raise AgentGitHubProcessError("main ruleset rules are invalid")
    typed_rules = [rule for rule in rules if isinstance(rule, dict)]
    rule_types = {rule.get("type") for rule in typed_rules}
    pre_state = {"deletion", "non_fast_forward"}
    post_state = {*pre_state, "required_status_checks"}
    if rule_types not in (pre_state, post_state):
        raise AgentGitHubInputError("main ruleset rules changed")
    if rule_types == post_state:
        current_required = next(
            rule for rule in typed_rules if rule.get("type") == "required_status_checks"
        )
        if current_required.get("parameters") != required_parameters:
            raise AgentGitHubInputError("main required checks changed")
    payload = {
        "name": "main integrity",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": desired_conditions,
        "rules": desired_rules,
    }
    return _api(
        "PUT",
        f"{API_ROOT}/rulesets/{MAIN_RULESET_ID}",
        root,
        runner,
        payload=payload,
    )


def run_agent_github(
    arguments: list[str],
    root: Path,
    *,
    read_only: bool,
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
    elif operation == "pr-merge":
        value = _pr_merge(arguments[1:], resolved_root, command_runner)
    elif operation == "main-required-checks":
        value = _main_required_checks(arguments[1:], resolved_root, command_runner)
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
