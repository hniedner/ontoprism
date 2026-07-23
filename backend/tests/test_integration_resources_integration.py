"""Real-service contracts for disposable integration resources (#144)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from backend.db import dispose_engine, make_engine
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from test_support.integration_resources import IntegrationResourceOwner

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

    integration_resource_owner.verify_database(database_name, marker)
    assert xref_table == "xref_run"


@pytest.mark.integration
async def test_isolated_oxigraph_accepts_only_run_owned_graph_data(
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
