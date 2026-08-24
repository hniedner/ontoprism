from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.adjudication import _parser
from scripts.research.current_evidence import CurrentComparison, CurrentEngineEvidence
from scripts.research.group_review_packet import (
    CorrectionBlockedDisposition,
    GroupReviewPacket,
    build_group_review_packet,
    diagnose_grouping,
    generate_group_review_packet,
    load_group_review_packet,
)

pytestmark = pytest.mark.unit

_GOLDEN = Path(__file__).with_name("golden")
_EVIDENCE = _GOLDEN / "neoplasm-current-engine-evidence.json"
_COMPARISON = _GOLDEN / "neoplasm-current-comparison.json"


def _inputs() -> tuple[CurrentEngineEvidence, CurrentComparison]:
    return (
        CurrentEngineEvidence.model_validate_json(_EVIDENCE.read_bytes()),
        CurrentComparison.model_validate_json(_COMPARISON.read_bytes()),
    )


def _packet() -> GroupReviewPacket:
    evidence, comparison = _inputs()
    return build_group_review_packet(evidence=evidence, comparison=comparison)


def test_packet_derives_current_cohort_metrics_and_controls() -> None:
    evidence, comparison = _inputs()
    packet = build_group_review_packet(evidence=evidence, comparison=comparison)

    assert packet.historical_full_partition_agreement.model_dump() == {
        "numerator": 2,
        "denominator": 20,
        "provenance": "historical-57",
    }
    assert packet.current_metrics == comparison.metrics
    assert packet.cohort.outcome_counts == dict(
        sorted(Counter(item.outcome for item in evidence.concepts).items())
    )
    assert packet.cohort.decomposed_codes == tuple(
        item.code for item in evidence.concepts if item.outcome == "decomposed"
    )
    assert {item.outcome for item in packet.cohort.controls} == {
        "semantic-excluded",
        "atomic-no-op",
    }
    assert all(
        item.interpretation == "empty-partition-control-not-grouping-success"
        for item in packet.cohort.controls
    )
    highest = max(
        evidence.concepts,
        key=lambda item: (len(item.all_source_occurrences), item.code),
    )
    observed_highest = (
        packet.cohort.highest_fanout_code,
        packet.cohort.highest_fanout_occurrences,
    )
    assert observed_highest == (
        highest.code,
        len(highest.all_source_occurrences),
    )


def test_every_disagreement_has_total_pair_group_and_disposition_diagnosis() -> None:
    packet = _packet()

    assert len(packet.concepts) == 18
    assert any(item.pair_delta.missing_pairs for item in packet.concepts)
    assert any(item.pair_delta.extra_pairs for item in packet.concepts)
    assert {item.grouping_diagnosis.kind for item in packet.concepts} >= {
        "agrees-on-common-pairs",
        "over-merge",
        "over-split",
    }
    a, b, c = ("op:A", "C1"), ("op:B", "C2"), ("op:C", "C3")
    assert diagnose_grouping(((a, b), (c,)), ((a, c), (b,))).kind == "misassignment"
    assert all(item.disposition.status != "accepted" for item in packet.concepts)
    assert any(
        item.pair_delta.status != "no-pair-delta"
        and item.grouping_diagnosis.kind
        in {"over-merge", "over-split", "misassignment"}
        for item in packet.concepts
    )
    assert {item.disposition.status for item in packet.concepts} == {
        "correction-blocked"
    }
    for item in packet.concepts:
        assert isinstance(item.disposition, CorrectionBlockedDisposition)
        assert item.disposition.reason == "missing-transformation-rule-evidence"


def test_every_actual_group_cites_exact_pair_and_source_occurrences() -> None:
    evidence, comparison = _inputs()
    packet = build_group_review_packet(evidence=evidence, comparison=comparison)
    evidence_by_code = {item.code: item for item in evidence.concepts}

    for concept in packet.concepts:
        expected_pairs = {
            pair for group in concept.expected_groups for pair in group.pairs
        }
        actual_pairs = {
            pair.pair for group in concept.actual_groups for pair in group.pairs
        }
        assert expected_pairs == {
            pair for block in concept.expected_partition for pair in block
        }
        assert actual_pairs == {
            pair for block in concept.actual_partition for pair in block
        }
        assert all(
            group.source_evidence.availability == "unavailable-historical-oracle"
            for group in concept.expected_groups
        )
        source_occurrences = {
            item.occurrence_id: item
            for item in evidence_by_code[concept.code].all_source_occurrences
        }
        for group in concept.actual_groups:
            assert not group.normalized_group_id.startswith("source:")
            assert group.transformation_evidence.status == "unavailable-upstream"
            for pair in group.pairs:
                if pair.availability == "unavailable-upstream":
                    assert pair.reason == "source-occurrence-unavailable-upstream"
                    continue
                assert pair.occurrences
                assert pair.occurrence_ids == tuple(
                    item.occurrence_id for item in pair.occurrences
                )
                assert all(
                    source_occurrences[item.occurrence_id].model_dump()
                    == item.model_dump()
                    for item in pair.occurrences
                )
                assert all(
                    item.filler_code == pair.pair[1] for item in pair.occurrences
                )


def test_rule_boundary_names_complete_catalog_without_fabricating_evidence() -> None:
    packet = _packet()

    assert tuple(item.kind for item in packet.transformation_rule_catalog) == (
        "co-assertion-preservation",
        "routing",
        "specificity-collapse",
        "repeated-pairs",
        "reviewed-regrouping",
    )
    assert packet.review_boundary.status == "blocked"
    assert packet.review_boundary.reason == "missing-transformation-rule-evidence"
    assert packet.review_boundary.sme_adjudication == "not-recorded"


def test_wrong_highest_fanout_normalized_partition_is_rejected() -> None:
    evidence, comparison = _inputs()
    highest = max(
        evidence.concepts,
        key=lambda item: (len(item.all_source_occurrences), item.code),
    )
    target = next(item for item in comparison.concepts if item.code == highest.code)
    wrong_full = target.full_partition.model_copy(
        update={"actual_partition": target.full_partition.expected_partition}
    )
    wrong_target = target.model_copy(update={"full_partition": wrong_full})
    wrong = comparison.model_copy(
        update={
            "concepts": tuple(
                wrong_target if item.code == highest.code else item
                for item in comparison.concepts
            )
        }
    )

    with pytest.raises(ValueError, match="actual normalized partition"):
        build_group_review_packet(evidence=evidence, comparison=wrong)


def test_packet_rejects_rebound_and_aliased_group_identity() -> None:
    evidence, comparison = _inputs()
    rebound = comparison.model_copy(update={"source_identity": "f" * 64})
    with pytest.raises(ValueError, match="source identity"):
        build_group_review_packet(evidence=evidence, comparison=rebound)

    packet = build_group_review_packet(evidence=evidence, comparison=comparison)
    payload = packet.model_dump(mode="json")
    actual = payload["concepts"][0]["actual_groups"][0]
    actual["normalized_group_id"] = actual["source_group_ids"][0]
    with pytest.raises(ValidationError, match="normalized group identity"):
        GroupReviewPacket.model_validate_json(json.dumps(payload))


def test_generator_writes_canonical_identity_checked_json(tmp_path: Path) -> None:
    output = tmp_path / "group-review.json"
    generated = generate_group_review_packet(
        evidence_path=_EVIDENCE,
        comparison_path=_COMPARISON,
        output=output,
    )

    assert load_group_review_packet(output) == generated
    assert output.read_bytes().endswith(b"\n")
    persisted_identity = json.loads(output.read_bytes())["packet_identity"]
    assert persisted_identity == generated.packet_identity

    payload = json.loads(output.read_bytes())
    payload["concepts"][0]["pair_delta"]["status"] = "no-pair-delta"
    output.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=r"packet identity|pair delta"):
        load_group_review_packet(output)


def test_strict_boundary_rejects_coercion_and_unknown_fields() -> None:
    payload = _packet().model_dump(mode="json")
    payload["cohort"]["highest_fanout_occurrences"] = "99"
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        GroupReviewPacket.model_validate(payload)


def test_group_review_cli_requires_both_bound_inputs_and_output() -> None:
    args = _parser().parse_args(
        [
            "generate-group-review-packet",
            "--current-evidence",
            "evidence.json",
            "--current-comparison",
            "comparison.json",
            "--output",
            "packet.json",
        ]
    )

    assert args.current_evidence == Path("evidence.json")
    assert args.current_comparison == Path("comparison.json")
    assert args.output == Path("packet.json")
