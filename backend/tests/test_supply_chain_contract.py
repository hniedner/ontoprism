"""Repository-level contracts for the standalone service and tool supply chain."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.validation.coverage_hierarchy import REPORT_RUNTIME_PACKAGES

from ontolib.core.data_build_tools import (
    JENA_RIOT_ARTIFACT,
    POSTGRES_IMAGE,
    QLEVER_IMAGE,
    QLEVER_TOOL,
    ROBOT_ARTIFACT,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

_ROOT = Path(__file__).resolve().parents[2]
_DIGEST_PIN = re.compile(r"^[^:@/\s]+(?:/[^:@/\s]+)+@sha256:[0-9a-f]{64}$")
_PDM_VERSION = "2.28.0"
_SETUP_PDM_ACTION = "pdm-project/setup-pdm@544d7237314ee09c256785bd360f6b30add38b37"
_ACTIONS_CACHE_ACTION = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
_REVIEWED_UPDATED_ACTION_PINS = {
    ".github/workflows/ci.yml": {
        "docker/setup-buildx-action": (
            "37fe631027851001ddb9b187196cc803df7f5f0e",
            "v4.3.0",
        ),
    },
    ".github/workflows/scorecard.yml": {
        "github/codeql-action/upload-sarif": (
            "db488ddef3bf6cb639b32c2e9a7c0a7ea8271d28",
            "v4.37.8",
        ),
    },
}
_MINIMUM_BRACE_EXPANSION_VERSION = (5, 0, 9)
_BRACE_EXPANSION_ADVISORIES = (
    "GHSA-mh99-v99m-4gvg",
    "GHSA-rgw5-rvv9-x895",
)


def _nested_image_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in (
                [child]
                if key == "image" and isinstance(child, str)
                else _nested_image_values(child)
            )
        ]
    if isinstance(value, list):
        return [item for child in value for item in _nested_image_values(child)]
    return []


def _digest_pin_identity(image: str) -> str:
    tagged_name, separator, digest = image.partition("@")
    repository = tagged_name.rsplit(":", maxsplit=1)[0]
    if "/" not in repository:
        repository = f"docker.io/library/{repository}"
    return f"{repository}{separator}{digest}"


def test_compose_uses_only_digest_pinned_standalone_service_images() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    services = compose["services"]

    assert POSTGRES_IMAGE == (
        "pgvector/pgvector@sha256:"
        "a947c45cdc5906a1bc951f20a8709e321256343ee0f251e4ae00b5e7def4e6da"
    )
    assert set(services) == {"qlever-ncit", "qlever-uberon", "postgres"}
    assert services["qlever-ncit"]["image"] == QLEVER_IMAGE
    assert services["qlever-uberon"]["image"] == QLEVER_IMAGE
    assert services["postgres"]["image"] == POSTGRES_IMAGE
    assert all(_DIGEST_PIN.fullmatch(service["image"]) for service in services.values())

    ncit_command = " ".join(services["qlever-ncit"]["command"])
    uberon_command = " ".join(services["qlever-uberon"]["command"])
    for command, basename in ((ncit_command, "ncit"), (uberon_command, "uberon")):
        assert f"qlever-server -i {basename}" in command
        assert "-j 2" in command
        assert "-s 30s" in command
        assert "--service-allowed-iri-prefixes -" in command
    assert "--persist-updates" in ncit_command
    assert "--persist-updates" not in uberon_command
    assert services["qlever-ncit"]["ports"] == ["127.0.0.1:7888:7001"]
    assert services["qlever-uberon"]["ports"] == ["127.0.0.1:7889:7001"]
    assert services["qlever-ncit"]["volumes"] == ["./data/qlever-ncit:/data"]
    assert services["qlever-uberon"]["volumes"] == ["./data/qlever-uberon:/data"]
    for name in ("qlever-ncit", "qlever-uberon"):
        assert "ASK" in " ".join(services[name]["healthcheck"]["test"])


def test_full_application_images_are_exactly_digest_pinned() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.app.yml").read_text())
    services = compose["services"]
    assert set(services) == {"api", "web", "proxy"}
    assert all(
        _DIGEST_PIN.fullmatch(_digest_pin_identity(service["image"]))
        for service in services.values()
        if "image" in service
    )
    assert services["proxy"]["image"] == (
        "caddy:2-alpine@sha256:"
        "5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
    )

    expected_from = {
        "backend/Dockerfile": (
            "python:3.13-slim@sha256:"
            "ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a"
        ),
        "frontend/Dockerfile": (
            "node:24-slim@sha256:"
            "3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03"
        ),
    }
    for relative_path, expected_image in expected_from.items():
        from_images = [
            line.split()[1]
            for line in (_ROOT / relative_path).read_text().splitlines()
            if line.startswith("FROM ")
        ]
        assert from_images == [expected_image, expected_image]


@pytest.fixture
def application_image_contract_root(tmp_path: Path) -> Path:
    for relative_path in (
        "docker-compose.app.yml",
        "backend/Dockerfile",
        "frontend/Dockerfile",
    ):
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((_ROOT / relative_path).read_text())
    return tmp_path


def test_application_image_contract_rejects_an_unpinned_added_service(
    application_image_contract_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose_path = application_image_contract_root / "docker-compose.app.yml"
    compose = yaml.safe_load(compose_path.read_text())
    compose["services"]["cache"] = {"image": "redis:7"}
    compose_path.write_text(yaml.safe_dump(compose))
    monkeypatch.setitem(globals(), "_ROOT", application_image_contract_root)

    with pytest.raises(AssertionError):
        test_full_application_images_are_exactly_digest_pinned()


def test_application_image_contract_rejects_a_pinned_unexpected_service(
    application_image_contract_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose_path = application_image_contract_root / "docker-compose.app.yml"
    compose = yaml.safe_load(compose_path.read_text())
    compose["services"]["cache"] = {"image": "redis@sha256:" + "a" * 64}
    compose_path.write_text(yaml.safe_dump(compose))
    monkeypatch.setitem(globals(), "_ROOT", application_image_contract_root)

    with pytest.raises(AssertionError):
        test_full_application_images_are_exactly_digest_pinned()


def test_qlever_candidate_provenance_names_source_version_and_digest() -> None:
    assert QLEVER_TOOL.as_dict() == {
        "name": "qlever-index-server",
        "source": "docker.io/adfreiburg/qlever",
        "version": "65f84b4",
        "digest": (
            "sha256:abeb20ae245184cee2991a99c22a9bb0a62f6884bb1a03747bf7e56165cb0ca6"
        ),
    }


def test_qlever_build_converter_is_pinned() -> None:
    assert JENA_RIOT_ARTIFACT.identity.as_dict() == {
        "name": "apache-jena-riot",
        "source": (
            "https://archive.apache.org/dist/jena/binaries/apache-jena-6.1.0.tar.gz"
        ),
        "version": "6.1.0",
        "digest": (
            "sha256:653108a91fd9b309a89bc756258bae0bca01587cef475942d11852e3beba2ae3"
        ),
    }
    assert JENA_RIOT_ARTIFACT.filename == "apache-jena-6.1.0.tar.gz"


def test_workflow_images_and_robot_install_are_immutable() -> None:
    workflows = [_ROOT / ".github" / "workflows" / "ci.yml"]
    images = [
        image
        for workflow in workflows
        for image in _nested_image_values(yaml.safe_load(workflow.read_text()))
    ]
    assert all(_DIGEST_PIN.fullmatch(image) for image in images)

    ci = workflows[0].read_text()
    assert "scripts/install_robot.py" in ci
    assert "ONTOPRISM_ROBOT_DIR" in ci
    assert ROBOT_ARTIFACT.identity.digest.startswith("sha256:")
    assert "curl" not in "\n".join(
        line for line in ci.splitlines() if "robot" in line.lower()
    )


def _assert_workflow_action_pins(root: Path) -> None:
    action_line = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<value>.+?)\s*$")
    immutable_action = re.compile(
        r"^(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
        r"(?:/[A-Za-z0-9_.-]+)*)@(?P<sha>[0-9a-f]{40})$"
    )
    version_comment = re.compile(r"^v\d+\.\d+(?:\.\d+)?$")
    reviewed_pins_seen: set[tuple[str, str]] = set()

    workflow_paths = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    assert workflow_paths, "no workflow files found"
    for workflow_path in workflow_paths:
        relative_path = workflow_path.relative_to(root).as_posix()
        for line_number, line in enumerate(
            workflow_path.read_text().splitlines(), start=1
        ):
            match = action_line.match(line)
            if match is None:
                continue
            value = match.group("value")
            action_ref, separator, comment = value.partition("#")
            action_ref = action_ref.strip()
            if action_ref.startswith("./"):
                continue
            location = f"{relative_path}:{line_number}"
            pin = immutable_action.fullmatch(action_ref)
            assert pin is not None, (
                f"{location}: external action must use a 40-character lowercase "
                "commit SHA"
            )
            version = comment.strip()
            assert separator, (
                f"{location}: action pin must have an inline version comment"
            )
            assert version_comment.fullmatch(version), (
                f"{location}: action pin must have an inline version comment"
            )

            action = pin.group("action")
            expected = _REVIEWED_UPDATED_ACTION_PINS.get(relative_path, {}).get(action)
            if expected is None:
                continue
            actual = (pin.group("sha"), version)
            assert actual == expected, (
                f"{location}: {action} does not match reviewed pin {expected!r}"
            )
            reviewed_pins_seen.add((relative_path, action))

    expected_reviewed_pins = {
        (relative_path, action)
        for relative_path, actions in _REVIEWED_UPDATED_ACTION_PINS.items()
        for action in actions
    }
    assert reviewed_pins_seen == expected_reviewed_pins, (
        f"reviewed action pins missing from workflows: "
        f"{sorted(expected_reviewed_pins - reviewed_pins_seen)}"
    )


def test_workflow_actions_are_sha_pinned_with_bound_version_comments() -> None:
    _assert_workflow_action_pins(_ROOT)


@pytest.fixture
def workflow_action_contract_root(tmp_path: Path) -> Path:
    for relative_path in _REVIEWED_UPDATED_ACTION_PINS:
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((_ROOT / relative_path).read_text())
    return tmp_path


def _mutate_workflow(root: Path, relative_path: str, old: str, new: str) -> None:
    workflow = root / relative_path
    original = workflow.read_text()
    assert original.count(old) == 1
    workflow.write_text(original.replace(old, new))


def test_workflow_action_contract_rejects_changed_sha_with_same_comment(
    workflow_action_contract_root: Path,
) -> None:
    _mutate_workflow(
        workflow_action_contract_root,
        ".github/workflows/ci.yml",
        "docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e  # v4.3.0",
        "docker/setup-buildx-action@47fe631027851001ddb9b187196cc803df7f5f0e  # v4.3.0",
    )

    with pytest.raises(AssertionError, match="does not match reviewed pin"):
        _assert_workflow_action_pins(workflow_action_contract_root)


def test_workflow_action_contract_rejects_changed_comment_with_same_sha(
    workflow_action_contract_root: Path,
) -> None:
    _mutate_workflow(
        workflow_action_contract_root,
        ".github/workflows/scorecard.yml",
        (
            "github/codeql-action/upload-sarif@"
            "db488ddef3bf6cb639b32c2e9a7c0a7ea8271d28  # v4.37.8"
        ),
        (
            "github/codeql-action/upload-sarif@"
            "db488ddef3bf6cb639b32c2e9a7c0a7ea8271d28  # v4.37.9"
        ),
    )

    with pytest.raises(AssertionError, match="does not match reviewed pin"):
        _assert_workflow_action_pins(workflow_action_contract_root)


def test_workflow_action_contract_rejects_mutable_tag(
    workflow_action_contract_root: Path,
) -> None:
    _mutate_workflow(
        workflow_action_contract_root,
        ".github/workflows/ci.yml",
        "docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e  # v4.3.0",
        "docker/setup-buildx-action@v4.3.0  # v4.3.0",
    )

    with pytest.raises(AssertionError, match="40-character lowercase commit SHA"):
        _assert_workflow_action_pins(workflow_action_contract_root)


def _npm_script_bodies(
    command: str, scripts: dict[str, str], depth: int = 0
) -> list[str]:
    """Expand `npm run <script>` in *command* to the bodies it actually executes."""
    if depth > 3:  # guard against a self-referential script chain
        return []
    expanded: list[str] = []
    for name, body in scripts.items():
        if re.search(rf"npm (?:--prefix \S+ )?run {re.escape(name)}\b", command):
            expanded.append(body)
            expanded.extend(_npm_script_bodies(body, scripts, depth + 1))
    return expanded


def test_every_ci_job_installs_the_tools_its_steps_invoke() -> None:
    """A CI job that invokes `pdm` must also set PDM up — including via an npm script.

    `web tests + coverage` regressed exactly this way: `npm run test:coverage` grew a
    `pdm run coverage-gate` tail (the gate must use the pinned project interpreter)
    while the job installed only Node and Python, so CI failed with `pdm: not found`.
    No local run could reveal it, because pdm is on a developer's PATH. Only comparing
    the workflow against the scripts it invokes catches this class of drift.
    """
    workflow = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    scripts: dict[str, str] = json.loads(
        (_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )["scripts"]

    for job_name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        commands = [step["run"] for step in steps if step.get("run")]
        executed = [
            fragment
            for command in commands
            for fragment in (command, *_npm_script_bodies(command, scripts))
        ]
        invokes_pdm = any(
            re.search(r"(?<!\S)pdm(?=\s|$)", fragment) for fragment in executed
        )
        if not invokes_pdm:
            continue
        assert any(step.get("uses") == _SETUP_PDM_ACTION for step in steps), (
            f"CI job {job_name!r} invokes pdm but never installs it; "
            f"add {_SETUP_PDM_ACTION} to that job"
        )


def test_frontend_hierarchy_report_runs_inside_the_project_environment() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["web-tests"]["steps"]
        if step.get("name")
        == "Report native frontend coverage hierarchy (non-blocking deficits)"
    )

    assert step["working-directory"] == "${{ github.workspace }}"
    assert step["run"] == (
        "pdm run python -m scripts.validation.frontend_coverage_hierarchy"
    )


def _locked_versions() -> dict[str, str]:
    packages = tomllib.loads((_ROOT / "pdm.lock").read_text())["package"]
    versions: dict[str, str] = {}
    for package in packages:
        name = package["name"]
        version = package["version"]
        previous = versions.setdefault(name, version)
        assert previous == version, f"pdm.lock has conflicting versions for {name}"
    return versions


def _python_entrypoints(command: str) -> list[tuple[str, str]]:
    return [
        (module or "", script or "")
        for module, script in re.findall(
            r"(?m)(?<!\S)python\s+(?:-m\s+([\w.]+)|(scripts/[\w/]+\.py))",
            command,
        )
    ]


def _local_module_path(root: Path, module: str) -> Path | None:
    module_path = root / module.replace(".", "/")
    source = module_path.with_suffix(".py")
    if source.is_file():
        return source
    package = module_path / "__init__.py"
    return package if package.is_file() else None


def _import_roots(
    path: Path, root: Path = _ROOT, visited: set[Path] | None = None
) -> set[str]:
    visited = set() if visited is None else visited
    path = path.resolve()
    if path in visited:
        return set()
    visited.add(path)
    tree = ast.parse(path.read_text())
    roots = {
        node.module.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    local_roots = {
        import_root
        for import_root in roots
        if _local_module_path(root, import_root) is not None
        or (root / import_root).is_dir()
    }
    for node in ast.walk(tree):
        modules = (
            [node.module]
            if isinstance(node, ast.ImportFrom) and node.module is not None
            else [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else []
        )
        for module in modules:
            candidate = _local_module_path(root, module)
            if candidate is not None:
                roots.update(_import_roots(candidate, root, visited))
    return roots - local_roots - sys.stdlib_module_names


def _assert_report_runtime_packages(
    required_roots: set[str], installed: dict[str, str]
) -> None:
    assert required_roots == REPORT_RUNTIME_PACKAGES
    assert installed.keys() == REPORT_RUNTIME_PACKAGES


def test_python_coverage_job_derives_and_pins_its_python_runtime() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["coverage-verify"]["steps"]

    install_index, install = next(
        (index, step)
        for index, step in enumerate(steps)
        if step.get("name") == "Install coverage report dependencies"
    )
    install_specs = shlex.split(install["run"])[2:]
    installed = dict(spec.split("==", maxsplit=1) for spec in install_specs)

    invoked = [
        (index, entrypoint)
        for index, step in enumerate(steps)
        for entrypoint in _python_entrypoints(step.get("run", ""))
    ]
    required_roots: set[str] = set()
    for _, (module, script) in invoked:
        if module:
            module_path = _ROOT / f"{module.replace('.', '/')}.py"
            if module_path.is_file():
                required_roots.update(_import_roots(module_path))
            else:
                required_roots.add(module.partition(".")[0])
        if script:
            required_roots.update(_import_roots(_ROOT / script))

    _assert_report_runtime_packages(required_roots, installed)
    locked = _locked_versions()
    assert installed == {name: locked[name] for name in REPORT_RUNTIME_PACKAGES}

    verify_index, verify = next(
        (index, step)
        for index, step in enumerate(steps)
        if step.get("name") == "Verify coverage report dependency versions"
    )
    assert verify["run"] == (
        "python -m scripts.validation.verify_coverage_runtime --lock pdm.lock"
    )
    assert install_index < verify_index
    assert all(verify_index <= index for index, _ in invoked)

    workflow_text = (_ROOT / ".github/workflows/ci.yml").read_text()
    assert (
        "Coverage.py, Pydantic, git on PATH, and the real checked-out source are "
        "needed to verify layer identity against the checkout, gate, and render; "
        "no editable install." in workflow_text.replace("\n      # ", " ")
    )
    assert "no editable ontolib/backend install" in workflow_text


def test_python_runtime_derivation_detects_new_dependency_through_plain_local_import(
    tmp_path: Path,
) -> None:
    entrypoint = tmp_path / "scripts" / "validation" / "entrypoint.py"
    imported = tmp_path / "scripts" / "validation" / "imported.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("import scripts.validation.imported\n")
    imported.write_text(
        "import coverage\nimport newly_added_report_dependency\nimport pydantic\n"
    )

    derived = _import_roots(entrypoint, tmp_path)

    assert derived == REPORT_RUNTIME_PACKAGES | {"newly_added_report_dependency"}
    with pytest.raises(AssertionError):
        _assert_report_runtime_packages(
            derived,
            {"coverage": "7.15.4", "pydantic": "2.13.4"},
        )


def test_coverage_uploads_fail_when_required_evidence_is_missing() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]

    for job_name, artifact_name in (
        ("backend-tests", "coverage-data"),
        ("integration-tests", "coverage-integration-data"),
    ):
        upload = next(
            step
            for step in jobs[job_name]["steps"]
            if step.get("with", {}).get("name") == artifact_name
        )
        assert upload["with"]["if-no-files-found"] == "error"


def test_frontend_hierarchy_runner_changes_trigger_frontend_ci() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])

    assert "scripts/validation/frontend_coverage_hierarchy.py" in filters["frontend"]


def test_frontend_transitive_security_and_install_script_policy() -> None:
    """Patched transitive tools stay pinned and optional native scripts stay denied."""
    package = json.loads(
        (_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )

    assert package["allowScripts"] == {"fsevents": False}
    assert package["overrides"]["brace-expansion"] == "^5.0.9"
    assert package["overrides"]["nanoid"] == "^3.3.18"
    assert lock["packages"]["node_modules/brace-expansion"]["version"] == "5.0.9"
    assert lock["packages"]["node_modules/nanoid"]["version"] == "3.3.18"


def test_frontend_vitest_manifest_matches_coverage_peer_and_lock() -> None:
    package = json.loads(
        (_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )
    root_lock = lock["packages"][""]["devDependencies"]
    coverage_lock = lock["packages"]["node_modules/@vitest/coverage-v8"]

    assert package["devDependencies"]["vitest"] == "^4.1.11"
    assert package["devDependencies"]["@vitest/coverage-v8"] == "^4.1.11"
    assert root_lock == package["devDependencies"]
    assert lock["packages"]["node_modules/vitest"]["version"] == "4.1.11"
    assert coverage_lock["version"] == "4.1.11"
    assert coverage_lock["peerDependencies"]["vitest"] == "4.1.11"


def test_ci_dependency_environments_are_pinned_clean_and_cached(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    jobs = workflow["jobs"]
    expected_sync = {
        "quality": "pdm sync --clean-unselected --dev",
        "backend-tests": "pdm sync --clean-unselected --dev",
        "integration-tests": "pdm sync --clean-unselected --dev",
        "web-tests": "pdm sync --clean-unselected --dev",
        "embedding-model-contract": ("pdm sync --clean-unselected --dev -G data-build"),
    }

    for job_name, sync_command in expected_sync.items():
        steps = jobs[job_name]["steps"]
        setup = next(step for step in steps if step.get("uses") == _SETUP_PDM_ACTION)
        assert setup["with"] == {
            "python-version": "3.13",
            "version": _PDM_VERSION,
            "cache": True,
        }
        assert any(step.get("run") == sync_command for step in steps)

    for sync_command in set(expected_sync.values()):
        args = shlex.split(sync_command)
        # argv comes only from the fixed expected commands above, never workflow data.
        completed = subprocess.run(  # noqa: S603
            [*args[:2], "--dry-run", *args[2:]],
            cwd=_ROOT,
            capture_output=True,
            check=False,
            env={**os.environ, "PDM_LOG_DIR": str(tmp_path / "pdm-logs")},
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    quality_steps = jobs["quality"]["steps"]
    pre_commit_cache = next(
        step for step in quality_steps if step.get("uses") == _ACTIONS_CACHE_ACTION
    )
    assert pre_commit_cache["with"] == {
        "path": "~/.cache/pre-commit",
        "key": (
            "${{ runner.os }}-${{ runner.arch }}-pre-commit-"
            "${{ steps.setup-pdm.outputs.python-version }}-"
            "${{ hashFiles('.pre-commit-config.yaml') }}"
        ),
    }

    dockerfile = (_ROOT / "backend" / "Dockerfile").read_text()
    assert f"RUN pip install --no-cache-dir pdm=={_PDM_VERSION}" in dockerfile


def test_frontend_brace_expansion_is_pinned_above_vulnerable_versions() -> None:
    assert _MINIMUM_BRACE_EXPANSION_VERSION == (5, 0, 9), (
        "brace-expansion security floor must remain at the patched boundary for "
        + ", ".join(_BRACE_EXPANSION_ADVISORIES)
    )
    package = json.loads((_ROOT / "frontend" / "package.json").read_text())
    lock = json.loads((_ROOT / "frontend" / "package-lock.json").read_text())

    locked_versions = [
        details["version"]
        for path, details in lock["packages"].items()
        if path.endswith("node_modules/brace-expansion")
    ]
    assert locked_versions, "brace-expansion is absent from the frontend lockfile"
    assert all(
        tuple(map(int, version.split("."))) >= _MINIMUM_BRACE_EXPANSION_VERSION
        for version in locked_versions
    ), f"vulnerable brace-expansion versions are locked: {locked_versions}"

    override = package["overrides"]["brace-expansion"]
    override_match = re.fullmatch(r"[~^>=]*(\d+)\.(\d+)\.(\d+)", override)
    assert override_match is not None, (
        f"unsupported brace-expansion override: {override}"
    )
    assert tuple(map(int, override_match.groups())) >= _MINIMUM_BRACE_EXPANSION_VERSION


def test_clean_machine_instructions_do_not_require_a_sibling_checkout() -> None:
    data_setup = (_ROOT / "docs" / "DATA_SETUP.md").read_text()

    assert "../fairdata" not in data_setup
    assert "scripts/install_robot.py" in data_setup
    assert "scripts/install_jena.py" in data_setup
    assert "docker compose up -d" in data_setup


def test_full_app_routes_icdo_entitlement_through_the_private_bff() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.app.yml").read_text())
    services = compose["services"]

    assert services["api"]["environment"]["ICDO_ENTITLEMENT_KEY"] == (
        "${ICDO_ENTITLEMENT_KEY:-}"
    )
    assert services["api"]["environment"]["ENABLE_LICENSED_MAPPINGS"] == (
        "${ENABLE_LICENSED_MAPPINGS:-false}"
    )
    assert services["web"]["environment"]["ICDO_ENTITLEMENT_KEY"] == (
        "${ICDO_ENTITLEMENT_KEY:-}"
    )
    assert services["web"]["environment"]["ONTOPRISM_FASTAPI_ORIGIN"] == (
        "http://api:8011"
    )
    assert services["web"]["build"] == {
        "context": ".",
        "dockerfile": "frontend/Dockerfile",
    }

    caddy = (_ROOT / "Caddyfile").read_text()
    assert "handle /api/*" not in caddy
    assert "reverse_proxy web:3000" in caddy

    env_example = (_ROOT / ".env.example").read_text()
    assert "# ICDO_ENTITLEMENT_KEY=" in env_example
    assert "ENABLE_LICENSED_MAPPINGS=false" in env_example


def test_active_runtime_has_no_oxigraph_dependency() -> None:
    active_paths = (
        "docker-compose.yml",
        "docker-compose.app.yml",
        "pyproject.toml",
        ".env.example",
        "backend/src",
        "ontolib/src",
        "scripts",
        "test_support",
    )
    occurrences: list[str] = []
    for relative in active_paths:
        path = _ROOT / relative
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        for file_path in files:
            if not file_path.is_file() or "__pycache__" in file_path.parts:
                continue
            try:
                text = file_path.read_text()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if "oxigraph" in line.lower():
                    occurrences.append(
                        f"{file_path.relative_to(_ROOT)}:{line_number}:{line.strip()}"
                    )
    assert occurrences == []
