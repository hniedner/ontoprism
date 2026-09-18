"""Validate the frontend FastAPI double against production page DTOs, input rejection,
metadata echoes, the refresh report, and mappings.
"""

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from test_support.frontend_fastapi_double import app

from backend.api.v1.icdo import (
    IcdoDetail,
    IcdoPage,
    require_served_icdo_dataset,
    validate_icdo_grid_filters,
)
from backend.api.v1.ncit import ConceptMappings
from backend.api.v1.refresh import RefreshReport
from ontolib.decomposition.read_models import ConceptDecomposition
from ontolib.repositories.cadsr.models import CdeSearchPage
from ontolib.repositories.icdo.models import (
    IcdoAxis,
    IcdoBehaviour,
    IcdoRecordLevel,
    decode_icdo_record,
)
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
            {
                "query": "",
                "limit": 50,
                "offset": 50,
                "sort": "code:desc",
                "representation_status": "legacy-precoordinated",
            },
        ),
        (
            "/api/v1/ncit/search?q=neoplasm&limit=10&offset=20&sort=label:asc"
            "&representation_status=legacy-precoordinated",
            SearchPage,
            {
                "query": "neoplasm",
                "limit": 10,
                "offset": 20,
                "sort": "label:asc",
                "representation_status": "legacy-precoordinated",
            },
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
            {
                "query": "",
                "limit": 50,
                "offset": 100,
                "sort": "label:desc",
                "source": "cl",
            },
        ),
        (
            "/api/v1/uberon/search?q=cell&limit=10&offset=20&sort=code:asc&source=cl",
            UberonSearchPage,
            {
                "query": "cell",
                "limit": 10,
                "offset": 20,
                "sort": "code:asc",
                "source": "cl",
            },
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


@pytest.mark.parametrize(
    ("path", "code", "base", "specificity", "behaviour", "parent"),
    [
        ("/api/v1/icdo/3.2/morphology/list", "8503/0", "8503", None, "0", None),
        ("/api/v1/icdo/4.0/morphology/list", "8240A/3", "8240", "A", "3", None),
        ("/api/v1/icdo/4.0/topography/list", "C34.9", None, None, None, "C34"),
    ],
)
def test_double_icdo_page_union_arms_validate_record_invariants(
    path: str,
    code: str,
    base: str | None,
    specificity: str | None,
    behaviour: str | None,
    parent: str | None,
) -> None:
    with TestClient(app) as client:
        response = client.get(path, headers={"X-ICDO-Entitlement": "licensed"})

    assert response.status_code == 200
    TypeAdapter(IcdoPage).validate_json(response.content)
    required_nullable_fields = {
        "parent_code",
        "base_morphology",
        "specificity",
        "behaviour",
    }
    assert required_nullable_fields <= response.json()["hits"][0].keys()
    record = decode_icdo_record(response.json()["hits"][0])
    assert (
        record.code,
        record.base_morphology,
        record.specificity,
        record.behaviour,
        record.parent_code,
    ) == (code, base, specificity, behaviour, parent)


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/api/v1/icdo/3.2/morphology/concepts/ODUwMy8w", "8503/0"),
        ("/api/v1/icdo/4.0/morphology/concepts/ODI0MEEvMw", "8240A/3"),
        ("/api/v1/icdo/4.0/topography/concepts/QzM0Ljk", "C34.9"),
    ],
)
def test_double_icdo_detail_union_arms_validate_record_invariants(
    path: str, code: str
) -> None:
    with TestClient(app) as client:
        response = client.get(path, headers={"X-ICDO-Entitlement": "licensed"})

    assert response.status_code == 200
    TypeAdapter(IcdoDetail).validate_json(response.content)
    required_nullable_fields = {
        "parent_code",
        "base_morphology",
        "specificity",
        "behaviour",
    }
    assert required_nullable_fields <= response.json()["record"].keys()
    assert decode_icdo_record(response.json()["record"]).code == code


@pytest.mark.parametrize(
    ("path", "response_model"),
    [
        ("/api/v1/ncit/list", BrowsePage),
        ("/api/v1/ncit/search", SearchPage),
        ("/api/v1/uberon/list", UberonBrowsePage),
        ("/api/v1/uberon/search", UberonSearchPage),
        ("/api/v1/icdo/{edition}/{axis}/list", IcdoPage),
        ("/api/v1/icdo/{edition}/{axis}/search", IcdoPage),
        ("/api/v1/icdo/{edition}/{axis}/concepts/{code}", IcdoDetail),
    ],
)
def test_double_repository_routes_apply_production_response_models(
    path: str, response_model: object
) -> None:
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path
    )

    assert route.response_model == response_model


@pytest.mark.parametrize(
    ("route_path", "request_path"),
    [
        ("/api/v1/cadsr/list", "/api/v1/cadsr/list"),
        ("/api/v1/cadsr/search", "/api/v1/cadsr/search?q=tumor"),
    ],
)
def test_double_cadsr_pages_use_strict_production_response_model(
    route_path: str,
    request_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == route_path
    )
    assert route.response_model == CdeSearchPage

    with TestClient(app) as client:
        response = client.get(request_path)
    assert response.status_code == 200
    CdeSearchPage.model_validate_json(response.content)

    original_call = cast(
        "Callable[..., Awaitable[dict[str, object]]]", route.dependant.call
    )

    async def return_drifted_body(**kwargs: object) -> dict[str, object]:
        return {**await original_call(**kwargs), "unexpected": "strict drift"}

    monkeypatch.setattr(route.dependant, "call", return_drifted_body)
    with TestClient(app, raise_server_exceptions=False) as client:
        drifted = client.get(request_path)

    assert drifted.status_code == 500


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


@pytest.mark.parametrize(
    ("axis", "behaviour", "level", "path"),
    [
        ("topography", ["3"], None, "/api/v1/icdo/4.0/topography/list?behaviour=3"),
        ("morphology", None, ["leaf"], "/api/v1/icdo/4.0/morphology/list?level=leaf"),
    ],
)
def test_double_and_production_grid_validation_share_rejection_semantics(
    axis: IcdoAxis,
    behaviour: list[IcdoBehaviour] | None,
    level: list[IcdoRecordLevel] | None,
    path: str,
) -> None:
    with pytest.raises(HTTPException) as production_error:
        validate_icdo_grid_filters(axis, behaviour, level)
    with TestClient(app) as client:
        doubled = client.get(path, headers={"X-ICDO-Entitlement": "licensed"})

    assert production_error.value.status_code == doubled.status_code == 422


def test_double_and_production_reject_unserved_detail_dataset_consistently() -> None:
    with pytest.raises(HTTPException) as production_error:
        require_served_icdo_dataset("3.2", "topography")
    with TestClient(app) as client:
        doubled = client.get(
            "/api/v1/icdo/3.2/topography/concepts/QzM0Ljk",
            headers={"X-ICDO-Entitlement": "licensed"},
        )

    assert production_error.value.status_code == doubled.status_code == 422


def test_double_echoes_sort_and_filter_metadata_and_filters_matching_rows() -> None:
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
    assert response.json()["behaviour"] == []
    assert response.json()["level"] == ["category", "leaf"]
    assert response.json()["hits"][0]["level"] == "leaf"
    assert empty.status_code == 200
    assert empty.json()["behaviour"] == ["9"]
    assert empty.json()["level"] == []
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


def test_double_decomposition_serves_every_field_production_serializes() -> None:
    response = TestClient(app).get("/api/v1/ncit/concepts/C3262/decomposition")

    production = ConceptDecomposition.model_validate_json(response.text)

    assert response.json() == production.model_dump(mode="json")
