#!/usr/bin/env python3
"""Import and evaluate provenance-bound SME decomposition adjudication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.research.golden_review import (
        evaluate_adjudication,
        import_adjudication_workbook,
        load_adjudication,
        write_evaluation_report,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.golden_review import (
        evaluate_adjudication,
        import_adjudication_workbook,
        load_adjudication,
        write_evaluation_report,
    )


def _write_artifact(workbook: Path, output: Path) -> None:
    artifact = import_adjudication_workbook(workbook)
    rendered = artifact.model_dump(mode="json", by_alias=True)
    output.write_text(
        json.dumps(rendered, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _evaluate(
    adjudication: Path,
    engine_evidence: Path,
    corpus_comparison: Path,
    output: Path,
) -> None:
    engine = json.loads(engine_evidence.read_text(encoding="utf-8"))
    corpus = json.loads(corpus_comparison.read_text(encoding="utf-8"))
    report = evaluate_adjudication(
        load_adjudication(adjudication),
        engine,
        corpus,
    )
    write_evaluation_report(report, output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed SME adjudication import and evaluation"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import-workbook")
    import_parser.add_argument("workbook", type=Path)
    import_parser.add_argument("output", type=Path)
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("adjudication", type=Path)
    evaluate_parser.add_argument("engine_evidence", type=Path)
    evaluate_parser.add_argument("corpus_comparison", type=Path)
    evaluate_parser.add_argument("output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args: Any = _parser().parse_args(argv)
    if args.command == "import-workbook":
        _write_artifact(args.workbook, args.output)
        return
    _evaluate(
        args.adjudication,
        args.engine_evidence,
        args.corpus_comparison,
        args.output,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
