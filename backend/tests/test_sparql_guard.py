"""Security contracts keep caller-supplied SPARQL out of the public API."""

import ast
from pathlib import Path
from typing import Never

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.dependencies import get_ncit_client
from backend.main import create_app

_APP_OPERATIONS = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/api/v1/ncit/search"),
    ("GET", "/api/v1/ncit/list"),
    ("GET", "/api/v1/ncit/concepts/{code}"),
    ("GET", "/api/v1/ncit/concepts/{code}/similar"),
    ("GET", "/api/v1/ncit/concepts/{code}/neighborhood"),
    ("GET", "/api/v1/ncit/concepts/{code}/mappings"),
    ("GET", "/api/v1/ncit/concepts/{code}/decomposition"),
    ("GET", "/api/v1/uberon/search"),
    ("GET", "/api/v1/uberon/list"),
    ("GET", "/api/v1/uberon/concepts/{code}"),
    ("GET", "/api/v1/uberon/concepts/{code}/neighborhood"),
    ("POST", "/api/v1/mappings/$translate"),
    ("GET", "/api/v1/cadsr/search"),
    ("GET", "/api/v1/cadsr/list"),
    ("GET", "/api/v1/cadsr/cdes/{public_id}"),
    ("GET", "/api/v1/cadsr/cdes/{public_id}/similar"),
    ("GET", "/api/v1/cadsr/concepts/{concept_code}/cdes"),
    ("GET", "/api/v1/cadsr/cdes/{public_id}/neighborhood"),
    ("POST", "/api/v1/refresh"),
    ("POST", "/api/v1/refresh/ncit/reload"),
    ("POST", "/api/v1/refresh/ncit/download"),
    ("POST", "/api/v1/refresh/cadsr/download"),
    ("POST", "/api/v1/refresh/ncit/search-index"),
    ("POST", "/api/v1/refresh/uberon/search-index"),
    ("POST", "/api/v1/clinicaltrials/search"),
    ("GET", "/api/v1/clinicaltrials/{nct_id}"),
    ("POST", "/api/v1/pubmed/search"),
    ("GET", "/api/v1/pubmed/{pmid}"),
    ("GET", "/api/v1/pubmed/{pmid}/related"),
    ("GET", "/api/v1/decomposition/runs"),
    ("GET", "/api/v1/decomposition/runs/{run_id}"),
    ("GET", "/api/v1/decomposition/runs/{run_id}/outcomes"),
    ("GET", "/api/v1/decomposition/minted-concepts"),
    ("GET", "/api/v1/decomposition/axes"),
}


@pytest.mark.security
def test_raw_sparql_routes_are_absent_without_resolving_route_dependencies() -> None:
    app = create_app()

    def _reject_client_resolution() -> Never:
        raise AssertionError("raw route resolved the NCIt transport")

    app.dependency_overrides[get_ncit_client] = _reject_client_resolution
    with TestClient(app) as client:
        for path in ("/api/v1/sparql", "/api/v1/sparql/"):
            response = client.post(
                path,
                json={"query": "SELECT * WHERE { ?s ?p ?o }"},
                follow_redirects=False,
            )
            assert response.status_code == 404


@pytest.mark.security
def test_raw_sparql_contract_and_endpoint_bound_settings_are_absent() -> None:
    schema = create_app().openapi()
    operations = {
        (method.upper(), path)
        for path, path_operations in schema["paths"].items()
        for method in path_operations
    }

    assert operations == _APP_OPERATIONS
    assert "/api/v1/sparql" not in schema["paths"]
    assert "SparqlRequest" not in schema["components"]["schemas"]
    assert "SparqlResponse" not in schema["components"]["schemas"]
    assert "sparql_row_cap" not in Settings.model_fields
    assert "sparql_timeout_sec" not in Settings.model_fields


@pytest.mark.security
def test_api_routers_do_not_access_generic_query_transport() -> None:
    api_root = Path(__file__).parents[1] / "src" / "backend" / "api"
    forbidden_access: list[str] = []
    generic_query_methods = {"ask", "select", "select_once", "select_raw"}
    low_level_dependencies = {"NcitClient", "get_ncit_client", "SparqlHttpClient"}

    for path in api_root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in generic_query_methods:
                location = f"{path.relative_to(api_root)}:{node.lineno}"
                forbidden_access.append(f"generic query access at {location}")
            if isinstance(node, ast.Attribute) and node.attr in low_level_dependencies:
                location = f"{path.relative_to(api_root)}:{node.lineno}"
                forbidden_access.append(f"low-level dependency access at {location}")
            if isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    if imported.name in low_level_dependencies:
                        location = f"{path.relative_to(api_root)}:{node.lineno}"
                        forbidden_access.append(
                            f"low-level dependency {imported.name} at {location}"
                        )

    assert forbidden_access == []
