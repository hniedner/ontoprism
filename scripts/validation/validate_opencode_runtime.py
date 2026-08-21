#!/usr/bin/env python3
"""Validate the installed OpenCode CLI against the repository runtime contract."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validation.validate_opencode_config import (  # noqa: E402
    PLUGIN,
    ROLES,
    Validation,
    load_agent,
    load_json,
    validate_local_configs,
)

MODEL_IDS = {contract[0] for contract in ROLES.values()}
READ_ONLY_ROLES = set(ROLES) - {"implementer", "pr-test-analyzer"}
GOVERNANCE_ENV_PREFIX = "OPENCODE_CONFIG"


class RuntimeContractError(RuntimeError):
    """A sanitized OpenCode runtime-contract failure."""


def parse_json_object(output: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeContractError(f"{label} output is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeContractError(f"{label} output is not a JSON object")
    return value


def effective_action(rules: list[dict[str, Any]], command: str) -> str | None:
    action = None
    for rule in rules:
        pattern = rule.get("pattern")
        if (
            rule.get("permission") == "bash"
            and isinstance(pattern, str)
            and fnmatch.fnmatchcase(command, pattern)
        ):
            candidate = rule.get("action")
            action = candidate if isinstance(candidate, str) else action
    return action


def governance_environment(
    base: Mapping[str, str], *, controlled: dict[str, str]
) -> dict[str, str]:
    unexpected = sorted(
        name
        for name, value in base.items()
        if name.startswith(GOVERNANCE_ENV_PREFIX) and value != controlled.get(name)
    )
    if unexpected:
        raise RuntimeContractError("unexpected governance environment override")
    env = {
        key: value
        for key, value in base.items()
        if not key.startswith(GOVERNANCE_ENV_PREFIX)
    }
    env |= controlled
    return env


def redact_diagnostic(value: str) -> str:
    redacted = re.sub("/" + r"Users/\S+", "<local-path>", value)
    redacted = re.sub(r"postgres(?:ql)?://\S+", "<database-url>", redacted, flags=re.I)
    redacted = re.sub(
        r"(?i)\b(?:password|passwd|api[_-]?key|client[_-]?secret)\s*[:=]\s*\S+",
        "<credential>",
        redacted,
    )
    return " ".join(redacted.split())[:240]


def validate_mcp_status(output: str, expected: set[str]) -> list[str]:
    lowered = re.sub(r"\x1b\[[0-9;]*m", "", output).lower()
    lines = lowered.splitlines()
    errors: list[str] = []
    for name in sorted(expected):
        matching = [
            line for line in lines if re.search(rf"\b{re.escape(name.lower())}\b", line)
        ]
        if not any(
            re.search(r"\bconnected\b", line) and "not connected" not in line
            for line in matching
        ):
            errors.append(f"MCP {name} is not connected")
    return errors


def validate_permission_contract(
    resolved: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> list[str]:
    if any(
        resolved[start : start + len(expected)] == expected
        for start in range(len(resolved) - len(expected) + 1)
    ):
        return []
    position = 0
    for rule in resolved:
        if position < len(expected) and rule == expected[position]:
            position += 1
    if position == len(expected):
        return [
            "resolved permission contract is not an exact contiguous project ruleset"
        ]
    return ["resolved permission contract omits or reorders project rules"]


def expected_permission_contract(root: Path, name: str) -> list[dict[str, Any]]:
    validation = Validation(root)
    metadata, _ = load_agent(root / ".opencode" / "agent" / f"{name}.md", validation)
    if validation.errors:
        raise RuntimeContractError(f"cannot read repository permissions for {name}")
    permission = metadata.get("permission")
    if not isinstance(permission, dict):
        raise RuntimeContractError(f"repository permissions for {name} are invalid")
    rules: list[dict[str, Any]] = []
    for tool, value in permission.items():
        if isinstance(value, str):
            rules.append({"permission": tool, "pattern": "*", "action": value})
        elif isinstance(value, dict):
            rules.extend(
                {"permission": tool, "pattern": pattern, "action": action}
                for pattern, action in value.items()
            )
    return rules


def configured_model(agent: dict[str, Any]) -> str | None:
    model = agent.get("model")
    if isinstance(model, str):
        return model
    if isinstance(model, dict):
        provider = model.get("providerID")
        model_id = model.get("modelID")
        if isinstance(provider, str) and isinstance(model_id, str):
            return f"{provider}/{model_id}"
    return None


def validate_agent_permissions(
    name: str, bash_rules: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    catch_alls = [
        rule
        for rule in bash_rules
        if rule.get("permission") == "bash" and rule.get("pattern") == "*"
    ]
    expected_catch_all = "deny"
    if not catch_alls or catch_alls[-1].get("action") != expected_catch_all:
        errors.append(f"{name} resolved bash catch-all must be {expected_catch_all}")
    if name in READ_ONLY_ROLES:
        for command in ("touch forbidden", "git status"):
            expected = "allow" if command == "git status" else "deny"
            if effective_action(bash_rules, command) != expected:
                errors.append(
                    f"{name} effective action for {command} must be {expected}"
                )
    elif name == "pr-test-analyzer":
        for command, expected in (
            ("cp source target", "allow"),
            ("git status", "allow"),
            ("git commit", "deny"),
            ("git push --force", "deny"),
            ("gh pr merge", "deny"),
            ("touch forbidden", "deny"),
        ):
            if effective_action(bash_rules, command) != expected:
                errors.append(
                    f"{name} effective action for {command} must be {expected}"
                )
    else:
        for command, expected in (
            ("git commit change", "allow"),
            ("git push --force", "deny"),
            ("gh pr merge", "deny"),
            ("npm publish", "deny"),
        ):
            if effective_action(bash_rules, command) != expected:
                errors.append(
                    f"{name} effective action for {command} must be {expected}"
                )
    return errors


def validate_resolved_agent(name: str, agent: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_model, expected_mode, _, _ = ROLES[name]
    if configured_model(agent) != expected_model:
        errors.append(f"{name} resolved model is not repository-required")
    if agent.get("mode") != expected_mode:
        errors.append(f"{name} resolved mode is not repository-required")
    rules = agent.get("permission")
    if not isinstance(rules, list):
        return [*errors, f"{name} resolved permissions are not an array"]
    errors.extend(
        validate_agent_permissions(
            name, [rule for rule in rules if isinstance(rule, dict)]
        )
    )
    return errors


def validate_implementer_config(agents: object) -> list[str]:
    if not isinstance(agents, dict) or not isinstance(agents.get("implementer"), dict):
        return ["resolved implementer config is missing"]
    implementer = agents["implementer"]
    errors: list[str] = []
    if implementer.get("model") != "openai/gpt-5.6-sol":
        errors.append("resolved implementer model is incorrect")
    fallback = implementer.get("fallback_models")
    options = implementer.get("options")
    if fallback is None and isinstance(options, dict):
        fallback = options.get("fallback_models")
    if fallback != ["github-copilot/gpt-5.6-sol"]:
        errors.append("resolved implementer fallback is incorrect")
    return errors


def validate_resolved_config(
    config: dict[str, Any], *, require_local_markers: bool
) -> list[str]:
    errors: list[str] = []
    if config.get("default_agent") != "ontoprism-team":
        errors.append("resolved default agent is not ontoprism-team")
    if config.get("plugin") != [PLUGIN]:
        errors.append("resolved plugin list is not the pinned repository plugin")
    command = config.get("command")
    review = command.get("review-pr") if isinstance(command, dict) else None
    if not isinstance(review, dict) or review.get("agent") != "ontoprism-team":
        errors.append("resolved review-pr command does not use ontoprism-team")
    errors.extend(validate_implementer_config(config.get("agent")))
    if require_local_markers:
        for marker in ("lsp", "mcp"):
            if not isinstance(config.get(marker), dict) or not config[marker]:
                errors.append(f"resolved local {marker} marker is missing")
    return errors


def run_command(
    arguments: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed opencode commands only
            arguments,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeContractError(f"{arguments[0]} executable is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeContractError(f"{arguments[0]} command timed out") from exc
    except OSError as exc:
        raise RuntimeContractError(f"{arguments[0]} command could not start") from exc
    if result.returncode != 0:
        detail = redact_diagnostic(result.stderr or result.stdout)
        raise RuntimeContractError(
            f"{Path(arguments[0]).name} command exited {result.returncode}: {detail}"
        )
    return result


def isolated_environment(directory: Path, base: dict[str, str]) -> dict[str, str]:
    env = base.copy()
    for variable, name in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        target = directory / name
        target.mkdir()
        env[variable] = str(target)
    env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "1"
    env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] = "1"
    return env


def local_config_exists(project: Path) -> bool:
    return any(
        (project / relative).is_file()
        for relative in (
            ".opencode/opencode.json",
            ".opencode/opencode.jsonc",
        )
    )


def collect_local_agents(config: dict[str, Any]) -> tuple[set[str], list[str]]:
    models: set[str] = set()
    errors: list[str] = []
    agents = config.get("agent", {})
    if not isinstance(agents, dict):
        return models, ["machine-local agents are invalid"]
    for name, agent in agents.items():
        if isinstance(agent, dict) and isinstance(agent.get("model"), str):
            models.add(agent["model"])
        else:
            errors.append(f"machine-local agent {name} has no valid model")
    return models, errors


def collect_local_mcp(config: dict[str, Any]) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    errors: list[str] = []
    mcp = config.get("mcp", {})
    if not isinstance(mcp, dict):
        return names, ["machine-local MCP configuration is invalid"]
    for name, entry in mcp.items():
        if not isinstance(entry, dict):
            errors.append(f"MCP {name} is invalid")
        elif entry.get("enabled", True) is not False:
            names.add(name)
    return names, errors


def local_runtime_contract(project: Path) -> tuple[set[str], set[str], list[str]]:
    models: set[str] = set()
    mcp_names: set[str] = set()
    errors: list[str] = []
    for relative in (".opencode/opencode.json", ".opencode/opencode.jsonc"):
        path = project / relative
        if not path.is_file():
            continue
        validation = Validation(project)
        config = load_json(path, validation, "LOCAL_CONFIG")
        if validation.errors:
            errors.append("machine-local OpenCode config cannot be parsed")
            continue
        local_models, agent_errors = collect_local_agents(config)
        models.update(local_models)
        errors.extend(agent_errors)
        local_mcp, mcp_errors = collect_local_mcp(config)
        mcp_names.update(local_mcp)
        errors.extend(mcp_errors)
    return models, mcp_names, errors


def validate_plugin_sentinel(
    root: Path, env: dict[str, str], isolated: Path
) -> str | None:
    sentinel_env = governance_environment(
        env,
        controlled={
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                {"agent": {"implementer": {"fallback_models": ["invalid-sentinel"]}}}
            )
        },
    )
    sentinel = run_command(
        ["opencode", "debug", "config", "--print-logs", "--log-level", "DEBUG"],
        cwd=root,
        env=sentinel_env,
    )
    evidence = sentinel.stdout + sentinel.stderr
    for path in isolated.rglob("*"):
        if path.is_file():
            evidence += path.read_text(errors="ignore")
    markers = ("Invalid fallback_models entry", "implementer", "invalid-sentinel")
    if all(marker in evidence for marker in markers):
        return None
    return "fallback plugin did not report the isolated implementer sentinel"


def validate_layered_project(
    root: Path, project: Path, env: dict[str, str], expected_mcp: set[str]
) -> list[str]:
    layered_env = governance_environment(
        env,
        controlled={
            "OPENCODE_CONFIG": str(root / "opencode.json"),
            "OPENCODE_CONFIG_DIR": str(root / ".opencode"),
        },
    )
    layered = parse_json_object(
        run_command(
            ["opencode", "debug", "config"], cwd=project, env=layered_env
        ).stdout,
        "layered debug config",
    )
    errors = validate_resolved_config(
        layered, require_local_markers=local_config_exists(project)
    )
    layered_agents = layered.get("agent")
    if not isinstance(layered_agents, dict) or not set(ROLES) <= set(layered_agents):
        errors.append("layered project agents are incomplete")
    if expected_mcp:
        status = run_command(["opencode", "mcp", "list"], cwd=project, env=layered_env)
        errors.extend(validate_mcp_status(status.stdout + status.stderr, expected_mcp))
    return errors


def validate_native_agents(root: Path, env: dict[str, str]) -> list[str]:
    errors: list[str] = []
    listed = run_command(["opencode", "agent", "list"], cwd=root, env=env).stdout
    if any(name not in listed for name in ROLES):
        errors.append("project agent list is incomplete")
    for name in ROLES:
        agent = parse_json_object(
            run_command(["opencode", "debug", "agent", name], cwd=root, env=env).stdout,
            f"debug agent {name}",
        )
        errors.extend(validate_resolved_agent(name, agent))
        resolved_rules = agent.get("permission")
        if isinstance(resolved_rules, list):
            errors.extend(
                validate_permission_contract(
                    [rule for rule in resolved_rules if isinstance(rule, dict)],
                    expected_permission_contract(root, name),
                )
            )
    return errors


def validate_model_catalog(
    root: Path, env: dict[str, str], local_models: set[str]
) -> list[str]:
    models = set(
        run_command(["opencode", "models"], cwd=root, env=env).stdout.splitlines()
    )
    errors: list[str] = []
    if not models >= MODEL_IDS:
        errors.append("configured model IDs are absent from the local catalog")
    if not models >= local_models:
        errors.append("machine-local agent model is absent from the local catalog")
    return errors


def validate_runtime(root: Path, project: Path) -> None:
    if shutil.which("opencode") is None:
        raise RuntimeContractError("opencode CLI is not installed")
    local_validation = Validation(project)
    validate_local_configs(local_validation)
    if local_validation.errors:
        raise RuntimeContractError("machine-local OpenCode governance is invalid")
    local_models, expected_mcp, local_errors = local_runtime_contract(project)
    if local_errors:
        raise RuntimeContractError("; ".join(local_errors))
    base_env = governance_environment(os.environ, controlled={})

    with tempfile.TemporaryDirectory(prefix="opencode-runtime-") as temporary:
        isolated = Path(temporary)
        env = isolated_environment(isolated, base_env)
        config = parse_json_object(
            run_command(["opencode", "debug", "config"], cwd=root, env=env).stdout,
            "debug config",
        )
        errors = validate_resolved_config(config, require_local_markers=False)
        errors.extend(validate_native_agents(root, env))
        errors.extend(validate_model_catalog(root, base_env, local_models))
        run_command(["opencode", "debug", "startup"], cwd=root, env=env)
        sentinel_error = validate_plugin_sentinel(root, env, isolated)
        if sentinel_error:
            errors.append(sentinel_error)
        errors.extend(validate_layered_project(root, project, env, expected_mcp))

    if errors:
        raise RuntimeContractError("; ".join(dict.fromkeys(errors)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--project", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    project = (args.project or root).resolve()
    try:
        validate_runtime(root, project)
    except RuntimeContractError as exc:
        print(f"OpenCode runtime validation failed: {exc}")
        return 1
    print("OpenCode runtime validation passed (no model calls).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
