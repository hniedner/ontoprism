from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import pytest
from scripts.adjudication import _parser

from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.corpus_baseline import (
    CorpusBaselineValidationError,
    corpus_baseline_identity,
    generate_corpus_baseline,
    load_corpus_baseline,
)
from ontolib.decomposition.provenance import RunStateError
from ontolib.decomposition.provenance_models import (
    CompletedRunForEvidence,
    CorpusBaselineAggregate,
    RunFingerprint,
)


def _fingerprint(**changes: object) -> RunFingerprint:
    values: dict[str, object] = {
        "source_identity": "a" * 64,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": branch_spec(DecompositionBranch.NEOPLASM).semantic_types,
        "worklist": ("C1", "C2", "C3", "C4", "C5"),
        "total_limit": None,
        "sample_manifest_identity": None,
        "algorithm_version": "decomposition-v3",
        "config_version": "nested-definition-v2",
        "walker_max_depth": 5,
        "output_mode": "file",
        "load_mode": "named-graph",
        "emitted_at": datetime.datetime(2026, 8, 15, tzinfo=datetime.UTC),
    }
    values.update(changes)
    return RunFingerprint.model_validate(values)


def _aggregate() -> CorpusBaselineAggregate:
    return CorpusBaselineAggregate.model_validate(
        {
            "worklist_count": 5,
            "outcome_counts": {
                "decomposed": 2,
                "residual": 1,
                "semantic_excluded": 1,
                "atomic_noop": 1,
                "unknown": 0,
            },
            "decomposed_codes": ("C1", "C2"),
            "emitted_constituent_pair_count": 4,
            "complete_semantic_fact_count": 9,
            "source_occurrence_count": 7,
            "selected_occurrence_count": 6,
            "minted_count": 1,
        }
    )


class _Store:
    def __init__(self, run: CompletedRunForEvidence) -> None:
        self.run = run

    async def completed_run_for_evidence(self, run_id: str) -> CompletedRunForEvidence:
        assert run_id == self.run.run_id
        return self.run

    async def corpus_baseline_aggregate(self, run_id: str) -> CorpusBaselineAggregate:
        assert run_id == self.run.run_id
        return _aggregate()


class _UnpublishedStore(_Store):
    async def completed_run_for_evidence(self, run_id: str) -> CompletedRunForEvidence:
        raise RunStateError(
            f"decomposition run {run_id!r} is not complete and published"
        )


def _artifact(path: Path, run_id: str, codes: tuple[str, ...] = ("C1", "C2")) -> str:
    path.write_text(
        "".join(
            f"<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{code}> "
            "<https://w3id.org/ontoprism/vocab#representationStatus> "
            '"legacy-precoordinated" ; '
            "<https://w3id.org/ontoprism/vocab#decomposedBy> "
            f'"{run_id}" .\n'
            for code in codes
        )
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.unit
async def test_generate_full_corpus_baseline_binds_every_observed_value(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "run.ttl"
    identity = _artifact(artifact, "full-run")
    run = CompletedRunForEvidence(
        run_id="full-run",
        ncit_version="26.07d",
        fingerprint=_fingerprint(),
        representation_identity=identity,
        publication_artifact_path=str(artifact),
    )

    baseline = await generate_corpus_baseline(
        run_id="full-run",
        artifact=artifact,
        store=_Store(run),
    )

    assert baseline.source_identity == "a" * 64
    assert baseline.ontology_release == "26.07d"
    assert baseline.run_fingerprint_identity == run.fingerprint.identity
    assert baseline.detector_identity != baseline.run_fingerprint_identity
    assert baseline.artifact_identity == identity
    assert baseline.representation_identity == identity
    assert baseline.outcome_counts.decomposed == 2
    assert baseline.outcome_counts.residual == 1
    assert baseline.outcome_counts.semantic_excluded == 1
    assert baseline.outcome_counts.atomic_noop == 1
    assert baseline.outcome_counts.unknown == 0
    assert baseline.emitted_constituent_pair_count == 4
    assert baseline.complete_semantic_fact_count == 9
    assert baseline.source_occurrence_count == 7
    assert baseline.selected_occurrence_count == 6
    assert baseline.minted_count == 1
    assert baseline.baseline_identity == corpus_baseline_identity(baseline)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sample_manifest_identity": "b" * 64, "schema_version": 3}, "sample"),
        ({"total_limit": 5}, "total limit"),
        ({"branch": "disease", "scope_root": "C2991"}, "neoplasm"),
        ({"algorithm_version": "decomposition-v2"}, "algorithm"),
        ({"semantic_types": ("Disease or Syndrome",)}, "semantic types"),
    ],
)
async def test_generate_full_corpus_baseline_refuses_non_production_fingerprint(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    artifact = tmp_path / "run.ttl"
    identity = _artifact(artifact, "full-run")
    run = CompletedRunForEvidence(
        run_id="full-run",
        ncit_version="26.07d",
        fingerprint=_fingerprint(**changes),
        representation_identity=identity,
        publication_artifact_path=str(artifact),
    )

    with pytest.raises(CorpusBaselineValidationError, match=message):
        await generate_corpus_baseline(
            run_id="full-run", artifact=artifact, store=_Store(run)
        )


@pytest.mark.unit
async def test_generate_full_corpus_baseline_refuses_turtle_membership_drift(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "run.ttl"
    identity = _artifact(artifact, "full-run", ("C1",))
    run = CompletedRunForEvidence(
        run_id="full-run",
        ncit_version="26.07d",
        fingerprint=_fingerprint(),
        representation_identity=identity,
        publication_artifact_path=str(artifact),
    )

    with pytest.raises(CorpusBaselineValidationError, match="expected concept set"):
        await generate_corpus_baseline(
            run_id="full-run", artifact=artifact, store=_Store(run)
        )


@pytest.mark.unit
async def test_generate_full_corpus_baseline_refuses_representation_drift(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "run.ttl"
    _artifact(artifact, "full-run")
    artifact.write_text(
        artifact.read_text()
        + "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        '<https://w3id.org/ontoprism/vocab#representationStatus> "other" .\n'
    )
    identity = hashlib.sha256(artifact.read_bytes()).hexdigest()
    run = CompletedRunForEvidence(
        run_id="full-run",
        ncit_version="26.07d",
        fingerprint=_fingerprint(),
        representation_identity=identity,
        publication_artifact_path=str(artifact),
    )

    with pytest.raises(CorpusBaselineValidationError, match="representation status"):
        await generate_corpus_baseline(
            run_id="full-run", artifact=artifact, store=_Store(run)
        )


@pytest.mark.unit
async def test_generate_full_corpus_baseline_refuses_malformed_aggregate(
    tmp_path: Path,
) -> None:
    aggregate = _aggregate()
    with pytest.raises(ValueError, match="outcome counts"):
        CorpusBaselineAggregate.model_validate(
            {**aggregate.model_dump(), "worklist_count": 4}
        )


@pytest.mark.unit
async def test_generate_full_corpus_baseline_refuses_unpublished_run(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "run.ttl"
    identity = _artifact(artifact, "full-run")
    run = CompletedRunForEvidence(
        run_id="full-run",
        ncit_version="26.07d",
        fingerprint=_fingerprint(),
        representation_identity=identity,
        publication_artifact_path=str(artifact),
    )

    with pytest.raises(RunStateError, match="not complete and published"):
        await generate_corpus_baseline(
            run_id="full-run", artifact=artifact, store=_UnpublishedStore(run)
        )


@pytest.mark.unit
async def test_generate_full_corpus_baseline_refuses_candidate_source_drift(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "run.ttl"
    identity = _artifact(artifact, "full-run")
    run = CompletedRunForEvidence(
        run_id="full-run",
        ncit_version="26.07d",
        fingerprint=_fingerprint(),
        representation_identity=identity,
        publication_artifact_path=str(artifact),
    )

    with pytest.raises(CorpusBaselineValidationError, match="candidate source"):
        await generate_corpus_baseline(
            run_id="full-run",
            artifact=artifact,
            store=_Store(run),
            expected_source_identity="f" * 64,
            expected_release="26.07d",
        )


@pytest.mark.unit
def test_load_corpus_baseline_rejects_payload_drift(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    payload = {
        "schema_version": 1,
        "run_id": "full-run",
        "source_identity": "a" * 64,
        "ontology_release": "26.07d",
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "run_fingerprint_identity": "b" * 64,
        "representation_identity": "c" * 64,
        "artifact_identity": "c" * 64,
        "detector_identity": "d" * 64,
        "worklist_count": 1,
        "outcome_counts": {
            "decomposed": 1,
            "residual": 0,
            "semantic_excluded": 0,
            "atomic_noop": 0,
            "unknown": 0,
        },
        "emitted_constituent_pair_count": 1,
        "complete_semantic_fact_count": 1,
        "source_occurrence_count": 1,
        "selected_occurrence_count": 1,
        "minted_count": 0,
    }
    payload["baseline_identity"] = corpus_baseline_identity(payload)
    payload["minted_count"] = 1
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="baseline identity"):
        load_corpus_baseline(path)


@pytest.mark.unit
def test_generate_corpus_baseline_cli_requires_explicit_inputs() -> None:
    args = _parser().parse_args(
        [
            "generate-corpus-baseline",
            "--source-manifest",
            "candidate.json",
            "--run-id",
            "full-run",
            "--artifact",
            "run.ttl",
            "--output",
            "baseline.json",
        ]
    )

    assert args.command == "generate-corpus-baseline"
    assert args.source_manifest == Path("candidate.json")
    assert args.run_id == "full-run"
    assert args.artifact == Path("run.ttl")
    assert args.output == Path("baseline.json")
