"""Authorized source-qualified collapse vetoes for #267."""

from __future__ import annotations

import gzip
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ontolib.decomposition import collapse_policy
from ontolib.decomposition.collapse_policy import CollapsePolicyError
from ontolib.decomposition.collapse_policy_generation import (
    _entry,
    _validate_authorized_accounting,
)
from ontolib.decomposition.filler_selection import select_constituents
from ontolib.decomposition.models import RoleRestriction
from ontolib.decomposition.provenance_models import RunFingerprint, RunResumeIdentity
from ontolib.decomposition.r101_conservation import load_r101_conservation_report
from ontolib.decomposition.r101_review import load_r101_decision_registry

_SOURCE = "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
_OTHER_SOURCE = "a" * 64
_GOLDEN = Path(__file__).parent / "golden" / "r101-review-registry-v3-sme.json.gz"


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


@pytest.mark.unit
def test_selector_requires_an_explicit_typed_policy() -> None:
    parameter = inspect.signature(select_constituents).parameters["collapse_policy"]
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
    assert {row.group for row in constituents} == {"op:PrimarySite"}


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
def test_tracked_registry_golden_has_exact_authorized_accounting() -> None:
    with gzip.open(_GOLDEN, "rt", encoding="ascii") as stream:
        payload = json.load(stream)
    outcomes: dict[str, int] = {}
    for row in payload["atomic_decisions"]:
        outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
    assert payload["schema_version"] == 3
    assert payload["status"] == "proposed"
    assert payload["registry_identity"] == (
        "358b42f8279c067fbd0543572073cd5f6887eea0dc74d148483328c02ceb6975"
    )
    assert outcomes == {
        "approved-non-exclusive-coverage": 3288,
        "rejected-retain-broader": 3,
    }
    assert len(payload["atomic_decisions"]) == 3291
    assert len(payload["disease_exceptions"]) == 2800
    assert not any(row["is_exception"] for row in payload["disease_exceptions"])


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


def _rejected_evidence():
    report = load_r101_conservation_report(
        Path(__file__).parent / "golden" / "neoplasm-r101-v4-conservation.json.gz"
    )
    source = next(
        row
        for row in report.occurrences
        if row.occurrence_id
        == "4a8c695aa34264fb7fb95a2afd4d8bfd0c6d49dab5f0313ef682a3b0fba39784"
    )
    frozen = SimpleNamespace(
        disease_code=source.concept_code,
        source_fact_id=source.source_fact_id,
        anchor_code=source.anchor_code,
        structural_path=source.structural_path,
        broader_code=source.filler_code,
        retained_code="C32639",
    )
    decision = SimpleNamespace(
        occurrence_id=source.occurrence_id,
        broader_code=source.filler_code,
        retained_code="C32639",
        atomic_decision_identity="7d72cc8db2ba1f05aea37e74f13048ce74cd59e924244cbe959bec7af3ecce42",
    )
    return report, source, frozen, decision


def _source_namespace(source):
    values = source.model_dump(mode="python")
    target = values["retained_r82_target"]
    values["retained_r82_target"] = SimpleNamespace(**target) if target else None
    return SimpleNamespace(**values)


def _source_occurrence(source, *, occurrence_id: str | None = None):
    return SimpleNamespace(
        occurrence_id=occurrence_id or source.occurrence_id,
        root_code=source.concept_code,
        source_fact_id=source.source_fact_id,
        source_group_id=source.source_group_id,
        anchor_code=source.anchor_code,
        depth=source.depth,
        role_code=source.role_code,
        filler_code=source.filler_code,
        structural_path=source.structural_path,
        member_position=source.member_position,
    )


@pytest.mark.unit
def test_generator_entry_uses_broader_endpoint_and_derived_axis() -> None:
    report, source, frozen, decision = _rejected_evidence()
    result = _entry(
        decision,
        {source.occurrence_id: source},
        {source.occurrence_id: frozen},
        report.source_identity,
    )
    assert (result.broader_code, result.narrower_code, result.normalized_axis) == (
        "C12351",
        "C32639",
        "op:PrimarySite",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda source, frozen, decision: setattr(frozen, "anchor_code", "C999"),
            "stale",
        ),
        (
            lambda source, frozen, decision: setattr(
                decision, "broader_code", "C32639"
            ),
            "broader_code",
        ),
        (
            lambda source, frozen, decision: setattr(
                source, "retained_r82_target", None
            ),
            "routing-derived",
        ),
        (
            lambda source, frozen, decision: setattr(source, "occurrence_id", "0" * 64),
            "recompute",
        ),
    ],
)
def test_generator_entry_refuses_stale_or_conflicting_evidence(
    mutation, message: str
) -> None:
    report, original, frozen, decision = _rejected_evidence()
    source = _source_namespace(original)
    mutation(source, frozen, decision)
    with pytest.raises(CollapsePolicyError, match=message):
        _entry(
            decision,
            {decision.occurrence_id: source},
            {decision.occurrence_id: frozen},
            report.source_identity,
        )


@pytest.mark.unit
def test_policy_qualification_refuses_missing_duplicate_and_provenance_drift() -> None:
    policy = _policy()
    report, source, _frozen, _decision = _rejected_evidence()
    occurrence = _source_occurrence(source)
    with pytest.raises(CollapsePolicyError, match="missing or ambiguous"):
        policy.qualify_live_occurrences((), source_identity=report.source_identity)
    with pytest.raises(CollapsePolicyError, match="duplicate"):
        policy.qualify_live_occurrences(
            cast("Any", (occurrence, occurrence)),
            source_identity=report.source_identity,
        )
    drifted = _source_occurrence(source, occurrence_id="0" * 64)
    with pytest.raises(CollapsePolicyError, match="missing or ambiguous"):
        policy.qualify_live_occurrences(
            cast("Any", (drifted,)), source_identity=report.source_identity
        )


@pytest.mark.unit
def test_generator_accounting_rejects_identity_outcome_and_exception_drift(
    tmp_path: Path,
) -> None:
    registry_json = tmp_path / "registry.json"
    with gzip.open(_GOLDEN, "rb") as source:
        registry_json.write_bytes(source.read())
    registry = load_r101_decision_registry(registry_json)
    _validate_authorized_accounting(registry)

    with pytest.raises(CollapsePolicyError, match="not authorized"):
        _validate_authorized_accounting(
            registry.model_copy(update={"registry_identity": "0" * 64})
        )
    unknown = registry.atomic_decisions[0].model_copy(update={"outcome": "future"})
    with pytest.raises(CollapsePolicyError, match="outcomes"):
        _validate_authorized_accounting(
            registry.model_copy(
                update={"atomic_decisions": (unknown, *registry.atomic_decisions[1:])}
            )
        )
    exception = registry.disease_exceptions[0].model_copy(
        update={"is_exception": True, "rationale": "test refusal"}
    )
    with pytest.raises(CollapsePolicyError, match="disease exceptions"):
        _validate_authorized_accounting(
            registry.model_copy(
                update={
                    "disease_exceptions": (
                        exception,
                        *registry.disease_exceptions[1:],
                    )
                }
            )
        )


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
def test_policy_live_qualification_accepts_all_keys_and_rejects_provenance_drift() -> (
    None
):
    policy = _policy()
    report = load_r101_conservation_report(
        Path(__file__).parent / "golden" / "neoplasm-r101-v4-conservation.json.gz"
    )
    by_id = {row.occurrence_id: row for row in report.occurrences}
    rows = tuple(
        _source_occurrence(by_id[entry.occurrence_id]) for entry in policy.entries
    )
    policy.qualify_live_occurrences(cast("Any", rows), source_identity=_SOURCE)
    drifted = (
        _source_occurrence(
            by_id[policy.entries[0].occurrence_id], occurrence_id="0" * 64
        ),
        *rows[1:],
    )
    with pytest.raises(CollapsePolicyError, match="provenance drifted"):
        policy.qualify_live_occurrences(cast("Any", drifted), source_identity=_SOURCE)
    with pytest.raises(CollapsePolicyError, match="source identity"):
        policy.qualify_live_occurrences(
            cast("Any", rows), source_identity=_OTHER_SOURCE
        )
    collapse_policy.NO_COLLAPSE_VETO_POLICY.qualify_live_occurrences(
        (), source_identity=_OTHER_SOURCE
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


@pytest.mark.unit
def test_generator_entry_refuses_missing_occurrence() -> None:
    report, source, frozen, decision = _rejected_evidence()
    with pytest.raises(CollapsePolicyError, match="missing from evidence"):
        _entry(decision, {}, {source.occurrence_id: frozen}, report.source_identity)
