"""Safety contracts for current-run-owned integration resources (#144)."""

from __future__ import annotations

import ast
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from scripts.test_runner import suites
from test_support.integration_resources import (
    DockerRun,
    IntegrationConnectionPolicy,
    IntegrationResourceOwner,
    IntegrationTestDeclaration,
    MutatorManifestEntry,
    ResourceOwnershipError,
    build_safe_integration_environment,
    find_persistent_mutator_tests,
    find_persistent_mutators,
    find_unmanifested_mutators,
    remove_owned_container_by_name,
    validate_integration_test_declaration,
    validate_mutator_manifest_entries,
    validate_mutator_manifest_files,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    _DockerRunner = DockerRun
    _ContainerInspector = Callable[
        [IntegrationResourceOwner, str, _DockerRunner], dict[str, object]
    ]
    _OxigraphOwnerVerifier = Callable[
        [IntegrationResourceOwner, str, Path, _DockerRunner], None
    ]


# xdist collection is a synchronized barrier: every worker collects independently
# and the controller verifies all collections match *before* dispatching any test
# to run, so a file written during test *execution* can never affect any worker's
# collection phase (confirmed against pytest-xdist's documented behavior and every
# publicly reported "Different tests were collected" cause, all of which trace to
# non-deterministic collection-time state, never a same-run filesystem write). The
# real race is at *execution* time: this probe test and the two scanner tests below
# each independently re-scan the live `backend/tests/` tree while other tests run
# concurrently on other workers, so a transient probe write can be observed by a
# concurrently-executing scanner as an unmanifested mutator. A cross-process file
# lock — every worker is a separate OS process — is the correct fix for that.
_TREE_SCAN_LOCK = Path(tempfile.gettempdir()) / "ontoprism-tree-scan.lock"


@contextmanager
def _exclusive_tree_scan() -> Iterator[None]:
    """Serialize real-tree scans against the collection-hook probe test."""
    with _TREE_SCAN_LOCK.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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
    assert owner.database_role == "ontoprism_test_019f8d64b0e274e2931a15452959797a"
    assert owner.database_role_comment == (
        "ontoprism-test-owner:019f8d64b0e274e2931a15452959797a"
    )
    assert owner.postgres_container_name == (
        "ontoprism-postgres-test-019f8d64b0e274e2931a15452959797a"
    )
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

    owner.verify_database(database_name=owner.database_name, marker=owner.nonce)

    with pytest.raises(ResourceOwnershipError, match="database name"):
        owner.verify_database(database_name="ontoprism", marker=owner.nonce)
    with pytest.raises(ResourceOwnershipError, match="database name"):
        owner.verify_database(
            database_name="ontoprism_test_some_other_run", marker=owner.nonce
        )
    with pytest.raises(ResourceOwnershipError, match="owner marker"):
        owner.verify_database(database_name=owner.database_name, marker="another-run")
    owner.verify_database_role(owner.database_role, owner.database_role_comment)
    with pytest.raises(ResourceOwnershipError, match="database owner role"):
        owner.verify_database_role("ontoprism", owner.database_role_comment)
    with pytest.raises(ResourceOwnershipError, match="role marker"):
        owner.verify_database_role(owner.database_role, "another-run")


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
    assert owner.postgres_admin_database_url(configured) == (
        "postgresql+asyncpg://ontoprism:ontoprism@localhost:5433/"
        "ontoprism_test_019f8d64b0e274e2931a15452959797a"
    )
    assert owner.database_url(configured) == (
        "postgresql+asyncpg://ontoprism_test_019f8d64b0e274e2931a15452959797a:"
        f"{owner.secret}@localhost:5433/"
        "ontoprism_test_019f8d64b0e274e2931a15452959797a"
    )


@pytest.mark.unit
def test_secret_is_independent_random_material_not_derived_from_the_nonce() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    same_nonce = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")

    assert owner.secret != owner.nonce
    assert owner.nonce not in owner.secret
    # Two owners sharing a nonce must NOT share a secret: unlike a deterministic
    # hash of nonce, recovering one owner's nonce (via an inspectable channel)
    # must never let anyone derive another (or the same) owner's credential.
    assert owner.secret != same_nonce.secret
    # secret is excluded from equality/repr: identity is nonce-based only.
    assert owner == same_nonce
    assert "secret" not in repr(owner)
    # secret is `init=False`: like `nonce` it is interpolated verbatim into raw,
    # non-parameterized SQL, but unlike `nonce` it has no format validation, so it
    # must never be externally settable. Constructing with a caller-supplied secret
    # must fail.
    with pytest.raises(TypeError):
        IntegrationResourceOwner(
            nonce="019f8d64b0e274e2931a15452959797a",
            secret="attacker-controlled",  # type: ignore[call-arg]  # noqa: S106
        )


@pytest.mark.unit
def test_safe_lane_separates_provisioning_credentials_from_application_targets(
    tmp_path: Path,
) -> None:
    configured = "postgresql+asyncpg://admin:secret@localhost:5433/ontoprism"
    data_root = tmp_path / "owned-data"

    environment = build_safe_integration_environment(
        {
            "DATABASE_URL": configured,
            "NCIT_SPARQL_URL": "http://localhost:7888",
            "ONTOPRISM_TEST_POSTGRES_ADMIN_URL": configured,
        },
        data_root=data_root,
    )

    assert "ONTOPRISM_TEST_POSTGRES_ADMIN_URL" not in environment
    assert environment["DATABASE_URL"] == (
        "postgresql+asyncpg://ontoprism_test_forbidden:forbidden@127.0.0.1:9/"
        "ontoprism_test_forbidden"
    )
    assert environment["NCIT_SPARQL_URL"] == "http://127.0.0.1:9"
    assert environment["UBERON_SPARQL_URL"] == "http://127.0.0.1:9"
    assert environment["CADSR_DB_PATH"] == str(data_root / "cadsr/cde_repository.db")
    assert environment["CADSR_DATA_DIR"] == str(data_root / "cadsr")
    assert environment["NCIT_OWL_DIR"] == str(data_root / "ncit-owl")
    assert environment["RELOAD_ALLOWED_DIR"] == str(data_root)
    assert environment["ONTOPRISM_SAFE_INTEGRATION"] == "1"


@pytest.mark.unit
def test_connection_policy_rejects_every_unregistered_tcp_target() -> None:
    policy = IntegrationConnectionPolicy()

    with pytest.raises(ResourceOwnershipError, match="not owned"):
        policy.verify_socket_address(("127.0.0.1", 7888))
    with pytest.raises(ResourceOwnershipError, match="not owned"):
        policy.verify_socket_address(("203.0.113.10", 443))
    with pytest.raises(ResourceOwnershipError, match="not owned"):
        policy.verify_socket_address("/var/run/postgresql/.s.PGSQL.5432")
    with pytest.raises(ResourceOwnershipError, match="malformed"):
        policy.verify_socket_address(("127.0.0.1",))

    with policy.registered("http://127.0.0.1:49152"):
        policy.verify_socket_address(("127.0.0.1", 49152))
    with pytest.raises(ResourceOwnershipError, match="not owned"):
        policy.verify_socket_address(("127.0.0.1", 49152))

    # The allow-list is `init=False`: `registered()` is the only way to widen it,
    # so a caller cannot seed a pre-approved target past the loopback checks by
    # constructing the policy with an initial `_allowed` set.
    with pytest.raises(TypeError):
        IntegrationConnectionPolicy(
            _allowed={("203.0.113.10", 443)},  # type: ignore[call-arg]
        )


def _verify_then_raise_while_registered(
    policy: IntegrationConnectionPolicy, url: str, address: tuple[str, int]
) -> None:
    with policy.registered(url):
        policy.verify_socket_address(address)  # succeeds while registered
        raise RuntimeError("boom")


@pytest.mark.unit
def test_registered_unregisters_even_when_the_context_body_raises() -> None:
    policy = IntegrationConnectionPolicy()
    url = "http://127.0.0.1:49200"

    with pytest.raises(RuntimeError, match="boom"):
        _verify_then_raise_while_registered(policy, url, ("127.0.0.1", 49200))

    with pytest.raises(ResourceOwnershipError, match="not owned"):
        policy.verify_socket_address(("127.0.0.1", 49200))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("declaration", "rules", "detected", "message"),
    [
        (
            IntegrationTestDeclaration(
                path="backend/tests/test_mixed.py",
                name="test_write",
                markers=frozenset(
                    {"integration", "full_store", "mutating_integration"}
                ),
                fixtures=frozenset({"isolated_postgres_settings"}),
            ),
            (),
            (),
            "both full_store and mutating_integration",
        ),
        (
            IntegrationTestDeclaration(
                path="backend/tests/test_mixed.py",
                name="test_write",
                markers=frozenset({"integration", "mutating_integration"}),
                fixtures=frozenset(),
            ),
            (),
            ("persistent SQL write",),
            "missing from integration_mutators.toml",
        ),
        (
            IntegrationTestDeclaration(
                path="backend/tests/test_mixed.py",
                name="test_read",
                markers=frozenset({"integration"}),
                fixtures=frozenset(),
            ),
            (),
            ("persistent SQL write",),
            "write signals but lacks mutating_integration",
        ),
        (
            IntegrationTestDeclaration(
                path="backend/tests/test_mixed.py",
                name="test_write",
                markers=frozenset({"integration", "mutating_integration"}),
                fixtures=frozenset({"isolated_oxigraph_settings"}),
            ),
            (
                MutatorManifestEntry(
                    path="backend/tests/test_mixed.py",
                    fixtures=frozenset({"isolated_postgres_settings"}),
                    tests=frozenset({"test_write"}),
                ),
            ),
            ("persistent SQL write",),
            "missing owned fixtures",
        ),
        (
            IntegrationTestDeclaration(
                path="backend/tests/test_mixed.py",
                name="test_write",
                markers=frozenset({"integration", "mutating_integration"}),
                fixtures=frozenset({"unrelated_fixture"}),
            ),
            (
                MutatorManifestEntry(
                    path="backend/tests/test_mixed.py",
                    fixtures=frozenset({"unrelated_fixture"}),
                ),
            ),
            ("persistent SQL write",),
            "Postgres write without an owned database",
        ),
        (
            IntegrationTestDeclaration(
                path="backend/tests/test_mixed.py",
                name="test_write",
                markers=frozenset({"integration", "mutating_integration"}),
                fixtures=frozenset({"unrelated_fixture"}),
            ),
            (
                MutatorManifestEntry(
                    path="backend/tests/test_mixed.py",
                    fixtures=frozenset({"unrelated_fixture"}),
                ),
            ),
            ("Oxigraph write",),
            "Oxigraph write without an owned store",
        ),
        (
            IntegrationTestDeclaration(
                path="backend/tests/test_mixed.py",
                name="test_write",
                markers=frozenset({"integration", "mutating_integration"}),
                fixtures=frozenset({"isolated_postgres_settings"}),
            ),
            (
                MutatorManifestEntry(
                    path="backend/tests/test_mixed.py",
                    fixtures=frozenset({"isolated_postgres_settings"}),
                ),
            ),
            ("persistent API write",),
            "persistent API write without both owned services",
        ),
        (
            IntegrationTestDeclaration(
                path="backend/tests/test_mixed.py",
                name="test_write",
                markers=frozenset({"integration", "mutating_integration"}),
                fixtures=frozenset({"isolated_postgres_settings"}),
            ),
            (
                MutatorManifestEntry(
                    path="backend/tests/test_mixed.py",
                    fixtures=frozenset({"isolated_postgres_settings"}),
                ),
                MutatorManifestEntry(
                    path="backend/tests/test_mixed.py",
                    fixtures=frozenset({"isolated_postgres_settings"}),
                    tests=frozenset({"test_write"}),
                ),
            ),
            (),
            "matches multiple",
        ),
    ],
)
def test_collected_integration_declarations_fail_closed(
    declaration: IntegrationTestDeclaration,
    rules: tuple[MutatorManifestEntry, ...],
    detected: tuple[str, ...],
    message: str,
) -> None:
    errors = validate_integration_test_declaration(
        declaration,
        manifest=rules,
        detected_reasons=detected,
    )

    assert any(message in error for error in errors)


@pytest.mark.unit
def test_mutator_manifest_rejects_stale_paths_and_test_selectors() -> None:
    declarations = (
        IntegrationTestDeclaration(
            path="backend/tests/test_current.py",
            name="test_write",
            markers=frozenset({"integration", "mutating_integration"}),
            fixtures=frozenset({"isolated_postgres_settings"}),
        ),
    )
    manifest = (
        MutatorManifestEntry(
            path="backend/tests/test_deleted.py",
            fixtures=frozenset({"isolated_postgres_settings"}),
        ),
        MutatorManifestEntry(
            path="backend/tests/test_current.py",
            fixtures=frozenset({"isolated_postgres_settings"}),
            tests=frozenset({"test_renamed"}),
        ),
    )

    errors = validate_mutator_manifest_entries(
        manifest=manifest,
        declarations=declarations,
    )

    assert any("test_deleted.py" in error for error in errors)
    assert any("test_renamed" in error for error in errors)


@pytest.mark.unit
def test_manifest_files_reject_missing_paths_and_uncollected_selectors(
    tmp_path: Path,
) -> None:
    """`validate_mutator_manifest_files` AST-scans the tree, not pytest collection.

    Drives the reject branch of the function this commit rewrote to build
    `_ParsedTestName`: a manifest path with no source file, and a real file whose
    `tests=` selector names a function that does not exist, must both be reported.
    """
    real = tmp_path / "backend/tests/test_real.py"
    real.parent.mkdir(parents=True)
    real.write_text("def test_present() -> None:\n    pass\n")

    manifest = (
        MutatorManifestEntry(
            path="backend/tests/test_absent.py",
            fixtures=frozenset({"isolated_postgres_settings"}),
        ),
        MutatorManifestEntry(
            path="backend/tests/test_real.py",
            fixtures=frozenset({"isolated_postgres_settings"}),
            tests=frozenset({"test_present", "test_missing"}),
        ),
    )

    errors = validate_mutator_manifest_files(tmp_path, manifest=manifest)

    assert any(
        "test_absent.py" in error and "does not exist" in error for error in errors
    )
    assert any("test_missing" in error for error in errors)
    # A selector that resolves to a real function is not flagged, while the bogus
    # sibling selector in the same entry still is — proving `entry.tests - names`
    # reports only unresolved selectors.
    assert not any("test_present" in error for error in errors)


@pytest.mark.unit
def test_oxigraph_command_is_loopback_disposable_and_digest_pinned() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    data_dir = Path(
        "/private/tmp/ontoprism-oxigraph-019f8d64b0e274e2931a15452959797a-fixture"
    )

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
    with pytest.raises(ResourceOwnershipError, match="data directory"):
        owner.oxigraph_run_command(
            Path("/private/tmp/prefix-019f8d64b0e274e2931a15452959797a-suffix")
        )

    assert owner.postgres_run_command() == [
        "docker",
        "run",
        "--detach",
        "--name",
        "ontoprism-postgres-test-019f8d64b0e274e2931a15452959797a",
        "--label",
        "org.ontoprism.test-owner=019f8d64b0e274e2931a15452959797a",
        "--publish",
        "127.0.0.1::5432",
        "--tmpfs",
        "/var/lib/postgresql/data:rw",
        "--env",
        "POSTGRES_USER=ontoprism_admin",
        "--env",
        f"POSTGRES_PASSWORD={owner.secret}",
        "--env",
        "POSTGRES_DB=postgres",
        "pgvector/pgvector@sha256:"
        "7f5681e45237acdf546cf7cdc0dfc0ed7752ede857fda6e54f6ea21b936f8742",
    ]


@pytest.mark.unit
def test_oxigraph_ownership_requires_label_mount_and_file_marker() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    data_dir = Path(
        "/private/tmp/ontoprism-oxigraph-019f8d64b0e274e2931a15452959797a-fixture"
    )

    owner.verify_container_label(owner.nonce)
    with pytest.raises(ResourceOwnershipError, match="label"):
        owner.verify_container_label("another-run")
    owner.verify_oxigraph(
        mounted_data_dir=data_dir,
        expected_data_dir=data_dir,
        file_marker=owner.nonce,
    )
    with pytest.raises(ResourceOwnershipError, match="mount"):
        owner.verify_oxigraph(
            mounted_data_dir=Path("/private/tmp/familiar-prefix-decoy"),
            expected_data_dir=data_dir,
            file_marker=owner.nonce,
        )
    with pytest.raises(ResourceOwnershipError, match="file marker"):
        owner.verify_oxigraph(
            mounted_data_dir=data_dir,
            expected_data_dir=data_dir,
            file_marker="another-run",
        )
    owner.verify_oxigraph_data_dir(data_dir, owner.nonce)
    with pytest.raises(ResourceOwnershipError, match="directory owner"):
        owner.verify_oxigraph_data_dir(
            Path("/private/tmp/ontoprism-test-another-run"), owner.nonce
        )
    with pytest.raises(ResourceOwnershipError, match="directory owner"):
        owner.verify_oxigraph_data_dir(
            Path("/private/tmp/prefix-019f8d64b0e274e2931a15452959797a-suffix"),
            owner.nonce,
        )
    with pytest.raises(ResourceOwnershipError, match="file marker"):
        owner.verify_oxigraph_data_dir(data_dir, "another-run")


@pytest.mark.unit
def test_cleanup_does_not_treat_a_docker_daemon_error_as_absence() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")

    def failed_inspect(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del args, check
        return subprocess.CompletedProcess(
            args=["docker", "inspect"],
            returncode=1,
            stdout="",
            stderr="Cannot connect to the Docker daemon",
        )

    with pytest.raises(RuntimeError, match="Docker inspect failed"):
        remove_owned_container_by_name(
            owner, owner.oxigraph_container_name, docker_run=failed_inspect
        )


@pytest.mark.unit
def test_cleanup_treats_a_genuinely_absent_container_as_a_clean_no_op() -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    calls: list[tuple[str, ...]] = []

    def absent_inspect(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        calls.append(args)
        # The real Docker CLI shape for a nonexistent object: lowercase, "error: "
        # prefixed — not the capitalized "No such object" the old code assumed.
        return subprocess.CompletedProcess(
            args=["docker", *args],
            returncode=1,
            stdout="",
            stderr=f"error: no such object: {args[1]}",
        )

    remove_owned_container_by_name(
        owner, owner.oxigraph_container_name, docker_run=absent_inspect
    )

    assert calls == [("inspect", owner.oxigraph_container_name)]


@pytest.mark.unit
def test_inspect_owned_container_rejects_an_id_mismatch(
    owned_container_inspector: _ContainerInspector,
) -> None:
    """Defense-in-depth: unreachable via a real single-full-ID `docker inspect`
    call, but must fail closed if Docker ever returned a mismatched identity."""
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    requested_id = "0" * 64
    returned_id = "1" * 64

    def mismatched_id(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": returned_id,
                        "Config": {"Labels": {"org.ontoprism.test-owner": owner.nonce}},
                        "Mounts": [],
                    }
                ]
            ),
            stderr="",
        )

    with pytest.raises(ResourceOwnershipError, match="container ID changed"):
        owned_container_inspector(owner, requested_id, mismatched_id)


@pytest.mark.unit
def test_inspect_owned_container_rejects_a_malformed_config(
    owned_container_inspector: _ContainerInspector,
) -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    container_id = "0" * 64

    def malformed_config(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=0,
            stdout=json.dumps([{"Id": container_id, "Config": None, "Mounts": []}]),
            stderr="",
        )

    with pytest.raises(ResourceOwnershipError, match="configuration is malformed"):
        owned_container_inspector(owner, container_id, malformed_config)


@pytest.mark.unit
def test_verify_oxigraph_owner_rejects_malformed_mounts(
    oxigraph_owner_verifier: _OxigraphOwnerVerifier,
    tmp_path: Path,
) -> None:
    owner = IntegrationResourceOwner(nonce="019f8d64b0e274e2931a15452959797a")
    container_id = "0" * 64

    def malformed_mounts(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": container_id,
                        "Config": {"Labels": {"org.ontoprism.test-owner": owner.nonce}},
                        "Mounts": "not-a-list",
                    }
                ]
            ),
            stderr="",
        )

    with pytest.raises(ResourceOwnershipError, match="mounts are malformed"):
        oxigraph_owner_verifier(owner, container_id, tmp_path, malformed_mounts)


@pytest.mark.unit
def test_mutating_integration_manifest_requires_owned_resource_fixtures() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest_path = root / "test_support/integration_mutators.toml"
    with manifest_path.open("rb") as stream:
        entries = tomllib.load(stream)["mutator"]
    with _exclusive_tree_scan():
        detected = find_persistent_mutators(root)

    assert entries
    for entry in entries:
        test_path = root / entry["path"]
        source = test_path.read_text()
        markers, fixtures = _declared_markers_and_fixtures(source)
        assert "mutating_integration" in markers, entry["path"]
        assert set(entry["fixtures"]) <= fixtures, (
            f"{entry['path']} does not request {entry['fixtures']}"
        )
        reasons = set(detected.get(entry["path"], ()))
        if reasons & {"Oxigraph write", "HTTP write"}:
            assert "isolated_oxigraph_settings" in fixtures or (
                "isolated_oxigraph_url" in fixtures
            ), f"{entry['path']} has an Oxigraph write without an owned store"
        if reasons & {
            "persistent SQL write",
            "repository write",
            "schema migration",
        }:
            assert "isolated_postgres_settings" in fixtures or (
                "isolated_postgres_url" in fixtures
            ), f"{entry['path']} has a Postgres write without an owned database"
        if "persistent API write" in reasons:
            assert {
                "isolated_postgres_settings",
                "isolated_oxigraph_settings",
            } <= fixtures, (
                f"{entry['path']} has a persistent API write without both services"
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
def test_mutation_scanner_reports_every_resource_kind_used_by_a_module(
    tmp_path: Path,
) -> None:
    test_root = tmp_path / "backend/tests"
    test_root.mkdir(parents=True)
    path = test_root / "test_mixed_integration.py"
    path.write_text(
        """
import pytest

pytestmark = pytest.mark.integration

async def test_unowned_writes(connection, client):
    await connection.execute("INSERT INTO developer_data VALUES (1)")
    await client.load(b"<urn:s> <urn:p> <urn:o> .")
""".lstrip()
    )

    assert find_persistent_mutators(tmp_path) == {
        "backend/tests/test_mixed_integration.py": (
            "Oxigraph write",
            "persistent SQL write",
        )
    }


@pytest.mark.unit
def test_mutation_scanner_keeps_mixed_module_test_scopes_separate(
    tmp_path: Path,
) -> None:
    test_root = tmp_path / "backend/tests"
    test_root.mkdir(parents=True)
    path = test_root / "test_mixed_contracts.py"
    path.write_text(
        """
import pytest
from alembic import command

@pytest.mark.integration
@pytest.mark.full_store
async def test_configured_read(connection):
    await connection.execute("SELECT 1")

@pytest.mark.integration
@pytest.mark.mutating_integration
@pytest.mark.usefixtures("isolated_postgres_settings")
def test_owned_migration():
    command.downgrade("base")
""".lstrip()
    )

    assert find_persistent_mutator_tests(tmp_path) == {
        "backend/tests/test_mixed_contracts.py": {
            "test_owned_migration": ("schema migration",)
        }
    }


@pytest.mark.unit
def test_mutation_scanner_ignores_hermetic_setup_sql_next_to_an_integration_test(
    tmp_path: Path,
) -> None:
    """A hermetic unit test's own temp-SQLite DDL string must not be mistaken for
    an integration write signal just because an unrelated integration test lives
    in the same module."""
    test_root = tmp_path / "backend/tests"
    test_root.mkdir(parents=True)
    path = test_root / "test_mixed_hermetic_and_integration.py"
    path.write_text(
        """
import sqlite3

import pytest


def test_hermetic_unit_builds_a_temp_sqlite_schema(tmp_path):
    conn = sqlite3.connect(tmp_path / "scratch.db")
    conn.executescript("CREATE TABLE tmp (id INTEGER PRIMARY KEY)")
    conn.close()


@pytest.mark.integration
async def test_owned_write(connection):
    await connection.execute("DELETE FROM developer_data")
""".lstrip()
    )

    assert find_persistent_mutator_tests(tmp_path) == {
        "backend/tests/test_mixed_hermetic_and_integration.py": {
            "test_owned_write": ("persistent SQL write",)
        }
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        (
            "await session.execute(insert(records).values(code='C1'))",
            ("persistent SQL write",),
        ),
        (
            "session.add(record)\n    await session.commit()",
            ("persistent SQL write",),
        ),
        (
            'await client.request("PUT", "/store?default", content=b"data")',
            ("HTTP write",),
        ),
        (
            "await store.upsert_records(records)",
            ("repository write",),
        ),
        (
            'await client.post("/refresh/ncit/search-index")',
            ("persistent API write",),
        ),
    ],
)
def test_mutation_scanner_detects_common_library_write_shapes(
    tmp_path: Path,
    statement: str,
    expected: tuple[str, ...],
) -> None:
    test_root = tmp_path / "backend/tests"
    test_root.mkdir(parents=True)
    path = test_root / "test_library_write.py"
    path.write_text(
        (
            """
import pytest

@pytest.mark.integration
async def test_write(session, client, records, record):
    __STATEMENT__
"""
        )
        .replace("__STATEMENT__", statement)
        .lstrip()
    )

    assert find_persistent_mutator_tests(tmp_path) == {
        "backend/tests/test_library_write.py": {"test_write": expected}
    }


@pytest.mark.unit
def test_mutation_scanner_follows_local_helpers_and_class_markers(
    tmp_path: Path,
) -> None:
    test_root = tmp_path / "backend/tests"
    test_root.mkdir(parents=True)
    path = test_root / "test_class_write.py"
    path.write_text(
        """
import pytest

async def persist(connection):
    await connection.execute("DELETE FROM developer_data")

@pytest.mark.integration
class TestWrites:
    async def test_helper_write(self, connection):
        await persist(connection)
""".lstrip()
    )

    assert find_persistent_mutator_tests(tmp_path) == {
        "backend/tests/test_class_write.py": {
            "test_helper_write": ("persistent SQL write",)
        }
    }


@pytest.mark.unit
def test_every_detected_persistent_mutator_is_in_the_ownership_manifest() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest_path = root / "test_support/integration_mutators.toml"
    with manifest_path.open("rb") as stream:
        entries = tomllib.load(stream)["mutator"]
    manifested = frozenset(entry["path"] for entry in entries)

    with _exclusive_tree_scan():
        assert find_unmanifested_mutators(root, manifested_paths=manifested) == {}


@pytest.mark.unit
def test_default_integration_command_excludes_explicit_full_store_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)

    scripts = project["tool"]["pdm"]["scripts"]
    markers = project["tool"]["pytest"]["ini_options"]["markers"]

    assert "not full_store" in scripts["test-integration"]
    assert "not full_build" in scripts["test-integration"]
    assert "integration and full_store" in scripts["test-integration-full-store"]
    assert "integration and full_build" in scripts["test-integration-full-build"]
    assert any(marker.startswith("full_store:") for marker in markers)


@pytest.mark.unit
def test_mutating_integration_commands_actually_invoke_the_safe_wrapper() -> None:
    """Marker filtering alone does not prove the safe lane runs: a regression that
    dropped `scripts/run_safe_integration.py` from these two `pyproject.toml`
    command strings would leave the sibling marker-only test above green while
    every mutating test connects with an unpoisoned application environment.
    `test_all_runner_keeps_full_store_contracts_explicit` below covers the other
    dispatch path, `scripts/test_runner.py`'s `pdm run test --all`."""
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    scripts = project["tool"]["pdm"]["scripts"]

    assert "scripts/run_safe_integration.py" in scripts["test-integration"]
    assert "scripts/run_safe_integration.py" in scripts["test-integration-ci"]
    # The explicit real-corpus lane must NOT poison application settings — it
    # needs the actually-configured store, so it must not route through the
    # safe wrapper.
    assert (
        "scripts/run_safe_integration.py" not in scripts["test-integration-full-store"]
    )


@pytest.mark.unit
def test_full_store_runner_fails_when_a_selected_contract_skips(tmp_path: Path) -> None:
    pytest_executable = shutil.which("pytest")
    assert pytest_executable is not None
    root = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": (
            "postgresql+asyncpg://ontoprism_test_forbidden:forbidden@127.0.0.1:9/"
            "ontoprism_test_forbidden"
        ),
    }

    result = subprocess.run(  # noqa: S603
        [
            pytest_executable,
            "--require-full-store",
            "backend/tests/test_migrations_integration.py::"
            "test_migration_matches_cloned_db_schema",
            "-q",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "full-store gate rejected 1 skipped contract" in (
        result.stdout + result.stderr
    )


@pytest.mark.unit
def test_full_store_runner_fails_when_no_contract_is_selected() -> None:
    pytest_executable = shutil.which("pytest")
    assert pytest_executable is not None
    root = Path(__file__).resolve().parents[2]

    result = subprocess.run(  # noqa: S603
        [
            pytest_executable,
            "--require-full-store",
            "backend/tests/test_migrations_integration.py",
            "-k",
            "no_such_contract",
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "full-store gate ran no contracts" in result.stdout + result.stderr


@pytest.mark.unit
def test_collection_hook_rejects_real_noncompliant_tests_end_to_end() -> None:
    """The `pytest_collection_modifyitems` hook itself — not just the pure
    validators it calls — must actually reject noncompliant collected tests,
    across all three reject paths: a marker-only check (mutating_integration
    without integration), a manifest-only check (missing from
    integration_mutators.toml), and the scanner-dependent check (a real write
    signal with `integration` but no `mutating_integration`), which requires a
    real on-disk file the AST scanner (`test_root.rglob("test_*.py")`) can find.
    The probe must therefore match that glob, so it holds the shared tree-scan
    lock for its window: the two scanner tests above independently re-scan the
    same live tree while other tests execute concurrently on other workers, and
    must never observe the probe mid-write as an unmanifested mutator.
    """
    root = Path(__file__).resolve().parents[2]
    probe = root / "backend/tests/test_zz_collection_hook_probe.py"
    with _exclusive_tree_scan():
        probe.write_text(
            """
import pytest


@pytest.mark.mutating_integration
def test_missing_integration_marker() -> None:
    pass


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_unmanifested_write(connection):
    await connection.execute("DELETE FROM developer_data")


@pytest.mark.integration
async def test_write_without_mutating_marker(connection):
    await connection.execute("DELETE FROM developer_data")
""".lstrip()
        )
        try:
            pytest_executable = shutil.which("pytest")
            assert pytest_executable is not None
            result = subprocess.run(  # noqa: S603
                [pytest_executable, str(probe), "-q"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            probe.unlink(missing_ok=True)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert (
        "test_missing_integration_marker is mutating_integration without integration"
        in output
    )
    assert "test_unmanifested_write is missing from integration_mutators.toml" in output
    assert (
        "test_write_without_mutating_marker has write signals but lacks "
        "mutating_integration" in output
    )


@pytest.mark.unit
def test_all_runner_keeps_full_store_contracts_explicit() -> None:
    integration_suites = [
        suite for suite in suites(include_slow=True) if suite.kind == "integration"
    ]

    assert integration_suites
    assert all(
        "integration and not full_store and not full_build" in suite.cmd
        for suite in integration_suites
    )
    assert all(
        "scripts/run_safe_integration.py" in suite.cmd for suite in integration_suites
    )


@pytest.mark.unit
def test_ci_integration_job_has_no_serving_resources_to_open() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["integration-tests"]
    steps = job["steps"]
    test_step = next(
        step
        for step in steps
        if step.get("run") == "pdm run test-integration-ci-coverage"
    )

    assert job["env"] == {
        "DATABASE_URL": (
            "postgresql+asyncpg://ontoprism_test_forbidden:forbidden@127.0.0.1:9/"
            "ontoprism_test_forbidden"
        ),
        "NCIT_SPARQL_URL": "http://127.0.0.1:9",
        "UBERON_SPARQL_URL": "http://127.0.0.1:9",
    }
    assert "env" not in test_step
    step_names = {step.get("name") for step in steps}
    assert "Start Oxigraph" not in step_names
    assert "Provision Postgres schema (Alembic migrations)" not in step_names
    assert "Seed the fixture (Oxigraph graph + caDSR DB)" not in step_names
    assert "services" not in job
