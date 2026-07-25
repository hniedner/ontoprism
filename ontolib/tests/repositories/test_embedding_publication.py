"""Pure invariant tests for embedding publication manifests."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ontolib.repositories.embeddings.publication import (
    Corpus,
    CorpusBuild,
    CorpusManifest,
)


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


def _manifest(state: str, **overrides: object) -> CorpusManifest:
    values: dict[str, object] = {
        "build_id": UUID("00000000-0000-0000-0000-000000000001"),
        "corpus": Corpus.NCIT,
        "state": state,
        "is_active": False,
        "source_version": "26.02d",
        "source_hash": "a" * 64,
        "model_id": "model",
        "model_revision": "d" * 40,
        "vector_dimension": 768,
        "expected_row_count": 2,
        "actual_row_count": None,
        "code_commit": "b" * 40,
        "required_doc_ids": ("C3262",),
        "error_message": None,
        "created_at": datetime.now(UTC),
        "completed_at": None,
    }
    values.update(overrides)
    return CorpusManifest(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_manifest_accepts_each_valid_lifecycle_state() -> None:
    assert _manifest("building").state == "building"
    assert _manifest("failed", error_message="boom").state == "failed"
    complete = _manifest(
        "complete",
        is_active=True,
        actual_row_count=2,
        completed_at=datetime.now(UTC),
    )
    assert complete.is_active


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "overrides"),
    [
        ("building", {"is_active": True}),
        ("building", {"actual_row_count": 1}),
        ("failed", {"error_message": ""}),
        ("failed", {"error_message": "boom", "actual_row_count": 1}),
        ("complete", {"actual_row_count": 1, "completed_at": datetime.now(UTC)}),
        ("complete", {"actual_row_count": 2}),
        (
            "complete",
            {
                "actual_row_count": 2,
                "completed_at": datetime.now(UTC),
                "error_message": "boom",
            },
        ),
    ],
)
def test_manifest_rejects_contradictory_lifecycle_evidence(
    state: str, overrides: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="manifest"):
        _manifest(state, **overrides)
