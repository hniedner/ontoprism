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


def _selected_payload(
    target_identity: str, candidate_identity: str, application_identity: str
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "selected-human-specificity-review",
        "question_kind": "most-specific-named-stated-descendant",
        "subject_code": "C2860",
        "role_code": "R103",
        "filler_code": "C12950",
        "question": (
            "For C2860/R103/C12950, does one of the 16 enumerated named stated "
            "descendants of C12950 in NCIt 26.07d provide a better "
            "normal-tissue-origin filler than C12950?"
        ),
        "selected_option": "qualify-global-most-specific-claim",
        "selected_candidate_code": None,
        "enumerated_candidate_count": 16,
        "bounded_conclusion": (
            "None of the 16 enumerated named stated descendants of C12950 in NCIt "
            "26.07d is a better normal-tissue-origin filler for C2860/R103 than "
            "C12950."
        ),
        "effective_outcome": "source-supported",
        "effective_rationale": (
            "Retain C12950 as the source-supported C2860/R103 filler. None of the "
            "16 enumerated named stated descendants of C12950 in NCIt 26.07d is a "
            "better normal-tissue-origin filler for C2860/R103 than C12950. This "
            "bounded comparison does not establish that C12950 is the globally "
            "most-specific available NCIt filler."
        ),
        "global_claim_disposition": "withdrawn-bounded-comparison-not-global-proof",
        "target_artifact_identity": target_identity,
        "candidate_artifact_identity": candidate_identity,
        "applied_policy_identity": application_identity,
        "prior_decision_identity": (
            "6967a9d51bbcb877a727058be93fa70c9c3ac3c7e3a71598d15bd997b3278e27"
        ),
        "transcription": {
            "actor": "software-transcriber",
            "authority": "user-confirmed-in-current-conversation",
            "authorship_claimed": False,
            "confirmation_date": "2026-09-07",
        },
        "proposal_created": False,
        "nci_adoption_inferred": False,
        "software_selected_answer": False,
    }


def _application(module):  # type: ignore[no-untyped-def]
    return module.load_applied_policy_report(GOLDEN / "r103-applied-policy-26.07d.json")


@pytest.mark.unit
def test_selected_specificity_review_transcribes_exact_accountable_human_choice(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    output = tmp_path / "r103-c2860-specificity-selected-26.07d.json"

    selected = module.generate_selected_specificity_review(
        inventory_path=GOLDEN / "r103-source-inventory-26.07d.json",
        candidate_path=GOLDEN / "r103-c12950-candidates-26.07d.json",
        authority_path=GOLDEN / "r103-authority-normalized-26.07d.json",
        application_path=GOLDEN / "r103-applied-policy-26.07d.json",
        revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
        target_path=GOLDEN / "r103-c2860-specificity-target-26.07d.json",
        pending_path=GOLDEN / "r103-c2860-specificity-pending-26.07d.json",
        output_path=output,
    )

    assert selected.model_dump(mode="json") == json.loads(output.read_text())
    assert selected.selected_option == "qualify-global-most-specific-claim"
    assert selected.selected_candidate_code is None
    assert selected.enumerated_candidate_count == 16
    assert selected.effective_outcome == "source-supported"
    assert selected.transcription.model_dump() == {
        "actor": "software-transcriber",
        "authority": "user-confirmed-in-current-conversation",
        "authorship_claimed": False,
        "confirmation_date": "2026-09-07",
    }
    assert selected.proposal_created is False
    assert selected.nci_adoption_inferred is False
    assert "globally most-specific available NCIt tissue-origin filler" not in (
        selected.effective_rationale
    )


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
def test_selected_specificity_review_loads_with_preserved_machine_evidence(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    target = module.build_specificity_review_target(
        inventory=inventory, candidates=candidates, authority=authority
    )
    application = _application(module)
    payload = _selected_payload(
        target.artifact_identity,
        candidates.artifact_identity,
        application.artifact_identity,
    )
    path = tmp_path / "selected-review.json"
    path.write_text(
        json.dumps({**payload, "artifact_identity": _identity(payload)}),
        encoding="utf-8",
    )

    selected = module.load_specificity_review(
        path,
        target=target,
        inventory=inventory,
        candidates=candidates,
        authority=authority,
        application=application,
        revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
    )

    assert selected.status == "selected-human-specificity-review"
    assert selected.target_artifact_identity == target.artifact_identity
    assert selected.candidate_artifact_identity == candidates.artifact_identity


@pytest.mark.unit
def test_selected_specificity_review_rejects_another_target(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    target = module.build_specificity_review_target(
        inventory=inventory, candidates=candidates, authority=authority
    )
    application = _application(module)
    payload = _selected_payload(
        "0" * 64, candidates.artifact_identity, application.artifact_identity
    )
    path = tmp_path / "selected-review.json"
    path.write_text(
        json.dumps({**payload, "artifact_identity": _identity(payload)}),
        encoding="utf-8",
    )

    with pytest.raises(module.R103SpecificityReviewError, match="binding differs"):
        module.load_specificity_review(
            path,
            target=target,
            inventory=inventory,
            candidates=candidates,
            authority=authority,
            application=application,
            revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("question", "Is C12950 globally optimal?"),
        ("selected_option", "affirm-no-better-enumerated-candidate"),
        ("bounded_conclusion", "Unbounded conclusion."),
        ("effective_rationale", "C12950 is globally optimal."),
        ("prior_decision_identity", "0" * 64),
        ("applied_policy_identity", "1" * 64),
        ("proposal_created", True),
        ("nci_adoption_inferred", True),
    ],
)
def test_selected_specificity_review_rejects_self_consistent_tampering(
    tmp_path: Path, field: str, replacement: object
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    inventory, candidates, authority = _inputs(module)
    target = module.build_specificity_review_target(
        inventory=inventory, candidates=candidates, authority=authority
    )
    application = _application(module)
    payload = _selected_payload(
        target.artifact_identity,
        candidates.artifact_identity,
        application.artifact_identity,
    )
    payload[field] = replacement
    path = tmp_path / "changed-selected.json"
    path.write_text(
        json.dumps({**payload, "artifact_identity": _identity(payload)}),
        encoding="utf-8",
    )

    with pytest.raises(module.R103SpecificityReviewError):
        module.load_specificity_review(
            path,
            target=target,
            inventory=inventory,
            candidates=candidates,
            authority=authority,
            application=application,
            revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("artifact_name", "model_name"),
    [
        ("r103-c2860-specificity-target-26.07d.json", "R103SpecificityReviewTarget"),
        (
            "r103-c2860-specificity-pending-26.07d.json",
            "R103PendingSpecificityReview",
        ),
        (
            "r103-c2860-specificity-selected-26.07d.json",
            "R103SelectedSpecificityReview",
        ),
    ],
)
def test_specificity_artifact_models_reject_stale_identity(
    artifact_name: str, model_name: str
) -> None:
    module = importlib.import_module("ontolib.decomposition.r103_specificity_review")
    payload = json.loads((GOLDEN / artifact_name).read_text(encoding="utf-8"))
    payload["artifact_identity"] = "0" * 64

    with pytest.raises(ValueError, match="identity differs"):
        getattr(module, model_name).model_validate_json(json.dumps(payload))


@pytest.mark.unit
def test_selected_specificity_builder_rejects_incomplete_bounded_enumeration() -> None:
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

    with pytest.raises(
        module.R103SpecificityReviewError, match="selected specificity-review inputs"
    ):
        module.build_selected_specificity_review(
            pending=pending,
            target=target,
            candidates=candidates.model_copy(update={"candidate_count": 15}),
            application=_application(module),
        )


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
