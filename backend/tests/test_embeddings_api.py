"""Semantic-similarity (pgvector embeddings) endpoints, against the live DB.

Skipped when Postgres/embeddings are unavailable (endpoint returns 503).
"""

import pytest
from fastapi.testclient import TestClient


def _similar(client: TestClient, path: str) -> list[dict]:
    resp = client.get(path)
    if resp.status_code == 503:
        pytest.skip("Embedding DB (pgvector) not available")
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.integration
def test_similar_concepts_are_semantically_related(live_api_client: TestClient) -> None:
    hits = _similar(live_api_client, "/api/v1/ncit/concepts/C3262/similar?limit=5")
    codes = {h["code"] for h in hits}
    # C9305 = Malignant Neoplasm — the nearest neighbor of C3262 (Neoplasm).
    assert "C9305" in codes
    assert all(0.0 <= h["score"] <= 1.0 for h in hits)
    assert all(h["code"] != "C3262" for h in hits)  # excludes itself


@pytest.mark.integration
def test_similar_concepts_have_labels(live_api_client: TestClient) -> None:
    hits = _similar(live_api_client, "/api/v1/ncit/concepts/C3262/similar?limit=3")
    assert any(h["label"] for h in hits)


@pytest.mark.integration
def test_similar_cdes_return_scored_summaries(live_api_client: TestClient) -> None:
    hits = _similar(live_api_client, "/api/v1/cadsr/cdes/2517527/similar?limit=3")
    assert hits
    assert all(h["long_name"] and 0.0 <= h["score"] <= 1.0 for h in hits)
