"""Evidence-bound generation of the minimal runtime collapse policy."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from ontolib.decomposition.collapse_policy import (
    AUTHORIZED_REGISTRY_IDENTITY,
    CollapsePolicyError,
    CollapseVeto,
    CollapseVetoPolicy,
)
from ontolib.decomposition.filler_selection import route_axis
from ontolib.decomposition.models import (
    RoleRestriction,
    canonical_source_occurrence_id,
)
from ontolib.decomposition.r101_review import dry_run_r101_decision_expansion

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ontolib.decomposition.models import SourceDefinitionOccurrence
    from ontolib.decomposition.r101_conservation import R101ConservationReport
    from ontolib.decomposition.r101_review import R101DecisionRegistry, R101ReviewPacket


def _validate_registry(
    registry: R101DecisionRegistry,
    packet: R101ReviewPacket,
    report: R101ConservationReport,
) -> None:
    _validate_authorized_accounting(registry)
    dry_run_r101_decision_expansion(report, packet, registry)


def _validate_authorized_accounting(registry: R101DecisionRegistry) -> None:
    if registry.registry_identity != AUTHORIZED_REGISTRY_IDENTITY:
        raise CollapsePolicyError("collapse policy registry is not authorized")
    counts = Counter(row.outcome for row in registry.atomic_decisions)
    expected = Counter(
        {"approved-non-exclusive-coverage": 3288, "rejected-retain-broader": 3}
    )
    if counts != expected:
        raise CollapsePolicyError("registry outcomes differ from authorized accounting")
    if any(row.is_exception for row in registry.disease_exceptions):
        raise CollapsePolicyError(
            "authorized collapse policy forbids disease exceptions"
        )


def build_authorized_collapse_veto_policy(
    registry: R101DecisionRegistry,
    packet: R101ReviewPacket,
    report: R101ConservationReport,
    live_occurrences: Iterable[SourceDefinitionOccurrence],
) -> CollapseVetoPolicy:
    """Join authorized registry atoms to packet, report, routing, and live source."""
    _validate_registry(registry, packet, report)
    report_by_id = {row.occurrence_id: row for row in report.occurrences}
    packet_by_id = {row.occurrence_id: row for row in packet.occurrences}
    if len(report_by_id) != len(report.occurrences) or len(packet_by_id) != len(
        packet.occurrences
    ):
        raise CollapsePolicyError("duplicate occurrence in report or packet")
    entries = tuple(
        _entry(decision, report_by_id, packet_by_id, report.source_identity)
        for decision in registry.atomic_decisions
        if decision.outcome == "rejected-retain-broader"
    )
    policy = CollapseVetoPolicy.create(
        registry_identity=registry.registry_identity, entries=entries
    )
    policy.qualify_live_occurrences(
        live_occurrences, source_identity=report.source_identity
    )
    return policy


def _entry(decision, report_by_id, packet_by_id, source_identity: str) -> CollapseVeto:
    source = report_by_id.get(decision.occurrence_id)
    frozen = packet_by_id.get(decision.occurrence_id)
    if source is None or frozen is None:
        raise CollapsePolicyError("rejected occurrence is missing from evidence")
    _validate_source_occurrence(source)
    frozen_key = (
        frozen.disease_code,
        frozen.source_fact_id,
        frozen.anchor_code,
        frozen.structural_path,
        frozen.broader_code,
        frozen.retained_code,
    )
    source_key = (
        source.concept_code,
        source.source_fact_id,
        source.anchor_code,
        source.structural_path,
        source.filler_code,
        decision.retained_code,
    )
    _validate_evidence_keys(decision, source, frozen_key, source_key)
    axis = route_axis(
        RoleRestriction(
            source.role_code,
            source.filler_code,
            anchoring_genus=source.anchor_code,
        )
    )
    _validate_target(source.retained_r82_target, axis, decision.retained_code)
    return CollapseVeto(
        source_identity=source_identity,
        concept_code=source.concept_code,
        role_code=source.role_code,
        anchoring_genus=source.anchor_code,
        normalized_axis=axis,
        broader_code=decision.broader_code,
        narrower_code=decision.retained_code,
        occurrence_id=source.occurrence_id,
        atomic_decision_identity=decision.atomic_decision_identity,
    )


def _validate_source_occurrence(source) -> None:
    recomputed = canonical_source_occurrence_id(
        source.concept_code, source.source_fact_id, source.structural_path
    )
    if source.occurrence_id != recomputed:
        raise CollapsePolicyError("source occurrence identity does not recompute")


def _validate_evidence_keys(decision, source, frozen_key, source_key) -> None:
    if frozen_key != source_key:
        raise CollapsePolicyError("registry, packet, and report binding is stale")
    if decision.broader_code != source.filler_code:
        raise CollapsePolicyError("policy endpoint must be registry broader_code")


def _validate_target(target, axis: str, narrower_code: str) -> None:
    if target is None or (target.axis, target.filler_code) != (axis, narrower_code):
        raise CollapsePolicyError("routing-derived axis or retained endpoint differs")
