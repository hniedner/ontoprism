#!/usr/bin/env python
"""Standalone data build for ontoprism (issue #7).

One command to stand ontoprism up on a machine with no fairdata dependency:

  pdm run data-build all          # ontology indexes -> caDSR -> embeddings
  pdm run data-build owl          # certify inferred + stated release pair (#180)
  pdm run data-build ncit-store   # build + validate an inactive sibling (#181)
  pdm run data-build ncit-activate --candidate-manifest PATH  # activate (#148)
  pdm run data-build ncit-bootstrap # first install only; refuses an existing target
  pdm run data-build uberon-store # download + build the Uberon/CL QLever index
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
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import UUID, uuid4

import typer

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from backend.icdo_datasets import ServedIcdoDataset
from backend.repository_metadata import (
    RepositoryMetadataService,
    RepositoryUnhealthy,
    icdo_expectation,
)
from ontolib.core.data_build_tools import configured_robot_installation
from ontolib.core.logging_config import get_logger
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.repositories.cadsr.archive import extract_cadsr_archive
from ontolib.repositories.cadsr.build import build_database
from ontolib.repositories.cadsr.download import download_cadsr_cdes
from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.repositories.embeddings.generate import (
    EMBED_DIM,
    Embedder,
    SentenceTransformerEmbedder,
    cadsr_source_fingerprint,
    ncit_source_fingerprint,
    stage_cde_embeddings,
    stage_ncit_embeddings,
)
from ontolib.repositories.embeddings.publication import (
    Corpus,
    CorpusBuild,
    EmbeddingCorpusPublisher,
    coordinate_corpus_source_replacement,
    corpus_manifests,
    replacing_corpus_source,
)
from ontolib.repositories.icdo.ingest import ingest_icdo4, ingest_icdo32_morphology
from ontolib.repositories.icdo.store import IcdoRepository, publish_dataset
from ontolib.repositories.xref.candidate_ingest import ingest_candidates
from ontolib.repositories.xref.coverage import (
    detect_coverage_regression,
    fetch_role_codes,
    generate_coverage_report,
    load_coverage_baseline,
    save_coverage_baseline,
)
from ontolib.repositories.xref.mapping_score import load_golden_mappings
from ontolib.repositories.xref.models import (
    UberonPromotionGenerationMetadata,
    UberonReadIdentity,
    XrefReadPolicy,
)
from ontolib.repositories.xref.p334_alignment import publish_p334_alignments
from ontolib.repositories.xref.promotion import run_promotion
from ontolib.repositories.xref.publisher_xref import publish_uberon_xrefs
from ontolib.repositories.xref.store import XrefStore
from ontolib.repositories.xref.vocab import EXACT_MATCH
from ontolib.terminologies.ncit.activation import (
    ActivationJournal,
    DockerComposeNcitService,
    QleverServiceContract,
    bind_projection_plan,
    capture_projection_plan,
    prepare_activation_journal,
    reconcile_projection_at_endpoint,
    run_journaled_activation,
    validate_active_store_health,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.owl_download import (
    PAIR_MANIFEST_FILENAME,
    download_ncit_owl_pair,
)
from ontolib.terminologies.ncit.search_index import (
    NcitSearchIndex,
    populate_from_store,
)
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    DockerQleverRuntime,
    NcitSiblingStoreManifest,
    build_initial_ncit_store,
    build_ncit_sibling_store,
    validate_ncit_sibling_manifest,
)
from ontolib.terminologies.sparql_http_client import SparqlHttpClient
from ontolib.terminologies.uberon.store import (
    UBERON_ARTIFACT_MANIFEST_FILENAME,
    UBERON_OWNER_MARKER_FILENAME,
    UberonIndexManifest,
    build_uberon_index,
    download_uberon_artifact,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from ontolib.repositories.cadsr.build import ValidatedCadsrCandidate

logger = get_logger(__name__)
app = typer.Typer(help="Standalone data build for ontoprism.", no_args_is_help=True)


@app.command("icdo")
def build_icdo(
    source_directory: Annotated[Path, typer.Option(exists=True, file_okay=False)],
) -> None:
    """Validate and atomically publish all three certified ICD-O datasets."""
    settings = get_settings()
    old = ingest_icdo32_morphology(
        source_directory / "ICD-O-3.2_final_update09102020.xls"
    )
    new = ingest_icdo4(
        source_directory / "ICD-O-4.zip",
        morphology_annex_path=source_directory / "Morphology_annexes.xlsx",
        topography_annex_path=source_directory / "Topography_annexes.xlsx",
    )

    async def publish() -> None:
        engine = make_engine(settings.database_url)
        try:
            sessions = make_sessionmaker(engine)
            published_at = datetime.now(UTC)
            manifests = [
                await publish_dataset(
                    sessions,
                    old,
                    publisher_url="http://www.iacr.com.fr/index.php?option=com_content&view=category&layout=blog&id=100&Itemid=577",
                    published_at=published_at,
                ),
                await publish_dataset(
                    sessions,
                    new.morphology,
                    publisher_url="https://tumourclassification.iarc.who.int/icd-o-4/",
                    published_at=published_at,
                ),
                await publish_dataset(
                    sessions,
                    new.topography,
                    publisher_url="https://tumourclassification.iarc.who.int/icd-o-4/",
                    published_at=published_at,
                ),
            ]
            for manifest in manifests:
                typer.echo(
                    f"{manifest.edition}/{manifest.axis}: "
                    f"{manifest.generation_id} {manifest.serving_sha256}"
                )
        finally:
            await dispose_engine(engine)

    asyncio.run(publish())


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


async def _prepare_owl_artifacts() -> dict[str, Path]:
    """Download and certify the release-bound pair for offline store construction."""
    settings = get_settings()
    output_dir = Path(settings.ncit_owl_dir)
    pair = await download_ncit_owl_pair(
        output_dir,
        base_url=settings.ncit_owl_base_url,
        max_retries=settings.ncit_owl_max_retries,
    )
    if (
        not pair.success
        or pair.inferred is None
        or pair.inferred.file_path is None
        or pair.stated is None
        or pair.stated.file_path is None
        or pair.manifest_path is None
    ):
        raise RuntimeError(f"NCIt artifact-pair download failed: {pair.error}")
    return {
        "inferred": Path(pair.inferred.file_path),
        "stated": Path(pair.stated.file_path),
        "manifest": Path(pair.manifest_path),
    }


async def _build_owl() -> None:
    prepared = await _prepare_owl_artifacts()
    typer.echo(
        "Certified NCIt OWL artifact pair for #181 offline construction: "
        + ", ".join(f"{name}={path}" for name, path in prepared.items())
    )


async def _build_ncit_sibling() -> NcitSiblingStoreManifest:
    """Build one validated, inactive sibling from the current pair manifest."""
    settings = get_settings()
    manifest = await build_ncit_sibling_store(
        Path(settings.ncit_owl_dir) / PAIR_MANIFEST_FILENAME,
        active_store_path=Path(settings.ncit_store_dir),
        runtime=DockerQleverRuntime(),
    )
    typer.echo(
        "Certified inactive NCIt sibling: "
        f"candidate={manifest.candidate_path}, "
        f"source_identity={manifest.source_identity}"
    )
    return manifest


async def _build_initial_ncit() -> NcitSiblingStoreManifest:
    """Build the first NCIt QLever index, refusing any existing target path."""
    settings = get_settings()
    manifest = await build_initial_ncit_store(
        Path(settings.ncit_owl_dir) / PAIR_MANIFEST_FILENAME,
        active_store_path=Path(settings.ncit_store_dir),
        runtime=DockerQleverRuntime(),
    )
    typer.echo(
        "Certified initial NCIt QLever index: "
        f"target={manifest.candidate_path}, "
        f"source_identity={manifest.source_identity}"
    )
    return manifest


async def _activate_ncit(candidate_manifest_path: Path) -> ActivationJournal:
    """Activate one certified sibling through the durable #148 journal."""
    settings = get_settings()
    active_path = Path(settings.ncit_store_dir).resolve()
    journal_path, journal = prepare_activation_journal(
        candidate_manifest_path.resolve(),
        expected_active_path=active_path,
    )
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        if journal.phase == "preflight":
            projection = await capture_projection_plan(
                settings.ncit_sparql_url,
                ProvenanceStore(sf),
            )
            journal = bind_projection_plan(journal_path, journal, projection)

        def pause_publication():
            return replacing_corpus_source(sf, Corpus.NCIT)

        async def reconcile_projection(current: ActivationJournal) -> None:
            await reconcile_projection_at_endpoint(
                settings.ncit_sparql_url,
                current,
            )

        async def validate_candidate(current: ActivationJournal) -> None:
            await validate_active_store_health(
                settings.ncit_sparql_url,
                current,
                expected_source_identity=current.candidate_source_identity,
            )

        async def validate_rollback(current: ActivationJournal) -> None:
            await validate_active_store_health(
                settings.ncit_sparql_url,
                current,
                expected_source_identity=current.active_source_identity,
            )

        activated = await run_journaled_activation(
            journal_path,
            service=DockerComposeNcitService(
                project_directory=Path(__file__).resolve().parents[1],
                contract=QleverServiceContract(
                    service_name="qlever-ncit",
                    container_name="ontoprism-qlever-ncit",
                    image=journal.qlever_image,
                    image_id=journal.qlever_image_id,
                    index_version=journal.qlever_index_version,
                    index_basename=journal.qlever_index_basename,
                ),
            ),
            pause_publication=pause_publication,
            reconcile_projection=reconcile_projection,
            validate_health=validate_candidate,
            validate_rollback_health=validate_rollback,
        )
    finally:
        await dispose_engine(engine)
    typer.echo(
        "Activated certified NCIt sibling: "
        f"phase={activated.phase}, "
        f"source_identity={activated.candidate_source_identity}"
    )
    return activated


async def _build_uberon_store() -> UberonIndexManifest:
    """Download, certify, build, and initially install the Uberon/CL index."""
    settings = get_settings()
    source_dir = Path(settings.uberon_owl_dir)
    artifact = await download_uberon_artifact(
        source_dir,
        source_url=settings.uberon_owl_url,
        expected_version_iri=settings.uberon_expected_version_iri,
        expected_sha256=settings.uberon_expected_sha256,
        max_retries=settings.uberon_owl_max_retries,
    )
    runtime = DockerQleverRuntime(
        index_basename="uberon",
        owner_marker_filename=UBERON_OWNER_MARKER_FILENAME,
        server_memory="2G",
        server_cache="256M",
        server_allocator="256M",
    )
    manifest = await build_uberon_index(
        source_dir / UBERON_ARTIFACT_MANIFEST_FILENAME,
        Path(settings.uberon_store_dir),
        runtime=runtime,
    )
    typer.echo(
        "Certified Uberon/CL QLever index: "
        f"target={manifest.target_path}, "
        f"artifact_identity={artifact.artifact_identity}, "
        f"source_identity={manifest.source_identity}"
    )
    return manifest


def _cadsr_sidecars(destination: Path) -> list[Path]:
    return [
        destination.with_name(destination.name + suffix)
        for suffix in ("-journal", "-shm", "-wal")
        if destination.with_name(destination.name + suffix).exists()
    ]


def _cleanup_cadsr_candidate(
    candidate_path: Path, original: BaseException | None = None
) -> None:
    for suffix in ("", "-journal", "-shm", "-wal"):
        path = candidate_path.with_name(candidate_path.name + suffix)
        try:
            path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            if original is not None:
                original.add_note(
                    f"Failed to remove caDSR candidate artifact: {cleanup_error}"
                )
            else:
                logger.exception(
                    "caDSR replacement committed but candidate cleanup failed"
                )


async def _dispose_cadsr_engine(
    engine: AsyncEngine, original: BaseException | None = None
) -> None:
    task = asyncio.create_task(dispose_engine(engine))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    except Exception as dispose_error:
        if original is not None:
            original.add_note(f"Failed to dispose caDSR build engine: {dispose_error}")
        else:
            logger.exception(
                "caDSR database replacement committed but engine disposal failed"
            )


def _build_cadsr() -> None:
    settings = get_settings()
    data_dir = Path(settings.cadsr_data_dir)
    destination = Path(settings.cadsr_db_path)
    candidate_path = destination.with_name(
        f".{destination.name}.{uuid4().hex}.candidate"
    )

    async def _prepare() -> ValidatedCadsrCandidate:
        sidecars = _cadsr_sidecars(destination)
        if sidecars:
            raise RuntimeError(
                "refusing to replace caDSR database with SQLite sidecars: "
                + ", ".join(str(path) for path in sidecars)
            )
        outcome = await download_cadsr_cdes(
            data_dir, base_url=settings.cadsr_download_url
        )
        with extract_cadsr_archive(
            outcome,
            expected_url=settings.cadsr_download_url,
            workspace_parent=data_dir,
        ) as extracted:
            return build_database(extracted, candidate_path)

    def _replace_source(candidate: ValidatedCadsrCandidate) -> None:
        candidate.path.replace(destination)

    async def _run() -> int:
        engine = make_engine(settings.database_url)
        try:
            candidate = await coordinate_corpus_source_replacement(
                make_sessionmaker(engine),
                Corpus.CADSR,
                prepare=_prepare,
                replace=_replace_source,
            )
        except BaseException as original:
            await _dispose_cadsr_engine(engine, original)
            raise
        await _dispose_cadsr_engine(engine)
        return candidate.cde_count

    try:
        count = asyncio.run(_run())
    except BaseException as original:
        _cleanup_cadsr_candidate(candidate_path, original)
        raise
    _cleanup_cadsr_candidate(candidate_path)
    typer.echo(f"Built caDSR DB with {count} CDEs at {settings.cadsr_db_path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_commit(repo: Path | None = None) -> str:
    root = repo or Path.cwd()
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is required to identify the embedding build commit")
    status = subprocess.run(  # noqa: S603
        [executable, "status", "--porcelain"],
        cwd=root,
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
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"required executable is not on PATH: {name}")
    return executable


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
            f"source_identity={manifest.source_identity} "
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
    except Exception as record_error:
        original.add_note(f"Failed to record embedding build failure: {record_error}")


async def _publish_ncit_embeddings(
    build_id: UUID,
    *,
    restart: bool,
    embedder: Embedder | None = None,
) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        active_manifest = validate_ncit_sibling_manifest(
            Path(settings.ncit_store_dir) / CANDIDATE_MANIFEST_FILENAME
        )
        async with ncit_sparql_client(settings.ncit_sparql_url) as client:
            store = NcitGraphStore(client)
            expected, source_hash = await ncit_source_fingerprint(store)
            source_version = _require_ncit_source(
                await client.version(),
                expected,
                expected_version=settings.ncit_expected_version,
                expected_count=settings.ncit_embedding_expected_rows,
            )
            encoder = embedder or SentenceTransformerEmbedder()
            publisher = EmbeddingCorpusPublisher(
                sf,
                CorpusBuild(
                    build_id=build_id,
                    corpus=Corpus.NCIT,
                    source_identity=active_manifest.source_identity,
                    source_version=source_version,
                    source_hash=source_hash,
                    model_id=encoder.model_id,
                    model_revision=encoder.model_revision,
                    vector_dimension=EMBED_DIM,
                    expected_row_count=settings.ncit_embedding_expected_rows,
                    code_commit=_code_commit(),
                    required_doc_ids=("C3262",),
                ),
            )
            await publisher.start(restart=restart)
            try:
                staged_count, staged_hash = await stage_ncit_embeddings(
                    store, encoder, publisher
                )
                if (staged_count, staged_hash) != (expected, source_hash):
                    raise RuntimeError(
                        "NCIt staged records differ from validated source: "
                        f"{staged_count}/{staged_hash} != {expected}/{source_hash}"
                    )

                async def validate_source() -> None:
                    final_version = await client.version()
                    final_count, final_hash = await ncit_source_fingerprint(store)
                    _require_stable_ncit_source(
                        (source_version, expected, source_hash),
                        (final_version, final_count, final_hash),
                    )
                    # Refresh from the validated source while the same advisory lock
                    # excludes source replacement. FTS commits independently before
                    # embedding activation and always matches the current source.
                    await populate_from_store(
                        store,
                        NcitSearchIndex(sf),
                        source_identity=active_manifest.source_identity,
                        source_hash=source_hash,
                    )

                manifest = await publisher.publish(validate_source)
            except BaseException as exc:
                await _record_build_failure(publisher, exc)
                raise
    finally:
        await dispose_engine(engine)
    return manifest.actual_row_count or 0


async def _publish_cadsr_embeddings(
    build_id: UUID,
    *,
    restart: bool,
    embedder: Embedder | None = None,
) -> int:
    settings = get_settings()
    db_path = Path(settings.cadsr_db_path)
    if not db_path.is_file():
        raise RuntimeError(f"caDSR source database is missing: {db_path}")
    source = CdeRepository(db_path).source_provenance()
    expected, source_hash = cadsr_source_fingerprint(str(db_path))
    if expected != settings.cadsr_embedding_expected_rows:
        raise RuntimeError(
            "caDSR embedding source count does not match release expectation: "
            f"{expected} != {settings.cadsr_embedding_expected_rows}"
        )
    engine = make_engine(settings.database_url)
    try:
        sf = make_sessionmaker(engine)
        encoder = embedder or SentenceTransformerEmbedder()
        publisher = EmbeddingCorpusPublisher(
            sf,
            CorpusBuild(
                build_id=build_id,
                corpus=Corpus.CADSR,
                source_identity=source.archive_sha256,
                source_version=f"sha256:{source_hash}",
                source_hash=source_hash,
                model_id=encoder.model_id,
                model_revision=encoder.model_revision,
                vector_dimension=EMBED_DIM,
                expected_row_count=settings.cadsr_embedding_expected_rows,
                code_commit=_code_commit(),
                required_doc_ids=("2517527:4",),
            ),
        )
        await publisher.start(restart=restart)
        try:
            await stage_cde_embeddings(str(db_path), encoder, publisher)

            async def validate_source() -> None:
                final_count, final_hash = cadsr_source_fingerprint(str(db_path))
                _require_stable_cadsr_source(
                    (source_hash, expected), (final_hash, final_count)
                )

            manifest = await publisher.publish(validate_source)
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
            ncit_sparql_client(settings.ncit_sparql_url) as ncit_client,
            SparqlHttpClient.for_qlever(settings.uberon_sparql_url) as uberon_client,
        ):
            store = XrefStore(sf)
            metadata = RepositoryMetadataService(
                settings=settings,
                cadsr=CdeRepository(settings.cadsr_db_path),
            )
            ncit_ready = await metadata.ncit()
            uberon_ready = await metadata.uberon(force=True)
            if isinstance(ncit_ready, RepositoryUnhealthy) or isinstance(
                uberon_ready, RepositoryUnhealthy
            ):
                raise RuntimeError("candidate sources are not certified ready")

            async def observe_source_identities() -> tuple[str, str, str]:
                ncit_after = await metadata.ncit()
                uberon_after = await metadata.uberon(force=True)
                if isinstance(ncit_after, RepositoryUnhealthy) or isinstance(
                    uberon_after, RepositoryUnhealthy
                ):
                    raise RuntimeError("candidate source certification changed")
                return (
                    ncit_after.source_identity,
                    uberon_after.source_identity,
                    uberon_after.observation.serving.sha256,
                )

            report = await ingest_candidates(
                store,
                ncit_client,
                uberon_client,
                ncit_version=ncit_ready.release,
                uberon_version=uberon_ready.version_iri,
                ncit_source_identity=ncit_ready.source_identity,
                uberon_source_identity=uberon_ready.source_identity,
                uberon_serving_identity=uberon_ready.observation.serving.sha256,
                observe_source_identities=observe_source_identities,
            )
    finally:
        await dispose_engine(engine)
    typer.echo(f"xref candidates ingested: {report}")


async def _build_uberon_publisher_xrefs() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with (
            ncit_sparql_client(settings.ncit_sparql_url) as ncit_client,
            SparqlHttpClient.for_qlever(settings.uberon_sparql_url) as uberon_client,
        ):
            metadata = RepositoryMetadataService(
                settings=settings, cadsr=CdeRepository(settings.cadsr_db_path)
            )
            ncit_ready = await metadata.ncit()
            uberon_ready = await metadata.uberon(force=True)
            if isinstance(ncit_ready, RepositoryUnhealthy) or isinstance(
                uberon_ready, RepositoryUnhealthy
            ):
                raise RuntimeError("publisher xref sources are not certified ready")
            report = await publish_uberon_xrefs(
                XrefStore(make_sessionmaker(engine)),
                ncit_client,
                uberon_client,
                ncit_source_identity=ncit_ready.source_identity,
                uberon_source_identity=uberon_ready.source_identity,
                uberon_serving_identity=uberon_ready.observation.serving.sha256,
            )
    finally:
        await dispose_engine(engine)
    typer.echo(report.model_dump_json(indent=2))


async def _build_p334_alignments() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with ncit_sparql_client(settings.ncit_sparql_url) as ncit_client:
            sessions = make_sessionmaker(engine)
            metadata = RepositoryMetadataService(
                settings=settings, cadsr=CdeRepository(settings.cadsr_db_path)
            )
            ncit_ready = await metadata.ncit()
            if isinstance(ncit_ready, RepositoryUnhealthy):
                raise RuntimeError("P334 NCIt source is not certified ready")
            report = await publish_p334_alignments(
                XrefStore(sessions),
                ncit_client,
                IcdoRepository(sessions),
                icdo_expected=icdo_expectation(
                    settings, ServedIcdoDataset.ICDO_32_MORPHOLOGY
                ),
                ncit_source_identity=ncit_ready.source_identity,
            )
    finally:
        await dispose_engine(engine)
    typer.echo(report.model_dump_json(indent=2))


async def _build_xref_coverage() -> None:
    """Print the CDE-level coverage report and check for regression."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        async with ncit_sparql_client(settings.ncit_sparql_url) as client:
            store = XrefStore(sf)
            metadata = RepositoryMetadataService(
                settings=settings, cadsr=CdeRepository(settings.cadsr_db_path)
            )
            ncit_ready = await metadata.ncit()
            uberon_ready = await metadata.uberon(force=True)
            if isinstance(ncit_ready, RepositoryUnhealthy) or isinstance(
                uberon_ready, RepositoryUnhealthy
            ):
                raise RuntimeError("coverage sources are not certified ready")
            role_codes = await fetch_role_codes(client)
            report = await generate_coverage_report(
                settings.cadsr_db_path,
                store,
                client,
                expected=XrefReadPolicy(
                    uberon=UberonReadIdentity(
                        ncit_source_identity=ncit_ready.source_identity,
                        uberon_source_identity=uberon_ready.source_identity,
                        uberon_serving_identity=(
                            uberon_ready.observation.serving.sha256
                        ),
                    )
                ),
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


async def _endpoint_version(client: SparqlHttpClient) -> str | None:
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
    ncit_client: SparqlHttpClient, uberon_client: SparqlHttpClient
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
    _robot_dir, robot_identity = configured_robot_installation()
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    try:
        async with (
            ncit_sparql_client(settings.ncit_sparql_url) as ncit_client,
            SparqlHttpClient.for_qlever(settings.uberon_sparql_url) as uberon_client,
        ):
            metadata = RepositoryMetadataService(
                settings=settings, cadsr=CdeRepository(settings.cadsr_db_path)
            )
            ncit_ready = await metadata.ncit()
            uberon_ready = await metadata.uberon(force=True)
            if isinstance(ncit_ready, RepositoryUnhealthy) or isinstance(
                uberon_ready, RepositoryUnhealthy
            ):
                raise RuntimeError("promotion sources are not certified ready")
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
                tool_identity=robot_identity,
                source_metadata=UberonPromotionGenerationMetadata(
                    ncit_source_identity=ncit_ready.source_identity,
                    uberon_source_identity=uberon_ready.source_identity,
                    uberon_serving_identity=uberon_ready.observation.serving.sha256,
                ),
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
    """Download and certify the stated/inferred release pair for #181.

    Online store loading is disabled.
    """
    asyncio.run(_build_owl())


@app.command()
def cadsr() -> None:
    """Download the caDSR CDE archive and build the SQLite repository."""
    _build_cadsr()


@app.command(name="ncit-store")
def ncit_store() -> None:
    """Build and validate an inactive NCIt sibling; never activate it."""
    asyncio.run(_build_ncit_sibling())


@app.command(name="ncit-activate")
def ncit_activate(
    candidate_manifest: Path = typer.Option(  # noqa: B008 - typer option factory
        ...,
        "--candidate-manifest",
        help="Exact #181 candidate manifest to activate or resume.",
        metavar="PATH",
    ),
) -> None:
    """Journal, activate, validate, and recover one certified NCIt sibling."""
    asyncio.run(_activate_ncit(candidate_manifest))


@app.command(name="ncit-bootstrap")
def ncit_bootstrap() -> None:
    """Build the first NCIt QLever index; refuse any existing target."""
    asyncio.run(_build_initial_ncit())


@app.command(name="uberon-store")
def uberon_store() -> None:
    """Download and build the source-bound Uberon/CL QLever index."""
    asyncio.run(_build_uberon_store())


@app.command()
def embeddings(
    publish: bool = typer.Option(
        False,
        "--publish",
        help=(
            "Build, validate, and replace each selected corpus with ordered "
            "reconciliation."
        ),
    ),
    corpus: Corpus | None = typer.Option(  # noqa: B008 — typer option factory
        None,
        "--corpus",
        help="Publish only `ncit` or `cadsr`; default publishes each independently.",
    ),
) -> None:
    """Inspect all embedding build manifests; write only with explicit `--publish`."""
    asyncio.run(_build_embeddings(publish=publish, corpus=corpus, restart=False))


@app.command()
def xref() -> None:
    """Populate concept_xref with Uberon/CL candidate mappings."""
    asyncio.run(_build_xref())


@app.command(name="xref-coverage")
def xref_coverage() -> None:
    """Print the CDE-level caDSR coverage report (COV)."""
    asyncio.run(_build_xref_coverage())


@app.command(name="uberon-publisher-xrefs")
def uberon_publisher_xrefs() -> None:
    """Publish alignments labelled with releases observed from both active stores."""
    asyncio.run(_build_uberon_publisher_xrefs())


@app.command(name="p334-alignments")
def p334_alignments() -> None:
    """Publish P334 after certifying both observed active source identities."""
    asyncio.run(_build_p334_alignments())


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
    """Build indexes, start/migrate their services, then publish dependent data."""
    asyncio.run(_build_owl())
    settings = get_settings()
    ncit_target = Path(settings.ncit_store_dir)
    if ncit_target.exists() or ncit_target.is_symlink():
        asyncio.run(_build_ncit_sibling())
    else:
        asyncio.run(_build_initial_ncit())
    asyncio.run(_build_uberon_store())
    subprocess.run(  # noqa: S603 - resolved executable plus constant arguments
        [_required_executable("docker"), "compose", "up", "-d", "--wait"],
        check=True,
    )
    subprocess.run(  # noqa: S603 - resolved executable plus constant arguments
        [_required_executable("alembic"), "upgrade", "head"],
        check=True,
    )
    _build_cadsr()
    asyncio.run(_build_embeddings(publish=True, corpus=None, restart=False))


if __name__ == "__main__":
    app()
