"""Content-addressed pre-run evidence for mixed-edge specificity chains."""

from __future__ import annotations

import hashlib
import json
import re
from itertools import pairwise
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ontolib.decomposition.atomic_write import atomic_write_bytes
from ontolib.decomposition.models import SemanticRoute
from ontolib.decomposition.r101_conservation import (
    _decompress_report,
    _unique_json_object,
)

_SHA256 = r"^[0-9a-f]{64}$"
_RUN_ID = r"^neoplasm-[0-9a-f-]+$"
_REQUIRED_CANARIES = frozenset({"C102570", "C161649", "C175329", "C27381"})
_HISTORICAL_REPORT_FILE_IDENTITY = (
    "f3d4f2bc551db08d3f665e92c9199ec09d9d80417f09a0c24e47a21b3a2de30f"
)
HISTORICAL_MIXED_CHAIN_SELECTOR_IDENTITY = (
    "aa777510e0ffc0a7cfc8c3682506c046300ed6749598b78504eb1ce8a3888608"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class MixedChainPathEdge(_StrictModel):
    """Serialized source-bound specificity edge."""

    kind: Literal["is-a", "r82"]
    broader_code: str = Field(pattern=r"^C[0-9]+$")
    narrower_code: str = Field(pattern=r"^C[0-9]+$")
    source_identity: str = Field(pattern=_SHA256)


def _mixed_chain_path(value: object) -> object:
    if not isinstance(value, (tuple, list)):
        return value
    return tuple(
        MixedChainPathEdge.model_validate(
            edge
            if isinstance(edge, dict)
            else {
                "kind": edge.kind,
                "broader_code": edge.broader_code,
                "narrower_code": edge.narrower_code,
                "source_identity": edge.source_identity,
            }
        )
        for edge in value
    )


def _require_candidate_occurrences(candidate: MixedChainCandidate) -> None:
    identities = candidate.source_occurrence_ids
    if identities != tuple(sorted(set(identities))):
        raise ValueError("mixed-chain source occurrences are not canonical")
    invalid = any(re.fullmatch(_SHA256, value) is None for value in identities)
    if not identities or invalid:
        raise ValueError("mixed-chain source occurrences are invalid")


def _require_candidate_path(candidate: MixedChainCandidate) -> None:
    path = candidate.specificity_path
    if path[0].broader_code != candidate.broad_filler:
        raise ValueError("mixed-chain path does not start at broad filler")
    if path[-1].narrower_code != candidate.terminal_filler:
        raise ValueError("mixed-chain path does not end at terminal filler")
    if {edge.kind for edge in path} != {"is-a", "r82"}:
        raise ValueError("mixed-chain path does not contain both relation kinds")
    if any(left.narrower_code != right.broader_code for left, right in pairwise(path)):
        raise ValueError("mixed-chain path is not contiguous")


class MixedChainCandidate(_StrictModel):
    """One source occurrence whose unique terminal requires both relation kinds."""

    concept_code: str = Field(pattern=r"^C[0-9]+$")
    axis: str = Field(pattern=r"^op:[A-Za-z][A-Za-z0-9]*$")
    source_role: str = Field(pattern=r"^R[0-9]+$")
    broad_filler: str = Field(pattern=r"^C[0-9]+$")
    terminal_filler: str = Field(pattern=r"^C[0-9]+$")
    source_occurrence_ids: tuple[str, ...]
    specificity_path: tuple[MixedChainPathEdge, ...] = Field(min_length=2)

    @field_validator("specificity_path", mode="before")
    @classmethod
    def _parse_specificity_path(cls, value: object) -> object:
        return _mixed_chain_path(value)

    @model_validator(mode="after")
    def _is_exact_mixed_path(self) -> Self:
        _require_candidate_occurrences(self)
        _require_candidate_path(self)
        return self


class PersistedSelectorOccurrence(_StrictModel):
    """Bounded persisted selector input for one source occurrence."""

    concept_code: str = Field(pattern=r"^C[0-9]+$")
    source_occurrence_id: str = Field(pattern=_SHA256)
    source_fact_id: str = Field(pattern=_SHA256)
    source_role: str = Field(pattern=r"^R[0-9]+$")
    anchoring_genus: str = Field(pattern=r"^C[0-9]+$")
    normalized_axis: str = Field(pattern=r"^(?:op:[A-Za-z][A-Za-z0-9]*|R[0-9]+)$")
    source_filler: str = Field(pattern=r"^C[0-9]+$")
    semantic_route: SemanticRoute
    semantic_type: str | None
    policy_decision_identity: str | None = Field(default=None, pattern=_SHA256)


class HistoricalNonR101DeltaRow(_StrictModel):
    change: Literal["added", "removed"]
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    axis: str = Field(min_length=1)
    filler_code: str = Field(pattern=r"^(?:C[0-9]+|MINT-[0-9a-f]{12})$")
    axis_source: Literal["role", "nlp", "parent"]
    source_roles: tuple[str, ...]
    most_specific: bool
    needs_review: bool
    relationship_group: str | None
    source_definition_ids: tuple[str, ...]
    source_occurrence_ids: tuple[str, ...]


class HistoricalMixedChainDeltaEvidence(_StrictModel):
    rows: tuple[HistoricalNonR101DeltaRow, ...]


class HistoricalMixedChainSourceReport(_StrictModel):
    """Immutable schema-3 diagnostic used only for the historical projection era."""

    schema_version: Literal[3]
    source_identity: str = Field(pattern=_SHA256)
    new_run_id: Literal["neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722"]
    report_identity: Literal[
        "25ed41375bc633505031a1e69327c41ac02a76f3f0759f86c899357b4fd4d6ba"
    ]
    non_r101_delta_evidence: HistoricalMixedChainDeltaEvidence


class HistoricalMixedChainRunFingerprint(_StrictModel):
    """Exact persisted fingerprint schema of the historical 2b39 run."""

    schema_version: Literal[4]
    source_identity: str = Field(pattern=_SHA256)
    collapse_policy_identity: str = Field(pattern=_SHA256)
    routing_implementation_identity: str = Field(pattern=_SHA256)
    branch: Literal["neoplasm"]
    scope_root: Literal["C3262"]
    scope_version: str = Field(min_length=1)
    semantic_types: tuple[str, ...]
    worklist: tuple[str, ...] = Field(min_length=1)
    total_limit: None
    sample_manifest_identity: None
    algorithm_version: Literal["decomposition-v5"]
    config_version: str = Field(min_length=1)
    walker_max_depth: int = Field(gt=0)
    output_mode: Literal["file"]
    load_mode: Literal["none"]
    emitted_at: AwareDatetime

    @model_validator(mode="after")
    def _collections_are_canonical(self) -> Self:
        if self.semantic_types != tuple(sorted(set(self.semantic_types))):
            raise ValueError("semantic types are not canonical")
        if len(self.worklist) != len(set(self.worklist)):
            raise ValueError("worklist is not unique")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()


class HistoricalMixedChainRunBinding(_StrictModel):
    """Validated persisted inputs required to replay the historical inventory."""

    run_id: Literal["neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722"]
    fingerprint: HistoricalMixedChainRunFingerprint
    fingerprint_identity: str = Field(pattern=_SHA256)
    materialized_worklist: tuple[str, ...]

    @model_validator(mode="after")
    def _identities_match(self) -> Self:
        if self.fingerprint_identity != self.fingerprint.identity:
            raise ValueError("historical fingerprint identity differs")
        if self.materialized_worklist != self.fingerprint.worklist:
            raise ValueError("historical materialized worklist differs")
        return self


class MixedChainInventory(_StrictModel):
    """Exact candidate inventory bound to one source, worklist, and selector."""

    schema_version: Literal[1] = 1
    source_identity: str = Field(pattern=_SHA256)
    worklist_identity: str = Field(pattern=_SHA256)
    worklist_count: int = Field(gt=0)
    selector_identity: str = Field(pattern=_SHA256)
    source_run_id: str = Field(pattern=_RUN_ID)
    source_report_identity: str = Field(pattern=_SHA256)
    candidates: tuple[MixedChainCandidate, ...]
    candidate_codes: tuple[str, ...]
    candidate_count: int = Field(ge=0)
    unclassified_codes: tuple[str, ...] = ()
    inventory_identity: str = Field(pattern=_SHA256)

    @classmethod
    def create(
        cls,
        *,
        source_identity: str,
        worklist_identity: str,
        worklist_count: int,
        selector_identity: str,
        source_run_id: str,
        source_report_identity: str,
        candidates: tuple[MixedChainCandidate, ...],
        unclassified_codes: tuple[str, ...] = (),
    ) -> MixedChainInventory:
        payload = {
            "schema_version": 1,
            "source_identity": source_identity,
            "worklist_identity": worklist_identity,
            "worklist_count": worklist_count,
            "selector_identity": selector_identity,
            "source_run_id": source_run_id,
            "source_report_identity": source_report_identity,
            "candidates": tuple(
                sorted(
                    candidates,
                    key=lambda item: (
                        item.concept_code,
                        item.axis,
                        item.broad_filler,
                        item.terminal_filler,
                    ),
                )
            ),
            "candidate_codes": tuple(
                sorted({candidate.concept_code for candidate in candidates})
            ),
            "candidate_count": len(
                {candidate.concept_code for candidate in candidates}
            ),
            "unclassified_codes": tuple(sorted(set(unclassified_codes))),
        }
        identity = hashlib.sha256(
            json.dumps(
                cls._json_payload(payload), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        return cls(**payload, inventory_identity=identity)

    @staticmethod
    def _json_payload(payload: dict[str, object]) -> dict[str, object]:
        return json.loads(
            json.dumps(
                payload,
                default=lambda value: value.model_dump(mode="json"),
                sort_keys=True,
            )
        )

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        _require_inventory_shape(self)
        _require_inventory_source_paths(self)
        _require_inventory_identity(self)
        return self

    @property
    def identity(self) -> str:
        return self.inventory_identity


def _require_inventory_shape(inventory: MixedChainInventory) -> None:
    expected_codes = tuple(sorted({item.concept_code for item in inventory.candidates}))
    if inventory.candidate_codes != expected_codes:
        raise ValueError("mixed-chain candidate codes differ")
    if inventory.candidate_count != len(expected_codes):
        raise ValueError("mixed-chain candidate count differs")
    if len(inventory.candidates) != inventory.candidate_count:
        raise ValueError("mixed-chain candidates are not one per concept")


def _require_inventory_source_paths(inventory: MixedChainInventory) -> None:
    if any(
        edge.source_identity != inventory.source_identity
        for candidate in inventory.candidates
        for edge in candidate.specificity_path
    ):
        raise ValueError("mixed-chain path source identity differs")


def _require_inventory_identity(inventory: MixedChainInventory) -> None:
    payload = inventory.model_dump(mode="json", exclude={"inventory_identity"})
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if inventory.inventory_identity != expected:
        raise ValueError("mixed-chain inventory identity differs")


def mixed_chain_worklist_identity(worklist: tuple[str, ...]) -> str:
    """Identify the exact ordered worklist used by inventory and run preflight."""
    return hashlib.sha256(
        json.dumps(worklist, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def write_mixed_chain_inventory(path: Path, inventory: MixedChainInventory) -> None:
    """Write one canonical inventory after full model and identity validation."""
    validated = MixedChainInventory.model_validate(inventory.model_dump())
    atomic_write_bytes(
        path,
        (
            json.dumps(validated.model_dump(mode="json"), sort_keys=True, indent=2)
            + "\n"
        ).encode(),
    )


def load_mixed_chain_inventory(path: Path) -> MixedChainInventory:
    """Load strict content-addressed mixed-chain inventory JSON."""
    return MixedChainInventory.model_validate_json(path.read_bytes())


def load_historical_mixed_chain_source_report(
    path: Path,
) -> HistoricalMixedChainSourceReport:
    """Load the exact immutable source report for the 2b39 projection evidence."""
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != _HISTORICAL_REPORT_FILE_IDENTITY:
        raise ValueError("historical mixed-chain report file identity differs")
    decompressed = _decompress_report(content)
    payload = json.loads(decompressed, object_pairs_hook=_unique_json_object)
    identified = dict(payload)
    identified.pop("report_identity", None)
    report_identity = hashlib.sha256(
        json.dumps(identified, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    if payload.get("report_identity") != report_identity:
        raise ValueError("historical mixed-chain report identity differs")
    return HistoricalMixedChainSourceReport.model_validate_json(
        json.dumps(
            {
                "schema_version": payload.get("schema_version"),
                "source_identity": payload.get("source_identity"),
                "new_run_id": payload.get("new_run_id"),
                "report_identity": payload.get("report_identity"),
                "non_r101_delta_evidence": {
                    "rows": payload.get("non_r101_delta_evidence", {}).get("rows")
                },
            }
        )
    )


def require_mixed_chain_preflight(
    inventory: MixedChainInventory,
    *,
    source_identity: str,
    worklist_identity: str,
    worklist_count: int,
) -> None:
    """Reject a run unless its mixed-chain inventory is exact and classified."""
    checks = (
        (inventory.source_identity == source_identity, "source identity"),
        (inventory.worklist_identity == worklist_identity, "worklist identity"),
        (inventory.worklist_count == worklist_count, "worklist count"),
        (
            inventory.selector_identity == HISTORICAL_MIXED_CHAIN_SELECTOR_IDENTITY,
            "historical selector identity",
        ),
        (not inventory.unclassified_codes, "unclassified mixed-chain candidates"),
        (set(inventory.candidate_codes) >= _REQUIRED_CANARIES, "required canaries"),
    )
    for valid, label in checks:
        if not valid:
            raise ValueError(f"mixed-chain inventory {label} differs")
