"""Generate the fixed active normalized-group policy from certified review inputs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from scripts.research import group_review_packet
from scripts.research.current_evidence import (
    CurrentComparison,
    CurrentConceptEvidence,
    CurrentEngineEvidence,
)

from ontolib.decomposition.normalized_group_policy import (
    ACTIVE_GROUP_CODES,
    DETERMINISTIC_SOURCE_CODES,
    PAIR_ONLY_CODES,
    UNRESOLVED_ABSTENTION_BLOCKS,
    ActiveNormalizedGroupPolicy,
    DecisionRegime,
    HistoricalDecision,
    HistoricalObservedPartition,
    NormalizedGroupPolicyRow,
    PolicyBlock,
    SourceCoordinate,
    SourcePairEvidence,
    TransformationName,
    UnavailableHistoricalArtifact,
    canonical_identity,
    load_normalized_group_policy,
    normalized_group_identity,
    normalized_group_label,
)

_MIN_DIAGNOSIS_PAIRS = 2
_CURRENT_PACKET_SCHEMA_VERSION = 4


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _pair_inventory(payload: bytes) -> dict[str, frozenset[tuple[str, str]]]:
    raw = json.loads(payload)
    return {
        concept["code"]: frozenset(
            (constituent["axis"], constituent["filler"])
            for constituent in concept["constituents"]
        )
        for concept in raw["concepts"]
    }


def _concept_semantics_without_groups(
    evidence: CurrentEngineEvidence,
) -> dict[str, object]:
    result = {}
    for concept in evidence.concepts:
        payload = concept.model_dump(mode="json")
        for constituent in payload["constituents"]:
            constituent.pop("axis_ambiguity_group_id")
            constituent.pop("source_group_ids")
            constituent.pop("source_facts")
            constituent.pop("normalized_group_id")
            constituent.pop("normalized_group_label")
        result[concept.code] = payload
    return result


def _group_inventory(
    evidence: CurrentEngineEvidence,
) -> dict[str, tuple[tuple[str | None, str | None], ...]]:
    return {
        concept.code: tuple(
            (item.normalized_group_id, item.normalized_group_label)
            for item in concept.constituents
        )
        for concept in evidence.concepts
    }


def validate_evidence_policy_group_map(
    evidence: CurrentEngineEvidence, policy: ActiveNormalizedGroupPolicy
) -> None:
    policy_map = {
        (row.concept_code, pair): (
            block.normalized_group_id,
            block.normalized_group_label,
        )
        for row in policy.rows
        for block in row.blocks
        for pair in block.pairs
    }
    evidence_rows = [
        (
            (concept.code, (item.axis, item.filler)),
            (item.normalized_group_id, item.normalized_group_label),
        )
        for concept in evidence.concepts
        if concept.code in policy.by_code
        for item in concept.constituents
    ]
    evidence_map = dict(evidence_rows)
    if len(evidence_map) != len(evidence_rows) or evidence_map != policy_map:
        raise ValueError("evidence normalized-group map differs from exact policy map")


def validate_promotion_bundle(
    *,
    evidence_path: Path,
    comparison_path: Path,
    policy_path: Path,
    current_evidence_path: Path,
) -> None:
    evidence = CurrentEngineEvidence.model_validate_json(evidence_path.read_bytes())
    current = CurrentEngineEvidence.model_validate_json(
        current_evidence_path.read_bytes()
    )
    comparison = CurrentComparison.model_validate_json(comparison_path.read_bytes())
    policy = load_normalized_group_policy(policy_path)
    if comparison.current_evidence_identity != evidence.evidence_identity:
        raise ValueError("candidate comparison does not bind candidate evidence")
    if (
        policy.source_identity != evidence.source_identity
        or policy.basis_run_id != evidence.run_id
        or policy.basis_artifact_identity != evidence.artifact_identity
        or policy.basis_evidence_identity != evidence.evidence_identity
        or policy.basis_comparison_identity != comparison.comparison_identity
    ):
        raise ValueError("candidate policy does not bind candidate replay evidence")
    validate_evidence_policy_group_map(evidence, policy)
    if _pair_inventory(current_evidence_path.read_bytes()) != _pair_inventory(
        evidence_path.read_bytes()
    ):
        raise ValueError("candidate replay changes the constituent pair inventory")
    if _concept_semantics_without_groups(current) != _concept_semantics_without_groups(
        evidence
    ):
        raise ValueError("candidate replay changes undeclared constituent semantics")
    current_groups = _group_inventory(current)
    candidate_groups = _group_inventory(evidence)
    changed_group_codes = {
        code
        for code in current_groups
        if current_groups[code] != candidate_groups[code]
    }
    if not changed_group_codes <= ACTIVE_GROUP_CODES:
        raise ValueError(
            "candidate replay changes grouping outside active policy concepts"
        )
    precision = comparison.metrics.exact_pair_precision
    recall = comparison.metrics.exact_pair_recall
    if (precision.numerator, precision.denominator) != (111, 132) or (
        recall.numerator,
        recall.denominator,
    ) != (111, 153):
        raise ValueError("candidate replay changes the approved pair metrics")
    common = comparison.metrics.common_pair_partition_agreement
    if (common.numerator, common.denominator, common.ineligible) != (18, 18, 2):
        raise ValueError("candidate replay changes common-pair grouping eligibility")
    by_code = {concept.code: concept for concept in comparison.concepts}
    unresolved = {
        code
        for code in ACTIVE_GROUP_CODES
        if not by_code[code].common_pair_partition.agrees
    }
    if unresolved:
        raise ValueError(
            "candidate replay retains normalized grouping disagreement: "
            + ", ".join(sorted(unresolved))
        )


def promote_bundle_atomically(replacements: tuple[tuple[Path, Path], ...]) -> None:
    staged: list[Path] = []
    originals = tuple(target.read_bytes() for _source, target in replacements)
    try:
        for source, target in replacements:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{target.name}.", dir=target.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            staged.append(Path(temporary))
        for temporary, (_source, target) in zip(staged, replacements, strict=True):
            os.replace(temporary, target)
        staged.clear()
    except BaseException:
        for original, (_source, target) in zip(originals, replacements, strict=True):
            _atomic_write(target, original)
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)


def _rationales(evidence) -> dict[str, str]:
    return {row.concept_code: row.rationale for row in evidence.rows}


def _pair_evidence_identity(constituents) -> str:
    return canonical_identity(
        tuple(
            {
                "pair": (item.axis, item.filler),
                "source_definition_ids": item.source_definition_ids,
                "source_occurrence_ids": item.source_occurrence_ids,
            }
            for item in sorted(constituents, key=lambda row: (row.axis, row.filler))
        )
    )


def _pair_source_evidence(concept) -> tuple[SourcePairEvidence, ...]:
    rows = []
    for item in sorted(
        concept.constituents, key=lambda value: (value.axis, value.filler)
    ):
        source_facts = item.source_facts
        source_occurrences = item.source_occurrences
        if not source_facts:
            source_occurrences = tuple(
                occurrence
                for occurrence in concept.all_source_occurrences
                if occurrence.filler_code == item.filler
            )
        facts = tuple(
            sorted(
                {fact.fact_id for fact in source_facts}
                | {occurrence.source_fact_id for occurrence in source_occurrences}
            )
        )
        coordinates = tuple(
            SourceCoordinate(source_group_id=group, anchor_code=anchor, depth=depth)
            for group, anchor, depth in sorted(
                {
                    (fact.source_group_id, fact.anchor_code, fact.depth)
                    for fact in source_facts
                }
                | {
                    (
                        occurrence.source_group_id,
                        occurrence.anchor_code,
                        occurrence.depth,
                    )
                    for occurrence in source_occurrences
                }
            )
        )
        genus = bool(source_facts) and all(
            fact.kind == "genus" for fact in source_facts
        )
        availability = (
            "not-applicable-genus-fact"
            if genus
            else "available-source-fact"
            if facts and not source_occurrences
            else "available"
            if facts
            else "unavailable-current-source-coordinate"
        )
        rows.append(
            SourcePairEvidence(
                pair=(item.axis, item.filler),
                source_fact_ids=facts,
                source_occurrence_ids=tuple(
                    sorted(item.occurrence_id for item in source_occurrences)
                ),
                source_coordinates=coordinates,
                occurrence_availability=availability,
            )
        )
    return tuple(rows)


def _source_partition(
    evidence: tuple[SourcePairEvidence, ...],
) -> tuple[tuple[tuple[str, str], ...], ...]:
    grouped: dict[tuple[object, ...], list[tuple[str, str]]] = {}
    for item in evidence:
        coordinates = tuple(
            sorted(
                (row.source_group_id, row.anchor_code, row.depth)
                for row in item.source_coordinates
            )
        )
        key = (
            (item.pair[0], "source-coordinates", coordinates)
            if coordinates
            else (item.pair[0], "current-pair-preservation", item.pair)
        )
        grouped.setdefault(key, []).append(item.pair)
    return tuple(sorted(tuple(sorted(block)) for block in grouped.values()))


def _transformation_rules(code: str) -> tuple[TransformationName, ...]:
    if code == "C27262":
        return ("co-assertion-preservation",)
    if code == "C102870":
        return ("routing",)
    if code == "C100051":
        return ("specificity-collapse",)
    if code == "C4791":
        return ("repeated-pairs",)
    return ("reviewed-regrouping",)


def _block(
    code: str,
    pairs,
    source_pair_evidence: tuple[SourcePairEvidence, ...],
    rule_kind: str,
    decision_regime: DecisionRegime,
    transformation_rules: tuple[TransformationName, ...],
    human_decision_identity: str | None,
    source_evidence_identity: str,
    *,
    grouping_status: Literal["decided", "unresolved"] = "decided",
):
    selected = tuple(item for item in source_pair_evidence if item.pair in set(pairs))
    facts = tuple(
        sorted({value for item in selected for value in item.source_fact_ids})
    )
    occurrence_ids = tuple(
        sorted({value for item in selected for value in item.source_occurrence_ids})
    )
    coordinates = tuple(
        SourceCoordinate(source_group_id=group, anchor_code=anchor, depth=depth)
        for group, anchor, depth in sorted(
            {
                (coordinate.source_group_id, coordinate.anchor_code, coordinate.depth)
                for item in selected
                for coordinate in item.source_coordinates
            }
        )
    )
    groups = tuple(sorted({item.source_group_id for item in coordinates}))
    statuses = {item.occurrence_availability for item in selected}
    availability: Literal[
        "available",
        "available-source-fact",
        "not-applicable-genus-fact",
        "unavailable-current-source-coordinate",
        "mixed",
    ] = next(iter(statuses)) if len(statuses) == 1 else "mixed"  # type: ignore[assignment]
    machine_policy_identity = (
        None
        if human_decision_identity is not None or grouping_status == "unresolved"
        else canonical_identity(
            {
                "kind": "normalized-group-machine-policy",
                "concept_code": code,
                "pairs": tuple(pairs),
                "decision_regime": decision_regime,
                "transformation_rules": transformation_rules,
                "source_evidence_identity": source_evidence_identity,
            }
        )
    )
    decision_identity = human_decision_identity or machine_policy_identity
    normalized_id = (
        normalized_group_identity(
            concept_code=code,
            canonical_block_members=tuple(pairs),
            rule_kind=rule_kind,
            transformation_rules=transformation_rules,
            decision_regime=decision_regime,
            decision_identity=decision_identity,
            source_evidence_identity=source_evidence_identity,
        )
        if decision_identity is not None
        else None
    )
    return PolicyBlock(
        pairs=tuple(pairs),
        grouping_status=grouping_status,
        transformation_rules=transformation_rules,
        decision_regime=decision_regime,
        human_decision_identity=human_decision_identity,
        machine_policy_identity=machine_policy_identity,
        normalized_group_id=normalized_id,
        normalized_group_label=(
            normalized_group_label(code, rule_kind, normalized_id)
            if normalized_id is not None
            else None
        ),
        source_fact_ids=facts,
        source_occurrence_ids=occurrence_ids,
        source_group_ids=groups,
        source_coordinates=coordinates,
        occurrence_availability=availability,
    )


def _policy_row(
    code: str,
    concept: CurrentConceptEvidence,
    current_packet_concept: group_review_packet.GroupReviewConcept,
    historical_concept: group_review_packet.HistoricalGroupReviewConcept,
    historical_row: group_review_packet.LoadedGroupReviewRationaleRow,
    *,
    packet_identity: str,
    historical_packet_identity: str,
    reviewer: str,
    review_date: str,
    rationale: str,
) -> NormalizedGroupPolicyRow:
    rule_kind: Literal["source-evidence-grouping", "reviewed-regrouping"] = (
        "source-evidence-grouping"
        if code in DETERMINISTIC_SOURCE_CODES
        else "reviewed-regrouping"
    )
    source_pair_evidence = _pair_source_evidence(concept)
    source_evidence_identity = canonical_identity(source_pair_evidence)
    pairs = {(item.axis, item.filler) for item in concept.constituents}
    if rule_kind == "reviewed-regrouping":
        packet_pairs = set(current_packet_concept.policy_pair_set)
        if pairs != packet_pairs:
            raise ValueError(f"reviewed current pair set differs for {code}")
        decision_target_pair_set = current_packet_concept.decision_target_pair_set
        reviewed_partition = current_packet_concept.reviewed_partition
        output_partition = current_packet_concept.policy_output_partition
    else:
        decision_target_pair_set = (
            tuple(sorted(pairs)) if code in {"C27262", "C102870"} else ()
        )
        reviewed_partition = ()
        output_partition = _source_partition(source_pair_evidence)
    diagnosis_pairs = historical_concept.grouping_diagnosis.affected_pairs
    if len(diagnosis_pairs) < _MIN_DIAGNOSIS_PAIRS:
        raise ValueError(f"historical diagnosis lacks affected pairs for {code}")
    reviewed_blocks = set(reviewed_partition)
    blocks = tuple(
        _policy_block_for_partition(
            code,
            block,
            source_pair_evidence,
            rule_kind,
            reviewed_blocks,
            source_evidence_identity,
            historical_row.review_row_identity,
        )
        for block in output_partition
    )
    historical_decision = HistoricalDecision(
        review_row_identity=historical_row.review_row_identity,
        decision=historical_row.decision,
        pair_decision=historical_row.pair_decision,
        reviewer=reviewer,
        review_date=review_date,
        rationale=rationale,
        rationale_sha256=historical_row.rationale_sha256,
    )
    payload = {
        "concept_code": code,
        "rule_kind": rule_kind,
        "input_diagnosis": historical_concept.grouping_diagnosis.kind,
        "diagnosis_pairs": diagnosis_pairs,
        "decision_target_pair_set": decision_target_pair_set,
        "reviewed_partition": reviewed_partition,
        "historical_expected_partition": historical_concept.expected_partition,
        "historical_observed_partition": HistoricalObservedPartition(
            kind="historical_observed_partition",
            packet_identity=historical_packet_identity,
            partition=historical_concept.actual_partition,
        ),
        "output_partition": output_partition,
        "input_pair_evidence_identity": _pair_evidence_identity(concept.constituents),
        "source_evidence_identity": source_evidence_identity,
        "source_pair_evidence": source_pair_evidence,
        "blocks": blocks,
        "historical_decision": historical_decision,
        "limitations": (
            "changes-normalized-grouping-only",
            "non-stage-pair-membership-drift-is-not-covered-by-reviewed-decision",
            "does-not-authorize-publication-or-ncit-adoption",
        ),
    }
    return NormalizedGroupPolicyRow(**payload, row_identity=canonical_identity(payload))


def _policy_block_for_partition(
    code: str,
    block: tuple[tuple[str, str], ...],
    source_pair_evidence: tuple[SourcePairEvidence, ...],
    rule_kind: Literal["source-evidence-grouping", "reviewed-regrouping"],
    reviewed_blocks: set[tuple[tuple[str, str], ...]],
    source_evidence_identity: str,
    historical_decision_identity: str,
) -> PolicyBlock:
    if frozenset(block) == UNRESOLVED_ABSTENTION_BLOCKS.get(code):
        return _block(
            code,
            block,
            source_pair_evidence,
            rule_kind,
            "unresolved-abstention",
            _transformation_rules(code),
            None,
            source_evidence_identity,
            grouping_status="unresolved",
        )
    if block in reviewed_blocks:
        regime: DecisionRegime = "historical-approval"
        human_identity = historical_decision_identity
        transformations: tuple[TransformationName, ...] = ("reviewed-regrouping",)
    else:
        member_evidence = tuple(
            item for item in source_pair_evidence if item.pair in block
        )
        available = all(
            item.occurrence_availability != "unavailable-current-source-coordinate"
            for item in member_evidence
        )
        regime = "source-evidence" if available else "current-pair-preservation"
        human_identity = None
        transformations = (
            _transformation_rules(code)
            if rule_kind == "source-evidence-grouping"
            else ("co-assertion-preservation",)
        )
    return _block(
        code,
        block,
        source_pair_evidence,
        rule_kind,
        regime,
        transformations,
        human_identity,
        source_evidence_identity,
    )


def generate_active_normalized_group_policy(
    *,
    evidence_path: Path,
    comparison_path: Path,
    packet_path: Path,
    historical_packet_path: Path,
    rationale_markdown_path: Path,
    rationale_sidecar_path: Path,
    output: Path,
) -> ActiveNormalizedGroupPolicy:
    inputs = {
        path: path.read_bytes()
        for path in (
            evidence_path,
            comparison_path,
            packet_path,
            historical_packet_path,
            rationale_markdown_path,
            rationale_sidecar_path,
        )
    }
    evidence = CurrentEngineEvidence.model_validate_json(inputs[evidence_path])
    comparison = json.loads(inputs[comparison_path])
    packet_raw = json.loads(inputs[packet_path])
    packet = (
        group_review_packet.load_group_review_packet(packet_path)
        if packet_raw.get("schema_version") == _CURRENT_PACKET_SCHEMA_VERSION
        else group_review_packet.load_historical_group_review_packet(packet_path)
    )
    if not isinstance(packet, group_review_packet.GroupReviewPacket):
        raise ValueError("current group review packet schema is required")
    historical = group_review_packet.load_historical_group_review_packet(
        historical_packet_path
    )
    rationale = group_review_packet.load_group_review_rationale_evidence(
        markdown_path=rationale_markdown_path,
        sidecar_path=rationale_sidecar_path,
        packet=historical,
    )
    if comparison["current_evidence_identity"] != evidence.evidence_identity:
        raise ValueError("active policy comparison does not bind current evidence")
    concepts = {item.code: item for item in evidence.concepts}
    current_packet_concepts = {item.code: item for item in packet.concepts}
    historical_rows = {item.concept_code: item for item in rationale.rows}
    rationales = _rationales(rationale)
    rows = []
    historical_concepts = {item.code: item for item in historical.concepts}
    for code in sorted(ACTIVE_GROUP_CODES):
        rows.append(
            _policy_row(
                code,
                concepts[code],
                current_packet_concepts[code],
                historical_concepts[code],
                historical_rows[code],
                packet_identity=packet.packet_identity,
                historical_packet_identity=historical.packet_identity,
                reviewer=rationale.reviewer,
                review_date=rationale.review_date,
                rationale=rationales[code],
            )
        )
    sidecar = rationale.sidecar
    payload = {
        "schema_version": 7,
        "source_identity": evidence.source_identity,
        "ncit_version": evidence.ncit_version,
        "basis_run_id": evidence.run_id,
        "basis_artifact_identity": evidence.artifact_identity,
        "basis_evidence_identity": evidence.evidence_identity,
        "basis_comparison_identity": comparison["comparison_identity"],
        "basis_packet_identity": packet.packet_identity,
        "unavailable_historical_artifact": UnavailableHistoricalArtifact(
            status="not-retained",
            family="m1-6-current-replay",
            run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
            expected_artifact_sha256=(
                "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
            ),
            reason="overwritten-before-immutable-retention",
            evidentiary_use="none",
        ),
        "historical_packet_identity": historical.packet_identity,
        "historical_packet_sha256": hashlib.sha256(
            inputs[historical_packet_path]
        ).hexdigest(),
        "rationale_markdown_sha256": hashlib.sha256(
            inputs[rationale_markdown_path]
        ).hexdigest(),
        "rationale_sidecar_identity": sidecar.sidecar_identity,
        "rationale_sidecar_sha256": hashlib.sha256(
            inputs[rationale_sidecar_path]
        ).hexdigest(),
        "rows": tuple(rows),
        "pair_only_codes": tuple(sorted(PAIR_ONLY_CODES)),
    }
    policy = ActiveNormalizedGroupPolicy(
        **payload, policy_identity=canonical_identity(payload)
    )
    _atomic_write(output, (policy.model_dump_json(indent=2) + "\n").encode())
    return policy
