"""Read-only source assembly for the bounded G1 neoplasm review sample."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import get_settings
from backend.db import make_sessionmaker
from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.models import GenusDefinitionFact, RestrictionDefinitionFact
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.scope import read_scope_hierarchy_edges
from ontolib.decomposition.semantic_identity import routing_implementation_identity
from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.sparql_transport import safe_iri

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ontolib.decomposition.scope import HierarchyEdge
    from ontolib.repositories.cadsr.models import CdeSummary
    from ontolib.terminologies.ncit.models import ConceptDetail

_ORACLE_SIZE = 20
_RANDOM_SIZE = 30
_PACKET_SIZE = _ORACLE_SIZE + _RANDOM_SIZE
_FRAME_SIZE = 1_000
_P334_BATCH = 80
type Cohort = Literal["oracle", "random"]


@dataclass(frozen=True, slots=True)
class ExpertConcept:
    """One reviewable source observation; no ICD-O value or adjudicated verdict."""

    code: str
    cohort: Cohort
    label: str
    outcome: str
    outcome_reason: str
    parents: tuple[tuple[str, str], ...]
    stated_roles: tuple[tuple[str, str, str, str], ...]
    constituents: tuple[tuple[str, str, str, bool, str], ...]
    flags: tuple[tuple[str, str], ...]
    genus: tuple[tuple[str, str], ...]
    anchors: tuple[tuple[str, str, bool], ...]
    cdes: Mapping[str, tuple[tuple[str, str, str], ...] | None]

    @property
    def anchor_count(self) -> int:
        return len(self.anchors)


def _require_current_frame(run, fingerprint) -> None:
    """Reject incomplete, graph-loaded, or semantically stale frame runs."""
    if run is None or run.status != "complete":
        raise ValueError("frame run is not complete")
    if (
        fingerprint.branch != DecompositionBranch.NEOPLASM
        or len(fingerprint.worklist) != _FRAME_SIZE
        or fingerprint.load_mode != "none"
        or fingerprint.algorithm_version
        != branch_spec(DecompositionBranch.NEOPLASM).algorithm_version
        or fingerprint.routing_implementation_identity
        != routing_implementation_identity()
    ):
        raise ValueError("frame run does not match current neoplasm routing")


def select_packet_sample(
    oracle: Sequence[str], decomposed: Iterable[str], *, seed: int
) -> list[tuple[str, Cohort]]:
    """Append an unbiased seeded draw from decomposed, non-oracle frame codes."""
    if len(oracle) != _ORACLE_SIZE or len(set(oracle)) != _ORACLE_SIZE:
        raise ValueError("packet requires 20 unique oracle concepts")
    eligible = sorted(set(decomposed) - set(oracle))
    if len(eligible) < _RANDOM_SIZE:
        raise ValueError("not enough eligible decomposed concepts")
    sample = random.Random(seed).sample(eligible, _RANDOM_SIZE)  # noqa: S311
    result: list[tuple[str, Cohort]] = [(code, "oracle") for code in oracle]
    result.extend((code, "random") for code in sample)
    return result


def told_parent_index(edges: Sequence[HierarchyEdge]) -> dict[str, frozenset[str]]:
    """Index the complete named subclass and definition-genus edge collection once."""
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        parents[edge.child].add(edge.parent)
    return {child: frozenset(values) for child, values in parents.items()}


def histology_anchors(
    concept: str, parents: Mapping[str, frozenset[str]], carriers: set[str]
) -> tuple[str, ...]:
    """Keep all incomparable most-specific carriers in the combined told DAG."""

    def ancestors(start: str) -> set[str]:
        reached: set[str] = set()
        pending = list(parents.get(start, ()))
        while pending:
            parent = pending.pop()
            if parent not in reached:
                reached.add(parent)
                pending.extend(parents.get(parent, frozenset()) - reached)
        return reached

    candidates = (ancestors(concept) | {concept}) & carriers
    return tuple(
        sorted(
            code
            for code in candidates
            if not any(
                code in ancestors(other) for other in candidates if other != code
            )
        )
    )


def review_reason(axis: str, filler: str, flagged: bool, ambiguous: bool) -> str:
    """Preserve the generic reason without assigning an expert verdict."""
    reasons = []
    if flagged:
        reasons.append(f"constituent {axis} / {filler} needs review")
    if ambiguous:
        reasons.append("axis assignment is ambiguous")
    return "; ".join(reasons)


def assemble_concept(
    detail: ConceptDetail,
    *,
    cohort: Cohort,
    outcome: str,
    outcome_reason: str,
    pairs: Sequence[tuple[str, str, bool, bool]],
    labels: Mapping[str, str],
    genus: Sequence[str],
    anchors: Sequence[str],
    cdes: Mapping[str, Sequence[CdeSummary] | None],
    flags: Sequence[tuple[str, str]],
    stated_roles: Sequence[tuple[str, str, str, str]] = (),
) -> ExpertConcept:
    """Combine existing typed read results, preserving empty mapped-CDE sets."""
    if not detail.label or not outcome or not outcome_reason:
        raise ValueError("NCIt label and engine outcome with reason are required")
    if any(not kind or not reason for kind, reason in flags):
        raise ValueError("each review flag requires a kind and reason")
    return ExpertConcept(
        code=detail.code,
        cohort=cohort,
        label=detail.label,
        outcome=outcome,
        outcome_reason=outcome_reason,
        parents=tuple((p.code, p.label or "") for p in detail.parents),
        stated_roles=tuple(
            sorted(
                set(stated_roles)
                | {
                    (
                        role.relation,
                        role.relation_label or "",
                        role.target.code,
                        role.target.label or "",
                    )
                    for role in detail.roles
                }
            )
        ),
        constituents=tuple(
            (
                axis,
                filler,
                labels.get(filler, "proposed filler"),
                flagged or ambiguous,
                review_reason(axis, filler, flagged, ambiguous),
            )
            for axis, filler, flagged, ambiguous in pairs
        ),
        flags=tuple(flags),
        genus=tuple((code, labels.get(code, "")) for code in genus),
        anchors=tuple(
            (
                code,
                detail.label if code == detail.code else labels.get(code, ""),
                code == detail.code,
            )
            for code in anchors
        ),
        cdes={
            code: (
                tuple((hit.public_id, hit.version, hit.long_name) for hit in hits)
                if hits is not None
                else None
            )
            for code, hits in cdes.items()
        },
    )


async def sample_from_rehearsal(
    frame_run: str, frame_manifest: Path, oracle_path: Path, *, seed: int
) -> list[tuple[str, Cohort]]:
    """Read only the completed corrected 1,000-concept sampling frame."""
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        store = ProvenanceStore(make_sessionmaker(engine))
        run = await store.get_run(frame_run)
        fingerprint = await store.fingerprint_for_run(frame_run)
        _require_current_frame(run, fingerprint)
        source = json.loads(frame_manifest.read_text())
        codes = tuple(item["code"] for item in source["concepts"])
        if fingerprint.worklist != codes:
            raise ValueError("frame run does not match the 1,000-concept manifest")
        oracle = [
            item["code"] for item in json.loads(oracle_path.read_text())["concepts"]
        ]
        outcomes = await store.work_item_outcomes(frame_run)
        eligible = (
            item.concept_code for item in outcomes if item.outcome == "decomposed"
        )
        sample = select_packet_sample(oracle, eligible, seed=seed)
        if not set(oracle) <= set(codes):
            raise ValueError("the canonical oracle is absent from the sampling frame")
        return sample
    finally:
        await engine.dispose()


async def _p334_carriers(client, codes: set[str]) -> set[str]:
    """Ask only whether each reached NCIt class carries P334, not for its value."""
    reached = sorted(codes)
    found: set[str] = set()
    for offset in range(0, len(reached), _P334_BATCH):
        values = " ".join(
            f"<{safe_iri(code, NCIT_NS)}>"
            for code in reached[offset : offset + _P334_BATCH]
        )
        query = (
            f"PREFIX ncit: <{NCIT_NS}> SELECT DISTINCT ?carrier WHERE {{ "
            f"GRAPH <{STATED_GRAPH_IRI}> {{ VALUES ?carrier {{ {values} }} "
            "?carrier ncit:P334 ?p334 . } }"
        )
        for row in await client.select(query, required_variables={"carrier"}):
            value = row["carrier"]
            if not value.startswith(NCIT_NS):
                raise ValueError("P334 carrier is not an NCIt concept")
            code = value.removeprefix(NCIT_NS)
            if code not in codes:
                raise ValueError("P334 carrier is outside the asked source frame")
            found.add(code)
    return found


async def assemble_run(  # noqa: PLR0915 - bounded join of existing source readers
    run_id: str, sample: Sequence[tuple[str, Cohort]]
) -> tuple[ExpertConcept, ...]:
    """Read 50 selected concepts from the complete file-only seeded frame run."""
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        store = ProvenanceStore(make_sessionmaker(engine))
        run = await store.get_run(run_id)
        fingerprint = await store.fingerprint_for_run(run_id)
        _require_current_frame(run, fingerprint)
        selected_codes = tuple(code for code, _cohort in sample)
        if (
            len(selected_codes) != _PACKET_SIZE
            or len(set(selected_codes)) != _PACKET_SIZE
        ):
            raise ValueError("packet requires 50 unique selected frame concepts")
        if not set(selected_codes) <= set(fingerprint.worklist):
            raise ValueError("selected concept is outside the current frame")
        outcomes = {
            item.concept_code: item for item in await store.work_item_outcomes(run_id)
        }
        decompositions = {
            item.code: item for item in await store.decompositions_for_run(run_id)
        }
        publications = {
            item.concept_code: item
            for item in await store.concept_publications_for_run(run_id)
        }
        if any(
            not set(selected_codes) <= set(rows) for rows in (outcomes, publications)
        ):
            raise ValueError("run is missing selected concept data")
        if any(
            code not in decompositions and outcomes[code].outcome == "decomposed"
            for code in selected_codes
        ):
            raise ValueError("decomposed concept has no persisted decomposition")
        async with ncit_sparql_client(settings.ncit_sparql_url) as client:
            ncit = NcitGraphStore(client)
            edges = await read_scope_hierarchy_edges(client)
            parents = told_parent_index(edges)
            reached = set(selected_codes)
            pending = list(reached)
            while pending:
                for parent in parents.get(pending.pop(), frozenset()) - reached:
                    reached.add(parent)
                    pending.append(parent)
            carriers = await _p334_carriers(client, reached)
            anchors = {
                code: histology_anchors(code, parents, carriers)
                for code in selected_codes
            }
            filler_codes = {
                row.filler_code
                for item in (
                    decompositions[code]
                    for code in selected_codes
                    if code in decompositions
                )
                for row in item.constituents
                if row.filler_code.startswith("C")
            }
            facts_by_code = {
                code: (
                    item.complete_definition.facts if item.complete_definition else ()
                )
                for code, item in decompositions.items()
                if code in selected_codes
            }
            source_codes = (
                {
                    fact.filler_code
                    for facts in facts_by_code.values()
                    for fact in facts
                    if isinstance(fact, RestrictionDefinitionFact)
                }
                | {
                    fact.role_code
                    for facts in facts_by_code.values()
                    for fact in facts
                    if isinstance(fact, RestrictionDefinitionFact)
                }
                | {
                    fact.genus_code
                    for facts in facts_by_code.values()
                    for fact in facts
                    if isinstance(fact, GenusDefinitionFact)
                }
            )
            labels = await ncit.exact_labels_for(
                sorted(filler_codes | carriers | source_codes)
            )
            cadsr = CdeRepository(settings.cadsr_db_path)
            cde_hits = {
                code: cadsr.find_cdes_by_concept(code, limit=1000)
                for code in set(selected_codes) | filler_codes
            }
            records = []
            for code, cohort in sample:
                detail = await ncit.get_concept_detail(code)
                if detail is None:
                    raise ValueError(f"missing stated NCIt concept {code}")
                item = decompositions.get(code)
                publication = publications[code]
                facts = facts_by_code.get(code, ())
                genus = sorted(
                    {
                        fact.genus_code
                        for fact in facts
                        if isinstance(fact, GenusDefinitionFact)
                        and fact.anchor_code == code
                    }
                )
                constituents = item.constituents if item is not None else ()
                local_codes = {code} | {row.filler_code for row in constituents}
                stated_roles = [
                    (
                        fact.role_code,
                        labels[fact.role_code],
                        fact.filler_code,
                        labels[fact.filler_code],
                    )
                    for fact in facts
                    if isinstance(fact, RestrictionDefinitionFact)
                    and fact.anchor_code == code
                ]
                records.append(
                    assemble_concept(
                        detail,
                        cohort=cohort,
                        outcome=publication.outcome,
                        outcome_reason=publication.reason,
                        pairs=[
                            (
                                row.axis,
                                row.filler_code,
                                row.needs_review,
                                row.axis_ambiguous,
                            )
                            for row in constituents
                        ],
                        labels=labels,
                        genus=genus,
                        anchors=anchors[code],
                        cdes={value: cde_hits.get(value) for value in local_codes},
                        flags=[(flag.kind, flag.reason) for flag in publication.flags],
                        stated_roles=stated_roles,
                    )
                )
            return tuple(records)
    finally:
        await engine.dispose()
