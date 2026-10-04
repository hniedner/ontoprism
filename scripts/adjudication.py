#!/usr/bin/env python3
"""Import, export, and evaluate provenance-bound SME decomposition adjudication."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Protocol, cast

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.proposal_registry import (
    load_proposal_registry,
    write_proposal_registry,
    write_submission_exports,
)
from ontolib.decomposition.proposal_registry_migration import (
    write_proposal_registry_migration_envelope,
)
from ontolib.decomposition.provenance import ProvenanceStore

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
    from scripts.research.axis_diagnostic_report import (
        generate_axis_diagnostic_report,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.axis_diagnostic_report import generate_axis_diagnostic_report

try:
    from scripts.research.specialist_review_packets import (
        generate_specialist_review_packets,
        validate_specialist_review_generation,
        validate_specialist_review_packet_directory,
        validate_specialist_review_row,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.specialist_review_packets import (
        generate_specialist_review_packets,
        validate_specialist_review_generation,
        validate_specialist_review_packet_directory,
        validate_specialist_review_row,
    )

try:
    from scripts.research.group_review_packet import (
        admit_group_review_rationale_evidence,
        dry_run_group_review_decisions,
        generate_group_review_boundary,
        import_group_review_decisions,
        load_group_decision_registry,
        load_group_review_packet,
        load_historical_group_review_packet,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.group_review_packet import (  # type: ignore[no-redef]
        admit_group_review_rationale_evidence,
        dry_run_group_review_decisions,
        generate_group_review_boundary,
        import_group_review_decisions,
        load_group_decision_registry,
        load_group_review_packet,
        load_historical_group_review_packet,
    )


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
    proposal_registry_migration: Path
    run_id: str
    artifact: Path
    artifact_manifest: Path | None
    artifact_manifest_identity: str | None
    engine_output: Path
    comparison_output: Path


class _AxisDiagnosticArgs(Protocol):
    source_manifest: Path
    endpoint: str
    oracle: Path
    row_decisions: Path
    proposal_registry: Path
    proposal_registry_migration: Path
    current_evidence: Path
    current_comparison: Path
    residual_filler: list[str]
    output: Path


class _GenerateSpecialistPacketsArgs(Protocol):
    literature_context: Path
    proposal_registry: Path
    cadsr_usage: Path
    label_source: Path
    ncit_source: Path
    axis_diagnostics: Path
    current_evidence: Path
    current_comparison: Path
    group_review_packet: Path
    output_directory: Path
    producing_command: str


class _ValidateSpecialistPacketsArgs(Protocol):
    directory: Path


class _GroupReviewArgs(Protocol):
    current_evidence: Path
    current_comparison: Path
    output: Path
    workbook: Path
    correction_audit: Path
    blank_validation: Path


class _ImportGroupReviewArgs(Protocol):
    packet: Path
    reviewed_xlsx: Path
    output: Path


class _DryRunGroupReviewArgs(Protocol):
    packet: Path
    registry: Path
    output: Path


class _AdmitGroupReviewEvidenceArgs(Protocol):
    packet: Path
    source_markdown: Path
    markdown_output: Path
    sidecar_output: Path


def _add_group_review_parser(subparsers: argparse._SubParsersAction) -> None:
    group_parser = subparsers.add_parser("generate-group-review-packet")
    group_parser.add_argument("--current-evidence", required=True, type=Path)
    group_parser.add_argument("--current-comparison", required=True, type=Path)
    group_parser.add_argument("--output", required=True, type=Path)
    group_parser.add_argument("--workbook", required=True, type=Path)
    group_parser.add_argument("--correction-audit", required=True, type=Path)
    group_parser.add_argument("--blank-validation", required=True, type=Path)
    importer = subparsers.add_parser("import-group-review")
    importer.add_argument("--packet", required=True, type=Path)
    importer.add_argument("--reviewed-xlsx", required=True, type=Path)
    importer.add_argument("--output", required=True, type=Path)
    dry_run = subparsers.add_parser("dry-run-group-review")
    dry_run.add_argument("--packet", required=True, type=Path)
    dry_run.add_argument("--registry", required=True, type=Path)
    dry_run.add_argument("--output", required=True, type=Path)
    admit = subparsers.add_parser("admit-group-review-evidence")
    admit.add_argument("--packet", required=True, type=Path)
    admit.add_argument("--source-markdown", required=True, type=Path)
    admit.add_argument("--markdown-output", required=True, type=Path)
    admit.add_argument("--sidecar-output", required=True, type=Path)


async def _generate_current(args: _CurrentEvidenceArgs) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        await generate_current_evidence(
            sample_manifest=args.sample_manifest,
            oracle=args.oracle,
            row_decisions=args.row_decisions,
            proposal_registry=args.proposal_registry,
            proposal_registry_migration=args.proposal_registry_migration,
            run_id=args.run_id,
            artifact=args.artifact,
            artifact_manifest=args.artifact_manifest,
            artifact_manifest_identity=args.artifact_manifest_identity,
            engine_output=args.engine_output,
            comparison_output=args.comparison_output,
            store=ProvenanceStore(make_sessionmaker(engine)),
        )
    finally:
        await dispose_engine(engine)


async def _generate_axis_diagnostics(args: _AxisDiagnosticArgs) -> None:
    await generate_axis_diagnostic_report(
        source_manifest=args.source_manifest,
        endpoint=args.endpoint,
        oracle_path=args.oracle,
        row_decisions_path=args.row_decisions,
        proposal_registry_path=args.proposal_registry,
        proposal_registry_migration_path=args.proposal_registry_migration,
        current_evidence_path=args.current_evidence,
        current_comparison_path=args.current_comparison,
        residual_fillers=tuple(args.residual_filler),
        output=args.output,
    )


def _generate_group_review(args: _GroupReviewArgs) -> None:
    generate_group_review_boundary(
        evidence_path=args.current_evidence,
        comparison_path=args.current_comparison,
        output=args.output,
        workbook=args.workbook,
        correction_audit=args.correction_audit,
        blank_validation=args.blank_validation,
    )


def _generate_specialist_packets(args: _GenerateSpecialistPacketsArgs) -> None:
    generate_specialist_review_packets(
        literature_context_path=args.literature_context,
        proposal_registry_path=args.proposal_registry,
        cadsr_usage_path=args.cadsr_usage,
        ncit_source_path=args.ncit_source,
        output_directory=args.output_directory,
        producing_command=args.producing_command,
        additional_input_paths=(
            args.axis_diagnostics,
            args.current_evidence,
            args.current_comparison,
            args.group_review_packet,
            args.label_source,
        ),
    )


def _import_group_review(args: _ImportGroupReviewArgs) -> None:
    import_group_review_decisions(
        load_group_review_packet(args.packet), args.reviewed_xlsx, args.output
    )


def _dry_run_group_review(args: _DryRunGroupReviewArgs) -> None:
    result = dry_run_group_review_decisions(
        load_group_review_packet(args.packet),
        load_group_decision_registry(args.registry),
    )
    write_canonical_json(result.model_dump(mode="json"), args.output)


def _admit_group_review_evidence(args: _AdmitGroupReviewEvidenceArgs) -> None:
    admit_group_review_rationale_evidence(
        packet=load_historical_group_review_packet(args.packet),
        source_markdown=args.source_markdown,
        markdown_output=args.markdown_output,
        sidecar_output=args.sidecar_output,
    )


def _parser() -> argparse.ArgumentParser:  # noqa: PLR0915
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
    registry_parser = subparsers.add_parser("write-proposal-registry")
    registry_parser.add_argument("registry", type=Path)
    migration_parser = subparsers.add_parser("bind-proposal-registry-migration")
    migration_parser.add_argument("--historical-oracle", required=True, type=Path)
    migration_parser.add_argument("--historical-r103-review", required=True, type=Path)
    migration_parser.add_argument(
        "--historical-r103-revision", required=True, type=Path
    )
    migration_parser.add_argument(
        "--historical-r103-corroboration", required=True, type=Path
    )
    migration_parser.add_argument("--current-registry", required=True, type=Path)
    migration_parser.add_argument("--output", required=True, type=Path)
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
    current_parser.add_argument(
        "--proposal-registry-migration", required=True, type=Path
    )
    current_parser.add_argument("--run-id", required=True)
    current_parser.add_argument("--artifact", required=True, type=Path)
    current_parser.add_argument("--artifact-manifest", type=Path)
    current_parser.add_argument("--artifact-manifest-identity")
    current_parser.add_argument("--engine-output", required=True, type=Path)
    current_parser.add_argument("--comparison-output", required=True, type=Path)
    axis_parser = subparsers.add_parser("generate-axis-diagnostics")
    axis_parser.add_argument("--source-manifest", required=True, type=Path)
    axis_parser.add_argument("--endpoint", required=True)
    axis_parser.add_argument("--oracle", required=True, type=Path)
    axis_parser.add_argument("--row-decisions", required=True, type=Path)
    axis_parser.add_argument("--proposal-registry", required=True, type=Path)
    axis_parser.add_argument("--proposal-registry-migration", required=True, type=Path)
    axis_parser.add_argument("--current-evidence", required=True, type=Path)
    axis_parser.add_argument("--current-comparison", required=True, type=Path)
    axis_parser.add_argument(
        "--residual-filler", required=True, action="append", default=[]
    )
    axis_parser.add_argument("--output", required=True, type=Path)
    specialist = subparsers.add_parser("generate-specialist-review-packets")
    specialist.add_argument("--literature-context", required=True, type=Path)
    specialist.add_argument("--proposal-registry", required=True, type=Path)
    specialist.add_argument("--cadsr-usage", required=True, type=Path)
    specialist.add_argument("--label-source", required=True, type=Path)
    specialist.add_argument("--ncit-source", required=True, type=Path)
    specialist.add_argument("--axis-diagnostics", required=True, type=Path)
    specialist.add_argument("--current-evidence", required=True, type=Path)
    specialist.add_argument("--current-comparison", required=True, type=Path)
    specialist.add_argument("--group-review-packet", required=True, type=Path)
    specialist.add_argument("--output-directory", required=True, type=Path)
    specialist.add_argument("--producing-command", required=True)
    validate_generation = subparsers.add_parser("validate-specialist-review-generation")
    validate_generation.add_argument("--directory", required=True, type=Path)
    validate_row = subparsers.add_parser("validate-completed-specialist-review-row")
    validate_row.add_argument("--code", required=True)
    validate_row.add_argument("--return-file", required=True, type=Path)
    validate_row.add_argument("--index", required=True, type=Path)
    validate_row.add_argument("--validation-output", required=True, type=Path)
    validate_set = subparsers.add_parser("validate-completed-specialist-review-set")
    validate_set.add_argument("--returns-directory", required=True, type=Path)
    validate_set.add_argument("--index", required=True, type=Path)
    _add_group_review_parser(subparsers)
    return parser


def main(  # noqa: C901, PLR0911, PLR0912
    argv: list[str] | None = None,
) -> None:
    args = _parser().parse_args(argv)
    if args.command == "import-workbook":
        _write_artifact(args.workbook, args.registry, args.output)
        return
    if args.command == "export-proposals":
        _export_proposals(args.registry, args.output_directory)
        return
    if args.command == "write-proposal-registry":
        registry = load_proposal_registry(args.registry)
        write_proposal_registry(registry, args.registry)
        return
    if args.command == "bind-proposal-registry-migration":
        write_proposal_registry_migration_envelope(
            historical_oracle_path=args.historical_oracle,
            historical_r103_review_path=args.historical_r103_review,
            historical_r103_revision_path=args.historical_r103_revision,
            historical_r103_corroboration_path=args.historical_r103_corroboration,
            current_registry_path=args.current_registry,
            output_path=args.output,
        )
        return
    if args.command == "export-row-decisions":
        _write_row_decisions(args.workbook, args.output)
        return
    if args.command == "generate-current-evidence":
        asyncio.run(_generate_current(cast("_CurrentEvidenceArgs", args)))
        return
    if args.command == "generate-axis-diagnostics":
        asyncio.run(_generate_axis_diagnostics(cast("_AxisDiagnosticArgs", args)))
        return
    if args.command == "generate-specialist-review-packets":
        _generate_specialist_packets(cast("_GenerateSpecialistPacketsArgs", args))
        return
    if args.command == "validate-specialist-review-generation":
        validation_args = cast("_ValidateSpecialistPacketsArgs", args)
        validate_specialist_review_generation(validation_args.directory)
        return
    if args.command == "validate-completed-specialist-review-row":
        validate_specialist_review_row(
            code=args.code,
            return_path=args.return_file,
            index_path=args.index,
            validation_output=args.validation_output,
        )
        return
    if args.command == "validate-completed-specialist-review-set":
        validate_specialist_review_packet_directory(
            args.returns_directory, index_path=args.index
        )
        return
    if args.command == "generate-group-review-packet":
        _generate_group_review(cast("_GroupReviewArgs", args))
        return
    if args.command == "import-group-review":
        _import_group_review(cast("_ImportGroupReviewArgs", args))
        return
    if args.command == "dry-run-group-review":
        _dry_run_group_review(cast("_DryRunGroupReviewArgs", args))
        return
    if args.command == "admit-group-review-evidence":
        _admit_group_review_evidence(cast("_AdmitGroupReviewEvidenceArgs", args))
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
