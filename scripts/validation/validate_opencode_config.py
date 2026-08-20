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
    ".opencode/opencode-model-fallback.jsonc",
    ".opencode/agent",
    ".opencode/command",
)
MIN_DESCRIPTION_LENGTH = 24
MIN_PROMPT_LENGTH = 100


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
            index += 2
            end = text.find("*/", index)
            index = len(text) if end == -1 else end + 2
        else:
            output.append(char)
            index += 1
    return "".join(output)


def load_json(path: Path, validation: Validation, code: str) -> dict[str, Any]:
    try:
        value = json.loads(strip_jsonc_comments(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
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
) -> None:
    bash = permission_action(metadata, "bash")
    if not isinstance(bash, dict) or next(iter(bash), None) != "*":
        validation.error(
            "ROLE_PERMISSION", f"{role} bash permissions must put the broad rule first"
        )
        return
    for pattern, action in required.items():
        if bash.get(pattern) != action:
            validation.error(
                "ROLE_PERMISSION", f"{role} bash rule {pattern} must be {action}"
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
    for permission, action in (("edit", edit), ("task", task)):
        if permission_action(metadata, permission) != action:
            validation.error(
                "ROLE_PERMISSION", f"{role} {permission} permission must be {action}"
            )
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


def validate_roles(
    validation: Validation, root_config: dict[str, Any]
) -> dict[str, tuple[dict[str, Any], str]]:
    agent_dir = validation.root / ".opencode" / "agent"
    if (agent_dir / "pr-reviewer.md").exists():
        validation.error("FILES", "stale .opencode/agent/pr-reviewer.md must be absent")
    loaded: dict[str, tuple[dict[str, Any], str]] = {}
    bodies: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for role, expected in ROLES.items():
        path = validation.require_file(f".opencode/agent/{role}.md")
        if path is None:
            continue
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
    require_bash_rules(
        validation,
        "implementer",
        implementer[0],
        {
            "git reset --hard*": "deny",
            "git clean *": "deny",
            "git push --force*": "deny",
            "gh pr merge*": "deny",
            "npm publish*": "deny",
            "pdm publish*": "deny",
        },
    )
    implementer_bash = permission_action(implementer[0], "bash")
    if isinstance(implementer_bash, dict) and "gh pr create*" in implementer_bash:
        validation.error(
            "ROLE_PERMISSION",
            "implementer PR creation must remain prompt-controlled through broad ask",
        )

    require_bash_rules(
        validation,
        "ontoprism-team",
        roles.get("ontoprism-team", ({}, ""))[0],
        {
            "pdm run verify*": "allow",
            "git reset *": "deny",
            "git clean *": "deny",
            "git push *": "deny",
            "gh pr create*": "deny",
            "gh pr merge*": "deny",
            "npm publish*": "deny",
            "pdm publish*": "deny",
        },
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
                "git reset *": "deny",
                "git clean *": "deny",
                "git push *": "deny",
                "gh pr *": "deny",
            },
        )


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
            "explicitly dispatched",
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
    else:
        required_denies = (
            "git add *",
            "git commit *",
            "git merge *",
            "git rebase *",
            "git restore *",
            "git checkout *",
            "git clean *",
            "git stash *",
            "git push *",
            "gh pr *",
            "git reset *",
        )
        for pattern in required_denies:
            if bash.get(pattern) != "deny":
                validation.error("R3_PERMISSION", f"R3 must deny {pattern}")
        keys = list(bash)
        if any(
            pattern in keys and keys.index(pattern) == 0 for pattern in required_denies
        ):
            validation.error(
                "R3_PERMISSION", "R3 narrow denies must follow its broad rule"
            )


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
            "PLUGIN_CONFIG", "plugin config must equal the approved explicit settings"
        )
    if config.get("fallback_models") != []:
        validation.error(
            "GLOBAL_FALLBACK", "global fallback_models must be exactly empty"
        )
    comment = path.read_text().lower()
    if (
        "explicit per-agent" not in comment
        or "aws" not in comment
        or "never automatic" not in comment
    ):
        validation.error(
            "PLUGIN_CONFIG",
            "plugin comment must limit automation to explicit per-agent fallback "
            "and exclude AWS",
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
            "in parallel",
            "runs alone",
            "same HEAD",
            "reduced",
            "PRE-PR REVIEW CONVERGED",
            "no push",
            "no PR",
            "never `gh pr merge`",
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
        ),
    )


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
        (
            re.compile(r"(?:^|[\s`'\"])(?:\./)?" + "tmp" + "/"),
            "temporary repository path",
        ),
    )
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


def validate(root: Path) -> list[str]:
    validation = Validation(root)
    root_config = validate_root(validation)
    roles = validate_roles(validation, root_config)
    validate_role_contracts(validation, roles)
    validate_plugin(validation)
    validate_command(validation)
    validate_agents_document(validation)
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
