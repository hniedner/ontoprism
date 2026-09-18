from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts.adjudication import main as adjudication_main
from scripts.research.golden_review import load_migrated_historical_adjudication

from ontolib.decomposition import proposal_registry_migration as migration_module
from ontolib.decomposition.proposal_registry import (
    ProposalRegistry,
    load_proposal_registry,
    write_proposal_registry,
)
from ontolib.decomposition.proposal_registry_migration import (
    HistoricalArtifactKind,
    ProposalRegistryMigrationError,
    load_proposal_registry_migration_envelope,
    validate_historical_migration_artifact,
    validate_migrated_proposal_registry,
    write_proposal_registry_migration_envelope,
)

GOLDEN = Path(__file__).parent / "golden"
ORACLE = GOLDEN / "neoplasm-adjudicated.json"
REV1 = GOLDEN / "r103-review-state-26.07d.json"
REV2 = GOLDEN / "r103-review-state-26.07d-rev2.json"
CORROBORATION = GOLDEN / "r103-c3264-corroboration-26.07d.json"
REGISTRY = GOLDEN / "proposal-registry.json"


def _generate(output: Path):
    return write_proposal_registry_migration_envelope(
        historical_oracle_path=ORACLE,
        historical_r103_review_path=REV1,
        historical_r103_revision_path=REV2,
        historical_r103_corroboration_path=CORROBORATION,
        current_registry_path=REGISTRY,
        output_path=output,
    )


def _recompute_envelope_identity(payload: dict[str, object]) -> None:
    content = {
        key: value for key, value in payload.items() if key != "envelope_identity"
    }
    payload["envelope_identity"] = hashlib.sha256(
        json.dumps(
            content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


@pytest.mark.unit
def test_schema2_migration_envelope_is_deterministic_and_binds_unchanged_evidence(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    first = _generate(first_path)
    second = _generate(second_path)

    assert first_path.read_bytes() == second_path.read_bytes()
    assert first == second == load_proposal_registry_migration_envelope(first_path)
    assert first.migration_kind == "proposal-registry-schema1-to-schema2-binding"
    assert first.old_registry.schema_version == 1
    assert first.old_registry.registry_identity == (
        "ceaba33f2fedcf8e83265ff8b653caff39a4dc82f334175f66d4f0c488976261"
    )
    assert first.old_registry.file_sha256 == (
        "784445fec19a54e1b8d73cfc15b6dbd1312027106d1c058d25f8e273eab20fde"
    )
    assert first.new_registry.schema_version == 2
    assert first.new_registry.registry_identity == (
        "fab02c05906bcca0ed33cc483465640e2348e98bef8c7ad01460c23da3eac7c1"
    )
    assert (
        first.migration_tool_identity
        == hashlib.sha256(Path(migration_module.__file__).read_bytes()).hexdigest()
    )
    assert first.proof.status_counts == {"locally-approved": 2, "proposed": 5}
    assert first.proof.no_proposal_transitioned_to_accepted_in_ncit is True
    assert first.proof.nci_adoption_inferred is False
    assert {item.kind for item in first.historical_artifacts} == {
        "neoplasm-adjudication",
        "r103-review-state",
        "r103-review-revision",
        "r103-corroboration",
    }
    validate_migrated_proposal_registry(first, REGISTRY)
    artifacts: tuple[tuple[HistoricalArtifactKind, Path], ...] = (
        ("neoplasm-adjudication", ORACLE),
        ("r103-review-state", REV1),
        ("r103-review-revision", REV2),
        ("r103-corroboration", CORROBORATION),
    )
    for kind, path in artifacts:
        validate_historical_migration_artifact(first, kind, path)
    adjudication = load_migrated_historical_adjudication(
        ORACLE,
        REGISTRY,
        first_path,
    )
    assert adjudication.meta.proposal_registry_identity == (
        first.old_registry.registry_identity
    )
    cli_path = tmp_path / "cli.json"
    adjudication_main(
        [
            "bind-proposal-registry-migration",
            "--historical-oracle",
            str(ORACLE),
            "--historical-r103-review",
            str(REV1),
            "--historical-r103-revision",
            str(REV2),
            "--historical-r103-corroboration",
            str(CORROBORATION),
            "--current-registry",
            str(REGISTRY),
            "--output",
            str(cli_path),
        ]
    )
    assert cli_path.read_bytes() == first_path.read_bytes()


@pytest.mark.unit
def test_migration_envelope_rejects_each_independent_tamper(tmp_path: Path) -> None:
    envelope_path = tmp_path / "envelope.json"
    envelope = _generate(envelope_path)

    identity_payload = json.loads(envelope_path.read_text())
    identity_payload["envelope_identity"] = "0" * 64
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(identity_payload), encoding="utf-8")
    with pytest.raises(ProposalRegistryMigrationError, match="envelope identity"):
        load_proposal_registry_migration_envelope(identity_path)

    old_registry_payload = envelope.model_dump(mode="json")
    old_registry_payload["old_registry"]["file_sha256"] = "0" * 64
    with pytest.raises(ProposalRegistryMigrationError, match="old registry"):
        write_proposal_registry_migration_envelope(
            historical_oracle_path=ORACLE,
            historical_r103_review_path=REV1,
            historical_r103_revision_path=REV2,
            historical_r103_corroboration_path=CORROBORATION,
            current_registry_path=REGISTRY,
            output_path=tmp_path / "old-registry.json",
            expected_old_registry_file_sha256="0" * 64,
        )

    tampered_oracle = tmp_path / "oracle.json"
    tampered_oracle.write_bytes(ORACLE.read_bytes() + b" ")
    with pytest.raises(
        ProposalRegistryMigrationError, match="historical artifact bytes"
    ):
        validate_historical_migration_artifact(
            envelope, "neoplasm-adjudication", tampered_oracle
        )

    changed_registry = load_proposal_registry(REGISTRY)
    changed_proposal = changed_registry.proposals[0].model_copy(
        update={"status": "submitted"}
    )
    changed_registry = ProposalRegistry(
        source_identity=changed_registry.source_identity,
        ontology_version=changed_registry.ontology_version,
        proposals=(changed_proposal, *changed_registry.proposals[1:]),
    )
    changed_path = tmp_path / "registry.json"
    write_proposal_registry(changed_registry, changed_path)
    with pytest.raises(ProposalRegistryMigrationError, match="new registry"):
        validate_migrated_proposal_registry(envelope, changed_path)

    tampered_revision = tmp_path / "revision.json"
    revision = REV2.read_text().replace(
        "My Recommendation: Concept-scoped exclusion",
        "My Recommendation: changed",
        1,
    )
    tampered_revision.write_text(revision, encoding="utf-8")
    with pytest.raises(ProposalRegistryMigrationError, match="human decision proof"):
        write_proposal_registry_migration_envelope(
            historical_oracle_path=ORACLE,
            historical_r103_review_path=REV1,
            historical_r103_revision_path=tampered_revision,
            historical_r103_corroboration_path=CORROBORATION,
            current_registry_path=REGISTRY,
            output_path=tmp_path / "decision.json",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        "contract",
        "tool",
        "inventory",
        "old-registry",
        "new-registry",
        "historical-identity",
        "historical-bytes",
        "counts",
        "duplicate-proposal",
        "human-proof",
    ],
)
def test_migration_loader_rejects_internal_tamper_with_recomputed_outer_identity(
    tmp_path: Path, mutation: str
) -> None:
    envelope_path = tmp_path / "envelope.json"
    _generate(envelope_path)
    payload = json.loads(envelope_path.read_text(encoding="ascii"))
    if mutation == "contract":
        payload["migration_contract_identity"] = "0" * 64
    elif mutation == "tool":
        payload["migration_tool_identity"] = "0" * 64
    elif mutation == "inventory":
        payload["historical_artifacts"][1]["kind"] = "neoplasm-adjudication"
    elif mutation == "old-registry":
        payload["old_registry"]["registry_identity"] = "0" * 64
    elif mutation == "new-registry":
        payload["new_registry"]["registry_identity"] = "0" * 64
    elif mutation == "historical-identity":
        payload["historical_artifacts"][0]["artifact_identity"] = "0" * 64
    elif mutation == "historical-bytes":
        payload["historical_artifacts"][0]["file_sha256"] = "0" * 64
    elif mutation == "counts":
        payload["proof"]["status_counts"]["proposed"] = 4
    elif mutation == "duplicate-proposal":
        payload["proof"]["proposal_rows"][1]["proposal_id"] = payload["proof"][
            "proposal_rows"
        ][0]["proposal_id"]
    else:
        payload["proof"]["historical_human_decision_proof_identity"] = "0" * 64
    _recompute_envelope_identity(payload)
    changed = tmp_path / f"{mutation}.json"
    changed.write_text(json.dumps(payload), encoding="ascii")

    with pytest.raises(ProposalRegistryMigrationError):
        load_proposal_registry_migration_envelope(changed)


@pytest.mark.unit
@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"schema_version":1,"schema_version":1}']
)
def test_migration_loader_rejects_malformed_and_duplicate_json(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="ascii")

    with pytest.raises(ProposalRegistryMigrationError):
        load_proposal_registry_migration_envelope(path)


@pytest.mark.unit
def test_migration_generator_rejects_duplicate_inputs_and_missing_output_parent(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ProposalRegistryMigrationError, match="duplicate migration inputs"
    ):
        write_proposal_registry_migration_envelope(
            historical_oracle_path=ORACLE,
            historical_r103_review_path=REV1,
            historical_r103_revision_path=REV2,
            historical_r103_corroboration_path=REV2,
            current_registry_path=REGISTRY,
            output_path=tmp_path / "duplicate.json",
        )

    with pytest.raises(ProposalRegistryMigrationError, match="output parent"):
        _generate(tmp_path / "missing" / "envelope.json")


@pytest.mark.unit
def test_migration_generator_replace_failure_preserves_output_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "envelope.json"
    output.write_text("preserved\n", encoding="ascii")

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(
        "ontolib.decomposition.proposal_registry_migration.os.replace", fail_replace
    )

    with pytest.raises(OSError, match="injected replace failure"):
        _generate(output)
    assert output.read_text(encoding="ascii") == "preserved\n"
    assert tuple(tmp_path.glob(".envelope.json.*")) == ()


@pytest.mark.unit
def test_migration_validators_reject_missing_current_and_historical_inputs(
    tmp_path: Path,
) -> None:
    envelope_path = tmp_path / "envelope.json"
    envelope = _generate(envelope_path)
    missing = tmp_path / "missing.json"

    with pytest.raises(ProposalRegistryMigrationError, match="new registry is invalid"):
        validate_migrated_proposal_registry(envelope, missing)
    with pytest.raises(ProposalRegistryMigrationError, match="invalid migration input"):
        validate_historical_migration_artifact(
            envelope, "neoplasm-adjudication", missing
        )
