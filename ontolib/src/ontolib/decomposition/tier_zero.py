"""Import existing stated-filler classification into the assertion ledger."""

from collections import Counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import (
    AssertionPair,
    AssertionSupport,
    EvidenceItem,
    LedgerAssessment,
    NormalizedGroup,
    PolicyChoice,
    SiblingMember,
    SupportContext,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.source_support import ConstituentEvidence


async def import_tier_zero(
    sessions: async_sessionmaker[AsyncSession],
    run_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    provenance = ProvenanceStore(sessions)
    run = await provenance.get_run(run_id)
    if run is None or run.status != "complete":
        raise ValueError("tier-zero import requires a completed stored run")
    async with sessions() as session:
        scope = await session.scalar(
            text("SELECT fingerprint->>'scope_root' FROM decomp_run WHERE id=:run"),
            {"run": run_id},
        )
        result = await session.execute(
            text(
                "SELECT DISTINCT concept_code FROM decomp_constituent "
                "WHERE run_id=:run ORDER BY concept_code"
            ),
            {"run": run_id},
        )
        codes = list(result.scalars())
    ledger = EvidenceLedger(sessions)
    sources = Counter()
    # Validate the entire input before the first write; dry-run follows the same path.
    assessments = []
    for code in codes:
        evidence = await provenance.constituent_evidence(run_id, code)
        siblings, groups = await _contexts(sessions, run_id, code)
        for item in evidence:
            assessment = _assessment(item, run.ncit_version, scope, siblings, groups)
            assessments.append(assessment)
            sources[item.support] += 1
    return await _import_report(ledger, assessments, sources, run_id, dry_run)


async def _import_report(ledger, assessments, sources, run_id, dry_run):
    counts, choices = Counter(), Counter()
    minted = 0
    for assessment in assessments:
        entry = assessment if dry_run else await ledger.assess(assessment)
        counts[(entry.axis, entry.status)] += 1
        minted += (
            entry.filler_code.startswith("MINT-") and entry.status == "provisional"
        )
        for choice in entry.support.policy_choices:
            # All imported choices are explicitly open; human history is separate.
            choices[(entry.axis, choice.kind)] += 1
    return _report(run_id, dry_run, len(assessments), sources, minted, counts, choices)


def _report(run_id, dry_run, total, sources, minted, counts, choices):
    return {
        "run_id": run_id,
        "assertions": total,
        "dry_run": dry_run,
        "source_backed": dict(sources),
        "mint_provisional": minted,
        "by_axis_status": [
            {"axis": a, "status": s, "count": n} for (a, s), n in sorted(counts.items())
        ],
        "open_choices": [
            {"axis": a, "kind": k, "count": n} for (a, k), n in sorted(choices.items())
        ],
    }


async def _contexts(sessions, run_id, code):
    params = {"run": run_id, "code": code}
    async with sessions() as session:
        facts = (
            (
                await session.execute(
                    text(
                        "SELECT group_id,role_code,"
                        "COALESCE(filler_code,genus_code) AS filler "
                        "FROM decomp_definition_fact "
                        "WHERE run_id=:run AND concept_code=:code "
                        "ORDER BY group_id,role_code,filler"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        constituents = (
            (
                await session.execute(
                    text(
                        "SELECT axis,filler_code,normalized_group_id,"
                        "normalized_group_label FROM decomp_constituent "
                        "WHERE run_id=:run AND concept_code=:code "
                        "ORDER BY axis,filler_code"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
    siblings, groups = {}, {}
    for fact in facts:
        siblings.setdefault(fact["group_id"], []).append(
            SiblingMember(role_code=fact["role_code"], filler_code=fact["filler"])
        )
    for row in constituents:
        if row["normalized_group_id"] is not None:
            members = tuple(
                AssertionPair(axis=c["axis"], filler_code=c["filler_code"])
                for c in constituents
                if c["normalized_group_id"] == row["normalized_group_id"]
            )
            groups[(row["axis"], row["filler_code"])] = NormalizedGroup(
                label=row["normalized_group_label"], members=members
            )
    return siblings, groups


def _assessment(
    item: ConstituentEvidence, release: str, scope: str, siblings: dict, groups: dict
) -> LedgerAssessment:
    contexts = tuple(
        SupportContext(
            **source.model_dump(exclude={"fact_id", "group_id", "occurrence_id"}),
            siblings=tuple(siblings[source.group_id]),
        )
        for source in item.sources
    )
    choices = tuple(
        PolicyChoice(
            kind=kind,
            policy="stored-engine-choice",
            rationale="Engine choice requires independent corroboration",
            state="open",
        )
        for kind in item.policy_choices
    )
    evidence = _stated_evidence(item, release)
    supported = (
        bool(evidence) and not choices and not item.filler_code.startswith("MINT-")
    )
    return LedgerAssessment(
        concept_code=item.concept_code,
        axis=item.axis,
        filler_code=item.filler_code,
        source_release=release,
        scope_root=scope,
        status="evidence-supported" if supported else "provisional",
        support=AssertionSupport(
            axis_source=item.axis_source,
            contexts=contexts,
            p334=item.p334,
            normalized_group=groups.get((item.axis, item.filler_code)),
            policy_choices=choices,
        ),
        evidence=evidence,
    )


def _stated_evidence(
    item: ConstituentEvidence, release: str
) -> tuple[EvidenceItem, ...]:
    return tuple(
        EvidenceItem(
            tier=0,
            kind="curated-source",
            source="NCIt",
            source_release=release,
            locator=(
                f"{source.anchor_code}/{source.kind}/{source.role_code or 'genus'}/"
                f"{source.filler_code}/{source.structural_path}"
            ),
            statement=(
                f"Stated {source.kind} at {source.anchor_code}: "
                f"{source.role_code or 'genus'} {source.filler_code}"
            ),
            context_indexes=(i,),
        )
        for i, source in enumerate(item.sources)
    )
