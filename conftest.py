"""Root fixtures and import setup for hermetic and integration test processes.

pytest imports this file before collecting tests, in the controller *and* in each
xdist worker (execnet workers start Python without full site initialization, so the
editable-install `.pth` finders are not registered there). Prepending the src roots
here makes `import ontolib` / `import backend` resolve to the real `*/src` packages —
ahead of the shadowing outer `ontolib/` & `backend/` directories — under prepend mode.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config

_ROOT = Path(__file__).parent
for _src in ("ontolib/src", "backend/src"):
    _abs = str(_ROOT / _src)
    if _abs not in sys.path:
        sys.path.insert(0, _abs)

from test_support.integration_resources import (  # noqa: E402
    DockerRun,
    IntegrationConnectionPolicy,
    IntegrationResourceOwner,
    IntegrationTestDeclaration,
    MutatorManifestEntry,
    ResourceOwnershipError,
    find_persistent_mutator_tests,
    remove_owned_container_by_name,
    run_docker,
    validate_integration_test_declaration,
    validate_mutator_manifest_files,
)

from backend.config import get_settings  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager

_DOCKER_PORT = re.compile(r"127\.0\.0\.1:(\d+)")
_SOCKET_CONNECT_ARGUMENTS = 2
_CONNECTION_POLICY = IntegrationConnectionPolicy()


def _integration_connection_audit(event: str, arguments: tuple[object, ...]) -> None:
    if event == "socket.connect" and len(arguments) >= _SOCKET_CONNECT_ARGUMENTS:
        _CONNECTION_POLICY.verify_socket_address(arguments[1])


if os.environ.get("ONTOPRISM_SAFE_INTEGRATION") == "1":
    sys.addaudithook(_integration_connection_audit)


def _mutator_manifest() -> tuple[MutatorManifestEntry, ...]:
    with (_ROOT / "test_support/integration_mutators.toml").open("rb") as stream:
        raw_entries = tomllib.load(stream)["mutator"]
    return tuple(
        MutatorManifestEntry(
            path=entry["path"],
            fixtures=frozenset(entry["fixtures"]),
            tests=frozenset(entry.get("tests", ())),
        )
        for entry in raw_entries
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Validate effective markers/fixtures for every collected integration test."""
    root = Path(str(config.rootpath)).resolve()
    detected = find_persistent_mutator_tests(root)
    manifest = _mutator_manifest()
    errors = list(validate_mutator_manifest_files(root, manifest=manifest))
    for item in items:
        markers = frozenset(marker.name for marker in item.iter_markers())
        if "mutating_integration" in markers and "integration" not in markers:
            errors.append(f"{item.nodeid} is mutating_integration without integration")
        if "integration" not in markers:
            continue
        path = Path(str(item.path)).resolve().relative_to(root).as_posix()
        name = getattr(item, "originalname", None) or item.name.split("[", 1)[0]
        declaration = IntegrationTestDeclaration(
            path=path,
            name=name,
            markers=markers,
            fixtures=frozenset(getattr(item, "fixturenames", ())),
        )
        errors.extend(
            validate_integration_test_declaration(
                declaration,
                manifest=manifest,
                detected_reasons=detected.get(path, {}).get(name, ()),
            )
        )
    if errors:
        raise pytest.UsageError(
            "integration ownership declarations are invalid:\n- "
            + "\n- ".join(sorted(set(errors)))
        )


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the explicit full-store gate option."""
    parser.addoption(
        "--require-full-store",
        action="store_true",
        help="fail when any selected full-store contract skips",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Turn skips into a failing explicit full-store gate."""
    if not session.config.getoption("--require-full-store"):
        return
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is None:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        return
    skipped = len(terminal.stats.get("skipped", ()))
    if skipped:
        terminal.write_line(
            f"full-store gate rejected {skipped} skipped contract"
            f"{'s' if skipped != 1 else ''}"
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        return
    if not terminal.stats.get("passed"):
        terminal.write_line("full-store gate ran no contracts")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _asyncpg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _create_test_database(
    owner: IntegrationResourceOwner, configured_url: str
) -> None:
    admin_url = _asyncpg_url(owner.postgres_admin_url(configured_url))
    try:
        admin = await asyncpg.connect(admin_url)
        try:
            database_exists = await admin.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                owner.database_name,
            )
            role_exists = await admin.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname = $1",
                owner.database_role,
            )
            if database_exists or role_exists:
                raise ResourceOwnershipError(
                    "refusing pre-existing integration database or role for "
                    f"{owner.nonce}"
                )
            async with admin.transaction():
                await admin.execute(
                    f'CREATE ROLE "{owner.database_role}" '
                    f"LOGIN PASSWORD '{owner.secret}'"
                )
                await admin.execute(
                    f'COMMENT ON ROLE "{owner.database_role}" IS '
                    f"'{owner.database_role_comment}'"
                )
            await admin.execute(
                f'CREATE DATABASE "{owner.database_name}" OWNER "{owner.database_role}"'
            )
        finally:
            await admin.close()

        extension_admin = await asyncpg.connect(
            _asyncpg_url(owner.postgres_admin_database_url(configured_url))
        )
        try:
            await extension_admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await extension_admin.close()

        database = await asyncpg.connect(
            _asyncpg_url(owner.database_url(configured_url))
        )
        try:
            await database.execute("CREATE SCHEMA ontoprism_test_meta")
            await database.execute(
                "CREATE TABLE ontoprism_test_meta.resource_owner "
                "(singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), "
                "nonce text NOT NULL)"
            )
            await database.execute(
                "INSERT INTO ontoprism_test_meta.resource_owner (singleton, nonce) "
                "VALUES (true, $1)",
                owner.nonce,
            )
        finally:
            await database.close()
    except BaseException:
        # Catalog ownership, not an in-memory flag, decides whether partial setup may
        # be removed. A missing/mismatched marker fails closed and preserves evidence.
        await _drop_test_database(
            owner,
            configured_url,
            require_schema_marker=False,
        )
        raise


async def _drop_test_database(
    owner: IntegrationResourceOwner,
    configured_url: str,
    *,
    require_schema_marker: bool = True,
) -> None:
    admin_url = _asyncpg_url(owner.postgres_admin_url(configured_url))
    admin = await asyncpg.connect(admin_url)
    try:
        identity = await admin.fetchrow(
            "SELECT d.datname, owner.rolname AS owner_role, "
            "shobj_description(owner.oid, 'pg_authid') AS role_comment "
            "FROM pg_database d "
            "JOIN pg_roles owner ON owner.oid = d.datdba "
            "WHERE d.datname = $1",
            owner.database_name,
        )
        role = await admin.fetchrow(
            "SELECT rolname, shobj_description(oid, 'pg_authid') AS role_comment "
            "FROM pg_roles WHERE rolname = $1",
            owner.database_role,
        )
        if identity is None:
            if role is not None:
                owner.verify_database_role(role["rolname"], role["role_comment"])
                await admin.execute(f'DROP ROLE "{owner.database_role}"')
            return

        owner.verify_database_role(
            identity["owner_role"],
            identity["role_comment"],
        )
        if require_schema_marker:
            database = await asyncpg.connect(
                _asyncpg_url(owner.database_url(configured_url))
            )
            try:
                marker = await database.fetchval(
                    "SELECT nonce FROM ontoprism_test_meta.resource_owner "
                    "WHERE singleton"
                )
                current = await database.fetchval("SELECT current_database()")
                owner.verify_database(database_name=current, marker=marker)
            finally:
                await database.close()
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            owner.database_name,
        )
        await admin.execute(f'DROP DATABASE "{owner.database_name}"')
        if role is None:
            raise ResourceOwnershipError("owned database role disappeared before drop")
        owner.verify_database_role(role["rolname"], role["role_comment"])
        await admin.execute(f'DROP ROLE "{owner.database_role}"')
    finally:
        await admin.close()


def _migrate_database(database_url: str) -> None:
    prior = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        config = Config(str(_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(_ROOT / "migrations"))
        command.upgrade(config, "head")
    finally:
        if prior is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prior
        get_settings.cache_clear()


def _wait_for_oxigraph(url: str) -> None:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.post(
                f"{url}/query",
                content=b"ASK {}",
                headers={
                    "Content-Type": "application/sparql-query",
                    "Accept": "application/sparql-results+json",
                },
                timeout=1,
            )
            response.raise_for_status()
            return
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError("disposable Oxigraph did not become ready") from last_error


def _wait_for_postgres(url: str) -> None:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None

    async def connect_once() -> None:
        connection = await asyncpg.connect(_asyncpg_url(url), timeout=1)
        await connection.close()

    while time.monotonic() < deadline:
        try:
            asyncio.run(connect_once())
            return
        except (OSError, asyncpg.PostgresError) as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError("disposable Postgres did not become ready") from last_error


def _inspect_owned_container(
    owner: IntegrationResourceOwner,
    container_id: str,
    *,
    docker_run: DockerRun = run_docker,
) -> dict[str, object]:
    inspected = docker_run("inspect", container_id)
    details: dict[str, object] = json.loads(inspected.stdout)[0]
    if details["Id"] != container_id:
        raise ResourceOwnershipError("container ID changed before teardown")
    config = details["Config"]
    if not isinstance(config, dict):
        raise ResourceOwnershipError("container configuration is malformed")
    labels = config["Labels"]
    label = labels.get("org.ontoprism.test-owner") if isinstance(labels, dict) else None
    owner.verify_container_label(label)
    return details


def _start_owned_container(
    owner: IntegrationResourceOwner,
    *,
    command_line: list[str],
    container_name: str,
    service_port: str,
    docker_run: DockerRun = run_docker,
) -> tuple[str, str]:
    try:
        started = docker_run(*command_line[1:], check=False)
    except BaseException:
        remove_owned_container_by_name(owner, container_name, docker_run=docker_run)
        raise
    if started.returncode != 0:
        remove_owned_container_by_name(owner, container_name, docker_run=docker_run)
        pytest.fail(
            f"disposable container failed to start: "
            f"{started.stderr.strip() or started.stdout.strip()}"
        )
    container_id = started.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        remove_owned_container_by_name(owner, container_name, docker_run=docker_run)
        raise RuntimeError(f"unexpected container ID: {container_id!r}")
    try:
        published = docker_run("port", container_id, service_port).stdout.strip()
        match = _DOCKER_PORT.fullmatch(published)
        if match is None:
            raise RuntimeError(f"unexpected container port mapping: {published!r}")
    except BaseException:
        _inspect_owned_container(owner, container_id, docker_run=docker_run)
        docker_run("rm", "--force", container_id)
        raise
    return container_id, match.group(1)


def _verify_oxigraph_owner(
    owner: IntegrationResourceOwner,
    container_id: str,
    data_dir: Path,
    *,
    docker_run: DockerRun = run_docker,
) -> None:
    details = _inspect_owned_container(owner, container_id, docker_run=docker_run)
    mounts = details["Mounts"]
    if not isinstance(mounts, list):
        raise ResourceOwnershipError("Oxigraph container mounts are malformed")
    mounted_data_dir = next(
        (Path(mount["Source"]) for mount in mounts if mount["Destination"] == "/data"),
        None,
    )
    file_marker = (data_dir / ".ontoprism-test-owner").read_text().strip()
    owner.verify_oxigraph(
        mounted_data_dir=mounted_data_dir,
        expected_data_dir=data_dir,
        file_marker=file_marker,
    )


def _verify_oxigraph_data_dir(
    owner: IntegrationResourceOwner,
    data_dir: Path,
) -> None:
    marker = (data_dir / ".ontoprism-test-owner").read_text().strip()
    owner.verify_oxigraph_data_dir(data_dir, marker)


def _seed_oxigraph(url: str) -> None:
    fixture = (_ROOT / "scripts/ci/fixtures/ncit-fixture.ttl").read_bytes()
    response = httpx.put(
        f"{url}/store?default",
        content=fixture,
        headers={"Content-Type": "text/turtle"},
        timeout=30,
    )
    response.raise_for_status()


@contextmanager
def _provision_postgres(
    owner: IntegrationResourceOwner,
    *,
    migrate_database: Callable[[str], None] = _migrate_database,
    wait_for_postgres: Callable[[str], None] = _wait_for_postgres,
    docker_run: DockerRun = run_docker,
) -> Iterator[tuple[str, str]]:
    """Provision a pinned Postgres container and nonce-owned restricted database."""
    container_id, port = _start_owned_container(
        owner,
        command_line=owner.postgres_run_command(),
        container_name=owner.postgres_container_name,
        service_port="5432/tcp",
        docker_run=docker_run,
    )
    admin_url = (
        f"postgresql+asyncpg://ontoprism_admin:{owner.secret}@127.0.0.1:{port}/postgres"
    )
    database_created = False
    postgres_ready = False
    with _CONNECTION_POLICY.registered(admin_url):
        try:
            wait_for_postgres(admin_url)
            postgres_ready = True
            asyncio.run(_create_test_database(owner, admin_url))
            database_created = True
            database_url = owner.database_url(admin_url)
            migrate_database(database_url)
            yield database_url, container_id
        finally:
            try:
                if postgres_ready:
                    asyncio.run(
                        _drop_test_database(
                            owner,
                            admin_url,
                            require_schema_marker=database_created,
                        )
                    )
            finally:
                _inspect_owned_container(owner, container_id, docker_run=docker_run)
                docker_run("rm", "--force", container_id)


@contextmanager
def _provision_oxigraph(
    owner: IntegrationResourceOwner,
    *,
    seed_store: Callable[[str], None] = _seed_oxigraph,
    before_start: Callable[[], None] | None = None,
    wait_for_oxigraph: Callable[[str], None] = _wait_for_oxigraph,
    docker_run: DockerRun = run_docker,
) -> Iterator[tuple[str, str]]:
    """Provision and exactly tear down one pinned disposable Oxigraph container."""
    prefix = f"ontoprism-oxigraph-{owner.nonce}-"
    data_dir = Path(tempfile.mkdtemp(prefix=prefix))
    (data_dir / ".ontoprism-test-owner").write_text(owner.nonce)
    run_command = owner.oxigraph_run_command(data_dir)
    container_id: str | None = None
    url: str | None = None
    try:
        if before_start is not None:
            before_start()
        container_id, port = _start_owned_container(
            owner,
            command_line=run_command,
            container_name=owner.oxigraph_container_name,
            service_port="7878/tcp",
            docker_run=docker_run,
        )
        url = f"http://127.0.0.1:{port}"
        with _CONNECTION_POLICY.registered(url):
            wait_for_oxigraph(url)
            seed_store(url)
            yield url, container_id
    finally:
        try:
            if container_id is not None:
                _verify_oxigraph_owner(
                    owner, container_id, data_dir, docker_run=docker_run
                )
                docker_run("rm", "--force", container_id)
        finally:
            _verify_oxigraph_data_dir(owner, data_dir)
            shutil.rmtree(data_dir)


@pytest.fixture(scope="session")
def integration_resource_owner() -> IntegrationResourceOwner:
    """Identity shared only by disposable resources in this pytest process."""
    return IntegrationResourceOwner(nonce=uuid.uuid4().hex)


@pytest.fixture(scope="session")
def isolated_postgres_url(
    integration_resource_owner: IntegrationResourceOwner,
) -> Iterator[str]:
    """Yield one process-shared, migrated database in a disposable container.

    The whole database is destroyed at session end, but that is not a cleanup
    substitute within the session: most consuming tests still `DELETE` their own
    rows because a *later* test in the same session depends on their absence.
    Two schema round-trip tests restore ``head`` afterward for the same reason.
    Only a couple of tests skip cleanup entirely, where no later test depends on
    the absence of what they wrote.
    """
    with _provision_postgres(integration_resource_owner) as (
        database_url,
        _container_id,
    ):
        yield database_url


@pytest.fixture(scope="session")
def isolated_oxigraph_url(
    integration_resource_owner: IntegrationResourceOwner,
) -> Iterator[str]:
    """Yield one process-shared disposable Oxigraph on a random loopback port.

    Mutating tests use exact run-owned graphs or restore the bounded default fixture.
    """
    with _provision_oxigraph(integration_resource_owner) as (url, _container_id):
        yield url


@pytest.fixture
def postgres_resource_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory for real Postgres lifecycle contracts."""
    return _provision_postgres


@pytest.fixture
def postgres_setup_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that injects failure after database creation."""

    def fail_migration(_database_url: str) -> None:
        raise RuntimeError("injected migration failure")

    return lambda owner: _provision_postgres(
        owner,
        migrate_database=fail_migration,
    )


@pytest.fixture
def postgres_readiness_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that injects failure before database creation."""

    def fail_readiness(_admin_url: str) -> None:
        raise RuntimeError("injected Postgres readiness failure")

    return lambda owner: _provision_postgres(
        owner,
        wait_for_postgres=fail_readiness,
    )


@pytest.fixture
def postgres_docker_run_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that injects failure at the `docker run` step."""

    def fail_at_run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "run":
            return subprocess.CompletedProcess(
                args=list(args),
                returncode=1,
                stdout="",
                stderr="injected docker run failure",
            )
        return run_docker(*args, check=check)

    return lambda owner: _provision_postgres(owner, docker_run=fail_at_run)


@pytest.fixture
def postgres_docker_port_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that injects failure at the `docker port` step."""

    def fail_at_port(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "port":
            return subprocess.CompletedProcess(
                args=list(args), returncode=0, stdout="not-a-port-mapping", stderr=""
            )
        return run_docker(*args, check=check)

    return lambda owner: _provision_postgres(owner, docker_run=fail_at_port)


@pytest.fixture
def postgres_docker_id_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that lies about a real `docker run`'s container ID."""

    def fail_at_run_id(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = run_docker(*args, check=check)
        if args and args[0] == "run" and result.returncode == 0:
            return subprocess.CompletedProcess(
                args=result.args,
                returncode=0,
                stdout="not-a-container-id",
                stderr=result.stderr,
            )
        return result

    return lambda owner: _provision_postgres(owner, docker_run=fail_at_run_id)


@pytest.fixture
def postgres_database_dropper() -> Callable[[IntegrationResourceOwner, str], None]:
    """Attempt exact database cleanup for reject-branch lifecycle contracts."""

    def drop(owner: IntegrationResourceOwner, admin_url: str) -> None:
        asyncio.run(_drop_test_database(owner, admin_url))

    return drop


@pytest.fixture
def postgres_database_creator() -> Callable[[IntegrationResourceOwner, str], None]:
    """Attempt exact database/role creation for reject-branch lifecycle contracts."""

    def create(owner: IntegrationResourceOwner, admin_url: str) -> None:
        asyncio.run(_create_test_database(owner, admin_url))

    return create


@pytest.fixture
def owned_container_inspector() -> Callable[
    [IntegrationResourceOwner, str, DockerRun],
    dict[str, object],
]:
    """Attempt exact container inspection for reject-branch lifecycle contracts."""

    def inspect(
        owner: IntegrationResourceOwner,
        container_id: str,
        docker_run: DockerRun,
    ) -> dict[str, object]:
        return _inspect_owned_container(owner, container_id, docker_run=docker_run)

    return inspect


@pytest.fixture
def oxigraph_owner_verifier() -> Callable[
    [
        IntegrationResourceOwner,
        str,
        Path,
        DockerRun,
    ],
    None,
]:
    """Attempt exact Oxigraph ownership verification for reject-branch contracts."""

    def verify(
        owner: IntegrationResourceOwner,
        container_id: str,
        data_dir: Path,
        docker_run: DockerRun,
    ) -> None:
        _verify_oxigraph_owner(owner, container_id, data_dir, docker_run=docker_run)

    return verify


@pytest.fixture
def oxigraph_resource_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory for real Oxigraph lifecycle contracts."""
    return _provision_oxigraph


@pytest.fixture
def oxigraph_setup_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that injects failure after store startup."""

    def fail_seed(_url: str) -> None:
        raise RuntimeError("injected Oxigraph seed failure")

    return lambda owner: _provision_oxigraph(owner, seed_store=fail_seed)


@pytest.fixture
def oxigraph_start_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that injects failure before container startup."""

    def fail_start() -> None:
        raise RuntimeError("injected Oxigraph start failure")

    return lambda owner: _provision_oxigraph(owner, before_start=fail_start)


@pytest.fixture
def oxigraph_readiness_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that injects failure before the store is ready."""

    def fail_readiness(_url: str) -> None:
        raise RuntimeError("injected Oxigraph readiness failure")

    return lambda owner: _provision_oxigraph(owner, wait_for_oxigraph=fail_readiness)


@pytest.fixture
def oxigraph_docker_id_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that lies about a real `docker run`'s container ID."""

    def fail_at_run_id(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = run_docker(*args, check=check)
        if args and args[0] == "run" and result.returncode == 0:
            return subprocess.CompletedProcess(
                args=result.args,
                returncode=0,
                stdout="not-a-container-id",
                stderr=result.stderr,
            )
        return result

    return lambda owner: _provision_oxigraph(owner, docker_run=fail_at_run_id)


@pytest.fixture
def oxigraph_docker_port_failure_provisioner() -> Callable[
    [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
]:
    """Return a context factory that injects failure at the `docker port` step."""

    def fail_at_port(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "port":
            return subprocess.CompletedProcess(
                args=list(args), returncode=0, stdout="not-a-port-mapping", stderr=""
            )
        return run_docker(*args, check=check)

    return lambda owner: _provision_oxigraph(owner, docker_run=fail_at_port)


@pytest.fixture
def oxigraph_container_remover() -> Callable[[IntegrationResourceOwner, str], None]:
    """Attempt exact container cleanup for reject-branch lifecycle contracts."""
    return remove_owned_container_by_name


@pytest.fixture
def isolated_postgres_settings(isolated_postgres_url: str) -> Iterator[None]:
    """Point settings at the migrated disposable database for one mutating test."""
    prior = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = isolated_postgres_url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prior
        get_settings.cache_clear()


@pytest.fixture
def isolated_oxigraph_settings(isolated_oxigraph_url: str) -> Iterator[None]:
    """Point NCIt settings at the disposable store for one mutating test."""
    prior = os.environ.get("NCIT_SPARQL_URL")
    os.environ["NCIT_SPARQL_URL"] = isolated_oxigraph_url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("NCIT_SPARQL_URL", None)
        else:
            os.environ["NCIT_SPARQL_URL"] = prior
        get_settings.cache_clear()
