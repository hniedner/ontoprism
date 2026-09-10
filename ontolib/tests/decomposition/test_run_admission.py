"""Closed contracts for full-run execution identity and admission outcomes."""

from __future__ import annotations

import datetime
from collections.abc import Mapping

import pytest

from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    FreshAdmitted,
    FullRunExecutionIdentity,
    RefusalReason,
    Refused,
    ResumeAdmitted,
    ResumeKind,
)

pytestmark = pytest.mark.unit


def _identity(**updates: object) -> FullRunExecutionIdentity:
    values: dict[str, object] = {
        "schema_version": 1,
        "source_identity": "a" * 64,
        "worklist": ("C1", "C2"),
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": ("Neoplastic Process",),
        "total_limit": None,
        "sample_manifest_identity": None,
        "walker_max_depth": 5,
        "algorithm_version": "decomposition-v3",
        "config_version": "nested-definition-v2",
        "routing_implementation_identity": "b" * 64,
        "collapse_policy_identity": "c" * 64,
        "mixed_chain_inventory_identity": "d" * 64,
        "stage_sequence_identity": RUN_STAGE_SEQUENCE_IDENTITY,
        "output_mode": "file",
        "load_mode": "named-graph",
    }
    values.update(updates)
    return FullRunExecutionIdentity.model_validate(values)


def test_full_run_identity_binds_every_execution_dimension() -> None:
    baseline = _identity()
    mutations = (
        {"source_identity": "e" * 64},
        {"worklist": ("C2", "C1")},
        {"semantic_types": ("Disease or Syndrome",)},
        {"walker_max_depth": 6},
        {"algorithm_version": "decomposition-v4"},
        {"config_version": "nested-definition-v3"},
        {"routing_implementation_identity": "e" * 64},
        {"collapse_policy_identity": "e" * 64},
        {"mixed_chain_inventory_identity": "e" * 64},
        {"output_mode": "none", "load_mode": "none"},
    )
    assert all(
        _identity(**mutation).identity != baseline.identity for mutation in mutations
    )
    with pytest.raises(ValueError, match="stage sequence identity"):
        _identity(stage_sequence_identity="e" * 64)


def test_admission_union_and_reason_vocabulary_are_closed() -> None:
    assert {reason.value for reason in RefusalReason} == {
        "active_run_exists",
        "completed_run_exists",
        "publication_retry_required",
        "identity_mismatch",
        "stage_schema_mismatch",
        "source_drift",
        "ambiguous_compatible_runs",
    }
    assert FreshAdmitted(run_id="fresh").run_id == "fresh"
    assert (
        ResumeAdmitted(run_id="resume", resume_kind=ResumeKind.SEMANTIC).resume_kind
        == ResumeKind.SEMANTIC
    )
    assert (
        Refused(reason=RefusalReason.IDENTITY_MISMATCH).reason
        is RefusalReason.IDENTITY_MISMATCH
    )


def test_execution_identity_rejects_missing_content_digests_and_timestamp() -> None:
    payload = _identity().model_dump()
    payload.pop("mixed_chain_inventory_identity")
    with pytest.raises(ValueError, match="mixed_chain_inventory_identity"):
        FullRunExecutionIdentity.model_validate(payload)
    with pytest.raises(ValueError, match="extra_forbidden"):
        FullRunExecutionIdentity.model_validate(
            _identity().model_dump()
            | {"emitted_at": datetime.datetime.now(datetime.UTC)}
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"semantic_types": ("Z", "A")}, "semantic_types must be sorted"),
        ({"semantic_types": ()}, "semantic_types must contain non-empty values"),
        ({"semantic_types": ("",)}, "semantic_types must contain non-empty values"),
        ({"worklist": ("C1", "C1")}, "worklist must contain unique"),
        ({"worklist": ("",)}, "worklist must contain unique"),
        ({"scope_root": "C2991"}, "neoplasm branch requires scope root C3262"),
        (
            {"branch": "disease", "scope_root": "C3262"},
            "disease branch requires scope root C2991",
        ),
        (
            {"output_mode": "none", "load_mode": "named-graph"},
            "named-graph load requires file output",
        ),
        (
            {"sample_manifest_identity": "e" * 64, "total_limit": 1},
            "sample manifest and total_limit are mutually exclusive",
        ),
    ],
)
def test_execution_identity_rejects_noncanonical_admission_inputs(
    updates: Mapping[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _identity(**updates)


def test_disease_execution_identity_accepts_its_canonical_scope_root() -> None:
    identity = _identity(branch="disease", scope_root="C2991")

    assert identity.branch == "disease"
    assert identity.scope_root == "C2991"
