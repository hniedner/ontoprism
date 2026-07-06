"""Unit tests for the embedding text-builders and the generation pipeline.

The text-builders are pure; the pipeline tests drive ``generate_cde_embeddings`` /
``generate_ncit_embeddings`` with a stub embedder and a fake async session factory
(no real ML model, no real pgvector), asserting the upserted doc_ids, vector
literals, metadata, batching and paging behaviour.
"""

import importlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ontolib.repositories.embeddings.generate import (
    SentenceTransformerEmbedder,
    _upsert_batch,
    cde_text,
    generate_cde_embeddings,
    generate_ncit_embeddings,
    ncit_text,
)


class _StubEmbedder:
    """Deterministic 3-dim encoder: each text -> [len(text)]*3."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[float(len(t))] * 3 for t in texts]


class _FakeBegin:
    async def __aenter__(self) -> "_FakeBegin":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeSession:
    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = batches

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def begin(self) -> _FakeBegin:
        return _FakeBegin()

    async def execute(self, _sql: Any, params: list[dict[str, Any]]) -> None:
        self._batches.append(params)


class _FakeSessionFactory:
    """Captures each upserted batch of parameter dicts."""

    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []

    def __call__(self) -> _FakeSession:
        return _FakeSession(self.batches)


class _FakeNcitStore:
    def __init__(self, records: list[dict[str, str | None]]) -> None:
        self._records = records
        self.pages: list[tuple[int, int]] = []

    async def embedding_records(
        self, *, limit: int, offset: int
    ) -> list[dict[str, str | None]]:
        self.pages.append((limit, offset))
        return self._records[offset : offset + limit]


def _make_cde_db(path: Path, rows: list[dict[str, str | None]]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE cdes (public_id TEXT, version TEXT, search_text TEXT, "
            "short_name TEXT, long_name TEXT, definition TEXT, context TEXT, "
            "workflow_status TEXT, registration_status TEXT)"
        )
        for r in rows:
            conn.execute(
                "INSERT INTO cdes (public_id, version, search_text, short_name, "
                "long_name, definition, context, workflow_status, "
                "registration_status) VALUES (:public_id,:version,:search_text,"
                ":short_name,:long_name,:definition,:context,:workflow_status,"
                ":registration_status)",
                r,
            )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.unit
def test_ncit_text_orders_and_caps_parts() -> None:
    text = ncit_text(
        "Neoplasm",
        [f"syn{i}" for i in range(8)],
        "A tissue growth." * 60,  # long definition → truncated to 500 chars
        "Neoplastic Process",
    )
    parts = text.split(" | ")
    assert parts[0] == "Neoplasm"
    # Only the first 5 synonyms are included.
    assert parts[1:6] == ["syn0", "syn1", "syn2", "syn3", "syn4"]
    assert "syn5" not in parts
    # Definition truncated to 500 chars; semantic type last.
    assert len(parts[6]) == 500
    assert parts[-1] == "Neoplastic Process"


@pytest.mark.unit
def test_ncit_text_omits_empty_optionals() -> None:
    assert ncit_text("Just A Name", [], None, None) == "Just A Name"


@pytest.mark.unit
def test_cde_text_prefers_search_text() -> None:
    assert (
        cde_text("precomputed search", "SN", "Long Name", "def") == "precomputed search"
    )


@pytest.mark.unit
def test_cde_text_falls_back_to_core_fields() -> None:
    assert (
        cde_text(None, "SN", "Long Name", "A definition.")
        == "SN | Long Name | A definition."
    )
    assert cde_text("", "SN", "", "") == "SN"


def _cde_row(public_id: str, version: str, **over: str | None) -> dict[str, str | None]:
    base: dict[str, str | None] = {
        "public_id": public_id,
        "version": version,
        "search_text": None,
        "short_name": f"SN{public_id}",
        "long_name": f"Long {public_id}",
        "definition": "A definition.",
        "context": "caDSR",
        "workflow_status": "RELEASED",
        "registration_status": "Standard",
    }
    base.update(over)
    return base


@pytest.mark.unit
async def test_generate_cde_embeddings_upserts_rows(tmp_path: Path) -> None:
    db = tmp_path / "cde.db"
    _make_cde_db(db, [_cde_row("100", "2.0", search_text="precomputed 100")])
    embedder = _StubEmbedder()
    sf = _FakeSessionFactory()

    count = await generate_cde_embeddings(str(db), embedder, sf)  # type: ignore[arg-type]

    assert count == 1
    # The embedder saw the CDE's search_text (its precomputed embedding text).
    assert embedder.seen == ["precomputed 100"]
    params = sf.batches[0][0]
    assert params["doc_id"] == "100:2.0"
    # 3-dim vector literal built from len("precomputed 100") == 15.
    assert params["embedding"] == "[15.0,15.0,15.0]"
    meta = json.loads(params["metadata"])
    assert meta["public_id"] == "100"
    assert meta["version"] == "2.0"
    assert meta["registration_status"] == "Standard"


@pytest.mark.unit
async def test_generate_cde_embeddings_batches_by_size(tmp_path: Path) -> None:
    db = tmp_path / "cde.db"
    _make_cde_db(db, [_cde_row("100", "2.0"), _cde_row("200", "1.0")])
    sf = _FakeSessionFactory()

    count = await generate_cde_embeddings(str(db), _StubEmbedder(), sf, batch_size=1)  # type: ignore[arg-type]

    assert count == 2
    # batch_size=1 flushes each CDE in its own upsert.
    assert [len(b) for b in sf.batches] == [1, 1]
    doc_ids = {b[0]["doc_id"] for b in sf.batches}
    assert doc_ids == {"100:2.0", "200:1.0"}


@pytest.mark.unit
async def test_generate_cde_embeddings_empty_db_is_noop(tmp_path: Path) -> None:
    db = tmp_path / "cde.db"
    _make_cde_db(db, [])
    sf = _FakeSessionFactory()

    count = await generate_cde_embeddings(str(db), _StubEmbedder(), sf)  # type: ignore[arg-type]

    assert count == 0
    assert sf.batches == []


@pytest.mark.unit
async def test_generate_ncit_embeddings_pages_until_exhausted() -> None:
    records: list[dict[str, str | None]] = [
        {
            "code": f"C{i}",
            "preferred_name": f"Concept {i}",
            "synonyms": "alt a | alt b",
            "definition": "Some definition.",
            "semantic_type": "Neoplastic Process",
        }
        for i in range(3)
    ]
    store = _FakeNcitStore(records)
    embedder = _StubEmbedder()
    sf = _FakeSessionFactory()

    count = await generate_ncit_embeddings(store, embedder, sf, batch_size=2)  # type: ignore[arg-type]

    assert count == 3
    # Paged 0,2 then 4 (empty) -> break.
    assert store.pages == [(2, 0), (2, 2), (2, 4)]
    # Two non-empty batches were upserted (sizes 2 and 1).
    assert [len(b) for b in sf.batches] == [2, 1]
    upserted_codes = {row["doc_id"] for batch in sf.batches for row in batch}
    assert upserted_codes == {"C0", "C1", "C2"}


@pytest.mark.unit
async def test_generate_ncit_uses_code_as_name_fallback() -> None:
    store = _FakeNcitStore(
        [
            {
                "code": "C999",
                "preferred_name": None,
                "synonyms": None,
                "definition": None,
                "semantic_type": None,
            }
        ]
    )
    embedder = _StubEmbedder()
    sf = _FakeSessionFactory()

    count = await generate_ncit_embeddings(store, embedder, sf, batch_size=10)  # type: ignore[arg-type]

    assert count == 1
    # With no preferred_name/synonyms/definition, the embedding text is just the code.
    assert embedder.seen == ["C999"]
    assert json.loads(sf.batches[0][0]["metadata"])["preferred_name"] == ""


@pytest.mark.unit
async def test_upsert_batch_empty_is_noop() -> None:
    sf = _FakeSessionFactory()
    await _upsert_batch(sf, "ncit_concepts", [])  # type: ignore[arg-type]
    assert sf.batches == []


class _FakeVector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _FakeModel:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def encode(self, texts: list[str]) -> list[_FakeVector]:
        return [_FakeVector([float(len(t))]) for t in texts]


@pytest.mark.unit
def test_sentence_transformer_embedder_lazy_imports_and_adapts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real embedder lazily imports the optional dep and adapts numpy .tolist()."""
    fake_module = SimpleNamespace(SentenceTransformer=_FakeModel)
    original = importlib.import_module

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "sentence_transformers":
            return fake_module
        return original(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(importlib, "import_module", fake_import)

    embedder = SentenceTransformerEmbedder("my-model")
    vectors = embedder.encode(["abcd", "xy"])

    # Each numpy-like vector was converted to a plain list via .tolist().
    assert vectors == [[4.0], [2.0]]
