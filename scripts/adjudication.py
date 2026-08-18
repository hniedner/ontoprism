#!/usr/bin/env python3
"""Import, export, and evaluate provenance-bound SME decomposition adjudication."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Protocol, cast

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.branches import DecompositionBranch
from ontolib.decomposition.corpus_baseline import (
    generate_corpus_baseline,
    write_corpus_baseline,
)
from ontolib.decomposition.extract import semantic_type_of_from_rows
from ontolib.decomposition.pre_resume import (
    generate_pre_resume_proof,
    semantic_dependency_identity,
    write_pre_resume_proof,
)
from ontolib.decomposition.proposal_registry import (
    load_proposal_registry,
    write_submission_exports,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.r101_conservation import (
    R101ConservationProgress,
    generate_r101_conservation_report,
    require_authorizable_r101_report,
    write_r101_conservation_report,
)
from ontolib.decomposition.resume_dry_run import (
    build_resume_dry_run,
    inspect_resume_selection,
    load_pre_resume_proof,
    write_resume_dry_run,
)
from ontolib.decomposition.run import RunConfig, build_resume_identity
from ontolib.decomposition.stated_queries import (
    build_semantic_type_of_query,
    resolve_part_of_pairs,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.sibling_store import validate_ncit_sibling_manifest

try:
    from scripts.research.golden_review import (
        evaluate_adjudication,
        export_row_decisions,
        import_adjudication_workbook,
        load_adjudication,
        read_json_without_duplicates,
        write_canonical_json,
        write_evaluation_report,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.golden_review import (
        evaluate_adjudication,
        export_row_decisions,
        import_adjudication_workbook,
        load_adjudication,
        read_json_without_duplicates,
        write_canonical_json,
        write_evaluation_report,
    )

try:
    from scripts.research.current_evidence import generate_current_evidence
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.current_evidence import generate_current_evidence

try:
    from scripts.decompose import _source_snapshot
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from decompose import _source_snapshot


def _write_artifact(workbook: Path, registry: Path, output: Path) -> None:
    artifact = import_adjudication_workbook(
        workbook,
        load_proposal_registry(registry),
    )
    write_canonical_json(artifact.model_dump(mode="json", by_alias=True), output)


def _write_row_decisions(workbook: Path, output: Path) -> None:
    export = export_row_decisions(workbook)
    write_canonical_json(export.model_dump(mode="json", by_alias=True), output)


def _evaluate(
    adjudication: Path,
    engine_evidence: Path,
    corpus_comparison: Path,
    registry: Path,
    output: Path,
) -> None:
    engine = read_json_without_duplicates(engine_evidence)
    corpus = read_json_without_duplicates(corpus_comparison)
    report = evaluate_adjudication(
        load_adjudication(adjudication, load_proposal_registry(registry)),
        engine,
        corpus,
    )
    write_evaluation_report(report, output)


def _export_proposals(registry: Path, output_directory: Path) -> None:
    write_submission_exports(load_proposal_registry(registry), output_directory)


class _CurrentEvidenceArgs(Protocol):
    sample_manifest: Path
    oracle: Path
    row_decisions: Path
    proposal_registry: Path
    run_id: str
    artifact: Path
    engine_output: Path
    comparison_output: Path


class _CorpusBaselineArgs(Protocol):
    source_manifest: Path
    run_id: str
    artifact: Path
    output: Path


class _R101ConservationArgs(Protocol):
    source_manifest: Path
    baseline: Path
    run_id: str
    new_run_id: str
    endpoint: str
    output: Path
    pre_resume_proof_identity: str
    resume_dry_run_identity: str
    mixed_cohort_identity: str


class _PreResumeArgs(Protocol):
    source_manifest: Path
    run_id: str
    endpoint: str
    output: Path


class _ResumeDryRunArgs(Protocol):
    source_manifest: Path
    proof: Path
    run_id: str
    endpoint: str
    branch: str
    walker_max_depth: int
    out: Path
    output: Path


def _print_r101_progress(progress: R101ConservationProgress) -> None:
    visible = (
        progress.phase == "heartbeat"
        or (progress.phase == "started" and progress.completed == 0)
        or (
            progress.phase == "completed"
            and (progress.completed == progress.total or progress.completed % 100 == 0)
        )
    )
    if not visible:
        return
    rate = (
        progress.completed / progress.elapsed_seconds if progress.elapsed_seconds else 0
    )
    remaining = progress.total - progress.completed
    eta = remaining / rate if rate else None
    eta_text = f"{eta:.0f}s" if eta is not None else "unknown"
    print(
        f"phase=r101-conservation event={progress.phase} "
        f"completed={progress.completed}/{progress.total} "
        f"active={progress.concept_code} rate={rate:.2f}/s eta={eta_text}",
        file=sys.stderr,
        flush=True,
    )


async def _generate_current(args: _CurrentEvidenceArgs) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        await generate_current_evidence(
            sample_manifest=args.sample_manifest,
            oracle=args.oracle,
            row_decisions=args.row_decisions,
            proposal_registry=args.proposal_registry,
            run_id=args.run_id,
            artifact=args.artifact,
            engine_output=args.engine_output,
            comparison_output=args.comparison_output,
            store=ProvenanceStore(make_sessionmaker(engine)),
        )
    finally:
        await dispose_engine(engine)


async def _generate_corpus(args: _CorpusBaselineArgs) -> None:
    manifest = validate_ncit_sibling_manifest(args.source_manifest)
    engine = make_engine(get_settings().database_url)
    try:
        baseline = await generate_corpus_baseline(
            run_id=args.run_id,
            artifact=args.artifact,
            store=ProvenanceStore(make_sessionmaker(engine)),
            expected_source_identity=manifest.source_identity,
            expected_release=manifest.ontology_version,
        )
        write_corpus_baseline(args.output, baseline)
    finally:
        await dispose_engine(engine)


async def _generate_r101_conservation(args: _R101ConservationArgs) -> None:
    manifest = validate_ncit_sibling_manifest(args.source_manifest)
    engine = make_engine(get_settings().database_url)
    try:
        async with ncit_sparql_client(args.endpoint, query_timeout=180.0) as client:
            source = await _source_snapshot(args.source_manifest, args.endpoint)
            if (
                source.source_identity != manifest.source_identity
                or source.ontology_version != manifest.ontology_version
            ):
                raise ValueError(
                    "live source does not match the explicit source manifest"
                )

            async def semantic_types(
                codes: tuple[str, ...],
            ) -> dict[str, str | None]:
                all_semantic_types: dict[str, list[str]] = {}
                for start in range(0, len(codes), 256):
                    batch = list(codes[start : start + 256])
                    rows = await client.select(
                        build_semantic_type_of_query(batch),
                        required_variables={"code", "st"},
                    )
                    all_semantic_types.update(semantic_type_of_from_rows(rows))
                return {
                    code: min(all_semantic_types[code])
                    if all_semantic_types.get(code)
                    else None
                    for code in codes
                }

            async def live_r82(
                codes: tuple[str, ...],
            ) -> tuple[tuple[str, str], ...]:
                return tuple(
                    (pair.part, pair.whole)
                    for pair in await resolve_part_of_pairs(client, codes)
                )

            report = await generate_r101_conservation_report(
                baseline_path=args.baseline,
                run_id=args.run_id,
                new_run_id=args.new_run_id,
                expected_source_identity=manifest.source_identity,
                expected_release=manifest.ontology_version,
                pre_resume_proof_identity=args.pre_resume_proof_identity,
                resume_dry_run_identity=args.resume_dry_run_identity,
                mixed_cohort_identity=args.mixed_cohort_identity,
                store=ProvenanceStore(make_sessionmaker(engine)),
                resolve_semantic_types=semantic_types,
                resolve_live_r82=live_r82,
                progress=_print_r101_progress,
            )
            write_r101_conservation_report(args.output, report)
            require_authorizable_r101_report(report)
    finally:
        await dispose_engine(engine)


async def _generate_pre_resume(args: _PreResumeArgs) -> None:
    source = await _source_snapshot(args.source_manifest, args.endpoint)
    engine = make_engine(get_settings().database_url)
    try:
        async with ncit_sparql_client(args.endpoint) as client:
            payload = await generate_pre_resume_proof(
                engine=engine,
                run_id=args.run_id,
                client=client,
                repo_root=Path(__file__).resolve().parent.parent,
                live_source_identity=source.source_identity,
                live_release=source.ontology_version,
                source_observation_reads=9,
            )
        write_pre_resume_proof(args.output, payload)
        print(
            f"postgres_reads={payload['postgres_reads']} "
            f"qlever_reads={payload['qlever_reads']}",
            file=sys.stderr,
        )
    finally:
        await dispose_engine(engine)


async def _dry_run_resume(args: _ResumeDryRunArgs) -> None:
    proof = load_pre_resume_proof(args.proof)
    source = await _source_snapshot(args.source_manifest, args.endpoint)
    config = RunConfig(
        branch=DecompositionBranch(args.branch),
        out=args.out,
        resume_from=args.run_id,
        walker_max_depth=args.walker_max_depth,
    )
    expected_identity = build_resume_identity(
        config,
        source,
        semantic_types=config.semantic_types,
        total_limit=None,
    )
    engine = make_engine(get_settings().database_url)
    try:
        selection, failure = await inspect_resume_selection(
            engine, args.run_id, expected_identity
        )
        semantic_identity, _ = semantic_dependency_identity(
            Path(__file__).resolve().parent.parent
        )
        payload = build_resume_dry_run(
            run_id=args.run_id,
            proof=proof,
            semantic_identity=semantic_identity,
            output_path=args.out,
            selection=selection,
            status=failure[0],
            error_type=failure[1],
            error_message=failure[2],
            qlever_reads=9,
            artifact_path=args.output,
        )
        write_resume_dry_run(args.output, payload)
        print(
            f"identity={payload['identity']} "
            f"postgres_reads={payload['postgres_reads']} "
            f"qlever_reads={payload['qlever_reads']}",
            file=sys.stderr,
        )
    finally:
        await dispose_engine(engine)


def _add_resume_dry_run_parser(subparsers: Any) -> None:
    resume_parser = subparsers.add_parser("dry-run-resume")
    resume_parser.add_argument("--source-manifest", required=True, type=Path)
    resume_parser.add_argument("--proof", required=True, type=Path)
    resume_parser.add_argument("--run-id", required=True)
    resume_parser.add_argument("--endpoint", required=True)
    resume_parser.add_argument(
        "--branch",
        required=True,
        choices=[branch.value for branch in DecompositionBranch],
    )
    resume_parser.add_argument("--walker-max-depth", required=True, type=int)
    resume_parser.add_argument("--out", required=True, type=Path)
    resume_parser.add_argument("--output", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed SME adjudication import, export, and evaluation"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import-workbook")
    import_parser.add_argument("workbook", type=Path)
    import_parser.add_argument("registry", type=Path)
    import_parser.add_argument("output", type=Path)
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("adjudication", type=Path)
    evaluate_parser.add_argument("engine_evidence", type=Path)
    evaluate_parser.add_argument("corpus_comparison", type=Path)
    evaluate_parser.add_argument("registry", type=Path)
    evaluate_parser.add_argument("output", type=Path)
    export_parser = subparsers.add_parser("export-proposals")
    export_parser.add_argument("registry", type=Path)
    export_parser.add_argument("output_directory", type=Path)
    rows_parser = subparsers.add_parser(
        "export-row-decisions",
        help="Export the selected row-decision projection from an attested workbook",
        description=(
            "Export the selected row-decision projection from an attested review "
            "workbook.\n"
            "Precondition: reviewer attestation and workbook structural gates must "
            "pass;\n"
            "oracle validation still requires import-workbook."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rows_parser.add_argument("workbook", type=Path)
    rows_parser.add_argument("output", type=Path)
    current_parser = subparsers.add_parser("generate-current-evidence")
    current_parser.add_argument("--sample-manifest", required=True, type=Path)
    current_parser.add_argument("--oracle", required=True, type=Path)
    current_parser.add_argument("--row-decisions", required=True, type=Path)
    current_parser.add_argument("--proposal-registry", required=True, type=Path)
    current_parser.add_argument("--run-id", required=True)
    current_parser.add_argument("--artifact", required=True, type=Path)
    current_parser.add_argument("--engine-output", required=True, type=Path)
    current_parser.add_argument("--comparison-output", required=True, type=Path)
    corpus_parser = subparsers.add_parser("generate-corpus-baseline")
    corpus_parser.add_argument("--source-manifest", required=True, type=Path)
    corpus_parser.add_argument("--run-id", required=True)
    corpus_parser.add_argument("--artifact", required=True, type=Path)
    corpus_parser.add_argument("--output", required=True, type=Path)
    conservation_parser = subparsers.add_parser("generate-r101-conservation")
    conservation_parser.add_argument("--source-manifest", required=True, type=Path)
    conservation_parser.add_argument("--baseline", required=True, type=Path)
    conservation_parser.add_argument("--run-id", required=True)
    conservation_parser.add_argument("--new-run-id", required=True)
    conservation_parser.add_argument("--endpoint", required=True)
    conservation_parser.add_argument("--output", required=True, type=Path)
    conservation_parser.add_argument("--pre-resume-proof-identity", required=True)
    conservation_parser.add_argument("--resume-dry-run-identity", required=True)
    conservation_parser.add_argument("--mixed-cohort-identity", required=True)
    pre_resume_parser = subparsers.add_parser("generate-pre-resume-proof")
    pre_resume_parser.add_argument("--source-manifest", required=True, type=Path)
    pre_resume_parser.add_argument("--run-id", required=True)
    pre_resume_parser.add_argument("--endpoint", required=True)
    pre_resume_parser.add_argument("--output", required=True, type=Path)
    _add_resume_dry_run_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:  # noqa: PLR0911
    args = _parser().parse_args(argv)
    if args.command == "import-workbook":
        _write_artifact(args.workbook, args.registry, args.output)
        return
    if args.command == "export-proposals":
        _export_proposals(args.registry, args.output_directory)
        return
    if args.command == "export-row-decisions":
        _write_row_decisions(args.workbook, args.output)
        return
    if args.command == "generate-current-evidence":
        asyncio.run(_generate_current(cast("_CurrentEvidenceArgs", args)))
        return
    if args.command == "generate-corpus-baseline":
        asyncio.run(_generate_corpus(cast("_CorpusBaselineArgs", args)))
        return
    if args.command == "generate-r101-conservation":
        asyncio.run(_generate_r101_conservation(cast("_R101ConservationArgs", args)))
        return
    if args.command == "generate-pre-resume-proof":
        asyncio.run(_generate_pre_resume(cast("_PreResumeArgs", args)))
        return
    if args.command == "dry-run-resume":
        asyncio.run(_dry_run_resume(cast("_ResumeDryRunArgs", args)))
        return
    _evaluate(
        args.adjudication,
        args.engine_evidence,
        args.corpus_comparison,
        args.registry,
        args.output,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
