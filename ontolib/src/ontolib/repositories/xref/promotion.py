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
   bridge: ROBOT profiles it to OWL 2 EL and ELK classifies it.  A bridge that puts a
   class under two disjoint parents is *refuted* and the candidate is rejected — never
   force-classified.
4. **Promote** (:func:`validation.promote_candidate`) and persist with the D29
   lifecycle: ``exactMatch`` + ``lifecycle_state='validated'``.  Un-promoted candidates
   are left exactly as they were, as ``closeMatch/proposed``.

**The oracle, stated honestly.** ELK here is a **refutation-only** oracle, and it is
worth being precise about what that does and does not buy, because overstating it is how
this module's bugs got in.

* *Positively*, the merge contains no defined-class structure — the edge queries filter
  to named classes (``isIRI``), so existential restrictions never enter it.  Over an
  ontology of declarations + named ``subClassOf`` + named ``equivalentClass``, ELK's
  entailed subsumptions **are** the transitive closure of the stated edges through the
  anchors.  A graph walk would compute the same set.  So this does **not** yet
  materialize the defined-class subsumption D21 is about; that would require carrying
  the restriction fillers into the merge, which is not built.
* *Negatively*, ELK earns its place: it **refutes**.  A bridge that forces a class under
  two disjoint parents makes the merge unsatisfiable, and that is a genuine error
  detection a walk cannot perform.  This power comes **entirely** from the
  ``owl:disjointWith`` axioms carried into the merge — without them a merge of
  subsumptions and equivalences is trivially satisfiable (interpret every class as the
  whole domain) and the gate is decorative.

What is read is the **stated** structure (NCIt's stated named graph), never NCIt's
shipped inferred graph (D21).  And the reasoner never *proves* an equivalence — hence
the independent-evidence gate.  Note the disjointness we actually get is a lower bound:
only binary ``owl:disjointWith`` is fetched (not ``owl:AllDisjointClasses``), NCIt ships
almost none, so in practice the refutation power rests on the upstream plane's axioms.
A run that loads zero of them logs a warning, because in that state "reasoner-validated"
would be a false claim.

**Three-valued reasoning, deliberately.** A merge is *accepted*, *refuted*, or **the
reasoner never ran**.  The third state raises
(:exc:`validation.ReasonerUnavailableError`) and is counted separately, because a
broken reasoner otherwise looks exactly like "no candidate qualified" — a silent
zero-promotion run that reads as a conservative verdict.  A run with any such error is
reported as **failed**, never as a clean zero.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
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
from ontolib.repositories.xref.ttl_writer import SUPPORTED_PREFIXES, object_iri
from ontolib.repositories.xref.validation import (
    ReasonerUnavailableError,
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
REASON_REFUTED = "refuted_by_reasoner"

_OBO_BASE = "http://purl.obolibrary.org/obo/"
_RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
_OWL_NS = "http://www.w3.org/2002/07/owl#"
_OBO_NCI_PREFIX = "NCI:"
_OWL_THING = URIRef(f"{_OWL_NS}Thing")
# Upstream prefixes we can expand back to an IRI; anything else must not enter a merge.
_OBO_PREFIXES = SUPPORTED_PREFIXES

_QUERY_BATCH_SIZE = 500


class PromotionEnvironmentError(RuntimeError):
    """The run cannot produce a meaningful verdict — refuse rather than report zero.

    Raised when the inputs are degenerate (e.g. the stated NCIt graph is not loaded, so
    *no* candidate could ever be corroborated) or when a merge that cannot legitimately
    be refuted is refuted anyway (which means the anchor set itself is inconsistent).
    Both would otherwise surface as a clean ``promoted: 0`` run and be misread as a
    conservative verdict.
    """


class Reasoner(Protocol):
    """Classify a Turtle fragment; return its inferred named-class subsumptions.

    Returns ``None`` **only** when the reasoner *refuted* the merge (it escapes OWL 2
    EL, or it is unsatisfiable).  If the reasoner could not run, it must raise
    :exc:`validation.ReasonerUnavailableError` — never return ``None``, which would
    launder an environment failure into a verdict about the candidate.
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
    disjoints: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PromotionOutcome:
    """What happened to one candidate, and why.

    ``el_valid`` is **tri-state**: ``True`` (ELK classified the bridged merge),
    ``False`` (ELK *refuted* it), or ``None`` (**the gate never ran** — the evidence
    was already insufficient, so we short-circuit rather than pay a JVM to tell us
    something we cannot act on).  A plain ``bool`` would recreate, inside the outcome
    type, the very conflation this module spends its docstring forbidding: a consumer
    counting ``not el_valid`` as "reasoner rejections" would silently include every
    candidate the reasoner never looked at.
    """

    record: SSSOMRecord
    promoted: SSSOMRecord | None
    evidence: tuple[Evidence, ...]
    el_valid: bool | None
    reason: str


@dataclass(frozen=True)
class PromotionReport:
    considered: int
    promoted: int
    insufficient_evidence: int
    refuted: int
    reasoner_errors: int = 0

    def __post_init__(self) -> None:
        # This is the number that lands in xref_run.metrics and moves the published
        # coverage figure (§13.3). A miscount here is exactly the class of bug that
        # matters, so make any future accounting drift fail loudly at the moment it
        # happens rather than quietly publish.
        counted = (
            self.promoted
            + self.insufficient_evidence
            + self.refuted
            + self.reasoner_errors
        )
        if counted != self.considered:
            raise ValueError(
                f"promotion accounting does not balance: considered={self.considered} "
                f"but promoted+insufficient+refuted+errors={counted}"
            )

    @property
    def failed(self) -> bool:
        """Did the reasoner fail to run for any candidate?

        A run with reasoner errors is **not** a clean zero-promotion run, and must not
        be recorded as one.
        """
        return self.reasoner_errors > 0

    def as_dict(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "promoted": self.promoted,
            "insufficient_evidence": self.insufficient_evidence,
            "refuted": self.refuted,
            "reasoner_errors": self.reasoner_errors,
        }


# ── the reasoner boundary ──────────────────────────────────────────────


def _is_named_edge(child: object, parent: object) -> bool:
    """Both ends are named classes, and the parent is not ``owl:Thing``."""
    return (
        isinstance(child, URIRef)
        and isinstance(parent, URIRef)
        and parent != _OWL_THING
    )


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


def elk_reasoner(ttl: str) -> set[tuple[str, str]] | None:
    """Profile-gate *ttl* to OWL 2 EL and classify it with ELK (via ROBOT).

    Returns the inferred named-class subsumptions, or ``None`` iff the reasoner
    **refuted** the merge (non-EL, or unsatisfiable).  Raises
    :exc:`validation.ReasonerUnavailableError` if the reasoner could not run — that is
    not a verdict, and must never be recorded as one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "merged.ttl"
        source.write_text(ttl)
        inferred = validate_and_classify(str(source))
        if inferred is None:
            return None
        try:
            return parse_inferred_subclasses(inferred)
        finally:
            inferred.unlink(missing_ok=True)


# ── structural corroboration (over a merge WITHOUT the candidate) ──────


def _ancestors(seed: str, edges: set[tuple[str, str]]) -> set[str]:
    """Every node reachable upward from *seed* over *edges*."""
    seen: set[str] = set()
    frontier = {seed}
    while frontier:
        parents = {p for c, p in edges if c in frontier} - seen - {seed}
        seen |= parents
        frontier = parents
    return seen


def _reachable_ancestors(start: str, edges: set[tuple[str, str]]) -> set[str]:
    """Every class reachable upward from *start* over the inferred *edges*.

    ``robot reason`` emits the **direct** subsumptions — the hierarchy is transitively
    reduced, so ``A ⊑ B`` and ``B ⊑ C`` do not yield a stated ``A ⊑ C``.  Corroboration
    therefore has to walk, not test for membership.
    """
    return _ancestors(start, edges)


def _scoped_edges(seed: str, edges: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """The subgraph of *edges* on the ancestor paths above *seed*.

    Keeps the merge handed to ELK genuinely small (MIREOT-style), instead of shipping
    the whole run's union graph to a fresh JVM once per candidate.
    """
    nodes = _ancestors(seed, edges) | {seed}
    return {(c, p) for c, p in edges if c in nodes}


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
    anchor image of *every* anchored ancestor holds: a single anchored ancestor the
    object demonstrably does not sit under is a disagreement between the planes, not a
    detail.  (An NCIt class may carry more than one validated upstream image, so the
    anchors are a multimap — collapsing them with ``dict()`` would silently drop the
    disagreeing one.)

    No anchored ancestor at all means *no signal* (``False``) — not a failure of the
    candidate, just nothing to corroborate it with.  This is what bootstraps: the
    curated (SME) pairs are the first anchors.
    """
    anchor_map: dict[str, list[str]] = {}
    for code, curie in anchors:
        anchor_map.setdefault(code, []).append(curie)

    ancestors = _ancestors(record.subject_id, ncit_edges)
    anchored = [curie for a in ancestors for curie in anchor_map.get(a, [])]
    if not anchored:
        return False

    upstream_ancestors = _reachable_ancestors(object_iri(record.object_id), inferred)
    return all(object_iri(curie) in upstream_ancestors for curie in anchored)


# ── the decision ───────────────────────────────────────────────────────


def _merge_for(
    record: SSSOMRecord,
    ctx: PromotionContext,
    anchors: Sequence[tuple[str, str]],
    *,
    include_bridge: bool,
) -> str:
    """Build the candidate's own small merge (its two ancestor paths, not the run's)."""
    return build_validation_ontology(
        record,
        ncit_edges=_scoped_edges(record.subject_id, ctx.ncit_edges),
        upstream_edges=_scoped_edges(record.object_id, ctx.upstream_edges),
        anchors=anchors,
        disjoints=ctx.disjoints,
        include_bridge=include_bridge,
    )


def _evidence_for(
    record: SSSOMRecord, ctx: PromotionContext, *, structurally_corroborated: bool
) -> tuple[Evidence, ...]:
    return tuple(
        gather_evidence(
            record,
            subject_labels=ctx.subject_labels.get(record.subject_id, set()),
            object_labels=ctx.object_labels.get(record.object_id, set()),
            object_xref_codes=ctx.object_xrefs.get(record.object_id, set()),
            curated_pairs=ctx.curated_pairs,
            structurally_corroborated=structurally_corroborated,
        )
    )


def _insufficient(
    record: SSSOMRecord, evidence: tuple[Evidence, ...]
) -> PromotionOutcome:
    return PromotionOutcome(
        record=record,
        promoted=None,
        evidence=evidence,
        el_valid=None,  # the EL gate never ran — not the same as "refuted"
        reason=REASON_INSUFFICIENT_EVIDENCE,
    )


def validate_candidate(
    record: SSSOMRecord,
    ctx: PromotionContext,
    *,
    reasoner: Reasoner,
    extra_anchors: Sequence[tuple[str, str]] = (),
) -> PromotionOutcome:
    """Decide whether *record* earns an identity-grade bridge.  See module docstring."""
    candidate = (record.subject_id, record.object_id)
    # A curated pair is a trusted anchor for *other* candidates; for itself it is the
    # circularity we forbid, so drop it before assembling this candidate's merges.
    anchors = tuple(a for a in (*ctx.anchors, *extra_anchors) if a != candidate)

    # Short-circuit before paying for a JVM. Corroboration is worth at most ONE
    # evidence kind, so a candidate that cannot clear the independence bar even when
    # granted it cannot be saved by the reasoner — and that is the majority of the
    # corpus (a lexical candidate with no back-xref and no curation can only ever reach
    # one kind, since its generating signal is dropped). Two ROBOT invocations apiece
    # across a full candidate set is the difference between a run that finishes and one
    # that does not.
    best_case = _evidence_for(record, ctx, structurally_corroborated=True)
    if not is_independent(best_case):
        return _insufficient(
            record, _evidence_for(record, ctx, structurally_corroborated=False)
        )

    inferred = reasoner(_merge_for(record, ctx, anchors, include_bridge=False))
    if inferred is None:
        # The bridge-free merge carries no candidate axiom.  If the reasoner refutes it,
        # the *anchor set* contradicts the taxonomy — a data-integrity alarm.  Reporting
        # it as "this candidate is not corroborated" would bury it.
        raise PromotionEnvironmentError(
            f"the reasoner refuted the bridge-free merge for {candidate}: the merge "
            "contains no candidate bridge, so this means the validated anchors are "
            "inconsistent with the stated taxonomy. Refusing to read that as a verdict "
            "about this candidate."
        )

    corroborated = is_structurally_corroborated(
        record, inferred, anchors=anchors, ncit_edges=ctx.ncit_edges
    )
    evidence = _evidence_for(record, ctx, structurally_corroborated=corroborated)
    if not is_independent(evidence):
        return _insufficient(record, evidence)

    bridged = reasoner(_merge_for(record, ctx, anchors, include_bridge=True))
    el_valid = bridged is not None
    promoted = promote_candidate(record, evidence, el_valid=el_valid)

    return PromotionOutcome(
        record=record,
        promoted=promoted,
        evidence=evidence,
        el_valid=el_valid,
        reason=REASON_PROMOTED if promoted else REASON_REFUTED,
    )


def _refuse_degenerate_context(
    records: Sequence[SSSOMRecord], ctx: PromotionContext
) -> None:
    """Refuse a run whose inputs make every candidate fail for a non-candidate reason.

    The guard is symmetric across both planes on purpose.  An unloaded *upstream* store
    is if anything the likelier misconfiguration — `ncit_sparql_url` is exercised by
    every other build step, `uberon_sparql_url` only by ingest and this pass — and it
    produces the same clean ``promoted: 0, status: completed, exit 0`` that would read
    as a conservative verdict.  That is the lie this module exists to abolish.
    """
    if not records:
        return
    missing = [
        name
        for name, loaded in (
            (
                f"stated NCIt subClassOf edges (graph <{STATED_GRAPH_IRI}>)",
                ctx.ncit_edges,
            ),
            ("upstream subClassOf edges", ctx.upstream_edges),
            ("upstream labels", ctx.object_labels),
        )
        if not loaded
    ]
    if missing:
        raise PromotionEnvironmentError(
            f"nothing loaded for {len(records)} candidates: {', '.join(missing)}. The "
            "endpoints are not loaded or not configured (run `data-build owl` / check "
            "`uberon_sparql_url`). Every candidate would fail corroboration for a "
            "reason unrelated to the candidate; refusing to run rather than reporting "
            "a zero that looks conservative."
        )


def promote_candidates(
    records: Sequence[SSSOMRecord],
    ctx: PromotionContext,
    *,
    reasoner: Reasoner,
) -> tuple[list[SSSOMRecord], PromotionReport]:
    """Run :func:`validate_candidate` over *records*; return promotions + a report.

    A reasoner that cannot run is counted in ``reasoner_errors`` and does **not**
    silently become "this candidate did not qualify"; the run is then reported failed.
    """
    _refuse_degenerate_context(records, ctx)

    promoted: list[SSSOMRecord] = []
    insufficient = refuted = errors = 0
    # Promotions made *in this run* become trusted anchors for the candidates after them
    # — and, critically, guard identity itself: ingest legitimately produces several
    # upstream candidates for one NCIt code, and promoting two of them would assert
    # C ≡ U1 and C ≡ U2, hence U1 ≡ U2 — an equivalence no reasoner saw and nobody
    # curated. The second candidate is now validated *against* the first (which is in
    # its anchor set), so it is refuted rather than silently co-asserted.
    minted: list[tuple[str, str]] = []

    for record in records:
        try:
            outcome = validate_candidate(
                record, ctx, reasoner=reasoner, extra_anchors=minted
            )
        except ReasonerUnavailableError as exc:
            errors += 1
            logger.error(
                "reasoner unavailable for %s -> %s (NOT a verdict): %s",
                record.subject_id,
                record.object_id,
                exc,
            )
            continue

        if outcome.promoted is not None:
            promoted.append(outcome.promoted)
            minted.append((record.subject_id, record.object_id))
        elif outcome.reason == REASON_INSUFFICIENT_EVIDENCE:
            insufficient += 1
        else:
            refuted += 1

    return promoted, PromotionReport(
        considered=len(records),
        promoted=len(promoted),
        insufficient_evidence=insufficient,
        refuted=refuted,
        reasoner_errors=errors,
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
    """Stated upstream named-class ``subClassOf`` edges on the ancestor paths.

    The parent filter is restricted to the prefixes we can actually expand back to an
    IRI (``ttl_writer._PREFIX_BASE``).  A real Uberon walk climbs into ``BFO_``/``GO_``/
    ``PATO_`` upper-ontology classes; admitting them here would hand
    ``build_validation_ontology`` a CURIE it cannot expand, and the resulting
    ``KeyError`` would abort the whole run — discarding every promotion computed so far
    — on the first real dataset.
    """
    iris = " ".join(f"<{object_iri(c)}>" for c in sorted(curies))
    supported = " || ".join(
        f'STRSTARTS(STR(?parent), "{_OBO_BASE}{prefix}_")' for prefix in _OBO_PREFIXES
    )
    return f"""\
PREFIX rdfs: <{_RDFS_NS}>
SELECT DISTINCT ?child ?parent WHERE {{
    VALUES ?seed {{ {iris} }}
    ?seed rdfs:subClassOf* ?child .
    ?child rdfs:subClassOf ?parent .
    FILTER(isIRI(?parent))
    FILTER({supported})
}}
"""


def build_disjoint_query(*, base_iri: str, graph_iri: str | None = None) -> str:
    """``owl:disjointWith`` pairs among named classes under *base_iri*.

    These are the axioms that let the reasoner refute a bridge at all; without them the
    satisfiability gate is decorative (see :mod:`bridge`).

    *graph_iri* scopes the NCIt plane to the **stated** graph — the default graph holds
    the *inferred* build (``owl_load``), and reading the reasoner's refutation power out
    of a shipped inferred graph is exactly what D21 forbids.

    Note this matches only binary ``owl:disjointWith``.  ``owl:AllDisjointClasses`` (an
    n-ary form OBO ontologies also use) is **not** picked up, so the refutation power we
    actually get is a lower bound on what the sources assert.
    """
    where = f"""\
    ?left owl:disjointWith ?right .
    FILTER(isIRI(?left) && isIRI(?right))
    FILTER(STRSTARTS(STR(?left), "{base_iri}"))
    FILTER(STRSTARTS(STR(?right), "{base_iri}"))"""
    body = f"GRAPH <{graph_iri}> {{\n{where}\n    }}" if graph_iri else where
    return f"""\
PREFIX owl: <{_OWL_NS}>
SELECT DISTINCT ?left ?right WHERE {{
    {body}
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


def _batches(
    items: Sequence[str], size: int = _QUERY_BATCH_SIZE
) -> list[Sequence[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _subject_labels(
    client: OxigraphHttpClient, subjects: Sequence[str]
) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = {}
    for batch in _batches(subjects):
        for row in await client.select(build_ncit_label_query(list(batch))):
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
    for batch in _batches(subjects):
        for row in await client.select(build_ncit_edges_query(list(batch))):
            child, parent = row.get("child"), row.get("parent")
            if child and parent:
                edges.add(
                    (
                        str(child).removeprefix(NCIT_NS),
                        str(parent).removeprefix(NCIT_NS),
                    )
                )
    return edges


async def _upstream_edges(
    client: OxigraphHttpClient, objects: Sequence[str]
) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for batch in _batches(objects):
        for row in await client.select(build_upstream_edges_query(list(batch))):
            child = _curie(str(row.get("child") or ""))
            parent = _curie(str(row.get("parent") or ""))
            if child and parent:
                edges.add((child, parent))
    return edges


async def _disjoints(
    ncit_client: OxigraphHttpClient, uberon_client: OxigraphHttpClient
) -> tuple[tuple[str, str], ...]:
    pairs: set[tuple[str, str]] = set()
    for client, base, graph in (
        (ncit_client, NCIT_NS, STATED_GRAPH_IRI),
        (uberon_client, _OBO_BASE, None),
    ):
        query = build_disjoint_query(base_iri=base, graph_iri=graph)
        for row in await client.select(query):
            left, right = row.get("left"), row.get("right")
            if left and right:
                pairs.add((str(left), str(right)))
    return tuple(sorted(pairs))


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

    ctx = PromotionContext(
        subject_labels=await _subject_labels(ncit_client, subjects),
        object_labels=await _object_labels(uberon_client, set(objects)),
        object_xrefs=await _object_xrefs(uberon_client, set(objects)),
        ncit_edges=await _ncit_edges(ncit_client, subjects),
        upstream_edges=await _upstream_edges(uberon_client, objects),
        anchors=tuple(dict.fromkeys([*validated_anchors, *sorted(curated_pairs)])),
        curated_pairs=curated_pairs,
        disjoints=await _disjoints(ncit_client, uberon_client),
    )
    logger.info(
        "promotion context: %d candidates, %d NCIt edges, %d upstream edges, "
        "%d anchors, %d disjointness axioms",
        len(records),
        len(ctx.ncit_edges),
        len(ctx.upstream_edges),
        len(ctx.anchors),
        len(ctx.disjoints),
    )
    if records and not ctx.disjoints:
        logger.warning(
            "no owl:disjointWith axioms loaded — the reasoner has nothing to refute "
            "with, so the satisfiability gate cannot fire and promotion rests entirely "
            "on the evidence policy"
        )
    return ctx


# ── persistence (D29 lifecycle) ────────────────────────────────────────


async def persist_promotions(
    store: XrefStore,
    promoted: Sequence[SSSOMRecord],
    report: PromotionReport,
    *,
    ncit_version: str,
    source_version: str,
    run_id: str | None = None,
    source: str = "promotion",
) -> str:
    """Write the promoted ``exactMatch/validated`` records as their own xref run.

    Promotions are additive: the original ``closeMatch/proposed`` candidates are left
    untouched, so a promotion is auditable against the candidate it came from.  The run
    is stamped with the endpoint versions it was *validated against* — never mined out
    of whichever record happened to sort first.
    """
    rid = run_id or uuid.uuid4().hex
    await store.upsert_run(
        run_id=rid,
        source=source,
        ncit_version=ncit_version,
        source_version=source_version,
    )
    # Re-stamp with the versions this run actually validated against.  The candidate
    # carried its *ingest-time* versions, and promote_candidate copies them through; if
    # they differ from the run's by so much as a character, quarantine_stale (which
    # compares exactly these columns) would demote every row this run just promoted, and
    # the run would still report success.  The row asserts "validated against these
    # endpoint versions" — so it must say which ones.
    stamped = [
        replace(
            r,
            subject_source_version=ncit_version,
            object_source_version=source_version,
        )
        for r in promoted
    ]
    await store.upsert_records(rid, stamped)
    await store.update_run_metrics(
        rid, report.as_dict(), status="failed" if report.failed else "completed"
    )
    return rid


async def run_promotion(
    store: XrefStore,
    ncit_client: OxigraphHttpClient,
    uberon_client: OxigraphHttpClient,
    *,
    ncit_version: str,
    source_version: str,
    curated_pairs: frozenset[tuple[str, str]] = frozenset(),
    reasoner: Reasoner = elk_reasoner,
    source: str = "promotion",
) -> dict[str, Any]:
    """Promote every proposed candidate, then quarantine bridges a release left stale.

    *ncit_version* / *source_version* are the endpoint versions this run validates
    against; they stamp the run and drive the D29 staleness sweep.  The sweep runs
    **unconditionally** — a release makes old bridges stale whether or not this run
    happened to promote anything.
    """
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
    run_id = await persist_promotions(
        store,
        promoted,
        report,
        ncit_version=ncit_version,
        source_version=source_version,
        source=source,
    )

    # The staleness sweep is destructive (it demotes validated bridges) and a run whose
    # reasoner never ran has established nothing — it must not also demote the bridges a
    # working run had validated.
    quarantined = 0
    if report.failed:
        logger.error(
            "skipping the D29 staleness sweep: the reasoner failed for %d candidate(s)"
            ", so this run established nothing and must not demote anything",
            report.reasoner_errors,
        )
    else:
        quarantined = await store.quarantine_stale(
            ncit_version=ncit_version,
            source_version=source_version,
            source=source,
        )

    return {
        **report.as_dict(),
        "run_id": run_id,
        "quarantined": quarantined,
        "status": "failed" if report.failed else "completed",
    }
