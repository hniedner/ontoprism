#!/usr/bin/env python
"""Decomposition engine CLI (design §9 / §12 PR 5b).

  pdm run decompose --source-manifest data/.candidate/.ontoprism-ncit-candidate.json
  pdm run decompose --source-manifest data/.candidate/.ontoprism-ncit-candidate.json \
      --branch neoplasm --out data/ncit_decomposed.ttl --load
  pdm run decompose --source-manifest data/.candidate/.ontoprism-ncit-candidate.json \
      --branch disease --out data/ncit_decomposed.ttl
  pdm run decompose --source-manifest data/.candidate/.ontoprism-ncit-candidate.json \
      --branch neoplasm --resume neoplasm-7bb8b360-a2ec-45d0-b06d-a79ae18c3689

Wires the pure orchestrator (`ontolib.decomposition.run.run_pipeline`) to the real
QLever client, the Postgres provenance store, and `NcitGraphStore` for the concept
labels the NLP fallback needs. See ``run.py``'s module docstring for the documented
scope boundaries (genus-DAG role extraction, morphology-from-parent, source-bound
exact resume).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.core.logging_config import get_logger
from ontolib.decomposition import vocab
from ontolib.decomposition.branches import DecompositionBranch
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import NcitSourceSnapshot
from ontolib.decomposition.run import (
    RunConfig,
    RunMetrics,
    SourceIdentityChangedError,
    run_pipeline,
)
from ontolib.decomposition.sampling import load_sample_manifest
from ontolib.repositories.xref.vocab import NCIT_UPSTREAM_XREF_GRAPH_IRI
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.sibling_store import (
    observation_without_graphs,
    observe_ncit_candidate,
    validate_ncit_sibling_manifest,
)

logger = get_logger(__name__)

# Graphs ontoprism publishes into the same store; they are not part of the NCIt
# source identity and must not read as drift.
_ADDITIVE_GRAPH_IRIS = frozenset(
    {vocab.DECOMPOSED_GRAPH_IRI, NCIT_UPSTREAM_XREF_GRAPH_IRI}
)


def _make_label_lookup(store: NcitGraphStore):  # type: ignore[no-untyped-def]
    """Resolve an NLP surface form to an existing concept via an exact label match."""

    async def lookup(term: str) -> str | None:
        page = await store.search(term, limit=5)
        normalized = term.strip().lower()
        for hit in page.hits:
            if hit.label and hit.label.strip().lower() == normalized:
                return hit.code
        return None

    return lookup


async def _source_snapshot(
    manifest_path: Path,
    endpoint_url: str,
) -> NcitSourceSnapshot:
    """Bind the live endpoint to a freshly revalidated #181 candidate proof.

    Compares only the NCIt source: ontoprism's own additive publication graphs are
    ignored, otherwise a single `--load` would make every later run of the same
    manifest fail as source drift. Default/stated counts, versions, restrictions and
    the stated-only sentinels are still compared exactly.
    """
    manifest = validate_ncit_sibling_manifest(manifest_path)
    observed = observation_without_graphs(
        await observe_ncit_candidate(endpoint_url), _ADDITIVE_GRAPH_IRIS
    )
    expected = observation_without_graphs(manifest.observation, _ADDITIVE_GRAPH_IRIS)
    if observed.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise SourceIdentityChangedError(
            "live NCIt endpoint observation does not match the #181 candidate proof"
        )
    return NcitSourceSnapshot(
        source_identity=manifest.source_identity,
        ontology_version=manifest.ontology_version,
    )


async def _run(
    source_manifest: Path,
    branch: DecompositionBranch,
    out: Path | None,
    load: bool,
    emit_equivalence: bool,
    resume: str | None,
    total_limit: int | None,
    walker_max_depth: int = 5,
    sample_manifest: Path | None = None,
) -> RunMetrics:
    sample = (
        load_sample_manifest(sample_manifest) if sample_manifest is not None else None
    )
    config = RunConfig(
        branch=branch,
        out=out,
        load_to_store=load,
        emit_equivalence=emit_equivalence,
        resume_from=resume,
        walker_max_depth=walker_max_depth,
        sample_manifest=sample,
    )
    if sample is not None and total_limit is not None:
        raise ValueError("sample manifest and total_limit are mutually exclusive")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    provenance = ProvenanceStore(sf)
    primary_error: BaseException | None = None
    try:
        try:
            try:
                async with ncit_sparql_client(settings.ncit_sparql_url) as client:
                    store = NcitGraphStore(client)
                    try:
                        metrics = await run_pipeline(
                            config,
                            client,
                            provenance,
                            get_source_snapshot=lambda: _source_snapshot(
                                source_manifest,
                                settings.ncit_sparql_url,
                            ),
                            get_labels=store.labels_for,
                            label_lookup=_make_label_lookup(store),
                            total_limit=total_limit,
                        )
                    except BaseException as exc:
                        primary_error = exc
                        raise
            except BaseException as exc:
                if primary_error is not None and exc is not primary_error:
                    primary_error.add_note(
                        "Closing the NCIt client also failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    raise primary_error from exc
                raise
        except Exception:
            logger.exception(
                "decompose run failed (branch=%s resume=%s)", branch, resume
            )
            raise
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            await dispose_engine(engine)
        except BaseException as cleanup_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Disposing the decomposition database engine also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
            logger.exception("dispose_engine failed during cleanup (branch=%s)", branch)
    return metrics


def main(
    source_manifest: Annotated[
        Path,
        typer.Option(
            "--source-manifest",
            help="Validated #181 inactive-candidate manifest for this endpoint.",
        ),
    ],
    branch: Annotated[
        DecompositionBranch,
        typer.Option(
            help=(
                "Hierarchy population: neoplasm (C3262 descendants) or disease "
                "(C2991 descendants). Regimen remains unavailable until its "
                "component-bag algorithm is implemented."
            )
        ),
    ] = DecompositionBranch.NEOPLASM,
    out: Annotated[
        Path | None, typer.Option(help="Write the decomposed TTL here.")
    ] = None,
    load: Annotated[
        bool,
        typer.Option(
            "--load",
            help=(
                "Publish staged TTL to the decomposed graph before finalizing --out."
            ),
        ),
    ] = False,
    emit_equivalence: Annotated[
        bool,
        typer.Option(
            "--emit-equivalence",
            help=(
                "Reserved until a separate validation step proves exact "
                "equivalence; requests currently fail closed."
            ),
        ),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume",
            help="Matching running/failed exact-worklist run id to resume.",
        ),
    ] = None,
    total_limit: Annotated[
        int | None,
        typer.Option(
            help=(
                "Cap how many enumerated codes are processed (smoke runs; cannot be "
                "combined with --load)."
            )
        ),
    ] = None,
    walker_max_depth: Annotated[
        int,
        typer.Option(
            "--walker-max-depth",
            help="Genus-chain walker recursion depth (default 5).",
        ),
    ] = 5,
    sample_manifest: Annotated[
        Path | None,
        typer.Option(
            "--sample-manifest",
            help=(
                "Run an explicit source-bound review sample. Requires --out and "
                "cannot be combined with --load or --total-limit."
            ),
        ),
    ] = None,
) -> None:
    """Run the decomposition pipeline for a branch and print its coverage metrics."""
    if emit_equivalence:
        raise typer.BadParameter(
            "--emit-equivalence is not available until a separate validation step "
            "can establish exact completeness"
        )
    if load and out is None:
        raise typer.BadParameter("--load requires --out")
    if load and total_limit is not None:
        raise typer.BadParameter("--load cannot be combined with --total-limit")
    if sample_manifest is not None:
        if out is None:
            raise typer.BadParameter("--sample-manifest requires --out")
        if load:
            raise typer.BadParameter("--sample-manifest cannot be combined with --load")
        if total_limit is not None:
            raise typer.BadParameter(
                "--sample-manifest and --total-limit are mutually exclusive"
            )
    metrics = asyncio.run(
        _run(
            source_manifest,
            branch,
            out,
            load,
            emit_equivalence,
            resume,
            total_limit,
            walker_max_depth,
            sample_manifest,
        )
    )
    typer.echo(
        f"in_scope={metrics.total_in_scope} decomposed={metrics.decomposed} "
        f"residual={metrics.residual} "
        f"semantic_excluded={metrics.semantic_excluded} "
        f"atomic_noop={metrics.atomic_noop} unknown={metrics.unknown_outcome} "
        f"minted={metrics.minted_count} "
        f"coverage={metrics.coverage:.2%} "
        # detector-relative (D37): reducibility as the detector sees it (not truth)
        f"residual_precoordination={metrics.residual_precoordination:.2%} "
        f"({metrics.residual_precoordinated_count}/{metrics.decomposed})"
    )


if __name__ == "__main__":
    typer.run(main)
