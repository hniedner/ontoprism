"""Operationally allowlisted source-qualified collapse vetoes for #267."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ontolib.decomposition import collapse_policy
from ontolib.decomposition.collapse_policy import CollapsePolicyError
from ontolib.decomposition.filler_selection import (
    _reduce_routed_plan,
    build_routed_plan,
)
from ontolib.decomposition.models import RoleRestriction
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    RunFingerprint,
    RunResumeIdentity,
)

_SOURCE = "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
_OTHER_SOURCE = "a" * 64


def _policy_module():
    return collapse_policy


def _c5292_roles() -> list[RoleRestriction]:
    return [
        RoleRestriction("R101", "C12351", anchoring_genus="C4807"),
        RoleRestriction("R101", "C12439", anchoring_genus="C170814"),
        RoleRestriction("R101", "C12512", anchoring_genus="C7048"),
        RoleRestriction("R101", "C32639", anchoring_genus="C5292"),
    ]


def _policy(source_identity: str = _SOURCE):
    module = _policy_module()
    entries = tuple(
        module.CollapseVeto(
            source_identity=source_identity,
            concept_code="C5292",
            role_code="R101",
            anchoring_genus=anchor,
            normalized_axis="op:PrimarySite",
            broader_code=broader,
            narrower_code="C32639",
            occurrence_id=occurrence,
            atomic_decision_identity=atomic,
        )
        for broader, anchor, occurrence, atomic in (
            (
                "C12351",
                "C4807",
                "4a8c695aa34264fb7fb95a2afd4d8bfd0c6d49dab5f0313ef682a3b0fba39784",
                "7d72cc8db2ba1f05aea37e74f13048ce74cd59e924244cbe959bec7af3ecce42",
            ),
            (
                "C12439",
                "C170814",
                "febec3e451327eebaaf06bfc9ef3a50621f37ca9d069fb4f34c5ba94c4381977",
                "c100c0c50e22db0c1f89c2959e105bcd82d6ef66538e1085c92659f543b69821",
            ),
            (
                "C12512",
                "C7048",
                "a426bd70ee9da26217d19ccf1948d185cbdbb1ec3185addaf1bd0aaac181da85",
                "56b693f87d806d8fc8de2e565c5e0ba0818bca7cb9f820be6af1303c092db0da",
            ),
        )
    )
    return module.CollapseVetoPolicy.create(
        registry_identity="358b42f8279c067fbd0543572073cd5f6887eea0dc74d148483328c02ceb6975",
        entries=entries,
    )


def select_constituents(
    restrictions: list[RoleRestriction],
    is_ancestor: Any,
    **kwargs: Any,
) -> list[Any]:
    plan = build_routed_plan(
        restrictions,
        semantic_type_of=kwargs.pop("semantic_type_of", None),
        parent_morphologies=kwargs.pop("parent_morphologies", ()),
        concept_code=kwargs.pop("concept_code", None),
        source_identity=kwargs.pop("source_identity"),
        collapse_policy=kwargs.pop("collapse_policy"),
    )
    return list(
        _reduce_routed_plan(
            plan,
            is_ancestor,
            is_part_of=kwargs.pop("is_part_of", None),
        ).constituents
    )


@pytest.mark.unit
def test_selector_requires_an_explicit_typed_policy() -> None:
    parameter = inspect.signature(build_routed_plan).parameters["collapse_policy"]
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.unit
def test_exact_c5292_veto_retains_broaders_as_one_unresolved_group() -> None:
    broader = {("C12351", "C32639"), ("C12439", "C32639"), ("C12512", "C32639")}

    constituents = select_constituents(
        _c5292_roles(),
        lambda parent, child: (parent, child) in broader,
        concept_code="C5292",
        source_identity=_SOURCE,
        collapse_policy=_policy(),
    )

    assert {row.filler_code for row in constituents} == {
        "C12351",
        "C12439",
        "C12512",
        "C32639",
    }
    assert {row.axis for row in constituents} == {"op:PrimarySite"}
    assert all(row.needs_review for row in constituents)
    assert all(row.axis_ambiguous for row in constituents)


@pytest.mark.unit
def test_veto_adds_broaders_without_erasing_normal_r101_region_resolution() -> None:
    roles = [
        *_c5292_roles(),
        RoleRestriction("R101", "C12789", anchoring_genus="C4656"),
        RoleRestriction("R101", "C32292", anchoring_genus="C4959"),
    ]
    semantic_types = {
        "C12789": "Body Part, Organ, or Organ Component",
        "C32292": "Anatomical Structure",
        "C32639": "Anatomical Structure",
        "C12351": "Anatomical Structure",
        "C12439": "Body Part, Organ, or Organ Component",
        "C12512": "Anatomical Structure",
    }

    def ancestors(parent: str, child: str) -> bool:
        return child == "C32639" and parent in {"C12351", "C12439", "C12512"}

    baseline = select_constituents(
        roles,
        ancestors,
        concept_code="C5292",
        parent_morphologies=("C4959",),
        semantic_type_of=semantic_types.get,
        source_identity=_SOURCE,
        collapse_policy=collapse_policy.NO_COLLAPSE_VETO_POLICY,
    )
    with pytest.raises(CollapsePolicyError, match=r"axis.*drift"):
        select_constituents(
            roles,
            ancestors,
            concept_code="C5292",
            parent_morphologies=("C4959",),
            semantic_type_of=semantic_types.get,
            source_identity=_SOURCE,
            collapse_policy=_policy(),
        )
    baseline_pairs = {(row.axis, row.filler_code) for row in baseline}

    assert ("op:AssociatedRegion", "C32292") in baseline_pairs


@pytest.mark.unit
def test_single_protected_axis_value_is_not_grouped_as_ambiguous() -> None:
    full_policy = _policy()
    c12351 = next(
        entry for entry in full_policy.entries if entry.broader_code == "C12351"
    )
    policy = collapse_policy.CollapseVetoPolicy.create(
        registry_identity=full_policy.registry_identity,
        entries=(c12351,),
    )
    roles = [
        RoleRestriction("R101", "C12351", anchoring_genus="C4807"),
        RoleRestriction("R101", "C32639", anchoring_genus="C5292"),
    ]
    with pytest.raises(CollapsePolicyError, match=r"axis.*drift"):
        select_constituents(
            roles,
            lambda parent, child: (parent, child) == ("C12351", "C32639"),
            concept_code="C5292",
            semantic_type_of=lambda _code: "Anatomical Structure",
            source_identity=_SOURCE,
            collapse_policy=policy,
        )


@pytest.mark.unit
def test_source_identity_anchor_and_unknown_source_are_load_bearing() -> None:
    broader = {("C12351", "C32639"), ("C12439", "C32639"), ("C12512", "C32639")}
    mismatched_anchor = [
        RoleRestriction(row.role_code, row.filler_code, anchoring_genus="C999")
        if row.filler_code == "C12351"
        else row
        for row in _c5292_roles()
    ]
    matched = select_constituents(
        mismatched_anchor,
        lambda parent, child: (parent, child) in broader,
        concept_code="C5292",
        source_identity=_SOURCE,
        collapse_policy=_policy(),
    )
    assert "C12351" not in {row.filler_code for row in matched}

    with pytest.raises(ValueError, match="source identity"):
        select_constituents(
            _c5292_roles(),
            lambda parent, child: (parent, child) in broader,
            concept_code="C5292",
            source_identity=_OTHER_SOURCE,
            collapse_policy=_policy(),
        )


@pytest.mark.unit
def test_veto_requires_narrower_on_same_routed_axis() -> None:
    roles = [row for row in _c5292_roles() if row.filler_code != "C32639"]
    roles.append(RoleRestriction("R101", "C999", anchoring_genus="C5292"))
    constituents = select_constituents(
        roles,
        lambda parent, child: (
            parent in {"C12351", "C12439", "C12512"} and child == "C999"
        ),
        concept_code="C5292",
        source_identity=_SOURCE,
        collapse_policy=_policy(),
    )
    assert {row.filler_code for row in constituents} == {"C999"}


@pytest.mark.unit
def test_empty_policy_has_double_fidelity_with_ordinary_collapse() -> None:
    module = _policy_module()
    roles = _c5292_roles()

    def ancestors(parent: str, child: str) -> bool:
        return parent != child and child == "C32639"

    explicit = select_constituents(
        roles,
        ancestors,
        concept_code="C5292",
        source_identity=_SOURCE,
        collapse_policy=module.NO_COLLAPSE_VETO_POLICY,
    )
    assert [row.filler_code for row in explicit] == ["C32639"]

    unrelated = select_constituents(
        roles,
        ancestors,
        concept_code="C999",
        source_identity=_SOURCE,
        collapse_policy=_policy(),
    )
    assert unrelated == explicit


@pytest.mark.unit
def test_packaged_policy_loads_via_importlib_resources() -> None:
    module = _policy_module()
    policy = module.load_packaged_collapse_veto_policy()
    assert policy == _policy()
    assert len(policy.entries) == 3


@pytest.mark.unit
def test_policy_identity_is_required_by_fingerprint_and_resume_identity() -> None:
    values = {
        "schema_version": 4,
        "source_identity": _SOURCE,
        "collapse_policy_identity": _policy().policy_identity,
        "routing_implementation_identity": "1" * 64,
        "mixed_chain_inventory_identity": "2" * 64,
        "stage_sequence_identity": RUN_STAGE_SEQUENCE_IDENTITY,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": ("Neoplastic Process",),
        "worklist": ("C5292",),
        "total_limit": None,
        "sample_manifest_identity": None,
        "algorithm_version": "nested-definition-v4",
        "config_version": "axis-qualified-v3",
        "walker_max_depth": 7,
        "output_mode": "none",
        "load_mode": "none",
        "emitted_at": datetime(2026, 8, 20, tzinfo=UTC),
    }
    fingerprint = RunFingerprint.model_validate(values)
    resume = RunResumeIdentity.from_fingerprint(fingerprint)
    assert resume.collapse_policy_identity == fingerprint.collapse_policy_identity

    changed = resume.model_copy(update={"collapse_policy_identity": "f" * 64})
    assert changed != resume
    old_schema = {**values, "schema_version": 2}
    with pytest.raises(ValueError, match="schema_version"):
        RunFingerprint.model_validate(old_schema)


@pytest.mark.unit
def test_registry_gzip_is_deterministic_and_uses_zero_mtime(tmp_path: Path) -> None:
    module = _policy_module()
    first = tmp_path / "first.json.gz"
    second = tmp_path / "second.json.gz"
    payload = {"schema_version": 3, "status": "proposed"}
    module.write_canonical_registry_gzip(first, payload)
    module.write_canonical_registry_gzip(second, payload)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes()[4:8] == b"\x00\x00\x00\x00"


@pytest.mark.unit
@pytest.mark.parametrize("failed_output", ["registry", "policy"])
def test_policy_artifact_pair_failure_restores_both_existing_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_output: str
) -> None:
    registry_path = tmp_path / "registry.json.gz"
    policy_path = tmp_path / "policy.json"
    registry_path.write_bytes(b"old-registry")
    policy_path.write_bytes(b"old-policy")
    real_replace = collapse_policy.os.replace
    failed_path = registry_path if failed_output == "registry" else policy_path

    def fail_policy_publish(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == failed_path:
            raise OSError("injected artifact publish failure")
        real_replace(source, destination)

    monkeypatch.setattr(collapse_policy.os, "replace", fail_policy_publish)
    with pytest.raises(OSError, match="injected artifact"):
        collapse_policy.write_collapse_policy_artifacts(
            registry_path,
            policy_path,
            {"schema_version": 3},
            _policy(),
        )

    assert registry_path.read_bytes() == b"old-registry"
    assert policy_path.read_bytes() == b"old-policy"


@pytest.mark.unit
def test_policy_second_staging_failure_leaves_no_partial_or_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path = tmp_path / "registry.json.gz"
    policy_path = tmp_path / "policy.json"
    real_stage = collapse_policy._stage_write

    def fail_second_stage(path: Path, content: bytes) -> str:
        if path == policy_path:
            raise OSError("injected second staging failure")
        return real_stage(path, content)

    monkeypatch.setattr(collapse_policy, "_stage_write", fail_second_stage)
    with pytest.raises(OSError, match="second staging"):
        collapse_policy.write_collapse_policy_artifacts(
            registry_path,
            policy_path,
            {"schema_version": 3},
            _policy(),
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_policy_rejects_duplicate_keys_and_duplicate_live_tuple() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="canonical and unique"):
        collapse_policy.CollapseVetoPolicy.create(
            registry_identity=policy.registry_identity,
            entries=(policy.entries[0], policy.entries[0]),
        )
    duplicate = [*_c5292_roles(), _c5292_roles()[0]]
    with pytest.raises(CollapsePolicyError, match="duplicate live"):
        select_constituents(
            duplicate,
            lambda parent, child: child == "C32639" and parent != child,
            concept_code="C5292",
            source_identity=_SOURCE,
            collapse_policy=policy,
        )
    with pytest.raises(CollapsePolicyError, match="concept code"):
        select_constituents(
            _c5292_roles(),
            lambda _parent, _child: False,
            concept_code=None,
            source_identity=_SOURCE,
            collapse_policy=policy,
        )


@pytest.mark.unit
def test_policy_model_rejects_wrong_identity_and_noncanonical_order() -> None:
    policy = _policy()
    payload = policy.model_dump(mode="python")
    with pytest.raises(ValueError, match="identity differs"):
        collapse_policy.CollapseVetoPolicy.model_validate(
            {**payload, "policy_identity": "0" * 64}
        )
    with pytest.raises(ValueError, match="canonical and unique"):
        collapse_policy.CollapseVetoPolicy.model_validate(
            {**payload, "entries": tuple(reversed(policy.entries))}
        )
