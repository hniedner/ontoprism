"""Backend test fixtures."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import get_cadsr_repo
from backend.main import create_app
from ontolib.repositories.cadsr.repository import CdeRepository


def _store_reachable(url: str) -> bool:
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
def app_client() -> Iterator[TestClient]:
    """TestClient with lifespan active (client/store wired); no live store needed."""
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def live_api_client() -> Iterator[TestClient]:
    """A TestClient wired to the live NCIt store; skips if the store is unreachable."""
    url = get_settings().ncit_sparql_url
    if not _store_reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    with TestClient(create_app()) as client:
        yield client


def _build_cadsr_db(path: Path) -> None:
    """Create a small real caDSR SQLite DB (mirrors the fairdata-built schema)."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE cdes (public_id TEXT, version TEXT, short_name TEXT, "
            "long_name TEXT, definition TEXT, context TEXT, workflow_status TEXT, "
            "registration_status TEXT, datatype TEXT, value_domain_type TEXT, "
            "search_text TEXT, cde_json TEXT, PRIMARY KEY (public_id, version));"
            "CREATE TABLE cde_concepts (concept_code TEXT, concept_name TEXT, "
            "public_id TEXT, version TEXT, concept_type TEXT, is_primary INTEGER, "
            "hierarchy_depth INTEGER, is_leaf INTEGER);"
        )
        payload = {
            "public_id": "100",
            "version": "2.0",
            "short_name": "NEOPLASM_HIST",
            "long_name": "Neoplasm Histology",
            "definition": "Histology of a neoplasm.",
            "context": "caDSR",
            "workflow_status": "RELEASED",
            "registration_status": "Standard",
            "datatype": "CHARACTER",
            "value_domain_type": "Enumerated",
            "permissible_values": [{"value": "Carcinoma", "meaning_code": "C2916"}],
        }
        conn.execute(
            "INSERT INTO cdes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "100",
                "2.0",
                "NEOPLASM_HIST",
                "Neoplasm Histology",
                "Histology of a neoplasm.",
                "caDSR",
                "RELEASED",
                "Standard",
                "CHARACTER",
                "Enumerated",
                "NEOPLASM_HIST Neoplasm Histology",
                json.dumps(payload),
            ),
        )
        conn.execute(
            "INSERT INTO cde_concepts (concept_code, concept_name, "
            "public_id, version, concept_type, is_primary) VALUES (?,?,?,?,?,?)",
            ("C3262", "Neoplasm", "100", "2.0", "object_class", 1),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def cadsr_client(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient with the caDSR repo pointed at a fresh temp DB (via override)."""
    db = tmp_path / "cde_repository.db"
    _build_cadsr_db(db)
    app = create_app()
    app.dependency_overrides[get_cadsr_repo] = lambda: CdeRepository(db)
    with TestClient(app) as client:
        yield client
