"""Guard the real-store smoke against unintended backend mutations."""

import pytest
from fastapi.testclient import TestClient
from scripts.smoke_real import app


@pytest.mark.api
def test_smoke_backend_denies_search_index_rebuild_before_store_access() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/refresh/ncit/search-index")
    assert response.status_code == 405
    assert response.json() == {"detail": "smoke backend is read-only"}


@pytest.mark.api
def test_smoke_backend_allows_remote_search_to_reach_validation() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/clinicaltrials/search", json={})
    assert response.status_code == 422
