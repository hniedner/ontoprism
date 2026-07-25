"""Integration tests for embedding staging/publication (fake embedder, real DB).

Uses a deterministic stub embedder so the whole staging/publication path is exercised
against disposable real Postgres/pgvector and a bounded disposable NCIt store without
the heavy ML dependency. Not `full_build` — runs in the CI services job.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from scripts import data_build
from scripts.data_build import _publish_cadsr_embeddings, _publish_ncit_embeddings
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.cadsr.build import build_database
from ontolib.repositories.embeddings.generate import (
    EMBED_DIM,
    generate_cde_embeddings,
    generate_ncit_embeddings,
)
from ontolib.repositories.embeddings.publication import (
    Corpus,
    CorpusBuild,
    EmbeddingCorpusPublisher,
)
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings", "isolated_oxigraph_settings"),
]


class _StubEmbedder:
    """Deterministic 768-dim vectors — no model, enough to exercise the pipeline."""

    model_id = "test-deterministic-embedder"
    model_revision = "1" * 40

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float((len(t) % 7) + 1) / 8.0] * EMBED_DIM for t in texts]


class _FailAfterOneBatchEmbedder:
    """Real generation seam that fails after one successful encoded batch."""

    def __init__(self) -> None:
        self._calls = 0

    model_id = "test-failing-embedder"
    model_revision = "1" * 40

    def encode(self, texts: list[str]) -> list[list[float]]:
        self._calls += 1
        if self._calls > 1:
            raise RuntimeError("injected embedding failure after first batch")
        return [[0.25] * EMBED_DIM for _ in texts]


_CADSR_XML = """<DataElementsList><DataElement>
  <PUBLICID>2517527</PUBLICID><VERSION>1.0</VERSION>
  <PREFERREDNAME>DEMO_CDE</PREFERREDNAME><LONGNAME>Demo CDE</LONGNAME>
  <PREFERREDDEFINITION>A demo CDE.</PREFERREDDEFINITION>
  <VALUEDOMAIN><Datatype>CHARACTER</Datatype></VALUEDOMAIN>
</DataElement></DataElementsList>"""


def _publisher(
    session_factory: async_sessionmaker[AsyncSession],
    corpus: Corpus,
    expected: int,
    embedder: _StubEmbedder | _FailAfterOneBatchEmbedder,
) -> EmbeddingCorpusPublisher:
    return EmbeddingCorpusPublisher(
        session_factory,
        CorpusBuild(
            build_id=uuid4(),
            corpus=corpus,
            source_version="test-source",
            source_hash="a" * 64,
            model_id=embedder.model_id,
            model_revision=embedder.model_revision,
            vector_dimension=EMBED_DIM,
            expected_row_count=expected,
            code_commit="c" * 40,
            required_doc_ids=(
                ("C3262",) if corpus is Corpus.NCIT else ("2517527:1.0",)
            ),
        ),
    )


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as session:
            await session.execute(text("SELECT 1"))
        yield sf
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_generate_cde_embeddings_writes_vectors(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    db = tmp_path / "cde.db"
    build_database([_write(tmp_path, _CADSR_XML)], db)
    embedder = _StubEmbedder()
    manifest = await generate_cde_embeddings(
        str(db), embedder, _publisher(session_factory, Corpus.CADSR, 1, embedder)
    )
    assert manifest.actual_row_count == 1
    async with session_factory() as session:
        row = await session.execute(
            text(
                "SELECT vector_dims(embedding) AS d FROM cde_repository "
                "WHERE doc_id = '2517527:1.0'"
            )
        )
        assert row.scalar_one() == EMBED_DIM


@pytest.mark.integration
async def test_generate_ncit_embeddings_from_store(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    url = get_settings().ncit_sparql_url
    async with OxigraphHttpClient(url) as client:
        store = NcitGraphStore(client)
        count = await store.embedding_record_count()
        embedder = _StubEmbedder()
        manifest = await generate_ncit_embeddings(
            store,
            embedder,
            _publisher(session_factory, Corpus.NCIT, count, embedder),
        )
    assert manifest.actual_row_count == count
    assert 1 <= count <= 11
    async with session_factory() as session:
        present = await session.execute(
            text("SELECT 1 FROM ncit_concepts WHERE doc_id = 'C3262'")
        )
        assert present.scalar_one_or_none() == 1


@pytest.mark.integration
async def test_interrupted_ncit_build_does_not_change_serving_corpus(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A successfully committed staging batch must remain invisible on failure."""
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM ncit_concepts"))
        await session.execute(
            text(
                "INSERT INTO ncit_concepts (doc_id, embedding, metadata) "
                "VALUES ('OLD', :embedding, '{}'::jsonb)"
            ),
            {"embedding": "[" + ",".join(["0.5"] * EMBED_DIM) + "]"},
        )

    url = get_settings().ncit_sparql_url
    async with OxigraphHttpClient(url) as client:
        embedder = _FailAfterOneBatchEmbedder()
        publisher = _publisher(
            session_factory,
            Corpus.NCIT,
            await NcitGraphStore(client).embedding_record_count(),
            embedder,
        )
        with pytest.raises(
            RuntimeError, match="injected embedding failure after first batch"
        ):
            await generate_ncit_embeddings(
                NcitGraphStore(client),
                embedder,
                publisher,
                batch_size=1,
            )

    async with session_factory() as session:
        rows = await session.execute(
            text("SELECT doc_id FROM ncit_concepts ORDER BY 1")
        )
        assert list(rows.scalars()) == ["OLD"]


@pytest.mark.integration
async def test_embedding_operator_inspects_then_refuses_implicit_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM embedding_corpus_staging"))
        await session.execute(text("DELETE FROM embedding_corpus_manifest"))
        await session.execute(text("DELETE FROM ncit_concepts"))
        await session.execute(
            text(
                "INSERT INTO ncit_concepts (doc_id, embedding, metadata) "
                "VALUES ('OLD', :embedding, '{}'::jsonb)"
            ),
            {"embedding": "[" + ",".join(["0.5"] * EMBED_DIM) + "]"},
        )

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "scripts/data_build.py",
        "embeddings",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode()
    stderr = stderr_bytes.decode()

    assert process.returncode == 1
    assert "No embedding corpus manifests" in stdout
    assert "Refusing to write without explicit --publish" in stderr
    async with session_factory() as session:
        rows = await session.execute(
            text("SELECT doc_id FROM ncit_concepts ORDER BY 1")
        )
        manifests = await session.scalar(
            text("SELECT count(*) FROM embedding_corpus_manifest")
        )
    assert list(rows.scalars()) == ["OLD"]
    assert manifests == 0


@pytest.mark.integration
async def test_production_ncit_publisher_records_source_and_refreshes_fts(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    url = get_settings().ncit_sparql_url
    async with OxigraphHttpClient(url) as client:
        count = await NcitGraphStore(client).embedding_record_count()
    monkeypatch.setenv("NCIT_EMBEDDING_EXPECTED_ROWS", str(count))
    monkeypatch.setattr("scripts.data_build._code_commit", lambda: "d" * 40)
    get_settings.cache_clear()
    build_id = uuid4()

    published = await _publish_ncit_embeddings(
        build_id,
        restart=False,
        embedder=_StubEmbedder(),  # type: ignore[arg-type]
    )

    assert published == count
    async with session_factory() as session:
        manifest = (
            await session.execute(
                text(
                    "SELECT source_version, source_hash, actual_row_count, "
                    "model_revision, is_active FROM embedding_corpus_manifest "
                    "WHERE build_id = :build_id"
                ),
                {"build_id": build_id},
            )
        ).one()
        search_count = await session.scalar(text("SELECT count(*) FROM ncit_search"))
    assert manifest.source_version == "26.02d"
    assert len(manifest.source_hash) == 64
    assert manifest.actual_row_count == count
    assert manifest.model_revision
    assert manifest.is_active
    assert search_count == count


@pytest.mark.integration
async def test_production_cadsr_publisher_records_file_provenance(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "cde.db"
    build_database([_write(tmp_path, _CADSR_XML)], db)
    monkeypatch.setenv("CADSR_DB_PATH", str(db))
    monkeypatch.setenv("CADSR_EMBEDDING_EXPECTED_ROWS", "1")
    monkeypatch.setattr("scripts.data_build._code_commit", lambda: "e" * 40)
    get_settings.cache_clear()
    build_id = uuid4()

    published = await _publish_cadsr_embeddings(
        build_id,
        restart=False,
        embedder=_StubEmbedder(),  # type: ignore[arg-type]
    )

    assert published == 1
    async with session_factory() as session:
        manifest = (
            await session.execute(
                text(
                    "SELECT source_version, source_hash, actual_row_count, is_active "
                    "FROM embedding_corpus_manifest WHERE build_id = :build_id"
                ),
                {"build_id": build_id},
            )
        ).one()
    assert manifest.source_version == f"sha256:{manifest.source_hash}"
    assert len(manifest.source_hash) == 64
    assert manifest.actual_row_count == 1
    assert manifest.is_active


@pytest.mark.integration
async def test_production_ncit_source_drift_fails_candidate_and_preserves_active(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    url = get_settings().ncit_sparql_url
    async with OxigraphHttpClient(url) as client:
        store = NcitGraphStore(client)
        count, fingerprint = await data_build.ncit_source_fingerprint(store)
        embedder = _StubEmbedder()
        old = _publisher(session_factory, Corpus.NCIT, count, embedder)
        await generate_ncit_embeddings(store, embedder, old)
    monkeypatch.setenv("NCIT_EMBEDDING_EXPECTED_ROWS", str(count))
    monkeypatch.setattr("scripts.data_build._code_commit", lambda: "f" * 40)
    calls = 0

    async def drifting_fingerprint(store: NcitGraphStore):
        nonlocal calls
        del store
        calls += 1
        return (count, fingerprint if calls == 1 else "0" * 64)

    monkeypatch.setattr(data_build, "ncit_source_fingerprint", drifting_fingerprint)
    get_settings.cache_clear()
    candidate_id = uuid4()

    with pytest.raises(RuntimeError, match="source changed"):
        await _publish_ncit_embeddings(
            candidate_id, restart=False, embedder=_StubEmbedder()
        )

    async with session_factory() as session:
        active = await session.scalar(
            text(
                "SELECT build_id FROM embedding_corpus_manifest "
                "WHERE corpus = 'ncit' AND is_active"
            )
        )
        candidate = (
            await session.execute(
                text(
                    "SELECT state, error_message FROM embedding_corpus_manifest "
                    "WHERE build_id = :build_id"
                ),
                {"build_id": candidate_id},
            )
        ).one()
    assert active == old.build.build_id
    assert candidate.state == "failed"
    assert "source changed" in candidate.error_message


@pytest.mark.integration
async def test_production_cadsr_source_drift_fails_candidate_and_preserves_active(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "cde.db"
    build_database([_write(tmp_path, _CADSR_XML)], db)
    embedder = _StubEmbedder()
    old = _publisher(session_factory, Corpus.CADSR, 1, embedder)
    await generate_cde_embeddings(str(db), embedder, old)
    monkeypatch.setenv("CADSR_DB_PATH", str(db))
    monkeypatch.setenv("CADSR_EMBEDDING_EXPECTED_ROWS", "1")
    monkeypatch.setattr("scripts.data_build._code_commit", lambda: "9" * 40)
    real_hash = data_build._sha256(db)
    hashes = iter((real_hash, "0" * 64))
    monkeypatch.setattr(data_build, "_sha256", lambda _path: next(hashes))
    get_settings.cache_clear()
    candidate_id = uuid4()

    with pytest.raises(RuntimeError, match="source changed"):
        await _publish_cadsr_embeddings(
            candidate_id, restart=False, embedder=_StubEmbedder()
        )

    async with session_factory() as session:
        active = await session.scalar(
            text(
                "SELECT build_id FROM embedding_corpus_manifest "
                "WHERE corpus = 'cadsr' AND is_active"
            )
        )
        state = await session.scalar(
            text(
                "SELECT state FROM embedding_corpus_manifest WHERE build_id = :build_id"
            ),
            {"build_id": candidate_id},
        )
    assert active == old.build.build_id
    assert state == "failed"


def _write(tmp_path: Path, xml: str) -> Path:
    path = tmp_path / "cdes.xml"
    path.write_text(xml)
    return path
