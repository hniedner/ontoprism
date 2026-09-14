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
    NormalizedGroupPolicyRow,
    PolicyBlock,
    SourceCoordinate,
    canonical_identity,
    load_normalized_group_policy,
)

_MIN_DIAGNOSIS_PAIRS = 2
_PRECHANGE_ARTIFACT_ID = (
    "4febb77cb0e0b91418a22a08c19d9fa05d65529f00af30e85afe53a8d716424d"
)
_PRECHANGE_EVIDENCE_ID = (
    "4475f9ec231e5f5fc6714eb5e7c899ec894d65c6075ca402b851c1bed70c2a32"
)
_PRECHANGE_COMPARISON_ID = (
    "4be29eb27325d533415048773253392a97c30a119f20e6afb06ea14ae703c87e"
)
_PRECHANGE_PACKET_ID = (
    "0f60c6f89c59624cf3e95685b3134fba8180f3bb312a1a03b99f8d949aea801f"
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


def validate_promotion_bundle(
    *,
    evidence_path: Path,
    comparison_path: Path,
    policy_path: Path,
    current_evidence_path: Path,
) -> None:
    evidence = CurrentEngineEvidence.model_validate_json(evidence_path.read_bytes())
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
    precision = comparison.metrics.exact_pair_precision
    recall = comparison.metrics.exact_pair_recall
    if (precision.numerator, precision.denominator) != (111, 132) or (
        recall.numerator,
        recall.denominator,
    ) != (111, 153):
        raise ValueError("candidate replay changes the approved pair metrics")
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


def _diagnostic_input_partition(
    output: tuple[tuple[tuple[str, str], ...], ...],
    diagnosis: str,
    preferred_pairs: tuple[tuple[str, str], ...],
) -> tuple[tuple[tuple[tuple[str, str], ...], ...], tuple[tuple[str, str], ...]]:
    available = {pair for block in output for pair in block}
    preferred = tuple(pair for pair in preferred_pairs if pair in available)
    if diagnosis == "over-merge":
        affected = preferred
        if (
            len(affected) < _MIN_DIAGNOSIS_PAIRS
            or len(
                {
                    index
                    for index, block in enumerate(output)
                    for pair in affected
                    if pair in block
                }
            )
            < _MIN_DIAGNOSIS_PAIRS
        ):
            affected = tuple(block[0] for block in output[:_MIN_DIAGNOSIS_PAIRS])
        affected_set = set(affected)
        untouched = tuple(
            remaining
            for block in output
            if (remaining := tuple(pair for pair in block if pair not in affected_set))
        )
        return tuple(sorted((*untouched, tuple(sorted(affected))))), tuple(
            sorted(affected)
        )
    if diagnosis == "over-split":
        affected = preferred
        if len(affected) < _MIN_DIAGNOSIS_PAIRS or not any(
            set(affected) <= set(block) for block in output
        ):
            affected = next(
                block[:_MIN_DIAGNOSIS_PAIRS]
                for block in output
                if len(block) >= _MIN_DIAGNOSIS_PAIRS
            )
        affected_set = set(affected)
        split = tuple((pair,) for pair in affected)
        untouched = tuple(
            remaining
            for block in output
            if (remaining := tuple(pair for pair in block if pair not in affected_set))
        )
        return tuple(sorted((*untouched, *split))), tuple(sorted(affected))
    raise ValueError(f"unsupported normalized grouping diagnosis: {diagnosis}")


def _current_decision(code: str, historical_row_id: str):
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
        "basis_packet_identity": _PRECHANGE_PACKET_ID,
        "supersedes_review_row_identity": historical_row_id,
    }
    return CurrentDecision(
        authority_identifier="project-owner-current-conversation",
        decision_date="2026-09-14",
        decision=decision,
        basis_packet_identity=_PRECHANGE_PACKET_ID,
        supersedes_review_row_identity=historical_row_id,
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


def _block(code: str, pairs, constituents, rule_kind: str, decision_identity: str):
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
    existing_groups = {item.relationship_group for item in selected}
    existing_group = (
        next(iter(existing_groups))
        if len(existing_groups) == 1 and None not in existing_groups
        else None
    )
    normalized_id = (
        existing_group
        or canonical_identity(
            {
                "concept_code": code,
                "canonical_block_members": tuple(pairs),
                "rule_kind": rule_kind,
                "decision_identity": decision_identity,
            }
        )
        if len(pairs) > 1
        else None
    )
    return PolicyBlock(
        pairs=tuple(pairs),
        normalized_group_id=normalized_id,
        source_fact_ids=facts,
        source_occurrence_ids=occurrence_ids,
        source_group_ids=groups,
        source_coordinates=coordinates,
        occurrence_availability=availability,
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
    packet = group_review_packet.load_historical_group_review_packet(packet_path)
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
    packet_concepts = {item.code: item for item in packet.concepts}
    historical_rows = {item.concept_code: item for item in rationale.rows}
    rationales = _rationales(rationale)
    rows = []
    for code in sorted(ACTIVE_GROUP_CODES):
        concept = concepts[code]
        packet_concept = packet_concepts.get(code)
        if packet_concept is None:
            raise ValueError(f"active policy concept is absent from packet: {code}")
        input_diagnosis = packet_concept.grouping_diagnosis.kind
        historical_row = historical_rows[code]
        current = _current_decision(code, historical_row.review_row_identity)
        rule_kind = (
            "source-evidence-grouping"
            if code in DETERMINISTIC_SOURCE_CODES
            else "reviewed-regrouping"
        )
        decision_identity = (
            current.decision_identity if current else historical_row.review_row_identity
        )
        pairs = {(item.axis, item.filler) for item in concept.constituents}
        target_partition = packet_concept.expected_partition
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
        output_partition = output_partition + tuple(
            block
            for block in (
                tuple(pair for pair in block if pair not in covered)
                for block in input_partition
            )
            if block
        )
        if {pair for block in output_partition for pair in block} != pairs:
            raise ValueError(
                f"output partition does not cover current pairs for {code}"
            )
        policy_input_partition, diagnosis_pairs = _diagnostic_input_partition(
            output_partition,
            input_diagnosis,
            packet_concept.grouping_diagnosis.affected_pairs,
        )
        blocks = tuple(
            _block(code, block, concept.constituents, rule_kind, decision_identity)
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
            "input_partition": policy_input_partition,
            "output_partition": output_partition,
            "input_pair_evidence_identity": _pair_evidence_identity(
                concept.constituents
            ),
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
        "schema_version": 4,
        "source_identity": evidence.source_identity,
        "ncit_version": evidence.ncit_version,
        "basis_run_id": evidence.run_id,
        "basis_artifact_identity": evidence.artifact_identity,
        "basis_evidence_identity": evidence.evidence_identity,
        "basis_comparison_identity": comparison["comparison_identity"],
        "basis_packet_identity": packet.packet_identity,
        "prechange_artifact_identity": _PRECHANGE_ARTIFACT_ID,
        "prechange_evidence_identity": _PRECHANGE_EVIDENCE_ID,
        "prechange_comparison_identity": _PRECHANGE_COMPARISON_ID,
        "prechange_packet_identity": _PRECHANGE_PACKET_ID,
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
