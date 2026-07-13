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
import logging
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
from ontolib.repositories.xref.candidate_ingest import ingest_candidates
from ontolib.repositories.xref.coverage import generate_coverage_report
from ontolib.repositories.xref.mapping_score import load_golden_mappings
from ontolib.repositories.xref.promotion import run_promotion
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.vocab import EXACT_MATCH
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


async def _build_xref() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        async with (
            OxigraphHttpClient(settings.ncit_sparql_url) as ncit_client,
            OxigraphHttpClient(settings.uberon_sparql_url) as uberon_client,
        ):
            store = XrefStore(sf)
            ncit_version = (await ncit_client.version()) or "unknown"
            uberon_version = "uberon-2026-01"
            report = await ingest_candidates(
                store,
                ncit_client,
                uberon_client,
                ncit_version=ncit_version,
                uberon_version=uberon_version,
            )
    finally:
        await dispose_engine(engine)
    typer.echo(f"xref candidates ingested: {report}")


async def _build_xref_coverage() -> None:
    """Print the CDE-level coverage report.

    Note: ``anchors_in_roles`` is 0 because ``role_codes`` is not yet
    wired here — a populated decomposition run (with role-target fillers)
    is needed to compute it. TODO(#78/#79): inject role codes from the
    decomposed `ncit_decomposed` graph once Uberon ``part_of`` and Mondo
    genus are available.
    """
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
            store = XrefStore(sf)
            report = await generate_coverage_report(
                settings.cadsr_db_path,
                store,
                client,
                role_codes=frozenset(),
            )
    finally:
        await dispose_engine(engine)
    typer.echo(str(report.as_dict()))


def _curated_pairs(golden: Path | None) -> frozenset[tuple[str, str]]:
    """Load the SME-signed ``exactMatch`` pairs from a curated SSSOM set."""
    if golden is None:
        return frozenset()
    return frozenset(
        (m["subject_id"], m["object_id"])
        for m in load_golden_mappings(golden)
        if m["predicate_id"] == EXACT_MATCH
    )


async def _build_xref_promote(golden: Path | None, uberon_version: str) -> None:
    """Validation-driven promotion (#73): closeMatch/proposed -> exactMatch/validated.

    Shells out to ROBOT/ELK per candidate (EL profile + satisfiability gate before any
    classification), so `robot` must be on PATH — see docs/DATA_SETUP.md.

    Exits non-zero if the reasoner failed to run for any candidate: a promotion pass
    that could not reason is a *failed* run, not a run that conservatively promoted
    nothing, and the two must never look alike from the outside.
    """
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        async with (
            OxigraphHttpClient(settings.ncit_sparql_url) as ncit_client,
            OxigraphHttpClient(settings.uberon_sparql_url) as uberon_client,
        ):
            ncit_version = (await ncit_client.version()) or "unknown"
            report = await run_promotion(
                XrefStore(sf),
                ncit_client,
                uberon_client,
                ncit_version=ncit_version,
                source_version=uberon_version,
                curated_pairs=_curated_pairs(golden),
            )
    finally:
        await dispose_engine(engine)

    typer.echo(f"xref promotion: {report}")
    if report["reasoner_errors"]:
        typer.echo(
            f"FAILED: the reasoner could not run for {report['reasoner_errors']} "
            "candidate(s). This is NOT 'no candidate qualified' — check that `robot` "
            "and Java are on PATH (docs/DATA_SETUP.md) and re-run.",
            err=True,
        )
        raise typer.Exit(code=1)


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


@app.command()
def xref() -> None:
    """Populate concept_xref with Uberon/CL candidate mappings."""
    asyncio.run(_build_xref())


@app.command(name="xref-coverage")
def xref_coverage() -> None:
    """Print the CDE-level caDSR coverage report (COV)."""
    asyncio.run(_build_xref_coverage())


@app.command(name="xref-promote")
def xref_promote(
    golden: Path | None = typer.Option(  # noqa: B008 — typer option factory
        None,
        help="Curated (SME-signed) SSSOM mapping set; its exactMatch pairs seed the "
        "trusted-anchor set that structural corroboration is measured against.",
    ),
    uberon_version: str = typer.Option(
        "uberon-2026-01",
        help="The upstream release this run validates against. Bridges validated "
        "against any other release are quarantined (D29).",
    ),
) -> None:
    """Promote validated candidates to exactMatch (needs `robot` on PATH)."""
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_build_xref_promote(golden, uberon_version))


@app.command(name="all")
def build_all() -> None:
    """Run the full build: OWL load -> caDSR build -> embeddings."""
    asyncio.run(_build_owl())
    _build_cadsr()
    asyncio.run(_build_embeddings())


if __name__ == "__main__":
    app()
