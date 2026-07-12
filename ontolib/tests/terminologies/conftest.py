"""Fixtures for terminology-store tests.

Two flavors of "real, no-mock" backing:
- ``ncit_url``: the actual running Oxigraph store (integration tests; skipped if down).
- ``ncit_stub_url``: a local ``http.server`` returning canned NCIt SPARQL-JSON keyed by
  query shape, so the repository assembly logic runs in CI without the live store.
"""

from __future__ import annotations

import functools
import os
from typing import Any

import httpx
import pytest

from ontolib.terminologies.namespaces import NCIT_NS as NS

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
        "SELECT ?label ?pref ?def",
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
    (
        "ORDER BY ?concept LIMIT 25 OFFSET 0",
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
        "ORDER BY ?concept LIMIT 100 OFFSET 0",
        [
            {
                "concept": f"{NS}C3262",
                "label": "Neoplasm",
                "semtype": "Neoplastic Process",
                "synonyms": "Neoplasia||Neoplasm",
            }
        ],
    ),
    (
        "ORDER BY ?concept LIMIT 200 OFFSET 0",
        [
            {
                "concept": f"{NS}C3262",
                "pref": "Neoplasm",
                "label": "Neoplasm",
                "def": "A benign or malignant tissue growth.",
                "semtype": "Neoplastic Process",
                "synonyms": "Neoplasia | Neoplasm",
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


@pytest.fixture
def ncit_stub_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """Canned NCIt SPARQL-JSON via an in-process ``httpx.MockTransport``.

    No real socket, thread, or port: the transport intercepts requests inside the
    same process and replies with :func:`canned_ncit_response`, keyed by query shape.
    Using a real threaded ``http.server`` here segfaulted xdist fork workers under
    coverage on Linux CI (daemon thread + fork + C tracer); the mock transport removes
    that failure mode entirely while keeping the exact same client code path.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        query = request.content.decode("utf-8")
        return httpx.Response(200, json=canned_ncit_response(query))

    transport = httpx.MockTransport(_handler)
    # OxigraphHttpClient builds ``httpx.AsyncClient(timeout=...)`` internally; inject
    # the mock transport by wrapping the class the module references.
    monkeypatch.setattr(
        "ontolib.terminologies.oxigraph_http_client.httpx.AsyncClient",
        functools.partial(httpx.AsyncClient, transport=transport),
    )
    return "http://ncit.stub"
