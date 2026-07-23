"""Safety contracts for current-run-owned integration resources (#144)."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
import yaml
from test_support.integration_resources import (
    IntegrationResourceOwner,
    ResourceOwnershipError,
    find_unmanifested_mutators,
)


def _declared_markers_and_fixtures(source: str) -> tuple[set[str], set[str]]:
    tree = ast.parse(source)
    markers: set[str] = set()
    fixtures = {
        argument.arg
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
        ):
            markers.add(node.attr)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "usefixtures"
        ):
            fixtures.update(
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            )
    return markers, fixtures


@pytest.mark.unit
def test_owner_builds_collision_resistant_scoped_resource_names() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")

    assert owner.database_name == "ontoprism_test_019f8d64b0e274e2931a15452959797a"
    assert (
        owner.oxigraph_container_name
        == "ontoprism-oxigraph-test-019f8d64b0e274e2931a15452959797a"
    )
    assert owner.graph_iri("decomposition") == (
        "urn:ontoprism:test:019f8d64b0e274e2931a15452959797a:decomposition"
    )


@pytest.mark.unit
def test_database_ownership_requires_exact_name_and_nonce_marker() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")

    owner.verify_database(owner.database_name, owner.nonce)

    with pytest.raises(ResourceOwnershipError, match="database name"):
        owner.verify_database("ontoprism", owner.nonce)
    with pytest.raises(ResourceOwnershipError, match="database name"):
        owner.verify_database("ontoprism_test_some_other_run", owner.nonce)
    with pytest.raises(ResourceOwnershipError, match="owner marker"):
        owner.verify_database(owner.database_name, "another-run")


@pytest.mark.unit
def test_owner_rejects_malformed_nonce_and_graph_component() -> None:
    with pytest.raises(ValueError, match="nonce"):
        IntegrationResourceOwner(nonce="../../developer")

    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    with pytest.raises(ValueError, match="component"):
        owner.graph_iri("../shared")


@pytest.mark.unit
def test_owner_derives_admin_and_isolated_database_urls() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    configured = "postgresql+asyncpg://ontoprism:ontoprism@localhost:5433/ontoprism"

    assert owner.postgres_admin_url(configured) == (
        "postgresql+asyncpg://ontoprism:ontoprism@localhost:5433/postgres"
    )
    assert owner.database_url(configured) == (
        "postgresql+asyncpg://ontoprism:ontoprism@localhost:5433/"
        "ontoprism_test_019f8d64b0e274e2931a15452959797a"
    )


@pytest.mark.unit
def test_oxigraph_command_is_loopback_disposable_and_digest_pinned() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    data_dir = Path("/private/tmp/ontoprism-test-019f8d64b0e274e2931a15452959797a")

    assert owner.oxigraph_run_command(data_dir) == [
        "docker",
        "run",
        "--detach",
        "--name",
        "ontoprism-oxigraph-test-019f8d64b0e274e2931a15452959797a",
        "--label",
        "org.ontoprism.test-owner=019f8d64b0e274e2931a15452959797a",
        "--publish",
        "127.0.0.1::7878",
        "--volume",
        f"{data_dir}:/data",
        "ghcr.io/oxigraph/oxigraph@sha256:"
        "cc943499d4724fbb348c75c623335c69a047de71c59852413b0d0467d3caebe3",
        "serve",
        "--location",
        "/data",
        "--bind",
        "0.0.0.0:7878",
    ]


@pytest.mark.unit
def test_oxigraph_ownership_requires_label_mount_and_file_marker() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    data_dir = Path("/private/tmp/ontoprism-test-019f8d64b0e274e2931a15452959797a")

    owner.verify_oxigraph(
        label=owner.nonce,
        mounted_data_dir=data_dir,
        expected_data_dir=data_dir,
        file_marker=owner.nonce,
    )
    with pytest.raises(ResourceOwnershipError, match="label"):
        owner.verify_oxigraph(
            label="another-run",
            mounted_data_dir=data_dir,
            expected_data_dir=data_dir,
            file_marker=owner.nonce,
        )
    with pytest.raises(ResourceOwnershipError, match="mount"):
        owner.verify_oxigraph(
            label=owner.nonce,
            mounted_data_dir=Path("/private/tmp/familiar-prefix-decoy"),
            expected_data_dir=data_dir,
            file_marker=owner.nonce,
        )
    with pytest.raises(ResourceOwnershipError, match="file marker"):
        owner.verify_oxigraph(
            label=owner.nonce,
            mounted_data_dir=data_dir,
            expected_data_dir=data_dir,
            file_marker="another-run",
        )


@pytest.mark.unit
def test_mutating_integration_manifest_requires_owned_resource_fixtures() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest_path = root / "test_support/integration_mutators.toml"
    with manifest_path.open("rb") as stream:
        entries = tomllib.load(stream)["mutator"]

    assert entries
    for entry in entries:
        test_path = root / entry["path"]
        source = test_path.read_text()
        markers, fixtures = _declared_markers_and_fixtures(source)
        assert "mutating_integration" in markers, entry["path"]
        assert set(entry["fixtures"]) <= fixtures, (
            f"{entry['path']} does not request {entry['fixtures']}"
        )


@pytest.mark.unit
def test_mutation_scanner_rejects_an_unmanifested_integration_test(
    tmp_path: Path,
) -> None:
    test_root = tmp_path / "backend/tests"
    test_root.mkdir(parents=True)
    path = test_root / "test_new_integration.py"
    path.write_text(
        """
import pytest

@pytest.mark.integration
async def test_unowned_write(connection):
    await connection.execute("DELETE FROM developer_data")
""".lstrip()
    )

    assert find_unmanifested_mutators(tmp_path, manifested_paths=frozenset()) == {
        "backend/tests/test_new_integration.py": ("persistent SQL write",)
    }


@pytest.mark.unit
def test_every_detected_persistent_mutator_is_in_the_ownership_manifest() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest_path = root / "test_support/integration_mutators.toml"
    with manifest_path.open("rb") as stream:
        entries = tomllib.load(stream)["mutator"]
    manifested = frozenset(entry["path"] for entry in entries)

    assert find_unmanifested_mutators(root, manifested_paths=manifested) == {}


@pytest.mark.unit
def test_default_integration_command_excludes_explicit_full_store_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)

    scripts = project["tool"]["pdm"]["scripts"]
    markers = project["tool"]["pytest"]["ini_options"]["markers"]

    assert "not full_store" in scripts["test-integration"]
    assert "integration and full_store" in scripts["test-integration-full-store"]
    assert any(marker.startswith("full_store:") for marker in markers)


@pytest.mark.unit
def test_ci_integration_step_cannot_open_configured_serving_resources() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["integration-tests"]["steps"]
    test_step = next(
        step for step in steps if step.get("run") == "pdm run test-integration-ci"
    )

    assert test_step["env"] == {
        "DATABASE_URL": (
            "postgresql+asyncpg://ontoprism:ontoprism@localhost:5433/"
            "ontoprism_ci_must_not_be_opened"
        ),
        "NCIT_SPARQL_URL": "http://127.0.0.1:9",
        "UBERON_SPARQL_URL": "http://127.0.0.1:9",
    }
