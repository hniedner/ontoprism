from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ontolib.decomposition.corpus_baseline import (
    CorpusBaseline,
    corpus_baseline_identity,
)
from ontolib.decomposition.provenance_models import RUN_STAGE_SEQUENCE_IDENTITY
from ontolib.decomposition.r101_comparator import (
    ComparatorRun,
    CurrentV5ComparatorFingerprint,
    HistoricalV4ComparatorFingerprint,
    R101ComparatorQualification,
    R101ComparatorValidationError,
    qualify_r101_comparator,
    write_r101_comparator_qualification,
)
from ontolib.decomposition.r101_conservation import r101_ledger_query_identity


def _fingerprint(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 4,
        "source_identity": "a" * 64,
        "collapse_policy_identity": "b" * 64,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": (
            "Cell or Molecular Dysfunction",
            "Disease or Syndrome",
            "Neoplastic Process",
        ),
        "worklist": ("C1", "C2"),
        "total_limit": None,
        "sample_manifest_identity": None,
        "algorithm_version": "decomposition-v4",
        "config_version": "nested-definition-v2",
        "walker_max_depth": 7,
        "output_mode": "file",
        "load_mode": "none",
        "emitted_at": datetime.datetime(2026, 9, 8, tzinfo=datetime.UTC),
    }
    payload.update(changes)
    return payload


def _identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=lambda value: value.isoformat().replace("+00:00", "Z"),
        ).encode()
    ).hexdigest()


@pytest.mark.unit
def test_comparator_fingerprint_variants_are_closed_and_hash_the_discriminator() -> (
    None
):
    historical_payload = _fingerprint()
    current_payload = {
        **historical_payload,
        "algorithm_version": "decomposition-v5",
        "routing_implementation_identity": "c" * 64,
        "mixed_chain_inventory_identity": "d" * 64,
        "stage_sequence_identity": RUN_STAGE_SEQUENCE_IDENTITY,
    }

    historical = HistoricalV4ComparatorFingerprint.model_validate(historical_payload)
    current = CurrentV5ComparatorFingerprint.model_validate(current_payload)

    assert historical.identity == _identity(historical_payload)
    assert current.identity == _identity(current_payload)
    assert historical.identity != current.identity
    assert "algorithm_version" in historical.model_dump(mode="json")
    with pytest.raises(ValidationError):
        HistoricalV4ComparatorFingerprint.model_validate(
            {**historical_payload, "routing_implementation_identity": None}
        )
    with pytest.raises(ValidationError):
        HistoricalV4ComparatorFingerprint.model_validate(
            {**historical_payload, "mixed_chain_inventory_identity": "d" * 64}
        )
    for field in (
        "routing_implementation_identity",
        "mixed_chain_inventory_identity",
        "stage_sequence_identity",
    ):
        missing = dict(current_payload)
        missing.pop(field)
        with pytest.raises(ValidationError):
            CurrentV5ComparatorFingerprint.model_validate(missing)
        with pytest.raises(ValidationError):
            CurrentV5ComparatorFingerprint.model_validate(
                {**current_payload, field: None}
            )


def _run(
    run_id: str,
    artifact: Path,
    *,
    algorithm: str,
    routing_identity: str | None,
    **fingerprint_changes: object,
) -> ComparatorRun:
    worklist = fingerprint_changes.pop("_worklist", ("C1", "C2"))
    assert isinstance(worklist, tuple)
    fingerprint = _fingerprint(
        algorithm_version=algorithm,
        **fingerprint_changes,
    )
    fingerprint["worklist"] = worklist
    if algorithm == "decomposition-v5":
        fingerprint["routing_implementation_identity"] = routing_identity
        fingerprint["mixed_chain_inventory_identity"] = "d" * 64
        fingerprint["stage_sequence_identity"] = RUN_STAGE_SEQUENCE_IDENTITY
    artifact.write_text(
        "".join(
            f"<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{code}> "
            "<https://w3id.org/ontoprism/vocab#hasConstituent>   "
            "[<https://w3id.org/ontoprism/vocab#axis> "
            "<https://w3id.org/ontoprism/vocab#Morphology> ; "
            "<https://w3id.org/ontoprism/vocab#filler> "
            "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C10> ; "
            '<https://w3id.org/ontoprism/vocab#axisSource> "role" ] .\n'
            for code in ("C187445", "C187447", "C53558")
        )
        + f"# artifact for {run_id}\n"
    )
    representation_identity = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return ComparatorRun.model_validate(
        {
            "run_id": run_id,
            "ncit_version": "26.07d",
            "fingerprint": fingerprint,
            "fingerprint_identity": _identity(fingerprint),
            "worklist": worklist,
            "representation_identity": representation_identity,
            "publication_artifact_path": str(artifact),
        }
    )


def _baseline(run: ComparatorRun) -> CorpusBaseline:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": run.run_id,
        "source_identity": run.fingerprint.source_identity,
        "ontology_release": run.ncit_version,
        "branch": run.fingerprint.branch,
        "scope_root": run.fingerprint.scope_root,
        "scope_version": run.fingerprint.scope_version,
        "run_fingerprint_identity": run.fingerprint_identity,
        "representation_identity": run.representation_identity,
        "artifact_identity": run.representation_identity,
        "detector_identity": "d" * 64,
        "worklist_count": len(run.worklist),
        "outcome_counts": {
            "decomposed": 2,
            "residual": 0,
            "semantic_excluded": 0,
            "atomic_noop": 0,
            "unknown": 0,
        },
        "emitted_constituent_pair_count": 2,
        "complete_semantic_fact_count": 2,
        "source_occurrence_count": 2,
        "selected_occurrence_count": 2,
        "minted_count": 0,
    }
    return CorpusBaseline.model_validate(
        {**payload, "baseline_identity": corpus_baseline_identity(payload)}
    )


@pytest.mark.unit
def test_qualifies_exact_full_v4_v5_pair_and_binds_both_artifacts(
    tmp_path: Path,
) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full",
        old_artifact,
        algorithm="decomposition-v4",
        routing_identity=None,
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
    )

    qualification = qualify_r101_comparator(
        old_run=old,
        new_run=new,
        old_baseline=_baseline(old),
        old_artifact=old_artifact,
        new_artifact=new_artifact,
    )

    assert qualification.shared_controls.worklist_identity == _identity(("C1", "C2"))
    assert qualification.shared_controls.worklist_count == 2
    assert qualification.shared_controls.total_limit is None
    assert qualification.shared_controls.sample_manifest_identity is None
    assert qualification.old.algorithm_version == "decomposition-v4"
    assert qualification.new.algorithm_version == "decomposition-v5"
    assert qualification.observed_treatment.model_dump(mode="json") == {
        "historical_routing_implementation": "absent",
        "current_routing_implementation_identity": "c" * 64,
        "historical_mixed_chain_inventory": "absent",
        "current_mixed_chain_inventory_identity": "d" * 64,
        "historical_stage_sequence": "absent",
        "current_stage_sequence_identity": RUN_STAGE_SEQUENCE_IDENTITY,
    }
    assert qualification.conclusions.model_dump(mode="json") == {
        "persisted_occurrence_output_comparison": "permitted",
        "semantic_isolation": "partial-unqualified",
        "execution_comparability": "unqualified",
        "fully_controlled": False,
        "all_controls_equal": False,
        "causal_attribution": "prohibited",
        "authorization": "pending",
        "publication": "blocked",
    }
    assert qualification.old.artifact_identity == old.representation_identity
    assert qualification.new.artifact_identity == new.representation_identity
    assert {row.concept_code for row in qualification.shared_canary_constituents} == {
        "C187445",
        "C187447",
        "C53558",
    }
    assert all(
        row.axis == "op:Morphology" and row.filler_code == "C10"
        for row in qualification.shared_canary_constituents
    )
    assert qualification.query_identity == r101_ledger_query_identity()
    assert qualification.qualification_identity


@pytest.mark.unit
@pytest.mark.parametrize(
    ("side", "changes", "message"),
    [
        ("new", {"source_identity": "f" * 64}, "source identity"),
        ("new", {"collapse_policy_identity": "f" * 64}, "collapse policy"),
        ("new", {"branch": "disease", "scope_root": "C2991"}, "branch"),
        ("new", {"scope_version": "other"}, "scope version"),
        ("new", {"semantic_types": ("Disease or Syndrome",)}, "semantic types"),
        ("new", {"_worklist": ("C1",)}, "worklist"),
        ("new", {"total_limit": 2}, "total limit"),
        ("new", {"sample_manifest_identity": "e" * 64}, "sample manifest"),
        ("new", {"config_version": "other"}, "configuration"),
        ("new", {"walker_max_depth": 8}, "walker depth"),
        ("new", {"output_mode": "none"}, "output mode"),
        ("new", {"load_mode": "named-graph"}, "load mode"),
    ],
)
def test_comparator_refuses_each_confounded_or_partial_pair_dimension(
    tmp_path: Path,
    side: str,
    changes: dict[str, object],
    message: str,
) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full",
        old_artifact,
        algorithm="decomposition-v4",
        routing_identity=None,
        **(changes if side == "old" else {}),
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
        **(changes if side == "new" else {}),
    )

    with pytest.raises(R101ComparatorValidationError, match=message):
        qualify_r101_comparator(
            old_run=old,
            new_run=new,
            old_baseline=_baseline(old),
            old_artifact=old_artifact,
            new_artifact=new_artifact,
        )


@pytest.mark.unit
def test_comparator_refuses_wrong_versions_release_baseline_and_artifact(
    tmp_path: Path,
) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full",
        old_artifact,
        algorithm="decomposition-v4",
        routing_identity=None,
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
    )

    cases = (
        (
            old.model_copy(
                update={
                    "fingerprint": old.fingerprint.model_copy(
                        update={"algorithm_version": "decomposition-v3"}
                    )
                }
            ),
            new,
            _baseline(old),
            "algorithm",
        ),
        (
            old,
            new.model_copy(update={"ncit_version": "other"}),
            _baseline(old),
            "release",
        ),
        (
            old,
            new,
            _baseline(old).model_copy(update={"run_id": "different"}),
            "baseline",
        ),
    )
    for candidate_old, candidate_new, baseline, message in cases:
        with pytest.raises(R101ComparatorValidationError, match=message):
            qualify_r101_comparator(
                old_run=candidate_old,
                new_run=candidate_new,
                old_baseline=baseline,
                old_artifact=old_artifact,
                new_artifact=new_artifact,
            )

    new_artifact.write_text("modified\n")
    with pytest.raises(R101ComparatorValidationError, match="artifact"):
        qualify_r101_comparator(
            old_run=old,
            new_run=new,
            old_baseline=_baseline(old),
            old_artifact=old_artifact,
            new_artifact=new_artifact,
        )


@pytest.mark.unit
def test_comparator_refuses_when_an_alleged_addition_is_absent_on_either_side(
    tmp_path: Path,
) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full",
        old_artifact,
        algorithm="decomposition-v4",
        routing_identity=None,
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
    )
    old_artifact.write_text(
        old_artifact.read_text().replace(
            "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C187445>",
            "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C187446>",
        )
    )
    changed_old = old.model_copy(
        update={
            "representation_identity": hashlib.sha256(
                old_artifact.read_bytes()
            ).hexdigest()
        }
    )

    with pytest.raises(R101ComparatorValidationError) as error:
        qualify_r101_comparator(
            old_run=changed_old,
            new_run=new,
            old_baseline=_baseline(changed_old),
            old_artifact=old_artifact,
            new_artifact=new_artifact,
        )
    assert "C187445, C187447, C53558" in str(error.value)


@pytest.mark.unit
def test_comparator_wire_models_reject_corrupt_fingerprint_identity(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "old.ttl"
    run = _run(
        "old-full",
        artifact,
        algorithm="decomposition-v4",
        routing_identity=None,
    )
    with pytest.raises(ValidationError, match="fingerprint identity"):
        ComparatorRun.model_validate(
            {**run.model_dump(), "fingerprint_identity": "0" * 64}
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"semantic_types": ("Neoplastic Process", "Disease or Syndrome")},
            "canonical",
        ),
        ({"worklist": ()}, "nonempty"),
        ({"worklist": ("C1", "C1")}, "unique"),
        ({"branch": "disease"}, "scope root"),
    ],
)
def test_comparator_fingerprint_rejects_noncanonical_scope_and_collections(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        HistoricalV4ComparatorFingerprint.model_validate(_fingerprint(**changes))


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_field",
    ["mixed_chain_inventory_identity", "stage_sequence_identity"],
)
def test_comparator_fingerprint_requires_each_execution_control_identity(
    missing_field: str,
) -> None:
    fingerprint = _fingerprint(
        algorithm_version="decomposition-v5",
        routing_implementation_identity="c" * 64,
        mixed_chain_inventory_identity="d" * 64,
        stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
    )
    del fingerprint[missing_field]

    with pytest.raises(ValidationError, match=missing_field):
        CurrentV5ComparatorFingerprint.model_validate(fingerprint)


@pytest.mark.unit
def test_comparator_records_observed_historical_control_absence(
    tmp_path: Path,
) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full",
        old_artifact,
        algorithm="decomposition-v4",
        routing_identity=None,
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
    )

    qualification = qualify_r101_comparator(
        old_run=old,
        new_run=new,
        old_baseline=_baseline(old),
        old_artifact=old_artifact,
        new_artifact=new_artifact,
    )
    assert (
        qualification.observed_treatment.historical_routing_implementation == "absent"
    )
    assert qualification.observed_treatment.historical_mixed_chain_inventory == "absent"
    assert qualification.observed_treatment.historical_stage_sequence == "absent"


@pytest.mark.unit
@pytest.mark.parametrize(
    "changes",
    [
        {"branch": "disease", "scope_root": "C2991"},
        {"total_limit": 2},
        {"sample_manifest_identity": "e" * 64},
        {"output_mode": "none"},
    ],
)
def test_comparator_rejects_matching_pairs_that_are_not_full_standard_neoplasm_runs(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full",
        old_artifact,
        algorithm="decomposition-v4",
        routing_identity=None,
        **changes,
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
        **changes,
    )

    with pytest.raises(R101ComparatorValidationError):
        qualify_r101_comparator(
            old_run=old,
            new_run=new,
            old_baseline=_baseline(old),
            old_artifact=old_artifact,
            new_artifact=new_artifact,
        )


@pytest.mark.unit
@pytest.mark.parametrize("artifact_kind", ["directory", "symlink"])
def test_comparator_refuses_non_regular_artifacts(
    tmp_path: Path, artifact_kind: str
) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full", old_artifact, algorithm="decomposition-v4", routing_identity=None
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
    )
    invalid_artifact = tmp_path / "invalid"
    if artifact_kind == "directory":
        invalid_artifact.mkdir()
    else:
        invalid_artifact.symlink_to(old_artifact)

    with pytest.raises(R101ComparatorValidationError, match="regular file"):
        qualify_r101_comparator(
            old_run=old,
            new_run=new,
            old_baseline=_baseline(old),
            old_artifact=invalid_artifact,
            new_artifact=new_artifact,
        )


@pytest.mark.unit
@pytest.mark.unit
def test_comparator_refuses_different_canary_constituents(tmp_path: Path) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full", old_artifact, algorithm="decomposition-v4", routing_identity=None
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
    )
    new_artifact.write_text(new_artifact.read_text().replace("#C10>", "#C11>"))
    changed_new = new.model_copy(
        update={
            "representation_identity": hashlib.sha256(
                new_artifact.read_bytes()
            ).hexdigest()
        }
    )

    with pytest.raises(R101ComparatorValidationError, match="canary constituent scope"):
        qualify_r101_comparator(
            old_run=old,
            new_run=changed_new,
            old_baseline=_baseline(old),
            old_artifact=old_artifact,
            new_artifact=new_artifact,
        )


@pytest.mark.unit
def test_qualification_identity_and_atomic_writer_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_artifact = tmp_path / "old.ttl"
    new_artifact = tmp_path / "new.ttl"
    old = _run(
        "old-full", old_artifact, algorithm="decomposition-v4", routing_identity=None
    )
    new = _run(
        "new-full",
        new_artifact,
        algorithm="decomposition-v5",
        routing_identity="c" * 64,
    )
    qualification = qualify_r101_comparator(
        old_run=old,
        new_run=new,
        old_baseline=_baseline(old),
        old_artifact=old_artifact,
        new_artifact=new_artifact,
    )
    with pytest.raises(ValidationError, match="qualification identity"):
        R101ComparatorQualification.model_validate(
            {**qualification.model_dump(), "qualification_identity": "0" * 64}
        )

    rebound = qualification.model_dump(mode="json")
    rebound["old"]["algorithm_version"] = "decomposition-v5"
    rebound["qualification_identity"] = _identity(
        {
            key: value
            for key, value in rebound.items()
            if key != "qualification_identity"
        }
    )
    with pytest.raises(ValidationError, match="old comparator algorithm"):
        R101ComparatorQualification.model_validate_json(json.dumps(rebound))

    destination = tmp_path / "qualification.json"
    write_r101_comparator_qualification(destination, qualification)
    assert json.loads(destination.read_text()) == qualification.model_dump(mode="json")
    original = destination.read_bytes()

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "ontolib.decomposition.r101_comparator.os.replace", fail_replace
    )
    with pytest.raises(OSError, match="simulated replace failure"):
        write_r101_comparator_qualification(destination, qualification)
    assert destination.read_bytes() == original
    assert tuple(tmp_path.glob(".qualification.json.*")) == ()
