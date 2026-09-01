"""Behavioral contract for the active seven-concept enhanced-NCIt showcase."""

from __future__ import annotations

import hashlib
import importlib
import json
from copy import deepcopy
from typing import cast

import pytest
from pydantic import ValidationError


def _showcase():
    return importlib.import_module("ontolib.decomposition.enhanced_showcase")


def _recompute_decision_set_identity(module, payload: dict[str, object]) -> None:
    identity_payload = {
        key: value for key, value in payload.items() if key != "decision_set_identity"
    }
    payload["decision_set_identity"] = hashlib.sha256(
        module._canonical(identity_payload)
    ).hexdigest()


@pytest.mark.unit
def test_packaged_decisions_are_complete_strict_and_semantically_bound() -> None:
    module = _showcase()
    policy = module.load_packaged_showcase_decision_set()

    assert policy.representation == "enhanced-ncit-showcase"
    assert policy.source_release == "26.07d"
    assert tuple(concept.code for concept in policy.concepts) == (
        "C100054",
        "C102870",
        "C198031",
        "C27262",
        "C35756",
        "C4791",
        "C6135",
    )
    assert len(policy.decision_set_identity) == 64
    assert all(concept.decisions for concept in policy.concepts)
    assert {
        decision.disposition
        for concept in policy.concepts
        for decision in concept.decisions
    } == {
        "include",
        "exclude",
        "unresolved-visible",
    }

    by_key = {
        (concept.code, decision.axis, decision.filler): decision
        for concept in policy.concepts
        for decision in concept.decisions
    }
    assert by_key[("C27262", "op:AssociatedRegion", "C41165")].disposition == "exclude"
    assert by_key[("C102870", "op:PrimarySite", "C12404")].disposition == "include"
    assert (
        by_key[("C102870", "op:Morphology", "C121619")].disposition
        == "unresolved-visible"
    )
    assert by_key[("C6135", "op:NormalTissueOrigin", "C33782")].disposition == "exclude"
    assert by_key[("C4791", "op:PrimarySite", "C12869")].disposition == "include"
    assert (
        by_key[("C100054", "op:ClinicalFinding", "C36027")].authority
        == "locally-approved"
    )
    assert (
        by_key[("C100054", "op:ClinicalFinding", "C8326")].authority
        == "locally-approved"
    )
    assert len(policy.concept("C100054").groups) == 7
    marrow = by_key[("C198031", "op:PrimarySite", "C12431")]
    assert marrow.disposition == "exclude"
    assert "systemic" in marrow.rationale.lower()
    assert by_key[("C198031", "op:StageValue", "C198022")].disposition == "exclude"
    correction = by_key[("C35756", "op:ClinicalFinding", "C9432")]
    assert correction.authority == "project-provisional"
    assert correction.support == ("project-inference", "peer-reviewed-supported")
    assert correction.source_occurrence_ids == ()
    assert correction.limitations
    assert by_key[("C35756", "op:ClinicalFinding", "C3331")].disposition == "include"
    assert (
        by_key[("C35756", "op:StageValue", "C27978")].disposition
        == "unresolved-visible"
    )


@pytest.mark.unit
def test_decision_authority_and_evidence_support_are_separate_and_fail_closed() -> None:
    module = _showcase()
    policy = module.load_packaged_showcase_decision_set()
    payload = policy.model_dump(mode="json")
    decision = payload["concepts"][0]["decisions"][0]
    decision["authority"] = "project-provisional"
    decision["support"] = ["peer-reviewed-supported"]
    decision["limitations"] = " "
    _recompute_decision_set_identity(module, payload)

    with pytest.raises(ValidationError) as missing_limitations:
        module.ShowcaseDecisionSet.model_validate_json(json.dumps(payload))
    assert str(missing_limitations.value.errors()[0].get("ctx", {}).get("error")) == (
        "project-provisional include requires project-inference and limitations"
    )

    decision["limitations"] = "Bounded limitation."
    _recompute_decision_set_identity(module, payload)
    with pytest.raises(ValidationError) as missing_inference:
        module.ShowcaseDecisionSet.model_validate_json(json.dumps(payload))
    assert str(missing_inference.value.errors()[0].get("ctx", {}).get("error")) == (
        "project-provisional include requires project-inference and limitations"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"source_occurrence_ids": ()}, "source support and binding"),
        (
            {"support": ("source-stated",)},
            "peer-reviewed-supported",
        ),
    ],
)
def test_locally_approved_decisions_require_bound_peer_reviewed_source_evidence(
    updates: dict[str, object], message: str
) -> None:
    module = _showcase()
    approved = next(
        decision
        for concept in module.load_packaged_showcase_decision_set().concepts
        for decision in concept.decisions
        if decision.authority == "locally-approved"
    )
    payload = approved.model_dump(mode="python")
    if "support" in updates:
        updates["support"] = tuple(
            module.EvidenceSupport(item)
            for item in cast("tuple[str, ...]", updates["support"])
        )
    payload.update(updates)

    with pytest.raises(ValueError, match=message):
        module.ShowcaseDecision.model_validate(payload)


@pytest.mark.unit
def test_nci_adopted_is_not_an_available_showcase_authority() -> None:
    module = _showcase()
    assert "nci-adopted" not in {
        authority.value for authority in module.DecisionAuthority
    }


@pytest.mark.unit
def test_overlay_changes_only_effective_constituents_and_binds_identity() -> None:
    module = _showcase()
    policy = module.load_packaged_showcase_decision_set()
    base = (
        module.ShowcaseConstituent(
            axis="op:NormalTissueOrigin",
            filler="C33782",
            label="Thyroid Gland Follicle",
        ),
        module.ShowcaseConstituent(
            axis="op:PrimarySite", filler="C12400", label="Thyroid Gland"
        ),
    )
    view = module.build_showcase_view("C6135", "b" * 64, base, policy=policy)

    assert view.representation == "enhanced-ncit-showcase"
    assert view.base_constituents == base
    assert ("op:NormalTissueOrigin", "C33782") not in {
        (item.axis, item.filler) for item in view.effective_constituents
    }
    assert ("op:PrimarySite", "C12400") in {
        (item.axis, item.filler) for item in view.effective_constituents
    }
    assert view.decisions
    assert view.unresolved_visible
    assert len(view.effective_representation_identity) == 64
    assert "not scientific publication" in view.banner
    assert "NCI adoption" in view.banner


@pytest.mark.unit
def test_decision_graph_serialization_and_scoped_recoverable_replacement() -> None:
    module = _showcase()
    policy = module.load_packaged_showcase_decision_set()
    turtle = module.serialize_showcase_decision_graph(policy)
    assert "ShowcaseDecision" in turtle
    staging = module.showcase_staging_graph_iri("run-127")
    update = module.build_showcase_replacement_update(staging)
    assert module.SHOWCASE_GRAPH_IRI in update
    assert staging in update
    assert "ncit_decomposed" not in update
    assert "CLEAR GRAPH" in update
    assert "ADD GRAPH" in update
    assert "DROP GRAPH" in update


@pytest.mark.unit
def test_exact_showcase_graph_rejects_a_row_beyond_the_closure_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _showcase()
    policy = module.load_packaged_showcase_decision_set()
    monkeypatch.setattr(
        module,
        "serialize_showcase_decision_graph",
        lambda _policy: "<urn:s> <urn:p> <urn:o> .",
    )
    rows = [{"s": "urn:s", "p": "urn:p", "o": "urn:o"}]
    rows.append(rows[0].copy())
    assert module._expected_showcase_graph_rows(policy) == {("urn:s", "urn:p", "urn:o")}

    with pytest.raises(
        module.ShowcasePolicyError,
        match=r"^stored showcase graph exceeds closure budgets$",
    ):
        module.require_exact_showcase_graph(rows, policy)


@pytest.mark.unit
def test_exact_showcase_graph_rejects_bytes_over_budget_within_closure_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _showcase()
    policy = module.load_packaged_showcase_decision_set()
    oversized_value = "x" * module._SHOWCASE_GRAPH_BYTE_BUDGET
    monkeypatch.setattr(
        module,
        "serialize_showcase_decision_graph",
        lambda _policy: f'<urn:s> <urn:p> """{oversized_value}""" .',
    )
    rows = [
        {
            "s": "urn:s",
            "p": "urn:p",
            "o": oversized_value,
        }
    ]
    assert module._expected_showcase_graph_rows(policy) == {
        ("urn:s", "urn:p", oversized_value)
    }
    assert len(rows) == 1

    with pytest.raises(
        module.ShowcasePolicyError,
        match=r"^stored showcase graph exceeds closure budgets$",
    ):
        module.require_exact_showcase_graph(rows, policy)


@pytest.mark.unit
def test_showcase_and_packaged_r101_policy_are_provably_orthogonal() -> None:
    module = _showcase()
    assert (
        module.qualify_showcase_orthogonality(
            module.load_packaged_showcase_decision_set()
        )
        is None
    )

    policy = module.load_packaged_showcase_decision_set()
    with pytest.raises(module.ShowcasePolicyError, match="overlaps R101 collapse-veto"):
        module.qualify_showcase_orthogonality(policy, collapse_concept_roots={"C6135"})
    with pytest.raises(module.ShowcasePolicyError, match="overlaps R101 collapse-veto"):
        module.qualify_showcase_orthogonality(
            policy,
            collapse_concept_roots=set(),
            collapse_runtime_keys={("C6135", "op:NormalTissueOrigin", "C33782")},
        )


@pytest.mark.unit
def test_showcase_occurrence_overlap_reject_branch_is_live() -> None:
    module = _showcase()
    policy = module.load_packaged_showcase_decision_set()
    occurrence = next(
        occurrence
        for concept in policy.concepts
        for decision in concept.decisions
        for occurrence in decision.source_occurrence_ids
    )

    with pytest.raises(
        module.ShowcasePolicyError,
        match=r"^enhanced showcase overlaps R101 collapse-veto policy$",
    ):
        module.qualify_showcase_orthogonality(
            policy,
            collapse_concept_roots=set(),
            collapse_runtime_keys=set(),
            collapse_occurrences={occurrence},
        )


@pytest.mark.unit
def test_malformed_showcase_authority_partition_and_storage_inputs_fail_closed() -> (
    None
):
    module = _showcase()
    policy = module.load_packaged_showcase_decision_set()
    source_decision = next(
        decision
        for concept in policy.concepts
        for decision in concept.decisions
        if decision.authority == "source-stated"
    )
    decision_payload = source_decision.model_dump(mode="python")

    duplicate_support = deepcopy(decision_payload)
    duplicate_support["support"] = (
        module.EvidenceSupport.SOURCE_STATED,
        module.EvidenceSupport.SOURCE_STATED,
    )
    with pytest.raises(ValueError, match="non-empty and unique"):
        module.ShowcaseDecision.model_validate(duplicate_support)

    unsupported_include = deepcopy(decision_payload)
    unsupported_include.update(
        disposition=module.Disposition.INCLUDE,
        authority=module.DecisionAuthority.LOCALLY_APPROVED,
        support=(module.EvidenceSupport.PEER_REVIEWED_NOT_FOUND,),
        source_occurrence_ids=(),
    )
    with pytest.raises(ValueError, match="cannot solely support inclusion"):
        module.ShowcaseDecision.model_validate(unsupported_include)

    missing_source_binding = deepcopy(decision_payload)
    missing_source_binding["source_occurrence_ids"] = ()
    with pytest.raises(ValueError, match="requires source support and binding"):
        module.ShowcaseDecision.model_validate(missing_source_binding)

    invalid_occurrence = deepcopy(decision_payload)
    invalid_occurrence["source_occurrence_ids"] = ("g" * 64,)
    with pytest.raises(ValueError, match="SHA-256 identity"):
        module.ShowcaseDecision.model_validate(invalid_occurrence)

    concept_payload = policy.concepts[0].model_dump(mode="python")
    noncanonical = deepcopy(concept_payload)
    noncanonical["decisions"] = tuple(reversed(noncanonical["decisions"]))
    with pytest.raises(ValueError, match="canonical and unique"):
        module.ShowcaseConceptPolicy.model_validate(noncanonical)

    wrong_owner = deepcopy(concept_payload)
    wrong_owner["decisions"][0]["candidate_id"] = "C999-P1"
    with pytest.raises(ValueError, match="different concept"):
        module.ShowcaseConceptPolicy.model_validate(wrong_owner)

    invalid_group = deepcopy(concept_payload)
    invalid_group["groups"] = (("C999-P1",),)
    with pytest.raises(ValueError, match="groups must reference candidates"):
        module.ShowcaseConceptPolicy.model_validate(invalid_group)

    incomplete_set = policy.model_dump(mode="python")
    incomplete_set["concepts"] = incomplete_set["concepts"][:-1]
    with pytest.raises(ValueError, match="exactly seven canonical concepts"):
        module.ShowcaseDecisionSet.model_validate(incomplete_set)

    wrong_identity = policy.model_dump(mode="python")
    wrong_identity["decision_set_identity"] = "0" * 64
    with pytest.raises(ValueError, match="identity differs"):
        module.ShowcaseDecisionSet.model_validate(wrong_identity)

    with pytest.raises(module.ShowcasePolicyError, match="outside the enhanced-NCIt"):
        policy.concept("C999")
    with pytest.raises(ValueError, match="outside its scoped namespace"):
        module.build_showcase_replacement_update("https://example.org/staging/run")
    with pytest.raises(
        module.ShowcasePolicyError, match="stored showcase decision graph"
    ):
        module.validate_showcase_rows([{"unexpected": "value"}])
