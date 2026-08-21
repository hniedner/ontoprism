#!/usr/bin/env python3
"""Validate the repository's portable OpenCode configuration and process."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

SCHEMA = "https://opencode.ai/config.json"
PLUGIN = "@razroo/opencode-model-fallback@0.3.2"
GPT = "github-copilot/gpt-5.6-sol"
CLAUDE = "github-copilot/claude-opus-5"
ROLES: dict[str, tuple[str, str, str, str]] = {
    "ontoprism-team": (GPT, "primary", "deny", "allow"),
    "implementer": ("openai/gpt-5.6-sol", "subagent", "allow", "deny"),
    "architect": (GPT, "subagent", "deny", "deny"),
    "ontology-engineer": (GPT, "subagent", "deny", "deny"),
    "oncology-evidence-analyst": (GPT, "subagent", "deny", "deny"),
    "plan-adversary": (CLAUDE, "subagent", "deny", "deny"),
    "ontology-validator": (CLAUDE, "subagent", "deny", "deny"),
    "pr-code-reviewer": (CLAUDE, "subagent", "deny", "deny"),
    "pr-silent-failure-hunter": (CLAUDE, "subagent", "deny", "deny"),
    "pr-comment-analyzer": (CLAUDE, "subagent", "deny", "deny"),
    "pr-type-design-analyzer": (CLAUDE, "subagent", "deny", "deny"),
    "pr-test-analyzer": (CLAUDE, "subagent", "allow", "deny"),
    "bedrock-gpt-reserve": (
        "amazon-bedrock/global.openai.gpt-5.6-sol",
        "primary",
        "deny",
        "deny",
    ),
    "bedrock-claude-reserve": (
        "amazon-bedrock/global.anthropic.claude-opus-5",
        "primary",
        "deny",
        "deny",
    ),
}
RESERVES = {"bedrock-gpt-reserve", "bedrock-claude-reserve"}
AUTO_SUBAGENTS = (
    "architect",
    "implementer",
    "oncology-evidence-analyst",
    "ontology-engineer",
    "ontology-validator",
    "plan-adversary",
    "pr-code-reviewer",
    "pr-comment-analyzer",
    "pr-silent-failure-hunter",
    "pr-test-analyzer",
    "pr-type-design-analyzer",
)
REVIEWERS = {
    "pr-code-reviewer",
    "pr-silent-failure-hunter",
    "pr-test-analyzer",
    "pr-comment-analyzer",
    "pr-type-design-analyzer",
}
TRACKED_PROCESS = (
    "opencode.json",
    "AGENTS.md",
    ".gitignore",
    ".opencode/opencode-model-fallback.jsonc",
    ".opencode/agent",
    ".opencode/command",
)
MIN_DESCRIPTION_LENGTH = 24
MIN_PROMPT_LENGTH = 100
COMMON_READ_ONLY_TOOLS = {
    "read",
    "glob",
    "grep",
    "lsp",
    "skill",
    "webfetch",
    "websearch",
    "question",
    "todowrite",
}
IMPLEMENTER_TOOLS = COMMON_READ_ONLY_TOOLS | {"edit"}
R3_TOOLS = {"read", "glob", "grep", "edit", "skill"}
IMPLEMENTER_PACKAGE_COMMANDS = (
    "verify",
    "test-ci",
    "test",
    "test-unit",
    "test-integration",
    "test-integration-full-store",
    "test-smoke",
    "lint",
    "fmt",
    "validate-opencode-config",
    "validate-opencode-runtime",
    "pre-commit run --all-files",
    "agent-test *",
)
IMPLEMENTER_NPM_COMMANDS = (
    "npm --prefix frontend run test:coverage",
    "npm --prefix frontend run test:unit -- --run",
    "npm --prefix frontend run check",
    "npm --prefix frontend run lint",
    "npm --prefix frontend run fallow",
    "npm --prefix frontend run build",
)
FIXED_GIT_INSPECTION = (
    "git status --porcelain",
    "git status --short --branch",
    "git rev-parse HEAD",
    "git diff --no-ext-diff main...HEAD",
    "git diff --check main...HEAD",
    "git log --oneline -10",
    "git show --stat --oneline HEAD",
)
SHELL_METACHARACTER_DENIES = ("*&*", "*;*", "*|*", "*>*", "*<*", "*`*", "*$*")
LINE_BREAK_DENIES = ("*\n*", "*\r*")


class Validation:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.errors: list[str] = []

    def error(self, code: str, message: str) -> None:
        self.errors.append(f"{code}: {message}")

    def require_file(self, relative: str) -> Path | None:
        path = self.root / relative
        if not path.is_file():
            self.error("FILES", f"required file missing: {relative}")
            return None
        return path


def strip_jsonc_comments(text: str) -> str:
    """Remove JavaScript comments while preserving comment markers in strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
        elif char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif char == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
        elif char == "/" and following == "*":
            index = block_comment_end(text, index + 2)
        else:
            output.append(char)
            index += 1
    return "".join(output)


def block_comment_end(text: str, start: int) -> int:
    end = text.find("*/", start)
    if end == -1:
        raise ValueError("unterminated block comment")
    return end + 2


def load_json(path: Path, validation: Validation, code: str) -> dict[str, Any]:
    try:
        value = json.loads(strip_jsonc_comments(path.read_text()))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        validation.error(
            code, f"cannot parse {path.relative_to(validation.root)}: {exc}"
        )
        return {}
    if not isinstance(value, dict):
        validation.error(
            code, f"{path.relative_to(validation.root)} must contain an object"
        )
        return {}
    return value


def load_agent(path: Path, validation: Validation) -> tuple[dict[str, Any], str]:
    text = path.read_text()
    match = re.fullmatch(r"---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        validation.error(
            "ROLE_BODY", f"{path.name} needs YAML frontmatter and a prompt body"
        )
        return {}, ""
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        validation.error("ROLE_BODY", f"cannot parse {path.name} frontmatter: {exc}")
        return {}, match.group(2).strip()
    if not isinstance(metadata, dict):
        validation.error("ROLE_BODY", f"{path.name} frontmatter must be a mapping")
        return {}, match.group(2).strip()
    return metadata, match.group(2).strip()


def permission_action(metadata: dict[str, Any], name: str) -> Any:
    permission = metadata.get("permission", {})
    return permission.get(name) if isinstance(permission, dict) else None


def require_bash_rules(
    validation: Validation,
    role: str,
    metadata: dict[str, Any],
    required: dict[str, str],
    *,
    catch_all: str,
) -> None:
    bash = permission_action(metadata, "bash")
    if not isinstance(bash, dict) or next(iter(bash), None) != "*":
        validation.error(
            "ROLE_PERMISSION", f"{role} bash permissions must put the broad rule first"
        )
        return
    if bash.get("*") != catch_all:
        validation.error(
            "ROLE_PERMISSION", f"{role} bash catch-all must be {catch_all}"
        )
    for pattern, action in required.items():
        if bash.get(pattern) != action:
            validation.error(
                "ROLE_PERMISSION", f"{role} bash rule {pattern} must be {action}"
            )
    validate_deny_order(validation, "ROLE_PERMISSION", role, bash, required)


def validate_deny_order(
    validation: Validation,
    code: str,
    role: str,
    bash: dict[str, Any],
    required: dict[str, str] | tuple[str, ...],
) -> None:
    rules = list(bash.items())
    for deny_pattern in required:
        if bash.get(deny_pattern) != "deny":
            continue
        command = deny_pattern.split(maxsplit=1)[0]
        if command not in {"git", "gh"}:
            continue
        deny_index = next(
            index for index, (pattern, _) in enumerate(rules) if pattern == deny_pattern
        )
        allow_indices = [
            index
            for index, (pattern, action) in enumerate(rules)
            if action == "allow"
            and (pattern == "*" or pattern.split(maxsplit=1)[0] == command)
        ]
        if allow_indices and deny_index < max(allow_indices):
            label = "Git" if command == "git" else "GH"
            validation.error(
                code,
                f"{role} deny {deny_pattern} must follow all {label} allow rules",
            )


def validate_root(validation: Validation) -> dict[str, Any]:
    path = validation.require_file("opencode.json")
    if path is None:
        return {}
    config = load_json(path, validation, "ROOT_CONFIG")
    if config.get("$schema") != SCHEMA:
        validation.error(
            "ROOT_CONFIG", "root $schema is not the current OpenCode schema"
        )
    if config.get("default_agent") != "ontoprism-team":
        validation.error("DEFAULT_AGENT", "default_agent must be ontoprism-team")
    if config.get("plugin") != [PLUGIN]:
        validation.error(
            "ROOT_CONFIG", "plugin list must contain only the pinned fallback plugin"
        )
    agent = config.get("agent")
    implementer = agent.get("implementer") if isinstance(agent, dict) else None
    expected = ["github-copilot/gpt-5.6-sol"]
    if (
        not isinstance(implementer, dict)
        or implementer.get("fallback_models") != expected
    ):
        validation.error(
            "IMPLEMENTER_FALLBACK", f"implementer fallback_models must equal {expected}"
        )
    serialized = json.dumps(config)
    if "amazon-bedrock/" in serialized:
        validation.error(
            "IMPLEMENTER_FALLBACK", "automatic root routes must not contain Bedrock"
        )
    return config


def validate_tool_permissions(
    validation: Validation, role: str, metadata: dict[str, Any]
) -> None:
    permission_config = metadata.get("permission")
    if (
        not isinstance(permission_config, dict)
        or next(iter(permission_config), None) != "*"
    ):
        validation.error(
            "ROLE_PERMISSION", f"{role} tool wildcard must be declared first"
        )
    elif permission_config.get("*") != "deny":
        validation.error("ROLE_PERMISSION", f"{role} tool catch-all must be deny")
    expected_tools = (
        IMPLEMENTER_TOOLS
        if role == "implementer"
        else R3_TOOLS
        if role == "pr-test-analyzer"
        else COMMON_READ_ONLY_TOOLS
    )
    if isinstance(permission_config, dict):
        for tool in expected_tools:
            if permission_config.get(tool) != "allow":
                validation.error("ROLE_PERMISSION", f"{role} tool {tool} must be allow")
        permitted_keys = expected_tools | {"*", "edit", "task", "bash"}
        if role == "pr-test-analyzer":
            permitted_keys.add("external_directory")
        for tool in permission_config.keys() - permitted_keys:
            validation.error(
                "ROLE_PERMISSION", f"{role} has unapproved explicit tool {tool}"
            )


def validate_shell_metacharacter_denies(
    validation: Validation, role: str, metadata: dict[str, Any]
) -> None:
    bash = permission_action(metadata, "bash")
    if not isinstance(bash, dict):
        return
    rules = list(bash)
    allow_indices = [
        index for index, pattern in enumerate(rules) if bash.get(pattern) == "allow"
    ]
    for pattern in SHELL_METACHARACTER_DENIES:
        if bash.get(pattern) != "deny":
            validation.error(
                "ROLE_PERMISSION", f"{role} must deny shell pattern {pattern}"
            )
        elif allow_indices and rules.index(pattern) < max(allow_indices):
            validation.error(
                "ROLE_PERMISSION",
                f"{role} shell deny {pattern} must follow every allow",
            )

    for pattern, action in bash.items():
        if (
            action == "allow"
            and "*" in pattern
            and pattern.startswith(
                ("git diff", "git log", "git show", "git status", "git rev-parse")
            )
        ):
            validation.error(
                "ROLE_PERMISSION", f"{role} has wildcard Git inspection {pattern}"
            )
    if any(
        action == "allow" and "*" in pattern for pattern, action in bash.items()
    ) and (
        list(bash)[-2:] != list(LINE_BREAK_DENIES)
        or any(bash.get(pattern) != "deny" for pattern in LINE_BREAK_DENIES)
    ):
        validation.error(
            "ROLE_PERMISSION",
            f"{role} literal line-break denies must be last",
        )


def validate_task_permission(
    validation: Validation,
    role: str,
    expected_action: str,
    metadata: dict[str, Any],
) -> None:
    task_permission = permission_action(metadata, "task")
    if role == "ontoprism-team":
        expected_task = {"*": "deny", **dict.fromkeys(AUTO_SUBAGENTS, "allow")}
        if task_permission != expected_task:
            validation.error(
                "ROLE_PERMISSION",
                "ontoprism-team task delegation must equal the approved agent map",
            )
    elif task_permission != expected_action:
        validation.error(
            "ROLE_PERMISSION", f"{role} task permission must be {expected_action}"
        )


def validate_role(
    validation: Validation,
    role: str,
    expected: tuple[str, str, str, str],
    metadata: dict[str, Any],
    body: str,
    descriptions: dict[str, str],
    bodies: dict[str, str],
) -> None:
    model, mode, edit, task = expected
    if metadata.get("model") != model:
        validation.error("ROLE_MODEL", f"{role} model must be {model}")
    if metadata.get("mode") != mode:
        validation.error("ROLE_MODE", f"{role} mode must be {mode}")
    if metadata.get("hidden") is True and role == "ontoprism-team":
        validation.error(
            "DEFAULT_AGENT", "ontoprism-team must be a visible primary agent"
        )
    for permission, action in (("edit", edit),):
        if permission_action(metadata, permission) != action:
            validation.error(
                "ROLE_PERMISSION", f"{role} {permission} permission must be {action}"
            )
    validate_task_permission(validation, role, task, metadata)
    validate_tool_permissions(validation, role, metadata)
    validate_shell_metacharacter_denies(validation, role, metadata)
    description = metadata.get("description")
    if (
        not isinstance(description, str)
        or len(description.strip()) < MIN_DESCRIPTION_LENGTH
    ):
        validation.error("ROLE_BODY", f"{role} needs a nontrivial description")
    elif description.strip() in descriptions:
        validation.error(
            "ROLE_BODY",
            f"{role} duplicates {descriptions[description.strip()]} description",
        )
    else:
        descriptions[description.strip()] = role
    normalized = re.sub(r"\s+", " ", body).strip()
    if len(normalized) < MIN_PROMPT_LENGTH:
        validation.error("ROLE_BODY", f"{role} needs a nontrivial prompt body")
    elif normalized in bodies:
        validation.error(
            "ROLE_BODY", f"{role} duplicates {bodies[normalized]} prompt body"
        )
    else:
        bodies[normalized] = role


def discover_project_agents(validation: Validation) -> dict[str, list[Path]]:
    agent_dirs = (
        validation.root / ".opencode" / "agent",
        validation.root / ".opencode" / "agents",
    )
    discovered: dict[str, list[Path]] = {}
    for agent_dir in agent_dirs:
        if not agent_dir.is_dir():
            continue
        for path in agent_dir.rglob("*.md"):
            discovered.setdefault(path.stem, []).append(path)
    for name in sorted(discovered.keys() - ROLES.keys()):
        if name == "pr-reviewer":
            validation.error(
                "FILES", "stale .opencode/agent/pr-reviewer.md must be absent"
            )
        else:
            validation.error("FILES", f"unexpected project agent {name}")
    for name, paths in sorted(discovered.items()):
        if len(paths) > 1:
            validation.error("FILES", f"duplicate project agent {name}")
    return discovered


def validate_roles(
    validation: Validation, root_config: dict[str, Any]
) -> dict[str, tuple[dict[str, Any], str]]:
    discovered = discover_project_agents(validation)
    loaded: dict[str, tuple[dict[str, Any], str]] = {}
    bodies: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for role, expected in ROLES.items():
        paths = discovered.get(role, [])
        if not paths:
            validation.error(
                "FILES", f"required file missing: .opencode/agent/{role}.md"
            )
            continue
        path = paths[0]
        metadata, body = load_agent(path, validation)
        loaded[role] = (metadata, body)
        validate_role(validation, role, expected, metadata, body, descriptions, bodies)

    default = root_config.get("default_agent")
    default_metadata = loaded.get(str(default), ({}, ""))[0]
    if (
        default_metadata.get("mode") != "primary"
        or default_metadata.get("hidden") is True
    ):
        validation.error(
            "DEFAULT_AGENT",
            "configured default must resolve to a visible primary agent",
        )
    return loaded


def require_terms(
    validation: Validation, code: str, label: str, text: str, terms: tuple[str, ...]
) -> None:
    lowered = re.sub(r"\s+", " ", text).lower()
    missing = [term for term in terms if term.lower() not in lowered]
    if missing:
        validation.error(
            code, f"{label} missing required semantics: {', '.join(missing)}"
        )


def validate_standard_permissions(
    validation: Validation, roles: dict[str, tuple[dict[str, Any], str]]
) -> None:
    implementer = roles.get("implementer", ({}, ""))
    approved_package_patterns = {
        *(f"pdm run {command}" for command in IMPLEMENTER_PACKAGE_COMMANDS),
        *IMPLEMENTER_NPM_COMMANDS,
    }
    implementer_required = {
        **dict.fromkeys(approved_package_patterns, "allow"),
        **dict.fromkeys(FIXED_GIT_INSPECTION, "allow"),
        **dict.fromkeys(SHELL_METACHARACTER_DENIES, "deny"),
        **dict.fromkeys(LINE_BREAK_DENIES, "deny"),
    }
    implementer_required |= {
        "git diff --cached --check": "allow",
        "git diff --cached --stat": "allow",
        "git merge-base main HEAD": "allow",
        "git ls-files": "allow",
        "git switch": "allow",
        "git switch *": "allow",
        "git add": "allow",
        "git add *": "allow",
        "git commit": "allow",
        "git commit *": "allow",
        "git merge --no-ff *": "allow",
        "git reset --hard": "deny",
        "git reset --hard*": "deny",
        "git clean": "deny",
        "git clean *": "deny",
        "git checkout": "deny",
        "git checkout *": "deny",
        "git restore": "deny",
        "git restore *": "deny",
        "git stash": "deny",
        "git stash *": "deny",
        "git rebase": "deny",
        "git rebase *": "deny",
        "git cherry-pick": "deny",
        "git cherry-pick *": "deny",
        "git push": "deny",
        "git push *": "deny",
        "git push -f*": "deny",
        "git push --force*": "deny",
        "git push * -f*": "deny",
        "git push * --force*": "deny",
        "gh pr": "deny",
        "gh pr *": "deny",
        "gh pr merge": "deny",
        "gh pr merge*": "deny",
        "npm publish": "deny",
        "npm publish*": "deny",
        "pdm publish": "deny",
        "pdm publish*": "deny",
    }
    require_bash_rules(
        validation,
        "implementer",
        implementer[0],
        implementer_required,
        catch_all="deny",
    )
    implementer_bash = permission_action(implementer[0], "bash")
    if isinstance(implementer_bash, dict):
        for pattern, action in implementer_bash.items():
            if (
                action == "allow"
                and pattern.split(maxsplit=1)[0] in {"pdm", "npm", "npx"}
                and pattern not in approved_package_patterns
            ):
                validation.error(
                    "ROLE_PERMISSION",
                    f"implementer unapproved package command {pattern}",
                )
    require_bash_rules(
        validation,
        "ontoprism-team",
        roles.get("ontoprism-team", ({}, ""))[0],
        {
            **dict.fromkeys(FIXED_GIT_INSPECTION, "allow"),
            "pdm run validate-opencode-config": "allow",
            "pdm run validate-opencode-runtime": "allow",
            "git reset": "deny",
            "git reset *": "deny",
            "git clean": "deny",
            "git clean *": "deny",
            "git push": "deny",
            "git push *": "deny",
            "gh pr create": "deny",
            "gh pr create*": "deny",
            "gh pr merge": "deny",
            "gh pr merge*": "deny",
            "npm publish": "deny",
            "npm publish*": "deny",
            "pdm publish": "deny",
            "pdm publish*": "deny",
        },
        catch_all="deny",
    )
    read_only_roles = set(ROLES) - {
        "ontoprism-team",
        "implementer",
        "pr-test-analyzer",
    }
    for role in read_only_roles:
        require_bash_rules(
            validation,
            role,
            roles.get(role, ({}, ""))[0],
            {
                **dict.fromkeys(FIXED_GIT_INSPECTION, "allow"),
                "git reset *": "deny",
                "git clean *": "deny",
                "git push *": "deny",
                "gh pr *": "deny",
            },
            catch_all="deny",
        )


def validate_r3_contract(
    validation: Validation, r3_metadata: dict[str, Any], r3_body: str
) -> None:
    require_terms(
        validation,
        "R3_ISOLATION",
        "pr-test-analyzer prompt",
        r3_body,
        (
            "runs alone",
            "outside the worktree",
            "byte",
            "restore",
            "git status --porcelain",
            "git rev-parse HEAD",
            "never fix",
        ),
    )
    bash = permission_action(r3_metadata, "bash")
    if not isinstance(bash, dict) or next(iter(bash), None) != "*":
        validation.error(
            "R3_PERMISSION", "R3 bash permissions must place broad rule first"
        )
        return
    if bash.get("*") != "deny":
        validation.error("R3_PERMISSION", "R3 bash catch-all must be deny")
    required_denies = (
        "git add",
        "git add *",
        "git commit",
        "git commit *",
        "git merge",
        "git merge *",
        "git rebase",
        "git rebase *",
        "git restore",
        "git restore *",
        "git checkout",
        "git checkout *",
        "git clean",
        "git clean *",
        "git stash",
        "git stash *",
        "git push",
        "git push *",
        "git push -f*",
        "git push --force*",
        "gh pr",
        "gh pr *",
        "git reset",
        "git reset *",
    )
    for pattern in required_denies:
        if bash.get(pattern) != "deny":
            validation.error("R3_PERMISSION", f"R3 must deny {pattern}")
    validate_deny_order(
        validation,
        "R3_PERMISSION",
        "pr-test-analyzer",
        bash,
        required_denies,
    )
    for pattern in (
        "git status --porcelain",
        "git status --short --branch",
        "git rev-parse HEAD",
        "pdm run agent-test *",
    ):
        if bash.get(pattern) != "allow":
            validation.error("R3_PERMISSION", f"R3 must allow {pattern}")


def validate_role_contracts(
    validation: Validation, roles: dict[str, tuple[dict[str, Any], str]]
) -> None:
    implementer = roles.get("implementer", ({}, ""))
    require_terms(
        validation,
        "IMPLEMENTER_PROCESS",
        "implementer prompt",
        implementer[1],
        (
            "strict TDD",
            "pdm run verify",
            "clean worktree",
            "manual user action",
            "report the ready state to the user",
            "never `gh pr merge`",
        ),
    )
    if "fallback_models" in implementer[0]:
        validation.error(
            "IMPLEMENTER_FALLBACK", "fallback_models belongs only in root opencode.json"
        )

    orchestrator = roles.get("ontoprism-team", ({}, ""))[1]
    require_terms(
        validation,
        "ORCHESTRATOR_PROCESS",
        "ontoprism-team prompt",
        orchestrator,
        (
            "ordinary task",
            "milestone task",
            "semantic",
            "pdm run verify",
            "git merge --no-ff",
            "R3",
            "runs alone",
            "reduced",
            "never `gh pr merge`",
            "human merges",
            "manual user actions",
        ),
    )
    validate_standard_permissions(validation, roles)
    for reserve in RESERVES:
        body = roles.get(reserve, ({}, ""))[1]
        require_terms(
            validation,
            "RESERVE_POLICY",
            f"{reserve} prompt",
            body,
            (
                "current conversation",
                "metered",
                "approval",
                "never delegate",
                "never edit",
                "never merge",
            ),
        )
    all_nonreserve_bodies = "\n".join(
        body for name, (_, body) in roles.items() if name not in RESERVES
    )
    for reserve in RESERVES:
        if reserve in all_nonreserve_bodies:
            validation.error(
                "RESERVE_POLICY",
                f"{reserve} must not be named by automatic task routes",
            )

    r3_metadata, r3_body = roles.get("pr-test-analyzer", ({}, ""))
    validate_r3_contract(validation, r3_metadata, r3_body)


def validate_plugin(validation: Validation) -> dict[str, Any]:
    path = validation.require_file(".opencode/opencode-model-fallback.jsonc")
    shadow = validation.root / ".opencode" / "opencode-model-fallback.json"
    if shadow.exists():
        validation.error(
            "PLUGIN_SHADOW",
            "JSON shadow file must be absent because plugin 0.3.2 reads it first",
        )
    if path is None:
        return {}
    config = load_json(path, validation, "PLUGIN_CONFIG")
    expected = {
        "enabled": True,
        "fallback_models": [],
        "max_fallback_attempts": 1,
        "cooldown_seconds": 21600,
        "timeout_seconds": 0,
        "notify_on_fallback": True,
    }
    if config != expected:
        validation.error(
            "PLUGIN_CONFIG",
            "plugin config must equal the repository-required explicit settings",
        )
    if config.get("fallback_models") != []:
        validation.error(
            "GLOBAL_FALLBACK", "global fallback_models must be exactly empty"
        )
    comment = path.read_text().lower()
    if (
        "explicit per-agent" not in comment
        or "tracked repository" not in comment
        or "never places aws" not in comment
        or "automatic fallback chain" not in comment
    ):
        validation.error(
            "PLUGIN_CONFIG",
            "plugin comment must limit automation to explicit per-agent fallback "
            "and scope the repository's exclusion of AWS",
        )
    return config


def validate_command(validation: Validation) -> None:
    path = validation.require_file(".opencode/command/review-pr.md")
    if path is None:
        return
    metadata, body = load_agent(path, validation)
    if metadata.get("agent") != "ontoprism-team":
        validation.error("REVIEW_COMMAND", "review-pr command must use ontoprism-team")
    require_terms(
        validation,
        "REVIEW_COMMAND",
        "review-pr command",
        body,
        (
            "committed",
            "clean",
            "pdm run verify",
            "pdm run validate-opencode-config",
            "pdm run validate-opencode-runtime",
            "dispatch `implementer` to run exact `pdm run verify`",
            "in parallel",
            "runs alone",
            "same HEAD",
            "reduced",
            "PRE-PR REVIEW CONVERGED",
            "no push",
            "no PR",
            "never `gh pr merge`",
            "launches fresh cli processes",
            "quit and restart opencode",
            *tuple(sorted(REVIEWERS)),
        ),
    )
    if "ready to merge" in body.lower() or "merge-ready" in body.lower():
        validation.error(
            "REVIEW_COMMAND", "review-pr must not make a merge-readiness claim"
        )


def validate_agents_document(validation: Validation) -> None:
    path = validation.require_file("AGENTS.md")
    if path is None:
        return
    text = path.read_text()
    require_terms(
        validation,
        "AGENTS_PROCESS",
        "AGENTS.md pre-PR process",
        text,
        (
            "R1 correctness",
            "R2 silent failure",
            "R3 test validity",
            "R4 comment accuracy",
            "R5 type design",
            "per dimension",
            *tuple(sorted(REVIEWERS)),
            "outside the worktree",
            "byte-exact",
            "human merges",
            "Only the implementer makes lasting repository code, test, "
            "documentation, fix, or commit edits",
            "All pushes and PR creation, updates, and mutations are manual "
            "user actions",
        ),
    )
    require_terms(
        validation,
        "AGENTS_GOVERNANCE",
        "AGENTS.md governance process",
        text,
        (
            "rg --no-ignore",
            "`tmp/` is gitignored (`.gitignore:2`)",
            "$TMPDIR/opencode/",
            "see D49",
            "Ephemeral planning/handover docs live in `tmp/plans/`",
            "under `./tmp/plans/`, not in `.opencode/plans/` or `docs/`",
            "must not depend on ephemeral artifacts there",
            "policy text and executable reproducibility commands may name the location",
        ),
    )


def validate_gitignore(validation: Validation) -> None:
    path = validation.require_file(".gitignore")
    if path is None:
        return
    lines = path.read_text().splitlines()
    for local_config in (
        "/.opencode/opencode.json",
        "/.opencode/opencode.jsonc",
    ):
        if local_config not in lines:
            validation.error(
                "GITIGNORE", f".gitignore must contain exact line {local_config}"
            )


def validate_local_configs(validation: Validation) -> None:
    allowed = {"$schema", "lsp", "mcp"}
    relatives = (
        ".opencode/opencode.json",
        ".opencode/opencode.jsonc",
    )
    existing = [
        relative for relative in relatives if (validation.root / relative).exists()
    ]
    if len(existing) > 1:
        validation.error("LOCAL_CONFIG", "machine-local JSON and JSONC cannot coexist")
    for relative in existing:
        path = validation.root / relative
        config = load_json(path, validation, "LOCAL_CONFIG")
        for key in config.keys() - allowed:
            validation.error("LOCAL_CONFIG", f"forbidden top-level key {key}")


def validate_forbidden_content(validation: Validation) -> None:
    forbidden = (
        (re.compile("/" + "Users/"), "local absolute user path"),
        (re.compile(r"postgres(?:ql)?://", re.IGNORECASE), "database URL"),
        (
            re.compile(
                r"\b(?:password|passwd|api[_-]?key|client[_-]?secret)\s*[:=]\s*\S+",
                re.IGNORECASE,
            ),
            "credential-shaped value",
        ),
    )
    ephemeral_path = re.compile(r"(?:^|[\s`'\"])(?:\./)?" + "tmp" + "/")
    paths: list[Path] = []
    for relative in TRACKED_PROCESS:
        target = validation.root / relative
        if target.is_file():
            paths.append(target)
        elif target.is_dir():
            paths.extend(path for path in target.rglob("*") if path.is_file())
    for path in paths:
        text = path.read_text(errors="replace")
        for pattern, label in forbidden:
            if pattern.search(text):
                validation.error(
                    "FORBIDDEN_CONTENT",
                    f"{path.relative_to(validation.root)} contains {label}",
                )
        relative = path.relative_to(validation.root)
        if relative not in {
            Path("AGENTS.md"),
            Path(".gitignore"),
        } and ephemeral_path.search(text):
            validation.error(
                "FORBIDDEN_CONTENT",
                f"{relative} contains temporary repository path",
            )


def validate(root: Path) -> list[str]:
    validation = Validation(root)
    root_config = validate_root(validation)
    roles = validate_roles(validation, root_config)
    validate_role_contracts(validation, roles)
    validate_plugin(validation)
    validate_command(validation)
    validate_agents_document(validation)
    validate_gitignore(validation)
    validate_local_configs(validation)
    validate_forbidden_content(validation)
    return validation.errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    errors = validate(args.root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        print(f"OpenCode configuration validation failed with {len(errors)} error(s).")
        return 1
    print("OpenCode configuration validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
