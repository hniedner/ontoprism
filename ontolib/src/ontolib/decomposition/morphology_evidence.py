"""Post-run morphology corroboration from NCIt's P334 carrier hierarchy."""

import json
import re
from collections import Counter

from sqlalchemy import text

from ontolib.decomposition.evidence_ledger import EvidenceLedger, reassess_entry
from ontolib.decomposition.evidence_models import (
    EvidenceItem,
    LedgerAssessment,
    LedgerEntry,
)
from ontolib.decomposition.histology_anchor import HistologyAnchors
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI


async def read_site_carriers(client) -> frozenset[str]:
    """Read positive site existentials, including nested intersection conjuncts."""
    rows = await client.select_once(
        f'''PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        SELECT DISTINCT ?carrier WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{
          VALUES ?role {{ <{NCIT_NS}R100> <{NCIT_NS}R101> <{NCIT_NS}R102> }}
          ?carrier (rdfs:subClassOf|owl:equivalentClass) ?expression .
          ?expression (owl:intersectionOf/rdf:rest*/rdf:first)* ?restriction .
          ?restriction owl:onProperty ?role ; owl:someValuesFrom ?site .
          FILTER(isIRI(?carrier) && STRSTARTS(STR(?carrier), "{NCIT_NS}"))
        }} }}''',
        required_variables={"carrier"},
    )
    return frozenset(_site_code(row["carrier"]) for row in rows)


def _site_code(iri):
    if not isinstance(iri, str) or not re.fullmatch(
        re.escape(NCIT_NS) + r"C[0-9]+", iri
    ):
        raise ValueError("site restriction carrier is not a named NCIt code")
    return iri.removeprefix(NCIT_NS)


async def morphology_inputs(sessions, run_id):
    """Require a completed run and matching existing ledger assessments."""
    async with sessions() as session:
        result = await session.execute(
            text(
                "SELECT status,ncit_version,source_identity,"
                "fingerprint->>'scope_root' AS scope "
                "FROM decomp_run WHERE id=:run"
            ),
            {"run": run_id},
        )
        run = result.mappings().one_or_none()
        if run is None or run["status"] != "complete":
            raise ValueError("morphology import requires a completed stored run")
        result = await session.execute(
            text(
                "SELECT to_jsonb(e) AS entry FROM decomp_constituent c "
                "LEFT JOIN assertion_evidence e USING(concept_code,axis,filler_code) "
                "WHERE c.run_id=:run AND c.axis='op:Morphology' "
                "ORDER BY c.concept_code,c.filler_code"
            ),
            {"run": run_id},
        )
        entries = [_entry(row[0], run) for row in result]
    return run, entries


def _entry(payload, run):
    if payload is None:
        raise ValueError("morphology import requires an existing ledger assessment")
    entry = LedgerEntry.model_validate_json(json.dumps(payload))
    if (entry.source_release, entry.scope_root) != (run["ncit_version"], run["scope"]):
        raise ValueError("ledger release/scope differs from stored run")
    return entry


def _item(entry, carrier, anchors):
    disease_path = anchors.source.ancestor_paths(entry.concept_code)[carrier]
    filler_path = anchors.source.ancestor_paths(entry.filler_code)[carrier]
    return EvidenceItem(
        tier=1,
        kind="curated-source",
        source="NCIt",
        source_release=entry.source_release,
        locator=f"{carrier}/P334",
        statement=f"P334 histology carrier {carrier} "
        "supports morphology axis-assignment; "
        f"not equivalence. Disease told path: {' -> '.join(disease_path)}; "
        f"filler told path: {' -> '.join(filler_path)}",
    ).model_dump(mode="json")


def _policy(payload, state):
    for choice in payload["support"]["policy_choices"]:
        if choice["kind"] == "axis-assignment":
            choice["state"] = state


def _reason(payload, kind, rationale):
    reason = {"kind": kind, "rationale": rationale}
    if reason not in payload["needs_sme_reasons"]:
        payload["needs_sme_reasons"].append(reason)
    payload["status"] = "needs-SME"


def _tier_zero_closed(entry):
    return (
        entry.status == "evidence-supported"
        and any(item.tier == 0 for item in entry.evidence)
        and not any(choice.state == "open" for choice in entry.support.policy_choices)
    )


def _attach(payload, entry, matching, anchors):
    for carrier in matching:
        item = _item(entry, carrier, anchors)
        if item not in payload["evidence"]:
            payload["evidence"].append(item)


def assess_morphology(entry, candidates, anchors, site_carriers):
    """Apply the owner-approved disease-anchor rule using existing ledger values."""
    payload = entry.model_dump(mode="json", include=set(LedgerAssessment.model_fields))
    matching = sorted(
        set(candidates).intersection(anchors.source.ancestor_paths(entry.filler_code))
    )
    if _nonmatching_bucket(entry, candidates, anchors, site_carriers) is not None:
        return LedgerAssessment.model_validate_json(json.dumps(payload))
    _attach(payload, entry, matching, anchors)
    if len(candidates) > 1:
        _policy(payload, "open")
        _reason(
            payload,
            "ambiguous",
            "Incomparable disease P334 anchors: " + ", ".join(candidates),
        )
    elif matching:
        _policy(payload, "corroborated")
        _supported_status(payload)
    elif not candidates and not _tier_zero_closed(entry):
        _reason(
            payload,
            "no-evidence",
            "No eligible P334 histology anchor for disease " + entry.concept_code,
        )
    return LedgerAssessment.model_validate_json(json.dumps(payload))


def _supported_status(payload):
    if payload["needs_sme_reasons"]:
        return
    remaining = any(p["state"] == "open" for p in payload["support"]["policy_choices"])
    payload["status"] = "provisional" if remaining else "evidence-supported"


def _unique_bucket(entry, carrier, anchors, site_carriers):
    paths = anchors.source.ancestor_paths(entry.filler_code)
    site_bearing = bool(set(paths).intersection(site_carriers))
    if entry.filler_code == carrier:
        return "equal-site-bearing" if site_bearing else None
    if carrier in paths:
        return (
            "strict-descendant-site-bearing"
            if site_bearing
            else "strict-descendant-site-free"
        )
    if entry.filler_code in anchors.source.ancestor_paths(carrier):
        return "filler-ancestor-of-anchor"
    return "incomparable"


def _nonmatching_bucket(entry, candidates, anchors, site_carriers):
    if len(candidates) == 1:
        return _unique_bucket(entry, candidates[0], anchors, site_carriers)
    return None


async def import_morphology_evidence(
    sessions, run_id, anchors: HistologyAnchors, site_carriers, *, dry_run=False
):
    """Preflight then write assessments, or preview the same rules when dry_run."""
    run, entries = await morphology_inputs(sessions, run_id)
    if anchors.source.source_identity != run["source_identity"]:
        raise ValueError("P334 hierarchy source differs from stored run")
    candidates = {
        code: anchors.for_concept(code) for code in {e.concept_code for e in entries}
    }
    prepared = [
        assess_morphology(e, candidates[e.concept_code], anchors, site_carriers)
        for e in entries
    ]
    return await _import_report(
        sessions, run_id, anchors, entries, candidates, prepared, dry_run, site_carriers
    )


async def _import_report(
    sessions, run_id, anchors, entries, candidates, prepared, dry_run, site_carriers
):
    counts, reasons, nonmatching, transitions = (
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    ledger = EvidenceLedger(sessions)
    for previous, assessment in zip(entries, prepared, strict=True):
        if dry_run:
            payload = reassess_entry(assessment.model_dump(mode="json"), previous, None)
            entry = LedgerEntry.model_validate_json(json.dumps(payload, default=str))
        else:
            entry = await ledger.assess(assessment)
        counts[entry.status] += 1
        transitions[(previous.status, entry.status)] += 1
        reasons.update(r.kind for r in entry.needs_sme_reasons)
        choices = candidates[previous.concept_code]
        bucket = _nonmatching_bucket(previous, choices, anchors, site_carriers)
        if bucket:
            nonmatching[bucket] += 1
    return {
        "run_id": run_id,
        "dry_run": dry_run,
        "assertions": len(entries),
        "by_status": dict(counts),
        "by_reason": dict(reasons),
        "unique_nonmatching": dict(nonmatching),
        "transitions": [
            {"before": a, "after": b, "count": n}
            for (a, b), n in sorted(transitions.items())
        ],
        "disease_anchor_counts": dict(
            Counter(str(len(c)) for c in candidates.values())
        ),
    }
