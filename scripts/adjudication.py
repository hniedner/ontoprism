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
    write_submission_exports,
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
    return parser


def main(argv: list[str] | None = None) -> None:
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
    _evaluate(
        args.adjudication,
        args.engine_evidence,
        args.corpus_comparison,
        args.registry,
        args.output,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
