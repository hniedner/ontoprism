"""Preview assertion-ledger changes from individually refuted projections."""

import json
from collections import Counter

from ontolib.decomposition.evidence_ledger import EvidenceLedger, _reassess
from ontolib.decomposition.evidence_models import LedgerEntry


def _matching_entry(entries, row):
    (entry,) = [
        e
        for e in entries
        if (e.axis, e.filler_code) == (row["axis"], row["filler_code"])
    ]
    return entry


async def preview_reasoner_evidence(sessions, report, release):
    counts = Counter()
    ledger = EvidenceLedger(sessions)
    for row in report["contradictory_assertions"]:
        entries = await ledger.for_concept(row["concept_code"])
        entry = _matching_entry(entries, row)
        if entry.source_release != release:
            raise ValueError("reasoner finding differs from ledger release")
        payload = entry.model_dump(
            mode="json", exclude={"decisions", "applicability", "updated_at"}
        )
        item = {
            "tier": 2,
            "kind": "reasoner",
            "source": "ROBOT/ELK",
            "source_release": release,
            "locator": f"{entry.concept_code}/{entry.axis}/{entry.filler_code}",
            "statement": "Adding this SubClassOf existential alone to stated NCIt "
            "makes the subject unsatisfiable; baseline subject was satisfiable",
        }
        if item not in payload["evidence"]:
            payload["evidence"].append(item)
        reason = {"kind": "contradictory", "rationale": item["statement"]}
        if reason not in payload["needs_sme_reasons"]:
            payload["needs_sme_reasons"].append(reason)
        payload["status"] = "needs-SME"
        predicted = LedgerEntry.model_validate_json(
            json.dumps(_reassess(payload, entry, None), default=str)
        )
        counts[(entry.axis, entry.status, predicted.status)] += 1
    return {
        "dry_run": True,
        "affected_assertions": sum(counts.values()),
        "transitions": [
            {"axis": a, "before": b, "after": c, "count": n}
            for (a, b, c), n in sorted(counts.items())
        ],
    }
