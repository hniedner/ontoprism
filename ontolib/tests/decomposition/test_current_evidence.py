from __future__ import annotations

import asyncio
import copy
import datetime
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from scripts.research.current_evidence import (
    ActualPairCitation,
    CurrentComparison,
    CurrentConceptEvidence,
    CurrentConstituent,
    CurrentEngineEvidence,
    CurrentEvidenceValidationError,
    CurrentSourceOccurrence,
    HistoricalOraclePairCitation,
    PartitionDiagnosisEvidence,
    generate_current_evidence,
    validate_current_comparison,
)
from scripts.research.golden_review import (
    GoldenSetValidationError,
    evaluate_adjudication,
    load_adjudication,
)

from ontolib.decomposition.evaluation import PartitionDiagnosis
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    DefinitionGroup,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
    canonical_definition_fact_id,
    canonical_definition_group_id,
    canonical_source_occurrence_id,
)
from ontolib.decomposition.proposal_registry import load_proposal_registry
from ontolib.decomposition.provenance_models import (
    CompletedRunForEvidence,
    RunFingerprint,
    WorkItemOutcome,
)

_GOLDEN = Path(__file__).parent / "golden"
_ORACLE = _GOLDEN / "neoplasm-adjudicated.json"
_ROWS = _GOLDEN / "neoplasm-row-decisions.json"
_REGISTRY = _GOLDEN / "proposal-registry.json"
_MANIFEST = Path("samples/ncit-26.07d-m1-current-replay.json")


def _payload_identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fingerprint() -> RunFingerprint:
    manifest = json.loads(_MANIFEST.read_text())
    return RunFingerprint(
        schema_version=3,
        source_identity=manifest["source_identity"],
        branch=manifest["branch"],
        scope_root=manifest["scope_root"],
        scope_version=manifest["scope_version"],
        semantic_types=("Neoplastic Process",),
        worklist=tuple(item["code"] for item in manifest["concepts"]),
        sample_manifest_identity=_payload_identity(manifest),
        algorithm_version="decomposition-v3",
        config_version="nested-definition-v2",
        walker_max_depth=5,
        output_mode="file",
        load_mode="named-graph",
        emitted_at=datetime.datetime(2026, 8, 15, 12, tzinfo=datetime.UTC),
    )


class _Store:
    def __init__(self, artifact: Path) -> None:
        fingerprint = _fingerprint()
        representation = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.completed = CompletedRunForEvidence(
            run_id="current-run",
            ncit_version="26.07d",
            fingerprint=fingerprint,
            representation_identity=representation,
            publication_artifact_path=str(artifact.resolve()),
        )
        self.outcomes = [
            WorkItemOutcome(
                run_id="current-run",
                concept_code=code,
                ordinal=ordinal,
                state="complete",
                outcome="atomic-no-op",
                semantic_type="Neoplastic Process",
                semantic_types=("Neoplastic Process",),
                is_decomposed=False,
                is_residual=False,
                constituent_count=0,
                minted_count=0,
            )
            for ordinal, code in enumerate(fingerprint.worklist)
        ]
        self.decompositions = [_repeated_occurrence_decomposition()]
        self.outcomes[0] = self.outcomes[0].model_copy(
            update={
                "outcome": "decomposed",
                "is_decomposed": True,
                "constituent_count": 1,
            }
        )

    async def completed_run_for_evidence(self, run_id: str) -> CompletedRunForEvidence:
        assert run_id == "current-run"
        return self.completed

    async def work_item_outcomes(self, run_id: str) -> list[WorkItemOutcome]:
        assert run_id == "current-run"
        return self.outcomes

    async def decompositions_for_run(self, run_id: str) -> list[Decomposition]:
        assert run_id == "current-run"
        return self.decompositions


def _empty_artifact(path: Path) -> None:
    code = _fingerprint().worklist[0]
    triples = (
        f"<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{code}> "
        "<https://w3id.org/ontoprism/vocab#representationStatus> "
        '"legacy-precoordinated" ; '
        '<https://w3id.org/ontoprism/vocab#decomposedBy> "current-run" .'
    )
    path.write_text(triples + "\n")


def _repeated_occurrence_decomposition() -> Decomposition:
    code = _fingerprint().worklist[0]
    group_id = canonical_definition_group_id(code, ("restriction:R101:C12400",))
    fact_id = canonical_definition_fact_id(
        code, group_id, "restriction", "R101", "C12400"
    )
    occurrences = tuple(
        SourceDefinitionOccurrence(
            occurrence_id=canonical_source_occurrence_id(code, fact_id, (0, position)),
            root_code=code,
            source_fact_id=fact_id,
            source_group_id=group_id,
            anchor_code=code,
            depth=0,
            role_code="R101",
            filler_code="C12400",
            structural_path=(0, position),
            member_position=position,
        )
        for position in (0, 1)
    )
    return Decomposition(
        code=code,
        semantic_type="Neoplastic Process",
        constituents=(
            Constituent(
                axis="op:PrimarySite",
                filler_code="C12400",
                axis_source="role",
                source_role="R101",
                source_definition_ids=(fact_id,),
                source_occurrence_ids=tuple(
                    occurrence.occurrence_id for occurrence in occurrences
                ),
            ),
        ),
        complete_definition=CompleteDefinition(
            root_code=code,
            facts=(
                RestrictionDefinitionFact(
                    fact_id=fact_id,
                    anchor_code=code,
                    group_id=group_id,
                    depth=0,
                    role_code="R101",
                    filler_code="C12400",
                ),
            ),
            groups=(DefinitionGroup(group_id=group_id, anchor_code=code, depth=0),),
            root_group_ids=(group_id,),
            occurrences=occurrences,
        ),
    )


@pytest.mark.unit
def test_generate_current_evidence_binds_inputs_and_writes_both_outputs(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "current.ttl"
    engine_output = tmp_path / "engine.json"
    comparison_output = tmp_path / "comparison.json"
    _empty_artifact(artifact)

    asyncio.run(
        generate_current_evidence(
            sample_manifest=_MANIFEST,
            oracle=_ORACLE,
            row_decisions=_ROWS,
            proposal_registry=_REGISTRY,
            run_id="current-run",
            artifact=artifact,
            engine_output=engine_output,
            comparison_output=comparison_output,
            store=_Store(artifact),
        )
    )

    evidence = CurrentEngineEvidence.model_validate_json(engine_output.read_bytes())
    comparison = CurrentComparison.model_validate_json(comparison_output.read_bytes())
    assert evidence.source_identity == _fingerprint().source_identity
    assert evidence.run_fingerprint_identity == _fingerprint().identity
    assert (
        evidence.representation_identity
        == hashlib.sha256(artifact.read_bytes()).hexdigest()
    )
    assert comparison.current_evidence_identity == evidence.evidence_identity
    assert (
        comparison.oracle_identity
        == json.loads(_ORACLE.read_text())["artifact_identity"]
    )
    assert (
        comparison.row_decision_identity
        == json.loads(_ROWS.read_text())["payload_identity"]
    )
    assert (
        comparison.proposal_registry_identity
        == json.loads(_REGISTRY.read_text())["registry_identity"]
    )
    assert [concept.code for concept in evidence.concepts] == list(
        _fingerprint().worklist
    )
    assert [concept.code for concept in evidence.concepts if concept.constituents] == [
        _fingerprint().worklist[0]
    ]
    assert comparison.metrics.exact_pair_precision.rate == 0.0
    assert comparison.metrics.full_partition_agreement.denominator == 20
    assert comparison.concepts[0].full_partition.eligible is True
    assert comparison.concepts[0].common_pair_partition.ineligibility_reason in {
        "zero-shared-pairs",
        "one-shared-pair",
    }
    diagnosis = comparison.concepts[0].full_partition.primary_diagnosis
    assert diagnosis is not None
    assert diagnosis.normalization_rule == "group-label-independent-co-membership"
    assert diagnosis.actual_pair_citations[0].occurrence_ids == tuple(
        item.occurrence_id
        for item in evidence.concepts[0].constituents[0].source_occurrences
    )
    with pytest.raises(GoldenSetValidationError):
        evaluate_adjudication(
            load_adjudication(_ORACLE, load_proposal_registry(_REGISTRY)),
            json.loads(engine_output.read_text()),
            {},
        )


@pytest.mark.unit
def test_typed_comparison_models_reject_untyped_metric_and_partition_payloads(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "current.ttl"
    _empty_artifact(artifact)
    _evidence, comparison = asyncio.run(
        generate_current_evidence(
            sample_manifest=_MANIFEST,
            oracle=_ORACLE,
            row_decisions=_ROWS,
            proposal_registry=_REGISTRY,
            run_id="current-run",
            artifact=artifact,
            engine_output=tmp_path / "engine.json",
            comparison_output=tmp_path / "comparison.json",
            store=_Store(artifact),
        )
    )

    with pytest.raises(ValueError, match="metric rate"):
        comparison.metrics.exact_pair_precision.model_copy(
            update={"rate": 0.5}
        ).model_validate(
            comparison.metrics.exact_pair_precision.model_copy(
                update={"rate": 0.5}
            ).model_dump()
        )


@pytest.mark.unit
def test_partition_diagnosis_cites_actual_occurrences_without_observing_oracle() -> (
    None
):
    occurrence_id = "a" * 64
    diagnosis = PartitionDiagnosisEvidence(
        diagnosis=PartitionDiagnosis.OVER_MERGE,
        normalization_rule="group-label-independent-co-membership",
        affected_pairs=(("op:Morphology", "C1"), ("op:PrimarySite", "C2")),
        actual_pair_citations=(
            ActualPairCitation(
                pair=("op:PrimarySite", "C2"),
                occurrence_ids=(occurrence_id,),
            ),
        ),
        expected_pair_citations=(
            HistoricalOraclePairCitation(
                pair=("op:Morphology", "C1"),
                availability="unavailable-historical-oracle",
            ),
            HistoricalOraclePairCitation(
                pair=("op:PrimarySite", "C2"),
                availability="unavailable-historical-oracle",
            ),
        ),
    )

    assert diagnosis.actual_pair_citations[0].occurrence_ids == (occurrence_id,)
    assert {
        citation.availability for citation in diagnosis.expected_pair_citations
    } == {"unavailable-historical-oracle"}


@pytest.mark.unit
def test_generate_current_evidence_preserves_repeated_source_occurrences(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "current.ttl"
    _empty_artifact(artifact)
    store = _Store(artifact)
    decomposition = _repeated_occurrence_decomposition()
    store.decompositions = [decomposition]
    evidence, _comparison = asyncio.run(
        generate_current_evidence(
            sample_manifest=_MANIFEST,
            oracle=_ORACLE,
            row_decisions=_ROWS,
            proposal_registry=_REGISTRY,
            run_id="current-run",
            artifact=artifact,
            engine_output=tmp_path / "engine.json",
            comparison_output=tmp_path / "comparison.json",
            store=store,
        )
    )

    citations = evidence.concepts[0].constituents[0].source_occurrences
    assert len(citations) == 2
    assert {item.member_position for item in citations} == {0, 1}
    assert {item.root_code for item in citations} == {decomposition.code}
    assert evidence.concepts[0].all_source_occurrences == citations
    assert evidence.concepts[0].constituents[0].source_occurrence_ids == tuple(
        item.occurrence_id for item in citations
    )


@pytest.mark.unit
def test_current_concept_rejects_selected_occurrence_outside_complete_definition() -> (
    None
):
    occurrence = CurrentSourceOccurrence.model_validate(
        asdict(_repeated_occurrence_decomposition().complete_definition.occurrences[0])  # type: ignore[union-attr]
    )
    with pytest.raises(ValueError, match="selected source occurrences"):
        CurrentConceptEvidence(
            code="C1",
            outcome="decomposed",
            semantic_types=("Neoplastic Process",),
            all_source_occurrences=(),
            constituents=(
                CurrentConstituent(
                    axis="op:PrimarySite",
                    filler="C12400",
                    relationship_group=None,
                    needs_review=False,
                    source_occurrence_ids=(occurrence.occurrence_id,),
                    source_occurrences=(occurrence,),
                ),
            ),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_identity", "f" * 64, "source identity"),
        ("sample_manifest_identity", "f" * 64, "manifest identity"),
        ("worklist", tuple(reversed(_fingerprint().worklist)), "worklist"),
        ("branch", "disease", "branch"),
        ("scope_version", "inferred-subclass-v1", "scope"),
    ],
)
def test_generate_current_evidence_rejects_fingerprint_drift(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    artifact = tmp_path / "current.ttl"
    _empty_artifact(artifact)
    store = _Store(artifact)
    store.completed = store.completed.model_copy(
        update={
            "fingerprint": store.completed.fingerprint.model_copy(update={field: value})
        }
    )

    with pytest.raises(CurrentEvidenceValidationError, match=message):
        asyncio.run(
            generate_current_evidence(
                sample_manifest=_MANIFEST,
                oracle=_ORACLE,
                row_decisions=_ROWS,
                proposal_registry=_REGISTRY,
                run_id="current-run",
                artifact=artifact,
                engine_output=tmp_path / "engine.json",
                comparison_output=tmp_path / "comparison.json",
                store=store,
            )
        )


@pytest.mark.unit
def test_generate_current_evidence_rejects_artifact_and_representation_drift(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "current.ttl"
    _empty_artifact(artifact)
    store = _Store(artifact)
    store.completed = store.completed.model_copy(
        update={"publication_artifact_path": str(tmp_path / "other.ttl")}
    )
    with pytest.raises(CurrentEvidenceValidationError, match="artifact path"):
        asyncio.run(
            generate_current_evidence(
                sample_manifest=_MANIFEST,
                oracle=_ORACLE,
                row_decisions=_ROWS,
                proposal_registry=_REGISTRY,
                run_id="current-run",
                artifact=artifact,
                engine_output=tmp_path / "engine.json",
                comparison_output=tmp_path / "comparison.json",
                store=store,
            )
        )

    store = _Store(artifact)
    store.completed = store.completed.model_copy(
        update={"representation_identity": "f" * 64}
    )
    with pytest.raises(CurrentEvidenceValidationError, match="representation identity"):
        asyncio.run(
            generate_current_evidence(
                sample_manifest=_MANIFEST,
                oracle=_ORACLE,
                row_decisions=_ROWS,
                proposal_registry=_REGISTRY,
                run_id="current-run",
                artifact=artifact,
                engine_output=tmp_path / "engine-2.json",
                comparison_output=tmp_path / "comparison-2.json",
                store=store,
            )
        )


@pytest.mark.unit
def test_generate_current_evidence_rejects_returned_run_id_drift(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "current.ttl"
    _empty_artifact(artifact)
    store = _Store(artifact)
    store.completed = store.completed.model_copy(update={"run_id": "other-run"})

    with pytest.raises(CurrentEvidenceValidationError, match="run id"):
        asyncio.run(
            generate_current_evidence(
                sample_manifest=_MANIFEST,
                oracle=_ORACLE,
                row_decisions=_ROWS,
                proposal_registry=_REGISTRY,
                run_id="current-run",
                artifact=artifact,
                engine_output=tmp_path / "engine.json",
                comparison_output=tmp_path / "comparison.json",
                store=store,
            )
        )


@pytest.mark.unit
def test_generate_current_evidence_rejects_an_output_that_aliases_an_input(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "current.ttl"
    _empty_artifact(artifact)

    with pytest.raises(CurrentEvidenceValidationError, match="every input"):
        asyncio.run(
            generate_current_evidence(
                sample_manifest=_MANIFEST,
                oracle=_ORACLE,
                row_decisions=_ROWS,
                proposal_registry=_REGISTRY,
                run_id="current-run",
                artifact=artifact,
                engine_output=artifact,
                comparison_output=tmp_path / "comparison.json",
                store=_Store(artifact),
            )
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "model",
    [CurrentEngineEvidence, CurrentComparison],
)
def test_current_output_models_reject_self_identity_drift(model: type[object]) -> None:
    raw = {
        "schema_version": 2,
        "ncit_version": "26.07d",
        "source_identity": "a" * 64,
        "sample_manifest_identity": "b" * 64,
        "run_id": "run",
        "run_fingerprint_identity": "c" * 64,
        "artifact_identity": "d" * 64,
        "representation_identity": "d" * 64,
        "detector_identity": "e" * 64,
        "oracle_identity": "f" * 64,
        "row_decision_identity": "1" * 64,
        "proposal_registry_identity": "2" * 64,
        "concepts": (),
        "evidence_identity": "0" * 64,
    }
    if model is CurrentComparison:
        raw.pop("concepts")
        raw.pop("evidence_identity")
        raw["current_evidence_identity"] = "3" * 64
        raw["metrics"] = {
            "exact_pair_precision": {
                "numerator": 0,
                "denominator": 0,
                "rate": 0.0,
            },
            "exact_pair_recall": {
                "numerator": 0,
                "denominator": 0,
                "rate": 0.0,
            },
            "full_partition_agreement": {
                "numerator": 0,
                "denominator": 0,
                "rate": 0.0,
            },
            "common_pair_partition_agreement": {
                "numerator": 0,
                "denominator": 0,
                "rate": 0.0,
                "ineligible": 0,
            },
        }
        raw["concepts"] = ()
        raw["comparison_identity"] = "0" * 64
    with pytest.raises(ValueError, match="identity"):
        model.model_validate(copy.deepcopy(raw))  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("ncit_version", "release"),
        ("source_identity", "source"),
        ("sample_manifest_identity", "manifest"),
        ("run_id", "run"),
        ("run_fingerprint_identity", "fingerprint"),
        ("artifact_identity", "artifact"),
        ("representation_identity", "representation"),
        ("detector_identity", "detector"),
        ("oracle_identity", "oracle"),
        ("row_decision_identity", "row decision"),
        ("proposal_registry_identity", "proposal registry"),
        ("current_evidence_identity", "evidence"),
    ],
)
def test_current_comparator_rejects_each_identity_drift(
    tmp_path: Path, field: str, message: str
) -> None:
    artifact = tmp_path / "current.ttl"
    _empty_artifact(artifact)
    evidence, comparison = asyncio.run(
        generate_current_evidence(
            sample_manifest=_MANIFEST,
            oracle=_ORACLE,
            row_decisions=_ROWS,
            proposal_registry=_REGISTRY,
            run_id="current-run",
            artifact=artifact,
            engine_output=tmp_path / "engine.json",
            comparison_output=tmp_path / "comparison.json",
            store=_Store(artifact),
        )
    )
    drifted = comparison.model_copy(
        update={
            field: ("different" if field in {"run_id", "ncit_version"} else "f" * 64)
        }
    )

    with pytest.raises(CurrentEvidenceValidationError, match=message):
        validate_current_comparison(evidence, drifted)


@pytest.mark.unit
def test_generate_current_evidence_rolls_back_both_outputs_on_second_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "current.ttl"
    engine_output = tmp_path / "engine.json"
    comparison_output = tmp_path / "comparison.json"
    _empty_artifact(artifact)
    engine_output.write_bytes(b"original engine\n")
    comparison_output.write_bytes(b"original comparison\n")
    original_replace = os.replace
    replacements = 0

    def fail_second_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("injected second replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr(
        "scripts.research.current_evidence.os.replace", fail_second_replace
    )

    with pytest.raises(OSError, match="injected second replacement failure"):
        asyncio.run(
            generate_current_evidence(
                sample_manifest=_MANIFEST,
                oracle=_ORACLE,
                row_decisions=_ROWS,
                proposal_registry=_REGISTRY,
                run_id="current-run",
                artifact=artifact,
                engine_output=engine_output,
                comparison_output=comparison_output,
                store=_Store(artifact),
            )
        )

    assert engine_output.read_bytes() == b"original engine\n"
    assert comparison_output.read_bytes() == b"original comparison\n"
