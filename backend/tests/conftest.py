"""Backend test fixtures."""

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import get_cadsr_repo, get_ncit_store, get_repository_metadata
from backend.main import create_app
from backend.repository_metadata import NcitRepositoryReady, RepositoryUnhealthy
from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.terminologies.ncit.sibling_store import CandidateObservation


class _IsolatedRepositoryMetadata:
    """Certified identity seam for run-owned disposable repository fixtures."""

    async def ncit(self) -> NcitRepositoryReady:
        return NcitRepositoryReady(
            source_identity="f" * 64,
            manifest_identity="e" * 64,
            release="26.07d",
            activated_at=datetime(2026, 8, 10, tzinfo=UTC),
            observation=CandidateObservation(
                default_triples=1,
                stated_triples=1,
                named_graphs=(),
                default_version="26.07d",
                stated_version="26.07d",
                restriction_count=1,
                has_required_restriction=True,
                default_has_stated_only_sentinel=False,
                stated_has_stated_only_sentinel=True,
            ),
        )

    def cadsr(self) -> RepositoryUnhealthy:
        return RepositoryUnhealthy(
            repository="cadsr",
            reason="manifest-missing",
            message="disposable API fixture has no caDSR certification",
        )


class _EmptyNcitStore:
    async def get_concept_detail(self, _code: str) -> None:
        return None


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
    app = create_app()
    app.dependency_overrides[get_repository_metadata] = _IsolatedRepositoryMetadata
    app.dependency_overrides[get_ncit_store] = _EmptyNcitStore
    with TestClient(app) as client:
        yield client


@pytest.fixture
def live_api_client() -> Iterator[TestClient]:
    """A TestClient wired to the live NCIt store; skips if the store is unreachable."""
    url = get_settings().ncit_sparql_url
    if not _store_reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def isolated_api_client(
    isolated_postgres_settings: None,
    isolated_qlever_settings: None,
) -> Iterator[TestClient]:
    """API client whose persistent services are current-run-owned disposables."""
    app = create_app()
    app.dependency_overrides[get_repository_metadata] = _IsolatedRepositoryMetadata
    with TestClient(app) as client:
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


@pytest.fixture
def isolated_cadsr_client(
    tmp_path: Path,
    isolated_qlever_settings: None,
) -> Iterator[TestClient]:
    """Temporary caDSR repository joined to the disposable NCIt service."""
    db = tmp_path / "cde_repository.db"
    _build_cadsr_db(db)
    app = create_app()
    app.dependency_overrides[get_cadsr_repo] = lambda: CdeRepository(db)
    with TestClient(app) as client:
        yield client
