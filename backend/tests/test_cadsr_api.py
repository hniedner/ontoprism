"""caDSR API endpoints against a real temp SQLite DB (no mocks, CI-runnable)."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
def test_cde_detail_renders_concepts_and_pvs(cadsr_client: TestClient) -> None:
    resp = cadsr_client.get("/api/v1/cadsr/cdes/100")
    assert resp.status_code == 200
    body = resp.json()
    assert body["short_name"] == "NEOPLASM_HIST"
    assert body["permissible_values"][0]["value"] == "Carcinoma"
    assert any(c["concept_code"] == "C3262" for c in body["concepts"])


@pytest.mark.api
def test_unknown_cde_is_404(cadsr_client: TestClient) -> None:
    assert cadsr_client.get("/api/v1/cadsr/cdes/999999").status_code == 404


@pytest.mark.api
def test_search_returns_hits(cadsr_client: TestClient) -> None:
    body = cadsr_client.get("/api/v1/cadsr/search", params={"q": "neoplasm"}).json()
    assert body["total"] == 1
    assert body["hits"][0]["public_id"] == "100"


@pytest.mark.api
def test_cdes_for_concept_join(cadsr_client: TestClient) -> None:
    # The caDSR<->NCIt cross-link: CDEs mapped to NCIt concept C3262.
    resp = cadsr_client.get("/api/v1/cadsr/concepts/C3262/cdes")
    assert resp.status_code == 200
    assert [c["public_id"] for c in resp.json()] == ["100"]
