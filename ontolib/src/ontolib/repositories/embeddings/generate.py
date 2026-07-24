"""Generate 768-dim embeddings for NCIt concepts + caDSR CDEs into pgvector.

The runtime only *reads* vectors (see :mod:`ontolib.repositories.embeddings.store`);
this module *produces* them for the standalone data build (issue #7). The heavy ML
dependency (sentence-transformers/torch) is optional — install the ``data-build`` group
— and is lazily imported only by :class:`SentenceTransformerEmbedder`, so an injected
:class:`Embedder` (e.g. a stub) exercises the whole pipeline without it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ontolib.repositories.embeddings.publication import (
        CorpusBuild,
        CorpusManifest,
        EmbeddingRow,
    )

from ontolib.core.logging_config import get_logger
from ontolib.repositories.embeddings.publication import (
    EMBEDDING_VECTOR_DIMENSION,
    Corpus,
)

logger = get_logger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-mpnet-base-v2"
DEFAULT_MODEL_REVISION = "e8c3b32edf5434bc2275fc9bab85f82640a19130"
EMBED_DIM = EMBEDDING_VECTOR_DIMENSION
BATCH_SIZE = 200
_SEP = " | "
_MAX_SYNONYMS = 5
_MAX_DEFINITION = 500


class Embedder(Protocol):
    """Encodes a batch of texts into fixed-width float vectors."""

    @property
    def model_id(self) -> str: ...

    @property
    def model_revision(self) -> str: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingBatchSink(Protocol):
    """Build-scoped destination that never exposes a batch to runtime readers."""

    async def stage(self, rows: list[EmbeddingRow]) -> None: ...


class EmbeddingPublisher(EmbeddingBatchSink, Protocol):
    @property
    def build(self) -> CorpusBuild: ...

    async def start(self, *, restart: bool = False) -> CorpusManifest: ...

    async def publish(self) -> CorpusManifest: ...

    async def fail(self, error_message: str) -> CorpusManifest: ...


class NcitEmbeddingSource(Protocol):
    """The stable ordered-record surface required by NCIt embedding generation."""

    async def embedding_records(
        self, *, limit: int, after: str | None = None
    ) -> list[NcitEmbeddingRecord]: ...


class NcitEmbeddingRecord(TypedDict):
    iri: str
    code: str
    preferred_name: str | None
    definition: str | None
    semantic_type: str | None
    synonyms: str


def _require_publisher_identity(
    publisher: EmbeddingPublisher, embedder: Embedder, corpus: Corpus
) -> None:
    build = publisher.build
    if build.corpus is not corpus:
        raise ValueError(f"publisher corpus is {build.corpus}, expected {corpus}")
    if (build.model_id, build.model_revision) != (
        embedder.model_id,
        embedder.model_revision,
    ):
        raise ValueError("publisher model provenance does not match the encoder")


class SentenceTransformerEmbedder:
    """The real 768-dim encoder — lazily imports the optional ML dependency."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        model_revision: str = DEFAULT_MODEL_REVISION,
    ) -> None:
        # Dynamic import: sentence-transformers is only installed with the optional
        # data-build group, so don't hard-import it (keeps runtime + type-check lean).
        import importlib  # noqa: PLC0415

        st = importlib.import_module("sentence_transformers")
        self._model_id = model_name
        self._model_revision = model_revision
        self._model = st.SentenceTransformer(model_name, revision=model_revision)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_revision(self) -> str:
        return self._model_revision

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.encode(texts)]


def ncit_text(
    preferred_name: str,
    synonyms: list[str],
    definition: str | None,
    semantic_type: str | None,
) -> str:
    """Build the NCIt concept embedding text (name, synonyms, definition, type)."""
    parts = [preferred_name, *synonyms[:_MAX_SYNONYMS]]
    if definition:
        parts.append(definition[:_MAX_DEFINITION])
    if semantic_type:
        parts.append(semantic_type)
    return _SEP.join(p for p in parts if p)


def cde_text(
    search_text: str | None, short_name: str, long_name: str, definition: str
) -> str:
    """The CDE embedding text: its precomputed search_text, else the core fields."""
    if search_text:
        return search_text
    return _SEP.join(p for p in (short_name, long_name, definition) if p)


def _iter_cde_rows(db_path: str) -> Iterator[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield from conn.execute(
            "SELECT public_id, version, search_text, short_name, long_name, "
            "definition, context, workflow_status, registration_status FROM cdes"
        )
    finally:
        conn.close()


async def stage_cde_embeddings(
    db_path: str,
    embedder: Embedder,
    sink: EmbeddingBatchSink,
    *,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Stage every CDE embedding through a build-scoped invisible sink.

    doc_id = ``{public_id}:{version}``. Returns the number of CDEs embedded.
    """
    total = 0
    texts: list[str] = []
    meta: list[tuple[str, dict[str, Any]]] = []

    async def flush() -> None:
        nonlocal total
        if not texts:
            return
        vectors = embedder.encode(texts)
        batch = [(m[0], v, m[1]) for (m, v) in zip(meta, vectors, strict=True)]
        await sink.stage(batch)
        total += len(batch)
        texts.clear()
        meta.clear()

    for row in _iter_cde_rows(db_path):
        doc_id = f"{row['public_id']}:{row['version']}"
        texts.append(
            cde_text(
                row["search_text"],
                row["short_name"],
                row["long_name"],
                row["definition"],
            )
        )
        meta.append(
            (
                doc_id,
                {
                    "public_id": row["public_id"],
                    "version": row["version"],
                    "short_name": row["short_name"],
                    "long_name": row["long_name"],
                    "context": row["context"] or "",
                    "workflow_status": row["workflow_status"] or "",
                    "registration_status": row["registration_status"] or "",
                },
            )
        )
        if len(texts) >= batch_size:
            await flush()
    await flush()
    logger.info("Staged %d caDSR CDE embeddings", total)
    return total


def _record_text(record: NcitEmbeddingRecord, code: str) -> str:
    synonyms = (record["synonyms"] or "").split(_SEP) if record["synonyms"] else []
    return ncit_text(
        record["preferred_name"] or code,
        synonyms,
        record["definition"],
        record["semantic_type"],
    )


def _record_meta(record: NcitEmbeddingRecord, code: str) -> dict[str, Any]:
    return {
        "code": code,
        "preferred_name": record["preferred_name"] or "",
        "semantic_type": record["semantic_type"] or "",
    }


def _ncit_batch(
    records: list[NcitEmbeddingRecord], embedder: Embedder
) -> list[tuple[str, list[float], dict[str, Any]]]:
    """Build (doc_id, vector, metadata) rows for a page of NCIt embedding records."""
    codes = [r["code"] or "" for r in records]
    texts = [_record_text(r, code) for r, code in zip(records, codes, strict=True)]
    vectors = embedder.encode(texts)
    return [
        (code, vec, _record_meta(r, code))
        for r, code, vec in zip(records, codes, vectors, strict=True)
    ]


async def stage_ncit_embeddings(
    store: NcitEmbeddingSource,
    embedder: Embedder,
    sink: EmbeddingBatchSink,
    *,
    batch_size: int = BATCH_SIZE,
) -> tuple[int, str]:
    """Stage every NCIt concept embedding through a build-scoped invisible sink.

    doc_id = concept code. Returns the count and exact ordered-record fingerprint.
    """
    total = 0
    after: str | None = None
    digest = hashlib.sha256()
    while True:
        records = await store.embedding_records(limit=batch_size, after=after)
        if not records:
            break
        _update_record_digest(digest, records)
        batch = _ncit_batch(records, embedder)
        await sink.stage(batch)
        total += len(batch)
        after = records[-1]["iri"]
    logger.info("Staged %d NCIt concept embeddings", total)
    return total, digest.hexdigest()


async def ncit_source_fingerprint(
    store: NcitEmbeddingSource, *, batch_size: int = BATCH_SIZE
) -> tuple[int, str]:
    """Hash the exact ordered records used as NCIt embedding input."""
    digest = hashlib.sha256()
    total = 0
    after: str | None = None
    while True:
        records = await store.embedding_records(limit=batch_size, after=after)
        if not records:
            break
        _update_record_digest(digest, records)
        total += len(records)
        after = records[-1]["iri"]
    return total, digest.hexdigest()


def _update_record_digest(digest: Any, records: list[NcitEmbeddingRecord]) -> None:
    for record in records:
        digest.update(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        )
        digest.update(b"\n")


async def _record_failure(
    publisher: EmbeddingPublisher, original: BaseException
) -> None:
    try:
        await publisher.fail(f"{type(original).__name__}: {original}")
    except Exception as record_error:
        original.add_note(f"Failed to record embedding build failure: {record_error}")


async def generate_cde_embeddings(
    db_path: str,
    embedder: Embedder,
    publisher: EmbeddingPublisher,
    *,
    batch_size: int = BATCH_SIZE,
    restart: bool = False,
) -> CorpusManifest:
    """Stage, validate, and atomically publish the complete caDSR corpus."""
    _require_publisher_identity(publisher, embedder, Corpus.CADSR)
    started = await publisher.start(restart=restart)
    if started.state == "complete":
        return started
    try:
        await stage_cde_embeddings(db_path, embedder, publisher, batch_size=batch_size)
        return await publisher.publish()
    except BaseException as exc:
        await _record_failure(publisher, exc)
        raise


async def generate_ncit_embeddings(
    store: NcitEmbeddingSource,
    embedder: Embedder,
    publisher: EmbeddingPublisher,
    *,
    batch_size: int = BATCH_SIZE,
    restart: bool = False,
) -> CorpusManifest:
    """Stage, validate, and atomically publish the complete NCIt corpus."""
    _require_publisher_identity(publisher, embedder, Corpus.NCIT)
    started = await publisher.start(restart=restart)
    if started.state == "complete":
        return started
    try:
        await stage_ncit_embeddings(store, embedder, publisher, batch_size=batch_size)
        return await publisher.publish()
    except BaseException as exc:
        await _record_failure(publisher, exc)
        raise
