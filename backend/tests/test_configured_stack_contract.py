"""Read-only contracts for the exact configured UAT stack."""

import asyncio

import asyncpg
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.full_store]

_XREF_COLUMNS = {
    "xref_generation": {
        "id",
        "source",
        "content_sha256",
        "source_metadata",
        "graph_iri",
        "run_id",
        "state",
        "created_at",
        "published_at",
    },
    "concept_xref": {
        "generation_id",
        "generation_source",
        "run_id",
        "subject_system",
        "subject_version",
        "subject_id",
        "predicate_id",
        "object_system",
        "object_version",
        "object_id",
        "mapping_justification",
        "confidence",
        "lifecycle_state",
        "review_status",
        "author",
        "evidence",
    },
}


def _asyncpg_dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _xref_columns() -> dict[str, set[str]]:
    connection = await asyncpg.connect(_asyncpg_dsn())
    try:
        rows = await connection.fetch(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=ANY($1::text[])",
            list(_XREF_COLUMNS),
        )
    finally:
        await connection.close()
    observed = {table: set() for table in _XREF_COLUMNS}
    for row in rows:
        table = str(row["table_name"])
        observed[table] = observed[table] | {str(row["column_name"])}
    return observed


def test_configured_xref_schema_matches_current_read_contract() -> None:
    assert asyncio.run(_xref_columns()) == _XREF_COLUMNS


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/ncit/concepts/C10000/mappings",
        "/api/v1/uberon/concepts/CL:0000000/alignments",
        "/api/v1/icdo/4.0/topography/concepts/QzAwLjA",
    ],
)
def test_configured_detail_mapping_routes_do_not_fail(
    live_api_client: TestClient, path: str
) -> None:
    headers = {"X-ICDO-Entitlement": get_settings().icdo_entitlement_key or ""}
    response = live_api_client.get(path, headers=headers)

    assert response.status_code == 200, response.text
