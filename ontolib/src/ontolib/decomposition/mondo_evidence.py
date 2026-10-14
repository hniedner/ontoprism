"""Post-run ToldGenus corroboration; nearest-frontier choices remain open."""

import json
from collections import Counter

from ontolib.decomposition.endpoint_assessment import GENUS_AXES
from ontolib.decomposition.evidence_ledger import EvidenceLedger, reassess_entry
from ontolib.decomposition.evidence_models import LedgerAssessment, LedgerEntry
from ontolib.decomposition.morphology_evidence import (
    _supported_status,
    morphology_inputs,
)
from ontolib.terminologies.mondo import BASIS, BUCKETS, VERSION, MondoHierarchy


def _prepare(previous, items):
    payload = previous.model_dump(
        mode="json", include=set(LedgerAssessment.model_fields)
    )
    new_items = [
        item.model_dump(mode="json") for item in items if item not in previous.evidence
    ]
    payload["evidence"].extend(new_items)
    # Subsumption does not settle the compound nearest-frontier axis choice.
    if items:
        _supported_status(payload)
    assessment = LedgerAssessment.model_validate_json(json.dumps(payload))
    preview = (
        LedgerEntry.model_validate_json(
            json.dumps(reassess_entry(payload, previous, None), default=str)
        )
        if items
        else previous
    )
    return assessment, preview, len(new_items)


def _has_p334(previous):
    return any(
        e.tier == 1 and e.source == "NCIt" and e.locator.endswith("/P334")
        for e in previous.evidence
    )


def _metrics(entry, counts):
    counts[f"status.{entry.status}.axis.{entry.axis}"] += 1
    for reason in entry.needs_sme_reasons:
        counts[f"needs-SME.axis.{entry.axis}.reason.{reason.kind}"] += 1


async def import_mondo_evidence(
    sessions, run_id: str, source: MondoHierarchy, *, dry_run: bool = False
):
    """Preflight all entries before writes; preview includes human applicability."""
    _, entries = await morphology_inputs(
        sessions, run_id, axes=GENUS_AXES, axis_source="parent"
    )
    if not entries:
        raise ValueError("selected run has no parent-derived genus assertions")
    prepared = []
    for entry in entries:
        bucket, items = source.evidence(entry.concept_code, entry.filler_code)
        assessment, preview, added = _prepare(entry, items)
        prepared.append((entry, bucket, assessment, preview, added))
    if not dry_run:
        ledger = EvidenceLedger(sessions)
        for _, bucket, assessment, _, _ in prepared:
            if bucket == "supported":
                await ledger.assess(assessment)
    return _report(run_id, source, dry_run, prepared)


def _report(run_id, source, dry_run, prepared):
    buckets, transitions, statuses, reasons = Counter(), Counter(), Counter(), Counter()
    metrics = {"before": Counter(), "after": Counter()}
    promotions, added = 0, 0
    for previous, bucket, _, preview, new_items in prepared:
        buckets[bucket] += 1
        added += new_items
        statuses[preview.status] += 1
        reasons.update(reason.kind for reason in preview.needs_sme_reasons)
        transitions[(previous.status, preview.status, _has_p334(previous))] += 1
        _metrics(previous, metrics["before"])
        _metrics(preview, metrics["after"])
        promotions += (
            previous.status != "evidence-supported"
            and preview.status == "evidence-supported"
        )
    return {
        "run_id": run_id,
        "dry_run": dry_run,
        "source_release": VERSION,
        "basis": BASIS,
        "assertions": len(prepared),
        "buckets": {bucket: buckets[bucket] for bucket in BUCKETS},
        "source_exclusions": source.exclusions,
        "evidence_items_added": added,
        "promotions": promotions,
        "by_status": dict(statuses),
        "by_reason": dict(reasons),
        "cohort_metrics": {phase: dict(counts) for phase, counts in metrics.items()},
        "transitions": [
            {"before": a, "after": b, "p334_evidence": p334, "count": n}
            for (a, b, p334), n in sorted(transitions.items())
        ],
    }
