"""Exact ownership identities for disposable integration-test resources."""

from __future__ import annotations

import ast
import json
import re
import secrets
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

_NONCE = re.compile(r"[0-9a-f]{32}")
_SOCKET_ADDRESS_PARTS = 2
_GRAPH_COMPONENT = re.compile(r"[a-z][a-z0-9-]{0,47}")
_PERSISTENT_SQL = re.compile(
    r"\b(?:ALTER\s+TABLE|CREATE\s+(?:DATABASE|EXTENSION|SCHEMA|TABLE)|"
    r"DELETE\s+FROM|DROP\s+(?:DATABASE|SCHEMA|TABLE)|INSERT\s+INTO|TRUNCATE|"
    r"UPDATE\s+[A-Za-z_])",
    re.IGNORECASE,
)
_REPOSITORY_WRITES: Final = frozenset(
    {
        "begin_publication",
        "claim_work_item",
        "complete_work_item",
        "create_run",
        "fail_run",
        "fail_work_item",
        "finish_run",
        "invalidate_run",
        "persist_promotions",
        "populate",
        "publish_artifact",
        "quarantine_stale",
        "record_publication_failure",
        "rebuild",
        "resume_run",
        "run_pipeline",
        "update_run_metrics",
        "upsert_records",
        "upsert_run",
    }
)
_OXIGRAPH_IMAGE = (
    "ghcr.io/oxigraph/oxigraph@sha256:"
    "cc943499d4724fbb348c75c623335c69a047de71c59852413b0d0467d3caebe3"
)
_POSTGRES_IMAGE = (
    "pgvector/pgvector@sha256:"
    "7f5681e45237acdf546cf7cdc0dfc0ed7752ede857fda6e54f6ea21b936f8742"
)
_DEAD_DATABASE_URL: Final = (
    "postgresql+asyncpg://ontoprism_test_forbidden:forbidden@127.0.0.1:9/"
    "ontoprism_test_forbidden"
)
_DEAD_HTTP_URL: Final = "http://127.0.0.1:9"
_OXIGRAPH_DATA_DIR_PREFIX: Final = "ontoprism-oxigraph-"


class ResourceOwnershipError(RuntimeError):
    """A persistent resource is not owned by the current test run."""


class DockerRun(Protocol):
    """The `run_docker` calling convention shared by lifecycle helpers and doubles.

    Positional arguments are Docker CLI tokens; `check` toggles raise-on-nonzero.
    Typing the injectable `docker_run` seam as this Protocol rather than
    `Callable[..., ...]` keeps callers from passing a non-`str` token or an unknown
    keyword and keeps a substituted double honest about the same signature.
    """

    def __call__(
        self, *args: str, check: bool = ...
    ) -> subprocess.CompletedProcess[str]: ...


def run_docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Docker and capture text output for ownership-safe lifecycle helpers.

    Functions in this module accept an injectable ``docker_run`` keyword defaulting
    to this function; that default is bound once at import time, not via a live
    lookup. Substitute a test double by passing ``docker_run=`` explicitly —
    monkeypatching this name after import does not affect an already-bound default.
    """
    executable = shutil.which("docker")
    if executable is None:
        raise RuntimeError("Docker is required for disposable integration tests")
    return subprocess.run(  # noqa: S603
        [executable, *args],
        check=check,
        capture_output=True,
        text=True,
    )


def remove_owned_container_by_name(
    owner: IntegrationResourceOwner,
    container_name: str,
    *,
    docker_run: DockerRun = run_docker,
) -> None:
    """Remove an exact labeled container, distinguishing absence from Docker failure."""
    inspected = docker_run("inspect", container_name, check=False)
    if inspected.returncode != 0:
        message = inspected.stderr.strip() or inspected.stdout.strip()
        lowered = message.lower()
        if "no such object" in lowered or "no such container" in lowered:
            return
        raise RuntimeError(f"Docker inspect failed for {container_name}: {message}")
    details = json.loads(inspected.stdout)[0]
    container_id = details["Id"]
    config = details.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    label = labels.get("org.ontoprism.test-owner") if isinstance(labels, dict) else None
    owner.verify_container_label(label)
    docker_run("rm", "--force", container_id)


@dataclass(slots=True)
class IntegrationConnectionPolicy:
    """Allow TCP connections only to explicitly registered disposable services."""

    _allowed: set[tuple[str, int]] = field(default_factory=set, init=False)

    @staticmethod
    def _target(url: str) -> tuple[str, int]:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
        if host not in {"127.0.0.1", "localhost", "::1"} or port is None:
            raise ResourceOwnershipError(
                f"integration service is not loopback-owned: {url!r}"
            )
        return ("127.0.0.1" if host == "localhost" else host, port)

    def _register_url(self, url: str) -> None:
        """Allow the loopback host and port encoded by *url*."""
        self._allowed.add(self._target(url))

    def _unregister_url(self, url: str) -> None:
        """Remove the loopback host and port encoded by *url*."""
        self._allowed.discard(self._target(url))

    @contextmanager
    def registered(self, url: str) -> Iterator[None]:
        """Allow *url* only for this context, guaranteeing symmetric removal.

        This is the only public way to widen the allow-list: hand-pairing a
        register/unregister call is not part of the public API (the underscore-
        prefixed helpers are internal), so a forgotten ``finally`` can no longer
        permanently widen or narrow it for the rest of this worker process.
        """
        self._register_url(url)
        try:
            yield
        finally:
            self._unregister_url(url)

    def verify_socket_address(self, address: object) -> None:
        """Refuse an unregistered TCP socket address before connection."""
        if not isinstance(address, tuple):
            raise ResourceOwnershipError(
                f"integration socket target is not owned by this run: {address!r}"
            )
        if len(address) < _SOCKET_ADDRESS_PARTS:
            raise ResourceOwnershipError(
                f"integration socket target is malformed: {address!r}"
            )
        host, port, *_ = address
        if isinstance(host, bytes):
            host = host.decode()
        if host == "localhost":
            host = "127.0.0.1"
        if not isinstance(host, str) or not isinstance(port, int):
            raise ResourceOwnershipError(
                f"integration socket target is malformed: {address!r}"
            )
        if (host, port) not in self._allowed:
            raise ResourceOwnershipError(
                f"integration TCP target is not owned by this run: {host}:{port}"
            )


@dataclass(frozen=True, slots=True)
class IntegrationResourceOwner:
    """Collision-resistant identity shared by one integration-test run."""

    nonce: str
    # Independently random, not derived from `nonce`: `nonce` is written verbatim
    # into container labels, data-directory names, and graph IRIs — all inspectable
    # by anything that can run `docker inspect`/list the filesystem without ever
    # connecting to Postgres. A deterministic function of `nonce` (e.g. a fixed
    # hash) would not fix that: the transform is public source code, so anyone who
    # recovers `nonce` through those channels recovers the credential in one line.
    # Excluded from equality/repr so two owners sharing a nonce (there are none in
    # this codebase, but nothing prevents it) are still equal by identity, without
    # ever printing the credential. `init=False`: like `nonce`, this value is
    # interpolated verbatim into raw (non-parameterized) SQL, but unlike `nonce` it
    # is not validated against a strict format (`nonce` is, for exactly that reason).
    # `secrets.token_hex(32)` is hex-only and so literal-safe, but that safety must
    # not depend on the caller — a caller-supplied `secret` could be anything, so it
    # must never be externally settable.
    secret: str = field(
        default_factory=lambda: secrets.token_hex(32),
        compare=False,
        repr=False,
        init=False,
    )

    def __post_init__(self) -> None:
        if _NONCE.fullmatch(self.nonce) is None:
            raise ValueError("test resource nonce must be 32 lowercase hex characters")

    @property
    def database_name(self) -> str:
        """Return the exact Postgres database owned by this run."""
        return f"ontoprism_test_{self.nonce}"

    @property
    def database_role(self) -> str:
        """Return the restricted login role owned by this run."""
        return f"ontoprism_test_{self.nonce}"

    @property
    def database_role_comment(self) -> str:
        """Return the independent catalog marker for the restricted role."""
        return f"ontoprism-test-owner:{self.nonce}"

    @property
    def oxigraph_container_name(self) -> str:
        """Return the exact disposable Oxigraph container name."""
        return f"ontoprism-oxigraph-test-{self.nonce}"

    @property
    def postgres_container_name(self) -> str:
        """Return the exact disposable Postgres container name."""
        return f"ontoprism-postgres-test-{self.nonce}"

    def graph_iri(self, component: str) -> str:
        """Return a run-owned graph IRI for a validated logical component."""
        if _GRAPH_COMPONENT.fullmatch(component) is None:
            raise ValueError("graph component must be lowercase alphanumeric/hyphen")
        return f"urn:ontoprism:test:{self.nonce}:{component}"

    def verify_database(self, *, database_name: str, marker: str) -> None:
        """Refuse a database unless both its exact name and marker match this run."""
        if database_name != self.database_name:
            raise ResourceOwnershipError(
                f"database name is not owned by this run: {database_name!r}"
            )
        if marker != self.nonce:
            raise ResourceOwnershipError(
                "database owner marker does not match this run"
            )

    def verify_database_role(self, role: str, comment: str | None) -> None:
        """Refuse a database owner role unless its exact name and marker match."""
        if role != self.database_role:
            raise ResourceOwnershipError("database owner role does not match this run")
        if comment != self.database_role_comment:
            raise ResourceOwnershipError("database role marker does not match this run")

    def verify_container_label(self, label: str | None) -> None:
        """Refuse a container unless its independent owner label matches this run."""
        if label != self.nonce:
            raise ResourceOwnershipError("container owner label mismatch")

    def verify_oxigraph(
        self,
        *,
        mounted_data_dir: Path | None,
        expected_data_dir: Path,
        file_marker: str,
    ) -> None:
        """Refuse an Oxigraph target unless all independent owner signals match."""
        if (
            mounted_data_dir is None
            or mounted_data_dir.resolve() != expected_data_dir.resolve()
        ):
            raise ResourceOwnershipError("Oxigraph container data mount mismatch")
        if file_marker != self.nonce:
            raise ResourceOwnershipError("Oxigraph data file marker mismatch")

    def verify_oxigraph_data_dir(self, data_dir: Path, file_marker: str) -> None:
        """Refuse filesystem cleanup unless the exact run-owned directory matches."""
        expected_prefix = f"{_OXIGRAPH_DATA_DIR_PREFIX}{self.nonce}-"
        if not data_dir.is_absolute() or not data_dir.name.startswith(expected_prefix):
            raise ResourceOwnershipError("Oxigraph data directory owner mismatch")
        if file_marker != self.nonce:
            raise ResourceOwnershipError("Oxigraph data file marker mismatch")

    def postgres_admin_url(self, configured_url: str) -> str:
        """Derive an administrative connection without touching the configured DB."""
        return (
            make_url(configured_url)
            .set(database="postgres")
            .render_as_string(hide_password=False)
        )

    def database_url(self, configured_url: str) -> str:
        """Derive the exact current-run database URL from server credentials."""
        url = make_url(configured_url).set(
            username=self.database_role,
            password=self.secret,
            database=self.database_name,
        )
        return url.render_as_string(
            hide_password=False,
        )

    def postgres_admin_database_url(self, configured_url: str) -> str:
        """Derive an administrative URL targeting only this run-owned database."""
        return (
            make_url(configured_url)
            .set(database=self.database_name)
            .render_as_string(hide_password=False)
        )

    def oxigraph_run_command(self, data_dir: Path) -> list[str]:
        """Build the pinned, loopback-only disposable Oxigraph command."""
        expected_prefix = f"{_OXIGRAPH_DATA_DIR_PREFIX}{self.nonce}-"
        if not data_dir.is_absolute() or not data_dir.name.startswith(expected_prefix):
            raise ResourceOwnershipError(
                "Oxigraph data directory is not an absolute current-run-owned path"
            )
        return [
            "docker",
            "run",
            "--detach",
            "--name",
            self.oxigraph_container_name,
            "--label",
            f"org.ontoprism.test-owner={self.nonce}",
            "--publish",
            "127.0.0.1::7878",
            "--volume",
            f"{data_dir}:/data",
            _OXIGRAPH_IMAGE,
            "serve",
            "--location",
            "/data",
            "--bind",
            "0.0.0.0:7878",
        ]

    def postgres_run_command(self) -> list[str]:
        """Build the pinned, loopback-only disposable Postgres command."""
        return [
            "docker",
            "run",
            "--detach",
            "--name",
            self.postgres_container_name,
            "--label",
            f"org.ontoprism.test-owner={self.nonce}",
            "--publish",
            "127.0.0.1::5432",
            "--tmpfs",
            "/var/lib/postgresql/data:rw",
            "--env",
            "POSTGRES_USER=ontoprism_admin",
            "--env",
            f"POSTGRES_PASSWORD={self.secret}",
            "--env",
            "POSTGRES_DB=postgres",
            _POSTGRES_IMAGE,
        ]


@dataclass(frozen=True, slots=True)
class IntegrationTestDeclaration:
    """Effective pytest metadata for one collected integration test."""

    path: str
    name: str
    markers: frozenset[str]
    fixtures: frozenset[str]


class CollectedTestName(Protocol):
    """A test's collection identity: its module path and function name.

    This is the whole surface `validate_mutator_manifest_entries` reads. Typing it
    structurally lets a full `IntegrationTestDeclaration` and a marker-less
    `_ParsedTestName` (a name found by AST scan, never collected) both qualify
    without the latter having to fabricate empty `markers`/`fixtures` it does not know.
    """

    @property
    def path(self) -> str: ...

    @property
    def name(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _ParsedTestName:
    """A `test_*` function found by AST-parsing a source file, not pytest collection.

    Manifest-path validation only needs the (path, name) pair to check a selector
    resolves to a real function; it has no markers or fixtures because nothing was
    collected, so it deliberately does not masquerade as `IntegrationTestDeclaration`.
    """

    path: str
    name: str


@dataclass(frozen=True, slots=True)
class MutatorManifestEntry:
    """Owned fixtures required by one module or selected tests in that module."""

    path: str
    fixtures: frozenset[str]
    tests: frozenset[str] = frozenset()

    def matches(self, declaration: IntegrationTestDeclaration) -> bool:
        """Return whether this rule applies to the collected test."""
        return self.path == declaration.path and (
            not self.tests or declaration.name in self.tests
        )


def validate_integration_test_declaration(
    declaration: IntegrationTestDeclaration,
    *,
    manifest: tuple[MutatorManifestEntry, ...],
    detected_reasons: tuple[str, ...],
) -> tuple[str, ...]:
    """Return fail-closed ownership errors for one collected integration test."""
    errors: list[str] = []
    markers = declaration.markers
    identity = f"{declaration.path}::{declaration.name}"
    if {"full_store", "mutating_integration"} <= markers:
        errors.append(f"{identity} is both full_store and mutating_integration")
    if detected_reasons and "mutating_integration" not in markers:
        errors.append(
            f"{identity} has write signals but lacks mutating_integration: "
            f"{', '.join(detected_reasons)}"
        )
    if "mutating_integration" not in markers:
        return tuple(errors)

    matches = tuple(entry for entry in manifest if entry.matches(declaration))
    if not matches:
        errors.append(f"{identity} is missing from integration_mutators.toml")
        return tuple(errors)
    if len(matches) > 1:
        errors.append(f"{identity} matches multiple integration_mutators.toml entries")
        return tuple(errors)

    matched_entry = next(iter(matches))
    missing = matched_entry.fixtures - declaration.fixtures
    if missing:
        errors.append(
            f"{identity} is missing owned fixtures: {', '.join(sorted(missing))}"
        )
    reasons = set(detected_reasons)
    postgres_fixtures = {
        "isolated_postgres_settings",
        "isolated_postgres_url",
        "postgres_resource_provisioner",
        "postgres_setup_failure_provisioner",
    }
    oxigraph_fixtures = {
        "oxigraph_sibling_store_root",
        "isolated_oxigraph_settings",
        "isolated_oxigraph_url",
        "oxigraph_resource_provisioner",
        "oxigraph_setup_failure_provisioner",
    }
    if reasons & {
        "persistent SQL write",
        "repository write",
        "schema migration",
    } and not (postgres_fixtures & declaration.fixtures):
        errors.append(f"{identity} has a Postgres write without an owned database")
    if reasons & {"HTTP write", "Oxigraph write"} and not (
        oxigraph_fixtures & declaration.fixtures
    ):
        errors.append(f"{identity} has an Oxigraph write without an owned store")
    if "persistent API write" in reasons and not (
        postgres_fixtures & declaration.fixtures
        and oxigraph_fixtures & declaration.fixtures
    ):
        errors.append(
            f"{identity} has a persistent API write without both owned services"
        )
    return tuple(errors)


def validate_mutator_manifest_entries(
    *,
    manifest: tuple[MutatorManifestEntry, ...],
    declarations: tuple[CollectedTestName, ...],
) -> tuple[str, ...]:
    """Return errors for manifest paths or test selectors that collect nowhere."""
    collected: dict[str, set[str]] = {}
    for declaration in declarations:
        collected.setdefault(declaration.path, set()).add(declaration.name)

    errors: list[str] = []
    for entry in manifest:
        names = collected.get(entry.path)
        if names is None:
            errors.append(
                f"integration_mutators.toml path does not collect: {entry.path}"
            )
            continue
        for name in sorted(entry.tests - names):
            errors.append(
                "integration_mutators.toml test selector does not collect: "
                f"{entry.path}::{name}"
            )
    return tuple(errors)


def build_safe_integration_environment(
    environment: Mapping[str, str],
    *,
    data_root: Path,
) -> dict[str, str]:
    """Build an integration-lane environment with dead application endpoints.

    Application settings start fail-closed and are switched to isolated disposable
    containers only by explicit fixtures.
    """
    safe = dict(environment)
    safe.pop("ONTOPRISM_TEST_POSTGRES_ADMIN_URL", None)
    safe["ONTOPRISM_SAFE_INTEGRATION"] = "1"
    safe["DATABASE_URL"] = _DEAD_DATABASE_URL
    safe["NCIT_SPARQL_URL"] = _DEAD_HTTP_URL
    safe["UBERON_SPARQL_URL"] = _DEAD_HTTP_URL
    root = data_root.resolve()
    safe["CADSR_DB_PATH"] = str(root / "cadsr/cde_repository.db")
    safe["CADSR_DATA_DIR"] = str(root / "cadsr")
    safe["NCIT_OWL_DIR"] = str(root / "ncit-owl")
    safe["NCIT_STORE_DIR"] = str(root / "oxigraph-ncit")
    return safe


def _is_marker(decorator: ast.expr, marker: str) -> bool:
    return (
        isinstance(decorator, ast.Attribute)
        and decorator.attr == marker
        and isinstance(decorator.value, ast.Attribute)
        and decorator.value.attr == "mark"
        and isinstance(decorator.value.value, ast.Name)
        and decorator.value.value.id == "pytest"
    )


def _module_has_integration_marker(tree: ast.Module) -> bool:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in node.targets
            )
            and "pytest.mark.integration" in ast.unparse(node.value)
        ):
            return True
    return False


def _integration_scopes(
    tree: ast.Module,
) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    module_marked = _module_has_integration_marker(tree)
    scopes: list[ast.AsyncFunctionDef | ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name.startswith("test_") and (
                module_marked
                or any(_is_marker(item, "integration") for item in node.decorator_list)
            ):
                scopes.append(node)
            continue
        if not isinstance(node, ast.ClassDef):
            continue
        class_marked = module_marked or any(
            _is_marker(item, "integration") for item in node.decorator_list
        )
        scopes.extend(
            method
            for method in node.body
            if isinstance(method, (ast.AsyncFunctionDef, ast.FunctionDef))
            and method.name.startswith("test_")
            and (
                class_marked
                or any(
                    _is_marker(item, "integration") for item in method.decorator_list
                )
            )
        )
    return scopes


def _call_mutation_reasons(node: ast.Call) -> set[str]:  # noqa: C901
    reasons: set[str] = set()
    function_name: str | None = None
    if isinstance(node.func, ast.Attribute):
        function_name = node.func.attr
    elif isinstance(node.func, ast.Name):
        function_name = node.func.id

    if function_name == "load":
        reasons.add("Oxigraph write")
    if function_name in {"downgrade", "upgrade", "_alembic"}:
        reasons.add("schema migration")
    if function_name in _REPOSITORY_WRITES:
        reasons.add("repository write")
    if function_name in {"add", "add_all", "commit"}:
        reasons.add("persistent SQL write")
    if function_name == "execute" and any(
        isinstance(nested, ast.Call)
        and isinstance(nested.func, ast.Name)
        and nested.func.id in {"delete", "insert", "update"}
        for argument in node.args
        for nested in ast.walk(argument)
    ):
        reasons.add("persistent SQL write")
    if function_name in {"delete", "patch", "put"}:
        reasons.add("HTTP write")
    if (
        function_name == "request"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.upper() in {"DELETE", "PATCH", "POST", "PUT"}
    ):
        reasons.add("HTTP write")
    if function_name == "post" and any(
        isinstance(argument, ast.Constant)
        and isinstance(argument.value, str)
        and (
            "/refresh/ncit/reload" in argument.value
            or "/refresh/ncit/search-index" in argument.value
        )
        for argument in node.args
    ):
        reasons.add("persistent API write")
    return reasons


def _mutation_reasons(
    scope: ast.AST,
    *,
    helpers: Mapping[str, ast.AsyncFunctionDef | ast.FunctionDef],
    visited: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    reasons: set[str] = set()
    for node in ast.walk(scope):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _PERSISTENT_SQL.search(node.value)
        ):
            reasons.add("persistent SQL write")
        if isinstance(node, ast.Call):
            reasons.update(_call_mutation_reasons(node))
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in helpers
                and node.func.id not in visited
            ):
                reasons.update(
                    _mutation_reasons(
                        helpers[node.func.id],
                        helpers=helpers,
                        visited=visited | {node.func.id},
                    )
                )
    return tuple(sorted(reasons))


def find_persistent_mutator_tests(
    root: Path,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Find persistent-write signals for each integration test function."""
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for test_root in (root / "backend/tests", root / "ontolib/tests"):
        if not test_root.exists():
            continue
        for path in test_root.rglob("test_*.py"):
            relative = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(), filename=str(path))
            helpers = {
                node.name: node
                for node in tree.body
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                and not node.name.startswith("test_")
            }
            tests = {
                scope.name: reasons
                for scope in _integration_scopes(tree)
                if (reasons := _mutation_reasons(scope, helpers=helpers))
            }
            if tests:
                result[relative] = tests
    return result


def validate_mutator_manifest_files(
    root: Path,
    *,
    manifest: tuple[MutatorManifestEntry, ...],
) -> tuple[str, ...]:
    """Return errors for manifest paths/selectors absent from the source tree."""
    declarations: list[_ParsedTestName] = []
    missing_paths: list[str] = []
    for path in sorted({entry.path for entry in manifest}):
        source_path = root / path
        if not source_path.is_file():
            missing_paths.append(
                f"integration_mutators.toml path does not exist: {path}"
            )
            continue
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name.startswith("test_")
        }
        declarations.extend(
            _ParsedTestName(
                path=path,
                name=name,
            )
            for name in names
        )
    return (
        *missing_paths,
        *validate_mutator_manifest_entries(
            manifest=manifest,
            declarations=tuple(declarations),
        ),
    )


def find_persistent_mutators(root: Path) -> dict[str, tuple[str, ...]]:
    """Find integration modules and the persistent-write signals they contain."""
    return {
        path: tuple(
            sorted(
                {reason for test_reasons in tests.values() for reason in test_reasons}
            )
        )
        for path, tests in find_persistent_mutator_tests(root).items()
    }


def find_unmanifested_mutators(
    root: Path, *, manifested_paths: frozenset[str]
) -> dict[str, tuple[str, ...]]:
    """Find integration tests with persistent-write signals missing from the manifest.

    The scanner considers only declared integration test functions (or a whole module
    with a module-level integration marker), avoiding temporary SQLite setup used by
    hermetic unit tests in otherwise mixed files.
    """
    return {
        path: reasons
        for path, reasons in find_persistent_mutators(root).items()
        if path not in manifested_paths
    }
