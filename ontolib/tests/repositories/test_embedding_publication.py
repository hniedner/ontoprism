"""Pure invariant tests for embedding publication manifests."""

from dataclasses import replace
from uuid import UUID

import pytest

from ontolib.repositories.embeddings.publication import Corpus, CorpusBuild


def _build() -> CorpusBuild:
    return CorpusBuild(
        build_id=UUID("00000000-0000-0000-0000-000000000001"),
        corpus=Corpus.NCIT,
        source_version="26.02d",
        source_hash="a" * 64,
        model_id="sentence-transformers/all-mpnet-base-v2",
        model_revision="b" * 40,
        vector_dimension=768,
        expected_row_count=2,
        code_commit="c" * 40,
        required_doc_ids=("C3262",),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_version", "", "provenance"),
        ("source_hash", " ", "provenance"),
        ("model_id", "", "provenance"),
        ("model_revision", "", "provenance"),
        ("code_commit", "", "provenance"),
        ("vector_dimension", 3, r"vector\(768\)"),
        ("expected_row_count", 0, "positive"),
        ("required_doc_ids", (), "non-empty sentinels"),
        ("required_doc_ids", ("",), "non-empty sentinels"),
        ("required_doc_ids", ("C3262", "C3262"), "unique"),
    ],
)
def test_corpus_build_rejects_unpublishable_contracts(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_build(), **{field: value})


@pytest.mark.unit
def test_corpus_build_is_immutable() -> None:
    build = _build()

    with pytest.raises(AttributeError):
        build.source_version = "changed"  # type: ignore[misc]
