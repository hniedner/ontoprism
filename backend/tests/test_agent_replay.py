"""Policy tests for the narrowly scoped current-replay agent wrapper."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import scripts.validation.run_agent_replay as replay
from scripts.validation.docker_selectors import DOCKER_SELECTOR_VARIABLES
from scripts.validation.run_agent_replay import (
    AgentReplayInputError,
    run_agent_replay,
)

from ontolib.decomposition.run_artifacts import ArtifactManifest


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0)


def _publish_test_generation(
    tmp_path: Path,
    *,
    family: str,
    generation_id: str,
    artifacts: dict[str, bytes],
    parents: tuple[replay.ParentManifestBinding, ...] = (),
    run_id: str | None = None,
) -> tuple[ArtifactManifest, Path]:
    sources: dict[str, Path] = {}
    for relative, payload in artifacts.items():
        source = tmp_path / "sources" / generation_id / Path(relative).name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        sources[relative] = source
    manifest = replay.publish_generation(
        artifacts_root=tmp_path / "tmp/artifacts/v1/generations",
        family=family,
        generation_id=generation_id,
        run_id=run_id,
        artifact_sources=sources,
        parents=parents,
        generator=replay.GeneratorBinding(identity="git:test", command=("test",)),
        sources=(),
        retention=replay.RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="tests",
            expires_at=None,
        ),
    )
    path = (
        tmp_path
        / "tmp/artifacts/v1/generations"
        / family
        / generation_id
        / "manifest.json"
    )
    return manifest, path


def _publish_readiness_detector_chain(tmp_path: Path) -> tuple[ArtifactManifest, Path]:
    evidence, evidence_path = _publish_test_generation(
        tmp_path,
        family="m1-6-current-evidence-candidate",
        generation_id="readiness-evidence-helper",
        artifacts={
            "artifacts/engine-evidence.json": b"e",
            "artifacts/comparison.json": b"c",
        },
    )
    evidence_binding = replay.ParentManifestBinding(
        family=evidence.family,
        generation_id=evidence.generation_id,
        manifest_path=evidence_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=evidence.manifest_identity,
    )
    r101, r101_path = _publish_test_generation(
        tmp_path,
        family="m1-6-r101-conservation",
        generation_id="readiness-r101-helper",
        artifacts={"artifacts/conservation.json.gz": b"r"},
    )
    r101_binding = replay.ParentManifestBinding(
        family=r101.family,
        generation_id=r101.generation_id,
        manifest_path=r101_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=r101.manifest_identity,
    )
    review, review_path = _publish_test_generation(
        tmp_path,
        family="m1-6-group-review-candidate",
        generation_id="readiness-review-helper",
        artifacts={"artifacts/group-review-packet.json": b"g"},
        parents=(evidence_binding, r101_binding),
    )
    review_binding = replay.ParentManifestBinding(
        family=review.family,
        generation_id=review.generation_id,
        manifest_path=review_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=review.manifest_identity,
    )
    policy, policy_path = _publish_test_generation(
        tmp_path,
        family="m1-6-normalized-group-policy-candidate",
        generation_id="readiness-policy-helper",
        artifacts={"artifacts/normalized-group-policy.json": b"p"},
        parents=(evidence_binding, review_binding),
    )
    policy_binding = replay.ParentManifestBinding(
        family=policy.family,
        generation_id=policy.generation_id,
        manifest_path=policy_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=policy.manifest_identity,
    )
    return _publish_test_generation(
        tmp_path,
        family="m1-6-grouping-detector-candidate",
        generation_id="readiness-detector-helper",
        artifacts={"artifacts/grouping-detector.json": b"d"},
        parents=(evidence_binding, review_binding, policy_binding),
    )


_ROOT = Path(__file__).resolve().parents[2]
_R101_REPORT = (
    _ROOT / "ontolib/tests/decomposition/golden/neoplasm-r101-v5-conservation.json.gz"
)


def _publish_policy_promotion_chain(
    tmp_path: Path,
) -> tuple[
    tuple[ArtifactManifest, Path],
    tuple[ArtifactManifest, Path],
    tuple[ArtifactManifest, Path],
    tuple[ArtifactManifest, Path],
]:
    current_evidence = _ROOT / (
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json"
    )
    normalized_policy = _ROOT / (
        "ontolib/src/ontolib/decomposition/data/normalized-group-policy.json"
    )
    current_module = __import__(
        "scripts.research.current_evidence",
        fromlist=["CurrentComparison", "CurrentEngineEvidence"],
    )
    group_module = __import__(
        "scripts.research.group_review_packet",
        fromlist=["build_machine_group_review_packet"],
    )
    policy_module = __import__(
        "ontolib.decomposition.normalized_group_policy",
        fromlist=["load_normalized_group_policy"],
    )
    policy_model = policy_module.load_normalized_group_policy(normalized_policy)

    def identity(payload: dict[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()

    evidence_payload = json.loads(current_evidence.read_bytes())
    policy_rows = {row.concept_code: row for row in policy_model.rows}
    for concept in evidence_payload["concepts"]:
        row = policy_rows.get(concept["code"])
        if row is None:
            continue
        blocks = {
            pair: (block.normalized_group_id, block.normalized_group_label)
            for block in row.blocks
            for pair in block.pairs
        }
        for constituent in concept["constituents"]:
            group_id, group_label = blocks[(constituent["axis"], constituent["filler"])]
            constituent["normalized_group_id"] = group_id
            constituent["normalized_group_label"] = group_label
    evidence_payload["evidence_identity"] = identity(
        {
            key: value
            for key, value in evidence_payload.items()
            if key != "evidence_identity"
        }
    )
    evidence_model = current_module.CurrentEngineEvidence.model_validate_json(
        json.dumps(evidence_payload)
    )
    regenerated_evidence = tmp_path / "sources/regenerated/engine-evidence.json"
    regenerated_comparison = tmp_path / "sources/regenerated/comparison.json"
    regenerated_evidence.parent.mkdir(parents=True)
    regenerated_evidence.write_text(evidence_model.model_dump_json(indent=2) + "\n")
    current_module.regenerate_current_comparison(
        evidence_path=regenerated_evidence,
        oracle_path=_ROOT
        / "ontolib/tests/decomposition/golden/neoplasm-adjudicated.json",
        row_decisions_path=_ROOT
        / "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json",
        proposal_registry_path=_ROOT
        / "ontolib/tests/decomposition/golden/proposal-registry.json",
        proposal_registry_migration_path=_ROOT
        / (
            "ontolib/tests/decomposition/golden/"
            "proposal-registry-schema2-migration.json"
        ),
        output=regenerated_comparison,
    )
    comparison_model = current_module.CurrentComparison.model_validate_json(
        regenerated_comparison.read_bytes()
    )
    review_model = group_module.build_machine_group_review_packet(
        evidence=evidence_model,
        comparison=comparison_model,
        r101_report_path=_ROOT
        / ("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz"),
    )
    policy_payload = policy_model.model_dump(mode="json", exclude={"policy_identity"})
    policy_payload["basis_evidence_identity"] = evidence_model.evidence_identity
    policy_payload["basis_comparison_identity"] = comparison_model.comparison_identity
    policy_payload["basis_packet_identity"] = review_model.packet_identity
    policy_payload["policy_identity"] = identity(policy_payload)
    policy_model = policy_module.ActiveNormalizedGroupPolicy.model_validate_json(
        json.dumps(policy_payload)
    )
    evidence_bytes = (evidence_model.model_dump_json(indent=2) + "\n").encode()
    comparison_bytes = (comparison_model.model_dump_json(indent=2) + "\n").encode()
    policy_bytes = (policy_model.model_dump_json(indent=2) + "\n").encode()

    evidence, evidence_path = _publish_test_generation(
        tmp_path,
        family="m1-6-current-evidence-candidate",
        generation_id="evidence",
        artifacts={
            "artifacts/engine-evidence.json": evidence_bytes,
            "artifacts/comparison.json": comparison_bytes,
        },
    )
    artifacts_root = tmp_path / "tmp/artifacts/v1/generations"

    def binding(manifest: ArtifactManifest, path: Path) -> replay.ParentManifestBinding:
        return replay.ParentManifestBinding(
            family=manifest.family,
            generation_id=manifest.generation_id,
            manifest_path=path.relative_to(artifacts_root).as_posix(),
            manifest_identity=manifest.manifest_identity,
        )

    evidence_binding = binding(evidence, evidence_path)
    r101, r101_path = _publish_test_generation(
        tmp_path,
        family="m1-6-r101-conservation",
        generation_id="r101",
        artifacts={"artifacts/conservation.json.gz": _R101_REPORT.read_bytes()},
    )
    review, review_path = _publish_test_generation(
        tmp_path,
        family="m1-6-group-review-candidate",
        generation_id="review",
        artifacts={
            "artifacts/group-review-packet.json": (
                review_model.model_dump_json(indent=2) + "\n"
            ).encode()
        },
        parents=(evidence_binding, binding(r101, r101_path)),
    )
    review_binding = binding(review, review_path)
    policy, policy_path = _publish_test_generation(
        tmp_path,
        family="m1-6-normalized-group-policy-candidate",
        generation_id="policy",
        artifacts={"artifacts/normalized-group-policy.json": policy_bytes},
        parents=(evidence_binding, review_binding),
    )
    detector_source = tmp_path / "sources/detector/grouping-detector.json"
    detector_source.parent.mkdir(parents=True)
    readiness_module = __import__(
        "scripts.research.pre_sme_readiness",
        fromlist=["generate_issue_274_detector_report"],
    )
    readiness_module.generate_issue_274_detector_report(
        evidence_path=evidence_path.parent / "artifacts/engine-evidence.json",
        comparison_path=evidence_path.parent / "artifacts/comparison.json",
        group_packet_path=review_path.parent / "artifacts/group-review-packet.json",
        policy_path=policy_path.parent / "artifacts/normalized-group-policy.json",
        output=detector_source,
    )
    detector, detector_path = _publish_test_generation(
        tmp_path,
        family="m1-6-grouping-detector-candidate",
        generation_id="detector",
        artifacts={"artifacts/grouping-detector.json": detector_source.read_bytes()},
        parents=(evidence_binding, review_binding, binding(policy, policy_path)),
    )
    return (
        (evidence, evidence_path),
        (review, review_path),
        (policy, policy_path),
        (detector, detector_path),
    )


def _copy_r101_report(tmp_path: Path) -> tuple[Path, Path]:
    relative = Path("evidence/report.json.gz")
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(_R101_REPORT.read_bytes())
    return relative, path


@pytest.mark.unit
def test_inspect_r101_report_emits_bounded_verified_row_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    relative, path = _copy_r101_report(tmp_path)

    assert run_agent_replay(["inspect-r101-report", str(relative)], tmp_path) == 0

    observed = json.loads(capsys.readouterr().out)
    structural_rows = observed["structural_rows"]
    assert len(structural_rows) == 2_097
    assert all(
        set(row)
        == {
            "axis",
            "axis_source",
            "concept_code",
            "direction",
            "filler_code",
            "most_specific",
            "needs_review",
            "axis_ambiguity_group_id",
            "source_definition_ids",
            "source_occurrence_ids",
            "source_roles",
        }
        for row in structural_rows
    )
    assert structural_rows == sorted(
        structural_rows,
        key=lambda row: (
            row["direction"],
            row["concept_code"],
            row["axis"],
            row["filler_code"],
            json.dumps(row, sort_keys=True, separators=(",", ":")),
        ),
    )
    metadata = observed["metadata_pairs"]
    assert metadata["pair_count"] == 38_648
    assert sum(item["pair_count"] for item in metadata["per_concept_counts"]) == 38_648
    assert (
        sum(item["pair_count"] for item in metadata["transition_cross_tab"]) >= 38_648
    )
    assert observed["count_reconciliation"] == {
        "classified_row_count": 0,
        "metadata_pair_count": 38_648,
        "raw_typed_delta_count": 79_393,
        "recomputed_raw_typed_delta_count": 79_393,
        "structural_row_count": 2_097,
        "verified": True,
    }
    json_identity = "07a7c93a0592110b6afe87e35bd58c1e25206b1154072ba3bc6d9ce1e5c1f2d3"
    report_identity = "1383ccf0d79fb8aab509e8cc94e6e9123e16f0596279d8dea4c526bfa846be57"
    tsv_identity = "595d4a1076855e6a2251e9e9108816d7cf9ea8453c11e9935abad526dd712a3e"
    assert observed["identity_verification"] == {
        "json_identity": {
            "recorded": json_identity,
            "recomputed": json_identity,
            "verified": True,
        },
        "model_validation": "verified",
        "report_identity": {
            "recorded": report_identity,
            "recomputed": report_identity,
            "verified": True,
        },
        "status": "verified",
        "tsv_identity": {
            "recorded": tsv_identity,
            "recomputed": tsv_identity,
            "verified": True,
        },
    }
    assert observed["report_binding"] == {
        "new_run_id": "neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016",
        "non_r101_typed_inventory_identity": (
            "a5fa597920ed4a02225aeac7967eb69724db65f3b9c492748b00a8ca6ab1503b"
        ),
        "old_run_id": "neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820",
        "query_identity": (
            "34610337f2ea48b38bb497485a60deeda7ef0e6835d2e88972c845c018110885"
        ),
        "r101_occurrence_inventory_identity": (
            "e77d040d9ac8dc905f432290361db5bcc9445532200a415591c3a2b2bf4163de"
        ),
        "report_identity": report_identity,
    }
    assert observed["statuses"] == {
        "r101_occurrence_certification": "complete",
        "non_r101_enumeration": "complete",
        "explanation": "incomplete",
        "semantic_isolation": "partial-unqualified",
        "execution_comparability": "unqualified",
        "fully_controlled": False,
        "all_controls_equal": False,
        "causal_attribution": "prohibited",
        "authorization": "pending",
        "publication": "blocked",
    }
    assert observed["file_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["duplicate", "missing", "inconsistent", "count"])
def test_inspect_r101_report_refuses_invalid_report_rows(
    tmp_path: Path, mutation: str
) -> None:
    relative, path = _copy_r101_report(tmp_path)
    payload = json.loads(gzip.decompress(path.read_bytes()))
    changed = deepcopy(payload)
    evidence = changed["non_r101_delta_evidence"]
    if mutation == "duplicate":
        evidence["rows"].append(deepcopy(evidence["rows"][0]))
    elif mutation == "missing":
        evidence["rows"].pop()
    elif mutation == "inconsistent":
        evidence["metadata_deltas"][0]["changed_fields"] = []
    else:
        changed["counts"]["non_r101_delta"] += 1
    path.write_bytes(gzip.compress(json.dumps(changed).encode(), mtime=0))

    with pytest.raises(
        AgentReplayInputError, match="R101 report failed strict validation"
    ):
        run_agent_replay(["inspect-r101-report", str(relative)], tmp_path)


@pytest.mark.unit
def test_inspect_decomposition_runs_accepts_only_bounded_run_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, ...]] = []

    async def inspect(run_ids: tuple[str, ...]) -> list[dict[str, object]]:
        seen.append(run_ids)
        return [{"run_id": run_ids[0], "compatible": False}]

    monkeypatch.setattr(
        replay, "_inspect_decomposition_runs_async", inspect, raising=False
    )
    run_id = "neoplasm-c476420a-879a-4d1b-888a-e183565a2f0b"

    assert run_agent_replay(["inspect-decomposition-runs", run_id], tmp_path) == 0
    assert seen == [(run_id,)]
    with pytest.raises(AgentReplayInputError, match="invalid decomposition run ID"):
        run_agent_replay(["inspect-decomposition-runs", "not-a-run"], tmp_path)


@pytest.mark.unit
def test_current_r101_comparator_qualification_uses_only_fixed_full_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = (
        "tmp/m1-6-prechange-v4-corpus-baseline.json",
        "tmp/m1-6-prechange-v4-full-corpus.ttl",
        "tmp/m1-6-current-full-corpus.ttl",
    )
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    old_run = "neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820"
    new_run = "neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016"
    calls: list[tuple[str, str, Path, Path, Path, Path]] = []

    async def qualify(
        old_run_id: str,
        new_run_id: str,
        baseline: Path,
        old_artifact: Path,
        new_artifact: Path,
        output: Path,
    ) -> None:
        calls.append(
            (old_run_id, new_run_id, baseline, old_artifact, new_artifact, output)
        )

    monkeypatch.setattr(
        replay, "_qualify_current_r101_comparator_async", qualify, raising=False
    )

    assert (
        run_agent_replay(
            ["qualify-current-r101-comparator", old_run, new_run], tmp_path
        )
        == 0
    )
    assert calls == [
        (
            old_run,
            new_run,
            tmp_path / required[0],
            tmp_path / required[1],
            tmp_path / required[2],
            tmp_path / "tmp/m1-6-r101-v5-comparator-qualification.json",
        )
    ]
    with pytest.raises(AgentReplayInputError, match="requires two distinct run IDs"):
        run_agent_replay(["qualify-current-r101-comparator", old_run], tmp_path)
    with pytest.raises(AgentReplayInputError, match="requires two distinct run IDs"):
        run_agent_replay(
            ["qualify-current-r101-comparator", old_run, old_run], tmp_path
        )


@pytest.mark.unit
def test_current_r101_conservation_generation_uses_fixed_qualified_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = (
        "scripts/adjudication.py",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "tmp/m1-6-prechange-v4-corpus-baseline.json",
        "tmp/m1-6-prechange-v4-full-corpus.ttl",
        "tmp/m1-6-current-full-corpus.ttl",
    )
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    class ReportRunner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            output = Path(arguments[arguments.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"report")
            qualification = Path(
                arguments[arguments.index("--qualification-output") + 1]
            )
            qualification.write_bytes(b"qualification")
            return super().__call__(arguments, **kwargs)

    runner = ReportRunner()
    conservation = __import__(
        "ontolib.decomposition.r101_conservation",
        fromlist=["load_r101_conservation_report"],
    )
    monkeypatch.setattr(
        conservation,
        "load_r101_conservation_report",
        lambda _path: SimpleNamespace(
            old_run_id="neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820",
            new_run_id="neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016",
            comparator_qualification_identity="c" * 64,
            report_identity="d" * 64,
        ),
    )
    comparator = __import__(
        "ontolib.decomposition.r101_comparator",
        fromlist=["load_r101_comparator_qualification"],
    )
    monkeypatch.setattr(
        comparator,
        "load_r101_comparator_qualification",
        lambda _path: SimpleNamespace(
            old=SimpleNamespace(run_id="neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820"),
            new=SimpleNamespace(run_id="neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016"),
            qualification_identity="c" * 64,
        ),
    )

    old_run = "neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820"
    new_run = "neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016"
    assert (
        run_agent_replay(
            [
                "generate-current-r101-conservation",
                old_run,
                new_run,
                "schema5-read-only-20260915",
            ],
            tmp_path,
            runner=runner,
        )
        == 0
    )

    command = runner.calls[0][0]
    assert command[:3] == ["/opt/homebrew/bin/pdm", "run", "adjudication"]
    assert old_run in command
    assert new_run in command
    assert str(tmp_path / "tmp/m1-6-prechange-v4-full-corpus.ttl") in command
    assert str(tmp_path / "tmp/m1-6-current-full-corpus.ttl") in command
    assert "generate-r101-conservation" in command
    assert "run_pipeline" not in command
    assert "decompose" not in command
    qualification_output = Path(command[command.index("--qualification-output") + 1])
    assert qualification_output.parent.name.startswith(".staging-")
    generation = (
        tmp_path
        / "tmp/artifacts/v1/generations/m1-6-r101-conservation"
        / "schema5-read-only-20260915"
    )
    assert (generation / "artifacts/conservation.json.gz").read_bytes() == b"report"
    assert (
        generation / "artifacts/comparator-qualification.json"
    ).read_bytes() == b"qualification"
    assert (
        ArtifactManifest.from_dict(
            json.loads((generation / "manifest.json").read_bytes())
        ).run_id
        == new_run
    )


@pytest.mark.unit
def test_current_corpus_baseline_generation_uses_fixed_published_run(
    tmp_path: Path,
) -> None:
    for relative in (
        "scripts/adjudication.py",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "tmp/m1-6-current-full-corpus.ttl",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = _Runner()

    current_run = "neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016"
    assert (
        run_agent_replay(
            ["generate-current-corpus-baseline", current_run], tmp_path, runner=runner
        )
        == 0
    )

    command = runner.calls[0][0]
    assert command[:3] == ["/opt/homebrew/bin/pdm", "run", "adjudication"]
    assert current_run in command
    assert str(tmp_path / "tmp/m1-6-current-full-corpus.ttl") in command
    assert str(tmp_path / "tmp/m1-6-current-corpus-baseline.json") in command


@pytest.mark.unit
def test_corrected_projection_generator_uses_historical_inventory_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = (
        tmp_path
        / "ontolib/src/ontolib/decomposition/data/neoplasm_mixed_chain_inventory.json"
    )
    inventory.parent.mkdir(parents=True)
    inventory.touch()
    report = (
        tmp_path / "ontolib/tests/decomposition/golden/"
        "neoplasm-r101-v5-2b39-historical-conservation.json.gz"
    )
    report.parent.mkdir(parents=True)
    report.touch()
    calls: list[tuple[Path, Path, Path]] = []

    async def generate(inventory_path: Path, report_path: Path, output: Path) -> None:
        calls.append((inventory_path, report_path, output))
        output.write_bytes(b"projection")

    monkeypatch.setattr(
        replay, "_generate_mixed_chain_corrected_projection_async", generate
    )
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:candidate")
    generation_id = "00000000-0000-0000-0000-000000000274"
    monkeypatch.setattr(
        replay.importlib.import_module("uuid"), "uuid4", lambda: generation_id
    )

    assert (
        run_agent_replay(["generate-mixed-chain-corrected-projection"], tmp_path) == 0
    )
    assert len(calls) == 1
    actual_inventory, actual_report, staged_output = calls[0]
    assert (actual_inventory, actual_report) == (inventory, report)
    assert staged_output.name == "corrected-projection.json"
    assert staged_output.parent.name.startswith(f"{generation_id}-")
    assert staged_output.parent.parent.name == ".producer-staging"
    generation = (
        tmp_path
        / "tmp/artifacts/v1/generations/m1-6-mixed-chain-corrected-projection"
        / generation_id
    )
    assert (generation / "artifacts/corrected-projection.json").read_bytes() == (
        b"projection"
    )
    assert (generation / "manifest.json").is_file()
    assert not (tmp_path / "tmp/m1-6-mixed-chain-corrected-projection.json").exists()


@pytest.mark.unit
def test_corrected_projection_promotion_requires_exact_immutable_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, manifest_path = _publish_test_generation(
        tmp_path,
        family="m1-6-mixed-chain-corrected-projection",
        generation_id="projection",
        artifacts={"artifacts/corrected-projection.json": b"projection"},
    )
    written: list[tuple[Path, object]] = []
    projection = SimpleNamespace(projection_identity="a" * 64)
    module = SimpleNamespace(
        load_corrected_projection=lambda path: (
            projection
            if path == manifest_path.parent / "artifacts/corrected-projection.json"
            else None
        ),
        write_corrected_projection=lambda path, value: written.append((path, value)),
    )
    real_import = replay.importlib.import_module
    monkeypatch.setattr(
        replay.importlib,
        "import_module",
        lambda name: (
            module
            if name == "ontolib.decomposition.mixed_chain_projection"
            else real_import(name)
        ),
    )

    assert (
        run_agent_replay(
            [
                "record-mixed-chain-corrected-projection",
                str(manifest_path.relative_to(tmp_path)),
                manifest.manifest_identity,
            ],
            tmp_path,
        )
        == 0
    )
    assert written == [
        (
            tmp_path / "ontolib/tests/decomposition/golden/"
            "neoplasm-r101-v5-corrected-projection.json",
            projection,
        )
    ]


@pytest.mark.unit
def test_current_r101_evidence_promotion_validates_and_writes_fixed_golden_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_source = tmp_path / "tmp/m1-6-r101-v5-conservation.json.gz"
    baseline_source = tmp_path / "tmp/m1-6-current-corpus-baseline.json"
    qualification_source = tmp_path / "tmp/m1-6-r101-v5-comparator-qualification.json"
    report_source.parent.mkdir(parents=True)
    report_source.write_bytes(b"report")
    baseline_source.write_bytes(b"baseline")
    qualification_source.write_bytes(b"qualification")
    golden = tmp_path / "ontolib/tests/decomposition/golden"
    golden.mkdir(parents=True)

    conservation = __import__(
        "ontolib.decomposition.r101_conservation",
        fromlist=["load_r101_conservation_report"],
    )
    baseline_module = __import__(
        "ontolib.decomposition.corpus_baseline", fromlist=["load_corpus_baseline"]
    )
    comparator_module = __import__(
        "ontolib.decomposition.r101_comparator",
        fromlist=["load_r101_comparator_qualification"],
    )
    monkeypatch.setattr(
        conservation,
        "load_r101_conservation_report",
        lambda _path: SimpleNamespace(
            old_run_id="neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820",
            new_run_id="neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016",
            new_run_fingerprint_identity="a" * 64,
            new_representation_identity="b" * 64,
            r101_occurrence_certification="complete",
            non_r101_enumeration="complete",
            explanation="incomplete",
            semantic_isolation="partial-unqualified",
            execution_comparability="unqualified",
            fully_controlled=False,
            all_controls_equal=False,
            causal_attribution="prohibited",
            authorization="pending",
            publication_gate="blocked",
            comparator_qualification_identity="c" * 64,
            non_r101_delta_evidence=SimpleNamespace(
                rows=(object(),),
                metadata_deltas=(object(),),
                classified_rows=(),
                raw_typed_delta_count=3,
            ),
        ),
    )
    monkeypatch.setattr(
        baseline_module,
        "load_corpus_baseline",
        lambda _path: SimpleNamespace(
            run_id="neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016",
            run_fingerprint_identity="a" * 64,
            representation_identity="b" * 64,
        ),
    )
    monkeypatch.setattr(
        comparator_module,
        "load_r101_comparator_qualification",
        lambda _path: SimpleNamespace(
            qualification_identity="c" * 64,
            old=SimpleNamespace(run_id="neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820"),
            new=SimpleNamespace(run_id="neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016"),
        ),
    )

    assert run_agent_replay(["promote-current-r101-evidence"], tmp_path) == 0

    assert (golden / "neoplasm-r101-v5-conservation.json.gz").read_bytes() == b"report"
    assert (
        golden / "neoplasm-current-corpus-baseline.json"
    ).read_bytes() == b"baseline"

    accepted_report = conservation.load_r101_conservation_report(report_source)
    monkeypatch.setattr(
        conservation,
        "load_r101_conservation_report",
        lambda _path: SimpleNamespace(
            **{**vars(accepted_report), "explanation": "complete"}
        ),
    )
    with pytest.raises(
        AgentReplayInputError,
        match="does not certify the fixed comparator pair",
    ):
        run_agent_replay(["promote-current-r101-evidence"], tmp_path)


@pytest.mark.unit
def test_incomplete_current_r101_report_can_only_be_recorded_as_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "tmp/m1-6-r101-v5-conservation.json.gz"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"incomplete-report")
    golden = tmp_path / "ontolib/tests/decomposition/golden"
    golden.mkdir(parents=True)
    conservation = __import__(
        "ontolib.decomposition.r101_conservation",
        fromlist=["load_r101_conservation_report"],
    )
    monkeypatch.setattr(
        conservation,
        "load_r101_conservation_report",
        lambda _path: SimpleNamespace(
            old_run_id="neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820",
            new_run_id="neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016",
            r101_occurrence_certification="blocked",
            explanation_status="incomplete",
            publication_gate="blocked",
            non_r101_delta_evidence=SimpleNamespace(
                rows=(object(),),
                metadata_deltas=(object(),),
                classified_rows=(),
                raw_typed_delta_count=3,
            ),
        ),
    )

    assert run_agent_replay(["record-current-r101-diagnostic"], tmp_path) == 0
    assert (
        golden / "neoplasm-r101-v5-conservation.json.gz"
    ).read_bytes() == b"incomplete-report"


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.unit
def test_group_review_registry_keeps_only_immutable_candidate_operation(
    tmp_path: Path,
) -> None:
    assert "generate-group-review-rev2" not in replay._OPERATIONS
    assert "generate-group-review-rev2-candidate" in replay._OPERATIONS

    with pytest.raises(AgentReplayInputError, match="replay operation is unsupported"):
        run_agent_replay(["generate-group-review-rev2"], tmp_path)
    with pytest.raises(
        AgentReplayInputError,
        match="exact current-evidence and R101 parent manifests are required",
    ):
        run_agent_replay(["generate-group-review-rev2-candidate"], tmp_path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operation", "activate"),
    [
        ("activate-enhanced-ncit-showcase", True),
        ("verify-enhanced-ncit-showcase", False),
    ],
)
def test_showcase_operations_are_fixed_and_refuse_free_form_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    activate: bool,
) -> None:
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        replay,
        "_run_showcase_operator",
        lambda root, runner, *, activate: calls.append((root, activate)) or 0,
        raising=False,
    )

    assert run_agent_replay([operation], tmp_path, runner=_Runner()) == 0
    assert calls == [(tmp_path.resolve(), activate)]
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay([operation, "http://attacker.test/graph"], tmp_path)


class _PodmanDiagnosticRunner:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-vm",
                            "State": "running",
                            "Rootful": False,
                            "SSHConfig": {
                                "Port": 49969,
                                "RemoteUsername": "core",
                            },
                            "ConnectionInfo": {
                                "PodmanSocket": {"Path": str(self.socket_path)}
                            },
                        }
                    ]
                ),
            )
        if arguments == ["/opt/homebrew/bin/docker", "info"]:
            return _Result(1, stderr="Cannot connect to API password=hunter2")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "compose",
            "logs",
            "--since",
            "2h",
            "--no-color",
            "--tail",
            "200",
        ]:
            return _Result(0, stdout="x" * 20_000 + " RECENT-END")
        return _Result(0, stdout="podman diagnostic evidence")


class _PodmanApiRunner:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments[:2] == ["/opt/homebrew/bin/docker", "ps"]:
            return _Result(0)
        if arguments[:2] == ["/opt/homebrew/bin/docker", "inspect"]:
            return _Result(1, stderr="no such object")
        if arguments[:3] == ["/opt/homebrew/bin/docker", "volume", "inspect"]:
            return _Result(1, stderr="no such volume")
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-vm",
                            "State": "running",
                            "Rootful": False,
                            "SSHConfig": {
                                "Port": 49969,
                                "RemoteUsername": "core",
                            },
                            "ConnectionInfo": {
                                "PodmanSocket": {"Path": str(self.socket_path)}
                            },
                        }
                    ]
                ),
            )
        return _Result(0, stdout="compatible")


class _DockerContextRunner(_PodmanApiRunner):
    def __init__(
        self,
        socket_path: Path,
        *,
        contexts: tuple[str, ...] = ("default",),
        current: str = "default",
    ) -> None:
        super().__init__(socket_path)
        self.contexts = contexts
        self.current = current

    def __call__(  # noqa: PLR0911 - fixed command-result table for the fake CLI
        self, arguments: list[str], **kwargs: object
    ) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-vm",
                            "State": "running",
                            "Rootful": False,
                            "SSHConfig": {
                                "Port": 49969,
                                "RemoteUsername": "core",
                            },
                            "ConnectionInfo": {
                                "PodmanSocket": {"Path": str(self.socket_path)}
                            },
                        }
                    ]
                ),
            )
        if arguments == ["/opt/homebrew/bin/docker", "context", "show"]:
            return _Result(0, stdout=f"{self.current}\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "context",
            "ls",
            "--format",
            "{{.Name}}",
        ]:
            return _Result(0, stdout="\n".join(self.contexts) + "\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "context",
            "inspect",
            "ontoprism-podman",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-podman",
                            "Metadata": {
                                "Description": "OntoPrism rootless Podman machine"
                            },
                            "Endpoints": {
                                "docker": {
                                    "Host": f"unix://{self.socket_path}",
                                    "SkipTLSVerify": False,
                                }
                            },
                        }
                    ]
                ),
            )
        if arguments == [
            "/opt/homebrew/bin/docker",
            "context",
            "use",
            "ontoprism-podman",
        ]:
            self.current = "ontoprism-podman"
            return _Result(0, stdout="ontoprism-podman\n")
        if arguments == ["/opt/homebrew/bin/docker", "version"]:
            return _Result(0, stdout="Server:\n Podman Engine:\n  Version: 6.1.0\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "info",
            "--format",
            "{{json .}}",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    {
                        "OSType": "linux",
                        "ServerVersion": "6.1.0",
                        "DockerRootDir": (
                            "/var/home/core/.local/share/containers/storage"
                        ),
                        "SecurityOptions": [
                            "name=seccomp,profile=default",
                            "name=rootless",
                        ],
                        "ProductLicense": "Apache-2.0",
                    }
                ),
            )
        return _Result(0)


def _write_compose_inputs(root: Path, *, app: bool = False) -> None:
    (root / "docker-compose.yml").touch()
    if app:
        (root / "docker-compose.app.yml").touch()
        (root / "Caddyfile").touch()


@pytest.mark.unit
def test_current_replay_uses_only_the_documented_fixed_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for relative in (
        "scripts/decompose.py",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "samples/ncit-26.07d-m1-current-replay.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    run_id = "neoplasm-0b00326b-6a9f-424f-b074-d4f1f8a0304d"

    class ReplayRunner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            self.calls.append((arguments, kwargs))
            output = Path(arguments[arguments.index("--out") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(f"<https://example.test/run/{run_id}> <p> <o> .\n")
            return subprocess.CompletedProcess(arguments, 0, "", f"run={run_id}\n")

    runner = ReplayRunner()
    monkeypatch.setattr(replay, "_verify_persisted_replay", lambda *_: "source-id")
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:abc")

    assert run_agent_replay(["decompose-current"], tmp_path, runner=runner) == 0

    command, options = runner.calls[0]
    assert command[1:-1] == [
        str(tmp_path / "scripts/decompose.py"),
        "--source-manifest",
        str(tmp_path / "data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        "--branch",
        "neoplasm",
        "--sample-manifest",
        str(tmp_path / "samples/ncit-26.07d-m1-current-replay.json"),
        "--walker-max-depth",
        "7",
        "--out",
    ]
    output = Path(command[-1])
    assert output.match(
        "*/tmp/artifacts/v1/generations/m1-6-current-replay/.staging/*/decomposition.ttl"
    )
    assert "tmp/m1-6-current-replay.ttl" not in str(command)
    generation = output.parents[2] / output.parent.name
    manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == run_id
    assert (generation / ".complete").is_file()
    report = json.loads(capsys.readouterr().out)
    assert report["run_id"] == run_id
    assert report["manifest_identity"] == manifest["manifest_identity"]
    assert report["artifact_sha256"] == manifest["artifact_records"][0]["sha256"]
    assert options["shell"] is False
    assert not output.parent.exists()

    first_bytes = {
        path.relative_to(generation): path.read_bytes()
        for path in generation.rglob("*")
        if path.is_file()
    }
    assert run_agent_replay(["decompose-current"], tmp_path, runner=runner) == 0
    assert {
        path.relative_to(generation): path.read_bytes()
        for path in generation.rglob("*")
        if path.is_file()
    } == first_bytes
    completed = [
        path.parent for path in output.parents[2].glob("*/.complete") if path.is_file()
    ]
    assert len(completed) == 2


@pytest.mark.unit
def test_completed_current_replay_resume_operation_is_absent(tmp_path: Path) -> None:
    operation = "decompose-current-resume"

    assert operation not in replay._OPERATIONS
    with pytest.raises(AgentReplayInputError, match="unsupported"):
        run_agent_replay(
            [operation, "neoplasm-4b18c7e5-70ee-4123-ae38-c57e8a552f51"],
            tmp_path,
        )
    assert not (tmp_path / "tmp/artifacts/v1/generations").exists()


@pytest.mark.unit
def test_generator_identity_binds_tracked_worktree_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracked = tmp_path / "scripts/decompose.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"before")

    def fake_git(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        if arguments[-2:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(stdout="abc\n")
        assert arguments[-2:] == ["ls-files", "-z"]
        return SimpleNamespace(stdout="scripts/decompose.py\0")

    monkeypatch.setattr(replay.subprocess, "run", fake_git)

    before = replay._git_head_identity(tmp_path)
    tracked.write_bytes(b"after")

    assert replay._git_head_identity(tmp_path) != before
    assert before.startswith("git:abc+worktree-sha256:")


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["subprocess", "publish"])
def test_current_replay_removes_only_its_staging_directory_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    for relative in (
        "scripts/decompose.py",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "samples/ncit-26.07d-m1-current-replay.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    family_root = tmp_path / "tmp/artifacts/v1/generations/m1-6-current-replay"
    published = family_root / "published/artifact.ttl"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"published")
    generation_id = "00000000-0000-0000-0000-000000000334"
    monkeypatch.setattr(
        replay.importlib.import_module("uuid"), "uuid4", lambda: generation_id
    )
    run_id = "neoplasm-0b00326b-6a9f-424f-b074-d4f1f8a0304d"

    class FailingRunner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            output = Path(arguments[arguments.index("--out") + 1])
            output.write_text(f"<{run_id}> <p> <o> .\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                arguments, 7 if failure == "subprocess" else 0, "", ""
            )

    monkeypatch.setattr(replay, "_verify_persisted_replay", lambda *_: "source-id")
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:abc")
    if failure == "publish":
        monkeypatch.setattr(
            replay,
            "publish_generation",
            lambda **_: (_ for _ in ()).throw(RuntimeError("publish failed")),
        )
        with pytest.raises(RuntimeError, match="publish failed"):
            run_agent_replay(["decompose-current"], tmp_path, runner=FailingRunner())
    else:
        assert (
            run_agent_replay(["decompose-current"], tmp_path, runner=FailingRunner())
            == 7
        )

    assert not (family_root / ".staging" / generation_id).exists()
    assert published.read_bytes() == b"published"


@pytest.mark.unit
def test_evidence_generation_requires_an_exact_parent_binding(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="parent manifest"):
        run_agent_replay(["generate-current-evidence", "guessed-run"], tmp_path)


@pytest.mark.unit
def test_evidence_generation_resolves_an_exact_parent_manifest(
    tmp_path: Path,
) -> None:
    fixed_inputs = (
        "scripts/adjudication.py",
        "samples/ncit-26.07d-m1-current-replay.json",
        "ontolib/tests/decomposition/golden/neoplasm-adjudicated.json",
        "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
    )
    for relative in fixed_inputs:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    run_id = "neoplasm-0b00326b-6a9f-424f-b074-d4f1f8a0304d"
    source = tmp_path / "source.ttl"
    source.write_text(f"<{run_id}> <p> <o> .\n")
    manifest = replay.publish_generation(
        artifacts_root=tmp_path / "tmp/artifacts/v1/generations",
        family="m1-6-current-replay",
        generation_id="parent",
        run_id=run_id,
        artifact_sources={"artifacts/decomposition.ttl": source},
        parents=(),
        generator=replay.GeneratorBinding(identity="git:abc", command=("test",)),
        sources=(),
        retention=replay.RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="tests",
            expires_at=None,
        ),
    )
    manifest_path = (
        tmp_path
        / "tmp/artifacts/v1/generations/m1-6-current-replay/parent/manifest.json"
    )
    runner = _Runner()

    assert (
        run_agent_replay(
            [
                "generate-current-evidence",
                str(manifest_path.relative_to(tmp_path)),
                manifest.manifest_identity,
            ],
            tmp_path,
            runner=runner,
        )
        == 0
    )

    command, options = runner.calls[0]
    migration_option = command.index("--proposal-registry-migration")
    assert command[migration_option + 1] == str(
        tmp_path
        / "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json"
    )
    assert command.count("--proposal-registry-migration") == 1
    artifact_option = command.index("--artifact")
    assert command[artifact_option + 1] == str(
        manifest_path.parent / "artifacts/decomposition.ttl"
    )
    assert command[command.index("--run-id") + 1] == run_id
    assert options["shell"] is False


@pytest.mark.unit
def test_candidate_preflight_publishes_an_immutable_parent_bound_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_inputs = (
        "scripts/adjudication.py",
        "samples/ncit-26.07d-m1-current-replay.json",
        "ontolib/tests/decomposition/golden/neoplasm-adjudicated.json",
        "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
    )
    before: dict[Path, bytes] = {}
    for index, relative in enumerate(fixed_inputs):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixed-input-{index}".encode())
        before[path] = path.read_bytes()
    sample = tmp_path / fixed_inputs[1]
    artifact = tmp_path / "parent.ttl"
    artifact.write_bytes(b"fixed-parent")
    monkeypatch.setattr(
        replay,
        "_CURRENT_REPLAY_SAMPLE_SHA256",
        hashlib.sha256(sample.read_bytes()).hexdigest(),
        raising=False,
    )
    run_id = "neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997"
    monkeypatch.setattr(replay, "_CURRENT_REPLAY_RUN_ID", run_id, raising=False)
    monkeypatch.setattr(
        replay,
        "_CURRENT_REPLAY_ARTIFACT_SHA256",
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
        raising=False,
    )
    parent = replay.publish_generation(
        artifacts_root=tmp_path / "tmp/artifacts/v1/generations",
        family="m1-6-current-replay",
        generation_id="parent",
        run_id=run_id,
        artifact_sources={"artifacts/decomposition.ttl": artifact},
        parents=(),
        generator=replay.GeneratorBinding(
            identity="git:historical-parent", command=("test",)
        ),
        sources=(),
        retention=replay.RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="tests",
            expires_at=None,
        ),
    )
    parent_path = (
        tmp_path
        / "tmp/artifacts/v1/generations/m1-6-current-replay/parent/manifest.json"
    )

    class ProducingRunner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(arguments, **kwargs)
            for option, payload in (
                ("--engine-output", b"new-evidence"),
                ("--comparison-output", b"new-comparison"),
            ):
                Path(arguments[arguments.index(option) + 1]).write_bytes(payload)
            return result

    runner = ProducingRunner()
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:candidate")
    verified: list[tuple[str, Path]] = []

    def verify_persisted(run: str, path: Path) -> str:
        verified.append((run, path))
        return "source-id"

    monkeypatch.setattr(replay, "_verify_persisted_replay", verify_persisted)

    assert (
        run_agent_replay(
            [
                "generate-current-evidence-candidate",
                str(parent_path.relative_to(tmp_path)),
                parent.manifest_identity,
            ],
            tmp_path,
            runner=runner,
        )
        == 0
    )

    command, options = runner.calls[0]
    assert command[command.index("--artifact") + 1] == str(
        parent_path.parent / "artifacts/decomposition.ttl"
    )
    assert command[command.index("--run-id") + 1] == run_id
    assert verified == [(run_id, parent_path.parent / "artifacts/decomposition.ttl")]
    assert ".producer-staging" in command[command.index("--engine-output") + 1]
    assert ".producer-staging" in command[command.index("--comparison-output") + 1]
    assert not any("golden/neoplasm-current-" in value for value in command)
    assert options["shell"] is False
    assert {path: path.read_bytes() for path in before} == before

    manifests = list(
        (
            tmp_path / "tmp/artifacts/v1/generations/m1-6-current-evidence-candidate"
        ).glob("*/manifest.json")
    )
    assert len(manifests) == 1
    candidate = replay.resolve_parent_manifest(manifests[0])
    assert candidate.parents == (
        replay.ParentManifestBinding(
            family=parent.family,
            generation_id=parent.generation_id,
            manifest_path=parent_path.relative_to(
                tmp_path / "tmp/artifacts/v1/generations"
            ).as_posix(),
            manifest_identity=parent.manifest_identity,
        ),
    )
    assert [record.relative_path for record in candidate.artifact_records] == [
        "artifacts/engine-evidence.json",
        "artifacts/comparison.json",
    ]
    assert not (tmp_path / "tmp/m1-6-current-engine-evidence-candidate.json").exists()
    assert not (tmp_path / "tmp/m1-6-current-comparison-candidate.json").exists()


@pytest.mark.unit
def test_candidate_preflight_refuses_missing_or_wrong_parent_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in (
        "scripts/adjudication.py",
        "samples/ncit-26.07d-m1-current-replay.json",
        "ontolib/tests/decomposition/golden/neoplasm-adjudicated.json",
        "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixed")
    del monkeypatch
    with pytest.raises(AgentReplayInputError, match="parent manifest"):
        run_agent_replay(["generate-current-evidence-candidate"], tmp_path)
    with pytest.raises(AgentReplayInputError, match="parent manifest"):
        run_agent_replay(
            ["generate-current-evidence-candidate", "missing/manifest.json", "0" * 64],
            tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["identity", "bytes", "missing"])
def test_candidate_preflight_refuses_changed_parent_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    artifact = tmp_path / "parent.ttl"
    artifact.write_bytes(b"immutable-parent")
    parent = replay.publish_generation(
        artifacts_root=tmp_path / "tmp/artifacts/v1/generations",
        family="m1-6-current-replay",
        generation_id="historical",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        artifact_sources={"artifacts/decomposition.ttl": artifact},
        parents=(),
        generator=replay.GeneratorBinding(identity="git:old", command=("test",)),
        sources=(),
        retention=replay.RetentionBinding(
            retention_class="referenced-bounded-run",
            owner="tests",
            expires_at=None,
        ),
    )
    manifest_path = (
        tmp_path
        / "tmp/artifacts/v1/generations/m1-6-current-replay/historical/manifest.json"
    )
    identity = parent.manifest_identity
    if mutation == "identity":
        identity = "0" * 64
    elif mutation == "bytes":
        (manifest_path.parent / "artifacts/decomposition.ttl").write_bytes(b"changed")
    else:
        (manifest_path.parent / "artifacts/decomposition.ttl").unlink()
    monkeypatch.setattr(
        replay,
        "_adjudication_inputs",
        lambda _root: (_ for _ in ()).throw(AssertionError("parent must fail first")),
    )

    with pytest.raises(AgentReplayInputError):
        run_agent_replay(
            [
                "generate-current-evidence-candidate",
                str(manifest_path.relative_to(tmp_path)),
                identity,
            ],
            tmp_path,
        )


@pytest.mark.unit
def test_unavailable_record_cannot_satisfy_required_candidate_manifest_parent(
    tmp_path: Path,
) -> None:
    unavailable = tmp_path / "tmp/artifacts/v1/unavailable/prechange.json"
    unavailable.parent.mkdir(parents=True)
    unavailable.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "record_type": "unavailable-artifact",
                "family": "m1-6-current-replay",
                "run_id": "neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
                "expected_sha256": "4" * 64,
                "last_known_path": "tmp/m1-6-current-replay.ttl",
                "reason": "overwritten-before-immutable-retention",
                "references": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AgentReplayInputError, match="generation registry"):
        run_agent_replay(
            [
                "generate-current-evidence-candidate",
                str(unavailable.relative_to(tmp_path)),
                "4" * 64,
            ],
            tmp_path,
        )


@pytest.mark.unit
def test_group_review_candidate_consumes_candidate_inputs_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in (
        "scripts/adjudication.py",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    evidence_manifest, evidence_path = _publish_test_generation(
        tmp_path,
        family="m1-6-current-evidence-candidate",
        generation_id="evidence",
        artifacts={
            "artifacts/engine-evidence.json": b"evidence",
            "artifacts/comparison.json": b"comparison",
        },
    )
    r101_manifest, r101_path = _publish_test_generation(
        tmp_path,
        family="m1-6-r101-conservation",
        generation_id="r101",
        artifacts={"artifacts/conservation.json.gz": b"r101"},
    )

    class ProducingRunner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(arguments, **kwargs)
            for option in (
                "--output",
                "--workbook",
                "--correction-audit",
                "--blank-validation",
            ):
                Path(arguments[arguments.index(option) + 1]).write_bytes(
                    option.encode()
                )
            return result

    runner = ProducingRunner()
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:candidate")

    assert (
        run_agent_replay(
            [
                "generate-group-review-rev2-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence_manifest.manifest_identity,
                str(r101_path.relative_to(tmp_path)),
                r101_manifest.manifest_identity,
            ],
            tmp_path,
            runner=runner,
        )
        == 0
    )

    command = runner.calls[0][0]
    assert command[command.index("--current-evidence") + 1] == str(
        evidence_path.parent / "artifacts/engine-evidence.json"
    )
    assert command[command.index("--current-comparison") + 1] == str(
        evidence_path.parent / "artifacts/comparison.json"
    )
    assert command[command.index("--r101-report") + 1] == str(
        r101_path.parent / "artifacts/conservation.json.gz"
    )
    assert not any("golden/neoplasm-current-" in value for value in command)
    manifests = list(
        (tmp_path / "tmp/artifacts/v1/generations/m1-6-group-review-candidate").glob(
            "*/manifest.json"
        )
    )
    assert len(manifests) == 1
    assert tuple(
        parent.manifest_identity
        for parent in replay.resolve_parent_manifest(manifests[0]).parents
    ) == (evidence_manifest.manifest_identity, r101_manifest.manifest_identity)


@pytest.mark.unit
def test_normalized_group_candidate_is_immutable_and_parent_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in (
        "evidence/group-review-packet-26.07d-schema3.json",
        "evidence/group-review-rationale-26.07d.md",
        "evidence/group-review-rationale-26.07d.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    evidence, evidence_path = _publish_test_generation(
        tmp_path,
        family="m1-6-current-evidence-candidate",
        generation_id="evidence",
        artifacts={
            "artifacts/engine-evidence.json": b"evidence",
            "artifacts/comparison.json": b"comparison",
        },
    )
    evidence_binding = replay.ParentManifestBinding(
        family=evidence.family,
        generation_id=evidence.generation_id,
        manifest_path=evidence_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=evidence.manifest_identity,
    )
    r101, r101_path = _publish_test_generation(
        tmp_path,
        family="m1-6-r101-conservation",
        generation_id="r101",
        artifacts={"artifacts/conservation.json.gz": b"r101"},
    )
    r101_binding = replay.ParentManifestBinding(
        family=r101.family,
        generation_id=r101.generation_id,
        manifest_path=r101_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=r101.manifest_identity,
    )
    review, review_path = _publish_test_generation(
        tmp_path,
        family="m1-6-group-review-candidate",
        generation_id="review",
        artifacts={"artifacts/group-review-packet.json": b"packet"},
        parents=(evidence_binding, r101_binding),
    )
    unavailable = tmp_path / (
        "tmp/artifacts/v1/unavailable/"
        "neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997.json"
    )
    unavailable.parent.mkdir(parents=True, exist_ok=True)
    unavailable.write_bytes(b"unavailable")
    real_import = replay.importlib.import_module

    def fake_import(name: str):
        if name == "scripts.research.normalized_group_policy":
            return SimpleNamespace(
                generate_active_normalized_group_policy=lambda **kwargs: kwargs[
                    "output"
                ].write_bytes(b"policy")
            )
        return real_import(name)

    monkeypatch.setattr(replay.importlib, "import_module", fake_import)
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:candidate")

    assert (
        run_agent_replay(
            [
                "generate-normalized-group-policy-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence.manifest_identity,
                str(review_path.relative_to(tmp_path)),
                review.manifest_identity,
            ],
            tmp_path,
        )
        == 0
    )
    manifests = list(
        (
            tmp_path
            / "tmp/artifacts/v1/generations/m1-6-normalized-group-policy-candidate"
        ).glob("*/manifest.json")
    )
    assert len(manifests) == 1
    candidate = replay.resolve_parent_manifest(manifests[0])
    assert tuple(parent.manifest_identity for parent in candidate.parents) == (
        evidence.manifest_identity,
        review.manifest_identity,
    )
    assert candidate.artifact_records[0].relative_path == (
        "artifacts/normalized-group-policy.json"
    )
    assert not (tmp_path / "tmp/m1-6-normalized-group-policy-candidate.json").exists()

    (r101_path.parent / "artifacts/conservation.json.gz").write_bytes(b"wrong-r101")
    with pytest.raises(AgentReplayInputError, match="artifact bytes differ"):
        run_agent_replay(
            [
                "generate-normalized-group-policy-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence.manifest_identity,
                str(review_path.relative_to(tmp_path)),
                review.manifest_identity,
            ],
            tmp_path,
        )


@pytest.mark.unit
def test_normalized_group_candidate_requires_exact_evidence_and_review_parents(
    tmp_path: Path,
) -> None:
    with pytest.raises(AgentReplayInputError, match="evidence and group-review"):
        run_agent_replay(
            ["generate-normalized-group-policy-candidate", "manifest.json", "0" * 64],
            tmp_path,
        )


@pytest.mark.unit
def test_grouping_detector_candidate_is_immutable_and_exact_parent_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, evidence_path = _publish_test_generation(
        tmp_path,
        family="m1-6-current-evidence-candidate",
        generation_id="evidence",
        artifacts={
            "artifacts/engine-evidence.json": b"evidence",
            "artifacts/comparison.json": b"comparison",
        },
    )
    evidence_binding = replay.ParentManifestBinding(
        family=evidence.family,
        generation_id=evidence.generation_id,
        manifest_path=evidence_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=evidence.manifest_identity,
    )
    r101, r101_path = _publish_test_generation(
        tmp_path,
        family="m1-6-r101-conservation",
        generation_id="r101",
        artifacts={"artifacts/conservation.json.gz": b"r101"},
    )
    r101_binding = replay.ParentManifestBinding(
        family=r101.family,
        generation_id=r101.generation_id,
        manifest_path=r101_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=r101.manifest_identity,
    )
    review, review_path = _publish_test_generation(
        tmp_path,
        family="m1-6-group-review-candidate",
        generation_id="review",
        artifacts={"artifacts/group-review-packet.json": b"review"},
        parents=(evidence_binding, r101_binding),
    )
    review_binding = replay.ParentManifestBinding(
        family=review.family,
        generation_id=review.generation_id,
        manifest_path=review_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=review.manifest_identity,
    )
    policy, policy_path = _publish_test_generation(
        tmp_path,
        family="m1-6-normalized-group-policy-candidate",
        generation_id="policy",
        artifacts={"artifacts/normalized-group-policy.json": b"policy"},
        parents=(evidence_binding, review_binding),
    )
    real_import = replay.importlib.import_module

    def fake_import(name: str):
        if name == "scripts.research.pre_sme_readiness":
            return SimpleNamespace(
                generate_issue_274_detector_report=lambda **kwargs: kwargs[
                    "output"
                ].write_bytes(b"detector")
            )
        return real_import(name)

    monkeypatch.setattr(replay.importlib, "import_module", fake_import)
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:candidate")

    assert (
        run_agent_replay(
            [
                "generate-grouping-detector-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence.manifest_identity,
                str(review_path.relative_to(tmp_path)),
                review.manifest_identity,
                str(policy_path.relative_to(tmp_path)),
                policy.manifest_identity,
            ],
            tmp_path,
        )
        == 0
    )
    manifests = list(
        (
            tmp_path / "tmp/artifacts/v1/generations/m1-6-grouping-detector-candidate"
        ).glob("*/manifest.json")
    )
    assert len(manifests) == 1
    candidate = replay.resolve_parent_manifest(manifests[0])
    assert tuple(parent.manifest_identity for parent in candidate.parents) == (
        evidence.manifest_identity,
        review.manifest_identity,
        policy.manifest_identity,
    )
    assert candidate.artifact_records[0].relative_path == (
        "artifacts/grouping-detector.json"
    )


@pytest.mark.unit
def test_normalized_group_promotion_replaces_detector_bound_four_file_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        (evidence, evidence_path),
        (review, review_path),
        (policy, policy_path),
        (detector, detector_path),
    ) = _publish_policy_promotion_chain(tmp_path)
    candidate_paths = (
        evidence_path.parent / "artifacts/engine-evidence.json",
        evidence_path.parent / "artifacts/comparison.json",
        policy_path.parent / "artifacts/normalized-group-policy.json",
        tmp_path / "tmp/artifacts/v1/generations/m1-6-r101-conservation/r101/"
        "artifacts/conservation.json.gz",
    )
    targets = {
        "ontolib/tests/decomposition/golden/"
        "neoplasm-current-engine-evidence.json": json.dumps(
            json.loads(candidate_paths[0].read_bytes()), separators=(",", ":")
        ).encode(),
        "ontolib/tests/decomposition/golden/"
        "neoplasm-current-comparison.json": b"old-comparison",
        "ontolib/src/ontolib/decomposition/data/"
        "normalized-group-policy.json": b"old-policy",
        "ontolib/tests/decomposition/golden/"
        "neoplasm-r101-v5-conservation.json.gz": b"old-r101",
    }
    for relative, payload in targets.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    policy_generator = __import__(
        "scripts.research.normalized_group_policy",
        fromlist=["validate_evidence_policy_group_map"],
    )

    def validate_map_closure(**paths: Path) -> None:
        current_module = __import__(
            "scripts.research.current_evidence", fromlist=["CurrentEngineEvidence"]
        )
        policy_module = __import__(
            "ontolib.decomposition.normalized_group_policy",
            fromlist=["load_normalized_group_policy"],
        )
        policy_generator.validate_evidence_policy_group_map(
            current_module.CurrentEngineEvidence.model_validate_json(
                paths["evidence_path"].read_bytes()
            ),
            policy_module.load_normalized_group_policy(paths["policy_path"]),
        )

    monkeypatch.setattr(
        policy_generator, "validate_promotion_bundle", validate_map_closure
    )

    assert (
        run_agent_replay(
            [
                "promote-normalized-group-policy-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence.manifest_identity,
                str(review_path.relative_to(tmp_path)),
                review.manifest_identity,
                str(policy_path.relative_to(tmp_path)),
                policy.manifest_identity,
                str(detector_path.relative_to(tmp_path)),
                detector.manifest_identity,
            ],
            tmp_path,
        )
        == 0
    )
    assert [path.read_bytes() for path in map(tmp_path.__truediv__, targets)] == [
        path.read_bytes() for path in candidate_paths
    ]
    accepted = {path: (tmp_path / path).read_bytes() for path in targets}
    (detector_path.parent / "artifacts/grouping-detector.json").write_bytes(b"changed")
    with pytest.raises(AgentReplayInputError, match="artifact bytes differ"):
        run_agent_replay(
            [
                "promote-normalized-group-policy-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence.manifest_identity,
                str(review_path.relative_to(tmp_path)),
                review.manifest_identity,
                str(policy_path.relative_to(tmp_path)),
                policy.manifest_identity,
                str(detector_path.relative_to(tmp_path)),
                detector.manifest_identity,
            ],
            tmp_path,
        )
    assert {path: (tmp_path / path).read_bytes() for path in targets} == accepted


@pytest.mark.unit
def test_normalized_group_promotion_requires_all_four_exact_manifest_bindings(
    tmp_path: Path,
) -> None:
    (evidence, evidence_path), (review, review_path), (policy, policy_path), _ = (
        _publish_policy_promotion_chain(tmp_path)
    )

    with pytest.raises(AgentReplayInputError, match="grouping-detector"):
        run_agent_replay(
            [
                "promote-normalized-group-policy-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence.manifest_identity,
                str(review_path.relative_to(tmp_path)),
                review.manifest_identity,
                str(policy_path.relative_to(tmp_path)),
                policy.manifest_identity,
            ],
            tmp_path,
        )


@pytest.mark.unit
def test_detector_bound_promotion_rolls_back_all_tracked_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        (evidence, evidence_path),
        (review, review_path),
        (policy, policy_path),
        (detector, detector_path),
    ) = _publish_policy_promotion_chain(tmp_path)
    candidate_evidence = evidence_path.parent / "artifacts/engine-evidence.json"
    target_payloads = {
        "ontolib/tests/decomposition/golden/"
        "neoplasm-current-engine-evidence.json": json.dumps(
            json.loads(candidate_evidence.read_bytes()), separators=(",", ":")
        ).encode(),
        "ontolib/tests/decomposition/golden/"
        "neoplasm-current-comparison.json": b"comparison-before",
        "ontolib/src/ontolib/decomposition/data/"
        "normalized-group-policy.json": b"policy-before",
        "ontolib/tests/decomposition/golden/"
        "neoplasm-r101-v5-conservation.json.gz": b"r101-before",
    }
    for relative, payload in target_payloads.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    generator = __import__(
        "scripts.research.normalized_group_policy",
        fromlist=["validate_evidence_policy_group_map"],
    )

    def validate_map_closure(**paths: Path) -> None:
        current_module = __import__(
            "scripts.research.current_evidence", fromlist=["CurrentEngineEvidence"]
        )
        policy_module = __import__(
            "ontolib.decomposition.normalized_group_policy",
            fromlist=["load_normalized_group_policy"],
        )
        generator.validate_evidence_policy_group_map(
            current_module.CurrentEngineEvidence.model_validate_json(
                paths["evidence_path"].read_bytes()
            ),
            policy_module.load_normalized_group_policy(paths["policy_path"]),
        )

    monkeypatch.setattr(generator, "validate_promotion_bundle", validate_map_closure)
    real_replace = generator.os.replace
    replacements = 0

    def fail_second_replacement(source: str | Path, target: str | Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("injected replacement failure")
        real_replace(source, target)

    monkeypatch.setattr(generator.os, "replace", fail_second_replacement)

    with pytest.raises(AgentReplayInputError, match="injected replacement failure"):
        run_agent_replay(
            [
                "promote-normalized-group-policy-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence.manifest_identity,
                str(review_path.relative_to(tmp_path)),
                review.manifest_identity,
                str(policy_path.relative_to(tmp_path)),
                policy.manifest_identity,
                str(detector_path.relative_to(tmp_path)),
                detector.manifest_identity,
            ],
            tmp_path,
        )
    assert {
        path: (tmp_path / path).read_bytes() for path in target_payloads
    } == target_payloads


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["forged-clear", "blocked", "wrong-parent"])
def test_normalized_group_promotion_rejects_untrusted_detector_before_writes(
    tmp_path: Path, mutation: str
) -> None:
    (
        (evidence, evidence_path),
        (review, review_path),
        (policy, policy_path),
        (detector, detector_path),
    ) = _publish_policy_promotion_chain(tmp_path)
    detector_bytes = (
        detector_path.parent / "artifacts/grouping-detector.json"
    ).read_bytes()
    parents = detector.parents
    if mutation == "wrong-parent":
        other_policy, other_policy_path = _publish_test_generation(
            tmp_path,
            family="m1-6-normalized-group-policy-candidate",
            generation_id="other-policy",
            artifacts={
                "artifacts/normalized-group-policy.json": (
                    policy_path.parent / "artifacts/normalized-group-policy.json"
                ).read_bytes()
            },
            parents=policy.parents,
        )
        parents = (
            *parents[:2],
            replay.ParentManifestBinding(
                family=other_policy.family,
                generation_id=other_policy.generation_id,
                manifest_path=other_policy_path.relative_to(
                    tmp_path / "tmp/artifacts/v1/generations"
                ).as_posix(),
                manifest_identity=other_policy.manifest_identity,
            ),
        )
    else:
        payload = json.loads(detector_bytes)
        if mutation == "forged-clear":
            payload["normalized_group_policy_identity"] = "0" * 64
        else:
            payload["status"] = "blocked"
            payload["normalized_group_violations"] = ["C1:forged"]
        payload["report_identity"] = hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in payload.items()
                    if key != "report_identity"
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        detector_bytes = (json.dumps(payload, indent=2) + "\n").encode()
    rejected_detector, rejected_path = _publish_test_generation(
        tmp_path,
        family="m1-6-grouping-detector-candidate",
        generation_id=f"detector-{mutation}",
        artifacts={"artifacts/grouping-detector.json": detector_bytes},
        parents=parents,
    )
    target_paths = (
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
        "ontolib/src/ontolib/decomposition/data/normalized-group-policy.json",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v5-conservation.json.gz",
    )
    for relative in target_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tracked-before")

    expected = {
        "forged-clear": "detector identities differ",
        "blocked": "detector reports violations",
        "wrong-parent": "detector parent chain differs",
    }[mutation]
    with pytest.raises(AgentReplayInputError, match=expected):
        run_agent_replay(
            [
                "promote-normalized-group-policy-candidate",
                str(evidence_path.relative_to(tmp_path)),
                evidence.manifest_identity,
                str(review_path.relative_to(tmp_path)),
                review.manifest_identity,
                str(policy_path.relative_to(tmp_path)),
                policy.manifest_identity,
                str(rejected_path.relative_to(tmp_path)),
                rejected_detector.manifest_identity,
            ],
            tmp_path,
        )
    assert all(
        (tmp_path / path).read_bytes() == b"tracked-before" for path in target_paths
    )


def test_record_artifact_registry_writes_sidecars_and_honest_unavailable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = (
        (
            "neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016",
            "tmp/m1-6-current-full-corpus.ttl",
            b"current",
        ),
        (
            "neoplasm-8fb79bb9-b4c8-4832-8731-8c562954a820",
            "../m1-6-prechange-v4-full-corpus.ttl",
            b"prechange",
        ),
    )
    records: list[dict[str, object]] = []
    before: dict[Path, bytes] = {}
    for run_id, persisted_path, payload in runs:
        name = (
            "m1-6-current-full-corpus.ttl"
            if payload == b"current"
            else "m1-6-prechange-v4-full-corpus.ttl"
        )
        artifact = tmp_path / "tmp" / name
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_bytes(payload + run_id.encode())
        before[artifact] = artifact.read_bytes()
        records.append(
            {
                "run_id": run_id,
                "status": "complete",
                "source_identity": "ncit-source",
                "representation_identity": hashlib.sha256(
                    artifact.read_bytes()
                ).hexdigest(),
                "publication_artifact_path": persisted_path,
            }
        )

    async def inspect(*_: object) -> list[dict[str, object]]:
        return records

    monkeypatch.setattr(replay, "_inspect_decomposition_runs_async", inspect)
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:abc")

    unavailable_path = (
        tmp_path / "tmp/artifacts/v1/unavailable/"
        "neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997.json"
    )
    stale = replay.ArtifactUnavailableRecord(
        schema_version=1,
        record_type="unavailable-artifact",
        family="m1-6-current-replay",
        run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
        expected_sha256="4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d",
        last_known_path="tmp/m1-6-current-replay.ttl",
        reason="overwritten-before-immutable-retention",
        references=(),
    )
    replay.write_unavailable_record(unavailable_path, stale)
    stale_bytes = unavailable_path.read_bytes()

    assert run_agent_replay(["record-artifact-registry"], tmp_path) == 0

    assert all(path.read_bytes() == content for path, content in before.items())
    sidecars = list((tmp_path / "tmp/artifacts/v1/legacy-in-place").glob("*.json"))
    assert len(sidecars) == 2
    unavailable = json.loads((unavailable_path).read_text(encoding="utf-8"))
    assert unavailable["record_type"] == "unavailable-artifact"
    assert unavailable["reason"] == "overwritten-before-immutable-retention"
    assert unavailable["references"] == [
        "tmp/m1-6-normalized-group-policy-candidate.json",
        "tmp/m1-6-group-review-pre274-observations.json",
    ]
    assert "artifact_records" not in unavailable
    audits = list((unavailable_path.parent / "superseded").glob("*.json"))
    assert len(audits) == 1
    assert audits[0].read_bytes() == stale_bytes

    sidecar_bytes = {path: path.read_bytes() for path in sidecars}
    monkeypatch.setattr(replay, "_git_head_identity", lambda *_: "git:later")
    assert run_agent_replay(["record-artifact-registry"], tmp_path) == 0
    assert {path: path.read_bytes() for path in sidecars} == sidecar_bytes
    assert (
        unavailable_path.read_bytes()
        == (
            json.dumps(unavailable, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    )
    assert audits[0].read_bytes() == stale_bytes


@pytest.mark.unit
def test_axis_diagnostics_reject_unsafe_fillers_without_an_arbitrary_count_cap(
    tmp_path: Path,
) -> None:
    with pytest.raises(AgentReplayInputError, match="filler"):
        run_agent_replay(
            ["generate-axis-diagnostics", "C35501", "../../unsafe"], tmp_path
        )
    for relative in (
        "scripts/adjudication.py",
        "samples/ncit-26.07d-m1-current-replay.json",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "ontolib/tests/decomposition/golden/neoplasm-adjudicated.json",
        "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json",
        "ontolib/tests/decomposition/golden/proposal-registry.json",
        "ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = _Runner()

    assert (
        run_agent_replay(
            ["generate-axis-diagnostics", *(f"C{index}" for index in range(1, 80))],
            tmp_path,
            runner=runner,
        )
        == 0
    )
    command, _options = runner.calls[0]
    assert command.count("--residual-filler") == 79


@pytest.mark.unit
def test_r101_current_validation_uses_only_existing_sme_artifacts(
    tmp_path: Path,
) -> None:
    for relative in (
        "scripts/adjudication.py",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        "tmp/r101-review-packet-v3.json",
        "tmp/r101-review-registry-v3-SME.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = _Runner()

    assert run_agent_replay(["validate-r101-current"], tmp_path, runner=runner) == 0

    command, options = runner.calls[0]
    assert command[1:] == [
        str(tmp_path / "scripts/adjudication.py"),
        "dry-run-r101-decision-expansion",
        "--report",
        str(
            tmp_path
            / "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz"
        ),
        "--packet",
        str(tmp_path / "tmp/r101-review-packet-v3.json"),
        "--registry",
        str(tmp_path / "tmp/r101-review-registry-v3-SME.json"),
        "--output",
        str(tmp_path / "tmp/r101-review-dry-run.json"),
    ]
    assert options["shell"] is False


@pytest.mark.unit
def test_r101_packet_regeneration_uses_current_report_and_source(
    tmp_path: Path,
) -> None:
    for relative in (
        "scripts/adjudication.py",
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner = _Runner()

    assert (
        run_agent_replay(["regenerate-r101-current-packet"], tmp_path, runner=runner)
        == 0
    )

    command, _options = runner.calls[0]
    assert command[1:] == [
        str(tmp_path / "scripts/adjudication.py"),
        "prepare-r101-review-packet",
        "--report",
        str(
            tmp_path
            / "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz"
        ),
        "--source-manifest",
        str(tmp_path / "data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        "--endpoint",
        "http://localhost:7888",
        "--output-packet",
        str(tmp_path / "tmp/r101-review-packet-current.json"),
        "--output-xlsx",
        str(tmp_path / "tmp/r101-review-workbook-current.xlsx"),
    ]


@pytest.mark.unit
def test_r101_reuse_report_uses_both_packets_and_existing_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in (
        "ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz",
        "tmp/r101-review-packet-v3.json",
        "tmp/r101-review-packet-current.json",
        "tmp/r101-review-registry-v3-SME.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    calls: list[dict[str, Path]] = []
    module = __import__(
        "scripts.research.pre_sme_readiness",
        fromlist=["generate_r101_reuse_validation"],
    )
    monkeypatch.setattr(
        module, "generate_r101_reuse_validation", lambda **values: calls.append(values)
    )

    assert run_agent_replay(["report-r101-current-reuse"], tmp_path) == 0

    assert calls == [
        {
            "report": tmp_path
            / (
                "ontolib/tests/decomposition/golden/"
                "neoplasm-r101-v4-conservation.json.gz"
            ),
            "existing_packet": tmp_path / "tmp/r101-review-packet-v3.json",
            "current_packet": tmp_path / "tmp/r101-review-packet-current.json",
            "registry": tmp_path / "tmp/r101-review-registry-v3-SME.json",
            "output": tmp_path / "tmp/r101-review-reuse-validation.json",
        }
    ]


@pytest.mark.unit
def test_pre_sme_artifact_operations_use_only_fixed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = (
        "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
        "tmp/m1-6-current-full-corpus.ttl",
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json",
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json",
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
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    evidence, evidence_path = _publish_test_generation(
        tmp_path,
        family="m1-6-current-evidence-candidate",
        generation_id="readiness-evidence",
        artifacts={
            "artifacts/engine-evidence.json": b"evidence",
            "artifacts/comparison.json": b"comparison",
        },
    )
    evidence_binding = replay.ParentManifestBinding(
        family=evidence.family,
        generation_id=evidence.generation_id,
        manifest_path=evidence_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=evidence.manifest_identity,
    )
    r101, r101_path = _publish_test_generation(
        tmp_path,
        family="m1-6-r101-conservation",
        generation_id="readiness-r101",
        artifacts={"artifacts/conservation.json.gz": b"r101"},
    )
    r101_binding = replay.ParentManifestBinding(
        family=r101.family,
        generation_id=r101.generation_id,
        manifest_path=r101_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=r101.manifest_identity,
    )
    review, review_path = _publish_test_generation(
        tmp_path,
        family="m1-6-group-review-candidate",
        generation_id="readiness-review",
        artifacts={"artifacts/group-review-packet.json": b"review"},
        parents=(evidence_binding, r101_binding),
    )
    review_binding = replay.ParentManifestBinding(
        family=review.family,
        generation_id=review.generation_id,
        manifest_path=review_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=review.manifest_identity,
    )
    policy, policy_path = _publish_test_generation(
        tmp_path,
        family="m1-6-normalized-group-policy-candidate",
        generation_id="readiness-policy",
        artifacts={"artifacts/normalized-group-policy.json": b"policy"},
        parents=(evidence_binding, review_binding),
    )
    policy_binding = replay.ParentManifestBinding(
        family=policy.family,
        generation_id=policy.generation_id,
        manifest_path=policy_path.relative_to(
            tmp_path / "tmp/artifacts/v1/generations"
        ).as_posix(),
        manifest_identity=policy.manifest_identity,
    )
    detector, detector_path = _publish_test_generation(
        tmp_path,
        family="m1-6-grouping-detector-candidate",
        generation_id="readiness-detector",
        artifacts={"artifacts/grouping-detector.json": b"detector"},
        parents=(evidence_binding, review_binding, policy_binding),
    )
    calls: list[dict[str, Path]] = []
    module = __import__(
        "scripts.research.pre_sme_readiness", fromlist=["generate_primary_site_audit"]
    )
    monkeypatch.setattr(
        module, "generate_primary_site_audit", lambda **values: calls.append(values)
    )
    monkeypatch.setattr(
        module, "generate_pre_sme_readiness", lambda **values: calls.append(values)
    )

    class Runner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["git", "status", "--porcelain"]:
                result.stdout = ""
                result.stderr = ""
            if arguments == ["git", "rev-parse", "HEAD"]:
                result.stdout = "a" * 40 + "\n"
                result.stderr = ""
            return result

    runner = Runner()
    assert run_agent_replay(["audit-primary-sites"], tmp_path, runner=runner) == 0
    assert (
        run_agent_replay(
            [
                "generate-pre-sme-readiness",
                str(detector_path.relative_to(tmp_path)),
                detector.manifest_identity,
            ],
            tmp_path,
            runner=runner,
        )
        == 0
    )

    assert calls[0] == {
        "source_manifest": tmp_path / "data/qlever-ncit/.ontoprism-ncit-candidate.json",
        "baseline": tmp_path
        / "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json",
        "artifact": tmp_path / "tmp/m1-6-current-full-corpus.ttl",
        "output": tmp_path / "tmp/m1-6-primary-site-audit.json",
    }
    assert calls[1]["current_evidence"] == (
        tmp_path
        / "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json"
    )
    assert calls[1]["r101_validation"] == (
        tmp_path / "tmp/r101-review-reuse-validation.json"
    )
    assert calls[1]["r101_report"] == (
        tmp_path
        / "ontolib/tests/decomposition/golden/neoplasm-r101-v5-conservation.json.gz"
    )
    assert calls[1]["group_packet"] == (
        review_path.parent / "artifacts/group-review-packet.json"
    )
    assert calls[1]["row_decisions"] == (
        tmp_path / "ontolib/tests/decomposition/golden/neoplasm-row-decisions.json"
    )
    assert calls[1]["group_packet"] != (tmp_path / "tmp/m1-6-group-review-packet.json")
    assert calls[1]["r103_review_state"] == (
        tmp_path
        / "ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json"
    )
    assert calls[1]["r103_source_inventory"] == (
        tmp_path
        / "ontolib/tests/decomposition/golden/r103-source-inventory-26.07d.json"
    )
    assert calls[1]["r103_candidates"] == (
        tmp_path
        / "ontolib/tests/decomposition/golden/r103-c12950-candidates-26.07d.json"
    )
    assert calls[1]["r103_specificity_target"] == (
        tmp_path
        / "ontolib/tests/decomposition/golden/r103-c2860-specificity-target-26.07d.json"
    )
    assert calls[1]["r103_specificity_review"] == (
        tmp_path
        / (
            "ontolib/tests/decomposition/golden/"
            "r103-c2860-specificity-selected-26.07d.json"
        )
    )
    assert "r103_packet" not in calls[1]
    assert calls[1]["output"] == tmp_path / "tmp/m1-6-machine-readiness.json"
    assert calls[1]["expected_git_head"] == "a" * 40

    (r101_path.parent / "artifacts/conservation.json.gz").write_bytes(b"wrong-r101")
    with pytest.raises(AgentReplayInputError, match="artifact bytes differ"):
        run_agent_replay(
            [
                "generate-pre-sme-readiness",
                str(detector_path.relative_to(tmp_path)),
                detector.manifest_identity,
            ],
            tmp_path,
            runner=runner,
        )


@pytest.mark.unit
def test_r103_specificity_selection_transcription_uses_only_fixed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = (
        "ontolib/tests/decomposition/golden/r103-source-inventory-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-c12950-candidates-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-authority-normalized-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-applied-policy-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json",
        "ontolib/tests/decomposition/golden/r103-c2860-specificity-target-26.07d.json",
        "ontolib/tests/decomposition/golden/r103-c2860-specificity-pending-26.07d.json",
    )
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    calls: list[dict[str, Path]] = []
    module = __import__(
        "ontolib.decomposition.r103_specificity_review",
        fromlist=["generate_selected_specificity_review"],
    )
    monkeypatch.setattr(
        module,
        "generate_selected_specificity_review",
        lambda **values: calls.append(values),
    )

    assert run_agent_replay(["transcribe-r103-specificity-selection"], tmp_path) == 0

    assert calls == [
        {
            "inventory_path": tmp_path / required[0],
            "candidate_path": tmp_path / required[1],
            "authority_path": tmp_path / required[2],
            "application_path": tmp_path / required[3],
            "revision_path": tmp_path / required[4],
            "target_path": tmp_path / required[5],
            "pending_path": tmp_path / required[6],
            "output_path": tmp_path
            / (
                "ontolib/tests/decomposition/golden/"
                "r103-c2860-specificity-selected-26.07d.json"
            ),
        }
    ]


@pytest.mark.unit
def test_pre_sme_readiness_refuses_dirty_worktree_before_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated = False
    module = __import__(
        "scripts.research.pre_sme_readiness", fromlist=["generate_pre_sme_readiness"]
    )

    def mark_generated(**_values: object) -> None:
        nonlocal generated
        generated = True

    monkeypatch.setattr(module, "generate_pre_sme_readiness", mark_generated)

    class Runner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["git", "status", "--porcelain"]:
                result.stdout = " M tracked.py\n"
                result.stderr = ""
            return result

    with pytest.raises(AgentReplayInputError, match="dirty worktree"):
        run_agent_replay(["generate-pre-sme-readiness"], tmp_path, runner=Runner())

    assert generated is False
    assert not (tmp_path / "tmp/m1-6-machine-readiness.json").exists()


@pytest.mark.unit
def test_pre_sme_readiness_generation_failure_removes_stale_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = (
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
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    output = tmp_path / "tmp/m1-6-machine-readiness.json"
    output.write_text("stale readiness\n")
    module = __import__(
        "scripts.research.pre_sme_readiness", fromlist=["generate_pre_sme_readiness"]
    )

    def fail_generation(**_values: object) -> None:
        raise ValueError("readiness failed")

    monkeypatch.setattr(module, "generate_pre_sme_readiness", fail_generation)
    detector, detector_path = _publish_readiness_detector_chain(tmp_path)

    class Runner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["git", "status", "--porcelain"]:
                result.stdout = ""
                result.stderr = ""
            if arguments == ["git", "rev-parse", "HEAD"]:
                result.stdout = "a" * 40 + "\n"
                result.stderr = ""
            return result

    with pytest.raises(AgentReplayInputError, match="readiness failed"):
        run_agent_replay(
            [
                "generate-pre-sme-readiness",
                str(detector_path.relative_to(tmp_path)),
                detector.manifest_identity,
            ],
            tmp_path,
            runner=Runner(),
        )

    assert not output.exists()


@pytest.mark.unit
def test_pre_sme_readiness_refuses_current_packet_without_tracked_state(
    tmp_path: Path,
) -> None:
    for relative in (
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
        "tmp/m1-6-r103-review-packet.json",
        "tmp/m1-6-verify-evidence.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    class Runner(_Runner):
        def __call__(
            self, arguments: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["git", "status", "--porcelain"]:
                result.stdout = ""
                result.stderr = ""
            return result

    detector, detector_path = _publish_readiness_detector_chain(tmp_path)

    with pytest.raises(
        AgentReplayInputError,
        match=(
            r"required input does not exist: ontolib/tests/decomposition/golden/"
            r"r103-review-state-26\.07d-rev2\.json"
        ),
    ):
        run_agent_replay(
            [
                "generate-pre-sme-readiness",
                str(detector_path.relative_to(tmp_path)),
                detector.manifest_identity,
            ],
            tmp_path,
            runner=Runner(),
        )

    assert not (tmp_path / "tmp/m1-6-machine-readiness.json").exists()


@pytest.mark.unit
def test_pre_sme_verify_evidence_is_written_only_after_exact_podman_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTOPRISM_PODMAN_STACK_ENSURED", "1")
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()

    class Runner(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == ["git", "rev-parse", "HEAD"]:
                self.calls.append((arguments, kwargs))
                return _Result(0, stdout="a" * 40 + "\n")
            if arguments == ["git", "status", "--porcelain"]:
                self.calls.append((arguments, kwargs))
                return _Result(0, stdout="")
            if arguments == ["/opt/homebrew/bin/pdm", "--version"]:
                self.calls.append((arguments, kwargs))
                return _Result(0, stdout="PDM, version 2.25.9\n")
            return super().__call__(arguments, **kwargs)

    runner = Runner(
        socket_path,
        contexts=("ontoprism-podman",),
        current="ontoprism-podman",
    )
    (tmp_path / "tmp").mkdir()

    assert run_agent_replay(["capture-pre-sme-verify"], tmp_path, runner=runner) == 0

    commands = [command for command, _options in runner.calls]
    assert ["/opt/homebrew/bin/pdm", "run", "verify"] in commands
    assert commands.count(["git", "rev-parse", "HEAD"]) == 2
    assert commands.count(["git", "status", "--porcelain"]) == 2
    evidence = json.loads((tmp_path / "tmp/m1-6-verify-evidence.json").read_text())
    assert evidence["command"] == "pdm run verify"
    assert evidence["status"] == "passed"
    assert evidence["git_head"] == "a" * 40
    assert evidence["publication_writes_performed"] is False
    assert evidence["observed_exit_code"] == 0
    assert evidence["docker_context"] == "ontoprism-podman"
    assert evidence["docker_endpoint"] == f"unix://{socket_path}"
    assert evidence["gate_executable"] == "/opt/homebrew/bin/pdm"
    assert evidence["gate_version"] == "PDM, version 2.25.9"


@pytest.mark.unit
def test_pre_sme_verify_refuses_dirty_worktree_without_running_gate_or_writing(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()

    class Runner(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == ["git", "status", "--porcelain"]:
                self.calls.append((arguments, kwargs))
                return _Result(0, stdout=" M tracked.py\n")
            return super().__call__(arguments, **kwargs)

    runner = Runner(
        socket_path,
        contexts=("ontoprism-podman",),
        current="ontoprism-podman",
    )
    (tmp_path / "tmp").mkdir()

    with pytest.raises(AgentReplayInputError, match="dirty worktree"):
        run_agent_replay(["capture-pre-sme-verify"], tmp_path, runner=runner)

    assert ["/opt/homebrew/bin/pdm", "run", "verify"] not in [
        command for command, _options in runner.calls
    ]
    assert not (tmp_path / "tmp/m1-6-verify-evidence.json").exists()


@pytest.mark.unit
def test_pre_sme_verify_gate_failure_removes_stale_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTOPRISM_PODMAN_STACK_ENSURED", "1")
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()

    class Runner(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            self.calls.append((arguments, kwargs))
            if arguments == ["git", "status", "--porcelain"]:
                return _Result(0, stdout="")
            if arguments == ["git", "rev-parse", "HEAD"]:
                return _Result(0, stdout="a" * 40 + "\n")
            if arguments == ["/opt/homebrew/bin/pdm", "--version"]:
                return _Result(0, stdout="PDM, version 2.25.9\n")
            if arguments == ["/opt/homebrew/bin/pdm", "run", "verify"]:
                return _Result(1, stderr="verify failed")
            self.calls.pop()
            return super().__call__(arguments, **kwargs)

    runner = Runner(
        socket_path,
        contexts=("ontoprism-podman",),
        current="ontoprism-podman",
    )
    output = tmp_path / "tmp/m1-6-verify-evidence.json"
    output.parent.mkdir()
    output.write_text("stale passed evidence\n")

    with pytest.raises(AgentReplayInputError, match="verify failed"):
        run_agent_replay(["capture-pre-sme-verify"], tmp_path, runner=runner)

    assert not output.exists()


@pytest.mark.unit
def test_wrapper_rejects_unlisted_operations(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="unsupported"):
        run_agent_replay(["import-workbook"], tmp_path)


@pytest.mark.unit
def test_inspect_podman_runs_only_fixed_bounded_read_only_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanDiagnosticRunner(socket_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "PATH": "inherited",
            **dict.fromkeys(DOCKER_SELECTOR_VARIABLES, "unsafe"),
        },
    )

    assert run_agent_replay(["inspect-podman"], tmp_path, runner=runner) == 0

    commands = [command for command, _options in runner.calls]
    events = next(
        command for command in commands if command[1:3] == ["events", "--since"]
    )
    assert events[:4] == ["/opt/homebrew/bin/docker", "events", "--since", "2h"]
    assert events[4] == "--until"
    assert events[5].endswith("Z")
    assert commands == [
        [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ],
        ["/opt/homebrew/bin/podman", "version", "--format", "json"],
        ["/opt/homebrew/bin/podman", "info", "--format", "json"],
        ["/opt/homebrew/bin/podman", "machine", "list", "--format", "json"],
        [
            "/opt/homebrew/bin/podman",
            "system",
            "connection",
            "list",
            "--format",
            "json",
        ],
        ["/usr/bin/stat", "-f", "%N %HT %Sp %Su %Sg", str(socket_path)],
        ["/usr/sbin/lsof", "-n", "-a", "-U", str(socket_path)],
        ["/opt/homebrew/bin/docker", "context", "show"],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "inspect",
            "ontoprism-podman",
        ],
        *(["/usr/bin/printenv", variable] for variable in DOCKER_SELECTOR_VARIABLES),
        ["/opt/homebrew/bin/docker", "version"],
        ["/opt/homebrew/bin/docker", "info"],
        ["/opt/homebrew/bin/docker-compose", "version"],
        ["/opt/homebrew/bin/podman", "compose", "version"],
        ["/opt/homebrew/bin/docker", "compose", "config", "--services"],
        ["/opt/homebrew/bin/docker", "compose", "ps", "-a"],
        *(
            [
                "/opt/homebrew/bin/docker",
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
        events,
        [
            "/opt/homebrew/bin/docker",
            "compose",
            "logs",
            "--since",
            "2h",
            "--no-color",
            "--tail",
            "200",
        ],
    ]
    assert all(options["cwd"] == tmp_path for _command, options in runner.calls)
    assert all(options["shell"] is False for _command, options in runner.calls)
    assert all(options["timeout"] == 20 for _command, options in runner.calls)
    assert all(options["capture_output"] is True for _command, options in runner.calls)
    assert all(options["text"] is True for _command, options in runner.calls)
    assert runner.calls[0][1]["env"] is None
    for _command, options in runner.calls[1:]:
        assert options["env"] == {"PATH": "inherited"}
    output = capsys.readouterr().out
    assert "podman diagnostic evidence" in output
    assert "hunter2" not in output
    assert "[REDACTED]" in output
    assert "[TRUNCATED" in output
    assert "RECENT-END" in output
    assert len(output) < 30_000


@pytest.mark.unit
def test_diagnostic_command_reports_failure_as_evidence_not_a_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def runner(arguments: list[str], **_kwargs: object) -> _Result:
        return _Result(17, stderr="diagnostic failed")

    completion = replay._collect_diagnostic_command(
        ["diagnostic", "status"], tmp_path, runner
    )

    assert completion is None
    assert "exit-code: 17" in capsys.readouterr().out


@pytest.mark.unit
def test_inspect_podman_rejects_all_user_arguments(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["inspect-podman", "--url", "unsafe"], tmp_path)


@pytest.mark.unit
def test_check_podman_api_pins_socket_cli_and_compose_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanApiRunner(socket_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "PATH": "inherited",
            **dict.fromkeys(DOCKER_SELECTOR_VARIABLES, "unsafe"),
        },
    )

    assert run_agent_replay(["check-podman-api"], tmp_path, runner=runner) == 0

    assert [command for command, _options in runner.calls] == [
        [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ],
        ["/opt/homebrew/bin/docker", "version"],
        ["/opt/homebrew/bin/docker", "info", "--format", "{{json .}}"],
        ["/opt/homebrew/bin/docker-compose", "version"],
        ["/opt/homebrew/bin/podman", "compose", "version"],
    ]
    for _command, options in runner.calls[1:]:
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment["DOCKER_HOST"] == f"unix://{socket_path}"
        assert environment["PODMAN_COMPOSE_PROVIDER"] == (
            "/opt/homebrew/bin/docker-compose"
        )
        assert set(environment).intersection(DOCKER_SELECTOR_VARIABLES) == {
            "DOCKER_HOST",
            "PODMAN_COMPOSE_PROVIDER",
        }
        assert environment["PATH"].startswith(f"{tmp_path}/.venv/bin:/opt/homebrew/bin")
        assert options["shell"] is False
        assert options["timeout"] == 20


@pytest.mark.unit
def test_activate_podman_context_creates_uses_and_verifies_exact_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(socket_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "PATH": "inherited",
            "DOCKER_HOST": "tcp://unsafe",
            "DOCKER_CONTEXT": "unsafe",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": "/unsafe",
        },
    )

    assert (
        run_agent_replay(["activate-podman-docker-context"], tmp_path, runner=runner)
        == 0
    )

    assert [command for command, _options in runner.calls] == [
        [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ],
        ["/opt/homebrew/bin/docker", "context", "show"],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "ls",
            "--format",
            "{{.Name}}",
        ],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "create",
            "ontoprism-podman",
            "--description",
            "OntoPrism rootless Podman machine",
            "--docker",
            f"host=unix://{socket_path}",
        ],
        ["/opt/homebrew/bin/docker", "context", "use", "ontoprism-podman"],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "inspect",
            "ontoprism-podman",
        ],
        ["/opt/homebrew/bin/docker", "context", "show"],
        ["/opt/homebrew/bin/docker", "version"],
        [
            "/opt/homebrew/bin/docker",
            "info",
            "--format",
            "{{json .}}",
        ],
    ]
    assert all(options["shell"] is False for _command, options in runner.calls)
    assert all(options["timeout"] == 20 for _command, options in runner.calls)
    assert runner.calls[0][1]["env"] is None
    for _command, options in runner.calls[1:]:
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment == {"PATH": "inherited"}
    output = capsys.readouterr().out
    assert "prior-docker-context=default" in output
    assert "active-docker-context=ontoprism-podman" in output
    assert f"podman-docker-endpoint=unix://{socket_path}" in output
    assert "docker-server=Podman" in output
    assert "podman-api-contract=rootless+containers-storage+apache-2.0" in output


@pytest.mark.unit
def test_activate_podman_context_updates_only_safe_exact_existing_context(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(
        socket_path,
        contexts=("default", "ontoprism-podman"),
    )

    assert (
        run_agent_replay(["activate-podman-docker-context"], tmp_path, runner=runner)
        == 0
    )

    commands = [command for command, _options in runner.calls]
    assert [
        "/opt/homebrew/bin/docker",
        "context",
        "update",
        "ontoprism-podman",
        "--description",
        "OntoPrism rootless Podman machine",
        "--docker",
        f"host=unix://{socket_path}",
    ] in commands
    assert not any("create" in command for command in commands)


@pytest.mark.unit
def test_activate_podman_context_refuses_unsafe_existing_context_before_mutation(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _UnsafeContext(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[-3:] == ["context", "inspect", "ontoprism-podman"]:
                payload = json.loads(result.stdout)
                payload[0]["Endpoints"]["kubernetes"] = {"Host": "unsafe"}
                result.stdout = json.dumps(payload)
            return result

    runner = _UnsafeContext(
        socket_path,
        contexts=("default", "ontoprism-podman"),
    )
    with pytest.raises(AgentReplayInputError, match="safe Docker context contract"):
        run_agent_replay(["activate-podman-docker-context"], tmp_path, runner=runner)
    assert not any(
        "update" in command or "use" in command for command, _options in runner.calls
    )


@pytest.mark.unit
def test_activate_podman_context_rejects_arguments_and_non_podman_server(
    tmp_path: Path,
) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["activate-podman-docker-context", "unsafe"], tmp_path)

    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _DockerServer(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["/opt/homebrew/bin/docker", "version"]:
                result.stdout = "Server: Docker Engine\n"
            return result

    with pytest.raises(AgentReplayInputError, match="Podman server predicate"):
        run_agent_replay(
            ["activate-podman-docker-context"],
            tmp_path,
            runner=_DockerServer(socket_path),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("Name", "decoy-vm"),
        ("State", "stopped"),
        ("Rootful", True),
    ],
)
def test_check_podman_api_rejects_wrong_machine_contract(
    changed_field: str, changed_value: object, tmp_path: Path
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _InvalidRunner(_PodmanApiRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[:3] == [
                "/opt/homebrew/bin/podman",
                "machine",
                "inspect",
            ]:
                payload = json.loads(result.stdout)
                payload[0][changed_field] = changed_value
                result.stdout = json.dumps(payload)
            return result

    with pytest.raises(AgentReplayInputError, match="machine contract"):
        run_agent_replay(
            ["check-podman-api"], tmp_path, runner=_InvalidRunner(socket_path)
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operation", "command"),
    [
        (
            "podman-test-integration",
            ["/opt/homebrew/bin/pdm", "run", "test-integration"],
        ),
        (
            "podman-test-full-store",
            ["/opt/homebrew/bin/pdm", "run", "test-integration-full-store"],
        ),
    ],
)
def test_podman_gate_operations_use_fixed_commands_and_controlled_runtime(
    operation: str,
    command: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanApiRunner(socket_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "SAFE_SETTING": "retained",
            "PATH": "unsafe",
            "ONTOPRISM_PODMAN_STACK_ENSURED": "1",
        },
    )

    assert run_agent_replay([operation], tmp_path, runner=runner) == 0

    assert runner.calls[-1][0] == command
    options = runner.calls[-1][1]
    assert options["shell"] is False
    assert options["timeout"] == 3600
    environment = options["env"]
    assert environment == {
        "SAFE_SETTING": "retained",
        "PATH": (
            f"{tmp_path}/.venv/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:unsafe"
        ),
        "DOCKER_HOST": f"unix://{socket_path}",
        "PODMAN_COMPOSE_PROVIDER": "/opt/homebrew/bin/docker-compose",
        "ONTOPRISM_PODMAN_STACK_ENSURED": "1",
    }


@pytest.mark.unit
def test_podman_verify_requires_selected_exact_context_and_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(socket_path)
    runner.current = "ontoprism-podman"
    monkeypatch.setattr(
        os,
        "environ",
        {
            "SAFE_SETTING": "retained",
            "PATH": "safe",
            "ONTOPRISM_PODMAN_STACK_ENSURED": "1",
        },
    )

    assert run_agent_replay(["podman-verify"], tmp_path, runner=runner) == 0

    assert [command for command, _options in runner.calls[-3:]] == [
        ["/opt/homebrew/bin/docker", "context", "show"],
        [
            "/opt/homebrew/bin/docker",
            "context",
            "inspect",
            "ontoprism-podman",
        ],
        ["/opt/homebrew/bin/pdm", "run", "verify"],
    ]
    gate_environment = runner.calls[-1][1]["env"]
    assert gate_environment == {
        "SAFE_SETTING": "retained",
        "PATH": "safe",
        "ONTOPRISM_PODMAN_STACK_ENSURED": "1",
    }


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["wrong-context", "wrong-endpoint"])
def test_podman_verify_refuses_non_podman_selected_context(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONTOPRISM_PODMAN_STACK_ENSURED", "1")
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _DockerContextRunner(socket_path)
    runner.current = "default" if failure == "wrong-context" else "ontoprism-podman"

    if failure == "wrong-endpoint":

        class _WrongEndpoint(_DockerContextRunner):
            def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
                result = super().__call__(arguments, **kwargs)
                if arguments[-3:] == ["context", "inspect", "ontoprism-podman"]:
                    payload = json.loads(result.stdout)
                    payload[0]["Endpoints"]["docker"]["Host"] = (
                        "unix:///tmp/podman/decoy-api.sock"
                    )
                    result.stdout = json.dumps(payload)
                return result

        runner = _WrongEndpoint(socket_path, current="ontoprism-podman")

    with pytest.raises(
        AgentReplayInputError,
        match=r"active (Docker context|Podman endpoint)",
    ):
        run_agent_replay(["podman-verify"], tmp_path, runner=runner)
    assert ["/opt/homebrew/bin/pdm", "run", "verify"] not in [
        command for command, _options in runner.calls
    ]


@pytest.mark.unit
def test_podman_gate_operations_reject_all_user_arguments(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["podman-verify", "--skip", "tests"], tmp_path)


@pytest.mark.unit
def test_podman_gate_failure_reports_labelled_stdout_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTOPRISM_PODMAN_STACK_ENSURED", "1")
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _FailedGate(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == ["/opt/homebrew/bin/pdm", "run", "verify"]:
                return _Result(
                    1,
                    stdout='{"api_token":"tests-secret"}',
                    stderr='lint failed PASSWORD="lint-secret"',
                )
            return result

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-verify"],
            tmp_path,
            runner=_FailedGate(socket_path, current="ontoprism-podman"),
        )

    message = str(raised.value)
    assert (
        "required command exited nonzero (1): "
        "/opt/homebrew/bin/pdm run verify" in message
    )
    assert 'stdout: {"api_token":"[REDACTED]"}' in message
    assert 'stderr: lint failed PASSWORD="[REDACTED]"' in message
    assert "tests-secret" not in message
    assert "lint-secret" not in message


@pytest.mark.unit
def test_podman_gate_timeout_names_command_and_preserves_sanitized_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTOPRISM_PODMAN_STACK_ENSURED", "1")
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _TimedOutGate(_DockerContextRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == ["/opt/homebrew/bin/pdm", "run", "verify"]:
                raise subprocess.TimeoutExpired(
                    arguments,
                    3600,
                    output='{"secret":"timeout-secret"}',
                    stderr="timed stderr",
                )
            return super().__call__(arguments, **kwargs)

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-verify"],
            tmp_path,
            runner=_TimedOutGate(socket_path, current="ontoprism-podman"),
        )

    message = str(raised.value)
    assert (
        "required command timed out after 3600s: "
        "/opt/homebrew/bin/pdm run verify" in message
    )
    assert 'stdout: {"secret":"[REDACTED]"}' in message
    assert "stderr: timed stderr" in message
    assert "timeout-secret" not in message


@pytest.mark.unit
def test_podman_compose_up_uses_exact_project_files_provider_and_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in ("docker-compose.yml", "docker-compose.app.yml"):
        (tmp_path / relative).touch()
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanApiRunner(socket_path)
    reserved: list[int] = []

    class _AvailableSocket:
        def bind(self, address: tuple[str, int]) -> None:
            reserved.append(address[1])

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _AvailableSocket())

    assert run_agent_replay(["podman-compose-up"], tmp_path, runner=runner) == 0

    compose = [
        "/opt/homebrew/bin/docker-compose",
        "--project-name",
        "ontoprism-podman-poc",
        "--file",
        str(tmp_path / "docker-compose.yml"),
    ]
    assert [call[0] for call in runner.calls[-2:]] == [
        [*compose, "config"],
        [*compose, "up", "--detach", "--wait"],
    ]
    assert reserved == [5433, 7888, 7889]
    for _command, options in runner.calls[-2:]:
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment["DOCKER_HOST"] == f"unix://{socket_path}"
        assert environment["PODMAN_COMPOSE_PROVIDER"] == (
            "/opt/homebrew/bin/docker-compose"
        )
        assert options["shell"] is False
        assert options["timeout"] == 1800


@pytest.mark.unit
def test_port_preflight_names_the_port_and_operating_system_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_compose_inputs(tmp_path)

    class _OccupiedSocket:
        def bind(self, address: tuple[str, int]) -> None:
            raise OSError(48, "Address already in use")

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _OccupiedSocket())
    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-compose-up"],
            tmp_path,
            runner=_PodmanApiRunner(tmp_path / "podman/ontoprism-vm-api.sock"),
        )

    assert "port 5433" in str(raised.value)
    assert "Address already in use" in str(raised.value)


class _ComposeCheckRunner(_PodmanApiRunner):
    def __init__(self, socket_path: Path, *, health: str = "healthy") -> None:
        super().__init__(socket_path)
        self.health = health

    def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
        if arguments[:2] == ["/opt/homebrew/bin/docker", "ps"]:
            self.calls.append((arguments, kwargs))
            return _Result(0, stdout="postgres\nqlever-ncit\nqlever-uberon\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "volume",
            "inspect",
            "ontoprism-podman-poc_ontoprism_pg_data",
        ]:
            self.calls.append((arguments, kwargs))
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-podman-poc_ontoprism_pg_data",
                            "Labels": {
                                "com.docker.compose.project": "ontoprism-podman-poc",
                                "com.docker.compose.volume": "ontoprism_pg_data",
                            },
                        }
                    ]
                ),
            )
        if arguments[:2] == ["/opt/homebrew/bin/docker", "inspect"]:
            name = arguments[2]
            service = name.removeprefix("ontoprism-")
            destination = (
                "/var/lib/postgresql/data" if service == "postgres" else "/data"
            )
            source = (
                "ontoprism-podman-poc_ontoprism_pg_data"
                if service == "postgres"
                else str(self.socket_path.parents[1] / f"data/{service}")
            )
            target_port = "5432/tcp" if service == "postgres" else "7001/tcp"
            host_port = {
                "postgres": "5433",
                "qlever-ncit": "7888",
                "qlever-uberon": "7889",
            }[service]
            labels = {
                "com.docker.compose.project": "ontoprism-podman-poc",
                "com.docker.compose.service": service,
            }
            self.calls.append((arguments, kwargs))
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Id": "a" * 64,
                            "Config": {"Labels": labels},
                            "State": {"Health": {"Status": self.health}},
                            "Mounts": [
                                {
                                    "Type": (
                                        "volume" if service == "postgres" else "bind"
                                    ),
                                    "Name": source if service == "postgres" else "",
                                    "Source": source,
                                    "Destination": destination,
                                }
                            ],
                            "NetworkSettings": {
                                "Ports": {
                                    target_port: [
                                        {"HostIp": "127.0.0.1", "HostPort": host_port}
                                    ]
                                }
                            },
                            "LargeRuntimeMetadata": "x" * 20_000,
                        }
                    ]
                ),
            )
        return super().__call__(arguments, **kwargs)


class _PodmanRecoveryRunner(_ComposeCheckRunner):
    def __init__(
        self,
        socket_path: Path,
        *,
        machine_state: str = "running",
        stale: bool = False,
        stack_state: str = "healthy",
        restart_succeeds: bool = True,
        machine_command_seconds: int = 0,
        stop_outlives_timeout: bool = False,
        inspections_until_stopped: int | None = None,
    ) -> None:
        super().__init__(socket_path)
        self.machine_state = machine_state
        self.stale = stale
        self.stack_state = stack_state
        self.restart_succeeds = restart_succeeds
        # How long `podman machine stop/start` runs; the caller's timeout must cover it.
        self.machine_command_seconds = machine_command_seconds
        # The CLI is killed at its timeout while the guest keeps powering off; the
        # machine then reports `stopped` after this many more inspections (or never).
        self.stop_outlives_timeout = stop_outlives_timeout
        self.inspections_until_stopped = inspections_until_stopped
        self.before_machine_stop: Callable[[], None] | None = None
        self.state_after_stop_timeout: str | None = None
        self.inspect_fails_after_stop_timeout = False
        self.machine_name = "ontoprism-vm"
        self._stop_timed_out = False
        self.contexts = ("default", "ontoprism-podman")
        self.current = "ontoprism-podman"

    def __call__(  # noqa: C901, PLR0911, PLR0912 - fixed recovery CLI fake
        self, arguments: list[str], **kwargs: object
    ) -> _Result:
        self.calls.append((arguments, kwargs))
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "inspect",
            "ontoprism-vm",
        ]:
            if self._stop_timed_out and self.inspect_fails_after_stop_timeout:
                return _Result(125, stderr="machine inspect: connection lost")
            if self.inspections_until_stopped is not None:
                self.inspections_until_stopped -= 1
                if self.inspections_until_stopped <= 0:
                    self.machine_state = "stopped"
                    self.inspections_until_stopped = None
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": self.machine_name,
                            "State": self.machine_state,
                            "Rootful": False,
                            "SSHConfig": {
                                "Port": 49969,
                                "RemoteUsername": "core",
                            },
                            "ConnectionInfo": {
                                "PodmanSocket": {"Path": str(self.socket_path)}
                            },
                        }
                    ]
                ),
            )
        if arguments == [
            "/opt/homebrew/bin/podman",
            "machine",
            "ssh",
            "ontoprism-vm",
            "true",
        ]:
            return _Result(1 if self.stale else 0, stderr="PASSWORD=hunter2")
        if arguments == [
            "/opt/homebrew/bin/podman",
            "system",
            "connection",
            "list",
            "--format",
            "json",
        ]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-vm",
                            "URI": "ssh://core@127.0.0.1:49969/run/user/501/podman/podman.sock",
                            "IsMachine": True,
                            "ReadWrite": True,
                        }
                    ]
                ),
            )
        if arguments[-3:] == ["machine", "stop", "ontoprism-vm"]:
            if self.before_machine_stop is not None:
                self.before_machine_stop()
            if self.stop_outlives_timeout:
                self._stop_timed_out = True
                if self.state_after_stop_timeout is not None:
                    self.machine_state = self.state_after_stop_timeout
                raise subprocess.TimeoutExpired(arguments, 300)
            self._run_machine_command(arguments, kwargs)
            self.machine_state = "stopped"
            return _Result(0)
        if arguments[-3:] == ["machine", "start", "ontoprism-vm"]:
            self._run_machine_command(arguments, kwargs)
            if not self.restart_succeeds:
                return _Result(1, stderr="start failed TOKEN=abc123")
            self.machine_state = "running"
            self.stale = False
            return _Result(0)
        if arguments == ["/opt/homebrew/bin/docker", "context", "show"]:
            return _Result(0, stdout=f"{self.current}\n")
        if arguments[1:4] == ["context", "ls", "--format"]:
            return _Result(0, stdout="default\nontoprism-podman\n")
        if arguments[-3:] == ["context", "inspect", "ontoprism-podman"]:
            return _Result(
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "ontoprism-podman",
                            "Metadata": {
                                "Description": "OntoPrism rootless Podman machine"
                            },
                            "Endpoints": {
                                "docker": {
                                    "Host": f"unix://{self.socket_path}",
                                    "SkipTLSVerify": False,
                                }
                            },
                        }
                    ]
                ),
            )
        if arguments[-3:] == ["context", "use", "ontoprism-podman"]:
            self.current = "ontoprism-podman"
            return _Result(0)
        if arguments == ["/opt/homebrew/bin/docker", "version"]:
            if self.stale:
                return _Result(1, stderr="API unavailable")
            return _Result(0, stdout="Server:\n Podman Engine:\n")
        if arguments == [
            "/opt/homebrew/bin/docker",
            "info",
            "--format",
            "{{json .}}",
        ]:
            if self.stale:
                return _Result(1, stderr="API unavailable")
            return _Result(
                0,
                stdout=json.dumps(
                    {
                        "OSType": "linux",
                        "ServerVersion": "6.1.0",
                        "DockerRootDir": "/home/core/.local/share/containers/storage",
                        "SecurityOptions": ["name=rootless"],
                        "ProductLicense": "Apache-2.0",
                    }
                ),
            )
        if arguments[:2] == ["/opt/homebrew/bin/docker", "ps"]:
            if self.stack_state == "absent":
                return _Result(0)
            if self.stack_state == "partial":
                return _Result(0, stdout="postgres\nqlever-ncit\n")
        if arguments[:2] == ["/opt/homebrew/bin/docker", "inspect"]:
            service = arguments[2].removeprefix("ontoprism-")
            if self.stack_state == "absent" or (
                self.stack_state == "partial" and service == "qlever-uberon"
            ):
                return _Result(1, stderr="no such object")
        if arguments[-3:] == ["up", "--detach", "--wait"]:
            self.stack_state = "healthy"
            return _Result(0)
        self.calls.pop()
        return super().__call__(arguments, **kwargs)

    def _run_machine_command(
        self, arguments: list[str], kwargs: dict[str, object]
    ) -> None:
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        if timeout < self.machine_command_seconds:
            raise subprocess.TimeoutExpired(arguments, timeout)


@pytest.mark.unit
def test_ensure_podman_stack_outlasts_a_guest_shutdown_and_boot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """On 2026-09-18 a half-dead VM (gvproxy gone, vfkit alive) needed well over
    the 20 s diagnostic timeout to power off, so the one sanctioned recovery failed."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(socket_path, stale=True, machine_command_seconds=90)

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert "machine-action=restarted-stale" in capsys.readouterr().out


@pytest.mark.unit
def test_redaction_is_linear_on_a_long_word_run() -> None:
    """Real ``docker inspect`` output carries long hashes and base64; a key prefix
    that may start anywhere rescans such a run from every position (#369: about
    four seconds for one 20 000-character run, quadratic, on every command)."""
    text = "x" * 200_000 + " xPASSWORD=hunter2 ok"

    started = time.perf_counter()
    sanitized = replay._bounded_sanitized(text, limit=None)
    elapsed = time.perf_counter() - started

    assert sanitized.endswith(" xPASSWORD=[REDACTED] ok")
    assert elapsed < 2


@pytest.mark.unit
def test_a_quoted_key_after_a_word_character_is_still_redacted() -> None:
    """The match may not start inside a word run; it must still start at the quote
    that follows one, or `x"PASSWORD":"v"` leaks."""
    assert replay._bounded_sanitized('x"PASSWORD":"hunter2"') == (
        'x"PASSWORD":"[REDACTED]"'
    )


@pytest.mark.unit
@pytest.mark.parametrize("new_session", [True, False])
def test_the_real_runner_gives_a_command_its_own_session_on_request(
    new_session: bool, tmp_path: Path
) -> None:
    """The recovery double accepts any option; the runner that reaches the real
    ``podman`` must actually detach the machine's processes from the caller."""
    result = replay._subprocess_runner(
        [sys.executable, "-c", "import os; print(os.getsid(0) == os.getpid())"],
        cwd=tmp_path,
        shell=False,
        check=False,
        timeout=30,
        capture_output=True,
        text=True,
        start_new_session=new_session,
    )

    assert result.stdout.strip() == str(new_session)


def _exited_process_id() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pid_file", "observed"),
    [
        (_exited_process_id, True),
        (os.getpid, False),
        (lambda: 0, False),
        (lambda: -1, False),
        (lambda: 10**30, False),
        (lambda: "not-a-pid", False),
        (None, False),
    ],
)
def test_ensure_podman_stack_reports_a_dead_gvproxy_only_when_it_saw_one(
    pid_file: Callable[[], object] | None,
    observed: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The recurring stale state is gvproxy gone while vfkit runs on. The line states
    what was seen (a pid that names no process); a missing or unreadable pid file,
    or a live pid, is no observation and says nothing."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    pid_path = socket_path.parent / "gvproxy.pid"
    content = None if pid_file is None else pid_file()
    if content is not None:
        pid_path.write_text(f"{content}\n")
    runner = _PodmanRecoveryRunner(socket_path, stale=True)

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    output = capsys.readouterr().out
    expected = (
        f"stale-machine-cause=gvproxy pid {content} from {pid_path} names no process"
    )
    assert (expected in output) is observed
    assert ("stale-machine-cause=" in output) is observed
    assert "machine-action=restarted-stale" in output


@pytest.mark.unit
@pytest.mark.parametrize("gvproxy_died", [True, False])
def test_the_stale_diagnosis_is_visible_before_the_machine_stop_blocks(
    gvproxy_died: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop may block for minutes under a harness that kills the run at its own
    timeout; a diagnosis still sitting in a block buffer is lost with the process.
    Without a dead gvproxy there is no cause line to flush the diagnostic for it."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    if gvproxy_died:
        (socket_path.parent / "gvproxy.pid").write_text(f"{_exited_process_id()}\n")
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, write_through=False))
    written_before_stop: list[str] = []
    runner = _PodmanRecoveryRunner(socket_path, stale=True)
    runner.before_machine_stop = lambda: written_before_stop.append(
        raw.getvalue().decode()
    )

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    (written,) = written_before_stop
    assert "stale-machine-diagnostic=" in written
    assert ("stale-machine-cause=" in written) is gvproxy_died


@pytest.mark.unit
def test_a_stop_that_outlives_its_timeout_is_waited_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Killing the `machine stop` CLI does not stop the guest's shutdown (seen on
    2026-09-18); recovery waits for `stopped` instead of failing."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(
        socket_path,
        stale=True,
        stop_outlives_timeout=True,
        inspections_until_stopped=3,
    )

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    output = capsys.readouterr().out
    assert "machine-stop-outlived-timeout=300s" in output
    assert "machine-action=restarted-stale" in output


@pytest.mark.unit
def test_a_machine_that_never_stops_fails_after_a_bounded_wait(tmp_path: Path) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, stale=True, stop_outlives_timeout=True)

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    commands = [command for command, _options in runner.calls]
    inspect = ["/opt/homebrew/bin/podman", "machine", "inspect", "ontoprism-vm"]
    start = ["/opt/homebrew/bin/podman", "machine", "start", "ontoprism-vm"]
    assert "300s later the machine reports 'running'" in str(raised.value)
    assert "rerun" in str(raised.value)
    assert commands.count(["/bin/sleep", "10"]) == 30
    assert commands.count(inspect) == 31
    assert start not in commands


@pytest.mark.unit
def test_the_wait_for_a_stop_tolerates_a_state_in_between(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A machine being polled mid-shutdown is in transition by construction; the
    strict running-or-stopped contract belongs to the entry check, not to the poll."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(
        socket_path,
        stale=True,
        stop_outlives_timeout=True,
        inspections_until_stopped=3,
    )
    runner.state_after_stop_timeout = "unknown"

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert "machine-action=restarted-stale" in capsys.readouterr().out


@pytest.mark.unit
def test_a_failure_while_waiting_for_the_stop_says_what_it_waited_for(
    tmp_path: Path,
) -> None:
    """The wait can end on an inspect error; alone, that error hides that the
    machine stop had already timed out and the guest may still be shutting down."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, stale=True, stop_outlives_timeout=True)
    runner.inspect_fails_after_stop_timeout = True

    with pytest.raises(AgentReplayInputError, match="machine inspect") as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    assert any(
        "machine stop ontoprism-vm" in note and "timed out after 300s" in note
        for note in raised.value.__notes__
    )


@pytest.mark.unit
def test_a_wrong_machine_is_not_reported_as_a_state_problem(tmp_path: Path) -> None:
    """Rerunning cannot fix a payload that is not our machine; the named-state
    refusal is for our machine in an unexpected state only."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, machine_state="starting")
    runner.machine_name = "someone-elses-vm"

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    assert str(raised.value) == "invalid Podman machine contract"


@pytest.mark.unit
def test_an_unexpected_machine_state_is_named(tmp_path: Path) -> None:
    """An interrupted `machine start` can leave a transitional state; the refusal
    must say which, not only that the contract is invalid."""
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, machine_state="starting")

    with pytest.raises(AgentReplayInputError, match="state 'starting'"):
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)


@pytest.mark.unit
@pytest.mark.parametrize("machine_state", ["stopped", "running"])
def test_ensure_podman_stack_starts_the_machine_in_its_own_session(
    machine_state: str, tmp_path: Path
) -> None:
    """vfkit and gvproxy keep the process group of whatever ran `machine start`;
    a new session keeps a harness or terminal signal away from the VM. The machine
    timeout belongs to `machine stop`/`start` and to nothing else."""
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(
        socket_path, machine_state=machine_state, stale=machine_state == "running"
    )

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert [
        command for command, options in runner.calls if options.get("start_new_session")
    ] == [["/opt/homebrew/bin/podman", "machine", "start", "ontoprism-vm"]]
    assert {
        tuple(command[1:3])
        for command, options in runner.calls
        if options["timeout"] == 300
    } == (
        {("machine", "stop"), ("machine", "start")}
        if machine_state == "running"
        else {("machine", "start")}
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("machine_state", "stale", "expected_action"),
    [
        ("running", False, "machine-action=no-op"),
        ("stopped", False, "machine-action=started"),
        ("running", True, "machine-action=restarted-stale"),
    ],
)
def test_ensure_podman_stack_recovers_machine_once_and_reports_action(
    machine_state: str,
    stale: bool,
    expected_action: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(
        socket_path, machine_state=machine_state, stale=stale
    )

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    commands = [command for command, _options in runner.calls]
    assert commands.count(
        ["/opt/homebrew/bin/podman", "machine", "stop", "ontoprism-vm"]
    ) == int(stale)
    assert commands.count(
        ["/opt/homebrew/bin/podman", "machine", "start", "ontoprism-vm"]
    ) == int(stale or machine_state == "stopped")
    assert not any(
        forbidden in command
        for command in commands
        for forbidden in ("reset", "rm", "init", "set", "volume")
    )
    output = capsys.readouterr().out
    assert expected_action in output
    assert "active-docker-context=ontoprism-podman" in output
    assert "stack-health=healthy" in output


@pytest.mark.unit
def test_ensure_podman_stack_failed_restart_is_bounded_and_redacted(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _PodmanRecoveryRunner(socket_path, stale=True, restart_succeeds=False)

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)

    commands = [command for command, _options in runner.calls]
    assert (
        commands.count(["/opt/homebrew/bin/podman", "machine", "stop", "ontoprism-vm"])
        == 1
    )
    assert (
        commands.count(["/opt/homebrew/bin/podman", "machine", "start", "ontoprism-vm"])
        == 1
    )
    assert len(commands) < 10
    assert "abc123" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize("stack_state", ["absent", "partial"])
def test_ensure_podman_stack_starts_or_reconciles_owned_stack(
    stack_state: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    runner = _PodmanRecoveryRunner(socket_path, stack_state=stack_state)

    class _AvailableSocket:
        def bind(self, _address: tuple[str, int]) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _AvailableSocket())

    assert run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner) == 0

    assert any(
        command[-3:] == ["up", "--detach", "--wait"] for command, _ in runner.calls
    )
    assert "stack-action=started-or-reconciled" in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("owner", "project owner predicate failed"),
        ("port", "port binding predicate failed"),
        ("volume", "mount source failed"),
    ],
)
def test_ensure_podman_stack_refuses_uncertain_existing_resources(
    failure: str, message: str, tmp_path: Path
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    socket_path.parent.mkdir()
    socket_path.touch()

    class _Uncertain(_PodmanRecoveryRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == [
                "/opt/homebrew/bin/docker",
                "inspect",
                "ontoprism-postgres",
            ]:
                payload = json.loads(result.stdout)
                if failure == "owner":
                    payload[0]["Config"]["Labels"]["com.docker.compose.project"] = (
                        "decoy"
                    )
                elif failure == "port":
                    payload[0]["NetworkSettings"]["Ports"]["5432/tcp"] = [
                        {"HostIp": "0.0.0.0", "HostPort": "5433"}  # noqa: S104
                    ]
                else:
                    payload[0]["Mounts"][0]["Name"] = "decoy-volume"
                result.stdout = json.dumps(payload)
            return result

    runner = _Uncertain(socket_path, stack_state="partial")
    with pytest.raises(AgentReplayInputError, match=message):
        run_agent_replay(["ensure-podman-stack"], tmp_path, runner=runner)
    assert not any(
        command[-3:] == ["up", "--detach", "--wait"] for command, _ in runner.calls
    )


@pytest.mark.unit
def test_ensure_podman_stack_rejects_arguments(tmp_path: Path) -> None:
    with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
        run_agent_replay(["ensure-podman-stack", "unsafe"], tmp_path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("service", "source"),
    [
        ("postgres", "ontoprism-podman-poc_ontoprism_pg_data-backup"),
        ("qlever-ncit", "decoy/qlever-ncit"),
        ("qlever-uberon", "data/not-qlever-uberon"),
    ],
)
def test_podman_compose_check_rejects_mount_source_decoys(
    service: str, source: str, tmp_path: Path
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    decoy_source = source if service == "postgres" else str(tmp_path / source)

    class _MountDecoyRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == [
                "/opt/homebrew/bin/docker",
                "inspect",
                f"ontoprism-{service}",
            ]:
                payload = json.loads(result.stdout)
                payload[0]["Mounts"][0]["Source"] = decoy_source
                payload[0]["Mounts"][0]["Name"] = decoy_source
                result.stdout = json.dumps(payload)
            return result

    with pytest.raises(AgentReplayInputError, match=f"{service} mount source"):
        run_agent_replay(
            ["podman-compose-check"],
            tmp_path,
            runner=_MountDecoyRunner(socket_path),
        )


@pytest.mark.unit
def test_podman_compose_check_validates_health_labels_mounts_ports_and_dns(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _ComposeCheckRunner(socket_path)

    assert run_agent_replay(["podman-compose-check"], tmp_path, runner=runner) == 0

    assert [call[0] for call in runner.calls[-5:]] == [
        [
            "/opt/homebrew/bin/docker",
            "ps",
            "--all",
            "--filter",
            "label=com.docker.compose.project=ontoprism-podman-poc",
            "--format",
            '{{.Label "com.docker.compose.service"}}',
        ],
        ["/opt/homebrew/bin/docker", "inspect", "ontoprism-postgres"],
        ["/opt/homebrew/bin/docker", "inspect", "ontoprism-qlever-ncit"],
        ["/opt/homebrew/bin/docker", "inspect", "ontoprism-qlever-uberon"],
        [
            "/opt/homebrew/bin/docker",
            "exec",
            "ontoprism-postgres",
            "getent",
            "hosts",
            "qlever-ncit",
            "qlever-uberon",
        ],
    ]


@pytest.mark.unit
def test_podman_compose_check_rejects_broken_health(tmp_path: Path) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    with pytest.raises(AgentReplayInputError, match="postgres health predicate"):
        run_agent_replay(
            ["podman-compose-check"],
            tmp_path,
            runner=_ComposeCheckRunner(socket_path, health="unhealthy"),
        )


@pytest.mark.unit
def test_podman_compose_check_rejects_non_list_mounts_with_named_predicate(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _MalformedMountsRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments == [
                "/opt/homebrew/bin/docker",
                "inspect",
                "ontoprism-postgres",
            ]:
                payload = json.loads(result.stdout)
                payload[0]["Mounts"] = "not-a-list"
                result.stdout = json.dumps(payload)
            return result

    with pytest.raises(
        AgentReplayInputError, match="postgres mounts shape predicate failed"
    ):
        run_agent_replay(
            ["podman-compose-check"],
            tmp_path,
            runner=_MalformedMountsRunner(socket_path),
        )


@pytest.mark.unit
def test_podman_compose_check_rejects_extra_project_service(tmp_path: Path) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _ExtraServiceRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[:2] == ["/opt/homebrew/bin/docker", "ps"]:
                result.stdout += "decoy\n"
            return result

    with pytest.raises(AgentReplayInputError, match="service inventory predicate"):
        run_agent_replay(
            ["podman-compose-check"],
            tmp_path,
            runner=_ExtraServiceRunner(socket_path),
        )


@pytest.mark.unit
def test_podman_compose_down_checks_exact_ownership_before_scoped_cleanup(
    tmp_path: Path,
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    runner = _ComposeCheckRunner(socket_path)

    assert run_agent_replay(["podman-compose-down"], tmp_path, runner=runner) == 0

    assert runner.calls[-2][0] == [
        "/opt/homebrew/bin/docker-compose",
        "--project-name",
        "ontoprism-podman-poc",
        "--file",
        str(tmp_path / "docker-compose.yml"),
        "down",
    ]


@pytest.mark.unit
def test_podman_compose_down_preserves_a_decoy_with_wrong_owner_label(
    tmp_path: Path,
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _DecoyRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[:2] == ["/opt/homebrew/bin/docker", "inspect"]:
                payload = json.loads(result.stdout)
                payload[0]["Config"]["Labels"]["com.docker.compose.project"] = "decoy"
                result.stdout = json.dumps(payload)
            return result

    runner = _DecoyRunner(socket_path)
    with pytest.raises(AgentReplayInputError, match="cleanup ownership"):
        run_agent_replay(["podman-compose-down"], tmp_path, runner=runner)
    assert all(call[0][-1] != "down" for call in runner.calls)


@pytest.mark.unit
def test_podman_compose_down_accepts_partial_owned_stack_and_verifies_volume(
    tmp_path: Path,
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _PartialRunner(_ComposeCheckRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == [
                "/opt/homebrew/bin/docker",
                "inspect",
                "ontoprism-qlever-ncit",
            ]:
                self.calls.append((arguments, kwargs))
                return _Result(1, stderr="error: no such object: ontoprism-qlever-ncit")
            if arguments[:3] == ["/opt/homebrew/bin/docker", "volume", "inspect"]:
                self.calls.append((arguments, kwargs))
                return _Result(
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "Name": "ontoprism-podman-poc_ontoprism_pg_data",
                                "Labels": {
                                    "com.docker.compose.project": (
                                        "ontoprism-podman-poc"
                                    ),
                                    "com.docker.compose.volume": "ontoprism_pg_data",
                                },
                            }
                        ]
                    ),
                )
            return super().__call__(arguments, **kwargs)

    runner = _PartialRunner(socket_path)
    assert run_agent_replay(["podman-compose-down"], tmp_path, runner=runner) == 0
    assert runner.calls[-1][0] == [
        "/opt/homebrew/bin/docker",
        "volume",
        "inspect",
        "ontoprism-podman-poc_ontoprism_pg_data",
    ]


@pytest.mark.unit
def test_podman_compose_up_preserves_primary_failure_when_rollback_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_compose_inputs(tmp_path)
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _AvailableSocket:
        def bind(self, _address: tuple[str, int]) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _AvailableSocket())

    class _FailedRollback(_PodmanApiRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[-3:] == ["up", "--detach", "--wait"]:
                return _Result(1, stderr="primary up failure")
            if arguments[-1:] == ["down"]:
                return _Result(1, stderr="rollback down failure")
            return result

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-compose-up"], tmp_path, runner=_FailedRollback(socket_path)
        )

    assert "primary up failure" in str(raised.value)
    assert any("rollback down failure" in note for note in raised.value.__notes__)


@pytest.mark.unit
def test_main_prints_cleanup_notes_to_cli_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failure = AgentReplayInputError("primary operation failed")
    failure.add_note("cleanup failure: generated override could not be removed")

    def fail_replay(_arguments: list[str], _root: Path) -> int:
        raise failure

    monkeypatch.setattr(replay, "run_agent_replay", fail_replay)
    monkeypatch.setattr(replay.sys, "argv", ["run_agent_replay.py", "podman-app-smoke"])

    assert replay.main() == 2
    assert capsys.readouterr().err == (
        "primary operation failed\n"
        "cleanup failure: generated override could not be removed\n"
    )


@pytest.mark.unit
def test_structural_inspect_redaction_covers_env_keys_and_asyncpg_urls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _SecretInspect(_PodmanApiRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            if arguments == [
                "/opt/homebrew/bin/docker",
                "info",
                "--format",
                "{{json .}}",
            ]:
                return _Result(
                    0,
                    stdout=json.dumps(
                        {
                            "Config": {
                                "Env": [
                                    "POSTGRES_PASSWORD=hunter2",
                                    "DATABASE_URL=postgresql+asyncpg://user:swordfish@db/app",
                                ]
                            }
                        }
                    ),
                )
            return super().__call__(arguments, **kwargs)

    assert (
        run_agent_replay(
            ["check-podman-api"], tmp_path, runner=_SecretInspect(socket_path)
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "hunter2" not in output
    assert "swordfish" not in output
    assert output.count("[REDACTED]") >= 2


@pytest.mark.unit
def test_health_rejection_matches_raw_combined_streams_and_always_removes_paths(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"

    class _SplitHealthFailure(_PodmanApiRunner):
        def __call__(self, arguments: list[str], **kwargs: object) -> _Result:
            result = super().__call__(arguments, **kwargs)
            if arguments[-5:] == ["up", "--detach", "--wait", "--wait-timeout", "30"]:
                return _Result(
                    1,
                    stdout="ontoprism-podman-health-reject-broken-1",
                    stderr="container is unhealthy",
                )
            if arguments[-1:] == ["down"]:
                return _Result(1, stderr="cleanup failed")
            return result

    with pytest.raises(AgentReplayInputError) as raised:
        run_agent_replay(
            ["podman-health-reject"], tmp_path, runner=_SplitHealthFailure(socket_path)
        )

    assert "cleanup failed" in str(raised.value)
    assert not (tmp_path / "tmp/podman-poc/broken-health.override.yml").exists()
    assert not (tmp_path / "tmp/podman-poc/broken-health-postgres").exists()


@pytest.mark.unit
def test_health_rejection_removes_paths_when_override_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "podman/ontoprism-vm-api.sock"
    original_write_text = Path.write_text

    def failed_override_write(
        path: Path, data: str, *, encoding: str | None = None, errors: str | None = None
    ) -> int:
        if path.name == "broken-health.override.yml":
            raise OSError("injected override write failure")
        return original_write_text(path, data, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "write_text", failed_override_write)
    with pytest.raises(OSError, match="injected override write failure"):
        run_agent_replay(
            ["podman-health-reject"], tmp_path, runner=_PodmanApiRunner(socket_path)
        )

    assert not (tmp_path / "tmp/podman-poc/broken-health.override.yml").exists()
    assert not (tmp_path / "tmp/podman-poc/broken-health-postgres").exists()


@pytest.mark.unit
def test_app_smoke_preflights_8080_and_owned_primary_volume_before_writing_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_compose_inputs(tmp_path, app=True)

    class _OccupiedSocket:
        def bind(self, address: tuple[str, int]) -> None:
            if address[1] == 8080:
                raise OSError(48, "Address already in use")

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args: _OccupiedSocket())
    with pytest.raises(AgentReplayInputError, match="port 8080"):
        run_agent_replay(["podman-app-smoke"], tmp_path, runner=_Runner())

    assert not (tmp_path / "tmp/podman-poc/app-podman.override.yml").exists()


@pytest.mark.unit
def test_poc_acceptance_operations_are_fixed_and_reject_arguments(
    tmp_path: Path,
) -> None:
    for operation in ("podman-health-reject", "podman-app-smoke"):
        with pytest.raises(AgentReplayInputError, match="accepts no arguments"):
            run_agent_replay([operation, "unsafe"], tmp_path, runner=_Runner())
