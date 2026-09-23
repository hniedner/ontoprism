#!/usr/bin/env python
"""Decomposition engine CLI (design §9 / §12 PR 5b).

  pdm run decompose --source-manifest data/.candidate/.ontoprism-ncit-candidate.json
  pdm run decompose --source-manifest data/.candidate/.ontoprism-ncit-candidate.json \
      --branch neoplasm --out data/ncit_decomposed.ttl --load
  pdm run decompose --source-manifest data/.candidate/.ontoprism-ncit-candidate.json \
      --branch disease --out data/ncit_decomposed.ttl
  pdm run decompose --source-manifest data/.candidate/.ontoprism-ncit-candidate.json \
      --branch neoplasm --resume neoplasm-7bb8b360-a2ec-45d0-b06d-a79ae18c3689

A full run is preceded by a preflight rehearsal of the tracked stratified SME sample
through the same pipeline and reporting (``--no-preflight`` skips it).

Wires the pure orchestrator (`ontolib.decomposition.run.run_pipeline`) to the real
QLever client, the Postgres provenance store, and `NcitGraphStore` for the concept
labels the NLP fallback needs. See ``run.py``'s module docstring for the documented
scope boundaries (genus-DAG role extraction, morphology-from-parent, source-bound
exact resume).
"""

from __future__ import annotations

import asyncio
import sys
from functools import partial
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
    ProgressCallback,
    RunConfig,
    RunMetrics,
    RunProgress,
    SourceIdentityChangedError,
    run_pipeline,
)
from ontolib.decomposition.sampling import load_sample_manifest
from ontolib.repositories.xref.vocab import NCIT_UPSTREAM_XREF_GRAPH_IRI
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.search_index import NcitSearchIndex
from ontolib.terminologies.ncit.sibling_store import (
    observation_without_graphs,
    observe_ncit_candidate,
    validate_ncit_sibling_manifest,
)

logger = get_logger(__name__)

_PROGRESS_COMPLETION_INTERVAL = 100

# Graphs ontoprism publishes into the same store; they are not part of the NCIt
# source identity and must not read as drift.
_ADDITIVE_GRAPH_IRIS = frozenset(
    {vocab.DECOMPOSED_GRAPH_IRI, NCIT_UPSTREAM_XREF_GRAPH_IRI}
)


# A full run takes about fifteen hours. Before one starts, the branch's tracked
# stratified SME sample (for neoplasm the D63 oracle cohort: 20 concepts across every
# review stratum) is rehearsed through the same pipeline and the same reporting, so an
# input, environment or reporting defect surfaces well before the full run does the
# work. The rehearsal is a throwaway run: admitted afresh each time, never published,
# never promoting its mints, never resumable, and it borrows only the sample's codes
# (the sample's source binding is recorded, not enforced). It cannot carry the
# mixed-chain inventory or the whole-worklist closure checks, which are bound to the
# full worklist; those still run at hour zero of the full run. This is distinct from
# the engine's own `preflight` stage, the constructor census every run performs.
PREFLIGHT_SAMPLES = {
    DecompositionBranch.NEOPLASM: (
        Path(__file__).resolve().parents[1] / "samples/ncit-26.07d-m1-sme-review.json"
    ),
}


def _make_label_lookup(index: NcitSearchIndex):  # type: ignore[no-untyped-def]
    """Resolve an NLP surface form to an existing concept via an exact label match."""

    async def lookup(term: str) -> str | None:
        page = await index.search(term, limit=5)
        normalized = term.strip().lower()
        for hit in page.hits:
            if hit.label and hit.label.strip().lower() == normalized:
                return hit.code
        return None

    return lookup


def _progress_message(progress: RunProgress) -> str | None:
    visible = (
        progress.phase == "heartbeat"
        or (progress.phase == "started" and progress.session_completed == 0)
        or (
            progress.phase == "completed"
            and (
                progress.completed == progress.total
                or progress.completed % _PROGRESS_COMPLETION_INTERVAL == 0
            )
        )
    )
    if not visible:
        return None
    rate = (
        progress.session_completed / progress.elapsed_seconds
        if progress.elapsed_seconds > 0
        else 0.0
    )
    remaining = progress.total - progress.completed
    eta = remaining / rate if rate > 0 else None
    eta_text = f"{eta:.0f}s" if eta is not None else "unknown"
    return (
        f"run={progress.run_id} phase={progress.phase} "
        f"completed={progress.completed}/{progress.total} "
        f"active={progress.concept_code} elapsed={progress.elapsed_seconds:.0f}s "
        f"rate={rate:.2f}/s eta={eta_text}"
    )


def _print_progress(progress: RunProgress, *, prefix: str = "") -> None:
    if message := _progress_message(progress):
        print(prefix + message, file=sys.stderr, flush=True)


def _print_residual_progress(
    completed: int, total: int, filler: str, *, prefix: str = ""
) -> None:
    if completed in (0, total) or completed % 100 == 0:
        print(
            f"{prefix}phase=residual-metric completed={completed}/{total} "
            f"active={filler}",
            file=sys.stderr,
            flush=True,
        )


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
    rehearsal: bool = False,
    progress: ProgressCallback | None = None,
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
        rehearsal=rehearsal,
        # The inventory is bound to the whole-corpus worklist identity, so only an
        # unbounded neoplasm run carries it.
        mixed_chain_inventory_path=(
            Path(__file__).resolve().parents[1]
            / "ontolib/src/ontolib/decomposition/data/"
            "neoplasm_mixed_chain_inventory.json"
            if branch is DecompositionBranch.NEOPLASM
            and sample is None
            and total_limit is None
            else None
        ),
    )
    if sample is not None and total_limit is not None:
        raise ValueError("sample manifest and total_limit are mutually exclusive")
    prefix = "preflight " if rehearsal else ""
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
                            label_lookup=_make_label_lookup(NcitSearchIndex(sf)),
                            total_limit=total_limit,
                            progress=(
                                progress
                                if progress is not None
                                else partial(_print_progress, prefix=prefix)
                            ),
                            residual_progress=partial(
                                _print_residual_progress, prefix=prefix
                            ),
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
    preflight: Annotated[
        bool,
        typer.Option(
            "--preflight/--no-preflight",
            help=(
                "Before a full run, rehearse the branch's tracked stratified SME "
                "sample through the same pipeline (never loading the graph) and stop "
                "if it fails or decomposes nothing."
            ),
        ),
    ] = True,
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
    is_full_run = resume is None and total_limit is None and sample_manifest is None
    if preflight and is_full_run:
        _rehearse(
            source_manifest=source_manifest,
            branch=branch,
            emit_equivalence=emit_equivalence,
            walker_max_depth=walker_max_depth,
        )
    metrics = asyncio.run(
        _run(
            source_manifest=source_manifest,
            branch=branch,
            out=out,
            load=load,
            emit_equivalence=emit_equivalence,
            resume=resume,
            total_limit=total_limit,
            walker_max_depth=walker_max_depth,
            sample_manifest=sample_manifest,
            rehearsal=False,
        )
    )
    typer.echo(_summary_line(metrics))


def _rehearse(
    *,
    source_manifest: Path,
    branch: DecompositionBranch,
    emit_equivalence: bool,
    walker_max_depth: int,
) -> None:
    """Run the branch's preflight sample as an unpublished rehearsal."""
    sample = PREFLIGHT_SAMPLES.get(branch)
    if sample is None:
        raise typer.BadParameter(
            f"no preflight sample is tracked for branch {branch.value!r}; "
            "pass --no-preflight"
        )
    try:
        metrics = asyncio.run(
            _run(
                source_manifest=source_manifest,
                branch=branch,
                out=None,
                load=False,
                emit_equivalence=emit_equivalence,
                resume=None,
                total_limit=None,
                walker_max_depth=walker_max_depth,
                sample_manifest=sample,
                rehearsal=True,
            )
        )
        if metrics.decomposed == 0:
            raise RuntimeError(
                f"preflight decomposed no concepts: {_summary_line(metrics)}"
            )
    except BaseException as exc:
        exc.add_note(
            f"raised by the preflight rehearsal of {sample}; the full run was not "
            "started; --no-preflight skips it"
        )
        raise
    typer.echo(f"preflight: {_summary_line(metrics)}")


def _summary_line(metrics: RunMetrics) -> str:
    residual_rate = metrics.residual_precoordination
    residual_summary = (
        f"{residual_rate:.2%} "
        f"({metrics.residual_precoordinated_count}/{metrics.decomposed})"
        if residual_rate is not None
        else f"unavailable (unknown={metrics.residual_precoordination_unknown_count})"
    )
    return (
        f"in_scope={metrics.total_in_scope} decomposed={metrics.decomposed} "
        f"residual={metrics.residual} "
        f"semantic_excluded={metrics.semantic_excluded} "
        f"atomic_noop={metrics.atomic_noop} unknown={metrics.unknown_outcome} "
        f"minted={metrics.minted_count} "
        f"coverage={metrics.coverage:.2%} "
        # detector-relative (D37): reducibility as the detector sees it (not truth)
        f"residual_precoordination={residual_summary}"
    )


if __name__ == "__main__":
    typer.run(main)
