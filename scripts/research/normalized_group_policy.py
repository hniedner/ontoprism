"""Generate the fixed active normalized-group policy from certified review inputs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from scripts.research import group_review_packet
from scripts.research.current_evidence import CurrentComparison, CurrentEngineEvidence

from ontolib.decomposition.normalized_group_policy import (
    ACTIVE_GROUP_CODES,
    DETERMINISTIC_SOURCE_CODES,
    PAIR_ONLY_CODES,
    ActiveNormalizedGroupPolicy,
    CurrentDecision,
    HistoricalDecision,
    HistoricalObservedPartition,
    NormalizedGroupPolicyRow,
    PolicyBlock,
    SourceCoordinate,
    SourcePairEvidence,
    UnavailableParentBinding,
    canonical_identity,
    load_normalized_group_policy,
    normalized_group_identity,
    normalized_group_label,
)
from ontolib.decomposition.run_artifacts import ArtifactUnavailableRecord

_MIN_DIAGNOSIS_PAIRS = 2
_CURRENT_PACKET_SCHEMA_VERSION = 4
_UNAVAILABLE_RECORD_RELATIVE_PATH = (
    "tmp/artifacts/v1/unavailable/neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997.json"
)

_CURRENT_DECISION_CODES = frozenset(
    {
        "C27262",
        "C102870",
        "C115057",
        "C101539",
        "C132677",
        "C206219",
        "C6135",
        "C89995",
        "C27787",
        "C115118",
    }
)


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
            constituent.pop("relationship_group")
        result[concept.code] = payload
    return result


def _group_inventory(
    evidence: CurrentEngineEvidence,
) -> dict[str, tuple[str | None, ...]]:
    return {
        concept.code: tuple(item.relationship_group for item in concept.constituents)
        for concept in evidence.concepts
    }


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


def _partition(constituents) -> tuple[tuple[tuple[str, str], ...], ...]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for item in constituents:
        pair = (item.axis, item.filler)
        key = item.relationship_group or f"singleton:{item.axis}:{item.filler}"
        grouped.setdefault(key, []).append(pair)
    return tuple(sorted(tuple(sorted(values)) for values in grouped.values()))


def _rationales(evidence) -> dict[str, str]:
    return {row.concept_code: row.rationale for row in evidence.rows}


def _current_decision(
    code: str,
    historical_row_id: str,
    *,
    packet_identity: str,
    source_evidence_identity: str,
):
    if code not in _CURRENT_DECISION_CODES:
        return None
    decision: Literal[
        "supersede-abstention-with-source-evidence-grouping",
        "activate-reviewed-normalized-group-policy",
    ] = (
        "supersede-abstention-with-source-evidence-grouping"
        if code in {"C27262", "C102870"}
        else "activate-reviewed-normalized-group-policy"
    )
    payload = {
        "authority_identifier": "project-owner-current-conversation",
        "decision_date": "2026-09-14",
        "decision": decision,
        "basis_packet_identity": packet_identity,
        "basis_source_evidence_identity": source_evidence_identity,
        "supersedes_review_row_identity": historical_row_id,
    }
    grouping_payload = {
        key: payload[key]
        for key in (
            "authority_identifier",
            "decision_date",
            "decision",
            "supersedes_review_row_identity",
        )
    }
    grouping_decision_identity = canonical_identity(grouping_payload)
    payload["grouping_decision_identity"] = grouping_decision_identity
    return CurrentDecision(
        authority_identifier="project-owner-current-conversation",
        decision_date="2026-09-14",
        decision=decision,
        basis_packet_identity=packet_identity,
        basis_source_evidence_identity=source_evidence_identity,
        supersedes_review_row_identity=historical_row_id,
        grouping_decision_identity=grouping_decision_identity,
        decision_identity=canonical_identity(payload),
    )


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


def _pair_source_evidence(constituents) -> tuple[SourcePairEvidence, ...]:
    rows = []
    for item in sorted(constituents, key=lambda value: (value.axis, value.filler)):
        facts = tuple(sorted(fact.fact_id for fact in item.source_facts))
        coordinates = tuple(
            SourceCoordinate(source_group_id=group, anchor_code=anchor, depth=depth)
            for group, anchor, depth in sorted(
                {
                    (fact.source_group_id, fact.anchor_code, fact.depth)
                    for fact in item.source_facts
                }
            )
        )
        genus = bool(item.source_facts) and all(
            fact.kind == "genus" for fact in item.source_facts
        )
        availability = (
            "not-applicable-genus-fact"
            if genus
            else "available"
            if facts
            else "unavailable-current-source-coordinate"
        )
        rows.append(
            SourcePairEvidence(
                pair=(item.axis, item.filler),
                source_fact_ids=facts,
                source_occurrence_ids=tuple(sorted(item.source_occurrence_ids)),
                source_coordinates=coordinates,
                occurrence_availability=availability,
            )
        )
    return tuple(rows)


def derive_normalized_group_identity(*, bootstrap_group: str | None, **values) -> str:
    del bootstrap_group
    return normalized_group_identity(**values)


def _block(
    code: str,
    pairs,
    constituents,
    rule_kind: str,
    decision_identity: str,
    source_evidence_identity: str,
):
    selected = tuple(
        item for item in constituents if (item.axis, item.filler) in set(pairs)
    )
    occurrences = tuple(
        occurrence for item in selected for occurrence in item.source_occurrences
    )
    source_facts = tuple(fact for item in selected for fact in item.source_facts)
    facts = tuple(sorted({fact.fact_id for fact in source_facts}))
    occurrence_ids = tuple(sorted({item.occurrence_id for item in occurrences}))
    groups = tuple(sorted({item.source_group_id for item in source_facts}))
    coordinates = tuple(
        SourceCoordinate(source_group_id=group, anchor_code=anchor, depth=depth)
        for group, anchor, depth in sorted(
            {
                (item.source_group_id, item.anchor_code, item.depth)
                for item in source_facts
            }
        )
    )
    availability = (
        "not-applicable-genus-fact"
        if facts and not occurrence_ids
        else "mixed"
        if any(not item.source_occurrence_ids for item in selected)
        else "available"
    )
    normalized_id = (
        derive_normalized_group_identity(
            concept_code=code,
            canonical_block_members=tuple(pairs),
            rule_kind=rule_kind,
            decision_identity=decision_identity,
            source_evidence_identity=source_evidence_identity,
            bootstrap_group=next(
                (
                    item.relationship_group
                    for item in selected
                    if item.relationship_group
                ),
                None,
            ),
        )
        if len(pairs) > 1
        else None
    )
    return PolicyBlock(
        pairs=tuple(pairs),
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


def _load_unavailable_prechange_record(
    raw: bytes, path: Path
) -> ArtifactUnavailableRecord:
    try:
        unavailable = ArtifactUnavailableRecord.from_dict(json.loads(raw))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            "unavailable prechange record is required and invalid"
        ) from exc
    if (
        unavailable.family != "m1-6-current-replay"
        or unavailable.run_id != "neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997"
        or unavailable.expected_sha256
        != "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
        or unavailable.last_known_path != "tmp/m1-6-current-replay.ttl"
        or unavailable.reason != "overwritten-before-immutable-retention"
    ):
        raise ValueError(f"unavailable prechange record binding differs: {path}")
    return unavailable


def generate_active_normalized_group_policy(
    *,
    evidence_path: Path,
    comparison_path: Path,
    packet_path: Path,
    historical_packet_path: Path,
    rationale_markdown_path: Path,
    rationale_sidecar_path: Path,
    unavailable_prechange_record_path: Path,
    output: Path,
) -> ActiveNormalizedGroupPolicy:
    if not unavailable_prechange_record_path.is_file():
        raise ValueError("unavailable prechange record is required and invalid")
    inputs = {
        path: path.read_bytes()
        for path in (
            evidence_path,
            comparison_path,
            packet_path,
            historical_packet_path,
            rationale_markdown_path,
            rationale_sidecar_path,
            unavailable_prechange_record_path,
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
    historical = group_review_packet.load_historical_group_review_packet(
        historical_packet_path
    )
    rationale = group_review_packet.load_group_review_rationale_evidence(
        markdown_path=rationale_markdown_path,
        sidecar_path=rationale_sidecar_path,
        packet=historical,
    )
    unavailable = _load_unavailable_prechange_record(
        inputs[unavailable_prechange_record_path], unavailable_prechange_record_path
    )
    if comparison["current_evidence_identity"] != evidence.evidence_identity:
        raise ValueError("active policy comparison does not bind current evidence")
    concepts = {item.code: item for item in evidence.concepts}
    historical_rows = {item.concept_code: item for item in rationale.rows}
    rationales = _rationales(rationale)
    rows = []
    for code in sorted(ACTIVE_GROUP_CODES):
        concept = concepts[code]
        historical_concept = {item.code: item for item in historical.concepts}[code]
        input_diagnosis = historical_concept.grouping_diagnosis.kind
        historical_row = historical_rows[code]
        rule_kind = (
            "source-evidence-grouping"
            if code in DETERMINISTIC_SOURCE_CODES
            else "reviewed-regrouping"
        )
        source_pair_evidence = _pair_source_evidence(concept.constituents)
        source_evidence_identity = canonical_identity(source_pair_evidence)
        current = _current_decision(
            code,
            historical_row.review_row_identity,
            packet_identity=packet.packet_identity,
            source_evidence_identity=source_evidence_identity,
        )
        decision_identity = (
            current.grouping_decision_identity
            if current
            else historical_row.review_row_identity
        )
        pairs = {(item.axis, item.filler) for item in concept.constituents}
        target_partition = historical_concept.expected_partition
        output_partition = tuple(
            block
            for block in (
                tuple(pair for pair in block if pair in pairs)
                for block in target_partition
            )
            if block
        )
        covered = {pair for block in output_partition for pair in block}
        input_partition = _partition(concept.constituents)
        output_partition = tuple(
            sorted(
                output_partition
                + tuple(
                    block
                    for block in (
                        tuple(pair for pair in block if pair not in covered)
                        for block in input_partition
                    )
                    if block
                )
            )
        )
        if {pair for block in output_partition for pair in block} != pairs:
            raise ValueError(
                f"output partition does not cover current pairs for {code}"
            )
        historical_partition = historical_concept.actual_partition
        diagnosis_pairs = historical_concept.grouping_diagnosis.affected_pairs
        if len(diagnosis_pairs) < _MIN_DIAGNOSIS_PAIRS:
            raise ValueError(f"historical diagnosis lacks affected pairs for {code}")
        blocks = tuple(
            _block(
                code,
                block,
                concept.constituents,
                rule_kind,
                decision_identity,
                source_evidence_identity,
            )
            for block in output_partition
        )
        historical_decision = HistoricalDecision(
            review_row_identity=historical_row.review_row_identity,
            decision=historical_row.decision,
            pair_decision=historical_row.pair_decision,
            reviewer=rationale.reviewer,
            review_date=rationale.review_date,
            rationale=rationales[code],
            rationale_sha256=historical_row.rationale_sha256,
        )
        payload = {
            "concept_code": code,
            "rule_kind": rule_kind,
            "input_diagnosis": input_diagnosis,
            "diagnosis_pairs": diagnosis_pairs,
            "historical_observed_partition": HistoricalObservedPartition(
                kind="historical_observed_partition",
                packet_identity=historical.packet_identity,
                partition=historical_partition,
            ),
            "output_partition": output_partition,
            "input_pair_evidence_identity": _pair_evidence_identity(
                concept.constituents
            ),
            "source_evidence_identity": source_evidence_identity,
            "source_pair_evidence": source_pair_evidence,
            "blocks": blocks,
            "historical_decision": historical_decision,
            "current_decision": current,
            "limitations": (
                "changes-normalized-grouping-only",
                "does-not-authorize-publication-or-ncit-adoption",
            ),
        }
        rows.append(
            NormalizedGroupPolicyRow(
                **payload, row_identity=canonical_identity(payload)
            )
        )
    sidecar = rationale.sidecar
    payload = {
        "schema_version": 5,
        "source_identity": evidence.source_identity,
        "ncit_version": evidence.ncit_version,
        "basis_run_id": evidence.run_id,
        "basis_artifact_identity": evidence.artifact_identity,
        "basis_evidence_identity": evidence.evidence_identity,
        "basis_comparison_identity": comparison["comparison_identity"],
        "basis_packet_identity": packet.packet_identity,
        "unavailable_prechange_parent": UnavailableParentBinding(
            binding_kind="unavailable_parent_binding",
            durable_record_path=_UNAVAILABLE_RECORD_RELATIVE_PATH,
            record_identity=canonical_identity(unavailable.to_dict()),
            record_sha256=hashlib.sha256(
                inputs[unavailable_prechange_record_path]
            ).hexdigest(),
            family="m1-6-current-replay",
            run_id="neoplasm-350b960f-ae1c-4677-81e6-a7f80d8ad997",
            expected_artifact_sha256=(
                "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
            ),
            last_known_path="tmp/m1-6-current-replay.ttl",
            reason="overwritten-before-immutable-retention",
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
