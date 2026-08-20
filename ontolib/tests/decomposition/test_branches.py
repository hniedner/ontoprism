"""Executable contracts for hierarchy scope and algorithm dispatch."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ontolib.decomposition import axes
from ontolib.decomposition.branches import (
    DecompositionAlgorithm,
    DecompositionBranch,
    branch_spec,
    parse_branch,
)
from ontolib.decomposition.provenance_models import RunFingerprint, RunResumeIdentity

pytestmark = pytest.mark.unit


def test_neoplasm_and_disease_are_nested_scopes_with_one_algorithm() -> None:
    neoplasm = branch_spec(DecompositionBranch.NEOPLASM)
    disease = branch_spec(DecompositionBranch.DISEASE)

    assert neoplasm.root_code == "C3262"
    assert disease.root_code == "C2991"
    assert neoplasm.root_code != disease.root_code
    assert neoplasm.algorithm is DecompositionAlgorithm.AXIS_QUALIFIED
    assert disease.algorithm is neoplasm.algorithm
    assert neoplasm.semantic_types == tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES))
    assert disease.semantic_types == neoplasm.semantic_types
    assert neoplasm.scope_version == disease.scope_version
    assert neoplasm.algorithm_version == disease.algorithm_version == "decomposition-v4"


def test_disease_is_supported_but_regimen_remains_unimplemented() -> None:
    assert parse_branch("disease") is DecompositionBranch.DISEASE
    with pytest.raises(ValueError, match="regimen remains unimplemented"):
        parse_branch("regimen")


def test_fingerprint_separates_hierarchy_scope_from_shared_algorithm() -> None:
    fingerprint = RunFingerprint(
        source_identity="a" * 64,
        collapse_policy_identity="0" * 64,
        branch="disease",
        scope_root="C2991",
        scope_version="stated-genus-subclass-v1",
        semantic_types=tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES)),
        worklist=("C3262",),
        algorithm_version="decomposition-v2",
        config_version="complete-definition-v1",
        walker_max_depth=5,
        output_mode="none",
        load_mode="none",
        emitted_at=datetime(2026, 7, 30, tzinfo=UTC),
    )

    resume = RunResumeIdentity.from_fingerprint(fingerprint)

    assert fingerprint.schema_version == 4
    assert resume.branch == "disease"
    assert resume.scope_root == "C2991"
    assert resume.scope_version == "stated-genus-subclass-v1"
