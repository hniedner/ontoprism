#!/usr/bin/env python
"""Standalone data build for ontoprism (issue #7).

One command to stand ontoprism up on a machine with no fairdata dependency:

  pdm run data-build all          # OWL load -> caDSR build -> embeddings
  pdm run data-build owl          # download + load inferred + stated (named graph)
  pdm run data-build cadsr        # download + build the caDSR CDE SQLite
  pdm run data-build embeddings --publish  # validate + publish embeddings -> pgvector

The embedding step needs the optional ML stack: `pdm install -G data-build`.
Config (store URL, DB paths) comes from the backend settings / env.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import typer

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.core.logging_config import get_logger
from ontolib.repositories.cadsr.build import build_database
from ontolib.repositories.cadsr.download import download_cadsr_cdes
from ontolib.repositories.embeddings.generate import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_REVISION,
    EMBED_DIM,
    SentenceTransformerEmbedder,
    ncit_source_fingerprint,
    stage_cde_embeddings,
    stage_ncit_embeddings,
)
from ontolib.repositories.embeddings.publication import (
    Corpus,
    CorpusBuild,
    EmbeddingCorpusPublisher,
    corpus_manifests,
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


def _require_ncit_source(
    version: str | None,
    count: int,
    *,
    expected_version: str,
    expected_count: int,
) -> str:
    if not version:
        raise RuntimeError("NCIt store has no owl:versionInfo")
    if version != expected_version:
        raise RuntimeError(
            "NCIt embedding source version does not match release expectation: "
            f"{version} != {expected_version}"
        )
    if count != expected_count:
        raise RuntimeError(
            "NCIt embedding source count does not match release expectation: "
            f"{count} != {expected_count}"
        )
    return version


def _require_stable_ncit_source(
    initial: tuple[str, int, str], final: tuple[str | None, int, str]
) -> None:
    if final != initial:
        raise RuntimeError(
            "NCIt source changed during embedding generation: "
            f"{'/'.join(map(str, initial))} -> {'/'.join(map(str, final))}"
        )


def _require_stable_cadsr_source(
    initial: tuple[str, int], final: tuple[str, int]
) -> None:
    if final != initial:
        raise RuntimeError(
            "caDSR source changed during embedding generation: "
            f"{'/'.join(map(str, initial))} -> {'/'.join(map(str, final))}"
        )


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_commit() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is required to identify the embedding build commit")
    status = subprocess.run(  # noqa: S603
        [executable, "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout:
        raise RuntimeError(
            "embedding publication requires a clean worktree so code_commit names "
            "the exact implementation"
        )
    result = subprocess.run(  # noqa: S603
        [executable, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


async def _show_embedding_manifests() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        manifests = await corpus_manifests(make_sessionmaker(engine))
    finally:
        await dispose_engine(engine)
    if not manifests:
        typer.echo("No embedding corpus manifests.")
        return
    for manifest in manifests:
        typer.echo(
            f"{manifest.corpus.value}: build={manifest.build_id} "
            f"state={manifest.state} active={manifest.is_active} "
            f"source={manifest.source_version} source_hash={manifest.source_hash} "
            f"rows={manifest.actual_row_count}/{manifest.expected_row_count} "
            f"model={manifest.model_id}@{manifest.model_revision} "
            f"dimension={manifest.vector_dimension} "
            f"sentinels={','.join(manifest.required_doc_ids)} "
            f"commit={manifest.code_commit} created={manifest.created_at} "
            f"completed={manifest.completed_at} error={manifest.error_message}"
        )


async def _record_build_failure(
    publisher: EmbeddingCorpusPublisher, original: BaseException
) -> None:
    try:
        await publisher.fail(f"{type(original).__name__}: {original}")
    except BaseException as record_error:
        original.add_note(f"Failed to record embedding build failure: {record_error}")


async def _publish_ncit_embeddings(build_id: UUID, *, restart: bool) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
            store = NcitGraphStore(client)
            expected, source_hash = await ncit_source_fingerprint(store)
            source_version = _require_ncit_source(
                await client.version(),
                expected,
                expected_version=settings.ncit_expected_version,
                expected_count=settings.ncit_embedding_expected_rows,
            )
            publisher = EmbeddingCorpusPublisher(
                sf,
                CorpusBuild(
                    build_id=build_id,
                    corpus=Corpus.NCIT,
                    source_version=source_version,
                    source_hash=source_hash,
                    model_id=DEFAULT_MODEL,
                    model_revision=DEFAULT_MODEL_REVISION,
                    vector_dimension=EMBED_DIM,
                    expected_row_count=settings.ncit_embedding_expected_rows,
                    code_commit=_code_commit(),
                    required_doc_ids=("C3262",),
                ),
            )
            await publisher.start(restart=restart)
            try:
                staged_count, staged_hash = await stage_ncit_embeddings(
                    store, SentenceTransformerEmbedder(), publisher
                )
                final_version = await client.version()
                final_count, final_hash = await ncit_source_fingerprint(store)
                if (staged_count, staged_hash) != (expected, source_hash):
                    raise RuntimeError(
                        "NCIt staged records differ from validated source: "
                        f"{staged_count}/{staged_hash} != {expected}/{source_hash}"
                    )
                _require_stable_ncit_source(
                    (source_version, expected, source_hash),
                    (final_version, final_count, final_hash),
                )
                # This independent cache refresh completes before embedding activation;
                # an FTS failure therefore cannot make an active embedding build look
                # failed to the operator.
                await populate_from_store(store, NcitSearchIndex(sf))
                manifest = await publisher.publish()
            except BaseException as exc:
                await _record_build_failure(publisher, exc)
                raise
    finally:
        await dispose_engine(engine)
    return manifest.actual_row_count or 0


async def _publish_cadsr_embeddings(build_id: UUID, *, restart: bool) -> int:
    settings = get_settings()
    db_path = Path(settings.cadsr_db_path)
    if not db_path.is_file():
        raise RuntimeError(f"caDSR source database is missing: {db_path}")
    source_hash = _sha256(db_path)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        expected = int(connection.execute("SELECT count(*) FROM cdes").fetchone()[0])
    if expected != settings.cadsr_embedding_expected_rows:
        raise RuntimeError(
            "caDSR embedding source count does not match release expectation: "
            f"{expected} != {settings.cadsr_embedding_expected_rows}"
        )
    engine = make_engine(settings.database_url)
    try:
        sf = make_sessionmaker(engine)
        publisher = EmbeddingCorpusPublisher(
            sf,
            CorpusBuild(
                build_id=build_id,
                corpus=Corpus.CADSR,
                source_version=f"sha256:{source_hash}",
                source_hash=source_hash,
                model_id=DEFAULT_MODEL,
                model_revision=DEFAULT_MODEL_REVISION,
                vector_dimension=EMBED_DIM,
                expected_row_count=settings.cadsr_embedding_expected_rows,
                code_commit=_code_commit(),
                required_doc_ids=("2517527:1.0",),
            ),
        )
        await publisher.start(restart=restart)
        try:
            await stage_cde_embeddings(
                str(db_path), SentenceTransformerEmbedder(), publisher
            )
            final_hash = _sha256(db_path)
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
                final_count = int(
                    connection.execute("SELECT count(*) FROM cdes").fetchone()[0]
                )
            _require_stable_cadsr_source(
                (source_hash, expected), (final_hash, final_count)
            )
            manifest = await publisher.publish()
        except BaseException as exc:
            await _record_build_failure(publisher, exc)
            raise
    finally:
        await dispose_engine(engine)
    return manifest.actual_row_count or 0


async def _build_embeddings(
    *, publish: bool, corpus: Corpus | None, restart: bool
) -> None:
    await _show_embedding_manifests()
    if not publish:
        typer.echo("Refusing to write without explicit --publish.", err=True)
        raise typer.Exit(code=1)
    if corpus in (None, Corpus.NCIT):
        ncit_build = uuid4()
        ncit = await _publish_ncit_embeddings(ncit_build, restart=restart)
        typer.echo(f"Published {ncit} NCIt embeddings as build {ncit_build}")
    if corpus in (None, Corpus.CADSR):
        cadsr_build = uuid4()
        cde = await _publish_cadsr_embeddings(cadsr_build, restart=restart)
        typer.echo(f"Published {cde} caDSR embeddings as build {cadsr_build}")


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
def embeddings(
    publish: bool = typer.Option(
        False,
        "--publish",
        help="Build, validate, and atomically replace each selected active corpus.",
    ),
    corpus: Corpus | None = typer.Option(  # noqa: B008 — typer option factory
        None,
        "--corpus",
        help="Publish only `ncit` or `cadsr`; default publishes each independently.",
    ),
) -> None:
    """Inspect active manifests; write only with explicit `--publish`."""
    asyncio.run(_build_embeddings(publish=publish, corpus=corpus, restart=False))


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
    asyncio.run(_build_embeddings(publish=True, corpus=None, restart=False))


if __name__ == "__main__":
    app()
