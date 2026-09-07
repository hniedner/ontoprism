from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"


@pytest.mark.unit
def test_specificity_review_target_is_bound_only_to_c2860_source_occurrence() -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory = module.load_source_inventory(
        GOLDEN / "r103-source-inventory-26.07d.json"
    )
    candidates = module.load_candidate_artifact(
        GOLDEN / "r103-c12950-candidates-26.07d.json"
    )
    authority = module.load_authority_artifact(
        GOLDEN / "r103-authority-normalized-26.07d.json"
    )

    target = module.build_specificity_review_target(
        inventory=inventory,
        candidates=candidates,
        authority=authority,
    )

    c2860 = inventory.rows[0]
    assert (target.subject_code, target.role_code, target.filler_code) == (
        "C2860",
        "R103",
        "C12950",
    )
    assert target.source_fact_identity == c2860.source_fact_identity
    assert target.source_group_identity == c2860.source_group_identity
    assert target.source_occurrence_identity == c2860.source_occurrence_identity
    assert target.source_row_identity == c2860.row_identity
    assert target.prior_decision_identity == (
        authority.entries[0].effective_decision_identity
    )
    assert target.candidate_artifact_identity == candidates.artifact_identity


def _inputs(module):  # type: ignore[no-untyped-def]
    inventory = module.load_source_inventory(
        GOLDEN / "r103-source-inventory-26.07d.json"
    )
    candidates = module.load_candidate_artifact(
        GOLDEN / "r103-c12950-candidates-26.07d.json"
    )
    authority = module.load_authority_artifact(
        GOLDEN / "r103-authority-normalized-26.07d.json"
    )
    return inventory, candidates, authority


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    ).hexdigest()


@pytest.mark.unit
def test_pending_specificity_review_is_unanswered_actionable_c2860_evidence() -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    target = module.build_specificity_review_target(
        inventory=inventory, candidates=candidates, authority=authority
    )

    pending = module.build_pending_specificity_review(
        target=target,
        inventory=inventory,
        candidates=candidates,
        authority=authority,
        revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
    )

    assert pending.status == "pending-human-specificity-review"
    assert pending.selected_option is None
    assert pending.selected_candidate_code is None
    assert pending.software_selected_answer is False
    assert pending.subject_code == "C2860"
    assert pending.candidate_artifact_identity == candidates.artifact_identity
    assert pending.source_inventory_identity == inventory.artifact_identity
    assert pending.authority_artifact_identity == authority.artifact_identity
    assert pending.prior_decision_identity == (
        authority.entries[0].effective_decision_identity
    )
    assert len(pending.allowed_options) == 3
    encoded = pending.model_dump(mode="json")
    assert "recommendation" not in encoded
    assert "human_decision" not in encoded


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("subject_code", "C3264"),
        ("source_occurrence_identity", "0" * 64),
        ("prior_decision_identity", "1" * 64),
        ("candidate_artifact_identity", "2" * 64),
    ],
)
def test_specificity_target_rejects_self_consistent_wrong_attachment(
    tmp_path: Path, field: str, replacement: str
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    payload = json.loads(
        (GOLDEN / "r103-c2860-specificity-target-26.07d.json").read_text()
    )
    payload[field] = replacement
    payload["artifact_identity"] = _identity(
        {key: value for key, value in payload.items() if key != "artifact_identity"}
    )
    path = tmp_path / "changed-target.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.R103SpecificityReviewError):
        module.load_specificity_review_target(
            path,
            inventory=inventory,
            candidates=candidates,
            authority=authority,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    ["question", "options", "prior-decision", "candidate"],
)
def test_pending_specificity_review_rejects_self_consistent_contract_tampering(
    tmp_path: Path, mutation: str
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    target = module.load_specificity_review_target(
        GOLDEN / "r103-c2860-specificity-target-26.07d.json",
        inventory=inventory,
        candidates=candidates,
        authority=authority,
    )
    payload = json.loads(
        (GOLDEN / "r103-c2860-specificity-pending-26.07d.json").read_text()
    )
    if mutation == "question":
        payload["question"] = "Which assertion should software choose?"
    elif mutation == "options":
        payload["allowed_options"][0]["semantics"] = "Unbounded affirmation."
    elif mutation == "prior-decision":
        payload["prior_decision_identity"] = "0" * 64
    else:
        payload["candidate_artifact_identity"] = "0" * 64
    payload["artifact_identity"] = _identity(
        {key: value for key, value in payload.items() if key != "artifact_identity"}
    )
    path = tmp_path / "changed-pending.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.R103SpecificityReviewError):
        module.load_pending_specificity_review(
            path,
            target=target,
            inventory=inventory,
            candidates=candidates,
            authority=authority,
            revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
        )


@pytest.mark.unit
def test_specificity_artifacts_do_not_reopen_terminal_c3264_exclusion(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    application_module = importlib.import_module(
        "ontolib.decomposition.r103_evidence_application"
    )
    application_path = GOLDEN / "r103-applied-policy-26.07d.json"
    before = application_path.read_bytes()
    application = application_module.load_applied_policy_report(application_path)

    module.generate_specificity_review_artifacts(
        inventory_path=GOLDEN / "r103-source-inventory-26.07d.json",
        candidate_path=GOLDEN / "r103-c12950-candidates-26.07d.json",
        authority_path=GOLDEN / "r103-authority-normalized-26.07d.json",
        revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
        output_directory=tmp_path,
    )

    assert application.c3264_source_retrievable is True
    assert application.c3264_effective_projected is False
    assert application_path.read_bytes() == before


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["missing-subject", "wrong-assertion"])
def test_target_builder_rejects_missing_or_wrong_c2860_source_assertion(
    mutation: str,
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    if mutation == "missing-subject":
        rows = inventory.rows[1:]
        message = "exact C2860 source occurrence is absent"
    else:
        rows = (
            inventory.rows[0].model_copy(update={"filler_code": "C34228"}),
            *inventory.rows[1:],
        )
        message = "C2860 review assertion differs"

    with pytest.raises(module.R103SpecificityReviewError, match=message):
        module.build_specificity_review_target(
            inventory=inventory.model_copy(update={"rows": rows}),
            candidates=candidates,
            authority=authority,
        )


@pytest.mark.unit
def test_target_builder_rejects_authority_for_another_source_occurrence() -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    entries = (
        authority.entries[0].model_copy(
            update={"source_occurrence_identity": "0" * 64}
        ),
        *authority.entries[1:],
    )

    with pytest.raises(
        module.R103SpecificityReviewError, match="target evidence binding differs"
    ):
        module.build_specificity_review_target(
            inventory=inventory,
            candidates=candidates,
            authority=authority.model_copy(update={"entries": entries}),
        )


@pytest.mark.unit
def test_pending_builder_rejects_target_for_another_candidate_artifact() -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    target = module.build_specificity_review_target(
        inventory=inventory, candidates=candidates, authority=authority
    )

    with pytest.raises(
        module.R103SpecificityReviewError, match="specificity-review target differs"
    ):
        module.build_pending_specificity_review(
            target=target.model_copy(update={"candidate_artifact_identity": "0" * 64}),
            inventory=inventory,
            candidates=candidates,
            authority=authority,
            revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
        )
