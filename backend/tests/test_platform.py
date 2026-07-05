"""Behavioral tests for platform hardening: rate limiting, version check, readiness."""

import logging
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import get_ncit_client
from backend.main import check_ncit_version, create_app
from backend.middleware import RateLimitMiddleware, RequestContextMiddleware
from ontolib.core.exceptions import StorageError


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeClient:
    def __init__(self, version: str | None = "26.02d", *, fail: bool = False) -> None:
        self._version = version
        self._fail = fail

    async def version(self) -> str | None:
        if self._fail:
            raise StorageError("store unreachable")
        return self._version


# --------------------------------------------------------------------- rate limit


@pytest.mark.api
def test_rate_limit_returns_429_over_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        codes = [client.get("/health").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


@pytest.mark.api
def test_rate_limit_429_has_retry_after_and_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        client.get("/health")
        blocked = client.get("/health")
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    body = blocked.json()
    assert body["error"] == "rate_limited"
    assert body["request_id"]  # 429 still carries the request id (middleware order)
    assert blocked.headers["X-Content-Type-Options"] == "nosniff"  # + security headers


@pytest.mark.api
def test_rate_limit_window_resets_after_expiry() -> None:
    # After the window elapses the counter resets — a capped client is not blocked
    # forever (guards the reset branch).
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit=1, window_sec=0.05)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/ping").status_code == 200
        assert client.get("/ping").status_code == 429  # over cap within the window
        time.sleep(0.08)  # let the window expire
        assert client.get("/ping").status_code == 200  # reset


@pytest.mark.api
def test_rate_limit_disabled_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        codes = [client.get("/health").status_code for _ in range(10)]
    assert all(code == 200 for code in codes)


# ---------------------------------------------------------------- version check


async def test_version_mismatch_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        await check_ncit_version(_FakeClient(version="99.99z"), "26.02d")
    assert any("version mismatch" in r.getMessage() for r in caplog.records)


async def test_matching_version_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        await check_ncit_version(_FakeClient(version="26.02d"), "26.02d")
    assert not any("version mismatch" in r.getMessage() for r in caplog.records)


async def test_unreachable_store_version_check_is_non_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        await check_ncit_version(_FakeClient(fail=True), "26.02d")  # must not raise
    assert any("unreachable" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------- readiness


@pytest.mark.api
def test_ready_reports_store_version(app_client: TestClient) -> None:
    app: Any = app_client.app
    app.dependency_overrides[get_ncit_client] = lambda: _FakeClient(version="26.02d")
    resp = app_client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["ncit_version"] == "26.02d"


@pytest.mark.api
def test_ready_is_503_when_store_unreachable(app_client: TestClient) -> None:
    app: Any = app_client.app
    app.dependency_overrides[get_ncit_client] = lambda: _FakeClient(fail=True)
    resp = app_client.get("/ready")
    assert resp.status_code == 503
