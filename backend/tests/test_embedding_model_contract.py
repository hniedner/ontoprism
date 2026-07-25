"""Real configured sentence-transformers contract (explicit `full_build` lane)."""

import math

import pytest

from ontolib.repositories.embeddings.generate import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_REVISION,
    EMBED_DIM,
    SentenceTransformerEmbedder,
)

pytestmark = [pytest.mark.integration, pytest.mark.full_build]


def test_pinned_sentence_transformer_returns_one_768_vector_per_text() -> None:
    """Pin the external model/API shape rather than certifying an invented double."""
    embedder = SentenceTransformerEmbedder(DEFAULT_MODEL, DEFAULT_MODEL_REVISION)

    vectors = embedder.encode(["Neoplasm", "Malignant neoplasm"])

    assert len(vectors) == 2
    assert all(len(vector) == EMBED_DIM for vector in vectors)
    assert all(math.isfinite(value) for vector in vectors for value in vector)
    assert all(any(value != 0.0 for value in vector) for vector in vectors)
    first, second = vectors
    cosine = sum(a * b for a, b in zip(first, second, strict=True)) / math.sqrt(
        sum(value * value for value in first) * sum(value * value for value in second)
    )
    assert cosine == pytest.approx(0.90395654, abs=1e-5)
