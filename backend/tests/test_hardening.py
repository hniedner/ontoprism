"""Behavioral tests for API hardening: headers, request-id, error envelope, authz.

Drives the real ASGI app end-to-end (no mocks). The reload/refresh guards fire before
any live store is touched, so these need no running services.
"""

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response

from backend.config import get_settings
from backend.dependencies import get_ncit_client
from backend.main import create_app
from backend.middleware import _apply_hardening_headers
from ontolib.core.exceptions import StorageError


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Isolate settings between tests (env-driven api_key must not leak across)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.security
def test_security_headers_and_request_id_present(app_client: TestClient) -> None:
    resp = app_client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Request-ID"]


@pytest.mark.security
def test_incoming_request_id_is_echoed(app_client: TestClient) -> None:
    resp = app_client.get("/health", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"


@pytest.mark.security
def test_error_envelope_shape(app_client: TestClient) -> None:
    resp = app_client.post(
        "/api/v1/refresh/ncit/reload", json={"source_path": "data/nope.csv"}
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]  # kept for existing clients
    assert body["error"] == "http_error"
    assert body["request_id"]


@pytest.mark.security
def test_cors_allows_configured_origin(app_client: TestClient) -> None:
    resp = app_client.get("/health", headers={"Origin": "http://localhost:5175"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5175"


@pytest.mark.security
def test_reload_rejects_path_traversal(app_client: TestClient) -> None:
    resp = app_client.post(
        "/api/v1/refresh/ncit/reload",
        json={"source_path": "../../../../etc/passwd"},
    )
    assert resp.status_code == 403
    assert "allowlist" in resp.json()["detail"]


@pytest.mark.security
def test_reload_rejects_absolute_outside_allowlist(app_client: TestClient) -> None:
    resp = app_client.post(
        "/api/v1/refresh/ncit/reload", json={"source_path": "/etc/hosts.ttl"}
    )
    assert resp.status_code == 403


@pytest.mark.security
def test_mutating_endpoints_require_api_key_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        unauth = client.post("/api/v1/refresh")
        assert unauth.status_code == 401
        wrong = client.post("/api/v1/refresh", headers={"X-API-Key": "nope"})
        assert wrong.status_code == 401
        # Correct key passes authorization (reaches the handler / store layer).
        ok = client.post("/api/v1/refresh", headers={"X-API-Key": "s3cret"})
        assert ok.status_code != 401
        # The file-ingesting reload endpoint carries its OWN auth dependency — guard
        # it independently so a future edit that drops it is caught.
        reload_unauth = client.post(
            "/api/v1/refresh/ncit/reload", json={"source_path": "data/x.ttl"}
        )
        assert reload_unauth.status_code == 401
        reload_auth = client.post(
            "/api/v1/refresh/ncit/reload",
            json={"source_path": "data/missing.ttl"},
            headers={"X-API-Key": "s3cret"},
        )
        assert reload_auth.status_code != 401


@pytest.mark.security
def test_empty_api_key_leaves_endpoints_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty API_KEY must mean "open" (dev default), not "locked behind an empty
    # string" — otherwise a blank config would 401 every unauthenticated caller.
    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        resp = client.post("/api/v1/refresh")
        assert resp.status_code != 401


@pytest.mark.security
def test_unhandled_error_carries_request_id_and_headers() -> None:
    # A non-HTTPException 500 is handled by Starlette's outer ServerErrorMiddleware,
    # which sits above our middleware — assert it still ships the request-id and
    # security headers (and a request_id in the body).
    app = create_app()
    app.add_api_route("/_boom", _raise_boom, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/_boom")
    assert resp.status_code == 500
    assert resp.json()["request_id"]
    assert resp.headers["X-Request-ID"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def _raise_boom() -> None:
    raise RuntimeError("boom")


@pytest.mark.unit
def test_apply_hardening_headers_without_request_id() -> None:
    response = Response()
    assert "X-Request-ID" not in response.headers
    _apply_hardening_headers(response, None)
    assert "X-Request-ID" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.security
def test_open_mode_logs_startup_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # With no API key, startup must emit an operator warning so an intended-auth
    # misconfiguration is visible rather than silently running open.
    monkeypatch.delenv("API_KEY", raising=False)
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING), TestClient(create_app()):
        pass
    assert any("open mode" in r.getMessage() for r in caplog.records)


@pytest.mark.security
def test_reload_storage_error_is_logged_and_returns_502(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A real store fault during reload must return 502 AND leave a server-side error
    # log (HTTPException responses are otherwise not logged by the error handler).
    ttl = tmp_path / "graph.ttl"
    ttl.write_text("@prefix ex: <http://e/> . ex:a ex:b ex:c .")
    monkeypatch.setenv("RELOAD_ALLOWED_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _FailingClient:
        async def count(self) -> int:
            return 0

        async def load(self, *_args: Any, **_kwargs: Any) -> None:
            raise StorageError("oxigraph unreachable")

    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FailingClient
    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        resp = client.post(
            "/api/v1/refresh/ncit/reload", json={"source_path": str(ttl)}
        )
    assert resp.status_code == 502
    assert "reload failed" in resp.json()["detail"].lower()
    assert any("reload failed" in r.getMessage().lower() for r in caplog.records)
