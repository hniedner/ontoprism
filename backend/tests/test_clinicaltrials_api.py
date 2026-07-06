"""Behavioral tests for the ClinicalTrials.gov endpoints (local HTTP double)."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app

_STUDY = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT01234567", "briefTitle": "Trial One"},
        "statusModule": {"overallStatus": "RECRUITING"},
        "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 50}},
        "conditionsModule": {"conditions": ["Melanoma"]},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": "Widgetinib"}]
        },
    }
}


class _Handler(BaseHTTPRequestHandler):
    # When set, every response uses this status (drives the upstream-failure path).
    fail_status: ClassVar[int | None] = None

    def do_GET(self) -> None:
        if _Handler.fail_status is not None:
            self.send_response(_Handler.fail_status)
            self.end_headers()
            return
        path = urlparse(self.path).path
        if path == "/studies":
            self._json({"studies": [_STUDY], "totalCount": 1})
        elif path == "/studies/NCT01234567":
            self._json(_STUDY)
        elif path == "/studies/NCT00000000":
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(400)
            self.end_headers()

    def _json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.fixture
def ct_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    _Handler.fail_status = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    monkeypatch.setenv("CLINICALTRIALS_API_URL", f"http://{host}:{port}")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        srv.shutdown()
        srv.server_close()
        get_settings.cache_clear()


@pytest.mark.api
def test_search_returns_parsed_trials(ct_app: TestClient) -> None:
    resp = ct_app.post("/api/v1/clinicaltrials/search", json={"condition": "melanoma"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["studies"][0]["nct_id"] == "NCT01234567"
    assert body["studies"][0]["interventions"] == ["Widgetinib"]


@pytest.mark.api
def test_search_requires_a_query_field(ct_app: TestClient) -> None:
    resp = ct_app.post("/api/v1/clinicaltrials/search", json={"limit": 10})
    assert resp.status_code == 422


@pytest.mark.api
def test_search_invalid_status_is_400(ct_app: TestClient) -> None:
    resp = ct_app.post(
        "/api/v1/clinicaltrials/search",
        json={"condition": "melanoma", "status": "BOGUS"},
    )
    assert resp.status_code == 400


@pytest.mark.api
def test_search_invalid_phase_is_400(ct_app: TestClient) -> None:
    resp = ct_app.post(
        "/api/v1/clinicaltrials/search",
        json={"condition": "melanoma", "phase": "PHASE9"},
    )
    assert resp.status_code == 400


@pytest.mark.api
def test_search_limit_out_of_bounds_is_422(ct_app: TestClient) -> None:
    resp = ct_app.post(
        "/api/v1/clinicaltrials/search", json={"condition": "melanoma", "limit": 500}
    )
    assert resp.status_code == 422


@pytest.mark.api
def test_search_upstream_failure_is_502(ct_app: TestClient) -> None:
    # An upstream 5xx from ClinicalTrials.gov surfaces as a clean 502, not a 500.
    _Handler.fail_status = 500
    resp = ct_app.post("/api/v1/clinicaltrials/search", json={"condition": "melanoma"})
    assert resp.status_code == 502


@pytest.mark.api
def test_trial_detail_returns_study(ct_app: TestClient) -> None:
    resp = ct_app.get("/api/v1/clinicaltrials/NCT01234567")
    assert resp.status_code == 200
    assert resp.json()["nct_id"] == "NCT01234567"


@pytest.mark.api
def test_trial_detail_unknown_is_404(ct_app: TestClient) -> None:
    resp = ct_app.get("/api/v1/clinicaltrials/NCT00000000")
    assert resp.status_code == 404


@pytest.mark.api
def test_trial_detail_malformed_id_is_400(ct_app: TestClient) -> None:
    resp = ct_app.get("/api/v1/clinicaltrials/NCT123")
    assert resp.status_code == 400
