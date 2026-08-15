"""The built-browser FastAPI double must satisfy production API contracts."""

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from test_support.frontend_fastapi_double import app

from backend.api.v1.icdo import IcdoPage
from backend.api.v1.ncit import ConceptMappings
from backend.api.v1.refresh import RefreshReport

pytestmark = pytest.mark.unit


def test_double_icdo_page_validates_against_production_dto() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/icdo/4.0/topography/list",
            headers={"X-ICDO-Entitlement": "licensed"},
        )

    assert response.status_code == 200
    TypeAdapter(IcdoPage).validate_python(response.json())


def test_double_refresh_report_validates_against_production_dto() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/refresh")

    assert response.status_code == 200
    RefreshReport.model_validate(response.json())


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

    disabled_dto = ConceptMappings.model_validate(disabled.json())
    missing_dto = ConceptMappings.model_validate(missing.json())
    enabled_dto = ConceptMappings.model_validate(enabled.json())
    assert disabled_dto.mappings == []
    assert missing_dto.mappings == []
    assert [mapping.object_id for mapping in enabled_dto.mappings] == [
        "8240/3",
        "8241/3",
        "8248/1",
    ]
