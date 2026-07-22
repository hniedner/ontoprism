#!/usr/bin/env python
"""Decomposition engine CLI (design §9 / §12 PR 5b).

  pdm run decompose --branch neoplasm
  pdm run decompose --branch neoplasm --out data/ncit_decomposed.ttl --load
  pdm run decompose --branch neoplasm --resume neoplasm-2026-07-08T00:00:00

Wires the pure orchestrator (`ontolib.decomposition.run.run_pipeline`) to the real
Oxigraph client, the Postgres provenance store, and `NcitGraphStore` for the concept
labels the NLP fallback needs. See ``run.py``'s module docstring for the documented
scope boundaries (flat-restriction extraction only, no morphology-from-parent yet,
best-effort resume).
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
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.run import RunConfig, RunMetrics, run_pipeline
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

logger = get_logger(__name__)


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


async def _run(
    branch: str,
    out: Path | None,
    load: bool,
    emit_equivalence: bool,
    resume: str | None,
    total_limit: int | None,
    walker_max_depth: int = 5,
) -> RunMetrics:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_sessionmaker(engine)
    provenance = ProvenanceStore(sf)
    config = RunConfig(
        branch=branch,
        out=out,
        load_to_store=load,
        emit_equivalence=emit_equivalence,
        resume_from=resume,
        walker_max_depth=walker_max_depth,
    )
    try:
        async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
            store = NcitGraphStore(client)
            try:
                metrics = await run_pipeline(
                    config,
                    client,
                    provenance,
                    get_labels=store.labels_for,
                    label_lookup=_make_label_lookup(store),
                    total_limit=total_limit,
                )
            except Exception:
                logger.exception(
                    "decompose run failed (branch=%s resume=%s)", branch, resume
                )
                raise
            if load:
                # out is None already rejected in main() before any work started.
                await client.load(
                    out.read_bytes(),  # type: ignore[union-attr]
                    content_type="text/turtle",
                    graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
                    replace=True,
                )
    finally:
        try:
            await dispose_engine(engine)
        except Exception:
            # Never let a cleanup-time failure replace/mask the pipeline's own
            # exception (if any) propagating out of the `try` block above.
            logger.exception("dispose_engine failed during cleanup (branch=%s)", branch)
    return metrics


def main(
    branch: Annotated[
        str, typer.Option(help="Run label ('neoplasm' | 'disease').")
    ] = "neoplasm",
    out: Annotated[
        Path | None, typer.Option(help="Write the decomposed TTL here.")
    ] = None,
    load: Annotated[
        bool,
        typer.Option(
            "--load", help="Load --out into the decomposed named graph after writing."
        ),
    ] = False,
    emit_equivalence: Annotated[
        bool,
        typer.Option(
            "--emit-equivalence",
            help="Emit owl:equivalentClass intersection axioms (lossless round-trip).",
        ),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Run id to resume (best-effort — see run.py)."),
    ] = None,
    total_limit: Annotated[
        int | None,
        typer.Option(help="Cap how many enumerated codes are processed (smoke runs)."),
    ] = None,
    walker_max_depth: Annotated[
        int,
        typer.Option(
            "--walker-max-depth",
            help="Genus-chain walker recursion depth (default 5).",
        ),
    ] = 5,
) -> None:
    """Run the decomposition pipeline for a branch and print its coverage metrics."""
    if load and out is None:
        raise typer.BadParameter("--load requires --out")
    metrics = asyncio.run(
        _run(branch, out, load, emit_equivalence, resume, total_limit, walker_max_depth)
    )
    typer.echo(
        f"in_scope={metrics.total_in_scope} decomposed={metrics.decomposed} "
        f"residual={metrics.residual} minted={metrics.minted_count} "
        f"coverage={metrics.coverage:.2%} "
        # detector-relative (D37): reducibility as the detector sees it (not truth)
        f"residual_precoordination={metrics.residual_precoordination:.2%} "
        f"({metrics.residual_precoordinated_count}/{metrics.decomposed})"
    )


if __name__ == "__main__":
    typer.run(main)
