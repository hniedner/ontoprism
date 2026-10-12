"""Post-run Uberon corroboration; alignments never establish NCIt identity."""

import json
from collections import Counter, defaultdict

from sqlalchemy import text

from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment, ReviewReason
from ontolib.terminologies.uberon.corroboration import UberonCorroboration

ANATOMY_AXES = (
    "op:PrimarySite",
    "op:PrimarySubsite",
    "op:AssociatedRegion",
    "op:AssociatedSite",
    "op:MetastaticSite",
    "op:NormalTissueOrigin",
)
VERDICTS = ("supported", "contradicted", "no mapping", "mapped but unresolved")


async def _inputs(sessions, run_id):
    async with sessions() as session:
        result = await session.execute(
            text(
                "SELECT status,ncit_version,fingerprint->>'scope_root' AS scope "
                "FROM decomp_run WHERE id=:run"
            ),
            {"run": run_id},
        )
        run = result.mappings().one_or_none()
        if run is None or run["status"] != "complete":
            raise ValueError("tier-one import requires a completed stored run")
        params = {"run": run_id, "axes": list(ANATOMY_AXES)}
        result = await session.execute(
            text(
                "SELECT to_jsonb(e) AS entry "
                "FROM decomp_constituent c LEFT JOIN assertion_evidence e "
                "USING (concept_code,axis,filler_code) "
                "WHERE c.run_id=:run AND c.axis=ANY(:axes) "
                "ORDER BY c.concept_code,c.axis,c.filler_code"
            ),
            params,
        )
        rows = result.mappings().all()
        result = await session.execute(
            text(
                "SELECT DISTINCT concept_code,normalized_axis,source_filler "
                "FROM decomp_occurrence_disposition WHERE run_id=:run "
                "AND normalized_axis=ANY(:axes) AND source_filler<>retained_filler"
            ),
            params,
        )
        collapsed = result.mappings().all()
    assessments, peers, sites = [], defaultdict(set), defaultdict(set)
    for row in rows:
        assessment = _existing(row["entry"], run)
        assessments.append(assessment)
        peers[(assessment.concept_code, assessment.axis)].add(assessment.filler_code)
        if assessment.axis == "op:PrimarySite":
            sites[assessment.concept_code].add(assessment.filler_code)
    for row in collapsed:
        peers[(row["concept_code"], row["normalized_axis"])].add(row["source_filler"])
    return assessments, peers, sites


def _existing(entry, run):
    if entry is None:
        raise ValueError("tier-one import requires existing tier-zero assessments")
    assessment = LedgerAssessment.model_validate_json(
        json.dumps({key: entry[key] for key in LedgerAssessment.model_fields})
    )
    if (assessment.source_release, assessment.scope_root) != (
        run["ncit_version"],
        run["scope"],
    ):
        raise ValueError("ledger release/scope differs from selected run")
    return assessment


async def import_tier_one(sessions, client, run_id, *, oracle_codes=(), dry_run=False):
    """Attach partial corroboration without closing unrelated or compound choices.

    All source reads and assessments are validated before writes. Interrupted imports
    may be rerun: equal evidence items are not appended a second time.
    """
    assessments, peers, sites = await _inputs(sessions, run_id)
    source = await UberonCorroboration.read(client, set().union(*peers.values()))
    counts, oracle, results, prepared = Counter(), Counter(), {}, []
    for assessment in assessments:
        code, axis, filler = (
            assessment.concept_code,
            assessment.axis,
            assessment.filler_code,
        )
        verdict, evidence = source.evidence(filler, axis, peers[(code, axis)])
        counts[(axis, verdict)] += 1
        if code in oracle_codes:
            oracle[(axis, verdict)] += 1
        results[(code, axis, filler)] = verdict
        prepared.append(_assessment(assessment, verdict, evidence))
    if not dry_run:
        ledger = EvidenceLedger(sessions)
        for assessment in prepared:
            await ledger.assess(assessment)
    groups = _groups(sites, results)
    return {
        "run_id": run_id,
        "dry_run": dry_run,
        "source_release": source.release,
        "whole_run": _counts(counts),
        "oracle": _counts(oracle),
        "basis": "Partial anatomy corroboration; policy choices and sites unchanged",
        "multi_primary_site": {
            "basis": "Non-exclusive support/conflict; all sets remain unresolved",
            "concepts": sum(group["concepts"] for group in groups.values()),
            "groups": [
                {"fillers": list(fillers), **dict(counts)}
                for fillers, counts in sorted(groups.items())
            ],
        },
    }


def _groups(sites, results):
    groups = defaultdict(Counter)
    for code, fillers in sites.items():
        if len(fillers) <= 1:
            continue
        group = groups[tuple(sorted(fillers))]
        verdicts = {results[(code, "op:PrimarySite", filler)] for filler in fillers}
        group.update(
            concepts=1,
            supported=int("supported" in verdicts),
            contradicted=int("contradicted" in verdicts),
            unresolved=1,
        )
    return groups


def _reasons(reasons, verdict):
    if verdict == "contradicted":
        reason = ReviewReason(
            kind="contradictory",
            rationale="Explicit Uberon disjointness contradicts D20 anatomy category; "
            "NCIt support and non-identity Uberon statements are attached",
        )
        if reason not in reasons:
            reasons = (*reasons, reason)
    return reasons


def _assessment(assessment, verdict, evidence):
    added = tuple(item for item in evidence if item not in assessment.evidence)
    reasons = _reasons(assessment.needs_sme_reasons, verdict)
    payload = assessment.model_dump(mode="json")
    payload.update(
        evidence=[
            item.model_dump(mode="json") for item in (*assessment.evidence, *added)
        ],
        needs_sme_reasons=[reason.model_dump(mode="json") for reason in reasons],
    )
    if reasons:
        payload["status"] = "needs-SME"
    return LedgerAssessment.model_validate_json(json.dumps(payload))


def _counts(counts):
    return {
        axis: {verdict: counts[(axis, verdict)] for verdict in VERDICTS}
        for axis in sorted({axis for axis, _ in counts})
    }
