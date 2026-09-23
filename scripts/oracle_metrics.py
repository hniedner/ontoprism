#!/usr/bin/env python3
"""Run the current engine on the D63 cohort and print the five D74 views."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import typer
from scripts.decompose import _run
from scripts.research.current_evidence import (
    CurrentComparison,
    CurrentEngineEvidence,
    build_current_comparison,
    generate_rehearsal_oracle_evidence,
)
from scripts.research.golden_review import (
    AdjudicationArtifact,
    RowDecisionExport,
    load_migrated_historical_adjudication,
    load_row_decisions,
)

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.branches import DecompositionBranch
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.proposal_registry import (
    ProposalRegistry,
    load_proposal_registry,
)
from ontolib.decomposition.provenance import ProvenanceStore

_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN = _ROOT / "ontolib/tests/decomposition/golden"
_SOURCE = _ROOT / "data/qlever-ncit/.ontoprism-ncit-candidate.json"
_SAMPLE = _ROOT / "samples/ncit-26.07d-m1-current-replay.json"
_ORACLE = _GOLDEN / "neoplasm-adjudicated.json"
_ROWS = _GOLDEN / "neoplasm-row-decisions.json"
_REGISTRY = _GOLDEN / "proposal-registry.json"
_MIGRATION = _GOLDEN / "proposal-registry-schema2-migration.json"


def _rate(name: str, numerator: int, denominator: int, rate: float | None) -> str:
    value = "not-computed" if rate is None else f"{rate:.6f}"
    return f"{name}={numerator}/{denominator} ({value})"


def oracle_metrics_report(
    evidence: CurrentEngineEvidence,
    oracle: AdjudicationArtifact,
    rows: RowDecisionExport,
    registry: ProposalRegistry,
) -> str:
    """Render the five independent D74 views for one current engine output."""
    comparison = build_current_comparison(evidence, oracle, rows, registry)
    metrics = comparison.metrics
    historical = rows.cross_tab().engine_suggestion
    common = metrics.common_pair_partition_agreement
    return "\n".join(
        (
            _rate(
                "historical_sme_include_rate",
                historical.include,
                historical.adjudicated,
                historical.included_rate,
            )
            + " [historical]",
            _rate(
                "exact_pair_precision",
                metrics.exact_pair_precision.numerator,
                metrics.exact_pair_precision.denominator,
                metrics.exact_pair_precision.rate,
            ),
            _rate(
                "exact_pair_recall",
                metrics.exact_pair_recall.numerator,
                metrics.exact_pair_recall.denominator,
                metrics.exact_pair_recall.rate,
            ),
            _rate(
                "full_partition_agreement",
                metrics.full_partition_agreement.numerator,
                metrics.full_partition_agreement.denominator,
                metrics.full_partition_agreement.rate,
            ),
            _rate(
                "common_pair_partition_agreement",
                common.numerator,
                common.denominator,
                common.rate,
            )
            + f" [ineligible={common.ineligible}]",
        )
    )


async def _execute() -> str:
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="ontoprism-oracle-metrics-") as directory:
        temporary = Path(directory)
        artifact = temporary / "decomposition.ttl"
        run_id: str | None = None

        def capture_run(progress: object) -> None:
            nonlocal run_id
            observed = getattr(progress, "run_id", None)
            if isinstance(observed, str):
                run_id = observed

        await _run(
            source_manifest=_SOURCE,
            branch=DecompositionBranch.NEOPLASM,
            out=artifact,
            load=False,
            emit_equivalence=False,
            resume=None,
            total_limit=None,
            walker_max_depth=7,
            sample_manifest=_SAMPLE,
            rehearsal=True,
            progress=capture_run,
        )
        if run_id is None:
            raise RuntimeError("rehearsal did not report its run id")
        engine = make_engine(settings.database_url)
        try:
            store = ProvenanceStore(make_sessionmaker(engine))
            rehearsal = await store.completed_rehearsal_for_oracle_metrics(run_id)
            await write_ttl(
                await store.decompositions_for_run(run_id),
                dest=artifact,
                run_id=run_id,
                emitted_on=rehearsal.fingerprint.emitted_at.date(),
            )
            engine_output = temporary / "engine-evidence.json"
            comparison_output = temporary / "comparison.json"
            evidence, _comparison = await generate_rehearsal_oracle_evidence(
                sample_manifest=_SAMPLE,
                oracle=_ORACLE,
                row_decisions=_ROWS,
                proposal_registry=_REGISTRY,
                proposal_registry_migration=_MIGRATION,
                run_id=run_id,
                artifact=artifact,
                engine_output=engine_output,
                comparison_output=comparison_output,
                store=store,
            )
            CurrentComparison.model_validate_json(comparison_output.read_bytes())
            oracle = load_migrated_historical_adjudication(
                _ORACLE, _REGISTRY, _MIGRATION
            )
            rows = load_row_decisions(_ROWS)
            registry = load_proposal_registry(_REGISTRY)
            conservation = await store.r101_conservation_counts(run_id)
            return (
                f"run_id={run_id}\n"
                + oracle_metrics_report(evidence, oracle, rows, registry)
                + "\n"
                + "r101_conservation="
                + conservation.model_dump_json()
            )
        finally:
            await dispose_engine(engine)


def main() -> None:
    """Run and score the current-source 20-concept rehearsal."""
    typer.echo(asyncio.run(_execute()))


if __name__ == "__main__":
    typer.run(main)
