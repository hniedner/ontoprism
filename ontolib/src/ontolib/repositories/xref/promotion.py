"""Validation-driven promotion: candidate ``closeMatch`` -> validated ``exactMatch``.

Issue #73 / design §4.4 / D28-D29.  This is the orchestration that turns the
validation primitives (:mod:`ontolib.repositories.xref.validation`) into actual
promotions, and it is the only thing that moves the published caDSR coverage number
(§13.3): every ingested candidate starts life as ``closeMatch/proposed``, and only an
identity-grade ``exactMatch`` counts as covered.

Per candidate:

1. **Corroborate, non-circularly.** Classify the merged NCIt+upstream fragment
   *without* the candidate bridge (:func:`bridge.build_validation_ontology` with
   ``include_bridge=False``).  The candidate is corroborated iff the upstream object is
   inferred to sit under the upstream image of every anchored NCIt ancestor of the
   subject — a fact that can only come from the upstream's own taxonomy plus a
   **separately validated** anchor.  With the bridge present this would be a tautology,
   which is exactly the circularity D28 forbids.
2. **Gather independent evidence** (:mod:`ontolib.repositories.xref.evidence`), minus
   the signal that generated the candidate.
3. **Gate on EL.** Only if the evidence already suffices, classify the merge *with* the
   bridge: ROBOT profiles it to OWL 2 EL, and a non-EL or unsatisfiable merge is
   rejected (never force-classified).
4. **Promote** (:func:`validation.promote_candidate`) and persist with the D29
   lifecycle: ``exactMatch`` + ``lifecycle_state='validated'``.  Un-promoted candidates
   are left exactly as they were, as ``closeMatch/proposed``.

**The oracle.** ELK is an *error detector*, not ground truth for equivalence (D28;
Bodenreider et al.): it can refute a candidate (unsatisfiable merge, or an anchored
ancestor the object demonstrably does not sit under), never prove one — which is why a
promotion additionally requires independent, human- or source-attested evidence.  The
oracle is the stated ``owl:equivalentClass``/``subClassOf`` structure fed to the
reasoner; it is **neither** an ``rdfs:subClassOf+`` walk over NCIt **nor** NCIt's
shipped inferred graph, neither of which materializes defined-class subsumption (D21).
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from typing import TYPE_CHECKING, Any, Protocol

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDFS

from ontolib.repositories.xref.bridge import build_validation_ontology
from ontolib.repositories.xref.candidate_ingest import (
    build_ncit_label_query,
    build_uberon_xref_query,
    build_upstream_labels_query,
)
from ontolib.repositories.xref.evidence import Evidence, gather_evidence, is_independent
from ontolib.repositories.xref.ttl_writer import object_iri
from ontolib.repositories.xref.validation import (
    promote_candidate,
    validate_and_classify,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.repositories.xref.models import SSSOMRecord
    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

logger = logging.getLogger(__name__)

REASON_PROMOTED = "promoted"
REASON_INSUFFICIENT_EVIDENCE = "insufficient_independent_evidence"
REASON_NOT_EL_VALID = "not_el_valid"

_OBO_BASE = "http://purl.obolibrary.org/obo/"
_RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
_OBO_INOWL_NS = "http://www.geneontology.org/formats/oboInOwl#"
_OBO_NCI_PREFIX = "NCI:"
_OWL_THING = URIRef("http://www.w3.org/2002/07/owl#Thing")


class Reasoner(Protocol):
    """Classify a Turtle fragment; return its inferred named-class subsumptions.

    Returns ``None`` when the merge is rejected — it escapes OWL 2 EL, or it is
    unsatisfiable.  A rejected merge is never force-classified (D28).
    """

    def __call__(self, ttl: str) -> set[tuple[str, str]] | None: ...


@dataclass(frozen=True)
class PromotionContext:
    """Every fact the promotion decision needs, fetched once for a whole run."""

    subject_labels: dict[str, set[str]]
    object_labels: dict[str, set[str]]
    object_xrefs: dict[str, set[str]]
    ncit_edges: set[tuple[str, str]]
    upstream_edges: set[tuple[str, str]]
    anchors: tuple[tuple[str, str], ...]
    curated_pairs: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class PromotionOutcome:
    """What happened to one candidate, and why."""

    record: SSSOMRecord
    promoted: SSSOMRecord | None
    evidence: tuple[Evidence, ...]
    el_valid: bool
    reason: str


@dataclass(frozen=True)
class PromotionReport:
    considered: int
    promoted: int
    insufficient_evidence: int
    not_el_valid: int

    def as_dict(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "promoted": self.promoted,
            "insufficient_evidence": self.insufficient_evidence,
            "not_el_valid": self.not_el_valid,
        }


# ── the reasoner boundary ──────────────────────────────────────────────


def parse_inferred_subclasses(path: str | Path) -> set[tuple[str, str]]:
    """Read ROBOT's inferred ontology into ``(child_iri, parent_iri)`` pairs.

    Only named-class edges are kept: blank-node superclasses (restrictions) and
    ``owl:Thing`` carry no subsumption signal we act on.  An ``owl:equivalentClass``
    (the trusted anchor bridges) subsumes in both directions, so it contributes both
    edges — that is what lets the walk cross from the upstream plane to the NCIt one.

    Note these are the **direct** subsumptions: ROBOT transitively reduces the
    hierarchy, so callers must walk it (:func:`_reachable_ancestors`), not test it for
    membership.
    """
    graph = Graph().parse(str(path))
    edges = {
        (str(child), str(parent))
        for child, parent in graph.subject_objects(RDFS.subClassOf)
        if _is_named_edge(child, parent)
    }
    for left, right in graph.subject_objects(OWL.equivalentClass):
        if _is_named_edge(left, right):
            edges.add((str(left), str(right)))
            edges.add((str(right), str(left)))
    return edges


def _is_named_edge(child: object, parent: object) -> bool:
    """Both ends are named classes, and the parent is not ``owl:Thing``."""
    return (
        isinstance(child, URIRef)
        and isinstance(parent, URIRef)
        and parent != _OWL_THING
    )


def elk_reasoner(ttl: str) -> set[tuple[str, str]] | None:
    """Profile-gate *ttl* to OWL 2 EL and classify it with ELK (via ROBOT).

    Returns the inferred named-class subsumptions, or ``None`` when the merge is
    rejected: it fails the EL profile gate, or ROBOT/ELK fails it (an unsatisfiable
    merge exits non-zero — the satisfiability gate).
    """
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "merged.ttl"
        source.write_text(ttl)
        try:
            inferred = validate_and_classify(str(source))
        except CalledProcessError:
            logger.warning("ROBOT/ELK rejected the merged ontology (unsatisfiable)")
            return None
        if inferred is None:
            return None
        try:
            return parse_inferred_subclasses(inferred)
        finally:
            inferred.unlink(missing_ok=True)


# ── structural corroboration (over a merge WITHOUT the candidate) ──────


def _ncit_ancestors(code: str, ncit_edges: set[tuple[str, str]]) -> set[str]:
    """Walk the stated NCIt taxonomy upward from *code*."""
    ancestors: set[str] = set()
    frontier = {code}
    while frontier:
        parents = {p for c, p in ncit_edges if c in frontier} - ancestors - {code}
        ancestors |= parents
        frontier = parents
    return ancestors


def _reachable_ancestors(start: str, edges: set[tuple[str, str]]) -> set[str]:
    """Every class reachable upward from *start* over *edges*.

    ``robot reason`` emits the **direct** subsumptions — the hierarchy is transitively
    reduced, so ``A ⊑ B`` and ``B ⊑ C`` do not yield a stated ``A ⊑ C``.  Corroboration
    therefore has to walk, not test for membership.
    """
    seen: set[str] = set()
    frontier = {start}
    while frontier:
        parents = {p for c, p in edges if c in frontier} - seen - {start}
        seen |= parents
        frontier = parents
    return seen


def is_structurally_corroborated(
    record: SSSOMRecord,
    inferred: set[tuple[str, str]],
    *,
    anchors: Sequence[tuple[str, str]],
    ncit_edges: set[tuple[str, str]],
) -> bool:
    """Does the upstream plane agree with the NCIt plane about where *record* sits?

    For every NCIt ancestor of the subject that carries a **separately validated**
    anchor (``P ≡ P'``), a correct candidate implies the upstream object sits under
    ``P'``.  Corroborated iff there is at least one such anchored ancestor and *every*
    one of them holds: a single anchored ancestor the object demonstrably does not sit
    under is a disagreement between the planes, not a detail.

    No anchored ancestor at all means *no signal* (``False``) — not a failure of the
    candidate, just nothing to corroborate it with.  This is what bootstraps: the
    curated (SME) pairs are the first anchors.
    """
    anchor_map = dict(anchors)
    ancestors = _ncit_ancestors(record.subject_id, ncit_edges)
    anchored = [anchor_map[a] for a in ancestors if a in anchor_map]
    if not anchored:
        return False

    upstream_ancestors = _reachable_ancestors(object_iri(record.object_id), inferred)
    return all(object_iri(curie) in upstream_ancestors for curie in anchored)


# ── the decision ───────────────────────────────────────────────────────


def validate_candidate(
    record: SSSOMRecord,
    ctx: PromotionContext,
    *,
    reasoner: Reasoner,
) -> PromotionOutcome:
    """Decide whether *record* earns an identity-grade bridge.  See module docstring."""
    candidate = (record.subject_id, record.object_id)
    # A curated pair is a trusted anchor for *other* candidates; for itself it is the
    # circularity we forbid, so drop it before assembling this candidate's merges.
    anchors = tuple(a for a in ctx.anchors if a != candidate)

    corroboration_merge = build_validation_ontology(
        record,
        ncit_edges=ctx.ncit_edges,
        upstream_edges=ctx.upstream_edges,
        anchors=anchors,
        include_bridge=False,
    )
    inferred = reasoner(corroboration_merge)
    corroborated = inferred is not None and is_structurally_corroborated(
        record, inferred, anchors=anchors, ncit_edges=ctx.ncit_edges
    )

    evidence = tuple(
        gather_evidence(
            record,
            subject_labels=ctx.subject_labels.get(record.subject_id, set()),
            object_labels=ctx.object_labels.get(record.object_id, set()),
            object_xref_codes=ctx.object_xrefs.get(record.object_id, set()),
            curated_pairs=ctx.curated_pairs,
            structurally_corroborated=corroborated,
        )
    )

    if not is_independent(evidence):
        return PromotionOutcome(
            record=record,
            promoted=None,
            evidence=evidence,
            el_valid=False,
            reason=REASON_INSUFFICIENT_EVIDENCE,
        )

    bridged_merge = build_validation_ontology(
        record,
        ncit_edges=ctx.ncit_edges,
        upstream_edges=ctx.upstream_edges,
        anchors=anchors,
        include_bridge=True,
    )
    el_valid = reasoner(bridged_merge) is not None
    promoted = promote_candidate(record, evidence, el_valid=el_valid)

    return PromotionOutcome(
        record=record,
        promoted=promoted,
        evidence=evidence,
        el_valid=el_valid,
        reason=REASON_PROMOTED if promoted else REASON_NOT_EL_VALID,
    )


def promote_candidates(
    records: Sequence[SSSOMRecord],
    ctx: PromotionContext,
    *,
    reasoner: Reasoner,
) -> tuple[list[SSSOMRecord], PromotionReport]:
    """Run :func:`validate_candidate` over *records*; return promotions + a report."""
    promoted: list[SSSOMRecord] = []
    insufficient = not_el_valid = 0

    for record in records:
        outcome = validate_candidate(record, ctx, reasoner=reasoner)
        if outcome.promoted is not None:
            promoted.append(outcome.promoted)
        elif outcome.reason == REASON_INSUFFICIENT_EVIDENCE:
            insufficient += 1
        else:
            not_el_valid += 1

    return promoted, PromotionReport(
        considered=len(records),
        promoted=len(promoted),
        insufficient_evidence=insufficient,
        not_el_valid=not_el_valid,
    )


# ── context loading (SPARQL over both planes) ──────────────────────────


def build_ncit_edges_query(codes: Sequence[str]) -> str:
    """Stated NCIt named-class ``subClassOf`` edges on the ancestor paths of *codes*."""
    iris = " ".join(f"<{NCIT_NS}{c}>" for c in sorted(codes))
    return f"""\
PREFIX rdfs: <{_RDFS_NS}>
SELECT DISTINCT ?child ?parent WHERE {{
    GRAPH <{STATED_GRAPH_IRI}> {{
        VALUES ?seed {{ {iris} }}
        ?seed rdfs:subClassOf* ?child .
        ?child rdfs:subClassOf ?parent .
        FILTER(isIRI(?parent))
        FILTER(STRSTARTS(STR(?parent), "{NCIT_NS}"))
    }}
}}
"""


def build_upstream_edges_query(curies: Sequence[str]) -> str:
    """Stated upstream named-class ``subClassOf`` edges on the ancestor paths."""
    iris = " ".join(f"<{object_iri(c)}>" for c in sorted(curies))
    return f"""\
PREFIX rdfs: <{_RDFS_NS}>
SELECT DISTINCT ?child ?parent WHERE {{
    VALUES ?seed {{ {iris} }}
    ?seed rdfs:subClassOf* ?child .
    ?child rdfs:subClassOf ?parent .
    FILTER(isIRI(?parent))
    FILTER(STRSTARTS(STR(?parent), "{_OBO_BASE}"))
}}
"""


def _curie(iri: str) -> str | None:
    if not iri.startswith(_OBO_BASE):
        return None
    suffix = iri.removeprefix(_OBO_BASE)
    if "_" not in suffix:
        return None
    prefix, local = suffix.split("_", 1)
    return f"{prefix}:{local}"


async def _subject_labels(
    client: OxigraphHttpClient, subjects: Sequence[str]
) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = {}
    for row in await client.select(build_ncit_label_query(list(subjects))):
        code, label = row.get("code"), row.get("label")
        if code and label:
            labels.setdefault(code, set()).add(label)
    return labels


async def _object_labels(
    client: OxigraphHttpClient, objects: set[str]
) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = {}
    for row in await client.select(build_upstream_labels_query()):
        iri, label = row.get("concept"), row.get("label")
        curie = _curie(str(iri)) if iri else None
        if curie in objects and label:
            labels.setdefault(str(curie), set()).add(label)
    return labels


async def _object_xrefs(
    client: OxigraphHttpClient, objects: set[str]
) -> dict[str, set[str]]:
    xrefs: dict[str, set[str]] = {}
    for row in await client.select(build_uberon_xref_query()):
        iri, xref = row.get("upstream"), row.get("xref")
        curie = _curie(str(iri)) if iri else None
        if curie in objects and xref and str(xref).startswith(_OBO_NCI_PREFIX):
            xrefs.setdefault(str(curie), set()).add(
                str(xref).removeprefix(_OBO_NCI_PREFIX)
            )
    return xrefs


async def _ncit_edges(
    client: OxigraphHttpClient, subjects: Sequence[str]
) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for row in await client.select(build_ncit_edges_query(list(subjects))):
        child, parent = row.get("child"), row.get("parent")
        if child and parent:
            edges.add(
                (str(child).removeprefix(NCIT_NS), str(parent).removeprefix(NCIT_NS))
            )
    return edges


async def _upstream_edges(
    client: OxigraphHttpClient, objects: Sequence[str]
) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for row in await client.select(build_upstream_edges_query(list(objects))):
        child = _curie(str(row.get("child") or ""))
        parent = _curie(str(row.get("parent") or ""))
        if child and parent:
            edges.add((child, parent))
    return edges


async def load_promotion_context(
    ncit_client: OxigraphHttpClient,
    uberon_client: OxigraphHttpClient,
    records: Sequence[SSSOMRecord],
    *,
    curated_pairs: frozenset[tuple[str, str]],
    validated_anchors: Sequence[tuple[str, str]],
) -> PromotionContext:
    """Fetch every fact the run needs about the endpoints of *records*.

    The anchor set is the already-validated bridges plus the curated (SME-signed)
    pairs — the curated set is what bootstraps corroboration when nothing has been
    validated yet.
    """
    subjects = [r.subject_id for r in records]
    objects = [r.object_id for r in records]

    return PromotionContext(
        subject_labels=await _subject_labels(ncit_client, subjects),
        object_labels=await _object_labels(uberon_client, set(objects)),
        object_xrefs=await _object_xrefs(uberon_client, set(objects)),
        ncit_edges=await _ncit_edges(ncit_client, subjects),
        upstream_edges=await _upstream_edges(uberon_client, objects),
        anchors=tuple(dict.fromkeys([*validated_anchors, *sorted(curated_pairs)])),
        curated_pairs=curated_pairs,
    )


# ── persistence (D29 lifecycle) ────────────────────────────────────────


async def persist_promotions(
    store: XrefStore,
    promoted: Sequence[SSSOMRecord],
    report: PromotionReport,
    *,
    run_id: str | None = None,
    source: str = "promotion",
) -> str:
    """Write the promoted ``exactMatch/validated`` records as their own xref run.

    Promotions are additive: the original ``closeMatch/proposed`` candidates are left
    untouched, so a promotion is auditable against the candidate it came from.
    """
    rid = run_id or uuid.uuid4().hex
    ncit_version = promoted[0].subject_source_version if promoted else "unknown"
    source_version = promoted[0].object_source_version if promoted else "unknown"

    await store.upsert_run(
        run_id=rid,
        source=source,
        ncit_version=ncit_version,
        source_version=source_version,
    )
    await store.upsert_records(rid, list(promoted))
    await store.update_run_metrics(rid, report.as_dict())
    return rid


async def run_promotion(
    store: XrefStore,
    ncit_client: OxigraphHttpClient,
    uberon_client: OxigraphHttpClient,
    *,
    curated_pairs: frozenset[tuple[str, str]] = frozenset(),
    reasoner: Reasoner = elk_reasoner,
) -> dict[str, Any]:  # pragma: no cover — integration-only orchestration
    """Promote every proposed candidate in the store, then quarantine stale bridges."""
    candidates = await store.proposed_candidates()
    anchors = await store.validated_anchors()
    ctx = await load_promotion_context(
        ncit_client,
        uberon_client,
        candidates,
        curated_pairs=curated_pairs,
        validated_anchors=anchors,
    )
    promoted, report = promote_candidates(candidates, ctx, reasoner=reasoner)
    run_id = await persist_promotions(store, promoted, report)

    quarantined = 0
    if promoted:
        quarantined = await store.quarantine_stale(
            ncit_version=promoted[0].subject_source_version,
            source_version=promoted[0].object_source_version,
        )

    return {**report.as_dict(), "run_id": run_id, "quarantined": quarantined}
