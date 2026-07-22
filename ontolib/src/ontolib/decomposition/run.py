"""Decomposition engine orchestration and CLI (design section 9).

Pipeline: enumerate in-scope concepts, detect, extract, select,
NLP fallback, mint, write TTL, commit provenance.

Usage:
    pdm run decompose --branch neoplasm [--out path.ttl] [--load]

Scope of this orchestrator (documented boundaries, not oversights):
- Extraction uses the genus-chain walker (``stated_queries.walk_genus_chain``) to
  traverse ``owl:equivalentClass``/``owl:intersectionOf`` members, collecting role
  restrictions from defined classes. A ``_CORE_NEOPLASM_ROLES`` boundary filter
  prevents over-collection of generic neoplasm biology from deep genus ancestors.
- Morphology-from-parent (design §6, the ``op:Morphology`` axis) is wired:
  ``stated_queries.resolve_morphology_filler`` walks the genus chain for the first
  non-staging genus, ``filler_selection._append_morphology`` adds the ``op:Morphology``
  constituent, and ``detector.detect`` counts it as a decomposable axis.
- ``--load`` (pushing the written TTL into the store) is a CLI-layer concern, not this
  function's — ``run_pipeline`` only ever writes the file at ``config.out``. The CLI
  script performs the store load afterwards using the concrete client's ``.load()``.
- ``--resume`` skips concept codes that already have persisted constituents for the
  resumed run id (``ProvenanceStore.processed_codes``). A concept that decomposed to
  *zero* constituents leaves no such row, so it is reprocessed on resume — safe
  (idempotent upserts), not exhaustive. The resumed run's ``ncit_version`` is checked
  against the live store's current version (``_prepare_run``) and refused on a mismatch
  (design §9/§13) — roles are version-pinned, so mixing two builds in one manifest would
  silently corrupt it.
- ``total_in_scope`` reflects the *full* branch enumeration on every invocation
  (including a resume); ``decomposed``/``residual``/``residual_precoordinated_count``/
  ``minted_count`` cover only the pending subset actually processed *this* invocation.
  So on a resumed run,
  ``coverage``/``pct_decomposed`` understates true cumulative progress — it is not
  self-consistently scoped to "this invocation" the way the other counters are.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from ontolib.core.logging_config import get_logger
from ontolib.decomposition import (
    axes,
    constituent_index,
    detector,
    extract,
    nlp_fallback,
    stated_queries,
)
from ontolib.decomposition import filler_selection as fs
from ontolib.decomposition.fidelity import roundtrip_fidelity
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Decomposition

logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ontolib.decomposition.constituent_index import LabelLookup
    from ontolib.decomposition.minting import MintedConcept
    from ontolib.decomposition.models import RoleRestriction
    from ontolib.decomposition.provenance import ProvenanceStore

# Batch code -> preferred label (design's NLP fallback needs the label; the detector's
# advisory label_multi_aspect signal needs it too). Injected so this module has no
# hard dependency on a concrete graph-store class — see the module docstring.
GetLabels = Callable[[list[str]], Awaitable[dict[str, str]]]

_DEFAULT_PAGE_SIZE = 500


class SparqlClient(Protocol):
    """The minimal client surface this orchestrator needs (structural typing).

    ``OxigraphHttpClient`` satisfies this; tests supply a lightweight fake.
    """

    async def select(self, query: str) -> list[dict[str, str | None]]: ...

    async def version(self) -> str | None: ...


async def _never_resolves(_: str) -> str | None:
    """Default ``label_lookup`` — always mint (never guess a false match)."""
    return None


@dataclass
class RunConfig:
    """Configuration for a decomposition run.

    ``load_to_store`` is accepted here for the CLI to carry alongside the rest of the
    config, but ``run_pipeline`` never reads it: loading into the store is a CLI-layer
    concern performed by the caller after ``run_pipeline`` returns (see the module
    docstring).  ``emit_equivalence`` is wired through to ``write_ttl``.
    """

    branch: str
    out: Path | None = None
    load_to_store: bool = False
    emit_equivalence: bool = False
    resume_from: str | None = None
    walker_max_depth: int = 5


@dataclass
class RunMetrics:
    """Coverage metrics for a decomposition run (design §10).

    **Two distinct residual counters — do not conflate them:**

    * ``residual`` — a concept detected as pre-coordinated that produced *zero*
      constituents. A degenerate safety net (currently unreachable: every defining role
      or NLP aspect yields >=1 constituent — see ``_persist_candidate``). NOT design
      §10's residual metric.
    * ``residual_precoordinated_count`` / :attr:`residual_precoordination` — **D37's
      metric**: decomposed concepts at least one of whose *emitted constituents is
      itself* classified as pre-coordinated by the same detector. This is "is what we
      produced actually atomic?" (irreducibility), the complement of
      ``roundtrip_fidelity``'s "did we capture everything?" (completeness).

      It is **detector-relative** — defined purely in terms of what ``detector.detect``
      flags — so an under-detecting detector reads it artificially low, and a detector
      improvement moves it with no ontology change (D37). Track it against the SME
      golden set (#57) as well as the corpus, so divergence surfaces detector drift.

      **What it can and cannot fire on (a structural bound, not a bug).** ``detect``
      gates on the in-scope semantic types (neoplasm/disease/dysfunction), so the metric
      can only flag a constituent filler that is *itself* an in-scope compound (a
      differentia neoplasm/disease with >=2 axes). Anatomic-site and morphology fillers
      are out of scope and read atomic by construction, and minted/NLP fillers are
      atomic by definition. So a **real** run reading 0 may mean the detector never met
      an in-scope compound filler, not that the corpus is clean — which is exactly why
      D37 says a 0 on the first real run (#127) is a signal to suspect the detector, and
      why the number is proved reachable there, on real data, not only in unit tests.

    ``roundtrip_fidelity`` is computed only when ``emit_equivalence=True`` — the
    fraction of stated defining restrictions covered by the emitted
    owl:equivalentClass intersection axiom (D21.3).
    """

    total_in_scope: int = 0
    decomposed: int = 0
    residual: int = 0
    residual_precoordinated_count: int = 0
    minted_count: int = 0
    pct_decomposed: float = 0.0
    roundtrip_fidelity: float = 0.0

    @property
    def coverage(self) -> float:
        """Fraction of in-scope concepts successfully decomposed."""
        if self.total_in_scope == 0:
            return 0.0
        return self.decomposed / self.total_in_scope

    @property
    def residual_precoordination(self) -> float:
        """D37: fraction of decomposed concepts that are residually pre-coordinated.

        Detector-relative (see the class docstring). ``0.0`` when nothing decomposed —
        honestly zero, not undefined.
        """
        if self.decomposed == 0:
            return 0.0
        return self.residual_precoordinated_count / self.decomposed


@dataclass
class _CandidateResult:
    decomposition: Decomposition | None
    minted: list[MintedConcept] = field(default_factory=list)
    stated_roles: list[RoleRestriction] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.decomposition is None and self.minted:
            raise ValueError(
                "_CandidateResult: minted concepts without a decomposition is not "
                "a valid state — minting only happens while building constituents "
                "for an actual candidate"
            )


def _new_run_id(branch: str) -> str:
    return f"{branch}-{datetime.now(UTC).isoformat(timespec='seconds')}"


def _compute_fidelity(
    decomposition: Decomposition,
    stated_roles: list[RoleRestriction],
) -> float | None:
    """Compute roundtrip fidelity for one decomposition.

    Returns None if there are no stated role restrictions (no basis for fidelity).
    Otherwise returns the fraction of stated role restrictions covered by the
    role-sourced constituents in the decomposition.
    """
    if not stated_roles:
        return None

    stated_set: set[tuple[str, str]] = set()
    for r in stated_roles:
        if not axes.is_excluded_role(r.role_label):
            axis = fs.route_axis(r)
            stated_set.add((axis, r.filler_code))

    if not stated_set:
        return None

    emitted_set: set[tuple[str, str]] = set()
    for c in decomposition.constituents:
        if c.axis_source == "role":
            emitted_set.add((c.axis, c.filler_code))

    return roundtrip_fidelity(emitted_set, stated_set)


def _maybe_collect_fidelity(
    result: _CandidateResult,
    emit_equivalence: bool,
    fidelity_scores: list[float],
) -> None:
    """Collect fidelity score if emit_equivalence and valid decomposition."""
    if not emit_equivalence or result.decomposition is None:
        return
    fidelity = _compute_fidelity(result.decomposition, result.stated_roles)
    if fidelity is not None:
        fidelity_scores.append(fidelity)


def _finalize_fidelity(
    metrics: RunMetrics,
    fidelity_scores: list[float],
    emit_equivalence: bool,
) -> None:
    """Set the aggregate roundtrip_fidelity metric."""
    if emit_equivalence and fidelity_scores:
        metrics.roundtrip_fidelity = sum(fidelity_scores) / len(fidelity_scores)


async def enumerate_in_scope_codes(
    client: SparqlClient,
    semantic_types: Sequence[str] | None = None,
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[str]:
    """Page through every stated-graph concept carrying an in-scope semantic type."""
    scope = (
        tuple(semantic_types)
        if semantic_types is not None
        else tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES))
    )
    codes: list[str] = []
    offset = 0
    while True:
        rows = await client.select(
            stated_queries.build_in_scope_concepts_query(
                scope, limit=page_size, offset=offset
            )
        )
        page = extract.concepts_from_rows(rows)
        codes.extend(page)
        if len(page) < page_size:
            return codes
        offset += page_size


async def _detect_concept(
    code: str,
    client: SparqlClient,
    *,
    label: str | None,
    walker_max_depth: int,
) -> tuple[detector.DetectionResult, list[RoleRestriction], str | None]:
    """Run the detector on *code*: semantic types, genus-chain roles, and morphology.

    Returns the ``DetectionResult`` plus the ``roles`` and ``morphology_filler`` the
    caller reuses, so this same machinery classifies both a decomposition candidate
    (in :func:`_decompose_one`) and, unchanged, each emitted constituent's filler when
    computing ``residual_precoordination`` (D37): the metric is only meaningful if a
    constituent is judged by the *same* detector as the concept it came from.
    """
    semantic_types = extract.semantic_types_from_rows(
        await client.select(stated_queries.build_semantic_type_query(code))
    )
    roles = await stated_queries.walk_genus_chain(
        client.select, code, max_depth=walker_max_depth
    )
    morphology_filler = await stated_queries.resolve_morphology_filler(
        client.select, code, max_depth=walker_max_depth
    )
    result = detector.detect(
        code,
        semantic_types,
        roles,
        has_parent_morphology=morphology_filler is not None,
        label=label,
    )
    return result, roles, morphology_filler


async def _decompose_one(
    code: str,
    client: SparqlClient,
    *,
    label: str | None,
    label_lookup: LabelLookup,
    walker_max_depth: int = 5,
) -> _CandidateResult:
    """Detect, extract, and resolve one concept. ``decomposition`` is ``None`` when the
    concept is not a decomposition candidate at all (atomic — never counted as residual,
    only a candidate that yields zero constituents is residual)."""
    # Phase 1: detect (semantic types + genus-chain roles + morphology-from-parent).
    # For primitive concepts (no owl:equivalentClass) the walker returns zero roles,
    # which is correct — nothing to decompose.
    result, roles, morphology_filler = await _detect_concept(
        code, client, label=label, walker_max_depth=walker_max_depth
    )

    # Phase 1a: resolve immediate genus for the equivalence axiom (the first
    # ``owl:intersectionOf`` member of the starting concept).  ``None`` for
    # primitive concepts — no equivalentClass to read it from.
    genus_code = await stated_queries.resolve_starting_genus(client.select, code)

    # Phase 1b: batch-resolve semantic_type_of for all filler codes (needed
    # by select_constituents for D20 axis routing).
    filler_codes = {r.filler_code for r in roles}
    if morphology_filler:
        filler_codes.add(morphology_filler)
    semantic_type_of: dict[str, list[str]] = {}
    if filler_codes:
        rows = await client.select(
            stated_queries.build_semantic_type_of_query(list(filler_codes))
        )
        semantic_type_of = extract.semantic_type_of_from_rows(rows)

    if not result.is_precoordinated:
        return _CandidateResult(decomposition=None, stated_roles=[])

    ancestor_pairs: set[tuple[str, str]] = set()
    if filler_codes:  # skip the round trip when there is nothing to look up
        ancestor_pairs = extract.ancestor_pairs_from_rows(
            await client.select(stated_queries.build_ancestor_pairs_query(filler_codes))
        )
        # Merge part-of relationships: if filler A is part-of filler B, B is
        # the "ancestor" of A for most-specific selection (D16).
        part_of_rows = await client.select(
            stated_queries.build_part_of_pairs_query(list(filler_codes))
        )
        part_of_pairs = extract.part_of_pairs_from_rows(part_of_rows)
        ancestor_pairs.update((part, whole) for (whole, part) in part_of_pairs)

    # Wrap semantic_type_of dict into a callable (prefer the first type if
    # multiple; NCIt rarely assigns more than one).
    def _semantic_type_of(filler_code: str) -> str | None:
        types = semantic_type_of.get(filler_code)
        return types[0] if types else None

    role_constituents = fs.select_constituents(
        roles,
        extract.make_is_ancestor(ancestor_pairs),
        parent_morphology=morphology_filler,
        semantic_type_of=_semantic_type_of,
    )

    aspects = nlp_fallback.parse_label_aspects(label)
    nlp_constituents, minted = await constituent_index.resolve_aspects(
        aspects, label_lookup
    )

    decomposition = Decomposition(
        code=code,
        semantic_type=result.semantic_type,
        genus_code=genus_code,
        constituents=[*role_constituents, *nlp_constituents],
    )
    return _CandidateResult(
        decomposition=decomposition, minted=minted, stated_roles=roles
    )


def _residual_count(
    decompositions: Sequence[Decomposition],
    *,
    precoordinated_fillers: set[str],
) -> int:
    """D37: how many decompositions have >=1 constituent that is itself pre-coordinated.

    Pure — the "which fillers are pre-coordinated" judgement is made once, up front, by
    :func:`_precoordinated_fillers` (running the real detector), and passed in as a set.
    """
    return sum(
        any(c.filler_code in precoordinated_fillers for c in d.constituents)
        for d in decompositions
    )


def _store_resident_constituent_fillers(
    decompositions: Sequence[Decomposition],
) -> list[str]:
    """Distinct constituent filler codes that exist in the stated graph, sorted.

    Minted/NLP fillers (``MINT-*``) are dropped: they are freshly-proposed atomic
    single-aspect concepts by construction, they do not exist in the stated graph, and
    running the detector on one is three SPARQL round-trips that can only ever return
    "atomic" (empty semantic types -> out of scope). So the residual metric is over
    *store-resident, role-sourced* constituents — the only ones the detector can judge.
    """
    return sorted(
        {
            c.filler_code
            for d in decompositions
            for c in d.constituents
            if not c.filler_code.startswith("MINT-")
        }
    )


async def _precoordinated_fillers(
    decompositions: Sequence[Decomposition],
    client: SparqlClient,
    get_labels: GetLabels | None,
    *,
    walker_max_depth: int,
) -> set[str]:
    """The constituent filler codes that are themselves pre-coordinated (D37).

    Every distinct store-resident filler is classified once, by the SAME detector that
    classified the concepts (:func:`_detect_concept`) — a filler judged pre-coordinated
    means decomposition bottomed out on a compound. De-duplicated because one filler
    recurs across many concepts; this is a post-pass over the run, so its cost is one
    detection per distinct filler, not per constituent.
    """
    fillers = _store_resident_constituent_fillers(decompositions)
    if not fillers:
        return set()
    labels = await get_labels(fillers) if get_labels is not None else {}
    precoordinated: set[str] = set()
    for filler in fillers:
        result, _roles, _morph = await _detect_concept(
            filler, client, label=labels.get(filler), walker_max_depth=walker_max_depth
        )
        if result.is_precoordinated:
            precoordinated.add(filler)
    return precoordinated


async def _persist_candidate(
    run_id: str,
    code: str,
    decomposition: Decomposition,
    minted: list[MintedConcept],
    provenance: ProvenanceStore,
    metrics: RunMetrics,
    decompositions: list[Decomposition],
) -> None:
    """Classify one decomposed candidate's outcome into *metrics* and persist it."""
    if not decomposition.constituents:
        # A concept flagged as precoordinated (≥2 decomposable axes including
        # morphology-from-parent) that somehow produced zero constituents.
        # Currently unreachable: every defining role or NLP aspect yields ≥1
        # constituent. Kept as a safety net for future edge cases.
        metrics.residual += 1
        return

    metrics.decomposed += 1
    decompositions.append(decomposition)
    await provenance.upsert_constituents(run_id, code, decomposition.constituents)
    for m in minted:
        await provenance.upsert_minted_concept(
            run_id,
            id=m.id,
            axis=m.axis,
            label=m.label,
            source_signal=m.source_signal,
            status=m.status,
        )
    metrics.minted_count += len(minted)


@dataclass(frozen=True)
class _RunSetup:
    run_id: str
    codes: list[str]
    pending: list[str]
    labels: dict[str, str]


async def _codes_to_process(
    client: SparqlClient,
    provenance: ProvenanceStore,
    config: RunConfig,
    run_id: str,
    *,
    semantic_types: Sequence[str] | None,
    page_size: int,
    total_limit: int | None,
) -> tuple[list[str], list[str]]:
    """Enumerate in-scope codes and the subset still pending for *run_id*."""
    already_processed: set[str] = set()
    if config.resume_from:
        already_processed = await provenance.processed_codes(run_id)

    codes = await enumerate_in_scope_codes(client, semantic_types, page_size=page_size)
    if total_limit is not None:
        codes = codes[:total_limit]
    pending = [c for c in codes if c not in already_processed]
    return codes, pending


async def _fetch_labels(
    get_labels: GetLabels | None, pending: list[str]
) -> dict[str, str]:
    """Batch-fetch labels for *pending*, or ``{}`` when no label source is wired."""
    if get_labels is None or not pending:
        return {}
    return await get_labels(pending)


async def _check_resume_version(
    provenance: ProvenanceStore, run_id: str, current_version: str
) -> None:
    """Refuse to resume *run_id* against a different NCIt build than it started with.

    Roles are version-pinned (design §9/§13): silently mixing two builds' roles into
    one manifest would corrupt it. ``None`` (no stored manifest yet for this id) is
    not a mismatch — there is nothing to compare against.
    """
    pinned = await provenance.run_version(run_id)
    if pinned is not None and pinned != current_version:
        raise RuntimeError(
            f"refusing to resume run_id={run_id!r}: pinned ncit_version={pinned!r} "
            f"but the live store now reports {current_version!r} — roles are "
            "version-pinned; resuming across a build bump would corrupt the manifest"
        )


async def _prepare_run(
    config: RunConfig,
    client: SparqlClient,
    provenance: ProvenanceStore,
    *,
    get_labels: GetLabels | None,
    semantic_types: Sequence[str] | None,
    page_size: int,
    total_limit: int | None,
) -> _RunSetup:
    """Resolve the run id, enumerate codes, and batch-fetch labels for a fresh run."""
    run_id = config.resume_from or _new_run_id(config.branch)
    raw_version = await client.version()
    if raw_version is None:
        logger.warning(
            "No owl:versionInfo in the stated graph for branch=%s (run_id=%s); "
            "recording ncit_version='unknown' — verify the NCIt bulk load completed",
            config.branch,
            run_id,
        )
    ncit_version = raw_version or "unknown"

    if config.resume_from:
        await _check_resume_version(provenance, run_id, ncit_version)
    await provenance.upsert_run(run_id, config.branch, ncit_version)

    codes, pending = await _codes_to_process(
        client,
        provenance,
        config,
        run_id,
        semantic_types=semantic_types,
        page_size=page_size,
        total_limit=total_limit,
    )
    labels = await _fetch_labels(get_labels, pending)
    return _RunSetup(run_id=run_id, codes=codes, pending=pending, labels=labels)


async def run_pipeline(
    config: RunConfig,
    client: SparqlClient,
    provenance: ProvenanceStore,
    *,
    get_labels: GetLabels | None = None,
    label_lookup: LabelLookup = _never_resolves,
    semantic_types: Sequence[str] | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    total_limit: int | None = None,
) -> RunMetrics:
    """Execute the decomposition pipeline for a given branch (design §9).

    ``get_labels`` batch-resolves code -> preferred label for the NLP fallback and the
    detector's label signal; when omitted, every concept is decomposed roles-only (no
    NLP fallback is attempted for any concept). ``label_lookup`` resolves an NLP
    surface form to an existing concept code; the default never resolves (always
    mints) — the conservative choice per design §7.2. ``total_limit`` caps how many
    enumerated codes are processed — a full in-scope enumeration is tens of thousands
    of concepts (assessment §3.3); use this for a manual/smoke run.
    """
    setup = await _prepare_run(
        config,
        client,
        provenance,
        get_labels=get_labels,
        semantic_types=semantic_types,
        page_size=page_size,
        total_limit=total_limit,
    )

    metrics = RunMetrics(total_in_scope=len(setup.codes))
    decompositions: list[Decomposition] = []
    fidelity_scores: list[float] = []

    for code in setup.pending:
        try:
            result = await _decompose_one(
                code,
                client,
                label=setup.labels.get(code),
                label_lookup=label_lookup,
                walker_max_depth=config.walker_max_depth,
            )
        except Exception:
            logger.exception(
                "decomposition failed for concept_code=%s (run_id=%s)",
                code,
                setup.run_id,
            )
            raise
        if result.decomposition is None:
            continue

        _maybe_collect_fidelity(result, config.emit_equivalence, fidelity_scores)

        await _persist_candidate(
            setup.run_id,
            code,
            result.decomposition,
            result.minted,
            provenance,
            metrics,
            decompositions,
        )

    metrics.pct_decomposed = metrics.coverage

    # D37: a decomposition is residually pre-coordinated when one of its own emitted
    # constituents is itself pre-coordinated (decomposition bottomed out on a compound).
    # Judged by the same detector, after the run so each distinct filler is classified
    # once. See RunMetrics for the detector-relative caveat.
    precoordinated = await _precoordinated_fillers(
        decompositions, client, get_labels, walker_max_depth=config.walker_max_depth
    )
    metrics.residual_precoordinated_count = _residual_count(
        decompositions, precoordinated_fillers=precoordinated
    )

    _finalize_fidelity(metrics, fidelity_scores, config.emit_equivalence)

    if config.out is not None:
        await write_ttl(
            decompositions,
            dest=config.out,
            run_id=setup.run_id,
            emit_equivalence=config.emit_equivalence,
        )

    finished = await provenance.finish_run(setup.run_id, metrics=asdict(metrics))
    if not finished:
        raise RuntimeError(
            f"finish_run found no decomp_run row for run_id={setup.run_id!r} "
            f"(branch={config.branch!r}) — its constituents/minted rows were "
            "written but the run manifest was never marked complete"
        )
    return metrics
