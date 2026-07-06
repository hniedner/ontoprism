#!/usr/bin/env python
"""Standalone data build for ontoprism (issue #7).

One command to stand ontoprism up on a machine with no fairdata dependency:

  pdm run data-build all          # OWL load -> caDSR build -> embeddings
  pdm run data-build owl          # download + load inferred + stated (named graph)
  pdm run data-build cadsr        # download + build the caDSR CDE SQLite
  pdm run data-build embeddings   # generate 768-dim NCIt + caDSR embeddings -> pgvector

The embedding step needs the optional ML stack: `pdm install -G data-build`.
Config (store URL, DB paths) comes from the backend settings / env.
"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import typer

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.core.logging_config import get_logger
from ontolib.repositories.cadsr.build import build_database
from ontolib.repositories.cadsr.download import download_cadsr_cdes
from ontolib.repositories.embeddings.generate import (
    generate_cde_embeddings,
    generate_ncit_embeddings,
)
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.owl_load import build_ncit_store
from ontolib.terminologies.ncit.search_index import (
    NcitSearchIndex,
    populate_from_store,
)
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

logger = get_logger(__name__)
app = typer.Typer(help="Standalone data build for ontoprism.", no_args_is_help=True)


async def _build_owl() -> None:
    settings = get_settings()
    async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
        loaded = await build_ncit_store(client, Path(settings.ncit_owl_dir))
    typer.echo(f"Loaded NCIt OWL variants: {', '.join(sorted(loaded))}")


def _build_cadsr() -> None:
    settings = get_settings()
    data_dir = Path(settings.cadsr_data_dir)

    async def _download() -> Path:
        outcome = await download_cadsr_cdes(
            data_dir, base_url=settings.cadsr_download_url
        )
        return Path(outcome.path)

    zip_path = asyncio.run(_download())
    extract_dir = data_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    xml_paths = sorted(extract_dir.rglob("*.xml"))
    if not xml_paths:
        typer.echo("No CDE XML found in the downloaded archive.", err=True)
        raise typer.Exit(code=1)
    count = build_database(xml_paths, Path(settings.cadsr_db_path))
    if count == 0:
        typer.echo("caDSR build produced 0 CDEs — aborting.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Built caDSR DB with {count} CDEs at {settings.cadsr_db_path}")


async def _build_embeddings() -> None:
    from ontolib.repositories.embeddings.generate import (  # noqa: PLC0415
        SentenceTransformerEmbedder,
    )

    settings = get_settings()
    embedder = SentenceTransformerEmbedder()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
            store = NcitGraphStore(client)
            ncit = await generate_ncit_embeddings(store, embedder, sf)
            # Also refresh the FTS cache while we're materializing from the store.
            await populate_from_store(store, NcitSearchIndex(sf))
        cde = await generate_cde_embeddings(settings.cadsr_db_path, embedder, sf)
    finally:
        await dispose_engine(engine)
    typer.echo(f"Embedded {ncit} NCIt concepts + {cde} CDEs into pgvector")


@app.command()
def owl() -> None:
    """Download + load the inferred (default) and stated (named graph) NCIt OWL."""
    asyncio.run(_build_owl())


@app.command()
def cadsr() -> None:
    """Download the caDSR CDE archive and build the SQLite repository."""
    _build_cadsr()


@app.command()
def embeddings() -> None:
    """Generate NCIt + caDSR embeddings into pgvector (needs the data-build extra)."""
    asyncio.run(_build_embeddings())


@app.command(name="all")
def build_all() -> None:
    """Run the full build: OWL load -> caDSR build -> embeddings."""
    asyncio.run(_build_owl())
    _build_cadsr()
    asyncio.run(_build_embeddings())


if __name__ == "__main__":
    app()
