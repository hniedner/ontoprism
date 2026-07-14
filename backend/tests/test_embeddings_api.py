"""Semantic-similarity (pgvector embeddings) endpoints, against the live DB.

**Two different absences, which must not be conflated.** The endpoint returns 503 when
pgvector is unreachable -- but when the DB is up and simply has *no embeddings loaded*
it returns ``200 []``, because a concept with no vector has no neighbours. Asserting
into that empty list produced a hard failure (``assert "C9305" in set()``) that read as
a broken endpoint and was really an unbuilt corpus: these are ``full_build`` tests, and
the embeddings step needs ``pdm install -G data-build`` plus a real embed run.

So :func:`_require_embeddings` separates them. **No rows at all** is an environment gap
and skips with an actionable message; rows present but *this* concept returning nothing
is a genuine failure and is left to fail. A blanket "skip when empty" would hide exactly
the regression these tests exist to catch.
"""

import asyncio
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker


def _embedding_rows(table: str) -> int:
    """Vectors actually loaded in *table*; -1 if the DB cannot be reached at all."""

    async def _count() -> int:
        engine = make_engine(get_settings().database_url)
        try:
            sf = make_sessionmaker(engine)
            async with sf() as session:
                # `table` is a fixed internal identifier, never user input.
                result = await session.execute(
                    text(f"SELECT count(*) FROM {table}")  # noqa: S608
                )
                return int(result.scalar_one())
        finally:
            await dispose_engine(engine)

    try:
        return asyncio.run(_count())
    except Exception:
        return -1


def _require_embeddings(table: str) -> None:
    rows = _embedding_rows(table)
    if rows < 0:
        pytest.skip("Embedding DB (pgvector) not reachable")
    if rows == 0:
        pytest.skip(
            f"no embeddings loaded in `{table}` -- these are full_build tests. Run "
            "`pdm install -G data-build`, then `pdm run data-build embeddings`."
        )


def _similar(client: TestClient, path: str) -> list[dict]:
    resp = client.get(path)
    if resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
        pytest.skip("Embedding DB (pgvector) not available")
    assert resp.status_code == HTTPStatus.OK
    return resp.json()


@pytest.mark.integration
@pytest.mark.full_build
def test_similar_concepts_are_semantically_related(live_api_client: TestClient) -> None:
    _require_embeddings("ncit_concepts")
    hits = _similar(live_api_client, "/api/v1/ncit/concepts/C3262/similar?limit=5")
    codes = {h["code"] for h in hits}
    # C9305 = Malignant Neoplasm — the nearest neighbor of C3262 (Neoplasm).
    assert "C9305" in codes
    assert all(0.0 <= h["score"] <= 1.0 for h in hits)
    assert all(h["code"] != "C3262" for h in hits)  # excludes itself


@pytest.mark.integration
@pytest.mark.full_build
def test_similar_concepts_have_labels(live_api_client: TestClient) -> None:
    _require_embeddings("ncit_concepts")
    hits = _similar(live_api_client, "/api/v1/ncit/concepts/C3262/similar?limit=3")
    assert any(h["label"] for h in hits)


@pytest.mark.integration
@pytest.mark.full_build
def test_similar_cdes_return_scored_summaries(live_api_client: TestClient) -> None:
    _require_embeddings("cde_repository")
    hits = _similar(live_api_client, "/api/v1/cadsr/cdes/2517527/similar?limit=3")
    assert hits
    assert all(h["long_name"] and 0.0 <= h["score"] <= 1.0 for h in hits)
