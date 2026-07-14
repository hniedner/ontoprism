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
   reachable — via ``subClassOf`` **or** Uberon ``part_of`` (#78) — to sit under the
   upstream image of *some* anchored NCIt ancestor of the subject (``any``, not ``all``:
   under the open-world assumption a non-reached image is *unknown*, not a disagreement)
   — a fact that can only come from the upstream's own taxonomy plus a **separately
   validated** anchor.  With the bridge present this would be a tautology, which is
   exactly the circularity D28 forbids.
2. **Gather independent evidence** (:mod:`ontolib.repositories.xref.evidence`), minus
   the signal(s) that generated the candidate — none, for a candidate both ingest
   passes produced independently, since there each signal corroborates the candidate
   the *other* one generated (D34).
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
  the restriction fillers into the merge, which is not built.  Structural corroboration
  therefore *is* a graph walk — and #78 widens it from ``subClassOf`` alone to
  ``subClassOf`` / ``part_of`` (BFO:0000050), because Uberon relates an organ to its
  system with ``part_of``, not ``subClassOf`` (verified on the live store).  The
  ``part_of`` edges are supplied as stated data straight to the walk
  (:func:`corroboration`), not through ELK, which does not echo existential-restriction
  subsumptions back as named edges.
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
from collections import Counter
from dataclasses import dataclass, field, replace
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
from ontolib.repositories.xref.evidence import (
    SME_CURATION,
    STRUCTURAL_CORROBORATION,
    Evidence,
    gather_evidence,
    is_independent,
)
from ontolib.repositories.xref.ttl_writer import SUPPORTED_PREFIXES, object_iri
from ontolib.repositories.xref.validation import (
    ReasonerUnavailableError,
    promote_candidate,
    validate_and_classify,
)
from ontolib.repositories.xref.vocab import (
    COMPOSITE_MATCHING,
    DATABASE_CROSS_REFERENCE,
    LEXICAL_MATCHING,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from collections.abc import Set as AbstractSet

    from ontolib.repositories.xref.models import SSSOMRecord
    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

logger = logging.getLogger(__name__)

REASON_PROMOTED = "promoted"
REASON_INSUFFICIENT_EVIDENCE = "insufficient_independent_evidence"
REASON_REFUTED = "refuted_by_reasoner"
REASON_CONFLICTING_IDENTITY = "conflicting_identity"
REASON_REASONER_ERROR = "reasoner_error"

_OBO_BASE = "http://purl.obolibrary.org/obo/"
_RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
_OWL_NS = "http://www.w3.org/2002/07/owl#"
# `NCIT:`, not `NCI:` — see `candidate_ingest._OBO_NCIT_PREFIX`.  This one feeds
# `ctx.object_xrefs`, i.e. the XREF_ASSERTION *evidence*: with the wrong prefix it
# stayed empty for every candidate, so nothing machine-generated could ever reach a
# second independent signal.
_OBO_NCIT_PREFIX = "NCIT:"
_OWL_THING = URIRef(f"{_OWL_NS}Thing")
# BFO:0000050 is the OBO ``part_of`` object property.  Uberon carries organ->system
# containment as an existential restriction on it, never as ``subClassOf``.
_BFO_PART_OF = f"{_OBO_BASE}BFO_0000050"
# Upstream prefixes we can expand back to an IRI; anything else must not enter a merge.
_OBO_PREFIXES = SUPPORTED_PREFIXES

_QUERY_BATCH_SIZE = 500

# Preference order when the same pair arrives under several justifications (see
# `_one_per_pair`): the row produced by the most ingest passes wins, because it is the
# one whose evidence policy suppresses the fewest signals.
_JUSTIFICATION_RANK: dict[str, int] = {
    COMPOSITE_MATCHING: 0,
    DATABASE_CROSS_REFERENCE: 1,
    LEXICAL_MATCHING: 2,
}


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
    # Upstream ``(child_curie, parent_curie)`` ``part_of`` (BFO:0000050) edges on the
    # ancestor paths.  Uberon relates an organ to its system with ``part_of``, not
    # ``subClassOf`` (verified on the live store: ``lung rdfs:subClassOf* respiratory
    # system`` is *false*), so without these structural corroboration is near-dead for
    # anatomy — the reason #78 moved from a tie-break spike onto the COV critical path.
    upstream_partof_edges: set[tuple[str, str]] = field(default_factory=set)


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
    conflicting_identity: int = 0
    # Dropped before validation: this build cannot expand their upstream prefix. NOT
    # part of `considered` (they were never scored), but they must be visible — a big
    # drop otherwise reads as a small candidate set.
    skipped_unexpandable: int = 0
    # Of `promoted`: how many rested on curation ALONE (no independent corroborating
    # signal), versus how many the non-circular machinery actually earned.  Without the
    # split, a run that merely imported a curated file reports exactly like a run in
    # which ELK, the anchors and the disjointness axioms did real work — and until #73
    # Option 1 (D33/D34) that was precisely what happened on real data.
    promoted_on_curation_alone: int = 0
    # Of `promoted`: how many the reasoner corroborated through a separately validated
    # anchor. This is the ONLY bucket the validation machinery can claim.
    promoted_with_structural_corroboration: int = 0

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
            + self.conflicting_identity
        )
        if counted != self.considered:
            raise ValueError(
                f"promotion accounting does not balance: considered={self.considered} "
                f"but the outcome buckets sum to {counted}"
            )
        sub_buckets = (
            self.promoted_on_curation_alone
            + self.promoted_with_structural_corroboration
        )
        if sub_buckets > self.promoted:
            raise ValueError(
                f"promoted sub-buckets ({sub_buckets}) exceed promoted "
                f"({self.promoted}) — counters are not mutually exclusive"
            )

    @property
    def promoted_on_source_agreement(self) -> int:
        """Promoted on two independent *source* signals (label + the upstream's xref).

        Real evidence — but the reasoner earned none of it: no anchor was used and no
        structural corroboration fired; ELK merely failed to refute.
        """
        return (
            self.promoted
            - self.promoted_on_curation_alone
            - self.promoted_with_structural_corroboration
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
            "conflicting_identity": self.conflicting_identity,
            "skipped_unexpandable": self.skipped_unexpandable,
            "promoted_on_curation_alone": self.promoted_on_curation_alone,
            "promoted_with_structural_corroboration": (
                self.promoted_with_structural_corroboration
            ),
            "promoted_on_source_agreement": self.promoted_on_source_agreement,
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
            entailed = parse_inferred_subclasses(inferred)
        except Exception as exc:
            # ROBOT exited 0 but wrote something unreadable (truncated, killed mid-
            # write,
            # disk full). That is an environment failure, not a verdict about the merge
            # —
            # letting it escape would abort the run and discard every promotion so far.
            raise ReasonerUnavailableError(
                f"`robot reason` exited 0 but its output is unparseable ({exc}). "
                "This is NOT a verdict about the merge."
            ) from exc
        finally:
            inferred.unlink(missing_ok=True)

    # Sanity-check that ROBOT actually classified *our* merge.  An empty entailment set
    # is `is not None`, so it would sail through the EL gate as `el_valid=True` and
    # *promote* the candidate on a classification that never happened — a false
    # positive, not a conservative failure.
    #
    # The check is on **reachability**, not set inclusion: `robot reason` removes
    # asserted subclass axioms that its own inferences make redundant
    # (`--remove-redundant-subclass-axioms` defaults to true), so a stated edge may
    # legitimately be absent as a *direct* triple while still being entailed.  Requiring
    # the literal axioms back is wrong, and real ELK rejects it — every stated
    # subsumption must still hold in the output, not survive verbatim in it.
    stated = _stated_edges(ttl)
    unentailed = [
        (child, parent)
        for child, parent in stated
        if parent not in _reachable_ancestors(child, entailed)
    ]
    if unentailed:
        raise ReasonerUnavailableError(
            f"`robot reason` exited 0 but {len(unentailed)} of the {len(stated)} "
            "subsumptions it was given are not entailed by its output — it did not "
            "classify this merge. This is NOT a verdict."
        )
    return entailed


def _stated_edges(ttl: str) -> set[tuple[str, str]]:
    """The named-class subsumptions asserted *in the input merge* (not inferred)."""
    graph = Graph().parse(data=ttl, format="turtle")
    edges = {
        (str(c), str(p))
        for c, p in graph.subject_objects(RDFS.subClassOf)
        if _is_named_edge(c, p)
    }
    for left, right in graph.subject_objects(OWL.equivalentClass):
        if _is_named_edge(left, right):
            edges.add((str(left), str(right)))
            edges.add((str(right), str(left)))
    return edges


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


CORROBORATED = "corroborated"
NO_ANCHORED_ANCESTOR = "no_anchored_ancestor"
NOT_ENTAILED = "not_entailed"


def corroboration(
    record: SSSOMRecord,
    inferred: set[tuple[str, str]],
    *,
    anchors: Sequence[tuple[str, str]],
    ncit_edges: set[tuple[str, str]],
    upstream_partof_edges: AbstractSet[tuple[str, str]] = frozenset(),
) -> str:
    """Three-valued for *observability* — but only ``CORROBORATED`` is evidence, and
    **none of these states may veto a promotion**.

    ``NO_ANCHORED_ANCESTOR`` — no anchored ancestor to reason from; the bootstrap has
    not reached here yet.

    ``NOT_ENTAILED`` — there is an anchored ancestor, but the object is not reachable
    (via ``subClassOf`` **or** ``part_of``) to sit under its upstream image.  **This is
    not a contradiction.**  An earlier version of this function treated it as one and
    vetoed the promotion; that was a serious modelling error, caught only by querying
    the real store:

    * Under the **open-world assumption**, "not provably under X" means *unknown*, not
      *false*.  A genuine contradiction can only be established by the reasoner deriving
      ``⊥`` — which the disjointness refutation already does (``REASON_REFUTED``).  The
      veto was therefore both wrong and redundant.
    * Empirically ``subClassOf`` alone is not enough for anatomy.  Uberon relates an
      organ to its system with **``part_of``**, not ``subClassOf``: on the live store,
      ``lung rdfs:subClassOf* respiratory system`` is **false**.  So a ``subClassOf``-
      only veto fired on the canonical *correct* pair (NCIt Lung -> Uberon lung, under
      the ``Respiratory System Organ ≡ respiratory system`` anchor), pinning coverage at
      zero while logging "the two ontologies disagree" — a confident, false explanation.

    **The reachability is mixed ``subClassOf`` / ``part_of`` (#78).** The upstream image
    is reached when the object is a *subclass or part* of it.  This function's walk is a
    plain transitive closure over the edges it is *given* (``inferred`` plus the
    ``part_of`` edges passed in), realising the sound ``subClassOf ∘ part_of ⊑ part_of``
    propagation.  Note the *deployed* reach is narrower than this walk primitive: its
    caller feeds ``part_of`` edges from :func:`build_upstream_partof_query`, which
    gathers only a single ``part_of`` hop off the object's — and each anchor's —
    ``subClassOf*`` cone, so end-to-end the pipeline reaches ``subClassOf*`` and
    ``subClassOf* ∘ part_of`` — *not* transitive ``part_of ∘ part_of`` off the cone
    (see D32).  On the live store the canonical path is
    ``lung ⊑* respiration organ`` (subClassOf) then ``respiration organ part_of
    respiratory system`` — a single hop; neither leg reaches it alone.

    *Honesty note.* ``part_of`` is supplied here as **stated** graph edges, not as an
    ELK entailment: ``robot reason`` classifies over named ``subClassOf``/
    ``equivalentClass`` and does not echo existential-restriction subsumptions back as
    named edges, so pushing ``part_of`` through the reasoner would not surface in
    *inferred*.  This keeps corroboration a structural graph walk — which, as the module
    docstring states, is exactly what ELK's *positive* entailments already reduce to
    over this fragment.  ELK's distinct contribution stays the *refutation* gate.
    """
    anchored = _anchored_images(record.subject_id, anchors, ncit_edges)
    if not anchored:
        return NO_ANCHORED_ANCESTOR

    # part_of edges are stored as CURIEs (like upstream_edges); lift them into the same
    # full-IRI space as ELK's inferred subClassOf edges so the single walk can cross
    # freely between the two relations.
    partof_iri_edges = {
        (object_iri(child), object_iri(parent))
        for child, parent in upstream_partof_edges
    }
    reachable = _reachable_ancestors(
        object_iri(record.object_id), inferred | partof_iri_edges
    )
    # `any`, not `all`.  Under the open-world reading, a non-entailed anchor image means
    # *unknown* — it cannot cancel a different anchored ancestor that IS entailed.
    # (`all` was the reverted veto's semantics: on the live store lung ⊑* organ is true
    # while lung ⊑* respiratory system is false, so `all` would silently withhold the
    # corroboration the organ anchor genuinely established.)
    if any(object_iri(curie) in reachable for curie in anchored):
        return CORROBORATED
    return NOT_ENTAILED


def _anchored_images(
    subject_id: str,
    anchors: Sequence[tuple[str, str]],
    ncit_edges: set[tuple[str, str]],
) -> list[str]:
    """Upstream CURIEs of the anchors whose NCIt code is an ancestor of *subject_id*."""
    anchor_map: dict[str, list[str]] = {}
    for code, curie in anchors:
        anchor_map.setdefault(code, []).append(curie)
    ancestors = _ancestors(subject_id, ncit_edges)
    return [curie for a in ancestors for curie in anchor_map.get(a, [])]


# ── the decision ───────────────────────────────────────────────────────


def _live_anchors(
    record: SSSOMRecord,
    ctx: PromotionContext,
    anchors: Sequence[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """The anchors that can actually affect this candidate's merge.

    Symmetric on purpose.  Keeping only anchors whose NCIt code is an ancestor of the
    subject kills every NCIt-plane refutation *by construction*: a bridge is refuted via
    NCIt disjointness precisely when the anchored class is **not** an ancestor of the
    subject — that non-membership IS the contradiction.  A one-sided filter left NCIt's
    disjointness axioms fetched, shipped to the JVM, and unable to ever fire.
    """
    subject_cone = _ancestors(record.subject_id, ctx.ncit_edges) | {record.subject_id}
    object_cone = _ancestors(record.object_id, ctx.upstream_edges) | {record.object_id}
    return tuple(a for a in anchors if a[0] in subject_cone or a[1] in object_cone)


def _merge_for(
    record: SSSOMRecord,
    ctx: PromotionContext,
    anchors: Sequence[tuple[str, str]],
    *,
    include_bridge: bool,
) -> str:
    """Build the candidate's own small merge — and make sure the anchors can *bite*.

    Scoping the taxonomy to the candidate's own two endpoints (which is all the first
    cut did) quietly neuters the whole gate: an anchor contributes ``P ≡ P'`` and a
    declaration of ``P'``, but **none of P''s own ancestor edges**, so ``P'`` enters the
    merge as a parentless floating class.  ELK can then never walk from it to a branch
    where a disjointness axiom could fire, and the refutation gate under-fires across
    the board — which looks exactly like "nothing was wrong".

    So each anchor brings its own ancestor paths, on both planes.  Anchors outside the
    subject's ancestor cone cannot affect this merge at all, so they are dropped rather
    than shipped to the JVM (the merge stays MIREOT-small; the anchor set no longer
    grows the merge with every promotion the run makes).  The subject itself is kept in
    the cone: an anchor *on the subject* is exactly what the same-subject conflict check
    leans on.
    """
    live = _live_anchors(record, ctx, anchors)
    ncit_seeds = {record.subject_id, *(code for code, _ in live)}
    upstream_seeds = {record.object_id, *(curie for _, curie in live)}

    return build_validation_ontology(
        record,
        ncit_edges=set().union(*(_scoped_edges(s, ctx.ncit_edges) for s in ncit_seeds)),
        upstream_edges=set().union(
            *(_scoped_edges(s, ctx.upstream_edges) for s in upstream_seeds)
        ),
        anchors=live,
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


def _corroborate(
    record: SSSOMRecord,
    ctx: PromotionContext,
    anchors: Sequence[tuple[str, str]],
    *,
    reasoner: Reasoner,
) -> str:
    """Classify the merge WITHOUT the candidate bridge, and read the verdict."""
    inferred = reasoner(_merge_for(record, ctx, anchors, include_bridge=False))
    if inferred is None:
        # The bridge-free merge carries no candidate axiom.  If the reasoner refutes it,
        # the *anchor set* contradicts the taxonomy — a data-integrity alarm.  Reporting
        # it as "this candidate is not corroborated" would bury it.
        raise PromotionEnvironmentError(
            "the reasoner refuted the bridge-free merge for "
            f"({record.subject_id}, {record.object_id}): it contains no candidate "
            "bridge, so either the validated anchors are inconsistent with the stated "
            "taxonomy or bridge.py emitted a non-EL merge. Refusing to read that as a "
            "verdict about this candidate."
        )
    return corroboration(
        record,
        inferred,
        anchors=anchors,
        ncit_edges=ctx.ncit_edges,
        upstream_partof_edges=ctx.upstream_partof_edges,
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

    verdict = _corroborate(record, ctx, anchors, reasoner=reasoner)
    evidence = _evidence_for(
        record, ctx, structurally_corroborated=verdict == CORROBORATED
    )
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
        raise PromotionEnvironmentError(
            "no proposed candidates: ingest has not run, or ran against an empty "
            "store. A run with nothing to validate cannot mint a single replacement — "
            "so sweeping would demote validated bridges and collapse coverage on a run "
            "that established nothing. Run `data-build xref` first."
        )
    missing = [
        name
        for name, loaded in (
            (
                f"stated NCIt subClassOf edges (graph <{STATED_GRAPH_IRI}>)",
                ctx.ncit_edges,
            ),
            ("upstream subClassOf edges", ctx.upstream_edges),
            ("upstream labels", ctx.object_labels),
            # label agreement needs BOTH sides. NCIt labels load from the *default*
            # graph while the edges load from the stated named graph — two independent
            # load paths, so this can fail on its own.
            ("NCIt labels", ctx.subject_labels),
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


def _claims_a_taken_endpoint(
    pair: tuple[str, str],
    ctx: PromotionContext,
    minted: Sequence[tuple[str, str]],
    claimed_subjects: set[str],
    claimed_objects: set[str],
) -> bool:
    """Would promoting *pair* give an endpoint two identity-grade bridges?"""
    subject, obj = pair
    if pair in ctx.anchors or pair in minted:
        return False  # the same bridge, not a competing one
    if subject not in claimed_subjects and obj not in claimed_objects:
        return False
    logger.warning(
        "refusing %s -> %s: an identity-grade bridge already claims one of its "
        "endpoints. Promoting both would assert an equivalence between the two "
        "upstream classes that nobody curated and no reasoner saw. Needs SME "
        "adjudication.",
        subject,
        obj,
    )
    return True


def _curation_alone(outcome: PromotionOutcome) -> bool:
    """Was the SME signal **load-bearing** — would this NOT have promoted without it?

    Not "was curation the only signal".  An earlier cut asked exactly that
    (``kinds == {SME_CURATION}``) and undercounted badly: a curated pair whose labels
    also agree has kinds ``{SME, LABEL}``, but one non-SME kind cannot clear
    ``is_independent`` on its own — so curation still carried it, while the run booked
    it as a promotion "the machinery earned".  Curated pairs almost always have agreeing
    labels, so that was the routine case, not a corner.
    """
    kinds = {e.kind for e in outcome.evidence}
    if SME_CURATION not in kinds:
        return False
    without_sme = [e for e in outcome.evidence if e.kind != SME_CURATION]
    return not is_independent(without_sme)


def _one_per_pair(records: Sequence[SSSOMRecord]) -> list[SSSOMRecord]:
    """One candidate per pair.

    The same ``(subject, object)`` can legitimately come back twice — re-ingested at a
    new version, or under a different justification.  It is ONE candidate: validating it
    twice costs two JVM launches and double-counts ``promoted``, the number that lands
    in ``xref_run.metrics`` and moves the published coverage figure.

    The tie-break is load-bearing, not tidiness.  Which duplicate survives decides which
    signals are suppressed as generating (:mod:`evidence`), and Postgres leaves the
    order of equal rows unspecified — so the **most corroborated** row must win, or a
    pair the two sources agree on silently degrades to a single-signal candidate that
    can never promote.  A composite row therefore outranks a single-source row (an
    earlier ingest leaves one behind: the rows differ in ``mapping_justification``, so
    the ``DISTINCT`` in ``proposed_candidates`` cannot collapse them), and an xref row
    outranks a lexical one.
    """
    ranked = sorted(records, key=_justification_rank)
    return list({(r.subject_id, r.object_id): r for r in reversed(ranked)}.values())


def _justification_rank(record: SSSOMRecord) -> int:
    """Lower is better: more generating passes behind the row, more signals to use."""
    return _JUSTIFICATION_RANK.get(
        record.mapping_justification, len(_JUSTIFICATION_RANK)
    )


def _initial_claims(
    ctx: PromotionContext, stale_anchors: frozenset[tuple[str, str]]
) -> tuple[set[str], set[str]]:
    """Which endpoints an identity-grade bridge already claims.

    Stale bridges are excluded: one is about to be quarantined by this very run, and
    letting it claim its endpoints would block its own replacement (see
    ``XrefStore.stale_anchors``).
    """
    current = [a for a in ctx.anchors if a not in stale_anchors]
    return {code for code, _ in current}, {curie for _, curie in current}


def _validate_or_report_error(
    record: SSSOMRecord,
    ctx: PromotionContext,
    reasoner: Reasoner,
    minted: Sequence[tuple[str, str]],
) -> PromotionOutcome | None:
    """``None`` iff the reasoner could not run — which is NOT a verdict, and is counted
    separately so a broken reasoner can never read as "no candidate qualified"."""
    try:
        return validate_candidate(record, ctx, reasoner=reasoner, extra_anchors=minted)
    except ReasonerUnavailableError as exc:
        logger.error(
            "reasoner unavailable for %s -> %s (NOT a verdict): %s",
            record.subject_id,
            record.object_id,
            exc,
        )
        return None
    except (ValueError, KeyError) as exc:
        # `gather_evidence` fails closed on an unrecognised mapping_justification —
        # which
        # is right, but it must fail closed for THAT CANDIDATE, not abort the run and
        # discard every promotion computed before it.
        logger.error(
            "cannot score %s -> %s, skipping it: %s",
            record.subject_id,
            record.object_id,
            exc,
        )
        return None


def promote_candidates(
    records: Sequence[SSSOMRecord],
    ctx: PromotionContext,
    *,
    reasoner: Reasoner,
    stale_anchors: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[list[SSSOMRecord], PromotionReport]:
    """Run :func:`validate_candidate` over *records*; return promotions + a report.

    A reasoner that cannot run is counted in ``reasoner_errors`` and does **not**
    silently become "this candidate did not qualify"; the run is then reported failed.

    **Identity is decided on evidence, never on sort order.** Ingest legitimately yields
    several upstream candidates for one NCIt code, and promoting two asserts ``C ≡ U1``
    and ``C ≡ U2`` — hence ``U1 ≡ U2``, an equivalence nobody curated and no reasoner
    saw.  A refutation-only oracle cannot adjudicate that (ELK objects only if U1 and U2
    are *provably disjoint*, which sibling Uberon terms never are), so it is enforced
    structurally here.  Crucially it is a **two-pass** decision: an earlier cut let the
    first candidate to qualify claim the subject, so the winner was whichever CURIE
    sorted lower — an arbitrary identity, published as ``exactMatch/validated``, with a
    ``conflicting_identity`` count that *looked* like the ambiguity had been detected
    and handled.  Now every candidate is validated first, and if two qualify for the
    same endpoint **neither** is promoted: that is what "needs SME adjudication" means.
    """
    _refuse_degenerate_context(records, ctx)

    records = _one_per_pair(records)
    counts: Counter[str] = Counter()

    # Endpoints already claimed by an existing, still-current bridge. (Stale ones are
    # excluded: one is about to be quarantined, and it must not block its replacement.)
    claimed_subjects, claimed_objects = _initial_claims(ctx, stale_anchors)

    qualified = _validate_all(
        records, ctx, reasoner, counts, claimed_subjects, claimed_objects
    )
    promoted, curation_only, corroborated = _promote_uncontested(
        qualified, counts, stale_anchors
    )

    return promoted, PromotionReport(
        considered=len(records),
        promoted=counts[REASON_PROMOTED],
        insufficient_evidence=counts[REASON_INSUFFICIENT_EVIDENCE],
        refuted=counts[REASON_REFUTED],
        reasoner_errors=counts[REASON_REASONER_ERROR],
        conflicting_identity=counts[REASON_CONFLICTING_IDENTITY],
        promoted_on_curation_alone=curation_only,
        promoted_with_structural_corroboration=corroborated,
    )


def _validate_all(
    records: Sequence[SSSOMRecord],
    ctx: PromotionContext,
    reasoner: Reasoner,
    counts: Counter[str],
    claimed_subjects: set[str],
    claimed_objects: set[str],
) -> list[PromotionOutcome]:
    """Pass 1 — validate every candidate, claiming nothing."""
    qualified: list[PromotionOutcome] = []
    for record in records:
        pair = (record.subject_id, record.object_id)
        if _claims_a_taken_endpoint(pair, ctx, (), claimed_subjects, claimed_objects):
            counts[REASON_CONFLICTING_IDENTITY] += 1
            continue

        outcome = _validate_or_report_error(record, ctx, reasoner, ())
        if outcome is None:
            counts[REASON_REASONER_ERROR] += 1
        elif outcome.promoted is None:
            counts[outcome.reason] += 1
        else:
            qualified.append(outcome)
    return qualified


def _promote_uncontested(
    qualified: Sequence[PromotionOutcome],
    counts: Counter[str],
    stale_anchors: frozenset[tuple[str, str]],
) -> tuple[list[SSSOMRecord], int, int]:
    """Pass 2 — an endpoint two qualifying candidates claim promotes NEITHER.

    With two exceptions a naive "contested ⇒ nobody wins" rule gets wrong:

    * **An SME-curated bridge beats a machine rival.** D28 lets curation stand alone, so
      it must also *settle* a contest.  Refusing both — and logging "needs SME
      adjudication" about a pair an SME has already adjudicated — is absurd, and it
      silently loses real coverage.
    * **A bridge this run is about to quarantine cannot contest its replacement.** The
      candidate row of a stale bridge survives (promotion is additive) and still
      qualifies, so without this it would contest the very replacement
      ``XrefStore.stale_anchors`` exists to let through, and then be quarantined —
      leaving the concept with *no* bridge at all, which is the exact outcome that
      method was written to prevent.

    ``_one_per_pair`` deduplicates the old candidate row and the replacement (same
    pair), so the stale bridge's candidate row never reaches ``qualified``.  The
    *replacement*
    must still be visible in contest detection: a separate same-endpoint candidate must
    not slip past because the replacement was filtered out.  Contest endpoints are
    therefore computed from ALL qualified outcomes, not from a stale-filtered subset.
    """
    live = [o for o in qualified if _pair(o) not in stale_anchors]
    contested_subjects = _contested(o.record.subject_id for o in qualified)
    contested_objects = _contested(o.record.object_id for o in qualified)
    settled = _sme_winners(live, contested_subjects, contested_objects)

    return _settle_contests(
        qualified, counts, settled, contested_subjects, contested_objects
    )


def _settle_contests(
    qualified: Sequence[PromotionOutcome],
    counts: Counter[str],
    settled: set[tuple[str, str]],
    subjects: set[str],
    objects: set[str],
) -> tuple[list[SSSOMRecord], int, int]:
    promoted: list[SSSOMRecord] = []
    curation_only = corroborated = 0
    for outcome in qualified:
        if _loses_the_contest(outcome, settled, subjects, objects):
            counts[REASON_CONFLICTING_IDENTITY] += 1
        elif outcome.promoted is not None:
            counts[REASON_PROMOTED] += 1
            promoted.append(outcome.promoted)
            # Structural corroboration takes priority over curation alone:
            # if the reasoner contributed, the promotion is booked under the
            # machinery bucket, not curation (promoted_on_curation_alone means
            # "curation ALONE — no independent corroborating signal").
            if _structurally_corroborated(outcome):
                corroborated += 1
            elif _curation_alone(outcome):
                curation_only += 1
    return promoted, curation_only, corroborated


def _loses_the_contest(
    outcome: PromotionOutcome,
    settled: set[tuple[str, str]],
    subjects: set[str],
    objects: set[str],
) -> bool:
    if _pair(outcome) in settled:
        return False  # an SME already adjudicated this endpoint
    return _is_contested(outcome, subjects, objects)


def _structurally_corroborated(outcome: PromotionOutcome) -> bool:
    return STRUCTURAL_CORROBORATION in {e.kind for e in outcome.evidence}


def _pair(outcome: PromotionOutcome) -> tuple[str, str]:
    return (outcome.record.subject_id, outcome.record.object_id)


def _sme_winners(
    qualified: Sequence[PromotionOutcome], subjects: set[str], objects: set[str]
) -> set[tuple[str, str]]:
    """Contested endpoints where exactly ONE contender is curated — curation settles."""
    winners: set[tuple[str, str]] = set()
    for endpoint in subjects:
        winners |= _sole_curated(qualified, endpoint, lambda o: o.record.subject_id)
    for endpoint in objects:
        winners |= _sole_curated(qualified, endpoint, lambda o: o.record.object_id)
    return winners


def _sole_curated(
    qualified: Sequence[PromotionOutcome],
    endpoint: str,
    key: Callable[[PromotionOutcome], str],
) -> set[tuple[str, str]]:
    curated = [
        o
        for o in qualified
        if key(o) == endpoint and SME_CURATION in {e.kind for e in o.evidence}
    ]
    return {_pair(curated[0])} if len(curated) == 1 else set()


def _is_contested(
    outcome: PromotionOutcome, subjects: set[str], objects: set[str]
) -> bool:
    record = outcome.record
    if record.subject_id not in subjects and record.object_id not in objects:
        return False
    logger.warning(
        "refusing %s -> %s: another candidate qualified for the same endpoint."
        " Promoting both would assert an equivalence between the two upstream classes"
        " that nobody curated and no reasoner saw. Neither is promoted — this needs SME"
        " adjudication, not a coin toss on CURIE sort order.",
        record.subject_id,
        record.object_id,
    )
    return True


def _contested(endpoints: Iterable[str]) -> set[str]:
    """Endpoints claimed by more than one qualifying candidate."""
    seen = Counter(endpoints)
    return {endpoint for endpoint, n in seen.items() if n > 1}


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
        FILTER(isIRI(?child) && isIRI(?parent))
        FILTER(STRSTARTS(STR(?child), "{NCIT_NS}"))
        FILTER(STRSTARTS(STR(?parent), "{NCIT_NS}"))
    }}
}}
"""


def build_upstream_edges_query(curies: Sequence[str]) -> str:
    """Stated upstream named-class ``subClassOf`` edges on the ancestor paths.

    **Both** ends are restricted to the prefixes we can expand back to an IRI
    (``ttl_writer.SUPPORTED_PREFIXES``).  Filtering only the parent is not enough: the
    ``subClassOf*`` walk passes *through* upper-ontology classes, so a real Uberon store
    yields children like ``GO:0110165`` (which has UBERON/CL classes beneath it) and
    ``COB:0000022``.  ``object_iri`` cannot expand those, and the ``KeyError`` would
    abort the entire run — discarding every promotion computed so far — on the first
    real dataset.  Truncating the cone at an unsupported class loses nothing: the merge
    cannot express that class anyway.
    """
    iris = " ".join(f"<{object_iri(c)}>" for c in sorted(curies))

    def _supported(var: str) -> str:
        return " || ".join(
            f'STRSTARTS(STR(?{var}), "{_OBO_BASE}{prefix}_")'
            for prefix in _OBO_PREFIXES
        )

    return f"""\
PREFIX rdfs: <{_RDFS_NS}>
SELECT DISTINCT ?child ?parent WHERE {{
    VALUES ?seed {{ {iris} }}
    ?seed rdfs:subClassOf* ?child .
    ?child rdfs:subClassOf ?parent .
    FILTER(isIRI(?child) && isIRI(?parent))
    FILTER({_supported("child")})
    FILTER({_supported("parent")})
}}
"""


def build_upstream_partof_query(curies: Sequence[str]) -> str:
    """Stated upstream ``part_of`` (BFO:0000050) edges on the ancestor paths.

    Emitted as ``(child_curie, parent_curie)`` for every ``?child`` that is a
    ``subClassOf`` ancestor (or self) of a seed and carries an existential
    ``part_of`` restriction ``?child ⊑ ∃ part_of . ?parent`` over a named class.

    This is the edge the corroboration walk needs but ``subClassOf`` cannot supply.
    Verified against the live store: ``lung ⊑* respiration organ`` (subClassOf) and
    ``respiration organ part_of respiratory system`` (this query) — the two together
    place lung under the anchored system; neither does alone, and lung's *own*
    ``part_of`` chain (``pair of lungs -> lower respiratory tract``) never reaches the
    system at all.  The ``subClassOf*`` prefix on ``?seed`` is what lets a part_of
    restriction stated on an ancestor (``respiration organ``) rather than on the object
    itself still be collected.

    Both ends are restricted to expandable prefixes for the same reason as
    :func:`build_upstream_edges_query`: ``object_iri`` raises ``KeyError`` on an
    upper-ontology CURIE, and that is not caught per-candidate.
    """
    iris = " ".join(f"<{object_iri(c)}>" for c in sorted(curies))

    def _supported(var: str) -> str:
        return " || ".join(
            f'STRSTARTS(STR(?{var}), "{_OBO_BASE}{prefix}_")'
            for prefix in _OBO_PREFIXES
        )

    return f"""\
PREFIX rdfs: <{_RDFS_NS}>
PREFIX owl: <{_OWL_NS}>
SELECT DISTINCT ?child ?parent WHERE {{
    VALUES ?seed {{ {iris} }}
    ?seed rdfs:subClassOf* ?child .
    ?child rdfs:subClassOf ?restriction .
    ?restriction owl:onProperty <{_BFO_PART_OF}> ; owl:someValuesFrom ?parent .
    FILTER(isIRI(?child) && isIRI(?parent))
    FILTER({_supported("child")})
    FILTER({_supported("parent")})
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
        if curie in objects and xref and str(xref).startswith(_OBO_NCIT_PREFIX):
            xrefs.setdefault(str(curie), set()).add(
                str(xref).removeprefix(_OBO_NCIT_PREFIX)
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


async def _upstream_partof_edges(
    client: OxigraphHttpClient, objects: Sequence[str]
) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for batch in _batches(objects):
        for row in await client.select(build_upstream_partof_query(list(batch))):
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


def _is_expandable(curie: str) -> bool:
    """Can ``ttl_writer.object_iri`` turn this CURIE back into an IRI?

    Anything else must never reach the merge builder: ``object_iri`` raises ``KeyError``
    there, the exception is not per-candidate, and the whole run dies — discarding every
    promotion computed so far. Rows like this are real: ingest's lexical pass indexes
    *every* labelled class in the upstream store, including ``GO:``/``COB:`` imports,
    and a future upstream (Mondo) writes ``MONDO:`` anchors into the same table.
    """
    return curie.split(":", 1)[0] in SUPPORTED_PREFIXES


def _expandable_anchors(
    validated: Sequence[tuple[str, str]], curated: frozenset[tuple[str, str]]
) -> tuple[tuple[str, str], ...]:
    """Anchors this build can express, de-duplicated and order-stable."""
    merged = dict.fromkeys([*validated, *sorted(curated)])
    return tuple(a for a in merged if _is_expandable(a[1]))


def _expandable_only(records: Sequence[SSSOMRecord]) -> list[SSSOMRecord]:
    keep, dropped = [], []
    for record in records:
        (keep if _is_expandable(record.object_id) else dropped).append(record)
    if dropped:
        logger.warning(
            "skipping %d candidate(s) whose upstream prefix this build cannot expand "
            "(supported: %s); e.g. %s. They cannot enter a validation merge.",
            len(dropped),
            ", ".join(SUPPORTED_PREFIXES),
            dropped[0].object_id,
        )
    return keep


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
    anchors = _expandable_anchors(validated_anchors, curated_pairs)

    # The taxonomy fetch must be seeded from the ANCHOR endpoints as well as the
    # candidates'.  `_scoped_edges` can only select from what was fetched, so seeding
    # from candidates alone leaves every anchor image a parentless floating class in the
    # merge: ELK cannot walk from it to a branch carrying a disjointness axiom, and the
    # refutation gate under-fires — which looks exactly like "nothing was wrong".
    anchor_codes = [code for code, _ in anchors]
    anchor_curies = [curie for _, curie in anchors]

    ctx = PromotionContext(
        subject_labels=await _subject_labels(ncit_client, subjects),
        object_labels=await _object_labels(uberon_client, set(objects)),
        object_xrefs=await _object_xrefs(uberon_client, set(objects)),
        ncit_edges=await _ncit_edges(ncit_client, [*subjects, *anchor_codes]),
        upstream_edges=await _upstream_edges(uberon_client, [*objects, *anchor_curies]),
        upstream_partof_edges=await _upstream_partof_edges(
            uberon_client, [*objects, *anchor_curies]
        ),
        anchors=anchors,
        curated_pairs=curated_pairs,
        disjoints=await _disjoints(ncit_client, uberon_client),
    )
    logger.info(
        "promotion context: %d candidates, %d NCIt edges, %d upstream edges, "
        "%d part_of edges, %d anchors, %d disjointness axioms",
        len(records),
        len(ctx.ncit_edges),
        len(ctx.upstream_edges),
        len(ctx.upstream_partof_edges),
        len(ctx.anchors),
        len(ctx.disjoints),
    )
    _warn_on_missing_signals(records, ctx)
    return ctx


def _warn_on_missing_signals(
    records: Sequence[SSSOMRecord], ctx: PromotionContext
) -> None:
    """Surface the two states that make a ``promoted: 0`` run a false conservative.

    Neither is degenerate enough to refuse the run (unlike
    :func:`_refuse_degenerate_context`), but each disables a whole promotion pathway
    silently, so log a warning that names it.
    """
    if records and not ctx.disjoints:
        logger.warning(
            "no owl:disjointWith axioms loaded — the reasoner has nothing to refute "
            "with, so the satisfiability gate cannot fire and promotion rests entirely "
            "on the evidence policy"
        )
    if records and not ctx.upstream_partof_edges:
        logger.warning(
            "no upstream part_of (BFO:0000050) edges loaded — structural corroboration "
            "for anatomy relies on them (an organ is part_of, not subClassOf, its "
            "system), so with none loaded that signal cannot fire and non-curated "
            "candidates rest on label + xref agreement alone"
        )


# ── persistence (D29 lifecycle) ────────────────────────────────────────


async def persist_promotions(
    store: XrefStore,
    promoted: Sequence[SSSOMRecord],
    report: PromotionReport,
    *,
    ncit_version: str,
    source_version: str,
    source: str,
    run_id: str | None = None,
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


async def _load_candidates(store: XrefStore) -> tuple[list[SSSOMRecord], int]:
    """Proposed candidates this build can actually express, plus how many it dropped.

    Filtered ONCE, here, so the context and the validator see the SAME list. An earlier
    cut filtered inside `load_promotion_context`, which rebinds a *local* — the caller's
    list was untouched, so unexpandable candidates still reached the merge builder and
    KeyError-ed the run to death, while the "skipping N candidates" log line claimed
    otherwise. A fix that did not fix, announcing that it had.
    """
    raw = await store.proposed_candidates()
    candidates = _expandable_only(raw)
    if raw and not candidates:
        raise PromotionEnvironmentError(
            f"all {len(raw)} candidates carry an upstream prefix this build cannot "
            f"expand (supported: {', '.join(SUPPORTED_PREFIXES)}). The endpoints are "
            "loaded and fine — do not go looking at them."
        )
    return candidates, len(raw) - len(candidates)


async def run_promotion(
    store: XrefStore,
    ncit_client: OxigraphHttpClient,
    uberon_client: OxigraphHttpClient,
    *,
    ncit_version: str,
    source_version: str,
    source: str,
    curated_pairs: frozenset[tuple[str, str]] = frozenset(),
    reasoner: Reasoner = elk_reasoner,
) -> dict[str, Any]:
    """Promote every proposed candidate, then quarantine bridges a release left stale.

    *ncit_version* / *source_version* are the endpoint versions this run validates
    against; they stamp the promoted rows and drive the D29 staleness sweep.

    The sweep runs whenever the run is sound — a release makes old bridges stale whether
    or not this run promoted anything — but is **skipped when the reasoner failed**: a
    run that established nothing must not also demote bridges a working run validated.
    In that case the stale bridges keep being served *and counted*, so the count is
    surfaced as ``stale_pending`` rather than left in a log line: the published coverage
    number is unreliable until a sound run sweeps.
    """
    candidates, skipped = await _load_candidates(store)
    # Filter ONCE, here, and hand the SAME list to the context and the validator. An
    # earlier cut filtered inside `load_promotion_context`, which rebinds a *local* —
    # the caller's list was untouched, so unexpandable candidates still reached the
    # merge builder and KeyError-ed the run to death, while the "skipping N candidates"
    # log line claimed otherwise. A fix that did not fix, announcing that it had.
    curated_pairs = frozenset(p for p in curated_pairs if _is_expandable(p[1]))
    anchors = await store.validated_anchors(source=source)
    ctx = await load_promotion_context(
        ncit_client,
        uberon_client,
        candidates,
        curated_pairs=curated_pairs,
        validated_anchors=anchors,
    )
    stale = await store.stale_anchors(
        ncit_version=ncit_version, source_version=source_version, source=source
    )
    promoted, report = promote_candidates(
        candidates, ctx, reasoner=reasoner, stale_anchors=frozenset(stale)
    )
    # The drop must be visible: a large silent drop is otherwise indistinguishable from
    # a small candidate set, and xref_run.metrics is the auditable artifact.
    report = replace(report, skipped_unexpandable=skipped)
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
    quarantined, stale_pending = await _sweep(
        store, report, ncit_version, source_version, source
    )

    outcome_dict = {
        **report.as_dict(),
        "run_id": run_id,
        "quarantined": quarantined,
        "stale_pending": stale_pending,
        "status": "failed" if report.failed else "completed",
    }
    await store.update_run_metrics(
        run_id,
        {k: v for k, v in outcome_dict.items() if k != "run_id"},
        status="failed" if report.failed else "completed",
    )
    return outcome_dict


async def _sweep(
    store: XrefStore,
    report: PromotionReport,
    ncit_version: str,
    source_version: str,
    source: str,
) -> tuple[int, int]:
    """Run the D29 staleness sweep — unless the run established nothing."""
    quarantined = stale_pending = 0
    if report.failed:
        stale_pending = await store.count_stale(
            ncit_version=ncit_version,
            source_version=source_version,
            source=source,
        )
        logger.error(
            "skipping the D29 staleness sweep: the reasoner failed for %d candidate(s),"
            " so this run established nothing and must not demote anything. %d"
            " bridge(s) are stale and still being served and counted — the published"
            " coverage number is unreliable until a sound run sweeps.",
            report.reasoner_errors,
            stale_pending,
        )
    else:
        quarantined = await store.quarantine_stale(
            ncit_version=ncit_version,
            source_version=source_version,
            source=source,
        )

    return quarantined, stale_pending
