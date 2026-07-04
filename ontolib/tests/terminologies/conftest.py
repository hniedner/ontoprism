"""Fixtures for terminology-store tests.

Two flavors of "real, no-mock" backing:
- ``ncit_url``: the actual running Oxigraph store (integration tests; skipped if down).
- ``ncit_stub_url``: a local ``http.server`` returning canned NCIt SPARQL-JSON keyed by
  query shape, so the repository assembly logic runs in CI without the live store.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from ontolib.terminologies.namespaces import NCIT_NS as NS

if TYPE_CHECKING:
    from collections.abc import Iterator

_DEFAULT_NCIT_URL = "http://localhost:7888"  # ontoprism's own store (fairdata is 7878)


def _reachable(url: str) -> bool:
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/query",
            content=b"ASK {}",
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=2.0,
        )
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


@pytest.fixture
def ncit_url() -> str:
    """Base URL of the live NCIt Oxigraph store; skip if it is not reachable."""
    url = os.environ.get("NCIT_SPARQL_URL", _DEFAULT_NCIT_URL)
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    return url


# --------------------------------------------------------------------------- stub


def _rows(bindings: list[dict[str, str]]) -> dict[str, Any]:
    cells = [{var: {"value": val} for var, val in b.items()} for b in bindings]
    return {"head": {"vars": []}, "results": {"bindings": cells}}


# (marker in query text, canned bindings) — first match wins; order matters where one
# marker is a prefix of another ("someValuesFrom <" before "someValuesFrom ?target").
_CANNED: list[tuple[str, list[dict[str, str]]]] = [
    (
        "GROUP_CONCAT",
        [
            {
                "label": "Neoplasm",
                "pref": "Neoplasm",
                "def": "A benign or malignant tissue growth.",
                "semtypes": "Neoplastic Process",
                "synonyms": "Neoplasia||Neoplasm",
            }
        ],
    ),
    ("COUNT(DISTINCT ?concept)", [{"count": "2"}]),
    (
        "VALUES ?c",  # labels_for batch query
        [
            {"c": f"{NS}C9305", "label": "Malignant Neoplasm"},
            {"c": f"{NS}C2991", "label": "Disease or Disorder"},
        ],
    ),
    (
        "ORDER BY ?label",
        [
            {
                "concept": f"{NS}C3262",
                "label": "Neoplasm",
                "semtype": "Neoplastic Process",
            },
            {
                "concept": f"{NS}C9305",
                "label": "Malignant Neoplasm",
                "semtype": "Neoplastic Process",
            },
        ],
    ),
    (
        "someValuesFrom <",
        [
            {
                "rel": f"{NS}R105",
                "rellabel": "Disease_Has_Abnormal_Cell",
                "src": f"{NS}C4",
                "slabel": "Some Disease",
            }
        ],
    ),
    (
        "someValuesFrom ?target",
        [
            {
                "rel": f"{NS}R105",
                "rellabel": "Disease_Has_Abnormal_Cell",
                "target": f"{NS}C12922",
                "tlabel": "Neoplastic Cell",
            }
        ],
    ),
    (
        "?rel != rdfs:subClassOf",
        [
            {
                "rel": f"{NS}A8",
                "rellabel": "Concept_In_Subset",
                "target": f"{NS}C116977",
                "tlabel": "Subset",
            }
        ],
    ),
    (
        "?node rdfs:subClassOf <",
        [{"node": f"{NS}C9305", "label": "Malignant Neoplasm"}],
    ),
    ("rdfs:subClassOf ?node", [{"node": f"{NS}C2991", "label": "Disease or Disorder"}]),
]


def canned_ncit_response(query: str) -> dict[str, Any]:
    """Return canned SPARQL-JSON for each NCIt query shape (fixed C3262 data)."""
    for marker, bindings in _CANNED:
        if marker in query:
            return _rows(bindings)
    return _rows([])


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        query = self.rfile.read(length).decode("utf-8")
        body = json.dumps(canned_ncit_response(query)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/sparql-results+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def ncit_stub_url() -> Iterator[str]:
    """A local HTTP server returning canned NCIt SPARQL-JSON (no live store needed)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
