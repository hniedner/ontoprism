"""Behavioral contracts for the C3262 corpus acceptance candidate."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError
from scripts.research.group_review_packet import load_historical_group_review_packet

from ontolib.decomposition import corpus_acceptance as corpus_acceptance_module
from ontolib.decomposition import vocab
from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.corpus_acceptance import (
    AcceptedHumanAcceptanceDecision,
    CorpusAcceptanceCandidate,
    CorpusAcceptanceContent,
    CorpusAcceptanceValidationError,
    ExcludedPairChange,
    PendingHumanAcceptanceDecision,
    PublicationDryRunEvidence,
    ReviewRequiredEffectiveExclusion,
    build_review_required_exclusions,
    classify_corpus_delta,
    dry_run_corpus_publication,
    finalize_corpus_acceptance_candidate,
    require_publication_authorization,
)
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    CompletedRunForEvidence,
    RunFingerprint,
)
from ontolib.decomposition.r101_conservation import (
    ClassifiedNonR101Delta,
    NonR101MetadataDelta,
    load_r101_conservation_report,
)

GOLDEN = Path(__file__).with_name("golden")
ROOT = Path(__file__).parents[3]
SHA = "a" * 64


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


@pytest.fixture(scope="module")
def exclusions():  # type: ignore[no-untyped-def]
    return build_review_required_exclusions(
        ROOT / "evidence/group-review-packet-26.07d-schema3.json",
        ROOT / "evidence/group-review-rationale-26.07d.md",
    )


@pytest.fixture(scope="module")
def report():  # type: ignore[no-untyped-def]
    return load_r101_conservation_report(
        GOLDEN / "neoplasm-r101-v5-conservation.json.gz"
    )


@pytest.fixture(scope="module")
def classification(report, exclusions):  # type: ignore[no-untyped-def]
    return classify_corpus_delta(report, exclusions=exclusions)


@pytest.fixture(scope="module")
def candidate(classification, exclusions):  # type: ignore[no-untyped-def]
    content = _content(classification, exclusions)
    return finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )


@pytest.mark.unit
def test_review_required_exclusions_are_exact_pair_scoped_and_source_preserving(
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    assert [item.concept_code for item in exclusions] == [
        "C102870",
        "C198031",
        "C27262",
        "C35756",
    ]
    by_code = {item.concept_code: item for item in exclusions}
    assert [
        (p.axis, p.filler_code, p.comparison_direction)
        for p in by_code["C27262"].pair_changes
    ] == [
        ("op:AssociatedRegion", "C41165", "missing-from-candidate"),
        ("op:ClinicalFinding", "C36220", "missing-from-candidate"),
        ("op:ClinicalFinding", "C41397", "missing-from-candidate"),
        ("op:Morphology", "C35501", "grouping-disputed"),
        ("op:Morphology", "C9290", "grouping-disputed"),
    ]
    assert [
        (p.axis, p.filler_code, p.comparison_direction)
        for p in by_code["C102870"].pair_changes
    ] == [
        ("op:AssociatedSite", "C12321", "missing-from-candidate"),
        ("op:Morphology", "C121619", "grouping-disputed"),
        ("op:Morphology", "C39986", "grouping-disputed"),
        ("op:PrimarySite", "C12404", "missing-from-candidate"),
    ]
    assert len(by_code["C198031"].pair_changes) == 5
    assert len(by_code["C35756"].pair_changes) == 16
    assert all(item.delta == "removed-from-effective" for item in exclusions)
    assert all(item.official_source_preserved for item in exclusions)
    assert all(
        item.human_approval is False and item.nci_approval is False
        for item in exclusions
    )
    assert all(
        item.source_assertion_identities and item.evidence_identities
        for item in exclusions
    )


@pytest.mark.unit
def test_review_required_exclusions_refuse_missing_target_concept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path = ROOT / "evidence/group-review-packet-26.07d-schema3.json"
    packet = load_historical_group_review_packet(packet_path)
    incomplete = packet.model_copy(
        update={
            "review_rows": tuple(
                row for row in packet.review_rows if row.concept_code != "C35756"
            )
        }
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "load_historical_group_review_packet",
        lambda _: incomplete,
    )

    with pytest.raises(CorpusAcceptanceValidationError, match="lacks an exclusion"):
        build_review_required_exclusions(
            packet_path,
            ROOT / "evidence/group-review-rationale-26.07d.md",
        )


@pytest.mark.unit
def test_existing_comparison_is_exhaustively_classified_without_causal_overclaim(
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    report = load_r101_conservation_report(
        GOLDEN / "neoplasm-r101-v5-conservation.json.gz"
    )
    result = classify_corpus_delta(report, exclusions=exclusions)

    assert result.structural_object_count == 2_097
    assert result.metadata_object_count == 38_648
    assert result.raw_row_count == 79_393
    assert len(result.classifications) == 40_745
    assert result.category_counts == {
        "compound-metadata-change": 2_754,
        "conservative-review-escalation": 2_097,
        "group-identity-rebinding": 35_017,
        "semantic-routing-change": 877,
    }
    assert result.unexplained_blockers == ()
    assert result.causal_attribution == "prohibited"


@pytest.mark.unit
def test_total_classifier_refuses_duplicate_or_omitted_objects(
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    report = load_r101_conservation_report(
        GOLDEN / "neoplasm-r101-v5-conservation.json.gz"
    )
    result = classify_corpus_delta(report, exclusions=exclusions)
    payload = {
        **result.__dict__,
        "classifications": (*result.classifications, result.classifications[0]),
        "classification_identity": SHA,
    }

    with pytest.raises(ValidationError, match="exactly once"):
        type(result).model_validate(payload)

    same_length = result.model_dump()
    same_length["classifications"] = (
        result.classifications[0],
        result.classifications[0],
        *result.classifications[2:],
    )
    with pytest.raises(ValidationError, match="exactly once"):
        type(result).model_validate(same_length)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ("excluded", "review-required-effective-exclusion"),
        ("minted", "proposal-minted-projection"),
        ("unexplained", "unexplained-blocker"),
        ("r101-linked", "r101-occurrence-linked-output-delta"),
    ],
)
def test_structural_classifier_preserves_distinct_evidence_shapes(
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
    shape: str,
    expected: str,
) -> None:
    evidence = report.non_r101_delta_evidence
    row = evidence.rows[0]
    selected_exclusions = ()
    classified = ()
    rows = (row,)
    if shape == "excluded":
        selected_exclusions = (
            exclusions[0].model_copy(
                update={
                    "concept_code": row.concept_code,
                    "pair_changes": (
                        ExcludedPairChange(
                            axis=row.axis,
                            filler_code=row.filler_code,
                            comparison_direction="grouping-disputed",
                        ),
                    ),
                }
            ),
        )
    elif shape == "minted":
        rows = (row.model_copy(update={"filler_code": "MINT-test"}),)
    elif shape == "unexplained":
        rows = (row.model_copy(update={"needs_review": False}),)
    else:
        rows = ()
        classified = (
            ClassifiedNonR101Delta.model_construct(
                row=row,
                classification="r101-occurrence-linked-output-delta",
                r101_occurrence_ids=("1" * 64,),
            ),
        )
    shaped_evidence = evidence.model_copy(
        update={
            "rows": rows,
            "metadata_deltas": (),
            "classified_rows": classified,
            "raw_typed_delta_count": 1,
        }
    )
    shaped_report = report.model_copy(
        update={"non_r101_delta_evidence": shaped_evidence}
    )

    result = classify_corpus_delta(shaped_report, exclusions=selected_exclusions)

    assert result.category_counts == {expected: 1}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changed_fields", "needs_review", "expected"),
    [
        (
            ("source_definition_ids",),
            True,
            "provenance-evidence-binding-refresh",
        ),
        (("needs_review",), False, "authority-required-review-clearance"),
    ],
)
def test_metadata_classifier_keeps_provenance_clearance_and_unknown_distinct(
    report,  # type: ignore[no-untyped-def]
    changed_fields: tuple[str, ...],
    needs_review: bool,
    expected: str,
) -> None:
    evidence = report.non_r101_delta_evidence
    delta = evidence.metadata_deltas[0]
    old = delta.old.model_copy(
        update={"change": "removed", "needs_review": not needs_review}
    )
    new_updates: dict[str, object] = {
        "change": "added",
        "needs_review": needs_review,
    }
    if changed_fields == ("source_definition_ids",):
        old = old.model_copy(update={"needs_review": needs_review})
        new_updates["source_definition_ids"] = tuple(
            sorted({*old.source_definition_ids, "f" * 64})
        )
    shaped_delta = NonR101MetadataDelta(
        old=old,
        new=old.model_copy(update=new_updates),
        changed_fields=changed_fields,  # type: ignore[arg-type]
    )
    shaped_evidence = evidence.model_copy(
        update={
            "rows": (),
            "metadata_deltas": (shaped_delta,),
            "classified_rows": (),
            "raw_typed_delta_count": 2,
        }
    )
    shaped_report = report.model_copy(
        update={"non_r101_delta_evidence": shaped_evidence}
    )

    result = classify_corpus_delta(shaped_report, exclusions=())

    assert result.category_counts == {expected: 1}


@pytest.mark.unit
def test_metadata_classifier_routes_only_the_exact_excluded_pair(
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    evidence = report.non_r101_delta_evidence
    delta = evidence.metadata_deltas[0]
    exclusion = exclusions[0].model_copy(
        update={
            "concept_code": delta.new.concept_code,
            "pair_changes": (
                ExcludedPairChange(
                    axis=delta.new.axis,
                    filler_code=delta.new.filler_code,
                    comparison_direction="grouping-disputed",
                ),
            ),
        }
    )
    shaped_evidence = evidence.model_copy(
        update={
            "rows": (),
            "metadata_deltas": (delta,),
            "classified_rows": (),
            "raw_typed_delta_count": 2,
        }
    )
    shaped_report = report.model_copy(
        update={"non_r101_delta_evidence": shaped_evidence}
    )

    result = classify_corpus_delta(shaped_report, exclusions=(exclusion,))

    assert result.category_counts == {"review-required-effective-exclusion": 1}
    assert (
        result.classifications[0].evidence_identities == exclusion.evidence_identities
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("raw_row_count", 79_392, "raw changed-row arithmetic differs"),
        ("category_counts", {}, "classification category counts differ"),
        ("unexplained_blockers", (SHA,), "unexplained blocker inventory differs"),
        ("classification_identity", SHA, "classification identity differs"),
    ],
)
def test_total_classifier_refuses_inconsistent_partition_evidence(
    classification,  # type: ignore[no-untyped-def]
    field: str,
    value: object,
    message: str,
) -> None:
    payload = classification.model_dump()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        type(classification).model_validate(payload)


@pytest.mark.unit
def test_review_required_exclusion_refuses_noncanonical_pair_or_evidence() -> None:
    pair = ExcludedPairChange(
        axis="op:Morphology",
        filler_code="C1",
        comparison_direction="grouping-disputed",
    )
    common = {
        "concept_code": "C2",
        "pair_changes": (pair,),
        "source_assertion_identities": ("1" * 64,),
        "evidence_identities": ("2" * 64,),
        "reason": "unresolved-semantic-ambiguity",
        "delta": "removed-from-effective",
        "official_source_preserved": True,
        "human_approval": False,
        "nci_approval": False,
    }
    with pytest.raises(ValidationError, match="canonical and unique"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"pair_changes": (pair, pair)}
        )
    with pytest.raises(ValidationError, match="source assertion identities"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"source_assertion_identities": ("not-a-digest",)}
        )
    with pytest.raises(ValidationError, match="review evidence identities"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"evidence_identities": ("2" * 64, "2" * 64)}
        )


def _dry_run(candidate_content_identity: str = SHA) -> PublicationDryRunEvidence:
    payload = {
        "schema_version": 1,
        "status": "passed",
        "candidate_content_identity": candidate_content_identity,
        "predecessor_marker_identity": "b" * 64,
        "destination_graph_iri": "https://example.org/effective",
        "artifact_identity": "c" * 64,
        "run_id": "run-1",
        "expected_concept_count": 15_633,
        "represented_concept_count": 14_884,
        "marker_protocol_identity": "d" * 64,
        "recovery_identity": "e" * 64,
        "postgres_read_verified": True,
        "qlever_read_verified": True,
        "publication_writes_performed": False,
    }
    return PublicationDryRunEvidence.model_validate(
        {
            **payload,
            "evidence_identity": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    )


def _content_payload(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> dict[str, object]:
    r101 = {
        "occurrence_count": 43_414,
        "routed_or_collapsed_or_suppressed_count": 43_414,
        "unresolved_count": 0,
        "causal_attribution": "prohibited",
    }
    r101["occurrence_partition_identity"] = _identity(r101)
    return {
        "schema_version": 1,
        "scope": {
            "root": "C3262",
            "version": "stated-genus-subclass-v1",
            "worklist_count": 15_633,
            "worklist_identity": "1" * 64,
        },
        "source": {
            "release": "26.07d",
            "source_identity": "2" * 64,
            "stated_artifact_identity": "3" * 64,
            "inferred_artifact_identity": "4" * 64,
            "sibling_manifest_identity": "5" * 64,
            "extraction_plane": "official-stated",
        },
        "execution": {
            "run_id": "run-1",
            "run_fingerprint_identity": "6" * 64,
            "git_head": "7" * 40,
            "policy_identity": "8" * 64,
        },
        "projection": {
            "artifact_sha256": "9" * 64,
            "representation_identity": "9" * 64,
            "served_semantic_projection_identity": "9" * 64,
            "no_equivalence": True,
        },
        "metrics": {"values": {"represented_concepts": 14_884}},
        "gates": {
            "primary_site_cardinality_violations": 0,
            "proposal_provenance_valid": True,
            "projection_loss_status": "passed",
            "residual_status": "passed",
            "fidelity_status": "passed",
            "issue_274_detector": "clear",
            "gate_liveness_identity": "a" * 64,
        },
        "r101_summary": r101,
        "delta_classification": classification,
        "review_required_exclusions": exclusions,
    }


def _content(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> CorpusAcceptanceContent:
    payload = _content_payload(classification, exclusions)
    return CorpusAcceptanceContent.model_validate(
        {**payload, "content_identity": _identity(payload)}
    )


def _publication_run(**changes: object) -> CompletedRunForEvidence:
    fingerprint_values: dict[str, object] = {
        "source_identity": SHA,
        "collapse_policy_identity": "0" * 64,
        "routing_implementation_identity": "1" * 64,
        "mixed_chain_inventory_identity": "2" * 64,
        "stage_sequence_identity": RUN_STAGE_SEQUENCE_IDENTITY,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": branch_spec(DecompositionBranch.NEOPLASM).semantic_types,
        "worklist": ("C1",),
        "total_limit": None,
        "sample_manifest_identity": None,
        "algorithm_version": "decomposition-v5",
        "config_version": "nested-definition-v2",
        "walker_max_depth": 5,
        "output_mode": "file",
        "load_mode": "named-graph",
        "emitted_at": datetime(2026, 9, 16, tzinfo=UTC),
    }
    fingerprint_values.update(cast("dict[str, object]", changes.pop("fingerprint", {})))
    values: dict[str, object] = {
        "run_id": "run-1",
        "ncit_version": "26.07d",
        "fingerprint": RunFingerprint.model_validate(fingerprint_values),
        "representation_identity": SHA,
        "publication_artifact_path": "/not-used",
    }
    values.update(changes)
    return CompletedRunForEvidence.model_validate(values)


class _PublicationStore:
    def __init__(self, run: CompletedRunForEvidence) -> None:
        self.run = run

    async def completed_run_for_evidence(self, run_id: str) -> CompletedRunForEvidence:
        assert run_id == self.run.run_id
        return self.run


@pytest.mark.unit
def test_publication_authorization_requires_exact_accepted_human_decision() -> None:
    dry_run = _dry_run()
    pending = PendingHumanAcceptanceDecision(
        status="not-requested",
        candidate_identity=SHA,
        publication_dry_run_identity=dry_run.evidence_identity,
    )
    with pytest.raises(
        CorpusAcceptanceValidationError, match="accepted human decision"
    ):
        require_publication_authorization(
            candidate_identity=SHA,
            dry_run=dry_run,
            decision=pending,
        )

    accepted = AcceptedHumanAcceptanceDecision(
        status="accepted",
        candidate_identity=SHA,
        publication_dry_run_identity=dry_run.evidence_identity,
        accountable_authority="Dr Example",
        decided_at=datetime(2026, 9, 16, tzinfo=UTC),
        decision_evidence_identity="1" * 64,
    )
    require_publication_authorization(
        candidate_identity=SHA,
        dry_run=dry_run,
        decision=accepted,
    )

    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        require_publication_authorization(
            candidate_identity="2" * 64,
            dry_run=dry_run,
            decision=accepted,
        )
    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        require_publication_authorization(
            candidate_identity=SHA,
            dry_run=dry_run,
            decision=accepted.model_copy(
                update={"publication_dry_run_identity": "2" * 64}
            ),
        )
    blocked_payload = dry_run.model_dump()
    blocked_payload.update(status="blocked", postgres_read_verified=False)
    blocked_payload["evidence_identity"] = _identity(
        {
            key: value
            for key, value in blocked_payload.items()
            if key != "evidence_identity"
        }
    )
    blocked = PublicationDryRunEvidence.model_validate(blocked_payload)
    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        require_publication_authorization(
            candidate_identity=SHA,
            dry_run=blocked,
            decision=accepted.model_copy(
                update={"publication_dry_run_identity": blocked.evidence_identity}
            ),
        )


@pytest.mark.unit
def test_publication_dry_run_evidence_refuses_false_status_or_identity() -> None:
    passed = _dry_run()
    payload = passed.model_dump()
    with pytest.raises(ValidationError, match="status differs"):
        PublicationDryRunEvidence.model_validate(payload | {"status": "blocked"})
    with pytest.raises(ValidationError, match="identity differs"):
        PublicationDryRunEvidence.model_validate(
            payload | {"evidence_identity": "f" * 64}
        )

    blocked_payload = payload | {
        "status": "blocked",
        "postgres_read_verified": False,
        "qlever_read_verified": True,
    }
    blocked_payload["evidence_identity"] = _identity(
        {
            key: value
            for key, value in blocked_payload.items()
            if key != "evidence_identity"
        }
    )
    blocked = PublicationDryRunEvidence.model_validate(blocked_payload)
    assert blocked.status == "blocked"

    qlever_blocked = blocked_payload | {
        "postgres_read_verified": True,
        "qlever_read_verified": False,
    }
    qlever_blocked["evidence_identity"] = _identity(
        {
            key: value
            for key, value in qlever_blocked.items()
            if key != "evidence_identity"
        }
    )
    assert PublicationDryRunEvidence.model_validate(qlever_blocked).status == "blocked"


@pytest.mark.unit
async def test_publication_dry_run_refuses_another_destination_before_reads() -> None:
    with pytest.raises(CorpusAcceptanceValidationError, match="destination differs"):
        await dry_run_corpus_publication(
            candidate_content_identity=SHA,
            run_id="run-1",
            source_identity=SHA,
            representation_identity=SHA,
            artifact=GOLDEN / "neoplasm-current-corpus-baseline.json",
            destination_graph_iri="https://example.org/wrong",
            expected_codes=(),
            expected_worklist_count=0,
            graph=object(),  # type: ignore[arg-type]
            provenance=object(),  # type: ignore[arg-type]
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("run", "source_identity", "representation_identity", "worklist_count"),
    [
        (_publication_run(), "b" * 64, SHA, 1),
        (_publication_run(representation_identity="b" * 64), SHA, SHA, 1),
        (_publication_run(), SHA, SHA, 2),
    ],
)
async def test_publication_dry_run_refuses_each_persisted_run_binding_mismatch(
    run: CompletedRunForEvidence,
    source_identity: str,
    representation_identity: str,
    worklist_count: int,
) -> None:
    with pytest.raises(CorpusAcceptanceValidationError, match="run binding differs"):
        await dry_run_corpus_publication(
            candidate_content_identity=SHA,
            run_id="run-1",
            source_identity=source_identity,
            representation_identity=representation_identity,
            artifact=GOLDEN / "neoplasm-current-corpus-baseline.json",
            destination_graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
            expected_codes=("C1",),
            expected_worklist_count=worklist_count,
            graph=object(),  # type: ignore[arg-type]
            provenance=_PublicationStore(run),
        )


@pytest.mark.unit
@pytest.mark.parametrize("artifact_kind", ["malformed", "identity-mismatch"])
async def test_publication_dry_run_refuses_invalid_or_unbound_artifact(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    artifact = tmp_path / "candidate.ttl"
    artifact.write_text("not turtle" if artifact_kind == "malformed" else "")
    message = (
        "not valid Turtle"
        if artifact_kind == "malformed"
        else "artifact representation identity differs"
    )

    with pytest.raises(CorpusAcceptanceValidationError, match=message):
        await dry_run_corpus_publication(
            candidate_content_identity=SHA,
            run_id="run-1",
            source_identity=SHA,
            representation_identity=SHA,
            artifact=artifact,
            destination_graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
            expected_codes=(),
            expected_worklist_count=1,
            graph=object(),  # type: ignore[arg-type]
            provenance=_PublicationStore(_publication_run()),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "gate",
    ["projection_loss_status", "residual_status", "fidelity_status"],
)
def test_candidate_content_refuses_failed_mechanical_gate(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
    gate: str,
) -> None:
    payload = _content_payload(classification, exclusions)
    gates = cast("dict[str, object]", payload["gates"]).copy()
    gates[gate] = "failed"
    payload["gates"] = gates

    with pytest.raises(ValidationError):
        CorpusAcceptanceContent.model_validate(
            {**payload, "content_identity": _identity(payload)}
        )


@pytest.mark.unit
def test_candidate_content_refuses_unbound_r101_partition(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    payload = _content_payload(classification, exclusions)
    r101 = cast("dict[str, object]", payload["r101_summary"]).copy()
    r101["occurrence_partition_identity"] = "f" * 64
    payload["r101_summary"] = r101

    with pytest.raises(ValidationError, match="R101 occurrence partition identity"):
        CorpusAcceptanceContent.model_validate(
            {**payload, "content_identity": _identity(payload)}
        )


@pytest.mark.unit
def test_candidate_content_refuses_changed_content_identity(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    payload = _content_payload(classification, exclusions)

    with pytest.raises(ValidationError, match="candidate content identity differs"):
        CorpusAcceptanceContent.model_validate(
            {**payload, "content_identity": "f" * 64}
        )


@pytest.mark.unit
def test_candidate_finalization_reaches_only_pending_human_authorization(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    content = _content(classification, exclusions)
    candidate = finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )

    assert isinstance(candidate, CorpusAcceptanceCandidate)
    assert candidate.status == "ready-for-human-authorization"
    assert candidate.human_authorization.status == "not-requested"
    assert candidate.candidate_content_identity == content.content_identity
    assert candidate.publication_dry_run.publication_writes_performed is False


@pytest.mark.unit
def test_candidate_finalization_refuses_dry_run_for_other_content(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    content = _content(classification, exclusions)

    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        finalize_corpus_acceptance_candidate(content, _dry_run())


@pytest.mark.unit
def test_failed_dry_run_keeps_candidate_machine_blocked(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    content = _content(classification, exclusions)
    dry_run_payload = _dry_run(content.content_identity).model_dump()
    dry_run_payload.update(
        status="blocked",
        postgres_read_verified=False,
    )
    dry_run_payload["evidence_identity"] = _identity(
        {
            key: value
            for key, value in dry_run_payload.items()
            if key != "evidence_identity"
        }
    )

    candidate = finalize_corpus_acceptance_candidate(
        content, PublicationDryRunEvidence.model_validate(dry_run_payload)
    )

    assert candidate.status == "machine-blocked"


@pytest.mark.unit
def test_unexplained_delta_keeps_candidate_machine_blocked(
    report,  # type: ignore[no-untyped-def]
) -> None:
    evidence = report.non_r101_delta_evidence
    unexplained = evidence.rows[0].model_copy(update={"needs_review": False})
    shaped_evidence = evidence.model_copy(
        update={
            "rows": (unexplained,),
            "metadata_deltas": (),
            "classified_rows": (),
            "raw_typed_delta_count": 1,
        }
    )
    classification = classify_corpus_delta(
        report.model_copy(update={"non_r101_delta_evidence": shaped_evidence}),
        exclusions=(),
    )
    content = _content(classification, ())

    candidate = finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )

    assert classification.unexplained_blockers
    assert candidate.status == "machine-blocked"


@pytest.mark.unit
def test_candidate_refuses_dry_run_bound_to_other_content(
    candidate,  # type: ignore[no-untyped-def]
) -> None:
    payload = candidate.model_dump()
    dry_run = candidate.publication_dry_run.model_dump()
    dry_run["candidate_content_identity"] = "f" * 64
    dry_run["evidence_identity"] = _identity(
        {key: value for key, value in dry_run.items() if key != "evidence_identity"}
    )
    payload["publication_dry_run"] = dry_run

    with pytest.raises(ValidationError, match="dry-run candidate binding differs"):
        CorpusAcceptanceCandidate.model_validate(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "machine-blocked", "candidate status differs"),
        (
            "candidate_content_identity",
            "f" * 64,
            "candidate content identity differs",
        ),
        ("candidate_identity", "f" * 64, "candidate identity differs"),
    ],
)
def test_candidate_refuses_inconsistent_bound_identity(
    candidate,  # type: ignore[no-untyped-def]
    field: str,
    value: object,
    message: str,
) -> None:
    payload = candidate.model_dump()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        CorpusAcceptanceCandidate.model_validate(payload)
