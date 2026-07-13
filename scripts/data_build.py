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
import json
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
from ontolib.repositories.xref.coverage import (
    detect_coverage_regression,
    fetch_role_codes,
    generate_coverage_report,
    load_coverage_baseline,
    save_coverage_baseline,
)
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
    """Print the CDE-level coverage report and check for regression."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
            store = XrefStore(sf)
            role_codes = await fetch_role_codes(client)
            report = await generate_coverage_report(
                settings.cadsr_db_path,
                store,
                client,
                role_codes=role_codes,
            )
    finally:
        await dispose_engine(engine)
    data = report.as_dict()
    typer.echo(str(data))

    # Check regression if a baseline exists
    baseline_path = Path("data/cov-baseline.json")
    if baseline_path.exists():
        prev = load_coverage_baseline(baseline_path)
        if detect_coverage_regression(prev, report):
            typer.echo(
                f"COVERAGE REGRESSION: {prev.cde_coverage} -> {report.cde_coverage}",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(
            f"Baseline COV: {prev.cde_coverage}, current: {report.cde_coverage} — ok"
        )

    # Also save as the new baseline
    save_coverage_baseline(baseline_path, report)
    typer.echo(f"Saved baseline to {baseline_path}")


def _curated_pairs(
    golden: Path | None, *, trust_unsigned: bool = False
) -> frozenset[tuple[str, str]]:
    """Load the SME-signed ``exactMatch`` pairs from a curated SSSOM set.

    A curated pair is admitted as ``SME_CURATION`` evidence, which **stands alone**
    under D28 — it promotes a bridge to identity-grade by itself. So the file had better
    actually be signed. The shipped `golden/mappings.json` says of itself:
    ``"curated_by": "seed (engine) — REQUIRES SME sign-off"``, ``"status": "seed"`` —
    i.e. machine-generated. Minting engine guesses as human curation, writing them as
    `exactMatch/validated`, and counting them as published coverage is exactly the
    unfalsifiable claim this epic exists to replace.
    """
    if golden is None:
        return frozenset()

    with open(golden) as f:
        status = json.load(f).get("_meta", {}).get("status")
    if status != "sme-signed" and not trust_unsigned:
        typer.echo(
            f"refusing to use {golden} as curation evidence: its _meta.status is "
            f"{status!r}, not 'sme-signed'. SME curation promotes a bridge to "
            "identity-grade on its own, so an unsigned (engine-seeded) set would "
            "publish machine guesses as human-validated coverage. Pass "
            "--trust-unsigned-golden to override deliberately.",
            err=True,
        )
        raise typer.Exit(code=1)

    return frozenset(
        (m["subject_id"], m["object_id"])
        for m in load_golden_mappings(golden)
        if m["predicate_id"] == EXACT_MATCH
    )


async def _endpoint_version(client: OxigraphHttpClient) -> str | None:
    """The endpoint's version, from ``owl:versionInfo`` or else ``owl:versionIRI``.

    Uberon (and most OBO releases) carry no ``owl:versionInfo`` — they carry a
    ``owl:versionIRI`` like ``…/uberon/releases/2026-04-01/uberon.owl``.  That release
    date is a *real* version, not a fabrication, so falling back to it is honest.
    Without the fallback the documented happy path (`data-build xref-promote`) refuses
    to run, and the only escape is hand-typing a version — which is exactly what makes
    the D29 sweep self-consistent forever.
    """
    rows = await client.select(
        "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
        "SELECT ?v WHERE { ?ont a owl:Ontology . "
        "{ ?ont owl:versionInfo ?v } UNION { ?ont owl:versionIRI ?v } }"
    )
    versions = sorted({str(r["v"]) for r in rows if r.get("v")})
    if not versions:
        return None
    # Deterministic, and it does NOT silently pick one: a store holding two ontology
    # headers (Uberon + CL in one endpoint — `SUPPORTED_PREFIXES` already admits CL)
    # would otherwise return an arbitrary version per run under `LIMIT 1`. The version
    # drives a *destructive* comparison: if it flips between runs, the D29 sweep
    # quarantines every validated bridge and reports a normal-looking `quarantined: N`.
    return " + ".join(versions)


async def _endpoint_versions(
    ncit_client: OxigraphHttpClient, uberon_client: OxigraphHttpClient
) -> tuple[str, str]:
    """The endpoint versions this run validates against — never fabricated.

    A promoted bridge asserts "validated against these endpoint versions", and the D29
    staleness sweep compares exactly those strings. A fabricated version (`"unknown"`,
    or a hardcoded CLI default) is *self-consistent forever*: `"unknown" <> "unknown"`
    is never true, so the sweep can never fire again, and stale bridges keep being
    served and counted — with a coverage number that simply never goes down.
    """
    ncit = await _endpoint_version(ncit_client)
    upstream = await _endpoint_version(uberon_client)
    missing = [name for name, v in (("NCIt", ncit), ("Uberon", upstream)) if not v]
    if missing:
        typer.echo(
            f"No owl:versionInfo or owl:versionIRI on: {', '.join(missing)}. A"
            " promotion run must be able to name what it validated against, or D29"
            " staleness can never be detected. Load the store from a versioned release"
            " (see docs/DATA_SETUP.md), or pass --uberon-version explicitly.",
            err=True,
        )
        raise typer.Exit(code=1)
    return str(ncit), str(upstream)


async def _build_xref_promote(
    golden: Path | None, uberon_version: str | None, trust_unsigned: bool = False
) -> None:
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
            ncit_version, endpoint_uberon = await _endpoint_versions(
                ncit_client, uberon_client
            )
            report = await run_promotion(
                XrefStore(sf),
                ncit_client,
                uberon_client,
                ncit_version=ncit_version,
                source_version=uberon_version or endpoint_uberon,
                # Named explicitly: the D29 sweep is scoped by source, and a shared
                # default would let a Uberon run quarantine every Mondo bridge.
                source="uberon-cl-promotion",
                curated_pairs=_curated_pairs(golden, trust_unsigned=trust_unsigned),
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
    trust_unsigned_golden: bool = typer.Option(
        False,
        help="Use an unsigned (engine-seeded) golden set as curation evidence anyway. "
        "It will publish machine guesses as human-validated coverage.",
    ),
    uberon_version: str | None = typer.Option(
        None,
        help="Override the upstream release this run validates against. Defaults to "
        "the endpoint's own owl:versionInfo — do not fabricate one, or the D29 "
        "staleness sweep can never fire.",
    ),
) -> None:
    """Promote validated candidates to exactMatch (needs `robot` on PATH)."""
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_build_xref_promote(golden, uberon_version, trust_unsigned_golden))


@app.command(name="all")
def build_all() -> None:
    """Run the full build: OWL load -> caDSR build -> embeddings."""
    asyncio.run(_build_owl())
    _build_cadsr()
    asyncio.run(_build_embeddings())


if __name__ == "__main__":
    app()
