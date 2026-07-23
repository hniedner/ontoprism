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
import uuid
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
    IntegrationResourceOwner,
    ResourceOwnershipError,
)

from backend.config import get_settings  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator

_DOCKER_PORT = re.compile(r"127\.0\.0\.1:(\d+)")


def _asyncpg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _create_test_database(
    owner: IntegrationResourceOwner, configured_url: str
) -> None:
    admin_url = _asyncpg_url(owner.postgres_admin_url(configured_url))
    created = False
    try:
        admin = await asyncpg.connect(admin_url)
        try:
            exists = await admin.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", owner.database_name
            )
            if exists:
                raise ResourceOwnershipError(
                    f"refusing pre-existing database {owner.database_name!r}"
                )
            await admin.execute(f'CREATE DATABASE "{owner.database_name}"')
            created = True
        finally:
            await admin.close()

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
        # The database did not exist before this function and this process has just
        # created its exact nonce name. Recover a partial setup without any wildcard.
        if created:
            cleanup_admin = await asyncpg.connect(admin_url)
            try:
                await cleanup_admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = $1 AND pid <> pg_backend_pid()",
                    owner.database_name,
                )
                await cleanup_admin.execute(f'DROP DATABASE "{owner.database_name}"')
            finally:
                await cleanup_admin.close()
        raise


async def _drop_test_database(
    owner: IntegrationResourceOwner, configured_url: str
) -> None:
    database = await asyncpg.connect(_asyncpg_url(owner.database_url(configured_url)))
    try:
        marker = await database.fetchval(
            "SELECT nonce FROM ontoprism_test_meta.resource_owner WHERE singleton"
        )
        current = await database.fetchval("SELECT current_database()")
        owner.verify_database(current, marker)
    finally:
        await database.close()

    admin_url = _asyncpg_url(owner.postgres_admin_url(configured_url))
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            owner.database_name,
        )
        await admin.execute(f'DROP DATABASE "{owner.database_name}"')
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


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("docker")
    if executable is None:
        pytest.fail("Docker is required for disposable Oxigraph integration tests")
    return subprocess.run(  # noqa: S603
        [executable, *args],
        check=check,
        capture_output=True,
        text=True,
    )


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


def _verify_oxigraph_owner(
    owner: IntegrationResourceOwner, container_name: str, data_dir: Path
) -> None:
    inspected = _docker("inspect", container_name)
    details = json.loads(inspected.stdout)[0]
    marker = details["Config"]["Labels"].get("org.ontoprism.test-owner")
    mounted_data_dir = next(
        (
            Path(mount["Source"])
            for mount in details["Mounts"]
            if mount["Destination"] == "/data"
        ),
        None,
    )
    file_marker = (data_dir / ".ontoprism-test-owner").read_text().strip()
    owner.verify_oxigraph(
        label=marker,
        mounted_data_dir=mounted_data_dir,
        expected_data_dir=data_dir,
        file_marker=file_marker,
    )


@pytest.fixture(scope="session")
def integration_resource_owner() -> IntegrationResourceOwner:
    """Identity shared only by disposable resources in this pytest process."""
    return IntegrationResourceOwner(nonce=uuid.uuid4().hex)


@pytest.fixture(scope="session")
def isolated_postgres_url(
    integration_resource_owner: IntegrationResourceOwner,
) -> Iterator[str]:
    """Yield a migrated, exact-owner Postgres database and drop only that database."""
    configured_url = get_settings().database_url
    asyncio.run(_create_test_database(integration_resource_owner, configured_url))
    database_url = integration_resource_owner.database_url(configured_url)
    try:
        _migrate_database(database_url)
        yield database_url
    finally:
        asyncio.run(_drop_test_database(integration_resource_owner, configured_url))


@pytest.fixture(scope="session")
def isolated_oxigraph_url(
    integration_resource_owner: IntegrationResourceOwner,
) -> Iterator[str]:
    """Yield a pinned disposable Oxigraph endpoint bound to a random loopback port."""
    prefix = f"ontoprism-oxigraph-{integration_resource_owner.nonce}-"
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        data_dir = Path(directory)
        (data_dir / ".ontoprism-test-owner").write_text(
            integration_resource_owner.nonce
        )
        run_command = integration_resource_owner.oxigraph_run_command(data_dir)
        container = integration_resource_owner.oxigraph_container_name
        started = _docker(*run_command[1:], check=False)
        if started.returncode != 0:
            partial = _docker("inspect", container, check=False)
            if partial.returncode == 0:
                _verify_oxigraph_owner(integration_resource_owner, container, data_dir)
                _docker("rm", "--force", container)
            pytest.fail(
                "disposable Oxigraph failed to start: "
                f"{started.stderr.strip() or started.stdout.strip()}"
            )
        try:
            published = _docker("port", container, "7878/tcp").stdout.strip()
            match = _DOCKER_PORT.fullmatch(published)
            if match is None:
                raise RuntimeError(f"unexpected Oxigraph port mapping: {published!r}")
            url = f"http://127.0.0.1:{match.group(1)}"
            _wait_for_oxigraph(url)
            fixture = (_ROOT / "scripts/ci/fixtures/ncit-fixture.ttl").read_bytes()
            response = httpx.put(
                f"{url}/store?default",
                content=fixture,
                headers={"Content-Type": "text/turtle"},
                timeout=30,
            )
            response.raise_for_status()
            yield url
        finally:
            _verify_oxigraph_owner(integration_resource_owner, container, data_dir)
            _docker("rm", "--force", container)


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
