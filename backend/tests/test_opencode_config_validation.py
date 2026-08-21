from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.validation.validate_opencode_config import (
    RESERVES,
    ROLES,
    Validation,
    load_agent,
    validate,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    for name in ("opencode.json", "AGENTS.md", ".gitignore"):
        shutil.copy2(ROOT / name, tmp_path / name)
    (tmp_path / ".opencode").mkdir()
    for directory in ("agent", "command"):
        shutil.copytree(
            ROOT / ".opencode" / directory, tmp_path / ".opencode" / directory
        )
    shutil.copy2(
        ROOT / ".opencode" / "opencode-model-fallback.jsonc",
        tmp_path / ".opencode" / "opencode-model-fallback.jsonc",
    )
    return tmp_path


def replace(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def update_root_config(root: Path, update: Callable[[dict[str, object]], None]) -> None:
    path = root / "opencode.json"
    config = json.loads(path.read_text())
    update(config)
    path.write_text(json.dumps(config))


def remove_implementer_fallback(config: dict[str, object]) -> None:
    agents = config["agent"]
    assert isinstance(agents, dict)
    implementer = agents["implementer"]
    assert isinstance(implementer, dict)
    implementer["fallback_models"] = []


def add_bedrock_fallback(config: dict[str, object]) -> None:
    agents = config["agent"]
    assert isinstance(agents, dict)
    implementer = agents["implementer"]
    assert isinstance(implementer, dict)
    implementer["fallback_models"] = ["amazon-bedrock/example"]


def test_checked_out_opencode_configuration_is_valid(config_root: Path) -> None:
    assert validate(config_root) == []


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("opencode.json", "FILES: required file missing: opencode.json"),
        (
            ".opencode/agent/architect.md",
            "FILES: required file missing: .opencode/agent/architect.md",
        ),
    ],
)
def test_required_files_are_enforced(
    config_root: Path, relative: str, expected: str
) -> None:
    (config_root / relative).unlink()

    assert expected in validate(config_root)


def test_stale_consolidated_reviewer_is_rejected(config_root: Path) -> None:
    shutil.copy2(
        config_root / ".opencode/agent/architect.md",
        config_root / ".opencode/agent/pr-reviewer.md",
    )

    assert "FILES: stale .opencode/agent/pr-reviewer.md must be absent" in validate(
        config_root
    )


def test_extra_project_agent_file_is_rejected(config_root: Path) -> None:
    shutil.copy2(
        config_root / ".opencode/agent/architect.md",
        config_root / ".opencode/agent/writer.md",
    )

    assert "FILES: unexpected project agent writer" in validate(config_root)


def test_duplicate_agent_name_across_supported_directories_is_rejected(
    config_root: Path,
) -> None:
    plural = config_root / ".opencode/agents"
    plural.mkdir()
    shutil.copy2(
        config_root / ".opencode/agent/architect.md",
        plural / "architect.md",
    )

    assert "FILES: duplicate project agent architect" in validate(config_root)


@pytest.mark.parametrize(
    ("relative", "old", "new", "expected"),
    [
        (
            "opencode.json",
            '"$schema": "https://opencode.ai/config.json"',
            '"$schema": "invalid"',
            "ROOT_CONFIG: root $schema",
        ),
        (
            "opencode.json",
            '"plugin": ["@razroo/opencode-model-fallback@0.3.2"]',
            '"plugin": []',
            "ROOT_CONFIG: plugin list",
        ),
        (
            "opencode.json",
            '"default_agent": "ontoprism-team"',
            '"default_agent": "implementer"',
            "DEFAULT_AGENT: default_agent must be ontoprism-team",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "mode: primary",
            "mode: subagent",
            "DEFAULT_AGENT: configured default",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "mode: primary",
            "mode: primary\nhidden: true",
            "DEFAULT_AGENT: ontoprism-team must be a visible primary",
        ),
        (
            ".opencode/agent/architect.md",
            "model: github-copilot/gpt-5.6-sol",
            "model: github-copilot/wrong",
            "ROLE_MODEL: architect model",
        ),
        (
            ".opencode/agent/architect.md",
            "mode: subagent",
            "mode: primary",
            "ROLE_MODE: architect mode",
        ),
        (
            ".opencode/agent/architect.md",
            "edit: deny",
            "edit: allow",
            "ROLE_PERMISSION: architect edit permission",
        ),
        (
            ".opencode/agent/implementer.md",
            "task: deny",
            "task: allow",
            "ROLE_PERMISSION: implementer task permission",
        ),
        (
            ".opencode/agent/architect.md",
            "description: Designs technical plans and acceptance contracts that "
            "preserve ONTOPRISM boundaries and end-to-end semantics.",
            "description: short",
            "ROLE_BODY: architect needs a nontrivial description",
        ),
        (
            ".opencode/agent/implementer.md",
            "model: openai/gpt-5.6-sol",
            "model: openai/gpt-5.6-sol\nfallback_models: []",
            "IMPLEMENTER_FALLBACK: fallback_models belongs only",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "ordinary task",
            "standard task",
            "ORCHESTRATOR_PROCESS: ontoprism-team prompt missing required "
            "semantics: ordinary task",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "milestone task",
            "release task",
            "ORCHESTRATOR_PROCESS: ontoprism-team prompt missing required "
            "semantics: milestone task",
        ),
        (
            ".opencode/agent/ontoprism-team.md",
            "Never `gh pr merge`; a human merges.",
            "A human handles completion.",
            "ORCHESTRATOR_PROCESS: ontoprism-team prompt missing required "
            "semantics: never `gh pr merge`, human merges",
        ),
        (
            ".opencode/agent/bedrock-gpt-reserve.md",
            "explicit approval",
            "permission",
            "RESERVE_POLICY: bedrock-gpt-reserve prompt missing required "
            "semantics: approval",
        ),
        (
            ".opencode/agent/pr-test-analyzer.md",
            "outside the worktree",
            "elsewhere",
            "R3_ISOLATION: pr-test-analyzer prompt missing required semantics: "
            "outside the worktree",
        ),
        (
            ".opencode/command/review-pr.md",
            "agent: ontoprism-team",
            "agent: implementer",
            "REVIEW_COMMAND: review-pr command must use ontoprism-team",
        ),
        (
            ".opencode/command/review-pr.md",
            "R3 `pr-test-analyzer`",
            "R3 test reviewer",
            "REVIEW_COMMAND: review-pr command missing required semantics: "
            "pr-test-analyzer",
        ),
        (
            ".opencode/command/review-pr.md",
            "Do not claim merge readiness.",
            "Report ready to merge.",
            "REVIEW_COMMAND: review-pr must not make a merge-readiness claim",
        ),
    ],
)
def test_role_and_process_contract_mutations_are_rejected(
    config_root: Path,
    relative: str,
    old: str,
    new: str,
    expected: str,
) -> None:
    replace(config_root, relative, old, new)

    assert any(error.startswith(expected) for error in validate(config_root))


def test_duplicate_role_prompt_is_rejected(config_root: Path) -> None:
    architect = (config_root / ".opencode/agent/architect.md").read_text()
    architect_body = architect.split("\n---\n", 1)[1]
    ontology = config_root / ".opencode/agent/ontology-engineer.md"
    ontology_text = ontology.read_text()
    ontology.write_text(
        ontology_text.split("\n---\n", 1)[0] + "\n---\n" + architect_body
    )

    assert "ROLE_BODY: ontology-engineer duplicates architect prompt body" in validate(
        config_root
    )


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        (
            remove_implementer_fallback,
            "IMPLEMENTER_FALLBACK: implementer fallback_models",
        ),
        (add_bedrock_fallback, "IMPLEMENTER_FALLBACK: automatic root routes"),
    ],
)
def test_automatic_fallback_routes_are_closed(
    config_root: Path,
    update: Callable[[dict[str, object]], None],
    expected: str,
) -> None:
    update_root_config(config_root, update)

    assert any(error.startswith(expected) for error in validate(config_root))


def test_reserve_cannot_be_auto_dispatched(config_root: Path) -> None:
    replace(
        config_root,
        ".opencode/agent/ontoprism-team.md",
        "Reserve agents are manual tools and are never automatic routes.",
        "Automatically dispatch bedrock-gpt-reserve.",
    )

    assert (
        "RESERVE_POLICY: bedrock-gpt-reserve must not be named by automatic task routes"
        in validate(config_root)
    )


@pytest.mark.parametrize(
    ("relative", "old", "new", "expected"),
    [
        (
            ".opencode/agent/pr-test-analyzer.md",
            '    "git commit *": deny\n',
            "",
            "R3_PERMISSION: R3 must deny git commit *",
        ),
        (
            ".opencode/agent/pr-test-analyzer.md",
            '    "git status --porcelain": allow\n',
            '    "git commit *": deny\n    "git status --porcelain": allow\n',
            "R3_PERMISSION: pr-test-analyzer deny git commit * must follow all "
            "Git allow rules",
        ),
        (
            ".opencode/agent/pr-test-analyzer.md",
            '  bash:\n    "*": deny\n',
            "  bash:\n",
            "R3_PERMISSION: R3 bash permissions must place broad rule first",
        ),
        (
            ".opencode/agent/architect.md",
            '    "git show --stat --oneline HEAD": allow\n',
            '    "git reset *": deny\n    "git show --stat --oneline HEAD": allow\n',
            "ROLE_PERMISSION: architect deny git reset * must follow all Git "
            "allow rules",
        ),
    ],
)
def test_permission_denies_have_effective_last_match_order(
    config_root: Path,
    relative: str,
    old: str,
    new: str,
    expected: str,
) -> None:
    replace(config_root, relative, old, new)

    assert expected in validate(config_root)


def test_plugin_shadow_is_rejected(config_root: Path) -> None:
    (config_root / ".opencode/opencode-model-fallback.json").write_text("{}")

    assert any(error.startswith("PLUGIN_SHADOW:") for error in validate(config_root))


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            '"fallback_models": []',
            '"fallback_models": ["github-copilot/example"]',
            "GLOBAL_FALLBACK: global fallback_models must be exactly empty",
        ),
        (
            "explicit per-agent",
            "configured",
            "PLUGIN_CONFIG: plugin comment must limit automation",
        ),
        (
            '"max_fallback_attempts": 1',
            '"max_fallback_attempts": 2',
            "PLUGIN_CONFIG: plugin config must equal the repository-required explicit "
            "settings",
        ),
    ],
)
def test_plugin_configuration_mutations_are_rejected(
    config_root: Path, old: str, new: str, expected: str
) -> None:
    replace(config_root, ".opencode/opencode-model-fallback.jsonc", old, new)

    assert any(error.startswith(expected) for error in validate(config_root))


def test_jsonc_comment_markers_inside_strings_are_preserved(config_root: Path) -> None:
    path = config_root / ".opencode/opencode-model-fallback.jsonc"
    path.write_text(
        path.read_text().replace(
            '"fallback_models": []',
            '"fallback_models": [],\n  "note": "https://example.invalid/*literal*/"',
        )
    )

    errors = validate(config_root)

    assert not any("cannot parse" in error for error in errors)


def test_unterminated_jsonc_comment_is_rejected(config_root: Path) -> None:
    path = config_root / ".opencode/opencode-model-fallback.jsonc"
    path.write_text(path.read_text() + "\n/* unterminated")

    assert any(
        error.startswith("PLUGIN_CONFIG: cannot parse")
        for error in validate(config_root)
    )


def test_portable_local_config_ignore_is_required(config_root: Path) -> None:
    replace(
        config_root,
        ".gitignore",
        "/.opencode/opencode.json\n",
        "",
    )

    assert (
        "GITIGNORE: .gitignore must contain exact line /.opencode/opencode.json"
        in validate(config_root)
    )


@pytest.mark.parametrize(
    ("relative", "text", "expected"),
    [
        (
            "opencode.json",
            "\n// " + "/" + "Users/example/config\n",
            "FORBIDDEN_CONTENT: opencode.json contains local absolute user path",
        ),
        (
            "opencode.json",
            "\n// " + "postgresql" + "://user:pw@host/db\n",
            "FORBIDDEN_CONTENT: opencode.json contains database URL",
        ),
        (
            ".opencode/agent/architect.md",
            "\napi_" + "key=example\n",
            "FORBIDDEN_CONTENT: .opencode/agent/architect.md contains "
            "credential-shaped value",
        ),
        (
            ".opencode/agent/architect.md",
            "\n" + "tmp" + "/plans/note.md\n",
            "FORBIDDEN_CONTENT: .opencode/agent/architect.md contains temporary "
            "repository path",
        ),
    ],
)
def test_sensitive_local_content_is_rejected(
    config_root: Path, relative: str, text: str, expected: str
) -> None:
    path = config_root / relative
    path.write_text(path.read_text() + text)

    assert expected in validate(config_root)


def test_agents_governance_rules_are_required(config_root: Path) -> None:
    replace(
        config_root,
        "AGENTS.md",
        "- **Ephemeral planning/handover docs live in `tmp/plans/` "
        "(gitignored), never tracked.**",
        "- **Planning notes are not tracked.**",
    )

    assert any(
        error.startswith("AGENTS_GOVERNANCE:") for error in validate(config_root)
    )


@pytest.mark.parametrize(
    "role",
    sorted(set(ROLES) - {"implementer", "pr-test-analyzer"}),
)
def test_read_only_agents_require_deny_by_default(config_root: Path, role: str) -> None:
    replace(
        config_root,
        f".opencode/agent/{role}.md",
        '  bash:\n    "*": deny\n',
        '  bash:\n    "*": ask\n',
    )

    assert f"ROLE_PERMISSION: {role} bash catch-all must be deny" in validate(
        config_root
    )


def test_r3_requires_deny_by_default(config_root: Path) -> None:
    replace(
        config_root,
        ".opencode/agent/pr-test-analyzer.md",
        '  bash:\n    "*": deny\n',
        '  bash:\n    "*": ask\n',
    )

    assert "R3_PERMISSION: R3 bash catch-all must be deny" in validate(config_root)


@pytest.mark.parametrize(
    "pattern",
    [
        "git add",
        "git commit",
        "git merge",
        "git rebase",
        "git restore",
        "git checkout",
        "git reset",
        "git clean",
        "git stash",
        "git push",
        "gh pr",
    ],
)
def test_r3_requires_bare_mutation_denies(config_root: Path, pattern: str) -> None:
    replace(
        config_root,
        ".opencode/agent/pr-test-analyzer.md",
        f'    "{pattern}": deny\n',
        "",
    )

    assert f"R3_PERMISSION: R3 must deny {pattern}" in validate(config_root)


@pytest.mark.parametrize(
    "pattern",
    [
        "git reset --hard",
        "git clean",
        "git push -f*",
        "git push --force*",
        "gh pr",
        "gh pr merge",
        "npm publish",
        "pdm publish",
    ],
)
def test_implementer_requires_bare_dangerous_command_denies(
    config_root: Path, pattern: str
) -> None:
    replace(
        config_root,
        ".opencode/agent/implementer.md",
        f'    "{pattern}": deny\n',
        "",
    )

    assert f"ROLE_PERMISSION: implementer bash rule {pattern} must be deny" in validate(
        config_root
    )


def write_local_config(config_root: Path, config: dict[str, object]) -> None:
    (config_root / ".opencode/opencode.json").write_text(json.dumps(config))


def test_machine_local_lsp_and_mcp_are_allowed(config_root: Path) -> None:
    write_local_config(
        config_root,
        {
            "$schema": "https://opencode.ai/config.json",
            "lsp": {"local": {"disabled": True}},
            "mcp": {"local": {"enabled": False}},
        },
    )

    assert not any(error.startswith("LOCAL_CONFIG:") for error in validate(config_root))


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("default_agent", "build", "forbidden top-level key default_agent"),
        ("plugin", [], "forbidden top-level key plugin"),
        ("permission", {}, "forbidden top-level key permission"),
        ("provider", {}, "forbidden top-level key provider"),
        ("command", {}, "forbidden top-level key command"),
        ("instructions", [], "forbidden top-level key instructions"),
        (
            "agent",
            {"local-reviewer": {"model": "github-copilot/gpt-5.6-sol"}},
            "forbidden top-level key agent",
        ),
    ],
)
def test_machine_local_governance_overrides_are_rejected(
    config_root: Path,
    key: str,
    value: object,
    expected: str,
) -> None:
    write_local_config(config_root, {key: value})

    assert any(
        error.startswith(f"LOCAL_CONFIG: {expected}") for error in validate(config_root)
    )


@pytest.mark.parametrize("role", sorted(ROLES))
def test_all_project_agents_deny_unknown_tools_by_default(
    config_root: Path, role: str
) -> None:
    replace(
        config_root,
        f".opencode/agent/{role}.md",
        'permission:\n  "*": deny\n',
        "permission:\n",
    )

    assert f"ROLE_PERMISSION: {role} tool wildcard must be declared first" in validate(
        config_root
    )


def test_implementer_bash_is_deny_by_default(config_root: Path) -> None:
    replace(
        config_root,
        ".opencode/agent/implementer.md",
        '  bash:\n    "*": deny\n',
        '  bash:\n    "*": ask\n',
    )

    assert "ROLE_PERMISSION: implementer bash catch-all must be deny" in validate(
        config_root
    )


def test_review_command_requires_static_runtime_and_delegated_verify(
    config_root: Path,
) -> None:
    command = (config_root / ".opencode/command/review-pr.md").read_text()

    assert "pdm run validate-opencode-config" in command
    assert "pdm run validate-opencode-runtime" in command
    assert "dispatch `implementer` to run exact `pdm run verify`" in command


def test_authoritative_verify_starts_with_static_opencode_validation() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    verify = pyproject["tool"]["pdm"]["scripts"]["verify"]["shell"]

    assert verify.startswith("pdm run validate-opencode-config &&")
    assert verify.count("pdm run validate-opencode-config") == 1
    assert "pdm run verify" not in verify


def test_agent_test_pdm_script_uses_repository_wrapper() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert pyproject["tool"]["pdm"]["scripts"]["agent-test"] == (
        "python scripts/validation/run_agent_test.py"
    )


def test_machine_local_json_and_jsonc_are_both_ignored_and_mutually_exclusive(
    config_root: Path,
) -> None:
    ignores = (config_root / ".gitignore").read_text().splitlines()
    assert "/.opencode/opencode.json" in ignores
    assert "/.opencode/opencode.jsonc" in ignores

    write_local_config(config_root, {"mcp": {}})
    (config_root / ".opencode/opencode.jsonc").write_text('{"lsp": {}}')

    assert "LOCAL_CONFIG: machine-local JSON and JSONC cannot coexist" in validate(
        config_root
    )


def test_implementer_has_no_broad_package_manager_wrapper_allows(
    config_root: Path,
) -> None:
    implementer = (config_root / ".opencode/agent/implementer.md").read_text()

    for forbidden in (
        '"pdm install": allow',
        '"pdm install *": allow',
        '"pdm build *": allow',
        '"pdm *": allow',
        '"pdm run *": allow',
        '"npm *": allow',
        '"npx *": allow',
        '"npm test *": allow',
        '"npx vitest *": allow',
    ):
        assert forbidden not in implementer
    for required in (
        '"pdm run verify": allow',
        '"pdm run agent-test *": allow',
        '"pdm run pre-commit run --all-files": allow',
        '"npm --prefix frontend run test:coverage": allow',
        '"npm --prefix frontend run test:unit -- --run": allow',
        '"*&*": deny',
    ):
        assert required in implementer


@pytest.mark.parametrize("role", sorted(ROLES))
def test_agent_git_read_commands_are_fixed_and_argument_free(
    config_root: Path, role: str
) -> None:
    metadata, _ = load_agent(
        config_root / f".opencode/agent/{role}.md",
        Validation(config_root),
    )
    bash = metadata["permission"]["bash"]

    for forbidden in (
        "git status*",
        "git diff*",
        "git log*",
        "git show*",
        "git rev-parse*",
    ):
        assert forbidden not in bash
    for required in (
        "git status --porcelain",
        "git status --short --branch",
        "git rev-parse HEAD",
    ):
        assert bash[required] == "allow"
    if role != "pr-test-analyzer":
        assert bash["git diff --no-ext-diff main...HEAD"] == "allow"


def test_orchestrator_validator_commands_accept_no_arguments(config_root: Path) -> None:
    metadata, _ = load_agent(
        config_root / ".opencode/agent/ontoprism-team.md",
        Validation(config_root),
    )
    bash = metadata["permission"]["bash"]

    assert bash["pdm run validate-opencode-config"] == "allow"
    assert bash["pdm run validate-opencode-runtime"] == "allow"
    assert "pdm run validate-opencode-config*" not in bash
    assert "pdm run validate-opencode-runtime*" not in bash


def test_orchestrator_task_delegation_is_exact_and_excludes_reserves(
    config_root: Path,
) -> None:
    metadata, _ = load_agent(
        config_root / ".opencode/agent/ontoprism-team.md",
        Validation(config_root),
    )
    permission = metadata["permission"]
    expected = {
        "implementer",
        "architect",
        "ontology-engineer",
        "oncology-evidence-analyst",
        "plan-adversary",
        "ontology-validator",
        "pr-code-reviewer",
        "pr-silent-failure-hunter",
        "pr-test-analyzer",
        "pr-comment-analyzer",
        "pr-type-design-analyzer",
    }

    task = permission["task"]
    assert list(task) == ["*", *sorted(expected)]
    assert task["*"] == "deny"
    assert all(task[name] == "allow" for name in expected)
    assert not expected & RESERVES


@pytest.mark.parametrize("role", sorted(ROLES))
def test_every_agent_denies_shell_metacharacter_chaining(
    config_root: Path, role: str
) -> None:
    agent = (config_root / f".opencode/agent/{role}.md").read_text()

    for pattern in ("*&*", "*;*", "*|*", "*>*", "*<*", "*`*", "*$*"):
        assert f'    "{pattern}": deny' in agent


@pytest.mark.parametrize("role", ["implementer", "pr-test-analyzer"])
def test_wildcard_bash_agents_end_with_literal_lf_and_cr_denies(
    config_root: Path, role: str
) -> None:
    metadata, _ = load_agent(
        config_root / f".opencode/agent/{role}.md",
        Validation(config_root),
    )
    bash = metadata["permission"]["bash"]

    assert list(bash)[-2:] == ["*\n*", "*\r*"]
    assert bash["*\n*"] == bash["*\r*"] == "deny"


def test_process_prose_assigns_remote_mutations_only_to_the_user(
    config_root: Path,
) -> None:
    agents = (config_root / "AGENTS.md").read_text()
    orchestrator = (config_root / ".opencode/agent/ontoprism-team.md").read_text()
    implementer = (config_root / ".opencode/agent/implementer.md").read_text()
    command = (config_root / ".opencode/command/review-pr.md").read_text()

    lasting_edits = (
        "Only the implementer makes lasting repository code, test, documentation, "
        "fix, or commit edits"
    )
    remote_actions = (
        "All pushes and PR creation, updates, and mutations are manual user actions"
    )
    assert lasting_edits in agents
    assert remote_actions in agents
    assert remote_actions in orchestrator
    assert "report the ready state to the user" in implementer
    assert "launches fresh CLI processes" in command
    assert "quit and restart opencode" in command.lower()
