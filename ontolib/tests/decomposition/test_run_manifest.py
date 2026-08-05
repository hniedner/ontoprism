"""Behavioral contracts for immutable decomposition-run identities."""

from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from ontolib.decomposition.provenance_models import RunFingerprint, RunResumeIdentity


def _fingerprint(**updates: object) -> RunFingerprint:
    values: dict[str, object] = {
        "source_identity": "a" * 64,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": ("Disease or Syndrome", "Neoplastic Process"),
        "worklist": ("C1", "C2"),
        "total_limit": 2,
        "algorithm_version": "decomposition-v1",
        "config_version": "axes-v1",
        "walker_max_depth": 5,
        "output_mode": "file",
        "load_mode": "none",
        "emitted_at": datetime.datetime(2026, 7, 29, 12, 0, tzinfo=datetime.UTC),
    }
    values.update(updates)
    return RunFingerprint.model_validate(values)


@pytest.mark.unit
def test_fingerprint_is_canonical_and_binds_every_run_dimension() -> None:
    original = _fingerprint()
    equivalent = RunFingerprint.model_validate_json(original.model_dump_json())

    assert equivalent.identity == original.identity
    assert (
        original.identity
        == "9707720a292ac5487abd396d4c0736c3bd8409e23ae67414a7ac664062a0c38f"
    )
    assert len(original.identity) == 64

    mutations = (
        {"source_identity": "b" * 64},
        {"branch": "disease", "scope_root": "C2991"},
        {"semantic_types": ("Neoplastic Process",)},
        {"worklist": ("C2", "C1")},
        {"total_limit": None},
        {"algorithm_version": "decomposition-v2"},
        {"config_version": "axes-v2"},
        {"walker_max_depth": 6},
        {"output_mode": "none"},
        {"load_mode": "named-graph"},
        {"emitted_at": datetime.datetime(2026, 7, 30, 12, 0, tzinfo=datetime.UTC)},
    )
    assert all(
        _fingerprint(**change).identity != original.identity for change in mutations
    )


@pytest.mark.unit
def test_sample_fingerprint_binds_manifest_identity_and_resume_contract() -> None:
    sample = _fingerprint(
        schema_version=3,
        total_limit=None,
        sample_manifest_identity="b" * 64,
    )
    different_sample = _fingerprint(
        schema_version=3,
        total_limit=None,
        sample_manifest_identity="c" * 64,
    )

    assert sample.identity != different_sample.identity
    resume = RunResumeIdentity.from_fingerprint(sample)
    assert resume.schema_version == 3
    assert resume.sample_manifest_identity == "b" * 64


@pytest.mark.unit
def test_named_graph_load_requires_a_file_output_for_run_and_resume() -> None:
    with pytest.raises(ValidationError, match="named-graph load requires file output"):
        _fingerprint(output_mode="none", load_mode="named-graph")

    resume = RunResumeIdentity.from_fingerprint(_fingerprint())
    with pytest.raises(ValidationError, match="named-graph load requires file output"):
        RunResumeIdentity.model_validate(
            resume.model_dump() | {"output_mode": "none", "load_mode": "named-graph"}
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "updates",
    [
        {"source_identity": "not-a-source"},
        {"branch": "disease"},
        {"branch": "regimen"},
        {"scope_root": "C2991"},
        {"scope_root": "C9999"},
        {"scope_version": "stated-genus-subclass-v2"},
        {"worklist": ("C1", "C1")},
        {"worklist": ("C1", "")},
        {"semantic_types": ()},
        {"semantic_types": ("Neoplastic Process", "Disease or Syndrome")},
        {"semantic_types": ("Disease or Syndrome", "Disease or Syndrome")},
        {"semantic_types": ("Disease or Syndrome", "")},
        {"walker_max_depth": 0},
        {"total_limit": 0},
        {"schema_version": 3},
        {"sample_manifest_identity": "b" * 64},
        {
            "schema_version": 3,
            "total_limit": None,
            "sample_manifest_identity": "not-a-digest",
        },
        {"emitted_at": datetime.datetime(2026, 7, 29, 12, 0)},
        {"unknown_field": "ignored-proof"},
    ],
)
def test_fingerprint_rejects_malformed_or_ambiguous_identity(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _fingerprint(**updates)
