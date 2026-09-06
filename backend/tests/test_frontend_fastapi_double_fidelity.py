"""Validate the built-browser double's ICD-O, refresh, and mapping DTOs."""

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from test_support.frontend_fastapi_double import app

from backend.api.v1.icdo import IcdoPage
from backend.api.v1.ncit import ConceptMappings
from backend.api.v1.refresh import RefreshReport
from ontolib.repositories.cadsr.models import CdeSearchPage
from ontolib.terminologies.ncit.models import BrowsePage, SearchPage
from ontolib.terminologies.uberon.models import UberonBrowsePage, UberonSearchPage

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("path", "page_type", "metadata"),
    [
        (
            "/api/v1/ncit/list?limit=50&offset=50&sort=code:desc"
            "&representation_status=legacy-precoordinated",
            BrowsePage,
            {"query": "", "limit": 50, "offset": 50, "sort": "code:desc"},
        ),
        (
            "/api/v1/ncit/search?q=neoplasm&limit=10&offset=20&sort=label:asc"
            "&representation_status=legacy-precoordinated",
            SearchPage,
            {"query": "neoplasm", "limit": 10, "offset": 20, "sort": "label:asc"},
        ),
        (
            "/api/v1/cadsr/list?limit=50&offset=100&sort=public_id:desc",
            CdeSearchPage,
            {"query": "", "limit": 50, "offset": 100, "sort": "public_id:desc"},
        ),
        (
            "/api/v1/cadsr/search?q=tumor&limit=10&offset=20&sort=name:asc",
            CdeSearchPage,
            {"query": "tumor", "limit": 10, "offset": 20, "sort": "name:asc"},
        ),
        (
            "/api/v1/uberon/list?limit=50&offset=100&sort=label:desc&source=cl",
            UberonBrowsePage,
            {"query": "", "limit": 50, "offset": 100, "sort": "label:desc"},
        ),
        (
            "/api/v1/uberon/search?q=cell&limit=10&offset=20&sort=code:asc&source=cl",
            UberonSearchPage,
            {"query": "cell", "limit": 10, "offset": 20, "sort": "code:asc"},
        ),
    ],
)
def test_double_local_repository_page_metadata_matches_production_contract(
    path: str, page_type: type[object], metadata: dict[str, object]
) -> None:
    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in metadata} == metadata
    TypeAdapter(page_type).validate_python(body)


def test_double_icdo_page_validates_against_production_dto() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/icdo/4.0/topography/list",
            headers={"X-ICDO-Entitlement": "licensed"},
        )

    assert response.status_code == 200
    TypeAdapter(IcdoPage).validate_json(response.content)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/icdo/4.0/topography/list?sort=relevance",
        "/api/v1/icdo/4.0/topography/list?behaviour=3",
        "/api/v1/icdo/4.0/morphology/list?level=leaf",
        "/api/v1/icdo/3.2/topography/list",
    ],
)
def test_double_rejects_the_same_invalid_icdo_grid_inputs_as_production(
    path: str,
) -> None:
    with TestClient(app) as client:
        response = client.get(path, headers={"X-ICDO-Entitlement": "licensed"})

    assert response.status_code == 422


def test_double_applies_and_echoes_valid_icdo_sort_and_repeated_filters() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/icdo/4.0/topography/list",
            params=[
                ("sort", "preferred:desc"),
                ("level", "category"),
                ("level", "leaf"),
            ],
            headers={"X-ICDO-Entitlement": "licensed"},
        )
        empty = client.get(
            "/api/v1/icdo/3.2/morphology/list",
            params={"behaviour": "9"},
            headers={"X-ICDO-Entitlement": "licensed"},
        )

    assert response.status_code == 200
    assert response.json()["sort"] == "preferred:desc"
    assert response.json()["hits"][0]["level"] == "leaf"
    assert empty.status_code == 200
    assert empty.json()["total"] == 0
    assert empty.json()["hits"] == []


def test_double_refresh_report_validates_against_production_dto() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/refresh")

    assert response.status_code == 200
    RefreshReport.model_validate_json(response.content)


def test_double_licensed_mappings_require_capability_and_entitlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_LICENSED_MAPPINGS", raising=False)
    with TestClient(app) as client:
        disabled = client.get(
            "/api/v1/ncit/concepts/C188218/mappings",
            headers={"X-ICDO-Entitlement": "licensed"},
        )
        missing = client.get("/api/v1/ncit/concepts/C188218/mappings")
        monkeypatch.setenv("ENABLE_LICENSED_MAPPINGS", "true")
        enabled = client.get(
            "/api/v1/ncit/concepts/C188218/mappings",
            headers={"X-ICDO-Entitlement": "licensed"},
        )

    disabled_dto = ConceptMappings.model_validate_json(disabled.content)
    missing_dto = ConceptMappings.model_validate_json(missing.content)
    enabled_dto = ConceptMappings.model_validate_json(enabled.content)
    assert disabled_dto.mappings == []
    assert missing_dto.mappings == []
    assert [mapping.object_id for mapping in enabled_dto.mappings] == [
        "8240/3",
        "8241/3",
        "8248/1",
    ]
