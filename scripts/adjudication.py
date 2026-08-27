#!/usr/bin/env python3
"""Import, export, and evaluate provenance-bound SME decomposition adjudication."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.branches import DecompositionBranch
from ontolib.decomposition.collapse_policy import (
    load_packaged_collapse_veto_policy,
    write_collapse_policy_artifacts,
)
from ontolib.decomposition.collapse_policy_generation import (
    build_authorized_collapse_veto_policy,
)
from ontolib.decomposition.corpus_baseline import (
    generate_corpus_baseline,
    load_corpus_baseline,
    write_corpus_baseline,
)
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
    LedgerBuildContext,
    QueryMetrics,
    R101ConservationValidationError,
    build_r101_occurrence_ledger,
    load_r101_conservation_report,
    r82_path_document,
    r101_detector_identity,
    r101_proof_identity,
    validate_r101_publication,
    write_r101_occurrence_ledger,
)
from ontolib.decomposition.r101_review import (
    QLeverReviewLabels,
    build_r101_review_packet,
    dry_run_r101_decision_expansion,
    import_r101_review_decisions,
    load_r101_decision_registry,
    load_r101_review_packet,
    write_r101_decision_expansion_dry_run,
    write_r101_review_packet,
    write_r101_review_workbook,
)
from ontolib.decomposition.r103_review import (
    build_r103_review_packet,
    dry_run_r103_review,
    import_r103_review_decisions,
    load_r103_decision_registry,
    load_r103_review_packet,
    write_r103_review_dry_run,
    write_r103_review_packet,
    write_r103_review_workbook,
)
from ontolib.decomposition.r103_review_promotion import promote_r103_review_state
from ontolib.decomposition.resume_dry_run import (
    build_resume_dry_run,
    inspect_resume_selection,
    load_pre_resume_proof,
    write_resume_dry_run,
)
from ontolib.decomposition.run import RunConfig, _detect_concept, build_resume_identity
from ontolib.decomposition.stated_queries import resolve_part_of_paths
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.sibling_store import (
    NCIT_CANDIDATE_OBSERVATION_QUERY_COUNT,
    validate_ncit_sibling_manifest,
)

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
    from scripts.research.group_review_packet import (
        dry_run_group_review_decisions,
        generate_group_review_boundary,
        import_group_review_decisions,
        load_group_decision_registry,
        load_group_review_packet,
    )
except ModuleNotFoundError:  # direct `python scripts/adjudication.py` entry point
    from research.group_review_packet import (  # type: ignore[no-redef]
        dry_run_group_review_decisions,
        generate_group_review_boundary,
        import_group_review_decisions,
        load_group_decision_registry,
        load_group_review_packet,
    )

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


class _AxisDiagnosticArgs(Protocol):
    source_manifest: Path
    endpoint: str
    oracle: Path
    row_decisions: Path
    proposal_registry: Path
    current_evidence: Path
    current_comparison: Path
    residual_filler: list[str]
    output: Path


class _GroupReviewArgs(Protocol):
    current_evidence: Path
    current_comparison: Path
    r101_report: Path
    output: Path
    workbook: Path


class _ImportGroupReviewArgs(Protocol):
    packet: Path
    reviewed_xlsx: Path
    output: Path


class _DryRunGroupReviewArgs(Protocol):
    packet: Path
    registry: Path
    output: Path


class _PrepareR103ReviewArgs(Protocol):
    stated_owl: Path
    source_manifest: Path
    proposal_registry: Path
    output_packet: Path
    output_xlsx: Path


class _ImportR103ReviewArgs(Protocol):
    packet: Path
    reviewed_xlsx: Path
    output: Path


class _DryRunR103ReviewArgs(Protocol):
    packet: Path
    registry: Path
    oracle: Path
    proposal_registry: Path
    output: Path


class _PromoteR103ReviewStateArgs(Protocol):
    packet: Path
    registry: Path
    dry_run: Path
    oracle: Path
    proposal_registry: Path
    output: Path


def _add_group_review_parser(subparsers: argparse._SubParsersAction) -> None:
    group_parser = subparsers.add_parser("generate-group-review-packet")
    group_parser.add_argument("--current-evidence", required=True, type=Path)
    group_parser.add_argument("--current-comparison", required=True, type=Path)
    group_parser.add_argument("--r101-report", required=True, type=Path)
    group_parser.add_argument("--output", required=True, type=Path)
    group_parser.add_argument("--workbook", required=True, type=Path)
    importer = subparsers.add_parser("import-group-review")
    importer.add_argument("--packet", required=True, type=Path)
    importer.add_argument("--reviewed-xlsx", required=True, type=Path)
    importer.add_argument("--output", required=True, type=Path)
    dry_run = subparsers.add_parser("dry-run-group-review")
    dry_run.add_argument("--packet", required=True, type=Path)
    dry_run.add_argument("--registry", required=True, type=Path)
    dry_run.add_argument("--output", required=True, type=Path)


def _add_r103_review_parser(subparsers: argparse._SubParsersAction) -> None:
    prepare = subparsers.add_parser("prepare-r103-review-packet")
    prepare.add_argument("--stated-owl", required=True, type=Path)
    prepare.add_argument("--source-manifest", required=True, type=Path)
    prepare.add_argument("--proposal-registry", required=True, type=Path)
    prepare.add_argument("--output-packet", required=True, type=Path)
    prepare.add_argument("--output-xlsx", required=True, type=Path)
    importer = subparsers.add_parser("import-r103-review-decisions")
    importer.add_argument("--packet", required=True, type=Path)
    importer.add_argument("--reviewed-xlsx", required=True, type=Path)
    importer.add_argument("--output", required=True, type=Path)
    dry_run = subparsers.add_parser("dry-run-r103-review")
    dry_run.add_argument("--packet", required=True, type=Path)
    dry_run.add_argument("--registry", required=True, type=Path)
    dry_run.add_argument("--oracle", required=True, type=Path)
    dry_run.add_argument("--proposal-registry", required=True, type=Path)
    dry_run.add_argument("--output", required=True, type=Path)
    promote = subparsers.add_parser("promote-r103-review-state")
    promote.add_argument("--packet", required=True, type=Path)
    promote.add_argument("--registry", required=True, type=Path)
    promote.add_argument("--dry-run", required=True, type=Path)
    promote.add_argument("--oracle", required=True, type=Path)
    promote.add_argument("--proposal-registry", required=True, type=Path)
    promote.add_argument("--output", required=True, type=Path)


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


class _R101PublicationArgs(Protocol):
    report: Path
    authorization_digest: str


class _PrepareR101ReviewArgs(Protocol):
    report: Path
    source_manifest: Path
    endpoint: str
    output_packet: Path
    output_xlsx: Path


class _ImportR101ReviewArgs(Protocol):
    packet: Path
    reviewed_xlsx: Path
    output: Path
    provenance: Literal["sme", "test-only"]


class _DryRunR101DecisionExpansionArgs(Protocol):
    report: Path
    packet: Path
    registry: Path
    output: Path


class _GenerateR101CollapsePolicyArgs(Protocol):
    registry: Path
    packet: Path
    report: Path
    source_manifest: Path
    endpoint: str
    output_registry_gzip: Path
    output_policy: Path


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


async def _generate_axis_diagnostics(args: _AxisDiagnosticArgs) -> None:
    await generate_axis_diagnostic_report(
        source_manifest=args.source_manifest,
        endpoint=args.endpoint,
        oracle_path=args.oracle,
        row_decisions_path=args.row_decisions,
        proposal_registry_path=args.proposal_registry,
        current_evidence_path=args.current_evidence,
        current_comparison_path=args.current_comparison,
        residual_fillers=tuple(args.residual_filler),
        output=args.output,
    )


def _generate_group_review(args: _GroupReviewArgs) -> None:
    generate_group_review_boundary(
        evidence_path=args.current_evidence,
        comparison_path=args.current_comparison,
        r101_report_path=args.r101_report,
        output=args.output,
        workbook=args.workbook,
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

            store = ProvenanceStore(make_sessionmaker(engine))
            baseline = load_corpus_baseline(args.baseline)
            old_run = await store.completed_run_for_evidence(args.run_id)
            new_run = await store.completed_run_for_evidence(args.new_run_id)
            if (
                baseline.run_id != old_run.run_id
                or baseline.source_identity != manifest.source_identity
                or baseline.ontology_release != manifest.ontology_version
                or baseline.representation_identity != old_run.representation_identity
                or old_run.fingerprint.algorithm_version != "decomposition-v3"
                or new_run.fingerprint.algorithm_version != "decomposition-v4"
                or new_run.fingerprint.source_identity != manifest.source_identity
                or new_run.ncit_version != manifest.ontology_version
            ):
                raise ValueError("source-identity-mismatch")
            old_dimensions = old_run.fingerprint.model_dump(
                exclude={"algorithm_version", "emitted_at"}
            )
            new_dimensions = new_run.fingerprint.model_dump(
                exclude={"algorithm_version", "emitted_at"}
            )
            if old_dimensions != new_dimensions:
                raise ValueError("source-identity-mismatch: run fingerprint drift")
            source_rows = await store.r101_occurrence_ledger(
                args.run_id, args.new_run_id
            )
            candidate_pairs = tuple(
                sorted(
                    {
                        (retained.filler_code, old.filler_code)
                        for item in source_rows.occurrences
                        if item.old_links and not item.new_links
                        for old in item.old_links
                        for retained in item.retained_new_r101_links
                        if old.axis == retained.axis
                    }
                )
            )
            path_result = await resolve_part_of_paths(
                client,
                candidate_pairs,
                source_identity=manifest.source_identity,
            )
            proof_identity = r101_proof_identity(
                args.pre_resume_proof_identity,
                args.resume_dry_run_identity,
                args.mixed_cohort_identity,
            )
            report = build_r101_occurrence_ledger(
                source_rows.occurrences,
                paths={
                    pair: r82_path_document(path)
                    for pair, path in path_result.paths.items()
                },
                context=LedgerBuildContext(
                    source_identity=manifest.source_identity,
                    source_release_id=manifest.ontology_version,
                    old_run_id=old_run.run_id,
                    old_run_fingerprint_identity=old_run.fingerprint.identity,
                    old_representation_identity=old_run.representation_identity,
                    old_baseline_identity=baseline.baseline_identity,
                    new_run_id=new_run.run_id,
                    new_run_fingerprint_identity=new_run.fingerprint.identity,
                    new_representation_identity=new_run.representation_identity,
                    detector_identity=r101_detector_identity(),
                    pre_resume_proof_identity=args.pre_resume_proof_identity,
                    resume_dry_run_identity=args.resume_dry_run_identity,
                    mixed_cohort_identity=args.mixed_cohort_identity,
                    proof_identity=proof_identity,
                    adapter_id="ncit-stated-r82-v1",
                    query_metrics=QueryMetrics(
                        postgres_query_count=3,
                        qlever_query_count=path_result.query_count + 1,
                        max_pair_batch_size=path_result.max_pair_batch_size,
                        max_r82_hops=8,
                        max_asserted_superclass_hops=20,
                    ),
                    non_r101_delta_evidence=source_rows.non_r101_delta_evidence,
                ),
            )
            write_r101_occurrence_ledger(args.output, report)
            print(
                f"json_identity={report.json_identity} "
                f"tsv_identity={report.tsv_identity} "
                f"report_identity={report.report_identity}",
                file=sys.stderr,
            )
    finally:
        await dispose_engine(engine)


async def _prepare_r101_review(args: _PrepareR101ReviewArgs) -> None:
    report = load_r101_conservation_report(args.report)
    source = await _source_snapshot(args.source_manifest, args.endpoint)
    if (
        source.source_identity != report.source_identity
        or source.ontology_version != report.source_release_id
    ):
        raise R101ConservationValidationError(
            "live source does not match review report"
        )
    async with ncit_sparql_client(args.endpoint) as client:
        labels = QLeverReviewLabels(client)
        packet = await build_r101_review_packet(report, args.source_manifest, labels)
    write_r101_review_packet(args.output_packet, packet)
    write_r101_review_workbook(args.output_xlsx, packet)
    print(
        f"packet_identity={packet.packet_identity} patterns={len(packet.patterns)} "
        f"diseases={len(packet.disease_propositions)} "
        f"occurrences={len(packet.occurrences)} "
        f"source_checks={NCIT_CANDIDATE_OBSERVATION_QUERY_COUNT} "
        f"label_reads={labels.query_count} "
        f"qlever_reads={NCIT_CANDIDATE_OBSERVATION_QUERY_COUNT + labels.query_count}",
        file=sys.stderr,
    )


def _import_r101_review(args: _ImportR101ReviewArgs) -> None:
    packet = load_r101_review_packet(args.packet)
    registry = import_r101_review_decisions(
        packet,
        args.reviewed_xlsx,
        args.output,
        provenance=args.provenance,
    )
    print(f"registry_identity={registry.registry_identity}", file=sys.stderr)


def _dry_run_r101_decision_expansion(args: _DryRunR101DecisionExpansionArgs) -> None:
    result = dry_run_r101_decision_expansion(
        load_r101_conservation_report(args.report),
        load_r101_review_packet(args.packet),
        load_r101_decision_registry(args.registry),
    )
    write_r101_decision_expansion_dry_run(args.output, result)
    print(f"verdict={result.verdict} writes_performed=false", file=sys.stderr)


def _prepare_r103_review(args: _PrepareR103ReviewArgs) -> None:
    packet = build_r103_review_packet(
        args.stated_owl, args.source_manifest, args.proposal_registry
    )
    write_r103_review_packet(args.output_packet, packet)
    write_r103_review_workbook(args.output_xlsx, packet)
    workbook_identity = hashlib.sha256(args.output_xlsx.read_bytes()).hexdigest()
    print(
        f"packet_identity={packet.packet_identity} rows={len(packet.rows)} "
        f"source_passes={packet.source_pass_count} "
        f"blank_workbook_sha256={workbook_identity}",
        file=sys.stderr,
    )


def _import_r103_review(args: _ImportR103ReviewArgs) -> None:
    registry = import_r103_review_decisions(
        load_r103_review_packet(args.packet), args.reviewed_xlsx, args.output
    )
    print(f"registry_identity={registry.registry_identity}", file=sys.stderr)


def _dry_run_r103_review(args: _DryRunR103ReviewArgs) -> None:
    result = dry_run_r103_review(
        load_r103_review_packet(args.packet),
        load_r103_decision_registry(args.registry),
        oracle_path=args.oracle,
        proposal_registry_path=args.proposal_registry,
    )
    write_r103_review_dry_run(args.output, result)
    print(f"readiness={result.readiness} writes_performed=false", file=sys.stderr)


def _promote_r103_review_state(args: _PromoteR103ReviewStateArgs) -> None:
    promoted = promote_r103_review_state(
        packet_path=args.packet,
        registry_path=args.registry,
        dry_run_path=args.dry_run,
        oracle_path=args.oracle,
        proposal_registry_path=args.proposal_registry,
        output_path=args.output,
    )
    print(
        f"artifact_identity={promoted.artifact_identity} "
        "readiness=review-incomplete writes_performed=false",
        file=sys.stderr,
    )


async def _generate_r101_collapse_policy(
    args: _GenerateR101CollapsePolicyArgs,
) -> None:
    registry = load_r101_decision_registry(args.registry)
    packet = load_r101_review_packet(args.packet)
    report = load_r101_conservation_report(args.report)
    source = await _source_snapshot(args.source_manifest, args.endpoint)
    if (
        source.source_identity != report.source_identity
        or source.ontology_version != report.source_release_id
    ):
        raise R101ConservationValidationError(
            "live source does not match collapse-policy evidence"
        )
    live_occurrences = []
    concepts = sorted(
        {
            row.disease_code
            for row in registry.atomic_decisions
            if row.outcome == "rejected-retain-broader"
        }
    )
    async with ncit_sparql_client(args.endpoint) as client:
        for concept_code in concepts:
            _detection, _roles, _morphology, definition, _types = await _detect_concept(
                concept_code,
                client,
                label=None,
                walker_max_depth=64,
            )
            live_occurrences.extend(definition.occurrences)
    policy = build_authorized_collapse_veto_policy(
        registry, packet, report, live_occurrences
    )
    write_collapse_policy_artifacts(
        args.output_registry_gzip,
        args.output_policy,
        registry.model_dump(mode="json"),
        policy,
    )
    print(
        f"registry_identity={registry.registry_identity} "
        f"policy_identity={policy.policy_identity} entries={len(policy.entries)}",
        file=sys.stderr,
    )


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
        collapse_policy=load_packaged_collapse_veto_policy(),
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


def _add_r101_parsers(subparsers: Any) -> None:
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
    publication_parser = subparsers.add_parser("validate-r101-publication")
    publication_parser.add_argument("--report", required=True, type=Path)
    publication_parser.add_argument("--authorization-digest", required=True)
    prepare_parser = subparsers.add_parser("prepare-r101-review-packet")
    prepare_parser.add_argument("--report", required=True, type=Path)
    prepare_parser.add_argument("--source-manifest", required=True, type=Path)
    prepare_parser.add_argument("--endpoint", required=True)
    prepare_parser.add_argument("--output-packet", required=True, type=Path)
    prepare_parser.add_argument("--output-xlsx", required=True, type=Path)
    import_parser = subparsers.add_parser("import-r101-review-decisions")
    import_parser.add_argument("--packet", required=True, type=Path)
    import_parser.add_argument("--reviewed-xlsx", required=True, type=Path)
    import_parser.add_argument("--output", required=True, type=Path)
    import_parser.add_argument(
        "--provenance", required=True, choices=("sme", "test-only")
    )
    dry_run_parser = subparsers.add_parser("dry-run-r101-decision-expansion")
    dry_run_parser.add_argument("--report", required=True, type=Path)
    dry_run_parser.add_argument("--packet", required=True, type=Path)
    dry_run_parser.add_argument("--registry", required=True, type=Path)
    dry_run_parser.add_argument("--output", required=True, type=Path)
    policy_parser = subparsers.add_parser("generate-r101-collapse-policy")
    policy_parser.add_argument("--registry", required=True, type=Path)
    policy_parser.add_argument("--packet", required=True, type=Path)
    policy_parser.add_argument("--report", required=True, type=Path)
    policy_parser.add_argument("--source-manifest", required=True, type=Path)
    policy_parser.add_argument("--endpoint", required=True)
    policy_parser.add_argument("--output-registry-gzip", required=True, type=Path)
    policy_parser.add_argument("--output-policy", required=True, type=Path)


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
    axis_parser = subparsers.add_parser("generate-axis-diagnostics")
    axis_parser.add_argument("--source-manifest", required=True, type=Path)
    axis_parser.add_argument("--endpoint", required=True)
    axis_parser.add_argument("--oracle", required=True, type=Path)
    axis_parser.add_argument("--row-decisions", required=True, type=Path)
    axis_parser.add_argument("--proposal-registry", required=True, type=Path)
    axis_parser.add_argument("--current-evidence", required=True, type=Path)
    axis_parser.add_argument("--current-comparison", required=True, type=Path)
    axis_parser.add_argument(
        "--residual-filler", required=True, action="append", default=[]
    )
    axis_parser.add_argument("--output", required=True, type=Path)
    _add_group_review_parser(subparsers)
    _add_r103_review_parser(subparsers)
    corpus_parser = subparsers.add_parser("generate-corpus-baseline")
    corpus_parser.add_argument("--source-manifest", required=True, type=Path)
    corpus_parser.add_argument("--run-id", required=True)
    corpus_parser.add_argument("--artifact", required=True, type=Path)
    corpus_parser.add_argument("--output", required=True, type=Path)
    _add_r101_parsers(subparsers)
    pre_resume_parser = subparsers.add_parser("generate-pre-resume-proof")
    pre_resume_parser.add_argument("--source-manifest", required=True, type=Path)
    pre_resume_parser.add_argument("--run-id", required=True)
    pre_resume_parser.add_argument("--endpoint", required=True)
    pre_resume_parser.add_argument("--output", required=True, type=Path)
    _add_resume_dry_run_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:  # noqa: C901, PLR0911, PLR0912
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
    if args.command == "generate-axis-diagnostics":
        asyncio.run(_generate_axis_diagnostics(cast("_AxisDiagnosticArgs", args)))
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
    if args.command == "prepare-r103-review-packet":
        _prepare_r103_review(cast("_PrepareR103ReviewArgs", args))
        return
    if args.command == "import-r103-review-decisions":
        _import_r103_review(cast("_ImportR103ReviewArgs", args))
        return
    if args.command == "dry-run-r103-review":
        _dry_run_r103_review(cast("_DryRunR103ReviewArgs", args))
        return
    if args.command == "promote-r103-review-state":
        _promote_r103_review_state(cast("_PromoteR103ReviewStateArgs", args))
        return
    if args.command == "generate-corpus-baseline":
        asyncio.run(_generate_corpus(cast("_CorpusBaselineArgs", args)))
        return
    if args.command == "generate-r101-conservation":
        asyncio.run(_generate_r101_conservation(cast("_R101ConservationArgs", args)))
        return
    if args.command == "validate-r101-publication":
        publication_args = cast("_R101PublicationArgs", args)
        report = load_r101_conservation_report(publication_args.report)
        validate_r101_publication(report)
        if (
            report.content_authorization.authorized_digest
            != publication_args.authorization_digest
        ):
            raise R101ConservationValidationError(
                "content-authorization-digest-mismatch"
            )
        return
    if args.command == "prepare-r101-review-packet":
        asyncio.run(_prepare_r101_review(cast("_PrepareR101ReviewArgs", args)))
        return
    if args.command == "import-r101-review-decisions":
        _import_r101_review(cast("_ImportR101ReviewArgs", args))
        return
    if args.command == "dry-run-r101-decision-expansion":
        _dry_run_r101_decision_expansion(cast("_DryRunR101DecisionExpansionArgs", args))
        return
    if args.command == "generate-r101-collapse-policy":
        asyncio.run(
            _generate_r101_collapse_policy(
                cast("_GenerateR101CollapsePolicyArgs", args)
            )
        )
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
