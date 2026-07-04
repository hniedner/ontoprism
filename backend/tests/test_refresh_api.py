"""Refresh endpoint tests: report (live), and reload guards (no store mutation)."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
def test_reload_rejects_unsupported_extension(app_client: TestClient) -> None:
    resp = app_client.post(
        "/api/v1/refresh/ncit/reload", json={"source_path": "some-data.csv"}
    )
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["detail"]


@pytest.mark.api
def test_reload_missing_file_is_404(app_client: TestClient) -> None:
    resp = app_client.post(
        "/api/v1/refresh/ncit/reload",
        json={"source_path": "missing-file-xyz-12345.ttl"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_refresh_reports_ncit_version_and_counts(live_api_client: TestClient) -> None:
    resp = live_api_client.post("/api/v1/refresh")
    assert resp.status_code == 200
    body = resp.json()
    repos = {r["name"]: r for r in body["repositories"]}
    assert repos["ncit"]["healthy"] is True
    assert repos["ncit"]["version"] == "26.02d"
    assert repos["ncit"]["item_count"] == 12836426
    assert "cadsr" in repos
