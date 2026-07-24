"""Unit tests for embedding text, deterministic source staging, and lifecycle glue."""

import importlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ontolib.repositories.embeddings.generate import (
    NcitEmbeddingRecord,
    SentenceTransformerEmbedder,
    cde_text,
    generate_cde_embeddings,
    generate_ncit_embeddings,
    ncit_source_fingerprint,
    ncit_text,
    stage_cde_embeddings,
    stage_ncit_embeddings,
)
from ontolib.repositories.embeddings.publication import CorpusManifest


class _StubEmbedder:
    """Deterministic 3-dim encoder: each text -> [len(text)]*3."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    model_id = "test-stub"
    model_revision = "1"

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[float(len(t))] * 3 for t in texts]


class _FakeSink:
    """Captures source-derived rows only; it does not fake publication semantics."""

    def __init__(self) -> None:
        self.batches: list[list[tuple[str, list[float], dict[str, Any]]]] = []

    async def stage(self, rows: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        self.batches.append(rows)


class _LifecyclePublisher(_FakeSink):
    def __init__(self) -> None:
        super().__init__()
        self.started: list[bool] = []
        self.failures: list[str] = []
        self.published = False
        self.start_manifest: object | None = None

    async def start(self, *, restart: bool = False) -> CorpusManifest:
        self.started.append(restart)
        if self.start_manifest is not None:
            return self.start_manifest  # type: ignore[return-value]
        return SimpleNamespace(state="building")  # type: ignore[return-value]

    async def publish(self) -> CorpusManifest:
        self.published = True
        return SimpleNamespace(actual_row_count=1)  # type: ignore[return-value]

    async def fail(self, error_message: str) -> CorpusManifest:
        self.failures.append(error_message)
        return SimpleNamespace(state="failed")  # type: ignore[return-value]


class _FakeNcitStore:
    def __init__(self, records: list[NcitEmbeddingRecord]) -> None:
        self._records = records
        self.pages: list[tuple[int, int]] = []

    async def embedding_records(
        self, *, limit: int, offset: int
    ) -> list[NcitEmbeddingRecord]:
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
    sink = _FakeSink()

    count = await stage_cde_embeddings(str(db), embedder, sink)

    assert count == 1
    # The embedder saw the CDE's search_text (its precomputed embedding text).
    assert embedder.seen == ["precomputed 100"]
    doc_id, vector, meta = sink.batches[0][0]
    assert doc_id == "100:2.0"
    assert vector == [15.0, 15.0, 15.0]
    assert meta["public_id"] == "100"
    assert meta["version"] == "2.0"
    assert meta["registration_status"] == "Standard"


@pytest.mark.unit
async def test_generate_cde_embeddings_batches_by_size(tmp_path: Path) -> None:
    db = tmp_path / "cde.db"
    _make_cde_db(db, [_cde_row("100", "2.0"), _cde_row("200", "1.0")])
    sink = _FakeSink()

    count = await stage_cde_embeddings(str(db), _StubEmbedder(), sink, batch_size=1)

    assert count == 2
    # batch_size=1 flushes each CDE in its own upsert.
    assert [len(b) for b in sink.batches] == [1, 1]
    doc_ids = {b[0][0] for b in sink.batches}
    assert doc_ids == {"100:2.0", "200:1.0"}


@pytest.mark.unit
async def test_generate_cde_embeddings_empty_db_is_noop(tmp_path: Path) -> None:
    db = tmp_path / "cde.db"
    _make_cde_db(db, [])
    sink = _FakeSink()

    count = await stage_cde_embeddings(str(db), _StubEmbedder(), sink)

    assert count == 0
    assert sink.batches == []


@pytest.mark.unit
async def test_generate_ncit_embeddings_pages_until_exhausted() -> None:
    records: list[NcitEmbeddingRecord] = [
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
    sink = _FakeSink()

    count, fingerprint = await stage_ncit_embeddings(
        store, embedder, sink, batch_size=2
    )

    assert count == 3
    assert len(fingerprint) == 64
    # Paged 0,2 then 4 (empty) -> break.
    assert store.pages == [(2, 0), (2, 2), (2, 4)]
    # Two non-empty batches were staged (sizes 2 and 1).
    assert [len(b) for b in sink.batches] == [2, 1]
    staged_codes = {row[0] for batch in sink.batches for row in batch}
    assert staged_codes == {"C0", "C1", "C2"}


@pytest.mark.unit
async def test_generate_ncit_uses_code_as_name_fallback() -> None:
    store = _FakeNcitStore(
        [
            {
                "code": "C999",
                "preferred_name": None,
                "synonyms": "",
                "definition": None,
                "semantic_type": None,
            }
        ]
    )
    embedder = _StubEmbedder()
    sink = _FakeSink()

    count, _ = await stage_ncit_embeddings(store, embedder, sink, batch_size=10)

    assert count == 1
    # With no preferred_name/synonyms/definition, the embedding text is just the code.
    assert embedder.seen == ["C999"]
    assert sink.batches[0][0][2]["preferred_name"] == ""


@pytest.mark.unit
async def test_generate_cde_publishes_after_staging(tmp_path: Path) -> None:
    db = tmp_path / "cde.db"
    _make_cde_db(db, [_cde_row("100", "2.0")])
    publisher = _LifecyclePublisher()

    manifest = await generate_cde_embeddings(
        str(db),
        _StubEmbedder(),
        publisher,
        restart=True,  # type: ignore[arg-type]
    )

    assert publisher.started == [True]
    assert publisher.published
    assert manifest.actual_row_count == 1


@pytest.mark.unit
async def test_generate_ncit_marks_failed_manifest_on_encoder_error() -> None:
    class _BrokenEmbedder:
        model_id = "test-broken"
        model_revision = "1"

        def encode(self, texts: list[str]) -> list[list[float]]:
            del texts
            raise RuntimeError("encoder exploded")

    store = _FakeNcitStore(
        [
            {
                "code": "C3262",
                "preferred_name": "Neoplasm",
                "synonyms": "",
                "definition": None,
                "semantic_type": None,
            }
        ]
    )
    publisher = _LifecyclePublisher()

    with pytest.raises(RuntimeError, match="encoder exploded"):
        await generate_ncit_embeddings(
            store,  # type: ignore[arg-type]
            _BrokenEmbedder(),
            publisher,  # type: ignore[arg-type]
        )

    assert publisher.failures == ["RuntimeError: encoder exploded"]
    assert not publisher.published


@pytest.mark.unit
async def test_generate_cde_marks_failed_manifest_on_encoder_error(
    tmp_path: Path,
) -> None:
    class _BrokenEmbedder:
        model_id = "test-broken"
        model_revision = "1"

        def encode(self, texts: list[str]) -> list[list[float]]:
            del texts
            raise RuntimeError("cde encoder exploded")

    db = tmp_path / "cde.db"
    _make_cde_db(db, [_cde_row("100", "2.0")])
    publisher = _LifecyclePublisher()

    with pytest.raises(RuntimeError, match="cde encoder exploded"):
        await generate_cde_embeddings(
            str(db),
            _BrokenEmbedder(),
            publisher,  # type: ignore[arg-type]
        )

    assert publisher.failures == ["RuntimeError: cde encoder exploded"]
    assert not publisher.published


@pytest.mark.unit
async def test_generate_returns_existing_completed_manifest_without_staging(
    tmp_path: Path,
) -> None:
    db = tmp_path / "cde.db"
    _make_cde_db(db, [_cde_row("100", "2.0")])
    publisher = _LifecyclePublisher()
    completed = SimpleNamespace(state="complete", actual_row_count=123)
    publisher.start_manifest = completed

    result = await generate_cde_embeddings(
        str(db),
        _StubEmbedder(),
        publisher,  # type: ignore[arg-type]
    )

    assert result is completed
    assert publisher.batches == []
    assert not publisher.published


@pytest.mark.unit
async def test_ncit_source_fingerprint_changes_with_record_content_and_order() -> None:
    first = _FakeNcitStore(
        [
            {
                "code": "C1",
                "preferred_name": "One",
                "synonyms": "",
                "definition": None,
                "semantic_type": None,
            },
            {
                "code": "C2",
                "preferred_name": "Two",
                "synonyms": "",
                "definition": None,
                "semantic_type": None,
            },
        ]
    )
    changed = _FakeNcitStore(
        [
            {
                "code": "C1",
                "preferred_name": "Changed",
                "synonyms": "",
                "definition": None,
                "semantic_type": None,
            },
            {
                "code": "C2",
                "preferred_name": "Two",
                "synonyms": "",
                "definition": None,
                "semantic_type": None,
            },
        ]
    )
    reversed_store = _FakeNcitStore(list(reversed(first._records)))

    original = await ncit_source_fingerprint(first, batch_size=1)
    content = await ncit_source_fingerprint(changed, batch_size=1)
    order = await ncit_source_fingerprint(reversed_store, batch_size=1)

    assert original[0] == content[0] == order[0] == 2
    assert len({original[1], content[1], order[1]}) == 3


@pytest.mark.unit
async def test_generation_preserves_original_error_when_failure_recording_fails() -> (
    None
):
    class _FailingPublisher(_LifecyclePublisher):
        async def fail(self, error_message: str) -> CorpusManifest:
            del error_message
            raise RuntimeError("manifest unavailable")

    class _BrokenEmbedder:
        model_id = "test-broken"
        model_revision = "1"

        def encode(self, texts: list[str]) -> list[list[float]]:
            del texts
            raise ValueError("original failure")

    publisher = _FailingPublisher()
    store = _FakeNcitStore(
        [
            {
                "code": "C3262",
                "preferred_name": "N",
                "synonyms": "",
                "definition": None,
                "semantic_type": None,
            }
        ]
    )

    with pytest.raises(ValueError, match="original failure") as captured:
        await generate_ncit_embeddings(
            store,
            _BrokenEmbedder(),
            publisher,  # type: ignore[arg-type]
        )

    assert any("manifest unavailable" in note for note in captured.value.__notes__)


class _FakeVector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _FakeModel:
    def __init__(self, model_name: str, *, revision: str) -> None:
        self.model_name = model_name
        self.revision = revision

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

    embedder = SentenceTransformerEmbedder("my-model", "my-revision")
    vectors = embedder.encode(["abcd", "xy"])

    # Each numpy-like vector was converted to a plain list via .tolist().
    assert vectors == [[4.0], [2.0]]
