"""Real behavioral test of the liveness probe — drives the actual ASGI app, no mocks."""

from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.api
def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]


@pytest.mark.api
def test_health_and_openapi_report_installed_backend_version(
    client: TestClient,
) -> None:
    installed_version = version("ontoprism-backend")

    assert client.get("/health").json()["version"] == installed_version
    assert client.get("/openapi.json").json()["info"]["version"] == installed_version
