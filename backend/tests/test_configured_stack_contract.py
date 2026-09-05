"""Read-only contracts for the exact configured UAT stack."""

import asyncio
import base64

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


async def _icdo_record_constraints() -> set[str]:
    connection = await asyncpg.connect(_asyncpg_dsn())
    try:
        rows = await connection.fetch(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='icdo_record'::regclass AND contype='c'"
        )
    finally:
        await connection.close()
    return {str(row["conname"]) for row in rows}


def test_configured_xref_schema_matches_current_read_contract() -> None:
    assert asyncio.run(_xref_columns()) == _XREF_COLUMNS


def test_configured_icdo_schema_binds_relational_columns_to_payload() -> None:
    assert {
        "ck_icdo_record_code_payload",
        "ck_icdo_record_level_payload",
        "ck_icdo_record_behaviour_payload",
    } <= asyncio.run(_icdo_record_constraints())


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


@pytest.mark.parametrize(
    ("edition", "axis"),
    [
        ("3.2", "morphology"),
        ("4.0", "morphology"),
        ("4.0", "topography"),
    ],
)
def test_configured_icdo_repository_routes_serve_certified_active_generation(
    live_api_client: TestClient, edition: str, axis: str
) -> None:
    entitlement = get_settings().icdo_entitlement_key
    assert entitlement
    headers = {"X-ICDO-Entitlement": entitlement}
    root = f"/api/v1/icdo/{edition}/{axis}"

    metadata = live_api_client.get(f"{root}/metadata", headers=headers)
    listing = live_api_client.get(f"{root}/list", headers=headers)

    assert metadata.status_code == 200, metadata.text
    assert listing.status_code == 200, listing.text
    metadata_body = metadata.json()
    listing_body = listing.json()
    assert listing_body["hits"]
    assert listing_body["activation_identity"] == metadata_body["activation_identity"]
    assert listing_body["serving_identity"] == metadata_body["serving_identity"]

    code = listing_body["hits"][0]["code"]
    search = live_api_client.get(f"{root}/search", params={"q": code}, headers=headers)
    segment = base64.urlsafe_b64encode(code.encode("ascii")).decode("ascii").rstrip("=")
    detail = live_api_client.get(f"{root}/concepts/{segment}", headers=headers)

    assert search.status_code == 200, search.text
    assert detail.status_code == 200, detail.text
    assert any(hit["code"] == code for hit in search.json()["hits"])
    assert detail.json()["record"]["code"] == code
    assert detail.json()["activation_identity"] == metadata_body["activation_identity"]
    assert detail.json()["serving_identity"] == metadata_body["serving_identity"]
