"""Real-service contracts for disposable integration resources (#144)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from test_support.integration_resources import (
    IntegrationResourceOwner,
    ResourceOwnershipError,
)

from backend.db import dispose_engine, make_engine
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

pytestmark = pytest.mark.mutating_integration


@pytest.mark.integration
async def test_isolated_postgres_is_migrated_and_owner_marked(
    isolated_postgres_url: str,
    integration_resource_owner: IntegrationResourceOwner,
) -> None:
    engine = make_engine(isolated_postgres_url)
    try:
        async with engine.connect() as connection:
            database_name = (
                await connection.execute(text("SELECT current_database()"))
            ).scalar_one()
            marker = (
                await connection.execute(
                    text(
                        "SELECT nonce FROM ontoprism_test_meta.resource_owner "
                        "WHERE singleton"
                    )
                )
            ).scalar_one()
            xref_table = (
                await connection.execute(
                    text("SELECT to_regclass('public.xref_run')::text")
                )
            ).scalar_one()
    finally:
        await dispose_engine(engine)

    integration_resource_owner.verify_database(
        database_name=database_name,
        marker=marker,
    )
    assert xref_table == "xref_run"


@pytest.mark.integration
async def test_isolated_oxigraph_accepts_owned_graph_data(
    isolated_oxigraph_url: str,
    integration_resource_owner: IntegrationResourceOwner,
) -> None:
    graph_iri = integration_resource_owner.graph_iri("ownership-contract")
    ttl = b"<urn:subject> <urn:predicate> <urn:object> ."

    async with OxigraphHttpClient(isolated_oxigraph_url) as client:
        await client.load(
            ttl,
            content_type="text/turtle",
            graph_iri=graph_iri,
            replace=True,
        )
        loaded = await client.ask(
            f"ASK {{ GRAPH <{graph_iri}> {{ <urn:subject> ?p ?o }} }}"
        )

    assert loaded is True


@pytest.mark.integration
def test_postgres_lifecycle_removes_database_and_role_after_context(
    postgres_resource_provisioner: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
) -> None:
    owner = IntegrationResourceOwner(nonce=uuid.uuid4().hex)

    with postgres_resource_provisioner(owner) as (database_url, container_id):

        async def connect_once() -> None:
            connection = await asyncpg.connect(
                database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            )
            await connection.close()

        asyncio.run(connect_once())

    docker = shutil.which("docker")
    assert docker is not None
    inspected = subprocess.run(  # noqa: S603
        [docker, "inspect", container_id],
        check=False,
        capture_output=True,
        text=True,
    )
    assert inspected.returncode != 0


@pytest.mark.integration
def test_postgres_setup_failure_removes_owned_container(
    postgres_setup_failure_provisioner: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
) -> None:
    owner = IntegrationResourceOwner(nonce=uuid.uuid4().hex)

    with (
        pytest.raises(RuntimeError, match="injected migration failure"),
        postgres_setup_failure_provisioner(owner),
    ):
        pytest.fail("a failing migration must not yield a database")

    docker = shutil.which("docker")
    assert docker is not None
    inspected = subprocess.run(  # noqa: S603
        [docker, "inspect", owner.postgres_container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    assert inspected.returncode != 0


@pytest.mark.integration
def test_postgres_readiness_failure_removes_owned_container(
    postgres_readiness_failure_provisioner: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
) -> None:
    owner = IntegrationResourceOwner(nonce=uuid.uuid4().hex)

    with (
        pytest.raises(RuntimeError, match="injected Postgres readiness failure"),
        postgres_readiness_failure_provisioner(owner),
    ):
        pytest.fail("a Postgres readiness failure must not yield a database")

    docker = shutil.which("docker")
    assert docker is not None
    inspected = subprocess.run(  # noqa: S603
        [docker, "inspect", owner.postgres_container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    assert inspected.returncode != 0


@pytest.mark.integration
def test_oxigraph_lifecycle_removes_exact_container_after_context(
    oxigraph_resource_provisioner: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
) -> None:
    owner = IntegrationResourceOwner(nonce=uuid.uuid4().hex)

    with oxigraph_resource_provisioner(owner) as (_url, container_id):
        assert container_id

    docker = shutil.which("docker")
    assert docker is not None
    inspected = subprocess.run(  # noqa: S603
        [docker, "inspect", container_id],
        check=False,
        capture_output=True,
        text=True,
    )
    assert inspected.returncode != 0


@pytest.mark.integration
def test_postgres_cleanup_does_not_touch_a_familiar_prefix_decoy(
    postgres_resource_provisioner: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
    postgres_database_dropper: Callable[[IntegrationResourceOwner, str], None],
) -> None:
    protected = IntegrationResourceOwner(nonce=uuid.uuid4().hex)
    other_run = IntegrationResourceOwner(nonce=uuid.uuid4().hex)

    with postgres_resource_provisioner(protected) as (database_url, _container_id):
        admin_url = (
            make_url(database_url)
            .set(
                username="ontoprism_admin",
                password=protected.nonce,
                database="postgres",
            )
            .render_as_string(hide_password=False)
        )
        postgres_database_dropper(other_run, admin_url)

        async def owner_marker() -> str:
            connection = await asyncpg.connect(
                database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            )
            try:
                return str(
                    await connection.fetchval(
                        "SELECT nonce FROM ontoprism_test_meta.resource_owner "
                        "WHERE singleton"
                    )
                )
            finally:
                await connection.close()

        assert asyncio.run(owner_marker()) == protected.nonce


@pytest.mark.integration
def test_oxigraph_cleanup_does_not_touch_a_familiar_prefix_decoy(
    oxigraph_resource_provisioner: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
    oxigraph_container_remover: Callable[[IntegrationResourceOwner, str], None],
) -> None:
    protected = IntegrationResourceOwner(nonce=uuid.uuid4().hex)
    other_run = IntegrationResourceOwner(nonce=uuid.uuid4().hex)

    with oxigraph_resource_provisioner(protected) as (url, _container_id):
        with pytest.raises(ResourceOwnershipError, match="label mismatch"):
            oxigraph_container_remover(other_run, protected.oxigraph_container_name)

        async def store_is_alive() -> bool:
            async with OxigraphHttpClient(url) as client:
                return await client.ask("ASK { ?s ?p ?o }")

        assert asyncio.run(store_is_alive()) is True


@pytest.mark.integration
def test_oxigraph_setup_failure_removes_owned_container_and_directory(
    oxigraph_setup_failure_provisioner: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
) -> None:
    owner = IntegrationResourceOwner(nonce=uuid.uuid4().hex)

    with (
        pytest.raises(RuntimeError, match="injected Oxigraph seed failure"),
        oxigraph_setup_failure_provisioner(owner),
    ):
        pytest.fail("a failing seed must not yield an Oxigraph endpoint")

    docker = shutil.which("docker")
    assert docker is not None
    inspected = subprocess.run(  # noqa: S603
        [docker, "inspect", owner.oxigraph_container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    assert inspected.returncode != 0
    data_dirs = Path(tempfile.gettempdir()).glob(f"ontoprism-oxigraph-{owner.nonce}-*")
    assert list(data_dirs) == []


@pytest.mark.integration
def test_oxigraph_start_failure_removes_owned_directory(
    oxigraph_start_failure_provisioner: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
) -> None:
    owner = IntegrationResourceOwner(nonce=uuid.uuid4().hex)

    with (
        pytest.raises(RuntimeError, match="injected Oxigraph start failure"),
        oxigraph_start_failure_provisioner(owner),
    ):
        pytest.fail("a start failure must not yield an Oxigraph endpoint")

    data_dirs = Path(tempfile.gettempdir()).glob(f"ontoprism-oxigraph-{owner.nonce}-*")
    assert list(data_dirs) == []
