"""Behavioral tests for the PubMed endpoints (local E-utilities HTTP double)."""

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

_ESEARCH = {"esearchresult": {"count": "1", "idlist": ["111"]}}
_ESUMMARY = {
    "result": {
        "uids": ["111"],
        "111": {"uid": "111", "title": "Widgetinib in melanoma", "authors": []},
    }
}
_EFETCH = (
    '<?xml version="1.0"?><PubmedArticleSet><PubmedArticle><MedlineCitation>'
    "<PMID>111</PMID><Article><ArticleTitle>Widgetinib in melanoma</ArticleTitle>"
    "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
)
_EFETCH_EMPTY = '<?xml version="1.0"?><PubmedArticleSet></PubmedArticleSet>'
_ELINK = {
    "linksets": [{"linksetdbs": [{"linkname": "pubmed_pubmed", "links": ["222"]}]}]
}


class _Handler(BaseHTTPRequestHandler):
    fail_status: ClassVar[int | None] = None

    def do_GET(self) -> None:
        if _Handler.fail_status is not None:
            self.send_response(_Handler.fail_status)
            self.end_headers()
            return
        path = urlparse(self.path).path
        if path.endswith("/esearch.fcgi"):
            self._json(_ESEARCH)
        elif path.endswith("/esummary.fcgi"):
            self._json(_ESUMMARY)
        elif path.endswith("/efetch.fcgi"):
            self._xml(_EFETCH if "id=111" in self.path else _EFETCH_EMPTY)
        elif path.endswith("/elink.fcgi"):
            self._json(_ELINK)
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, payload: dict[str, Any]) -> None:
        self._send(json.dumps(payload).encode(), "application/json")

    def _xml(self, text: str) -> None:
        self._send(text.encode(), "application/xml")

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.fixture
def pm_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    _Handler.fail_status = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    monkeypatch.setenv("PUBMED_API_URL", f"http://{host}:{port}")
    monkeypatch.setenv("PUBMED_REQUESTS_PER_SECOND", "0")  # no throttling in tests
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        srv.shutdown()
        srv.server_close()
        get_settings.cache_clear()


@pytest.mark.api
def test_search_returns_summaries(pm_app: TestClient) -> None:
    resp = pm_app.post("/api/v1/pubmed/search", json={"query": "melanoma"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["articles"][0]["pmid"] == "111"


@pytest.mark.api
def test_search_requires_query(pm_app: TestClient) -> None:
    resp = pm_app.post("/api/v1/pubmed/search", json={"query": ""})
    assert resp.status_code == 422


@pytest.mark.api
def test_search_upstream_failure_is_502(pm_app: TestClient) -> None:
    _Handler.fail_status = 500
    resp = pm_app.post("/api/v1/pubmed/search", json={"query": "melanoma"})
    assert resp.status_code == 502


@pytest.mark.api
def test_article_detail_returns_article(pm_app: TestClient) -> None:
    resp = pm_app.get("/api/v1/pubmed/111")
    assert resp.status_code == 200
    assert resp.json()["pmid"] == "111"


@pytest.mark.api
def test_article_detail_unknown_is_404(pm_app: TestClient) -> None:
    resp = pm_app.get("/api/v1/pubmed/999999")
    assert resp.status_code == 404


@pytest.mark.api
def test_article_detail_malformed_pmid_is_400(pm_app: TestClient) -> None:
    resp = pm_app.get("/api/v1/pubmed/not-a-number")
    assert resp.status_code == 400


@pytest.mark.api
def test_related_returns_pmids(pm_app: TestClient) -> None:
    resp = pm_app.get("/api/v1/pubmed/111/related?link_type=similar")
    assert resp.status_code == 200
    assert resp.json()["related_pmids"] == ["222"]


@pytest.mark.api
def test_related_invalid_link_type_is_422(pm_app: TestClient) -> None:
    resp = pm_app.get("/api/v1/pubmed/111/related?link_type=bogus")
    assert resp.status_code == 422


@pytest.mark.api
def test_article_detail_upstream_failure_is_502(pm_app: TestClient) -> None:
    _Handler.fail_status = 500
    resp = pm_app.get("/api/v1/pubmed/111")
    assert resp.status_code == 502


@pytest.mark.api
def test_related_upstream_failure_is_502(pm_app: TestClient) -> None:
    _Handler.fail_status = 500
    resp = pm_app.get("/api/v1/pubmed/111/related?link_type=similar")
    assert resp.status_code == 502
