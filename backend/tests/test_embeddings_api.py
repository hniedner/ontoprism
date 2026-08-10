"""Semantic-similarity (pgvector embeddings) endpoints, against the live DB.

Configured-store tests require a completed active publication manifest. A non-empty
legacy table is not evidence of completeness: the 4,752-row partial NCIt publication
that motivated #174 had valid 768-dimensional vectors but omitted canonical C3262.
Unreachable Postgres remains an environment gap; absent/mismatched publication evidence
is a genuine failed contract and must not skip.
"""

import asyncio
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker

pytestmark = pytest.mark.full_store


@pytest.mark.integration
def test_expected_embedding_counts_are_release_specific() -> None:
    settings = get_settings()
    assert settings.ncit_expected_version == "26.07d"
    assert settings.ncit_embedding_expected_rows == 212_475
    assert settings.cadsr_embedding_expected_rows == 79_827


def _require_published_corpus(corpus: str, table: str, required_doc_id: str) -> None:
    """Require an active manifest with matching counts and a canonical sentinel."""

    async def _facts() -> tuple[int, int, int, int]:
        engine = make_engine(get_settings().database_url)
        try:
            sf = make_sessionmaker(engine)
            async with sf() as session:
                result = await session.execute(
                    text(
                        f"SELECT manifest.expected_row_count, "  # noqa: S608
                        "manifest.actual_row_count, "
                        f"(SELECT count(*) FROM {table}), "
                        f"(SELECT count(*) FROM {table} "
                        " WHERE doc_id = :required_doc_id) "
                        "FROM embedding_corpus_manifest manifest "
                        "WHERE manifest.corpus = :corpus "
                        "AND manifest.state = 'complete' AND manifest.is_active"
                    ),
                    {"corpus": corpus, "required_doc_id": required_doc_id},
                )
                row = result.one_or_none()
                assert row is not None, (
                    f"{corpus} has no completed active embedding manifest; existing "
                    "rows are uncertified and must be rebuilt with "
                    "`pdm run data-build embeddings --publish`"
                )
                return tuple(int(value) for value in row)  # type: ignore[return-value]
        finally:
            await dispose_engine(engine)

    try:
        expected, actual, physical, sentinel = asyncio.run(_facts())
    except (OSError, OperationalError, InterfaceError):
        pytest.skip("Embedding DB (pgvector) not reachable")
        return
    assert expected == actual == physical
    assert sentinel == 1, f"{corpus} active corpus lacks required {required_doc_id}"


def _similar(client: TestClient, path: str) -> list[dict]:
    """Call *path*. Every caller has already established that vectors are loaded.

    So there is no 503 skip here: the endpoint maps *any* ``SQLAlchemyError`` to 503
    (`api/v1/ncit.py`), which means a dropped HNSW index, a renamed column or a broken
    cosine cast would all arrive as 503. Skipping on that would turn precisely the
    regression these tests exist to catch into a green run.
    """
    resp = client.get(path)
    assert resp.status_code == HTTPStatus.OK, (
        f"{path} returned {resp.status_code} with embeddings loaded -- the endpoint "
        f"maps any SQLAlchemyError to 503, so this is a bug, not a missing corpus: "
        f"{resp.text[:300]}"
    )
    return resp.json()


@pytest.mark.integration
@pytest.mark.full_build
def test_similar_concepts_are_semantically_related(live_api_client: TestClient) -> None:
    _require_published_corpus("ncit", "ncit_concepts", "C3262")
    hits = _similar(live_api_client, "/api/v1/ncit/concepts/C3262/similar?limit=5")
    codes = {h["code"] for h in hits}
    # C9305 = Malignant Neoplasm — the nearest neighbor of C3262 (Neoplasm).
    assert "C9305" in codes
    assert all(0.0 <= h["score"] <= 1.0 for h in hits)
    assert all(h["code"] != "C3262" for h in hits)  # excludes itself


@pytest.mark.integration
@pytest.mark.full_build
def test_similar_concepts_have_labels(live_api_client: TestClient) -> None:
    _require_published_corpus("ncit", "ncit_concepts", "C3262")
    hits = _similar(live_api_client, "/api/v1/ncit/concepts/C3262/similar?limit=3")
    assert any(h["label"] for h in hits)


@pytest.mark.integration
@pytest.mark.full_build
def test_similar_cdes_return_scored_summaries(live_api_client: TestClient) -> None:
    _require_published_corpus("cadsr", "cde_repository", "2517527:4")
    hits = _similar(live_api_client, "/api/v1/cadsr/cdes/2517527/similar?limit=3")
    assert hits
    assert all(h["long_name"] and 0.0 <= h["score"] <= 1.0 for h in hits)
