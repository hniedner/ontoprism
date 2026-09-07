"""Append-only evidence binding the historical proposal registry to schema 2."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import to_jsonable_python

from ontolib.decomposition.proposal_registry import (
    ConceptProposal,
    ProposalRegistry,
    load_proposal_registry,
)
from ontolib.decomposition.r103_review_promotion import (
    load_r103_promoted_review_revision,
    load_r103_promoted_review_state,
)

_SHA256 = r"^[0-9a-f]{64}$"
_MIGRATION_KIND = "proposal-registry-schema1-to-schema2-binding"
_TOOL_VERSION = "ontoprism-proposal-registry-binding-v1"
_OLD_REGISTRY_IDENTITY = (
    "ceaba33f2fedcf8e83265ff8b653caff39a4dc82f334175f66d4f0c488976261"
)
_OLD_REGISTRY_FILE_SHA256 = (
    "784445fec19a54e1b8d73cfc15b6dbd1312027106d1c058d25f8e273eab20fde"
)
_NEW_REGISTRY_IDENTITY = (
    "fab02c05906bcca0ed33cc483465640e2348e98bef8c7ad01460c23da3eac7c1"
)
_NEW_REGISTRY_FILE_SHA256 = (
    "c1c3b78f18da14255244989621a83969549d05c82e9e79ce42f5f5d5c0203120"
)
_HISTORICAL_SHA256 = {
    "neoplasm-adjudication": (
        "b3e909802ddc762d3c348c19ac25f343cc888d9e2ec29108bd94b02c89657509"
    ),
    "r103-review-state": (
        "3b17fee5ac354ca8d48637f2a7f8b0451e0b4afed6922d0f745e6d284ca9c899"
    ),
    "r103-review-revision": (
        "03822dcbfc4190e09e9394cb310aae2a6cca2f9c8d728bf3997d9e11d1e4730f"
    ),
    "r103-corroboration": (
        "a1d4b82f985d6fc099040491ac3ad4d40231452265efc10c2b8ac1c43519c823"
    ),
}
_HISTORICAL_ARTIFACT_IDENTITIES = {
    "neoplasm-adjudication": (
        "103542fc9fcc0391ea29ac0a1ed585781f15f5e34f9d0f613e1f4bf51a040406"
    ),
    "r103-review-state": (
        "90ea507e93cebaf6399b3aa5bea92081e6d3dba50b7631783666d9382d267d1a"
    ),
    "r103-review-revision": (
        "d99b3f27bb2d6416149411ecbe13893aed88d183f39405acd80529c771a5d160"
    ),
    "r103-corroboration": (
        "f96081372e6d7e3be0e65a5ab8342b12f5b5d129df16b263db5dbafe6130552c"
    ),
}
_HUMAN_DECISION_PROOF_IDENTITY = (
    "917638dcf54d37477e209fecc199cb5029b31a94d58c813fbb20629433013325"
)
HistoricalArtifactKind = Literal[
    "neoplasm-adjudication",
    "r103-review-state",
    "r103-review-revision",
    "r103-corroboration",
]


class ProposalRegistryMigrationError(ValueError):
    """The immutable historical evidence cannot support the schema-2 binding."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        to_jsonable_python(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _identity(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ProposalRegistryMigrationError(
            f"migration input cannot be read: {path}"
        ) from error


def _load_json(path: Path) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ProposalRegistryMigrationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProposalRegistryMigrationError(
            f"invalid migration input: {path}"
        ) from error
    if not isinstance(value, dict):
        raise ProposalRegistryMigrationError("migration input must be a JSON object")
    return value


class RegistryMigrationBinding(_StrictModel):
    schema_version: Literal[1, 2]
    registry_identity: str = Field(pattern=_SHA256)
    file_sha256: str = Field(pattern=_SHA256)


class HistoricalArtifactBinding(_StrictModel):
    kind: HistoricalArtifactKind
    artifact_identity: str = Field(pattern=_SHA256)
    file_sha256: str = Field(pattern=_SHA256)


class ProposalMigrationProofRow(_StrictModel):
    proposal_id: str
    kind: Literal["concept", "relation"]
    status: Literal["proposed", "locally-approved"]
    axis: str
    source_references: tuple[str, ...]
    subject_semantics_identity: str = Field(pattern=_SHA256)


class ProposalMigrationProof(_StrictModel):
    proposal_rows: tuple[ProposalMigrationProofRow, ...]
    status_counts: dict[Literal["proposed", "locally-approved"], int]
    old_and_new_subject_semantics_identical: Literal[True]
    historical_human_decision_proof_identity: str = Field(pattern=_SHA256)
    no_proposal_transitioned_to_accepted_in_ncit: Literal[True]
    nci_adoption_inferred: Literal[False]


class ProposalRegistryMigrationEnvelope(_StrictModel):
    schema_version: Literal[1]
    migration_kind: Literal["proposal-registry-schema1-to-schema2-binding"]
    old_registry: RegistryMigrationBinding
    new_registry: RegistryMigrationBinding
    historical_artifacts: tuple[
        HistoricalArtifactBinding,
        HistoricalArtifactBinding,
        HistoricalArtifactBinding,
        HistoricalArtifactBinding,
    ]
    proof: ProposalMigrationProof
    migration_tool_version: Literal["ontoprism-proposal-registry-binding-v1"]
    migration_contract_identity: str = Field(pattern=_SHA256)
    migration_tool_identity: str = Field(pattern=_SHA256)
    envelope_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_envelope(self) -> Self:
        if self.migration_contract_identity != _contract_identity():
            raise ValueError("migration contract identity differs")
        if self.migration_tool_identity != _tool_identity():
            raise ValueError("migration tool identity differs")
        kinds = tuple(item.kind for item in self.historical_artifacts)
        if kinds != tuple(_HISTORICAL_SHA256):
            raise ValueError("historical artifact inventory differs")
        self._validate_registry_bindings()
        self._validate_historical_bindings()
        self._validate_proof()
        expected = _identity(self.model_dump(exclude={"envelope_identity"}))
        if self.envelope_identity != expected:
            raise ValueError("envelope identity differs")
        return self

    def _validate_registry_bindings(self) -> None:
        expected_old = (1, _OLD_REGISTRY_IDENTITY, _OLD_REGISTRY_FILE_SHA256)
        if (
            self.old_registry.schema_version,
            self.old_registry.registry_identity,
            self.old_registry.file_sha256,
        ) != expected_old:
            raise ValueError("old registry binding differs")
        expected_new = (2, _NEW_REGISTRY_IDENTITY, _NEW_REGISTRY_FILE_SHA256)
        if (
            self.new_registry.schema_version,
            self.new_registry.registry_identity,
            self.new_registry.file_sha256,
        ) != expected_new:
            raise ValueError("new registry binding differs")

    def _validate_historical_bindings(self) -> None:
        for binding in self.historical_artifacts:
            if (
                binding.artifact_identity
                != _HISTORICAL_ARTIFACT_IDENTITIES[binding.kind]
                or binding.file_sha256 != _HISTORICAL_SHA256[binding.kind]
            ):
                raise ValueError("historical artifact binding differs")

    def _validate_proof(self) -> None:
        if self.proof.status_counts != dict(
            Counter(row.status for row in self.proof.proposal_rows)
        ):
            raise ValueError("proposal migration status counts differ")
        if len({row.proposal_id for row in self.proof.proposal_rows}) != len(
            self.proof.proposal_rows
        ):
            raise ValueError("duplicate proposal IDs")
        if (
            self.proof.historical_human_decision_proof_identity
            != _HUMAN_DECISION_PROOF_IDENTITY
        ):
            raise ValueError("historical human decision proof differs")


def _contract_identity() -> str:
    return _identity(
        {
            "migration_kind": _MIGRATION_KIND,
            "old_schema": 1,
            "new_schema": 2,
            "historical_artifacts": tuple(_HISTORICAL_SHA256),
            "unchanged_fields": (
                "proposal IDs",
                "proposal kinds",
                "proposal statuses",
                "source references",
                "human decisions",
                "rationales",
                "reviewer/date",
                "subject semantics",
            ),
            "adoption_rule": "no NCI adoption inferred",
        }
    )


def _tool_identity() -> str:
    return _file_sha256(Path(__file__))


def _old_registry_payload(registry: ProposalRegistry) -> dict[str, object]:
    payload = registry.model_dump(mode="json", exclude={"registry_identity"})
    payload["schema_version"] = 1
    proposals = payload.get("proposals")
    if not isinstance(proposals, list):
        raise ProposalRegistryMigrationError("new registry proposals are malformed")
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise ProposalRegistryMigrationError("new registry proposal is malformed")
        proposal.pop("adoption_evidence", None)
    return payload


def _proposal_rows(registry: ProposalRegistry) -> tuple[ProposalMigrationProofRow, ...]:
    rows: list[ProposalMigrationProofRow] = []
    for proposal in registry.proposals:
        source_references = (
            (*proposal.source_concepts, *proposal.source_roles)
            if isinstance(proposal, ConceptProposal)
            else (*proposal.source_roles, *proposal.source_examples)
        )
        old_semantics = _old_registry_payload(
            ProposalRegistry(
                source_identity=registry.source_identity,
                ontology_version=registry.ontology_version,
                proposals=(proposal,),
            )
        )["proposals"]
        rows.append(
            ProposalMigrationProofRow(
                proposal_id=proposal.id,
                kind="concept" if isinstance(proposal, ConceptProposal) else "relation",
                status=cast("Literal['proposed', 'locally-approved']", proposal.status),
                axis=proposal.axis,
                source_references=tuple(source_references),
                subject_semantics_identity=_identity(old_semantics[0]),  # type: ignore[index]
            )
        )
    if len({row.proposal_id for row in rows}) != len(rows):
        raise ProposalRegistryMigrationError("duplicate proposal IDs")
    return tuple(rows)


def _artifact_identity(kind: HistoricalArtifactKind, value: dict[str, object]) -> str:
    if kind == "neoplasm-adjudication":
        identity = value.get("artifact_identity")
    elif kind == "r103-corroboration":
        identity = value.get("corroboration_identity")
    else:
        identity = value.get("artifact_identity")
    if not isinstance(identity, str):
        raise ProposalRegistryMigrationError("historical artifact identity is missing")
    return identity


def _human_decision_proof(
    oracle: dict[str, object], revision: dict[str, object]
) -> str:
    meta = oracle.get("_meta")
    concepts = oracle.get("concepts")
    registry = revision.get("registry")
    transcription = revision.get("transcription")
    if not isinstance(meta, dict) or not isinstance(concepts, list):
        raise ProposalRegistryMigrationError("historical human decision proof differs")
    if not isinstance(registry, dict) or not isinstance(
        registry.get("decisions"), list
    ):
        raise ProposalRegistryMigrationError("historical human decision proof differs")
    return _identity(
        {
            "oracle_reviewer": meta.get("reviewer"),
            "oracle_workbook_identity": meta.get("workbook_identity"),
            "oracle_concepts": concepts,
            "r103_decisions": registry["decisions"],
            "r103_transcription": transcription,
        }
    )


def _historical_bindings(
    paths: tuple[tuple[HistoricalArtifactKind, Path], ...],
) -> tuple[
    tuple[HistoricalArtifactBinding, ...],
    dict[HistoricalArtifactKind, dict[str, object]],
]:
    resolved = tuple(path.resolve() for _kind, path in paths)
    if len(set(resolved)) != len(resolved):
        raise ProposalRegistryMigrationError("duplicate migration inputs")
    bindings: list[HistoricalArtifactBinding] = []
    values: dict[HistoricalArtifactKind, dict[str, object]] = {}
    for kind, path in paths:
        value = _load_json(path)
        digest = _file_sha256(path)
        if digest != _HISTORICAL_SHA256[kind]:
            raise ProposalRegistryMigrationError("historical artifact bytes differ")
        bindings.append(
            HistoricalArtifactBinding(
                kind=kind,
                artifact_identity=_artifact_identity(kind, value),
                file_sha256=digest,
            )
        )
        values[kind] = value
    return tuple(bindings), values


def _old_registry_binding(
    registry: ProposalRegistry,
    values: dict[HistoricalArtifactKind, dict[str, object]],
    expected_file_sha256: str,
) -> RegistryMigrationBinding:
    old_identity = _identity(_old_registry_payload(registry))
    oracle_meta = values["neoplasm-adjudication"].get("_meta")
    rev1 = values["r103-review-state"]
    rev2 = values["r103-review-revision"]
    if not isinstance(oracle_meta, dict):
        raise ProposalRegistryMigrationError("old registry binding differs")
    observed = (
        old_identity,
        oracle_meta.get("proposal_registry_identity"),
        rev1.get("proposal_registry_identity"),
        rev2.get("proposal_registry_identity"),
    )
    if observed != (_OLD_REGISTRY_IDENTITY,) * 4:
        raise ProposalRegistryMigrationError("old registry semantic binding differs")
    file_digests = (
        expected_file_sha256,
        rev1.get("proposal_registry_file_sha256"),
        rev2.get("proposal_registry_file_sha256"),
    )
    if file_digests != (_OLD_REGISTRY_FILE_SHA256,) * 3:
        raise ProposalRegistryMigrationError("old registry file binding differs")
    return RegistryMigrationBinding(
        schema_version=1,
        registry_identity=old_identity,
        file_sha256=_OLD_REGISTRY_FILE_SHA256,
    )


def _render(envelope: ProposalRegistryMigrationEnvelope) -> bytes:
    return (
        json.dumps(
            envelope.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        ).encode("ascii")
        + b"\n"
    )


def _atomic_write(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise ProposalRegistryMigrationError(
            f"output parent does not exist: {path.parent}"
        )
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staging = Path(name)
    primary: BaseException | None = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError as cleanup:
            if primary is None:
                raise
            primary.add_note(f"cleanup failure: {cleanup}")


def write_proposal_registry_migration_envelope(
    *,
    historical_oracle_path: Path,
    historical_r103_review_path: Path,
    historical_r103_revision_path: Path,
    historical_r103_corroboration_path: Path,
    current_registry_path: Path,
    output_path: Path,
    expected_old_registry_file_sha256: str = _OLD_REGISTRY_FILE_SHA256,
) -> ProposalRegistryMigrationEnvelope:
    """Generate the deterministic append-only schema migration evidence."""
    paths: tuple[tuple[HistoricalArtifactKind, Path], ...] = (
        ("neoplasm-adjudication", historical_oracle_path),
        ("r103-review-state", historical_r103_review_path),
        ("r103-review-revision", historical_r103_revision_path),
        ("r103-corroboration", historical_r103_corroboration_path),
    )
    try:
        load_r103_promoted_review_state(historical_r103_review_path)
        load_r103_promoted_review_revision(historical_r103_revision_path)
    except (OSError, ValueError) as error:
        raise ProposalRegistryMigrationError(
            "historical human decision proof differs"
        ) from error
    bindings, values = _historical_bindings(paths)
    registry = load_proposal_registry(current_registry_path)
    old_binding = _old_registry_binding(
        registry, values, expected_old_registry_file_sha256
    )
    rows = _proposal_rows(registry)
    proof = ProposalMigrationProof(
        proposal_rows=rows,
        status_counts=dict(Counter(row.status for row in rows)),
        old_and_new_subject_semantics_identical=True,
        historical_human_decision_proof_identity=_human_decision_proof(
            values["neoplasm-adjudication"], values["r103-review-revision"]
        ),
        no_proposal_transitioned_to_accepted_in_ncit=True,
        nci_adoption_inferred=False,
    )
    payload = {
        "schema_version": 1,
        "migration_kind": _MIGRATION_KIND,
        "old_registry": old_binding,
        "new_registry": RegistryMigrationBinding(
            schema_version=2,
            registry_identity=registry.registry_identity,
            file_sha256=_file_sha256(current_registry_path),
        ),
        "historical_artifacts": bindings,
        "proof": proof,
        "migration_tool_version": _TOOL_VERSION,
        "migration_contract_identity": _contract_identity(),
        "migration_tool_identity": _tool_identity(),
    }
    envelope = ProposalRegistryMigrationEnvelope.model_validate(
        {**payload, "envelope_identity": _identity(payload)}
    )
    _atomic_write(output_path, _render(envelope))
    return envelope


def load_proposal_registry_migration_envelope(
    path: Path,
) -> ProposalRegistryMigrationEnvelope:
    """Load only the current machine envelope, never an old proposal registry."""
    try:
        return ProposalRegistryMigrationEnvelope.model_validate_json(
            _canonical(_load_json(path))
        )
    except (ValidationError, ValueError) as error:
        raise ProposalRegistryMigrationError(str(error)) from error


def validate_migrated_proposal_registry(
    envelope: ProposalRegistryMigrationEnvelope, path: Path
) -> ProposalRegistry:
    """Validate the current schema-2 registry against the migration envelope."""
    try:
        registry = load_proposal_registry(path)
    except (OSError, ValueError) as error:
        raise ProposalRegistryMigrationError("new registry is invalid") from error
    observed = (registry.schema_version, registry.registry_identity, _file_sha256(path))
    expected = (
        envelope.new_registry.schema_version,
        envelope.new_registry.registry_identity,
        envelope.new_registry.file_sha256,
    )
    if observed != expected or _proposal_rows(registry) != envelope.proof.proposal_rows:
        raise ProposalRegistryMigrationError("new registry binding differs")
    return registry


def validate_historical_migration_artifact(
    envelope: ProposalRegistryMigrationEnvelope,
    kind: HistoricalArtifactKind,
    path: Path,
) -> None:
    """Verify immutable historical bytes without loading old proposal data."""
    matches = tuple(item for item in envelope.historical_artifacts if item.kind == kind)
    if len(matches) != 1:
        raise ProposalRegistryMigrationError("historical artifact binding is missing")
    value = _load_json(path)
    observed = (_artifact_identity(kind, value), _file_sha256(path))
    expected = (matches[0].artifact_identity, matches[0].file_sha256)
    if observed != expected:
        raise ProposalRegistryMigrationError("historical artifact bytes differ")
