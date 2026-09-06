"""Validate the frontend FastAPI double against production page DTOs, input rejection,
metadata echoes, the refresh report, and mappings.
"""

import pytest
from fastapi import HTTPException
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
    assert decode_icdo_record(response.json()["record"]).code == code


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
