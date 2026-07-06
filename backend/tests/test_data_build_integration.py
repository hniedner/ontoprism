"""Integration tests for embedding generation → pgvector (fake embedder, real DB).

Uses a deterministic stub embedder so the whole generate→upsert path is exercised
against real Postgres/pgvector (and the live NCIt store for the concept path) without
the heavy ML dependency. Not `full_build` — runs in the CI services job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.repositories.cadsr.build import build_database
from ontolib.repositories.embeddings.generate import (
    EMBED_DIM,
    generate_cde_embeddings,
    generate_ncit_embeddings,
)
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class _StubEmbedder:
    """Deterministic 768-dim vectors — no model, enough to exercise the pipeline."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7) / 7.0] * EMBED_DIM for t in texts]


_CADSR_XML = """<DataElementsList><DataElement>
  <PUBLICID>2517527</PUBLICID><VERSION>1.0</VERSION>
  <PREFERREDNAME>DEMO_CDE</PREFERREDNAME><LONGNAME>Demo CDE</LONGNAME>
  <PREFERREDDEFINITION>A demo CDE.</PREFERREDDEFINITION>
  <VALUEDOMAIN><Datatype>CHARACTER</Datatype></VALUEDOMAIN>
</DataElement></DataElementsList>"""


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        await dispose_engine(engine)
        pytest.skip("Postgres not reachable")
    try:
        yield sf
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_generate_cde_embeddings_writes_vectors(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    db = tmp_path / "cde.db"
    build_database([_write(tmp_path, _CADSR_XML)], db)
    count = await generate_cde_embeddings(str(db), _StubEmbedder(), session_factory)
    assert count == 1
    async with session_factory() as session:
        try:
            row = await session.execute(
                text(
                    "SELECT vector_dims(embedding) AS d FROM cde_repository "
                    "WHERE doc_id = '2517527:1.0'"
                )
            )
            assert row.scalar_one() == EMBED_DIM
        finally:
            await session.execute(
                text("DELETE FROM cde_repository WHERE doc_id = '2517527:1.0'")
            )
            await session.commit()


@pytest.mark.integration
async def test_generate_ncit_embeddings_from_store(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    url = get_settings().ncit_sparql_url
    async with OxigraphHttpClient(url) as client:
        try:
            await client.count()
        except Exception:
            pytest.skip(f"NCIt store not reachable at {url}")
        store = NcitGraphStore(client)
        count = await generate_ncit_embeddings(store, _StubEmbedder(), session_factory)
    assert count >= 1
    async with session_factory() as session:
        try:
            present = await session.execute(
                text("SELECT 1 FROM ncit_concepts WHERE doc_id = 'C3262'")
            )
            assert present.scalar_one_or_none() == 1
        finally:
            await session.execute(text("DELETE FROM ncit_concepts"))
            await session.commit()


def _write(tmp_path: Path, xml: str) -> Path:
    path = tmp_path / "cdes.xml"
    path.write_text(xml)
    return path
