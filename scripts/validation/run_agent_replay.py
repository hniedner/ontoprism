#!/usr/bin/env python3
"""Run fixed operations, including mutating local container/tmp operations.

Operations return zero on success. Contract refusals and required-command failures raise
``AgentReplayInputError``; local filesystem setup failures may propagate as their native
environment exceptions. Cleanup failures are attached without replacing an earlier
operation failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, assert_never, cast

import yaml

from ontolib.decomposition.artifact_contract import COMPOSE_PROJECT
from ontolib.decomposition.run_artifacts import (
    ArtifactManifest,
    ArtifactUnavailableRecord,
    GeneratorBinding,
    ParentManifestBinding,
    RetentionBinding,
    SourceIdentity,
    publish_generation,
    reconcile_missing_unavailable_references,
    resolve_parent_manifest,
    write_legacy_in_place_manifest,
    write_unavailable_record,
)

from .docker_selectors import DOCKER_SELECTOR_VARIABLES

if TYPE_CHECKING:
    from collections.abc import Iterator

_RUN_ID = re.compile(
    r"neoplasm-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FILLER = re.compile(r"(?:C[0-9]+|MINT-[0-9a-f]+)")
_MAX_INSPECTED_RUNS = 8
_PARENT_MANIFEST_ARGUMENT_COUNT = 2
_PROMOTION_MANIFEST_ARGUMENT_COUNT = 4
_DIAGNOSTIC_TIMEOUT_SECONDS = 20
_EXPECTED_R101_STRUCTURAL_ADDITIONS = 39
_MIN_SPECIFICITY_FILLERS = 2
_GATE_TIMEOUT_SECONDS = 3_600
_COMPOSE_TIMEOUT_SECONDS = 1_800
_MAX_DIAGNOSTIC_CHARS = 8_192
_MAX_R101_STRUCTURAL_ROWS = 2_500
_MAX_R101_METADATA_PAIRS = 40_000
_MAX_R101_METADATA_TRANSITIONS = 100
_MAX_R101_METADATA_CONCEPTS = 16_000
_MAX_R101_INSPECTION_BYTES = 5_000_000
_R101_PAIR_ARGUMENT_COUNT = 2
_R101_REPORT_ARGUMENT_COUNT = 3
_GENERATION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_POC_DIR = Path("tmp/podman-poc")
_PODMAN_PROJECT = COMPOSE_PROJECT
_PODMAN_VOLUME = f"{_PODMAN_PROJECT}_ontoprism_pg_data"
_PODMAN_MACHINE = "ontoprism-vm"
_PODMAN_DOCKER_CONTEXT = "ontoprism-podman"
_PODMAN_DOCKER_CONTEXT_DESCRIPTION = "OntoPrism rootless Podman machine"
_PODMAN = "/opt/homebrew/bin/podman"
_DOCKER = "/opt/homebrew/bin/docker"
_DOCKER_COMPOSE = "/opt/homebrew/bin/docker-compose"
_PDM = "/opt/homebrew/bin/pdm"
type ComposeService = Literal["postgres", "qlever-ncit", "qlever-uberon"]
_COMPOSE_SERVICES: tuple[ComposeService, ...] = (
    "postgres",
    "qlever-ncit",
    "qlever-uberon",
)
_POSTGRES_IMAGE = (
    "pgvector/pgvector@sha256:"
    "a947c45cdc5906a1bc951f20a8709e321256343ee0f251e4ae00b5e7def4e6da"
)
_SECRET_VALUE = re.compile(
    r"(?i)([\"']?[A-Z0-9_-]*(?:PASSWORD|PASSWD|TOKEN|SECRET|API[_-]?KEY)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
_URL_CREDENTIALS = re.compile(
    r"((?:https?|postgresql(?:\+asyncpg)?)://[^\s:/]+:)[^@\s]+(@)", re.I
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_CODEPOINT_LIMIT = 32
_CONSOLIDATION_VALUE_COUNT = 3
_NORMALIZED_GROUP_POLICY_ROW_COUNT = 15
_POLICY_CANDIDATE_PARENT_ARGUMENT_COUNT = 4
_GROUP_REVIEW_PARENT_ARGUMENT_COUNT = 4
_GROUP_REVIEW_PARENT_COUNT = 2
_POLICY_PROMOTION_PARENT_ARGUMENT_COUNT = 6
_GROUPING_DETECTOR_PARENT_ARGUMENT_COUNT = 6
_GROUPING_DETECTOR_PARENT_COUNT = 3
_CURRENT_REPLAY_SAMPLE_SHA256 = (
    "d229aa9e7cf28bfcf64d5bfbedb6820a48e217dc8ff83f3c6abaf8efad180477"
)


class AgentReplayInputError(ValueError):
    """The requested operation is outside the fixed replay contract."""


class CapturedCommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        shell: Literal[False],
        check: Literal[False],
        timeout: float | None,
        capture_output: bool,
        text: Literal[True],
        env: dict[str, str] | None = None,
    ) -> CapturedCommandResult: ...


class Operation(Protocol):
    def __call__(self, values: list[str], root: Path, runner: CommandRunner) -> int: ...


@dataclass(frozen=True)
class ArtifactInventory:
    entries: tuple[dict[str, object], ...]
    identity: str
    logical_bytes: int
    allocated_bytes: int


@dataclass(frozen=True)
class ConsolidationEntry:
    source_relative: str
    source: Path
    destination_relative: str
    destination: Path
    duplicate_of_relative: str | None
    inventory: ArtifactInventory


@dataclass(frozen=True)
class ConsolidationContext:
    manifest_relative: str
    manifest_path: Path
    report_relative: str
    report_path: Path
    manifest_bytes: bytes
    manifest_digest: str
    source_specs: tuple[dict[str, object], ...]


async def _inspect_decomposition_runs_async(
    run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    settings = importlib.import_module("backend.config").get_settings()
    database = importlib.import_module("backend.db")
    inspect = importlib.import_module(
        "ontolib.decomposition.run_inspection"
    ).inspect_decomposition_runs
    engine = database.make_engine(settings.database_url)
    try:
        return [item.model_dump(mode="json") for item in await inspect(engine, run_ids)]
    finally:
        await database.dispose_engine(engine)


def _inspect_decomposition_runs(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del root, runner
    if not values or len(values) > _MAX_INSPECTED_RUNS:
        raise AgentReplayInputError("inspect-decomposition-runs requires 1-8 run IDs")
    if any(_RUN_ID.fullmatch(value) is None for value in values):
        raise AgentReplayInputError("invalid decomposition run ID")
    payload = asyncio.run(_inspect_decomposition_runs_async(tuple(values)))
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


def _validated_r101_pair(values: list[str], *, operation: str) -> tuple[str, str]:
    if (
        len(values) != _R101_PAIR_ARGUMENT_COUNT
        or any(_RUN_ID.fullmatch(value) is None for value in values)
        or values[0] == values[1]
    ):
        raise AgentReplayInputError(f"{operation} requires two distinct run IDs")
    return values[0], values[1]


async def _qualify_current_r101_comparator_async(
    old_run_id: str,
    new_run_id: str,
    baseline: Path,
    old_artifact: Path,
    new_artifact: Path,
    output: Path,
) -> None:
    settings = importlib.import_module("backend.config").get_settings()
    database = importlib.import_module("backend.db")
    provenance = importlib.import_module("ontolib.decomposition.provenance")
    comparator = importlib.import_module("ontolib.decomposition.r101_comparator")
    corpus = importlib.import_module("ontolib.decomposition.corpus_baseline")
    engine = database.make_engine(settings.database_url)
    try:
        store = provenance.ProvenanceStore(database.make_sessionmaker(engine))
        old_run = await store.completed_comparator_run_for_evidence(old_run_id)
        new_run = await store.completed_comparator_run_for_evidence(new_run_id)
        qualification = comparator.qualify_r101_comparator(
            old_run=old_run,
            new_run=new_run,
            old_baseline=corpus.load_corpus_baseline(baseline),
            old_artifact=old_artifact,
            new_artifact=new_artifact,
        )
        comparator.write_r101_comparator_qualification(output, qualification)
        print(
            json.dumps(
                {
                    "old_run_id": qualification.old.run_id,
                    "new_run_id": qualification.new.run_id,
                    "query_identity": qualification.query_identity,
                    "shared_canary_constituents": len(
                        qualification.shared_canary_constituents
                    ),
                    "qualification_identity": qualification.qualification_identity,
                },
                sort_keys=True,
                indent=2,
            )
        )
    finally:
        await database.dispose_engine(engine)


def _qualify_current_r101_comparator(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    old_run_id, new_run_id = _validated_r101_pair(
        values, operation="qualify-current-r101-comparator"
    )
    baseline, old_artifact, new_artifact = (
        Path(path)
        for path in _require_files(
            root,
            (
                "tmp/m1-6-prechange-v4-corpus-baseline.json",
                "tmp/m1-6-prechange-v4-full-corpus.ttl",
                "tmp/m1-6-current-full-corpus.ttl",
            ),
        )
    )
    asyncio.run(
        _qualify_current_r101_comparator_async(
            old_run_id,
            new_run_id,
            baseline,
            old_artifact,
            new_artifact,
            root / "tmp/m1-6-r101-v5-comparator-qualification.json",
        )
    )
    return 0


def _generate_current_r101_conservation(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if (
        len(values) != _R101_REPORT_ARGUMENT_COUNT
        or _GENERATION_ID.fullmatch(values[2]) is None
    ):
        raise AgentReplayInputError(
            "generate-current-r101-conservation requires two distinct run IDs "
            "and one generation ID"
        )
    old_run_id, new_run_id = _validated_r101_pair(
        values[:2], operation="generate-current-r101-conservation"
    )
    generation_id = values[2]
    _script, source_manifest, baseline, old_artifact, new_artifact = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "tmp/m1-6-prechange-v4-corpus-baseline.json",
            "tmp/m1-6-prechange-v4-full-corpus.ttl",
            "tmp/m1-6-current-full-corpus.ttl",
        ),
    )
    staging = Path(tempfile.mkdtemp(prefix=".staging-r101-", dir=root / "tmp"))
    qualification_output = staging / "comparator-qualification.json"
    report_output = staging / "conservation.json.gz"
    command = [
        _PDM,
        "run",
        "adjudication",
        "generate-r101-conservation",
        "--source-manifest",
        source_manifest,
        "--baseline",
        baseline,
        "--run-id",
        old_run_id,
        "--new-run-id",
        new_run_id,
        "--old-artifact",
        old_artifact,
        "--new-artifact",
        new_artifact,
        "--qualification-output",
        str(qualification_output),
        "--endpoint",
        "http://localhost:7888",
        "--output",
        str(report_output),
        "--pre-resume-proof-identity",
        "f3c321c38deb8478f7a1abfa5c1edb1ef9ac3daf793d0dfe8d1e758eb62d2018",
        "--resume-dry-run-identity",
        "2f5a0530f72028353a32b050a7e7a06a1880d7bcfe1aad4bcacd902333e7bd98",
        "--mixed-cohort-identity",
        "dda9c71a8a777e451a08fe81e4e2bae799f85e5f2c4984a90e5d95d71784777a",
    ]
    try:
        result = _run(command, root, runner)
        if result != 0:
            return result
        conservation = importlib.import_module(
            "ontolib.decomposition.r101_conservation"
        )
        comparator = importlib.import_module("ontolib.decomposition.r101_comparator")
        report = conservation.load_r101_conservation_report(report_output)
        qualification = comparator.load_r101_comparator_qualification(
            qualification_output
        )
        if (
            report.old_run_id != old_run_id
            or report.new_run_id != new_run_id
            or qualification.old.run_id != old_run_id
            or qualification.new.run_id != new_run_id
            or report.comparator_qualification_identity
            != qualification.qualification_identity
        ):
            raise AgentReplayInputError(
                "generated R101 report differs from the requested qualified pair"
            )
        generator_files = (
            Path(_script),
            Path(__file__),
            Path(cast("str", conservation.__file__)),
            Path(
                cast(
                    "str",
                    importlib.import_module(
                        "ontolib.decomposition.provenance"
                    ).__file__,
                )
            ),
        )
        generator_identity = hashlib.sha256(
            b"\0".join(path.read_bytes() for path in generator_files)
        ).hexdigest()
        manifest = publish_generation(
            artifacts_root=root / "tmp/artifacts/v1/generations",
            family="m1-6-r101-conservation",
            generation_id=generation_id,
            run_id=new_run_id,
            artifact_sources={
                "artifacts/conservation.json.gz": report_output,
                "artifacts/comparator-qualification.json": qualification_output,
            },
            parents=(),
            generator=GeneratorBinding(
                identity=f"sha256:{generator_identity}",
                command=(
                    "pdm",
                    "run",
                    "agent-replay",
                    "generate-current-r101-conservation",
                    old_run_id,
                    new_run_id,
                    generation_id,
                ),
            ),
            sources=tuple(
                SourceIdentity(
                    name=name,
                    identity=f"sha256:{hashlib.sha256(Path(path).read_bytes()).hexdigest()}",
                )
                for name, path in (
                    ("ncit-source-manifest", source_manifest),
                    ("prechange-corpus-baseline", baseline),
                    ("prechange-full-corpus-ttl", old_artifact),
                    ("current-full-corpus-ttl", new_artifact),
                )
            ),
            retention=RetentionBinding(
                retention_class="referenced-full-store-report",
                owner="decomposition",
                expires_at=None,
            ),
        )
        print(
            json.dumps(
                {
                    "generation_id": generation_id,
                    "manifest_identity": manifest.manifest_identity,
                    "report_identity": report.report_identity,
                    "qualification_identity": qualification.qualification_identity,
                    "read_only": True,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        shutil.rmtree(staging)


def _generate_current_corpus_baseline(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if len(values) != 1 or _RUN_ID.fullmatch(values[0]) is None:
        raise AgentReplayInputError(
            "generate-current-corpus-baseline requires one valid run ID"
        )
    (run_id,) = values
    _script, source_manifest, artifact = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "tmp/m1-6-current-full-corpus.ttl",
        ),
    )
    return _run(
        [
            _PDM,
            "run",
            "adjudication",
            "generate-corpus-baseline",
            "--source-manifest",
            source_manifest,
            "--run-id",
            run_id,
            "--artifact",
            artifact,
            "--output",
            str(root / "tmp/m1-6-current-corpus-baseline.json"),
        ],
        root,
        runner,
    )


def _promote_current_r101_evidence(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError(
            "promote-current-r101-evidence accepts no arguments"
        )
    report_path, baseline_path, qualification_path = (
        Path(item)
        for item in _require_files(
            root,
            (
                "tmp/m1-6-r101-v5-conservation.json.gz",
                "tmp/m1-6-current-corpus-baseline.json",
                "tmp/m1-6-r101-v5-comparator-qualification.json",
            ),
        )
    )
    conservation = importlib.import_module("ontolib.decomposition.r101_conservation")
    baseline_module = importlib.import_module("ontolib.decomposition.corpus_baseline")
    comparator_module = importlib.import_module("ontolib.decomposition.r101_comparator")
    report = conservation.load_r101_conservation_report(report_path)
    baseline = baseline_module.load_corpus_baseline(baseline_path)
    qualification = comparator_module.load_r101_comparator_qualification(
        qualification_path
    )
    if (
        report.old_run_id != qualification.old.run_id
        or report.new_run_id != qualification.new.run_id
        or report.old_run_id == report.new_run_id
        or report.r101_occurrence_certification != "complete"
        or report.non_r101_enumeration != "complete"
        or report.explanation != "incomplete"
        or report.semantic_isolation != "partial-unqualified"
        or report.execution_comparability != "unqualified"
        or report.fully_controlled
        or report.all_controls_equal
        or report.causal_attribution != "prohibited"
        or report.authorization != "pending"
        or report.publication_gate != "blocked"
        or report.comparator_qualification_identity
        != qualification.qualification_identity
    ):
        raise AgentReplayInputError(
            "current R101 report does not certify the fixed comparator pair"
        )
    if (
        baseline.run_id != report.new_run_id
        or baseline.run_fingerprint_identity != report.new_run_fingerprint_identity
        or baseline.representation_identity != report.new_representation_identity
    ):
        raise AgentReplayInputError(
            "current corpus baseline does not bind the qualified new run"
        )
    golden = root / "ontolib/tests/decomposition/golden"
    if not golden.is_dir():
        raise AgentReplayInputError("golden evidence directory does not exist")
    (golden / "neoplasm-r101-v5-conservation.json.gz").write_bytes(
        report_path.read_bytes()
    )
    (golden / "neoplasm-current-corpus-baseline.json").write_bytes(
        baseline_path.read_bytes()
    )
    return 0


def _record_current_r101_diagnostic(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    """Record incomplete fixed-pair evidence without representing it as promoted."""
    del runner
    if values:
        raise AgentReplayInputError(
            "record-current-r101-diagnostic accepts no arguments"
        )
    (report_path_raw,) = _require_files(
        root, ("tmp/m1-6-r101-v5-conservation.json.gz",)
    )
    report_path = Path(report_path_raw)
    conservation = importlib.import_module("ontolib.decomposition.r101_conservation")
    report = conservation.load_r101_conservation_report(report_path)
    evidence = report.non_r101_delta_evidence
    expected_raw = (
        len(evidence.rows)
        + len(evidence.classified_rows)
        + 2 * len(evidence.metadata_deltas)
    )
    if (
        _RUN_ID.fullmatch(report.old_run_id) is None
        or _RUN_ID.fullmatch(report.new_run_id) is None
        or report.old_run_id == report.new_run_id
        or report.r101_occurrence_certification != "blocked"
        or report.publication_gate != "blocked"
        or evidence.raw_typed_delta_count != expected_raw
        or not (evidence.rows or evidence.metadata_deltas)
    ):
        raise AgentReplayInputError(
            "current R101 diagnostic is not an incomplete fixed-pair report"
        )
    golden = root / "ontolib/tests/decomposition/golden"
    if not golden.is_dir():
        raise AgentReplayInputError("golden evidence directory does not exist")
    (golden / "neoplasm-r101-v5-conservation.json.gz").write_bytes(
        report_path.read_bytes()
    )
    return 0


def _r101_structural_rows(evidence: Any) -> list[dict[str, Any]]:
    if len(evidence.rows) > _MAX_R101_STRUCTURAL_ROWS:
        raise AgentReplayInputError("R101 structural row output exceeds bounded limit")
    result: list[dict[str, Any]] = []
    for row in evidence.rows:
        item = row.model_dump(mode="json")
        item["direction"] = item.pop("change")
        result.append(item)
    result.sort(
        key=lambda item: (
            item["direction"],
            item["concept_code"],
            item["axis"],
            item["filler_code"],
            json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    )
    return result


def _r101_metadata_summary(evidence: Any) -> dict[str, object]:
    if len(evidence.metadata_deltas) > _MAX_R101_METADATA_PAIRS:
        raise AgentReplayInputError("R101 metadata pair output exceeds bounded limit")
    transitions: Counter[tuple[str, str, str]] = Counter()
    per_concept: Counter[str] = Counter()
    for delta in evidence.metadata_deltas:
        old = delta.old.model_dump(mode="json")
        new = delta.new.model_dump(mode="json")
        per_concept[delta.old.concept_code] += 1
        for field in delta.changed_fields:
            old_value = json.dumps(old[field], sort_keys=True, separators=(",", ":"))
            new_value = json.dumps(new[field], sort_keys=True, separators=(",", ":"))
            transitions[(field, old_value, new_value)] += 1
    if len(transitions) > _MAX_R101_METADATA_TRANSITIONS:
        raise AgentReplayInputError(
            "R101 metadata transition output exceeds bounded limit"
        )
    if len(per_concept) > _MAX_R101_METADATA_CONCEPTS:
        raise AgentReplayInputError(
            "R101 metadata concept output exceeds bounded limit"
        )
    return {
        "pair_count": len(evidence.metadata_deltas),
        "transition_cross_tab": [
            {
                "changed_field": field,
                "old_value": json.loads(old),
                "new_value": json.loads(new),
                "pair_count": count,
            }
            for (field, old, new), count in sorted(transitions.items())
        ],
        "per_concept_counts": [
            {"concept_code": concept, "pair_count": count}
            for concept, count in sorted(per_concept.items())
        ],
    }


def _r101_verification(report: Any, conservation: Any) -> dict[str, object]:
    evidence = report.non_r101_delta_evidence
    raw_recomputed = (
        len(evidence.rows)
        + len(evidence.classified_rows)
        + 2 * len(evidence.metadata_deltas)
    )
    raw_verified = evidence.raw_typed_delta_count == raw_recomputed
    recomputed_json, recomputed_tsv, recomputed_report = (
        conservation.recompute_r101_report_identities(report)
    )
    identities_verified = (
        recomputed_json == report.json_identity
        and recomputed_tsv == report.tsv_identity
        and recomputed_report == report.report_identity
    )
    if not identities_verified or not raw_verified:
        raise AgentReplayInputError(
            "R101 report recomputation differs from recorded evidence"
        )
    return {
        "count_reconciliation": {
            "structural_row_count": len(evidence.rows),
            "metadata_pair_count": len(evidence.metadata_deltas),
            "classified_row_count": len(evidence.classified_rows),
            "raw_typed_delta_count": evidence.raw_typed_delta_count,
            "recomputed_raw_typed_delta_count": raw_recomputed,
            "verified": raw_verified,
        },
        "identity_verification": {
            "status": "verified",
            "model_validation": "verified",
            "json_identity": {
                "recorded": report.json_identity,
                "recomputed": recomputed_json,
                "verified": recomputed_json == report.json_identity,
            },
            "tsv_identity": {
                "recorded": report.tsv_identity,
                "recomputed": recomputed_tsv,
                "verified": recomputed_tsv == report.tsv_identity,
            },
            "report_identity": {
                "recorded": report.report_identity,
                "recomputed": recomputed_report,
                "verified": recomputed_report == report.report_identity,
            },
        },
    }


def _inspect_r101_report(values: list[str], root: Path, runner: CommandRunner) -> int:
    del runner
    if len(values) != 1:
        raise AgentReplayInputError("inspect-r101-report requires one report path")
    relative = _validated_repository_relative(values[0], label="R101 report")
    path = root / relative
    _require_no_symlink_components(path, root=root, label="R101 report")
    if not path.is_file() or not path.name.endswith(".json.gz"):
        raise AgentReplayInputError("R101 report must be an existing .json.gz file")
    conservation = importlib.import_module("ontolib.decomposition.r101_conservation")
    try:
        report = conservation.load_r101_conservation_report(path)
    except (OSError, ValueError) as exc:
        raise AgentReplayInputError(
            f"R101 report failed strict validation: {exc}"
        ) from exc
    evidence = report.non_r101_delta_evidence
    result = {
        "report_binding": {
            "old_run_id": report.old_run_id,
            "new_run_id": report.new_run_id,
            "query_identity": evidence.query_identity,
            "report_identity": report.report_identity,
            "r101_occurrence_inventory_identity": (
                report.r101_occurrence_inventory_identity
            ),
            "non_r101_typed_inventory_identity": (
                report.non_r101_typed_inventory_identity
            ),
        },
        "statuses": {
            "r101_occurrence_certification": report.r101_occurrence_certification,
            "non_r101_enumeration": report.non_r101_enumeration,
            "explanation": report.explanation,
            "semantic_isolation": report.semantic_isolation,
            "execution_comparability": report.execution_comparability,
            "fully_controlled": report.fully_controlled,
            "all_controls_equal": report.all_controls_equal,
            "causal_attribution": report.causal_attribution,
            "authorization": report.authorization,
            "publication": report.publication_gate,
        },
        **_r101_verification(report, conservation),
        "structural_rows": _r101_structural_rows(evidence),
        "metadata_pairs": _r101_metadata_summary(evidence),
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    output = json.dumps(result, sort_keys=True, indent=2)
    if len(output.encode()) > _MAX_R101_INSPECTION_BYTES:
        raise AgentReplayInputError("R101 inspection output exceeds bounded byte limit")
    print(output)
    return 0


async def _classify_mixed_chain_delta(
    *,
    delta: Any,
    rows: tuple[Any, ...],
    client: Any,
    source_identity: str,
    extract: Any,
    fs: Any,
    inventory_module: Any,
    models: Any,
    stated: Any,
) -> Any | None:
    fillers = {row.source_filler for row in rows}
    if (
        delta.filler_code not in fillers
        or len(fillers) < _MIN_SPECIFICITY_FILLERS
        or len(delta.source_roles) != 1
    ):
        return None
    ancestor_rows = await client.select(
        stated.build_ancestor_pairs_query(fillers),
        required_variables={"ancestor", "descendant"},
    )
    ancestor_pairs = extract.ancestor_pairs_from_rows(ancestor_rows)
    part_pairs = await stated.resolve_part_of_pairs(client, fillers)
    occurrences = tuple(
        fs.RoutedOccurrence(
            restriction=models.RoleRestriction(
                role_code=row.source_role,
                filler_code=row.source_filler,
                anchoring_genus=row.anchoring_genus,
                source_definition_ids=(row.source_fact_id,),
                source_occurrence_ids=(row.source_occurrence_id,),
                source_kind="stated",
            ),
            normalized_axis=row.normalized_axis,
            semantic_route=row.semantic_route,
            semantic_type=row.semantic_type,
            source_fact_id=row.source_fact_id,
            source_occurrence_id=row.source_occurrence_id,
        )
        for row in rows
    )
    plan = fs.RoutedPlan(
        occurrences=occurrences,
        parent_morphologies=(),
        specificity_groups=((delta.axis, tuple(sorted(fillers))),),
        comparison_groups=((delta.axis, tuple(sorted(fillers))),),
        protected_pairs=frozenset(
            (row.normalized_axis, row.source_filler)
            for row in rows
            if row.policy_decision_identity is not None
        ),
        policy_decisions=tuple(
            (row.source_occurrence_id, row.policy_decision_identity)
            for row in rows
            if row.policy_decision_identity is not None
        ),
        source_identity=source_identity,
    )
    part_of = {(pair.part, pair.whole) for pair in part_pairs}
    selected = fs.diagnose_historical_collapse_dispositions(
        plan,
        extract.make_is_ancestor(set(ancestor_pairs)),
        purpose=fs.DiagnosticReductionPurpose.HISTORICAL_MIXED_CHAIN_RECONSTRUCTION,
        is_part_of=lambda part, whole, pairs=part_of: (part, whole) in pairs,
    )
    broad = tuple(
        item
        for item in selected.dispositions
        if item.source_filler == delta.filler_code
    )
    if not broad or any(item.kind != "collapsed-mixed" for item in broad):
        return None
    first = broad[0]
    if any(
        item.retained_filler != first.retained_filler
        or item.specificity_path != first.specificity_path
        for item in broad
    ):
        return None
    return inventory_module.MixedChainCandidate(
        concept_code=delta.concept_code,
        axis=delta.axis,
        source_role=delta.source_roles[0],
        broad_filler=delta.filler_code,
        terminal_filler=first.retained_filler,
        source_occurrence_ids=tuple(
            sorted(item.source_occurrence_id for item in broad)
        ),
        specificity_path=first.specificity_path,
    )


async def _generate_mixed_chain_inventory_async(
    report_path: Path, output: Path
) -> None:
    settings = importlib.import_module("backend.config").get_settings()
    database = importlib.import_module("backend.db")
    extract = importlib.import_module("ontolib.decomposition.extract")
    fs = importlib.import_module("ontolib.decomposition.filler_selection")
    inventory_module = importlib.import_module(
        "ontolib.decomposition.mixed_chain_inventory"
    )
    models = importlib.import_module("ontolib.decomposition.models")
    provenance = importlib.import_module("ontolib.decomposition.provenance")
    stated = importlib.import_module("ontolib.decomposition.stated_queries")
    ncit_client = importlib.import_module("ontolib.terminologies.ncit.client")
    report = inventory_module.load_historical_mixed_chain_source_report(report_path)
    structural = report.non_r101_delta_evidence.rows
    if len(structural) != _EXPECTED_R101_STRUCTURAL_ADDITIONS or any(
        row.change != "added" for row in structural
    ):
        raise AgentReplayInputError("mixed-chain inventory requires exact 39 additions")
    codes = tuple(sorted({row.concept_code for row in structural}))
    engine = database.make_engine(settings.database_url)
    try:
        store = provenance.ProvenanceStore(database.make_sessionmaker(engine))
        run = await store.historical_mixed_chain_run_for_evidence(report.new_run_id)
        persisted = await store.selector_occurrences_for_codes(report.new_run_id, codes)
    finally:
        await database.dispose_engine(engine)
    by_concept_axis: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in persisted:
        by_concept_axis[(row.concept_code, row.normalized_axis)].append(row)
    candidates = []
    unclassified: set[str] = set()
    async with ncit_client.ncit_sparql_client(settings.ncit_sparql_url) as client:
        for delta in structural:
            rows = tuple(by_concept_axis[(delta.concept_code, delta.axis)])
            candidate = await _classify_mixed_chain_delta(
                delta=delta,
                rows=rows,
                client=client,
                source_identity=run.fingerprint.source_identity,
                extract=extract,
                fs=fs,
                inventory_module=inventory_module,
                models=models,
                stated=stated,
            )
            if candidate is None:
                unclassified.add(delta.concept_code)
                continue
            candidates.append(candidate)
    inventory = inventory_module.MixedChainInventory.create(
        source_identity=run.fingerprint.source_identity,
        worklist_identity=inventory_module.mixed_chain_worklist_identity(
            run.fingerprint.worklist
        ),
        worklist_count=len(run.fingerprint.worklist),
        selector_identity=inventory_module.HISTORICAL_MIXED_CHAIN_SELECTOR_IDENTITY,
        source_run_id=run.run_id,
        source_report_identity=report.report_identity,
        candidates=tuple(candidates),
        unclassified_codes=tuple(unclassified),
    )
    inventory_module.write_mixed_chain_inventory(output, inventory)
    print(json.dumps(inventory.model_dump(mode="json"), sort_keys=True, indent=2))


def _generate_mixed_chain_inventory(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError(
            "generate-mixed-chain-inventory accepts no arguments"
        )
    report = (
        root / "ontolib/tests/decomposition/golden/"
        "neoplasm-r101-v5-2b39-historical-conservation.json.gz"
    )
    output = root / "tmp/m1-6-mixed-chain-inventory.json"
    output.unlink(missing_ok=True)
    asyncio.run(_generate_mixed_chain_inventory_async(report, output))
    return 0


def _record_mixed_chain_inventory(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError("record-mixed-chain-inventory accepts no arguments")
    inventory_module = importlib.import_module(
        "ontolib.decomposition.mixed_chain_inventory"
    )
    source = root / "tmp/m1-6-mixed-chain-inventory.json"
    target = (
        root / "ontolib/src/ontolib/decomposition/data/"
        "neoplasm_mixed_chain_inventory.json"
    )
    inventory = inventory_module.load_mixed_chain_inventory(source)
    if (
        inventory.candidate_count != _EXPECTED_R101_STRUCTURAL_ADDITIONS
        or inventory.unclassified_codes
    ):
        raise AgentReplayInputError("mixed-chain inventory is not complete")
    inventory_module.write_mixed_chain_inventory(target, inventory)
    print(f"recorded {inventory.identity} at {target.relative_to(root)}")
    return 0


async def _generate_mixed_chain_corrected_projection_async(
    inventory_path: Path, report_path: Path, output: Path
) -> None:
    settings = importlib.import_module("backend.config").get_settings()
    database = importlib.import_module("backend.db")
    inventory_module = importlib.import_module(
        "ontolib.decomposition.mixed_chain_inventory"
    )
    projection_module = importlib.import_module(
        "ontolib.decomposition.mixed_chain_projection"
    )
    provenance = importlib.import_module("ontolib.decomposition.provenance")
    inventory = inventory_module.load_mixed_chain_inventory(inventory_path)
    report = inventory_module.load_historical_mixed_chain_source_report(report_path)
    if (
        inventory.source_run_id != report.new_run_id
        or inventory.source_report_identity != report.report_identity
    ):
        raise AgentReplayInputError("projection inventory source report differs")
    selector_identity = inventory.selector_identity
    engine = database.make_engine(settings.database_url)
    try:
        store = provenance.ProvenanceStore(database.make_sessionmaker(engine))
        occurrences = await store.selector_occurrences_for_codes(
            inventory.source_run_id, inventory.candidate_codes
        )
        states = await store.projection_state_for_codes(
            inventory.source_run_id, inventory.candidate_codes
        )
    finally:
        await database.dispose_engine(engine)
    occurrences_by_code: dict[str, list[Any]] = defaultdict(list)
    for row in occurrences:
        occurrences_by_code[row.concept_code].append(row)
    states_by_code = {row.concept_code: row for row in states}
    projections = tuple(
        projection_module.project_mixed_chain_candidate(
            candidate=candidate,
            occurrences=tuple(occurrences_by_code[candidate.concept_code]),
            before_constituents=states_by_code[candidate.concept_code].constituents,
            before_dispositions=states_by_code[candidate.concept_code].dispositions,
            source_identity=inventory.source_identity,
        )
        for candidate in inventory.candidates
    )
    artifact = projection_module.create_corrected_projection(
        source_run_id=inventory.source_run_id,
        source_report_identity=inventory.source_report_identity,
        source_identity=inventory.source_identity,
        selector_identity=selector_identity,
        inventory_identity=inventory.identity,
        expected_candidate_codes=inventory.candidate_codes,
        projections=projections,
    )
    projection_module.write_corrected_projection(output, artifact)
    print(
        json.dumps(
            {
                "projection_identity": artifact.projection_identity,
                "candidate_count": artifact.candidate_count,
                "constituent_transition_counts": (
                    artifact.constituent_transition_counts.model_dump(mode="json")
                ),
                "metadata_transition_counts": (
                    artifact.metadata_transition_counts.model_dump(mode="json")
                ),
                "disposition_transition_count": (artifact.disposition_transition_count),
            },
            sort_keys=True,
        )
    )


def _generate_mixed_chain_corrected_projection(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError(
            "generate-mixed-chain-corrected-projection accepts no arguments"
        )
    inventory = (
        root / "ontolib/src/ontolib/decomposition/data/"
        "neoplasm_mixed_chain_inventory.json"
    )
    report = (
        root / "ontolib/tests/decomposition/golden/"
        "neoplasm-r101-v5-2b39-historical-conservation.json.gz"
    )
    family = "m1-6-mixed-chain-corrected-projection"
    generation_id, staging = _candidate_staging(root, family)
    output = staging / "corrected-projection.json"
    try:
        asyncio.run(
            _generate_mixed_chain_corrected_projection_async(
                inventory,
                report,
                output,
            )
        )
        manifest = publish_generation(
            artifacts_root=root / "tmp/artifacts/v1/generations",
            family=family,
            generation_id=generation_id,
            run_id=None,
            artifact_sources={"artifacts/corrected-projection.json": output},
            parents=(),
            generator=GeneratorBinding(
                identity=_git_head_identity(root),
                command=(
                    "pdm",
                    "run",
                    "agent-replay",
                    "generate-mixed-chain-corrected-projection",
                ),
            ),
            sources=(
                SourceIdentity(
                    name="mixed-chain-inventory",
                    identity=f"sha256:{hashlib.sha256(inventory.read_bytes()).hexdigest()}",
                ),
                SourceIdentity(
                    name="historical-r101-report",
                    identity=f"sha256:{hashlib.sha256(report.read_bytes()).hexdigest()}",
                ),
            ),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )
        print(
            json.dumps(
                {
                    "manifest_path": (
                        root
                        / "tmp/artifacts/v1/generations"
                        / family
                        / generation_id
                        / "manifest.json"
                    )
                    .relative_to(root)
                    .as_posix(),
                    "manifest_identity": manifest.manifest_identity,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _record_mixed_chain_corrected_projection(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    manifest, manifest_path, _binding = _resolve_candidate_parent(
        values,
        root,
        expected_family="m1-6-mixed-chain-corrected-projection",
    )
    projection_module = importlib.import_module(
        "ontolib.decomposition.mixed_chain_projection"
    )
    generated = _bound_artifact_path(
        manifest, manifest_path, "artifacts/corrected-projection.json"
    )
    artifact = projection_module.load_corrected_projection(generated)
    destination = (
        root / "ontolib/tests/decomposition/golden/"
        "neoplasm-r101-v5-corrected-projection.json"
    )
    projection_module.write_corrected_projection(destination, artifact)
    print(f"recorded {artifact.projection_identity} at {destination.relative_to(root)}")
    return 0


def _subprocess_runner(
    arguments: list[str],
    *,
    cwd: Path,
    shell: Literal[False],
    check: Literal[False],
    timeout: float | None,
    capture_output: bool,
    text: Literal[True],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        arguments,
        cwd=cwd,
        shell=shell,
        check=check,
        timeout=timeout,
        capture_output=capture_output,
        text=text,
        env=env,
    )


def _require_files(root: Path, relatives: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    for relative in relatives:
        path = root / relative
        if not path.is_file():
            raise AgentReplayInputError(f"required input does not exist: {relative}")
        print(f"verified input: {relative}", file=sys.stderr)
        paths.append(str(path))
    return paths


def _run(command: list[str], root: Path, runner: CommandRunner) -> int:
    result = runner(
        command,
        cwd=root,
        shell=False,
        check=False,
        timeout=None,
        capture_output=False,
        text=True,
    )
    return result.returncode


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AgentReplayInputError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_strict_json(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(data, object_pairs_hook=_strict_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError(f"{label} is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise AgentReplayInputError(f"{label} must be a JSON object")
    return payload


def _validated_repository_relative(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AgentReplayInputError(f"{label} must be a non-empty repository path")
    path = Path(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < _CONTROL_CODEPOINT_LIMIT for character in value)
        or any(character in value for character in "*?[]{}")
        or path.as_posix() != value
    ):
        raise AgentReplayInputError(f"{label} must be a normalized relative path")
    return value


def _require_no_symlink_components(path: Path, *, root: Path, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise AgentReplayInputError(f"{label} escapes the repository") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise AgentReplayInputError(f"{label} contains a symlink: {current}")


def _artifact_inventory(path: Path) -> ArtifactInventory:
    entries: list[dict[str, object]] = []
    logical_bytes = 0
    allocated_bytes = 0

    def visit(current: Path, relative: str) -> None:
        nonlocal allocated_bytes, logical_bytes
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise AgentReplayInputError(f"artifact tree contains a symlink: {current}")
        allocated_bytes += metadata.st_blocks * 512
        if current.is_file():
            digest = hashlib.sha256()
            with current.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            logical_bytes += metadata.st_size
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "sha256": digest.hexdigest(),
                    "bytes": metadata.st_size,
                }
            )
            return
        if not current.is_dir():
            raise AgentReplayInputError(f"artifact has unsupported kind: {current}")
        entries.append({"path": relative, "kind": "directory"})
        with os.scandir(current) as children:
            for child in sorted(children, key=lambda item: item.name):
                visit(
                    Path(child.path),
                    child.name if relative == "." else f"{relative}/{child.name}",
                )

    visit(path, ".")
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return ArtifactInventory(
        entries=tuple(entries),
        identity=hashlib.sha256(encoded).hexdigest(),
        logical_bytes=logical_bytes,
        allocated_bytes=allocated_bytes,
    )


def _git_result(
    arguments: list[str], root: Path, runner: CommandRunner
) -> CapturedCommandResult:
    try:
        return runner(
            arguments,
            cwd=root,
            shell=False,
            check=False,
            timeout=_DIAGNOSTIC_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentReplayInputError(
            f"Git preflight failed: {' '.join(arguments)}"
        ) from exc


def _require_untracked_ignored(
    relative: str, root: Path, runner: CommandRunner
) -> None:
    tracked = _git_result(["git", "ls-files", "--", relative], root, runner)
    if tracked.returncode != 0:
        raise AgentReplayInputError(f"Git tracked-source check failed: {relative}")
    if tracked.stdout.strip():
        raise AgentReplayInputError(f"source is tracked by Git: {relative}")
    ignored = _git_result(["git", "check-ignore", "-q", "--", relative], root, runner)
    if ignored.returncode == 1:
        raise AgentReplayInputError(f"source is not ignored by Git: {relative}")
    if ignored.returncode != 0:
        raise AgentReplayInputError(f"Git ignored-source check failed: {relative}")


def _path_contains(parent: Path, child: Path) -> bool:
    return child == parent or parent in child.parents


def _destination_for(source_relative: str) -> str:
    within_tmp = Path(source_relative).relative_to("tmp")
    if len(within_tmp.parts) == 1:
        return (Path("tmp/obsolete/root") / within_tmp).as_posix()
    return (Path("tmp/obsolete") / within_tmp).as_posix()


def _read_manifest_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _rename_artifact(source: Path, destination: Path) -> None:
    os.rename(source, destination)


def _same_filesystem(source: Path, destination_parent: Path) -> bool:
    return source.stat().st_dev == destination_parent.stat().st_dev


def _inventory_payload(inventory: ArtifactInventory) -> dict[str, object]:
    return {
        "identity": inventory.identity,
        "logical_bytes": inventory.logical_bytes,
        "allocated_bytes": inventory.allocated_bytes,
        "entries": list(inventory.entries),
    }


def _write_report(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(serialized, encoding="utf-8")


def _valid_completed_totals(
    report: dict[str, Any],
    *,
    sources: int,
    logical_bytes: int,
    allocated_bytes: int,
) -> bool:
    return (
        report.get("totals")
        == {
            "sources": sources,
            "logical_bytes": logical_bytes,
            "allocated_bytes": allocated_bytes,
        }
        and re.fullmatch(r"[0-9a-f]{40,64}", str(report.get("git_head"))) is not None
    )


def _completed_report_mappings(
    report: dict[str, Any],
    *,
    manifest_relative: str,
    manifest_digest: str,
    source_count: int,
) -> list[object]:
    if set(report) != {
        "schema_version",
        "status",
        "manifest",
        "git_head",
        "mappings",
        "totals",
    }:
        raise AgentReplayInputError(
            "existing consolidation report has an invalid schema"
        )
    if (
        type(report.get("schema_version")) is not int
        or report.get("schema_version") != 1
        or report.get("status") != "completed"
        or report.get("manifest")
        != {"path": manifest_relative, "sha256": manifest_digest}
    ):
        raise AgentReplayInputError(
            "existing consolidation report conflicts with manifest"
        )
    mappings = report.get("mappings")
    if not isinstance(mappings, list) or len(mappings) != source_count:
        raise AgentReplayInputError(
            "existing consolidation report mapping count differs"
        )
    return mappings


def _validate_completed_rerun(
    report_path: Path,
    *,
    manifest_relative: str,
    manifest_digest: str,
    source_specs: list[dict[str, object]],
    root: Path,
) -> bool:
    if not report_path.exists():
        return False
    report = _load_strict_json(report_path.read_bytes(), label="consolidation report")
    mappings = _completed_report_mappings(
        report,
        manifest_relative=manifest_relative,
        manifest_digest=manifest_digest,
        source_count=len(source_specs),
    )
    expected_logical = 0
    expected_allocated = 0
    for spec, mapping in zip(source_specs, mappings, strict=True):
        if not isinstance(mapping, dict) or set(mapping) != {
            "source",
            "destination",
            "duplicate_of",
            "pre",
            "post",
        }:
            raise AgentReplayInputError(
                "existing consolidation report mapping is invalid"
            )
        source_relative = cast("str", spec["path"])
        destination_relative = _destination_for(source_relative)
        if (
            mapping.get("source") != source_relative
            or mapping.get("destination") != destination_relative
            or mapping.get("duplicate_of") != spec.get("duplicate_of")
            or (root / source_relative).exists()
            or (root / source_relative).is_symlink()
        ):
            raise AgentReplayInputError(
                "completed consolidation state conflicts with manifest"
            )
        destination = root / destination_relative
        if not destination.exists() or destination.is_symlink():
            raise AgentReplayInputError(
                "completed consolidation destination is missing"
            )
        inventory = _artifact_inventory(destination)
        if mapping.get("pre") != _inventory_payload(inventory) or mapping.get(
            "post"
        ) != _inventory_payload(inventory):
            raise AgentReplayInputError(
                "completed consolidation destination differs from report"
            )
        expected_logical += inventory.logical_bytes
        expected_allocated += inventory.allocated_bytes
    if not _valid_completed_totals(
        report,
        sources=len(mappings),
        logical_bytes=expected_logical,
        allocated_bytes=expected_allocated,
    ):
        raise AgentReplayInputError("existing consolidation report totals are invalid")
    return True


def _parse_consolidation_sources(
    manifest_bytes: bytes,
) -> tuple[dict[str, object], ...]:
    manifest = _load_strict_json(manifest_bytes, label="consolidation manifest")
    valid_schema = (
        set(manifest) == {"schema_version", "sources"}
        and type(manifest.get("schema_version")) is int
        and manifest.get("schema_version") == 1
    )
    if not valid_schema:
        raise AgentReplayInputError("consolidation manifest has an invalid schema")
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise AgentReplayInputError("manifest sources must be a non-empty ordered list")
    source_specs: list[dict[str, object]] = []
    for index, raw in enumerate(raw_sources):
        valid_keys = (
            isinstance(raw, dict)
            and set(raw) <= {"path", "duplicate_of"}
            and "path" in raw
        )
        if not valid_keys:
            raise AgentReplayInputError(
                f"manifest source {index} has an invalid schema"
            )
        source_relative = _validated_repository_relative(
            raw["path"], label=f"manifest source {index}"
        )
        spec: dict[str, object] = {"path": source_relative}
        if "duplicate_of" in raw:
            spec["duplicate_of"] = _validated_repository_relative(
                raw["duplicate_of"],
                label=f"manifest source {index} duplicate_of",
            )
        source_specs.append(spec)
    return tuple(source_specs)


def _parse_consolidation_request(values: list[str], root: Path) -> ConsolidationContext:
    if len(values) != _CONSOLIDATION_VALUE_COUNT or values[1] != "--report":
        raise AgentReplayInputError(
            "consolidate-obsolete requires <manifest> --report <report>"
        )
    manifest_relative = _validated_repository_relative(values[0], label="manifest")
    report_relative = _validated_repository_relative(values[2], label="report")
    manifest_path = root / manifest_relative
    report_path = root / report_relative
    if (
        not _path_contains(root / "tmp/plans", manifest_path)
        or not manifest_path.is_file()
    ):
        raise AgentReplayInputError("manifest must be a file under tmp/plans")
    _require_no_symlink_components(manifest_path, root=root, label="manifest path")
    if report_path.parent != manifest_path.parent or report_path == manifest_path:
        raise AgentReplayInputError("report must be a distinct sibling of the manifest")
    _require_no_symlink_components(report_path.parent, root=root, label="report path")
    manifest_bytes = _read_manifest_bytes(manifest_path)
    return ConsolidationContext(
        manifest_relative=manifest_relative,
        manifest_path=manifest_path,
        report_relative=report_relative,
        report_path=report_path,
        manifest_bytes=manifest_bytes,
        manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
        source_specs=_parse_consolidation_sources(manifest_bytes),
    )


def _preflight_consolidation_entry(
    spec: dict[str, object],
    *,
    request: ConsolidationContext,
    root: Path,
    runner: CommandRunner,
) -> ConsolidationEntry:
    source_relative = cast("str", spec["path"])
    source = root / source_relative
    if not _path_contains(root / "tmp", source):
        raise AgentReplayInputError(f"source must be under tmp: {source_relative}")
    if _path_contains(root / "tmp/obsolete", source):
        raise AgentReplayInputError(f"source is already obsolete: {source_relative}")
    if _path_contains(request.manifest_path.parent, source):
        raise AgentReplayInputError(
            f"source is inside the cleanup-plan directory: {source_relative}"
        )
    _require_no_symlink_components(source, root=root, label="source path")
    if not source.exists() or (not source.is_file() and not source.is_dir()):
        raise AgentReplayInputError(
            f"source is missing or has wrong kind: {source_relative}"
        )
    _require_untracked_ignored(source_relative, root, runner)
    destination_relative = _destination_for(source_relative)
    destination = root / destination_relative
    _require_no_symlink_components(
        destination.parent, root=root, label="destination path"
    )
    if destination.exists() or destination.is_symlink():
        raise AgentReplayInputError(
            f"destination already exists: {destination_relative}"
        )
    inventory = _artifact_inventory(source)
    duplicate_relative = cast("str | None", spec.get("duplicate_of"))
    if duplicate_relative is not None:
        duplicate = root / duplicate_relative
        _require_no_symlink_components(duplicate, root=root, label="duplicate_of path")
        if not duplicate.exists() or (
            not duplicate.is_file() and not duplicate.is_dir()
        ):
            raise AgentReplayInputError(
                f"duplicate_of is missing or has wrong kind: {duplicate_relative}"
            )
        if _artifact_inventory(duplicate).identity != inventory.identity:
            raise AgentReplayInputError(
                f"duplicate_of differs from source: {source_relative}"
            )
    return ConsolidationEntry(
        source_relative,
        source,
        destination_relative,
        destination,
        duplicate_relative,
        inventory,
    )


def _validate_consolidation_relationships(entries: list[ConsolidationEntry]) -> None:
    for index, left in enumerate(entries):
        for right in entries[index + 1 :]:
            if _path_contains(left.source, right.source) or _path_contains(
                right.source, left.source
            ):
                raise AgentReplayInputError("manifest sources duplicate or overlap")
    for entry in entries:
        if any(
            _path_contains(entry.source, other.destination)
            or _path_contains(other.destination, entry.source)
            for other in entries
        ):
            raise AgentReplayInputError("source and destination overlap")
        existing_parent = entry.destination.parent
        while not existing_parent.exists():
            existing_parent = existing_parent.parent
        if not _same_filesystem(entry.source, existing_parent):
            raise AgentReplayInputError(
                f"cross-device move refused: {entry.source_relative}"
            )


def _create_destination_parent(destination: Path, created_parents: list[Path]) -> None:
    missing: list[Path] = []
    parent = destination.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir()
        created_parents.append(directory)


def _rollback_consolidation(
    moved: list[ConsolidationEntry], created_parents: list[Path], root: Path
) -> list[str]:
    rollback_errors: list[str] = []
    for entry in reversed(moved):
        try:
            _rename_artifact(entry.destination, entry.source)
        except OSError as exc:
            rollback_errors.append(f"{entry.destination_relative}: {exc}")
    for directory in reversed(created_parents):
        try:
            directory.rmdir()
        except OSError as exc:
            if directory.exists():
                rollback_errors.append(f"{directory.relative_to(root)}: {exc}")
    return rollback_errors


def _verified_consolidation_mappings(
    entries: list[ConsolidationEntry],
) -> list[dict[str, object]]:
    mappings: list[dict[str, object]] = []
    for entry in entries:
        if entry.source.exists() or entry.source.is_symlink():
            raise AgentReplayInputError(
                f"source remains after movement: {entry.source_relative}"
            )
        post = _artifact_inventory(entry.destination)
        if post != entry.inventory:
            raise AgentReplayInputError(
                f"destination verification failed: {entry.destination_relative}"
            )
        mappings.append(
            {
                "source": entry.source_relative,
                "destination": entry.destination_relative,
                "duplicate_of": entry.duplicate_of_relative,
                "pre": _inventory_payload(entry.inventory),
                "post": _inventory_payload(post),
            }
        )
    return mappings


def _execute_consolidation(
    entries: list[ConsolidationEntry],
    *,
    request: ConsolidationContext,
    head: str,
    root: Path,
) -> None:
    created_parents: list[Path] = []
    moved: list[ConsolidationEntry] = []
    try:
        for entry in entries:
            _create_destination_parent(entry.destination, created_parents)
            _rename_artifact(entry.source, entry.destination)
            moved.append(entry)
        mappings = _verified_consolidation_mappings(entries)
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": "completed",
            "manifest": {
                "path": request.manifest_relative,
                "sha256": request.manifest_digest,
            },
            "git_head": head,
            "mappings": mappings,
            "totals": {
                "sources": len(entries),
                "logical_bytes": sum(item.inventory.logical_bytes for item in entries),
                "allocated_bytes": sum(
                    item.inventory.allocated_bytes for item in entries
                ),
            },
        }
        _write_report(request.report_path, payload)
    except BaseException as primary:
        rollback_errors = _rollback_consolidation(moved, created_parents, root)
        if rollback_errors:
            primary.add_note("rollback incomplete: " + "; ".join(rollback_errors))
        raise


def _consolidate_obsolete(values: list[str], root: Path, runner: CommandRunner) -> int:
    """Quarantine reviewed ignored artifacts for manual deletion; never delete them."""
    if values == ["--help"]:
        print(
            "usage: agent-replay consolidate-obsolete <manifest> --report <report>\n"
            "Quarantines explicitly reviewed ignored tmp artifacts under tmp/obsolete "
            "for manual deletion; it does not delete artifacts."
        )
        return 0
    request = _parse_consolidation_request(values, root)
    if _validate_completed_rerun(
        request.report_path,
        manifest_relative=request.manifest_relative,
        manifest_digest=request.manifest_digest,
        source_specs=list(request.source_specs),
        root=root,
    ):
        print(f"consolidation already completed: {request.report_relative}")
        return 0
    entries = [
        _preflight_consolidation_entry(spec, request=request, root=root, runner=runner)
        for spec in request.source_specs
    ]
    _validate_consolidation_relationships(entries)
    head = _capture_required(["git", "rev-parse", "HEAD"], root, runner).strip()
    if _read_manifest_bytes(request.manifest_path) != request.manifest_bytes:
        raise AgentReplayInputError("manifest changed during preflight")
    _execute_consolidation(entries, request=request, head=head, root=root)
    print(f"consolidated {len(entries)} artifacts; report: {request.report_relative}")
    return 0


def _redact_structural_environment(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text

    def redact(value: object, *, key: str | None = None) -> object:
        if isinstance(value, dict):
            return {str(k): redact(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            if key == "Env":
                return [
                    _SECRET_VALUE.sub(r"\1[REDACTED]", item)
                    if isinstance(item, str)
                    else redact(item)
                    for item in value
                ]
            return [redact(item) for item in value]
        return value

    return json.dumps(redact(payload), separators=(",", ":"))


def _bounded_sanitized(value: str, *, limit: int | None = _MAX_DIAGNOSTIC_CHARS) -> str:
    text = _redact_structural_environment(value)
    text = _ANSI_ESCAPE.sub("", text).replace("\x00", "")
    text = _SECRET_VALUE.sub(r"\1[REDACTED]", text)
    text = _URL_CREDENTIALS.sub(r"\1[REDACTED]\2", text)
    if limit is None or len(text) <= limit:
        return text
    omitted = len(text) - limit
    retained_head = limit // 2
    retained_tail = limit - retained_head
    return (
        f"{text[:retained_head]}\n[TRUNCATED {omitted} CHARS]\n{text[-retained_tail:]}"
    )


def _collect_diagnostic_command(
    command: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    environment: dict[str, str] | None = None,
) -> None:
    """Collect one diagnostic; its output and exit code are evidence, not a verdict.

    Returning means only that collection completed. ``inspect-podman`` is best-effort
    diagnosis, so the overall operation does not aggregate command success.
    """
    print(f"\n=== {' '.join(command)} ===")
    try:
        result = runner(
            command,
            cwd=root,
            shell=False,
            check=False,
            timeout=_DIAGNOSTIC_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"collection-error: {_bounded_sanitized(str(exc))}")
        return
    print(f"exit-code: {result.returncode}")
    stdout = _bounded_sanitized(result.stdout)
    stderr = _bounded_sanitized(result.stderr)
    if stdout:
        print("stdout:")
        print(stdout)
    if stderr:
        print("stderr:")
        print(stderr)


def _adjudication_inputs(root: Path) -> tuple[str, str, str, str, str]:
    return cast(
        "tuple[str, str, str, str, str]",
        tuple(
            _require_files(
                root,
                (
                    "scripts/adjudication.py",
                    "samples/ncit-26.07d-m1-current-replay.json",
                    "ontolib/tests/decomposition/golden/neoplasm-adjudicated.json",
                    "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json",
                    "ontolib/tests/decomposition/golden/proposal-registry.json",
                ),
            )
        ),
    )


def _read_issue(values: list[str], root: Path, runner: CommandRunner) -> int:
    if len(values) != 1 or not values[0].isdigit():
        raise AgentReplayInputError("issue number must be numeric")
    return _run(
        [
            "gh",
            "issue",
            "view",
            values[0],
            "--repo",
            "hniedner/ontoprism",
            "--json",
            "number,title,body,labels,milestone,state,url",
        ],
        root,
        runner,
    )


def _decompose_current(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("decompose-current accepts no arguments")
    script, source, sample = _require_files(
        root,
        (
            "scripts/decompose.py",
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "samples/ncit-26.07d-m1-current-replay.json",
        ),
    )
    generation_id = str(importlib.import_module("uuid").uuid4())
    family_root = root / "tmp/artifacts/v1/generations/m1-6-current-replay"
    staging = family_root / ".staging" / generation_id
    staging.mkdir(parents=True, exist_ok=False)
    try:
        return _decompose_current_staged(
            root=root,
            runner=runner,
            script=script,
            source=source,
            sample=sample,
            generation_id=generation_id,
            family_root=family_root,
            staging=staging,
        )
    finally:
        shutil.rmtree(staging)


def _decompose_current_staged(
    *,
    root: Path,
    runner: CommandRunner,
    script: str,
    source: str,
    sample: str,
    generation_id: str,
    family_root: Path,
    staging: Path,
) -> int:
    output = staging / "decomposition.ttl"
    command = [
        sys.executable,
        script,
        "--source-manifest",
        source,
        "--branch",
        "neoplasm",
        "--sample-manifest",
        sample,
        "--walker-max-depth",
        "7",
        "--out",
        str(output),
    ]
    result = runner(
        command,
        cwd=root,
        shell=False,
        check=False,
        timeout=None,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return result.returncode
    if not output.is_file() or output.is_symlink():
        raise AgentReplayInputError("bounded replay did not produce a regular artifact")
    run_ids = set(_RUN_ID.findall(output.read_text(encoding="utf-8")))
    run_ids.update(_RUN_ID.findall(result.stdout or ""))
    run_ids.update(_RUN_ID.findall(result.stderr or ""))
    if len(run_ids) != 1:
        raise AgentReplayInputError(
            "bounded replay output does not bind exactly one run ID"
        )
    run_id = run_ids.pop()
    source_identity = _verify_persisted_replay(run_id, output)
    manifest = publish_generation(
        artifacts_root=root / "tmp/artifacts/v1/generations",
        family="m1-6-current-replay",
        generation_id=generation_id,
        run_id=run_id,
        artifact_sources={"artifacts/decomposition.ttl": output},
        parents=(),
        generator=GeneratorBinding(
            identity=_git_head_identity(root),
            command=(*command[:-1], "<generation-staging>/decomposition.ttl"),
        ),
        sources=(SourceIdentity(name="ncit", identity=source_identity),),
        retention=RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="decomposition",
            expires_at=None,
        ),
    )
    final = family_root / generation_id
    artifact = final / "artifacts/decomposition.ttl"
    print(
        json.dumps(
            {
                "run_id": run_id,
                "artifact_path": artifact.relative_to(root).as_posix(),
                "artifact_sha256": manifest.artifact_records[0].sha256,
                "manifest_path": (final / "manifest.json").relative_to(root).as_posix(),
                "manifest_identity": manifest.manifest_identity,
            },
            sort_keys=True,
        )
    )
    return 0


def _git_head_identity(root: Path) -> str:
    head = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = subprocess.run(
        ["/usr/bin/git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    digest = hashlib.sha256()
    for relative in sorted(path for path in tracked.stdout.split("\0") if path):
        payload = (root / relative).read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, byteorder="big"))
        digest.update(payload)
    return f"git:{head.stdout.strip()}+worktree-sha256:{digest.hexdigest()}"


def _verify_persisted_replay(run_id: str, artifact: Path) -> str:
    records = asyncio.run(_inspect_decomposition_runs_async((run_id,)))
    if len(records) != 1 or records[0].get("status") != "complete":
        raise AgentReplayInputError("bounded replay run is not persisted as complete")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if records[0].get("representation_identity") != digest:
        raise AgentReplayInputError(
            "bounded replay bytes differ from persisted identity"
        )
    source_identity = records[0].get("source_identity")
    if not isinstance(source_identity, str) or not source_identity:
        raise AgentReplayInputError("bounded replay has no persisted source identity")
    return source_identity


_CRITICAL_IN_PLACE_ARTIFACTS = (
    (
        "tmp/m1-6-current-full-corpus.ttl",
        "neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016",
    ),
    (
        "tmp/m1-6-prechange-v4-full-corpus.ttl",
        "neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820",
    ),
)


def _record_artifact_registry(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError("record-artifact-registry accepts no arguments")
    paths = _require_files(
        root, tuple(relative for relative, _run_id in _CRITICAL_IN_PLACE_ARTIFACTS)
    )
    run_ids = tuple(run_id for _relative, run_id in _CRITICAL_IN_PLACE_ARTIFACTS)
    records = asyncio.run(_inspect_decomposition_runs_async(run_ids))
    by_run = {record.get("run_id"): record for record in records}
    if set(by_run) != set(run_ids):
        raise AgentReplayInputError("critical artifact persisted run inventory differs")
    generator = GeneratorBinding(
        identity=_git_head_identity(root),
        command=("pdm", "run", "agent-replay", "record-artifact-registry"),
    )
    reported: list[dict[str, str]] = []
    sidecars = root / "tmp/artifacts/v1/legacy-in-place"
    for (relative, run_id), artifact_text in zip(
        _CRITICAL_IN_PLACE_ARTIFACTS, paths, strict=True
    ):
        record = by_run[run_id]
        representation = record.get("representation_identity")
        persisted_path = record.get("publication_artifact_path")
        source_identity = record.get("source_identity")
        if (
            record.get("status") != "complete"
            or not isinstance(representation, str)
            or _SHA256.fullmatch(representation) is None
            or not isinstance(persisted_path, str)
            or not isinstance(source_identity, str)
            or not source_identity
        ):
            raise AgentReplayInputError(
                f"critical artifact DB binding is incomplete: {run_id}"
            )
        sidecar = sidecars / f"{Path(relative).stem}.manifest.json"
        try:
            manifest = write_legacy_in_place_manifest(
                path=sidecar,
                repository_root=root,
                artifact_path=Path(artifact_text),
                run_id=run_id,
                persisted_representation_identity=representation,
                persisted_artifact_path=persisted_path,
                source_identity=source_identity,
                generator=generator,
            )
        except ValueError as exc:
            raise AgentReplayInputError(str(exc)) from exc
        reported.append(
            {
                "manifest_path": sidecar.relative_to(root).as_posix(),
                "manifest_identity": manifest.manifest_identity,
                "artifact_path": manifest.artifact_records[0].relative_path,
                "artifact_sha256": manifest.artifact_records[0].sha256,
            }
        )
    unavailable_path = (
        root / "tmp/artifacts/v1/unavailable/"
        "neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997.json"
    )
    stale_unavailable = ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256=(
            "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
        ),
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=(),
    )
    unavailable = stale_unavailable.model_copy(
        update={
            "references": (
                "tmp/m1-6-normalized-group-policy-candidate.json",
                "tmp/m1-6-group-review-pre274-observations.json",
            )
        }
    )
    audit: Path | None = None
    if unavailable_path.exists():
        try:
            audit = reconcile_missing_unavailable_references(
                path=unavailable_path,
                expected_stale=stale_unavailable,
                corrected=unavailable,
            )
        except ValueError as exc:
            raise AgentReplayInputError(str(exc)) from exc
    else:
        write_unavailable_record(unavailable_path, unavailable)
    unavailable_bytes = unavailable_path.read_bytes()
    print(
        json.dumps(
            {
                "legacy_manifests": reported,
                "unavailable_record": unavailable_path.relative_to(root).as_posix(),
                "unavailable_record_sha256": hashlib.sha256(
                    unavailable_bytes
                ).hexdigest(),
                "superseded_audit": audit.relative_to(root).as_posix()
                if audit is not None
                else None,
                "superseded_audit_sha256": hashlib.sha256(
                    audit.read_bytes()
                ).hexdigest()
                if audit is not None
                else None,
            },
            sort_keys=True,
        )
    )
    return 0


def _inspect_current_replay(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError("inspect-current-replay accepts no arguments")
    artifact, sample = _require_files(
        root,
        (
            "tmp/m1-6-current-replay.ttl",
            "samples/ncit-26.07d-m1-current-replay.json",
        ),
    )
    payload = Path(artifact).read_bytes()
    run_ids = sorted(
        {
            match.decode()
            for match in re.findall(rb'"(neoplasm-[0-9a-f-]{36})"', payload)
        }
    )
    print(
        json.dumps(
            {
                "artifact_sha256": hashlib.sha256(payload).hexdigest(),
                "run_ids": run_ids,
                "sample_sha256": hashlib.sha256(Path(sample).read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


def _generate_current_evidence(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if (
        len(values) != _PARENT_MANIFEST_ARGUMENT_COUNT
        or _SHA256.fullmatch(values[1]) is None
    ):
        raise AgentReplayInputError(
            "an exact parent manifest path and identity are required"
        )
    manifest_path = (root / values[0]).resolve()
    artifacts_root = (root / "tmp/artifacts/v1/generations").resolve()
    if artifacts_root not in manifest_path.parents:
        raise AgentReplayInputError(
            "parent manifest must be in the generation registry"
        )
    try:
        parent = resolve_parent_manifest(manifest_path, values[1])
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    if parent.family != "m1-6-current-replay" or parent.run_id is None:
        raise AgentReplayInputError("parent is not a bounded current replay")
    script, sample, oracle, rows, registry = _adjudication_inputs(root)
    (migration,) = _require_files(
        root,
        (
            "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
        ),
    )
    golden = root / "ontolib/tests/decomposition/golden"
    return _run(
        [
            sys.executable,
            script,
            "generate-current-evidence",
            "--sample-manifest",
            sample,
            "--oracle",
            oracle,
            "--row-decisions",
            rows,
            "--proposal-registry",
            registry,
            "--proposal-registry-migration",
            migration,
            "--run-id",
            parent.run_id,
            "--artifact",
            str(manifest_path.parent / parent.artifact_records[0].relative_path),
            "--engine-output",
            str(golden / "neoplasm-current-engine-evidence.json"),
            "--comparison-output",
            str(golden / "neoplasm-current-comparison.json"),
        ],
        root,
        runner,
    )


def _generate_current_evidence_candidate(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    parent, parent_path, parent_binding = _resolve_candidate_parent(
        values, root, expected_family="m1-6-current-replay"
    )
    script, sample, oracle, rows, registry = _adjudication_inputs(root)
    (migration,) = _require_files(
        root,
        (
            "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
        ),
    )
    sample_path = Path(sample)
    artifact_path = _bound_artifact_path(
        parent, parent_path, "artifacts/decomposition.ttl"
    )
    if hashlib.sha256(sample_path.read_bytes()).hexdigest() != (
        _CURRENT_REPLAY_SAMPLE_SHA256
    ):
        raise AgentReplayInputError("current replay sample manifest digest differs")
    family = "m1-6-current-evidence-candidate"
    generation_id, staging = _candidate_staging(root, family)
    outputs = (staging / "engine-evidence.json", staging / "comparison.json")
    for path, label in (
        (Path(script), "adjudication script"),
        (sample_path, "current replay sample manifest"),
        (Path(oracle), "current replay oracle"),
        (Path(rows), "current replay row decisions"),
        (Path(registry), "current replay proposal registry"),
        (Path(migration), "current replay proposal registry migration"),
        (artifact_path, "current replay artifact"),
        (outputs[0], "current evidence candidate output"),
        (outputs[1], "current comparison candidate output"),
    ):
        _require_no_symlink_components(path, root=root, label=label)
    command = [
        sys.executable,
        script,
        "generate-current-evidence",
        "--sample-manifest",
        sample,
        "--oracle",
        oracle,
        "--row-decisions",
        rows,
        "--proposal-registry",
        registry,
        "--proposal-registry-migration",
        migration,
        "--run-id",
        parent.run_id,
        "--artifact",
        str(artifact_path),
        "--artifact-manifest",
        str(parent_path),
        "--artifact-manifest-identity",
        parent.manifest_identity,
        "--engine-output",
        str(outputs[0]),
        "--comparison-output",
        str(outputs[1]),
    ]
    return _run_and_publish_candidate(
        command=command,
        root=root,
        runner=runner,
        family=family,
        generation_id=generation_id,
        staging=staging,
        run_id=parent.run_id,
        artifact_sources={
            "artifacts/engine-evidence.json": outputs[0],
            "artifacts/comparison.json": outputs[1],
        },
        parents=(parent_binding,),
        sources=(
            SourceIdentity(
                name="sample-manifest",
                identity=f"sha256:{_CURRENT_REPLAY_SAMPLE_SHA256}",
            ),
        ),
    )


def _resolve_candidate_parent(
    values: list[str], root: Path, *, expected_family: str
) -> tuple[ArtifactManifest, Path, ParentManifestBinding]:
    if (
        len(values) != _PARENT_MANIFEST_ARGUMENT_COUNT
        or _SHA256.fullmatch(values[1]) is None
    ):
        raise AgentReplayInputError(
            "an exact parent manifest path and identity are required"
        )
    artifacts_root = (root / "tmp/artifacts/v1/generations").resolve()
    manifest_path = (root / values[0]).resolve()
    if artifacts_root not in manifest_path.parents:
        raise AgentReplayInputError(
            "parent manifest must be in the generation registry"
        )
    try:
        parent = resolve_parent_manifest(manifest_path, values[1])
    except (OSError, ValueError) as exc:
        raise AgentReplayInputError(str(exc)) from exc
    if parent.family != expected_family:
        raise AgentReplayInputError(f"parent is not a {expected_family} generation")
    binding = ParentManifestBinding(
        family=parent.family,
        generation_id=parent.generation_id,
        manifest_path=manifest_path.relative_to(artifacts_root).as_posix(),
        manifest_identity=parent.manifest_identity,
    )
    return parent, manifest_path, binding


def _parent_by_family(manifest: ArtifactManifest, family: str) -> ParentManifestBinding:
    matches = tuple(parent for parent in manifest.parents if parent.family == family)
    if len(matches) != 1:
        raise AgentReplayInputError(
            f"manifest requires exactly one {family} parent binding"
        )
    return matches[0]


def _resolve_bound_parent(
    binding: ParentManifestBinding, root: Path, *, expected_family: str
) -> tuple[ArtifactManifest, Path]:
    parent, path, resolved = _resolve_candidate_parent(
        [
            str(Path("tmp/artifacts/v1/generations") / binding.manifest_path),
            binding.manifest_identity,
        ],
        root,
        expected_family=expected_family,
    )
    if resolved != binding:
        raise AgentReplayInputError(f"{expected_family} parent binding differs")
    return parent, path


def _bound_artifact_path(
    parent: ArtifactManifest, manifest_path: Path, relative: str
) -> Path:
    if not any(record.relative_path == relative for record in parent.artifact_records):
        raise AgentReplayInputError(
            f"parent manifest lacks required artifact: {relative}"
        )
    return manifest_path.parent / relative


def _candidate_staging(root: Path, family: str) -> tuple[str, Path]:
    generation_id = str(importlib.import_module("uuid").uuid4())
    staging_root = root / "tmp/artifacts/v1/generations" / family / ".producer-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{generation_id}-", dir=staging_root))
    return generation_id, staging


def _run_and_publish_candidate(
    *,
    command: list[str],
    root: Path,
    runner: CommandRunner,
    family: str,
    generation_id: str,
    staging: Path,
    run_id: str | None,
    artifact_sources: dict[str, Path],
    parents: tuple[ParentManifestBinding, ...],
    sources: tuple[SourceIdentity, ...],
) -> int:
    try:
        result = _run(command, root, runner)
        if result != 0:
            return result
        manifest = publish_generation(
            artifacts_root=root / "tmp/artifacts/v1/generations",
            family=family,
            generation_id=generation_id,
            run_id=run_id,
            artifact_sources=artifact_sources,
            parents=parents,
            generator=GeneratorBinding(
                identity=_git_head_identity(root), command=tuple(command)
            ),
            sources=sources,
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )
        manifest_path = (
            root
            / "tmp/artifacts/v1/generations"
            / family
            / generation_id
            / "manifest.json"
        )
        print(
            json.dumps(
                {
                    "manifest_path": manifest_path.relative_to(root).as_posix(),
                    "manifest_identity": manifest.manifest_identity,
                },
                sort_keys=True,
            )
        )
        return 0
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _regenerate_current_comparison(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError(
            "regenerate-current-comparison accepts no arguments"
        )
    sys.path.insert(0, str(root))
    regenerate_current_comparison = importlib.import_module(
        "scripts.research.current_evidence"
    ).regenerate_current_comparison

    _script, _sample, oracle, rows, registry = _adjudication_inputs(root)
    golden = root / "ontolib/tests/decomposition/golden"
    evidence, _existing_output = _require_files(
        root,
        (
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        ),
    )
    regenerate_current_comparison(
        evidence_path=Path(evidence),
        oracle_path=Path(oracle),
        row_decisions_path=Path(rows),
        proposal_registry_path=Path(registry),
        proposal_registry_migration_path=(
            golden / "proposal-registry-schema2-migration.json"
        ),
        output=golden / "neoplasm-current-comparison.json",
    )
    return 0


def _generate_axis_diagnostics(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if not values:
        raise AgentReplayInputError("at least one residual filler is required")
    if len(values) != len(set(values)) or any(
        _FILLER.fullmatch(value) is None for value in values
    ):
        raise AgentReplayInputError("residual filler values are invalid")
    script, _sample, oracle, rows, registry = _adjudication_inputs(root)
    source, evidence, comparison, migration = _require_files(
        root,
        (
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
            "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
        ),
    )
    command = [
        sys.executable,
        script,
        "generate-axis-diagnostics",
        "--source-manifest",
        source,
        "--endpoint",
        "http://localhost:7888",
        "--oracle",
        oracle,
        "--row-decisions",
        rows,
        "--proposal-registry",
        registry,
        "--proposal-registry-migration",
        migration,
        "--current-evidence",
        evidence,
        "--current-comparison",
        comparison,
    ]
    for filler in values:
        command.extend(("--residual-filler", filler))
    command.extend(("--output", str(root / "tmp/m1-6-axis-diagnostics-rev2.json")))
    return _run(command, root, runner)


def _generate_group_review_rev2_candidate(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if len(values) != _GROUP_REVIEW_PARENT_ARGUMENT_COUNT:
        raise AgentReplayInputError(
            "exact current-evidence and R101 parent manifests are required"
        )
    parent, parent_path, parent_binding = _resolve_candidate_parent(
        values[:2], root, expected_family="m1-6-current-evidence-candidate"
    )
    r101_parent, r101_parent_path, r101_parent_binding = _resolve_candidate_parent(
        values[2:], root, expected_family="m1-6-r101-conservation"
    )
    script, historical_r101_report = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        ),
    )
    evidence = _bound_artifact_path(
        parent, parent_path, "artifacts/engine-evidence.json"
    )
    comparison = _bound_artifact_path(parent, parent_path, "artifacts/comparison.json")
    r101_report = _bound_artifact_path(
        r101_parent, r101_parent_path, "artifacts/conservation.json.gz"
    )
    family = "m1-6-group-review-candidate"
    generation_id, staging = _candidate_staging(root, family)
    outputs = (
        staging / "group-review-packet.json",
        staging / "group-review-workbook.xlsx",
        staging / "group-correction-audit.xlsx",
        staging / "group-review-blank-validation.json",
    )
    for path in (
        Path(script),
        Path(evidence),
        Path(comparison),
        Path(r101_report),
        *outputs,
    ):
        _require_no_symlink_components(
            path, root=root, label="group review candidate path"
        )
    command = [
        sys.executable,
        script,
        "generate-group-review-packet",
        "--current-evidence",
        str(evidence),
        "--current-comparison",
        str(comparison),
        "--r101-report",
        str(r101_report),
        "--historical-r101-report",
        historical_r101_report,
        "--output",
        str(outputs[0]),
        "--workbook",
        str(outputs[1]),
        "--correction-audit",
        str(outputs[2]),
        "--blank-validation",
        str(outputs[3]),
    ]
    return _run_and_publish_candidate(
        command=command,
        root=root,
        runner=runner,
        family=family,
        generation_id=generation_id,
        staging=staging,
        run_id=parent.run_id,
        artifact_sources={
            "artifacts/group-review-packet.json": outputs[0],
            "artifacts/group-review-workbook.xlsx": outputs[1],
            "artifacts/group-correction-audit.xlsx": outputs[2],
            "artifacts/group-review-blank-validation.json": outputs[3],
        },
        parents=(parent_binding, r101_parent_binding),
        sources=(),
    )


def _generate_normalized_group_policy_candidate(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if len(values) != _POLICY_CANDIDATE_PARENT_ARGUMENT_COUNT:
        raise AgentReplayInputError(
            "exact evidence and group-review parent manifests are required"
        )
    parent, parent_path, parent_binding = _resolve_candidate_parent(
        values[:2], root, expected_family="m1-6-current-evidence-candidate"
    )
    review, review_path, review_binding = _resolve_candidate_parent(
        values[2:], root, expected_family="m1-6-group-review-candidate"
    )
    if (
        len(review.parents) != _GROUP_REVIEW_PARENT_COUNT
        or review.parents[0] != parent_binding
    ):
        raise AgentReplayInputError(
            "group-review candidate is not bound to the evidence and R101 candidates"
        )
    r101_binding = _parent_by_family(review, "m1-6-r101-conservation")
    r101, r101_path = _resolve_bound_parent(
        r101_binding, root, expected_family="m1-6-r101-conservation"
    )
    _bound_artifact_path(r101, r101_path, "artifacts/conservation.json.gz")
    required = _require_files(
        root,
        (
            "evidence/group-review-packet-26.07d-schema3.json",
            "evidence/group-review-rationale-26.07d.md",
            "evidence/group-review-rationale-26.07d.json",
        ),
    )
    evidence = _bound_artifact_path(
        parent, parent_path, "artifacts/engine-evidence.json"
    )
    comparison = _bound_artifact_path(parent, parent_path, "artifacts/comparison.json")
    packet = _bound_artifact_path(
        review, review_path, "artifacts/group-review-packet.json"
    )
    family = "m1-6-normalized-group-policy-candidate"
    generation_id, staging = _candidate_staging(root, family)
    output = staging / "normalized-group-policy.json"
    for path in (evidence, comparison, packet, *required, output):
        _require_no_symlink_components(
            Path(path), root=root, label="normalized group policy candidate path"
        )
    sys.path.insert(0, str(root))
    generator = importlib.import_module(
        "scripts.research.normalized_group_policy"
    ).generate_active_normalized_group_policy
    command = (
        "python-call",
        "scripts.research.normalized_group_policy.generate_active_normalized_group_policy",
        str(evidence),
        str(comparison),
        str(packet),
        *required,
        str(output),
    )
    try:
        generator(
            evidence_path=evidence,
            comparison_path=comparison,
            packet_path=packet,
            historical_packet_path=Path(required[0]),
            rationale_markdown_path=Path(required[1]),
            rationale_sidecar_path=Path(required[2]),
            output=output,
        )
        manifest = publish_generation(
            artifacts_root=root / "tmp/artifacts/v1/generations",
            family=family,
            generation_id=generation_id,
            run_id=parent.run_id,
            artifact_sources={"artifacts/normalized-group-policy.json": output},
            parents=(parent_binding, review_binding),
            generator=GeneratorBinding(
                identity=_git_head_identity(root), command=command
            ),
            sources=tuple(
                SourceIdentity(
                    name=name,
                    identity=f"sha256:{hashlib.sha256(Path(path).read_bytes()).hexdigest()}",
                )
                for name, path in zip(
                    (
                        "schema3-historical-packet",
                        "group-review-rationale-markdown",
                        "group-review-rationale-sidecar",
                    ),
                    required,
                    strict=True,
                )
            ),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )
        print(
            json.dumps(
                {
                    "manifest_path": (
                        root
                        / "tmp/artifacts/v1/generations"
                        / family
                        / generation_id
                        / "manifest.json"
                    )
                    .relative_to(root)
                    .as_posix(),
                    "manifest_identity": manifest.manifest_identity,
                },
                sort_keys=True,
            )
        )
        return 0
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _generate_grouping_detector_candidate(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if len(values) != _GROUPING_DETECTOR_PARENT_ARGUMENT_COUNT:
        raise AgentReplayInputError(
            "grouping detector requires exact evidence, group-review, and policy "
            "parent manifests"
        )
    evidence, evidence_path, evidence_binding = _resolve_candidate_parent(
        values[:2], root, expected_family="m1-6-current-evidence-candidate"
    )
    review, review_path, review_binding = _resolve_candidate_parent(
        values[2:4], root, expected_family="m1-6-group-review-candidate"
    )
    policy, policy_path, policy_binding = _resolve_candidate_parent(
        values[4:], root, expected_family="m1-6-normalized-group-policy-candidate"
    )
    if (
        len(review.parents) != _GROUP_REVIEW_PARENT_COUNT
        or review.parents[0] != evidence_binding
        or policy.parents != (evidence_binding, review_binding)
    ):
        raise AgentReplayInputError("grouping detector parent chain differs")
    r101_binding = _parent_by_family(review, "m1-6-r101-conservation")
    r101, r101_path = _resolve_bound_parent(
        r101_binding, root, expected_family="m1-6-r101-conservation"
    )
    _bound_artifact_path(r101, r101_path, "artifacts/conservation.json.gz")
    engine_evidence = _bound_artifact_path(
        evidence, evidence_path, "artifacts/engine-evidence.json"
    )
    comparison = _bound_artifact_path(
        evidence, evidence_path, "artifacts/comparison.json"
    )
    group_packet = _bound_artifact_path(
        review, review_path, "artifacts/group-review-packet.json"
    )
    normalized_policy = _bound_artifact_path(
        policy, policy_path, "artifacts/normalized-group-policy.json"
    )
    family = "m1-6-grouping-detector-candidate"
    generation_id, staging = _candidate_staging(root, family)
    output = staging / "grouping-detector.json"
    for path in (
        engine_evidence,
        comparison,
        group_packet,
        normalized_policy,
        output,
    ):
        _require_no_symlink_components(
            Path(path), root=root, label="grouping detector candidate path"
        )
    generator = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).generate_issue_274_detector_report
    command = (
        "python-call",
        "scripts.research.pre_sme_readiness.generate_issue_274_detector_report",
        str(engine_evidence),
        str(comparison),
        str(group_packet),
        str(normalized_policy),
        str(output),
    )
    try:
        generator(
            evidence_path=engine_evidence,
            comparison_path=comparison,
            group_packet_path=group_packet,
            policy_path=normalized_policy,
            output=output,
        )
        manifest = publish_generation(
            artifacts_root=root / "tmp/artifacts/v1/generations",
            family=family,
            generation_id=generation_id,
            run_id=evidence.run_id,
            artifact_sources={"artifacts/grouping-detector.json": output},
            parents=(evidence_binding, review_binding, policy_binding),
            generator=GeneratorBinding(
                identity=_git_head_identity(root), command=command
            ),
            sources=(),
            retention=RetentionBinding(
                retention_class="referenced-bounded-run",
                owner="decomposition",
                expires_at=None,
            ),
        )
        print(
            json.dumps(
                {
                    "manifest_path": (
                        root
                        / "tmp/artifacts/v1/generations"
                        / family
                        / generation_id
                        / "manifest.json"
                    )
                    .relative_to(root)
                    .as_posix(),
                    "manifest_identity": manifest.manifest_identity,
                },
                sort_keys=True,
            )
        )
        return 0
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _promote_normalized_group_policy_candidate(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if len(values) != _POLICY_PROMOTION_PARENT_ARGUMENT_COUNT:
        raise AgentReplayInputError(
            "promotion requires exact evidence, group-review, and policy parent "
            "manifests"
        )
    evidence, evidence_path, evidence_binding = _resolve_candidate_parent(
        values[:2], root, expected_family="m1-6-current-evidence-candidate"
    )
    review, _review_path, review_binding = _resolve_candidate_parent(
        values[2:4], root, expected_family="m1-6-group-review-candidate"
    )
    policy, policy_path, _policy_binding = _resolve_candidate_parent(
        values[4:], root, expected_family="m1-6-normalized-group-policy-candidate"
    )
    if (
        len(review.parents) != _GROUP_REVIEW_PARENT_COUNT
        or review.parents[0] != evidence_binding
        or policy.parents
        != (
            evidence_binding,
            review_binding,
        )
    ):
        raise AgentReplayInputError("promotion candidate parent chain differs")
    r101_binding = _parent_by_family(review, "m1-6-r101-conservation")
    r101, r101_path = _resolve_bound_parent(
        r101_binding, root, expected_family="m1-6-r101-conservation"
    )
    candidates = (
        _bound_artifact_path(evidence, evidence_path, "artifacts/engine-evidence.json"),
        _bound_artifact_path(evidence, evidence_path, "artifacts/comparison.json"),
        _bound_artifact_path(
            policy, policy_path, "artifacts/normalized-group-policy.json"
        ),
        _bound_artifact_path(r101, r101_path, "artifacts/conservation.json.gz"),
    )
    targets = tuple(
        root / relative
        for relative in (
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
            "ontolib/src/ontolib/decomposition/data/normalized-group-policy.json",
            "ontolib/tests/decomposition/golden/neoplasm-r101-v5-conservation.json.gz",
        )
    )
    for path in (*candidates, *targets):
        _require_no_symlink_components(
            path, root=root, label="normalized group policy promotion path"
        )
    sys.path.insert(0, str(root))
    module = importlib.import_module("ontolib.decomposition.normalized_group_policy")
    policy = module.load_normalized_group_policy(candidates[2])
    if len(policy.rows) != _NORMALIZED_GROUP_POLICY_ROW_COUNT:
        raise AgentReplayInputError("normalized group policy row count differs")
    generator = importlib.import_module("scripts.research.normalized_group_policy")
    try:
        generator.validate_promotion_bundle(
            evidence_path=candidates[0],
            comparison_path=candidates[1],
            policy_path=candidates[2],
            current_evidence_path=targets[0],
        )
        generator.promote_bundle_atomically(
            tuple(zip(candidates, targets, strict=True))
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


def _generate_specialist_review_packets(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "generate-specialist-review-packets accepts no arguments"
        )
    (
        script,
        literature,
        registry,
        cadsr,
        diagnostics,
        evidence,
        comparison,
        groups,
        labels,
        ncit,
    ) = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "tmp/m1-6-specialist-literature-context.json",
            "ontolib/tests/decomposition/golden/proposal-registry.json",
            "tmp/m1-6-specialist-cadsr-usage.json",
            "tmp/m1-6-axis-diagnostics-rev2.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
            "tmp/m1-6-group-review-packet-rev2.json",
            "ontolib/tests/decomposition/golden/neoplasm-draft.json",
            "data/ncit-owl/Thesaurus-stated.owl",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "generate-specialist-review-packets",
            "--literature-context",
            literature,
            "--proposal-registry",
            registry,
            "--cadsr-usage",
            cadsr,
            "--label-source",
            labels,
            "--ncit-source",
            ncit,
            "--axis-diagnostics",
            diagnostics,
            "--current-evidence",
            evidence,
            "--current-comparison",
            comparison,
            "--group-review-packet",
            groups,
            "--output-directory",
            str(root / "tmp/m1-6-specialist-packets"),
            "--producing-command",
            "pdm run agent-replay generate-specialist-review-packets",
        ],
        root,
        runner,
    )


def _generate_specialist_literature_context(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "generate-specialist-literature-context accepts no arguments"
        )
    source, _script = _require_files(
        root,
        (
            "scripts/research/data/specialist_literature_context_26_07d.json",
            "scripts/research/specialist_literature_context.py",
        ),
    )
    return _run(
        [
            sys.executable,
            "-m",
            "scripts.research.specialist_literature_context",
            "--source",
            source,
            "--output",
            str(root / "tmp/m1-6-specialist-literature-context.json"),
        ],
        root,
        runner,
    )


def _generate_specialist_cadsr_usage(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "generate-specialist-cadsr-usage accepts no arguments"
        )
    database, _script = _require_files(
        root,
        ("data/cadsr/cde_repository.db", "scripts/research/specialist_cadsr_usage.py"),
    )
    command = [
        sys.executable,
        "-m",
        "scripts.research.specialist_cadsr_usage",
        "--database",
        database,
        "--output",
        str(root / "tmp/m1-6-specialist-cadsr-usage.json"),
        "--limit",
        "100",
        "--producing-command",
        "pdm run agent-replay generate-specialist-cadsr-usage",
    ]
    for code in ("C27262", "C102870", "C6135", "C4791", "C100054", "C198031", "C35756"):
        command.extend(("--root-code", code))
    return _run(command, root, runner)


def _validate_specialist_review_generation(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "validate-specialist-review-generation accepts no arguments"
        )
    script, _index, _validation = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "tmp/m1-6-specialist-packets/index.json",
            "tmp/m1-6-specialist-packets/generation-validation.json",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "validate-specialist-review-generation",
            "--directory",
            str(root / "tmp/m1-6-specialist-packets"),
        ],
        root,
        runner,
    )


def _generate_r103_review(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("generate-r103-review accepts no arguments")
    script, owl, source, proposals = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "data/ncit-owl/Thesaurus-stated.owl",
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "ontolib/tests/decomposition/golden/proposal-registry.json",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "prepare-r103-review-packet",
            "--stated-owl",
            owl,
            "--source-manifest",
            source,
            "--proposal-registry",
            proposals,
            "--output-packet",
            str(root / "tmp/m1-6-r103-review-packet.json"),
            "--output-xlsx",
            str(root / "tmp/m1-6-r103-review-workbook.xlsx"),
        ],
        root,
        runner,
    )


def _generate_r103_evidence_application(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError(
            "generate-r103-evidence-application accepts no arguments"
        )
    relatives = (
        "data/ncit-owl/Thesaurus-stated.owl",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "ontolib/tests/decomposition/golden/r103-review-state-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json",
        "ontolib/tests/decomposition/golden/r103-c3264-corroboration-26.07d.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
        "ontolib/tests/decomposition/golden/neoplasm-adjudicated.json",
    )
    paths = tuple(Path(item) for item in _require_files(root, relatives))
    generate = importlib.import_module(
        "ontolib.decomposition.r103_evidence_application"
    ).generate_r103_evidence_application
    try:
        artifacts = asyncio.run(
            generate(
                endpoint="http://localhost:7888",
                owl_path=paths[0],
                manifest_path=paths[1],
                rev1_path=paths[2],
                rev2_path=paths[3],
                historical_corroboration_path=paths[4],
                proposal_registry_path=paths[5],
                migration_path=paths[6],
                oracle_path=paths[7],
                output_directory=root / "ontolib/tests/decomposition/golden",
            )
        )
        generate_specificity = importlib.import_module(
            "ontolib.decomposition.r103_specificity_review"
        ).generate_specificity_review_artifacts
        specificity_artifacts = generate_specificity(
            inventory_path=root
            / "ontolib/tests/decomposition/golden/r103-source-inventory-26.07d.json",
            candidate_path=root
            / "ontolib/tests/decomposition/golden/r103-c12950-candidates-26.07d.json",
            authority_path=root
            / (
                "ontolib/tests/decomposition/golden/"
                "r103-authority-normalized-26.07d.json"
            ),
            revision_path=paths[3],
            output_directory=root / "ontolib/tests/decomposition/golden",
        )
        artifacts = (*artifacts, *specificity_artifacts)
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    print(
        " ".join(
            f"artifact_{index}={item.artifact_identity}"
            for index, item in enumerate(artifacts, 1)
        ),
        file=sys.stderr,
    )
    return 0


def _transcribe_r103_specificity_selection(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError(
            "transcribe-r103-specificity-selection accepts no arguments"
        )
    relatives = (
        "ontolib/tests/decomposition/golden/r103-source-inventory-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-c12950-candidates-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-authority-normalized-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-applied-policy-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json",
        "ontolib/tests/decomposition/golden/r103-c2860-specificity-target-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-c2860-specificity-pending-26.07d.json",
    )
    paths = tuple(Path(item) for item in _require_files(root, relatives))
    generate = importlib.import_module(
        "ontolib.decomposition.r103_specificity_review"
    ).generate_selected_specificity_review
    try:
        generate(
            inventory_path=paths[0],
            candidate_path=paths[1],
            authority_path=paths[2],
            application_path=paths[3],
            revision_path=paths[4],
            target_path=paths[5],
            pending_path=paths[6],
            output_path=root
            / (
                "ontolib/tests/decomposition/golden/"
                "r103-c2860-specificity-selected-26.07d.json"
            ),
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


def _validate_r101_current(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("validate-r101-current accepts no arguments")
    script, report, packet, registry = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
            "tmp/r101-review-packet-v3.json",
            "tmp/r101-review-registry-v3-SME.json",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "dry-run-r101-decision-expansion",
            "--report",
            report,
            "--packet",
            packet,
            "--registry",
            registry,
            "--output",
            str(root / "tmp/r101-review-dry-run.json"),
        ],
        root,
        runner,
    )


def _regenerate_r101_current_packet(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "regenerate-r101-current-packet accepts no arguments"
        )
    script, report, source = _require_files(
        root,
        (
            "scripts/adjudication.py",
            "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        ),
    )
    return _run(
        [
            sys.executable,
            script,
            "prepare-r101-review-packet",
            "--report",
            report,
            "--source-manifest",
            source,
            "--endpoint",
            "http://localhost:7888",
            "--output-packet",
            str(root / "tmp/r101-review-packet-current.json"),
            "--output-xlsx",
            str(root / "tmp/r101-review-workbook-current.xlsx"),
        ],
        root,
        runner,
    )


def _report_r101_current_reuse(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    del runner
    if values:
        raise AgentReplayInputError("report-r101-current-reuse accepts no arguments")
    report, existing, current, registry = _require_files(
        root,
        (
            "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
            "tmp/r101-review-packet-v3.json",
            "tmp/r101-review-packet-current.json",
            "tmp/r101-review-registry-v3-SME.json",
        ),
    )
    generate = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).generate_r101_reuse_validation
    try:
        generate(
            report=Path(report),
            existing_packet=Path(existing),
            current_packet=Path(current),
            registry=Path(registry),
            output=root / "tmp/r101-review-reuse-validation.json",
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


def _audit_primary_sites(values: list[str], root: Path, runner: CommandRunner) -> int:
    del runner
    if values:
        raise AgentReplayInputError("audit-primary-sites accepts no arguments")
    source, baseline, artifact = _require_files(
        root,
        (
            "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
            "tmp/m1-6-current-full-corpus.ttl",
        ),
    )
    generate = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).generate_primary_site_audit
    try:
        generate(
            source_manifest=Path(source),
            baseline=Path(baseline),
            artifact=Path(artifact),
            output=root / "tmp/m1-6-primary-site-audit.json",
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


def _generate_pre_sme_readiness(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    status = _capture_required(["git", "status", "--porcelain"], root, runner).strip()
    if status:
        raise AgentReplayInputError("pre-SME readiness refuses a dirty worktree")
    detector, detector_path, _detector_binding = _resolve_candidate_parent(
        values, root, expected_family="m1-6-grouping-detector-candidate"
    )
    if len(detector.parents) != _GROUPING_DETECTOR_PARENT_COUNT:
        raise AgentReplayInputError("pre-SME grouping detector parent chain differs")
    evidence_binding = _parent_by_family(detector, "m1-6-current-evidence-candidate")
    review_binding = _parent_by_family(detector, "m1-6-group-review-candidate")
    policy_binding = _parent_by_family(
        detector, "m1-6-normalized-group-policy-candidate"
    )
    evidence, evidence_path = _resolve_bound_parent(
        evidence_binding, root, expected_family="m1-6-current-evidence-candidate"
    )
    review, review_path = _resolve_bound_parent(
        review_binding, root, expected_family="m1-6-group-review-candidate"
    )
    policy, policy_path = _resolve_bound_parent(
        policy_binding, root, expected_family="m1-6-normalized-group-policy-candidate"
    )
    if review.parents[0] != evidence_binding or policy.parents != (
        evidence_binding,
        review_binding,
    ):
        raise AgentReplayInputError("pre-SME transitive parent chain differs")
    r101_binding = _parent_by_family(review, "m1-6-r101-conservation")
    r101, r101_path = _resolve_bound_parent(
        r101_binding, root, expected_family="m1-6-r101-conservation"
    )
    _bound_artifact_path(evidence, evidence_path, "artifacts/engine-evidence.json")
    _bound_artifact_path(evidence, evidence_path, "artifacts/comparison.json")
    _bound_artifact_path(policy, policy_path, "artifacts/normalized-group-policy.json")
    _bound_artifact_path(r101, r101_path, "artifacts/conservation.json.gz")
    group_packet = _bound_artifact_path(
        review, review_path, "artifacts/group-review-packet.json"
    )
    grouping_detector = _bound_artifact_path(
        detector, detector_path, "artifacts/grouping-detector.json"
    )
    relatives = (
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
        "tmp/m1-6-current-full-corpus.ttl",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v5-conservation.json.gz",
        "tmp/r101-review-reuse-validation.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
        "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json",
        "tmp/m1-6-primary-site-audit.json",
        "ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json",
        "ontolib/tests/decomposition/golden/r103-source-inventory-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-c12950-candidates-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-authority-normalized-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-corroboration-normalized-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-applied-policy-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-c2860-specificity-target-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-c2860-specificity-selected-26.07d.json",
        "tmp/m1-6-verify-evidence.json",
    )
    paths = [Path(item) for item in _require_files(root, relatives)]
    paths.insert(11, group_packet)
    paths.insert(12, grouping_detector)
    generate = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).generate_pre_sme_readiness
    names = (
        "source_manifest",
        "current_evidence",
        "current_comparison",
        "corpus_baseline",
        "corpus_artifact",
        "r101_report",
        "r101_validation",
        "proposal_registry",
        "proposal_registry_migration",
        "row_decisions",
        "primary_site_audit",
        "group_packet",
        "grouping_detector",
        "r103_review_state",
        "r103_source_inventory",
        "r103_candidates",
        "r103_authority",
        "r103_corroboration",
        "r103_applied_policy",
        "r103_specificity_target",
        "r103_specificity_review",
        "verify_evidence",
    )
    output = root / "tmp/m1-6-machine-readiness.json"
    output.unlink(missing_ok=True)
    try:
        git_head = _capture_required(["git", "rev-parse", "HEAD"], root, runner).strip()
        generate(
            **dict(zip(names, paths, strict=True)),
            expected_git_head=git_head,
            output=output,
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


def _run_showcase_operator(root: Path, runner: CommandRunner, *, activate: bool) -> int:
    settings = importlib.import_module("backend.config").Settings()
    client_factory = importlib.import_module(
        "ontolib.terminologies.ncit.client"
    ).ncit_sparql_client
    readiness = importlib.import_module("ontolib.decomposition.showcase_readiness")
    git_head = _capture_required(["git", "rev-parse", "HEAD"], root, runner).strip()
    operation = (
        "activate-enhanced-ncit-showcase"
        if activate
        else "verify-enhanced-ncit-showcase"
    )

    async def execute() -> None:
        async with client_factory(settings.ncit_sparql_url) as client:
            function = (
                readiness.activate_showcase_readiness
                if activate
                else readiness.verify_showcase_readiness
            )
            await function(
                client,
                output=root / "tmp/m1-6-enhanced-showcase-readiness.json",
                git_head=git_head,
                producing_command=f"pdm run agent-replay {operation}",
            )

    asyncio.run(execute())
    return 0


def _activate_enhanced_ncit_showcase(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "activate-enhanced-ncit-showcase accepts no arguments"
        )
    return _run_showcase_operator(root, runner, activate=True)


def _verify_enhanced_ncit_showcase(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "verify-enhanced-ncit-showcase accepts no arguments"
        )
    return _run_showcase_operator(root, runner, activate=False)


def _refresh_sparql_inventory(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError("refresh-sparql-inventory accepts no arguments")
    script = _require_files(root, ("scripts/validation/write_sparql_inventory.py",))[0]
    return _run(
        [
            sys.executable,
            script,
            "--root",
            str(root),
            "--output",
            str(root / "scripts/validation/sparql-inventory.json"),
        ],
        root,
        runner,
    )


def _inspect_podman(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("inspect-podman accepts no arguments")
    socket_path = _podman_socket(root, runner)
    captured_now = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    environment = _docker_context_environment()
    commands = (
        [_PODMAN, "version", "--format", "json"],
        [_PODMAN, "info", "--format", "json"],
        [_PODMAN, "machine", "list", "--format", "json"],
        [_PODMAN, "system", "connection", "list", "--format", "json"],
        ["/usr/bin/stat", "-f", "%N %HT %Sp %Su %Sg", str(socket_path)],
        ["/usr/sbin/lsof", "-n", "-a", "-U", str(socket_path)],
        [_DOCKER, "context", "show"],
        [_DOCKER, "context", "inspect", _PODMAN_DOCKER_CONTEXT],
        *(["/usr/bin/printenv", variable] for variable in DOCKER_SELECTOR_VARIABLES),
        [_DOCKER, "version"],
        [_DOCKER, "info"],
        [_DOCKER_COMPOSE, "version"],
        [_PODMAN, "compose", "version"],
        [_DOCKER, "compose", "config", "--services"],
        [_DOCKER, "compose", "ps", "-a"],
        *(
            [
                _DOCKER,
                "inspect",
                "--format",
                "{{json .State}} {{json .RestartCount}}",
                container,
            ]
            for container in (
                "ontoprism-qlever-ncit",
                "ontoprism-qlever-uberon",
                "ontoprism-postgres",
            )
        ),
        [_DOCKER, "events", "--since", "2h", "--until", captured_now],
        [
            _DOCKER,
            "compose",
            "logs",
            "--since",
            "2h",
            "--no-color",
            "--tail",
            "200",
        ],
    )
    for command in commands:
        _collect_diagnostic_command(command, root, runner, environment=environment)
    return 0


def _capture_required(
    command: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    environment: dict[str, str] | None = None,
    timeout: int = _DIAGNOSTIC_TIMEOUT_SECONDS,
    display_limit: int | None = _MAX_DIAGNOSTIC_CHARS,
) -> str:
    rendered_command = " ".join(command)
    try:
        result = runner(
            command,
            cwd=root,
            shell=False,
            check=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_stream_text(exc.stdout)
        stderr = _timeout_stream_text(exc.stderr)
        labelled = _labelled_streams(
            stdout or "", stderr or "", display_limit=display_limit
        )
        raise AgentReplayInputError(
            f"required command timed out after {timeout}s: {rendered_command}"
            f"{f': {labelled}' if labelled else ''}"
        ) from exc
    except OSError as exc:
        raise AgentReplayInputError(
            f"required command could not start: {rendered_command}: "
            f"{_bounded_sanitized(str(exc), limit=display_limit)}"
        ) from exc
    raw_stdout = result.stdout
    raw_stderr = result.stderr
    labelled = _labelled_streams(raw_stdout, raw_stderr, display_limit=display_limit)
    if result.returncode != 0:
        raise AgentReplayInputError(
            f"required command exited nonzero ({result.returncode}): {rendered_command}"
            f"{f': {labelled}' if labelled else ''}"
        )
    if labelled:
        print(labelled)
    return raw_stdout


def _timeout_stream_text(value: bytes | str | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


def _labelled_streams(stdout: str, stderr: str, *, display_limit: int | None) -> str:
    displayed_stdout = _bounded_sanitized(stdout, limit=display_limit)
    displayed_stderr = _bounded_sanitized(stderr, limit=display_limit)
    return "\n".join(
        line
        for line in (
            f"stdout: {displayed_stdout}" if displayed_stdout else "",
            f"stderr: {displayed_stderr}" if displayed_stderr else "",
        )
        if line
    )


def _podman_socket(root: Path, runner: CommandRunner) -> Path:
    output = _capture_required(
        [_PODMAN, "machine", "inspect", _PODMAN_MACHINE], root, runner
    )
    try:
        payload = json.loads(output)
        machine = payload[0]
        socket_path = Path(machine["ConnectionInfo"]["PodmanSocket"]["Path"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid Podman machine contract") from exc
    if (
        len(payload) != 1
        or machine.get("Name") != _PODMAN_MACHINE
        or machine.get("State") != "running"
        or machine.get("Rootful") is not False
        or not socket_path.is_absolute()
        or socket_path.name != "ontoprism-vm-api.sock"
        or socket_path.parent.name != "podman"
    ):
        raise AgentReplayInputError("invalid Podman machine contract")
    return socket_path


def _docker_context_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for variable in DOCKER_SELECTOR_VARIABLES:
        environment.pop(variable, None)
    return environment


def _validate_safe_podman_context(output: str) -> None:
    try:
        contexts = json.loads(output)
        context = contexts[0]
        metadata = context["Metadata"]
        endpoints = context["Endpoints"]
        docker_endpoint = endpoints["docker"]
        host = docker_endpoint["Host"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid safe Docker context contract") from exc
    if (
        len(contexts) != 1
        or context.get("Name") != _PODMAN_DOCKER_CONTEXT
        or not isinstance(metadata, dict)
        or metadata.get("Description") != _PODMAN_DOCKER_CONTEXT_DESCRIPTION
        or set(endpoints) != {"docker"}
        or not isinstance(docker_endpoint, dict)
        or docker_endpoint.get("SkipTLSVerify") is not False
        or not isinstance(host, str)
        or not host.startswith("unix:///")
        or not Path(host.removeprefix("unix://")).is_absolute()
    ):
        raise AgentReplayInputError("invalid safe Docker context contract")


def _validate_active_podman_context(output: str, socket_path: Path) -> None:
    _validate_safe_podman_context(output)
    context = json.loads(output)[0]
    if context["Endpoints"]["docker"]["Host"] != f"unix://{socket_path}":
        raise AgentReplayInputError("active Podman endpoint predicate failed")


def _validate_podman_api_info(output: str) -> None:
    try:
        info = json.loads(output)
        security_options = info["SecurityOptions"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid Podman API contract") from exc
    if (
        not isinstance(info, dict)
        or info.get("OSType") != "linux"
        or not isinstance(info.get("ServerVersion"), str)
        or not cast("str", info["ServerVersion"])
        or not isinstance(info.get("DockerRootDir"), str)
        or not cast("str", info["DockerRootDir"]).endswith("/containers/storage")
        or not isinstance(security_options, list)
        or "name=rootless" not in security_options
        or info.get("ProductLicense") != "Apache-2.0"
    ):
        raise AgentReplayInputError("invalid Podman API contract")


def _activate_podman_docker_context(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError(
            "activate-podman-docker-context accepts no arguments"
        )
    socket_path = _podman_socket(root, runner)
    endpoint = f"unix://{socket_path}"
    environment = _docker_context_environment()
    prior_context = _capture_required(
        [_DOCKER, "context", "show"],
        root,
        runner,
        environment=environment,
    ).strip()
    if not prior_context or "\n" in prior_context:
        raise AgentReplayInputError("invalid current Docker context contract")
    print(f"prior-docker-context={prior_context}")

    context_lines = _capture_required(
        [_DOCKER, "context", "ls", "--format", "{{.Name}}"],
        root,
        runner,
        environment=environment,
    ).splitlines()
    contexts = [name.strip() for name in context_lines if name.strip()]
    if len(contexts) != len(set(contexts)):
        raise AgentReplayInputError("invalid Docker context inventory contract")

    context_command: list[str]
    if _PODMAN_DOCKER_CONTEXT in contexts:
        existing = _capture_required(
            [_DOCKER, "context", "inspect", _PODMAN_DOCKER_CONTEXT],
            root,
            runner,
            environment=environment,
        )
        _validate_safe_podman_context(existing)
        context_command = [_DOCKER, "context", "update", _PODMAN_DOCKER_CONTEXT]
    else:
        context_command = [_DOCKER, "context", "create", _PODMAN_DOCKER_CONTEXT]
    _capture_required(
        [
            *context_command,
            "--description",
            _PODMAN_DOCKER_CONTEXT_DESCRIPTION,
            "--docker",
            f"host={endpoint}",
        ],
        root,
        runner,
        environment=environment,
    )
    _capture_required(
        [_DOCKER, "context", "use", _PODMAN_DOCKER_CONTEXT],
        root,
        runner,
        environment=environment,
    )

    inspected = _capture_required(
        [_DOCKER, "context", "inspect", _PODMAN_DOCKER_CONTEXT],
        root,
        runner,
        environment=environment,
    )
    _validate_active_podman_context(inspected, socket_path)
    active_context = _capture_required(
        [_DOCKER, "context", "show"],
        root,
        runner,
        environment=environment,
    ).strip()
    if active_context != _PODMAN_DOCKER_CONTEXT:
        raise AgentReplayInputError("active Docker context predicate failed")
    version = _capture_required(
        [_DOCKER, "version"], root, runner, environment=environment
    )
    if re.search(r"(?m)^\s*Podman Engine:\s*$", version) is None:
        raise AgentReplayInputError("Docker client Podman server predicate failed")
    info = _capture_required(
        [_DOCKER, "info", "--format", "{{json .}}"],
        root,
        runner,
        environment=environment,
    )
    _validate_podman_api_info(info)
    print(f"active-docker-context={active_context}")
    print(f"podman-docker-endpoint={endpoint}")
    print("docker-server=Podman")
    print("podman-api-contract=rootless+containers-storage+apache-2.0")
    return 0


def _check_podman_api(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("check-podman-api accepts no arguments")
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    commands = (
        [_DOCKER, "version"],
        [_DOCKER, "info", "--format", "{{json .}}"],
        [_DOCKER_COMPOSE, "version"],
        [_PODMAN, "compose", "version"],
    )
    for command in commands:
        _capture_required(command, root, runner, environment=environment)
    return 0


def _podman_environment(root: Path, socket_path: Path) -> dict[str, str]:
    environment = dict(os.environ)
    inherited_path = environment.get("PATH", "")
    for variable in DOCKER_SELECTOR_VARIABLES:
        environment.pop(variable, None)
    environment.update(
        {
            "PATH": (
                f"{root / '.venv/bin'}:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
                f"{f':{inherited_path}' if inherited_path else ''}"
            ),
            "DOCKER_HOST": f"unix://{socket_path}",
            "PODMAN_COMPOSE_PROVIDER": _DOCKER_COMPOSE,
        }
    )
    return environment


def _podman_gate(
    values: list[str],
    root: Path,
    runner: CommandRunner,
    *,
    operation: str,
    script: Literal["test-integration", "test-integration-full-store", "verify"],
    routing: Literal["environment", "context"],
) -> Literal[0]:
    if values:
        raise AgentReplayInputError(f"{operation} accepts no arguments")
    socket_path = _podman_socket(root, runner)
    if routing == "environment":
        environment = _podman_environment(root, socket_path)
    else:
        environment = _docker_context_environment()
        active_context = _capture_required(
            [_DOCKER, "context", "show"],
            root,
            runner,
            environment=environment,
        ).strip()
        if active_context != _PODMAN_DOCKER_CONTEXT:
            raise AgentReplayInputError("active Docker context predicate failed")
        inspected = _capture_required(
            [_DOCKER, "context", "inspect", _PODMAN_DOCKER_CONTEXT],
            root,
            runner,
            environment=environment,
        )
        _validate_active_podman_context(inspected, socket_path)
    _capture_required(
        [_PDM, "run", script],
        root,
        runner,
        environment=environment,
        timeout=_GATE_TIMEOUT_SECONDS,
        display_limit=None,
    )
    return 0


def _podman_test_integration(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    return _podman_gate(
        values,
        root,
        runner,
        operation="podman-test-integration",
        script="test-integration",
        routing="environment",
    )


def _podman_test_full_store(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    return _podman_gate(
        values,
        root,
        runner,
        operation="podman-test-full-store",
        script="test-integration-full-store",
        routing="environment",
    )


def _podman_verify(values: list[str], root: Path, runner: CommandRunner) -> int:
    return _podman_gate(
        values,
        root,
        runner,
        operation="podman-verify",
        script="verify",
        routing="context",
    )


def _capture_pre_sme_verify(
    values: list[str], root: Path, runner: CommandRunner
) -> int:
    if values:
        raise AgentReplayInputError("capture-pre-sme-verify accepts no arguments")
    status_before = _capture_required(
        ["git", "status", "--porcelain"], root, runner
    ).strip()
    if status_before:
        raise AgentReplayInputError("verify evidence refuses a dirty worktree")
    head_before = _capture_required(["git", "rev-parse", "HEAD"], root, runner).strip()
    socket_path = _podman_socket(root, runner)
    context = _capture_required(
        [_DOCKER, "context", "show"],
        root,
        runner,
        environment=_docker_context_environment(),
    ).strip()
    gate_version = _capture_required([_PDM, "--version"], root, runner).strip()
    evidence_path = root / "tmp/m1-6-verify-evidence.json"
    evidence_path.unlink(missing_ok=True)
    gate_exit: Literal[0] = _podman_gate(
        values,
        root,
        runner,
        operation="capture-pre-sme-verify",
        script="verify",
        routing="context",
    )
    head_after = _capture_required(["git", "rev-parse", "HEAD"], root, runner).strip()
    status_after = _capture_required(
        ["git", "status", "--porcelain"], root, runner
    ).strip()
    if head_after != head_before:
        raise AgentReplayInputError("git HEAD changed during verify gate")
    if status_after:
        raise AgentReplayInputError("verify gate left a dirty worktree")
    writer = importlib.import_module(
        "scripts.research.pre_sme_readiness"
    ).write_verify_evidence
    try:
        writer(
            evidence_path,
            git_head=head_after,
            docker_context=context,
            docker_endpoint=f"unix://{socket_path}",
            gate_executable=_PDM,
            gate_version=gate_version,
            observed_exit_code=gate_exit,
        )
    except ValueError as exc:
        raise AgentReplayInputError(str(exc)) from exc
    return 0


@contextmanager
def _reserved_fixed_ports(ports: tuple[int, ...]) -> Iterator[None]:
    listeners: list[socket.socket] = []
    try:
        for port in ports:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listeners.append(listener)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                message = exc.strerror or str(exc)
                raise AgentReplayInputError(
                    f"fixed port {port} is unavailable: errno {exc.errno}: {message}"
                ) from exc
        yield None
    finally:
        for listener in listeners:
            listener.close()


def _compose_command(compose_file: str) -> list[str]:
    return [
        _DOCKER_COMPOSE,
        "--project-name",
        _PODMAN_PROJECT,
        "--file",
        compose_file,
    ]


def _add_cleanup_note(primary: BaseException, cleanup: AgentReplayInputError) -> None:
    primary.add_note(f"cleanup failure: {cleanup}")


def _podman_compose_up(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-compose-up accepts no arguments")
    with _reserved_fixed_ports(_DATA_PORTS):
        pass
    compose_file = _require_files(root, ("docker-compose.yml",))[0]
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    compose = _compose_command(compose_file)
    _capture_required(
        [*compose, "config"],
        root,
        runner,
        environment=environment,
        timeout=_COMPOSE_TIMEOUT_SECONDS,
    )
    try:
        _capture_required(
            [*compose, "up", "--detach", "--wait"],
            root,
            runner,
            environment=environment,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
        )
    except AgentReplayInputError as primary:
        try:
            _capture_required(
                [*compose, "down"],
                root,
                runner,
                environment=environment,
                timeout=_COMPOSE_TIMEOUT_SECONDS,
            )
        except AgentReplayInputError as cleanup:
            _add_cleanup_note(primary, cleanup)
        raise primary
    return 0


@dataclass(frozen=True)
class NamedVolume:
    name: str


@dataclass(frozen=True)
class BindPath:
    relative: Path


@dataclass(frozen=True)
class ServiceExpectation:
    destination: str
    target_port: str
    host_port: str
    source: NamedVolume | BindPath


ServiceExpectations = TypedDict(
    "ServiceExpectations",
    {
        "postgres": ServiceExpectation,
        "qlever-ncit": ServiceExpectation,
        "qlever-uberon": ServiceExpectation,
    },
)


_SERVICE_EXPECTATIONS: ServiceExpectations = {
    "postgres": ServiceExpectation(
        "/var/lib/postgresql/data", "5432/tcp", "5433", NamedVolume(_PODMAN_VOLUME)
    ),
    "qlever-ncit": ServiceExpectation(
        "/data", "7001/tcp", "7888", BindPath(Path("data/qlever-ncit"))
    ),
    "qlever-uberon": ServiceExpectation(
        "/data", "7001/tcp", "7889", BindPath(Path("data/qlever-uberon"))
    ),
}
_DATA_PORTS = tuple(
    int(_SERVICE_EXPECTATIONS[service].host_port) for service in _COMPOSE_SERVICES
)
_APP_PORTS = (*_DATA_PORTS, 8080)


def _mount_source_is_valid(
    mount: dict[str, object], expectation: ServiceExpectation, root: Path
) -> bool:
    if isinstance(expectation.source, NamedVolume):
        return (
            mount.get("Type") == "volume"
            and mount.get("Name") == expectation.source.name
        )
    if isinstance(expectation.source, BindPath):
        mount_source = mount.get("Source")
        return (
            mount.get("Type") == "bind"
            and isinstance(mount_source, str)
            and Path(mount_source).resolve()
            == (root / expectation.source.relative).resolve()
        )
    assert_never(expectation.source)


def _expected_mount(
    mounts: object, expectation: ServiceExpectation, service: ComposeService
) -> dict[str, object]:
    if not isinstance(mounts, list) or any(
        not isinstance(mount, dict) for mount in mounts
    ):
        raise AgentReplayInputError(f"{service} mounts shape predicate failed")
    matching_mounts = [
        mount for mount in mounts if mount.get("Destination") == expectation.destination
    ]
    if len(matching_mounts) != 1:
        raise AgentReplayInputError(f"{service} mount cardinality failed")
    return cast("dict[str, object]", matching_mounts[0])


def _validate_compose_resource(
    output: str, *, root: Path, service: ComposeService
) -> None:
    expectation = _SERVICE_EXPECTATIONS[service]
    try:
        resource = json.loads(output)[0]
        labels = resource["Config"]["Labels"]
        health = resource["State"]["Health"]["Status"]
        mounts = resource["Mounts"]
        bindings = resource["NetworkSettings"]["Ports"][expectation.target_port]
        identifier = resource["Id"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("invalid compose resource contract") from exc
    if not isinstance(labels, dict):
        raise AgentReplayInputError(f"{service} owner labels predicate failed")
    mount = _expected_mount(mounts, expectation, service)
    if not _mount_source_is_valid(mount, expectation, root):
        raise AgentReplayInputError(f"{service} mount source failed")
    if (
        not isinstance(identifier, str)
        or re.fullmatch(r"[0-9a-f]{64}", identifier) is None
    ):
        raise AgentReplayInputError(f"{service} identity predicate failed")
    if labels.get("com.docker.compose.project") != _PODMAN_PROJECT:
        raise AgentReplayInputError(f"{service} project owner predicate failed")
    if labels.get("com.docker.compose.service") != service:
        raise AgentReplayInputError(f"{service} service label predicate failed")
    if health != "healthy":
        raise AgentReplayInputError(f"{service} health predicate failed")
    if bindings != [{"HostIp": "127.0.0.1", "HostPort": expectation.host_port}]:
        raise AgentReplayInputError(f"{service} port binding predicate failed")


def _podman_compose_check(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-compose-check accepts no arguments")
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    inventory = _capture_required(
        [
            _DOCKER,
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={_PODMAN_PROJECT}",
            "--format",
            '{{.Label "com.docker.compose.service"}}',
        ],
        root,
        runner,
        environment=environment,
    ).splitlines()
    if len(inventory) != len(_COMPOSE_SERVICES) or set(inventory) != set(
        _COMPOSE_SERVICES
    ):
        raise AgentReplayInputError("service inventory predicate failed")
    for service in _COMPOSE_SERVICES:
        output = _capture_required(
            [_DOCKER, "inspect", f"ontoprism-{service}"],
            root,
            runner,
            environment=environment,
        )
        _validate_compose_resource(output, root=root, service=service)
    _capture_required(
        [
            _DOCKER,
            "exec",
            "ontoprism-postgres",
            "getent",
            "hosts",
            "qlever-ncit",
            "qlever-uberon",
        ],
        root,
        runner,
        environment=environment,
    )
    return 0


def _podman_compose_down(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-compose-down accepts no arguments")
    compose_file = _require_files(root, ("docker-compose.yml",))[0]
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    for service in _COMPOSE_SERVICES:
        try:
            output = _capture_required(
                [_DOCKER, "inspect", f"ontoprism-{service}"],
                root,
                runner,
                environment=environment,
            )
        except AgentReplayInputError as exc:
            if "no such object" in str(exc).lower():
                continue
            raise
        try:
            resource = json.loads(output)[0]
            labels = resource["Config"]["Labels"]
            identifier = resource["Id"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AgentReplayInputError("invalid cleanup ownership contract") from exc
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[0-9a-f]{64}", identifier) is None
            or labels.get("com.docker.compose.project") != _PODMAN_PROJECT
            or labels.get("com.docker.compose.service") != service
        ):
            raise AgentReplayInputError("invalid cleanup ownership contract")
    _capture_required(
        [
            *_compose_command(compose_file),
            "down",
        ],
        root,
        runner,
        environment=environment,
        timeout=_COMPOSE_TIMEOUT_SECONDS,
    )
    volume_output = _capture_required(
        [_DOCKER, "volume", "inspect", _PODMAN_VOLUME],
        root,
        runner,
        environment=environment,
    )
    _validate_owned_volume(volume_output)
    return 0


def _validate_owned_volume(output: str) -> None:
    try:
        volume = json.loads(output)[0]
        name = volume["Name"]
        labels = volume["Labels"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AgentReplayInputError("named volume identity predicate failed") from exc
    if not isinstance(labels, dict):
        raise AgentReplayInputError("named volume labels predicate failed")
    if name != _PODMAN_VOLUME:
        raise AgentReplayInputError("named volume identity predicate failed")
    if (
        labels.get("com.docker.compose.project") != _PODMAN_PROJECT
        or labels.get("com.docker.compose.volume") != "ontoprism_pg_data"
    ):
        raise AgentReplayInputError("named volume ownership predicate failed")


def _write_fixed_override(path: Path, content: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(content, sort_keys=True), encoding="utf-8")


def _remove_operation_paths(*paths: Path) -> list[AgentReplayInputError]:
    errors: list[AgentReplayInputError] = []
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(
                AgentReplayInputError(
                    f"temporary path cleanup failed: {path.name}: {exc}"
                )
            )
    return errors


def _finish_cleanup(
    primary: BaseException | None,
    cleanup_errors: list[AgentReplayInputError],
) -> None:
    if primary is not None:
        for cleanup in cleanup_errors:
            _add_cleanup_note(primary, cleanup)
        raise primary
    if cleanup_errors:
        first, *rest = cleanup_errors
        for cleanup in rest:
            _add_cleanup_note(first, cleanup)
        raise first


def _podman_health_reject(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-health-reject accepts no arguments")
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    override = root / _POC_DIR / "broken-health.override.yml"
    data_dir = root / _POC_DIR / "broken-health-postgres"
    compose = [
        _DOCKER_COMPOSE,
        "--project-name",
        "ontoprism-podman-health-reject",
        "--file",
        str(override),
    ]
    primary: BaseException | None = None
    cleanup_errors: list[AgentReplayInputError] = []
    compose_attempted = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _write_fixed_override(
            override,
            {
                "services": {
                    "broken": {
                        "image": _POSTGRES_IMAGE,
                        "environment": {
                            "POSTGRES_USER": "ontoprism",
                            "POSTGRES_PASSWORD": "ontoprism",
                            "POSTGRES_DB": "ontoprism",
                        },
                        "volumes": [f"{data_dir}:/var/lib/postgresql/data"],
                        "healthcheck": {
                            "test": ["CMD", "/bin/false"],
                            "interval": "1s",
                            "timeout": "1s",
                            "retries": 1,
                        },
                    }
                }
            },
        )
        compose_attempted = True
        result = runner(
            [*compose, "up", "--detach", "--wait", "--wait-timeout", "30"],
            cwd=root,
            shell=False,
            check=False,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode == 0:
            raise AgentReplayInputError("broken-health Compose project was accepted")
        raw_detail = f"stdout: {result.stdout}\nstderr: {result.stderr}"
        detail = _bounded_sanitized(raw_detail)
        if (
            "unhealthy" not in raw_detail.lower()
            or "ontoprism-podman-health-reject-broken-1" not in raw_detail
        ):
            raise AgentReplayInputError(
                "broken-health Compose failed for an unexpected reason"
            )
        print(f"broken-health-rejected exit={result.returncode} detail={detail}")
    except AgentReplayInputError as exc:
        primary = exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        primary = exc
    finally:
        if compose_attempted:
            try:
                _capture_required(
                    [*compose, "down"],
                    root,
                    runner,
                    environment=environment,
                    timeout=_COMPOSE_TIMEOUT_SECONDS,
                )
            except AgentReplayInputError as cleanup:
                cleanup_errors.append(cleanup)
        cleanup_errors.extend(_remove_operation_paths(override, data_dir))
    _finish_cleanup(primary, cleanup_errors)
    return 0


@dataclass(frozen=True)
class AppSmokePrecondition:
    environment: dict[str, str]
    volume: NamedVolume


def _app_smoke_precondition(root: Path, runner: CommandRunner) -> AppSmokePrecondition:
    with _reserved_fixed_ports(_APP_PORTS):
        pass
    socket_path = _podman_socket(root, runner)
    environment = _podman_environment(root, socket_path)
    volume_output = _capture_required(
        [_DOCKER, "volume", "inspect", _PODMAN_VOLUME],
        root,
        runner,
        environment=environment,
    )
    _validate_owned_volume(volume_output)
    for service in _COMPOSE_SERVICES:
        try:
            _capture_required(
                [_DOCKER, "inspect", f"ontoprism-{service}"],
                root,
                runner,
                environment=environment,
            )
        except AgentReplayInputError as exc:
            if "no such object" in str(exc).lower():
                continue
            raise
        raise AgentReplayInputError(
            f"app-smoke precondition failed: existing resource ontoprism-{service}"
        )
    return AppSmokePrecondition(environment, NamedVolume(_PODMAN_VOLUME))


def _podman_app_smoke(values: list[str], root: Path, runner: CommandRunner) -> int:
    if values:
        raise AgentReplayInputError("podman-app-smoke accepts no arguments")
    _require_files(
        root,
        (
            "docker-compose.yml",
            "docker-compose.app.yml",
            "Caddyfile",
        ),
    )
    precondition = _app_smoke_precondition(root, runner)
    environment = precondition.environment
    override = root / _POC_DIR / "app-podman.override.yml"
    refresh_dir = root / _POC_DIR / "app-refresh"
    compose = [
        _DOCKER_COMPOSE,
        "--project-name",
        "ontoprism-podman-app",
        "--file",
        str(root / "docker-compose.yml"),
        "--file",
        str(root / "docker-compose.app.yml"),
        "--file",
        str(override),
    ]
    primary: BaseException | None = None
    cleanup_errors: list[AgentReplayInputError] = []
    compose_attempted = False
    try:
        refresh_dir.mkdir(parents=True, exist_ok=True)
        _write_fixed_override(
            override,
            {
                "services": {
                    "api": {
                        "volumes": [
                            "./data/cadsr:/app/data/cadsr:ro",
                            f"{refresh_dir}:/app/refresh",
                        ]
                    }
                },
                "volumes": {
                    "ontoprism_pg_data": {
                        "external": True,
                        "name": precondition.volume.name,
                    }
                },
            },
        )
        compose_attempted = True
        _capture_required(
            [*compose, "up", "--detach", "--wait", "--build"],
            root,
            runner,
            environment=environment,
            timeout=_GATE_TIMEOUT_SECONDS,
        )
        root_page = _capture_required(
            [
                "/usr/bin/curl",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "10",
                "--retry-all-errors",
                "--retry-delay",
                "0",
                "--max-time",
                "180",
                "http://127.0.0.1:8080/",
            ],
            root,
            runner,
            environment=environment,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
        )
        bff = _capture_required(
            [
                "/usr/bin/curl",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "10",
                "--retry-all-errors",
                "--retry-delay",
                "0",
                "--max-time",
                "180",
                "http://127.0.0.1:8080/api/v1/ncit/concepts/C3262",
            ],
            root,
            runner,
            environment=environment,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
        )
        if "<html" not in root_page.lower() or '"code":"C3262"' not in re.sub(
            r"\s+", "", bff
        ):
            raise AgentReplayInputError("full-app Caddy/BFF smoke contract failed")
        dns = _capture_required(
            [
                _DOCKER,
                "exec",
                "ontoprism-api",
                "python",
                "-c",
                "import socket;[socket.getaddrinfo(n,None) for n in "
                "('web','postgres','qlever-ncit','qlever-uberon')]",
            ],
            root,
            runner,
            environment=environment,
        )
        if dns.strip():
            raise AgentReplayInputError("service DNS check emitted unexpected output")
        print("app-smoke=caddy-root+bff-C3262+service-dns")
    except AgentReplayInputError as exc:
        primary = exc
    except OSError as exc:
        primary = exc
    finally:
        if compose_attempted:
            try:
                _capture_required(
                    [*compose, "down"],
                    root,
                    runner,
                    environment=environment,
                    timeout=_COMPOSE_TIMEOUT_SECONDS,
                )
            except AgentReplayInputError as cleanup:
                cleanup_errors.append(cleanup)
        cleanup_errors.extend(_remove_operation_paths(override, refresh_dir))
    _finish_cleanup(primary, cleanup_errors)
    return 0


_OPERATIONS: dict[str, Operation] = {
    "activate-enhanced-ncit-showcase": _activate_enhanced_ncit_showcase,
    "consolidate-obsolete": _consolidate_obsolete,
    "read-issue": _read_issue,
    "decompose-current": _decompose_current,
    "inspect-current-replay": _inspect_current_replay,
    "record-artifact-registry": _record_artifact_registry,
    "generate-current-evidence": _generate_current_evidence,
    "generate-current-evidence-candidate": _generate_current_evidence_candidate,
    "generate-grouping-detector-candidate": _generate_grouping_detector_candidate,
    "regenerate-current-comparison": _regenerate_current_comparison,
    "generate-axis-diagnostics": _generate_axis_diagnostics,
    "generate-group-review-rev2-candidate": _generate_group_review_rev2_candidate,
    "generate-normalized-group-policy-candidate": (
        _generate_normalized_group_policy_candidate
    ),
    "promote-normalized-group-policy-candidate": (
        _promote_normalized_group_policy_candidate
    ),
    "generate-specialist-literature-context": _generate_specialist_literature_context,
    "generate-specialist-cadsr-usage": _generate_specialist_cadsr_usage,
    "generate-specialist-review-packets": _generate_specialist_review_packets,
    "validate-specialist-review-generation": _validate_specialist_review_generation,
    "generate-r103-review": _generate_r103_review,
    "generate-r103-evidence-application": _generate_r103_evidence_application,
    "transcribe-r103-specificity-selection": (_transcribe_r103_specificity_selection),
    "validate-r101-current": _validate_r101_current,
    "verify-enhanced-ncit-showcase": _verify_enhanced_ncit_showcase,
    "regenerate-r101-current-packet": _regenerate_r101_current_packet,
    "report-r101-current-reuse": _report_r101_current_reuse,
    "audit-primary-sites": _audit_primary_sites,
    "generate-pre-sme-readiness": _generate_pre_sme_readiness,
    "refresh-sparql-inventory": _refresh_sparql_inventory,
    "inspect-podman": _inspect_podman,
    "inspect-decomposition-runs": _inspect_decomposition_runs,
    "qualify-current-r101-comparator": _qualify_current_r101_comparator,
    "generate-current-r101-conservation": _generate_current_r101_conservation,
    "generate-current-corpus-baseline": _generate_current_corpus_baseline,
    "promote-current-r101-evidence": _promote_current_r101_evidence,
    "record-current-r101-diagnostic": _record_current_r101_diagnostic,
    "inspect-r101-report": _inspect_r101_report,
    "generate-mixed-chain-inventory": _generate_mixed_chain_inventory,
    "record-mixed-chain-inventory": _record_mixed_chain_inventory,
    "generate-mixed-chain-corrected-projection": (
        _generate_mixed_chain_corrected_projection
    ),
    "record-mixed-chain-corrected-projection": (
        _record_mixed_chain_corrected_projection
    ),
    "activate-podman-docker-context": _activate_podman_docker_context,
    "check-podman-api": _check_podman_api,
    "podman-test-integration": _podman_test_integration,
    "podman-test-full-store": _podman_test_full_store,
    "podman-verify": _podman_verify,
    "capture-pre-sme-verify": _capture_pre_sme_verify,
    "podman-compose-up": _podman_compose_up,
    "podman-compose-check": _podman_compose_check,
    "podman-compose-down": _podman_compose_down,
    "podman-health-reject": _podman_health_reject,
    "podman-app-smoke": _podman_app_smoke,
}


def run_agent_replay(
    arguments: list[str],
    root: Path,
    *,
    runner: CommandRunner | None = None,
) -> int:
    """Validate and run one fixed replay operation without shell interpretation."""
    runner = runner or _subprocess_runner
    root = root.resolve()
    if not arguments:
        raise AgentReplayInputError("replay operation is unsupported")
    operation, *values = arguments
    handler = _OPERATIONS.get(operation)
    if handler is None:
        raise AgentReplayInputError("replay operation is unsupported")
    return handler(values, root, runner)


def main() -> int:
    try:
        return run_agent_replay(sys.argv[1:], Path(__file__).resolve().parents[2])
    except AgentReplayInputError as exc:
        print(str(exc), file=sys.stderr)
        for note in getattr(exc, "__notes__", ()):
            print(note, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
