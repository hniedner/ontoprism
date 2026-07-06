"""Generate 768-dim embeddings for NCIt concepts + caDSR CDEs into pgvector.

The runtime only *reads* vectors (see :mod:`ontolib.repositories.embeddings.store`);
this module *produces* them for the standalone data build (issue #7). The heavy ML
dependency (sentence-transformers/torch) is optional — install the ``data-build`` group
— and is lazily imported only by :class:`SentenceTransformerEmbedder`, so an injected
:class:`Embedder` (e.g. a stub) exercises the whole pipeline without it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ontolib.terminologies.ncit.graph_store import NcitGraphStore

from ontolib.core.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-mpnet-base-v2"
EMBED_DIM = 768
BATCH_SIZE = 200
_SEP = " | "
_MAX_SYNONYMS = 5
_MAX_DEFINITION = 500

_UPSERT = """
    INSERT INTO {table} (doc_id, embedding, metadata)
    VALUES (:doc_id, (:embedding)::vector, (:metadata)::jsonb)
    ON CONFLICT (doc_id) DO UPDATE
        SET embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata
"""


class Embedder(Protocol):
    """Encodes a batch of texts into fixed-width float vectors."""

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """The real 768-dim encoder — lazily imports the optional ML dependency."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        # Dynamic import: sentence-transformers is only installed with the optional
        # data-build group, so don't hard-import it (keeps runtime + type-check lean).
        import importlib  # noqa: PLC0415

        st = importlib.import_module("sentence_transformers")
        self._model = st.SentenceTransformer(model_name)

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


def _vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


async def _upsert_batch(
    session_factory: async_sessionmaker[AsyncSession],
    table: str,
    rows: Sequence[tuple[str, list[float], dict[str, Any]]],
) -> None:
    if not rows:
        return
    # `table` is a fixed internal identifier; doc_id/embedding/metadata are bound.
    sql = text(_UPSERT.format(table=table))
    params = [
        {"doc_id": doc_id, "embedding": _vec_literal(vec), "metadata": json.dumps(meta)}
        for doc_id, vec, meta in rows
    ]
    async with session_factory() as session, session.begin():
        await session.execute(sql, params)


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


async def generate_cde_embeddings(
    db_path: str,
    embedder: Embedder,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Embed every CDE in the caDSR SQLite into the ``cde_repository`` pgvector table.

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
        await _upsert_batch(session_factory, "cde_repository", batch)
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
    logger.info("Embedded %d CDEs into cde_repository", total)
    return total


def _record_text(record: dict[str, str | None], code: str) -> str:
    synonyms = (record["synonyms"] or "").split(_SEP) if record["synonyms"] else []
    return ncit_text(
        record["preferred_name"] or code,
        synonyms,
        record["definition"],
        record["semantic_type"],
    )


def _record_meta(record: dict[str, str | None], code: str) -> dict[str, Any]:
    return {
        "code": code,
        "preferred_name": record["preferred_name"] or "",
        "semantic_type": record["semantic_type"] or "",
    }


def _ncit_batch(
    records: list[dict[str, str | None]], embedder: Embedder
) -> list[tuple[str, list[float], dict[str, Any]]]:
    """Build (doc_id, vector, metadata) rows for a page of NCIt embedding records."""
    codes = [r["code"] or "" for r in records]
    texts = [_record_text(r, code) for r, code in zip(records, codes, strict=True)]
    vectors = embedder.encode(texts)
    return [
        (code, vec, _record_meta(r, code))
        for r, code, vec in zip(records, codes, vectors, strict=True)
    ]


async def generate_ncit_embeddings(
    store: NcitGraphStore,
    embedder: Embedder,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Embed every NCIt concept into the ``ncit_concepts`` pgvector table.

    doc_id = concept code. Pages the store's embedding records. Returns the count.
    """
    total = 0
    offset = 0
    while True:
        records = await store.embedding_records(limit=batch_size, offset=offset)
        if not records:
            break
        batch = _ncit_batch(records, embedder)
        await _upsert_batch(session_factory, "ncit_concepts", batch)
        total += len(batch)
        offset += batch_size
    logger.info("Embedded %d NCIt concepts into ncit_concepts", total)
    return total
