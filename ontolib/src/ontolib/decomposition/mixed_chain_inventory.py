"""Content-addressed pre-run evidence for mixed-edge specificity chains."""

from __future__ import annotations

import hashlib
import json
import re
from itertools import pairwise
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ontolib.decomposition.models import SemanticRoute

_SHA256 = r"^[0-9a-f]{64}$"
_RUN_ID = r"^neoplasm-[0-9a-f-]+$"
_REQUIRED_CANARIES = frozenset({"C102570", "C161649", "C175329", "C27381"})


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


class MixedChainInventory(_StrictModel):
    """Exact candidate inventory bound to one source, worklist, and selector."""

    schema_version: int = 1
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
    path.write_text(
        json.dumps(validated.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
    )


def load_mixed_chain_inventory(path: Path) -> MixedChainInventory:
    """Load strict content-addressed mixed-chain inventory JSON."""
    return MixedChainInventory.model_validate_json(path.read_bytes())


def require_mixed_chain_preflight(
    inventory: MixedChainInventory,
    *,
    source_identity: str,
    worklist_identity: str,
    worklist_count: int,
    selector_identity: str,
) -> None:
    """Reject a run unless its mixed-chain inventory is exact and classified."""
    checks = (
        (inventory.source_identity == source_identity, "source identity"),
        (inventory.worklist_identity == worklist_identity, "worklist identity"),
        (inventory.worklist_count == worklist_count, "worklist count"),
        (inventory.selector_identity == selector_identity, "selector identity"),
        (not inventory.unclassified_codes, "unclassified mixed-chain candidates"),
        (set(inventory.candidate_codes) >= _REQUIRED_CANARIES, "required canaries"),
    )
    for valid, label in checks:
        if not valid:
            raise ValueError(f"mixed-chain inventory {label} differs")
