"""Decomposition engine orchestration and CLI (design section 9).

Pipeline: enumerate in-scope concepts, detect, extract, select,
NLP fallback, mint, write TTL, commit provenance.

Usage:
    pdm run decompose --source-manifest <candidate>/.ontoprism-ncit-candidate.json \
        --branch neoplasm [--out path.ttl] [--load] [--resume RUN_ID]

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
  function's — ``run_pipeline`` only ever writes the file at ``config.out``, and only
  after the source identity is re-verified and the run is marked complete: the TTL is
  rendered to an unpublished staging sibling and atomically moved into place, so a
  drifted or failed run never leaves an artifact at ``config.out``. The CLI script
  performs the store load afterwards using the concrete client's ``.load()``.
- ``--resume`` consumes the immutable materialized worklist for a matching
  running/failed run. Every concept has an explicit state, including concepts producing
  no constituents. Per-concept replacement and completion are one fenced transaction.
- Metrics and output are reconstructed cumulatively from the full persisted worklist,
  so an interrupted/resumed run is equivalent to a fresh run over the same fingerprint.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

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
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Decomposition
from ontolib.decomposition.provenance import RunStateError
from ontolib.decomposition.provenance_models import (
    NcitSourceSnapshot,
    RunFingerprint,
    RunResumeIdentity,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from pathlib import Path

    from ontolib.decomposition.constituent_index import LabelLookup
    from ontolib.decomposition.minting import MintedConcept
    from ontolib.decomposition.models import RoleRestriction
    from ontolib.decomposition.provenance import ProvenanceStore

# Batch code -> preferred label (design's NLP fallback needs the label; the detector's
# advisory label_multi_aspect signal needs it too). Injected so this module has no
# hard dependency on a concrete graph-store class — see the module docstring.
GetLabels = Callable[[list[str]], Awaitable[dict[str, str]]]
GetSourceSnapshot = Callable[[], Awaitable[NcitSourceSnapshot]]

_DEFAULT_PAGE_SIZE = 500
_ALGORITHM_VERSION = "decomposition-v1"
_CONFIG_VERSION = "axes-v1"


class SourceIdentityChangedError(RuntimeError):
    """The query source no longer matches the #181 identity pinned by the run."""


class SparqlClient(Protocol):
    """The minimal client surface this orchestrator needs (structural typing).

    ``OxigraphHttpClient`` satisfies this; tests supply a lightweight fake.
    """

    async def select(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]: ...

    async def version(self) -> str | None: ...


class DecompositionSparqlClient(
    SparqlClient,
    stated_queries.SingleAttemptSelectRows,
    Protocol,
):
    """SPARQL client with a non-retrying SELECT path for bounded closure."""


async def _never_resolves(_: str) -> str | None:
    """Default ``label_lookup`` — always mint (never guess a false match)."""
    return None


@dataclass(frozen=True)
class RunConfig:
    """Configuration for a decomposition run.

    ``load_to_store`` never causes ``run_pipeline`` to load anything: loading is a
    CLI-layer concern performed by the caller after ``run_pipeline`` returns (see the
    module docstring). It is read only to record ``load_mode`` in the immutable run
    fingerprint, which is why it must agree with ``out``. Equivalence emission is
    quarantined until #153 provides a proof-bearing representation.
    """

    branch: str
    out: Path | None = None
    load_to_store: bool = False
    emit_equivalence: bool = False
    resume_from: str | None = None
    walker_max_depth: int = 5

    def __post_init__(self) -> None:
        if self.emit_equivalence:
            raise ValueError(
                "equivalence emission is not available until a proof-bearing "
                "representation can establish exact completeness (#153)"
            )
        if self.load_to_store and self.out is None:
            raise ValueError("load_to_store requires an output path")
        # `branch` becomes part of the run id and therefore of the staging filename.
        # Reject a path-unsafe branch here rather than after the whole worklist has
        # been processed, where the run can no longer be resumed under a fixed name.
        if not self.branch or set(self.branch) & {"/", "\\", "\0"}:
            raise ValueError("branch must be non-empty and free of path separators")


@dataclass
class RunMetrics:
    """Coverage metrics for a decomposition run (design §10).

    **Two distinct residual counters — do not conflate them:**

    * ``residual`` — a concept detected as pre-coordinated that produced *zero*
      constituents. A degenerate safety net (currently unreachable: every defining role
      or NLP aspect yields >=1 constituent). NOT design
      §10's residual metric.
    * ``residual_precoordinated_count`` / :attr:`residual_precoordination` — **D37's
      metric**: decomposed concepts at least one of whose *emitted constituents is
      itself* classified as pre-coordinated by the same detector. This is "is what we
      produced actually atomic?" (irreducibility), the counterpart of
      the future ``roundtrip_fidelity`` metric's "did we capture everything?"
      (completeness).

      It is **detector-relative** — defined purely in terms of what ``detector.detect``
      flags — so an under-detecting detector reads it artificially low, and a detector
      improvement moves it with no ontology change (D37). Track it against the SME
      golden set (#57) as well as the corpus, so divergence surfaces detector drift.

      **What it can fire on.** ``detect`` gates on the in-scope semantic types
      (neoplasm/disease/dysfunction) *and* >=2 decomposable axes, so the metric flags a
      constituent filler only when the filler is *itself* an in-scope compound. Two
      classes of filler therefore never fire, for different reasons: **anatomic-site**
      fillers are out of scope by *semantic type* (``Body Part, Organ, or Organ
      Component`` is not in scope), and **minted/NLP** fillers are atomic by definition
      (and excluded before detection). But **morphology/genus** fillers do *not* get a
      pass — the morphology constituent's filler is the genus code, a store-resident
      neoplasm that is squarely in scope, and it fires precisely when that genus is
      itself a defined >=2-axis class. That is the *most likely* source of a non-zero
      reading (the genus chain is exactly where compounds nest), and it is a legitimate
      residual signal, not a blind spot. So a **real** run reading 0 means either the
      corpus genuinely bottoms out on atomic in-scope fillers, or the detector never met
      an in-scope compound filler at all — indistinguishable without the real run, which
      is why D37 makes a 0 on the first run (#127) a signal to suspect the detector, and
      why the number is proved reachable there (start at the morphology/genus path), on
      real data, not only in unit tests.

    ``roundtrip_fidelity`` is unavailable for the current curated projection. Numeric
    values from historical runs remain readable, but new runs record ``None`` until
    #153 provides a proof-bearing representation.
    """

    total_in_scope: int = 0
    decomposed: int = 0
    residual: int = 0
    residual_precoordinated_count: int = 0
    minted_count: int = 0
    pct_decomposed: float = 0.0
    roundtrip_fidelity: None = None

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

    def __post_init__(self) -> None:
        if self.decomposition is None and self.minted:
            raise ValueError(
                "_CandidateResult: minted concepts without a decomposition is not "
                "a valid state — minting only happens while building constituents "
                "for an actual candidate"
            )


def _new_run_id(branch: str) -> str:
    return f"{branch}-{uuid4()}"


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
            ),
            required_variables={"concept"},
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
        await client.select(
            stated_queries.build_semantic_type_query(code),
            required_variables={"semanticType"},
        )
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
    client: DecompositionSparqlClient,
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

    # Phase 1a: batch-resolve semantic_type_of for all filler codes (needed
    # by select_constituents for D20 axis routing).
    filler_codes = {r.filler_code for r in roles}
    if morphology_filler:
        filler_codes.add(morphology_filler)
    semantic_type_of: dict[str, list[str]] = {}
    if filler_codes:
        rows = await client.select(
            stated_queries.build_semantic_type_of_query(list(filler_codes)),
            required_variables={"code", "st"},
        )
        semantic_type_of = extract.semantic_type_of_from_rows(rows)

    if not result.is_precoordinated:
        return _CandidateResult(decomposition=None)

    ancestor_pairs = extract.ancestor_pairs_from_rows(
        await client.select(
            stated_queries.build_ancestor_pairs_query(filler_codes),
            required_variables={"ancestor", "descendant"},
        )
    )
    # Treat an R82 whole as broader than its part for specificity selection (D16).
    part_of_pairs = await stated_queries.resolve_part_of_pairs(
        client, fs.comparison_filler_codes(roles)
    )
    ancestor_pairs.update(
        extract.AncestorPair(ancestor=pair.whole, descendant=pair.part)
        for pair in part_of_pairs
    )

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
        constituents=[*role_constituents, *nlp_constituents],
    )
    return _CandidateResult(decomposition=decomposition, minted=minted)


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
        try:
            result, _roles, _morph = await _detect_concept(
                filler,
                client,
                label=labels.get(filler),
                walker_max_depth=walker_max_depth,
            )
        except Exception:
            # Match the main loop's contextual log-then-reraise (this pass is not in
            # it): a bare traceback from the metric post-pass otherwise names no filler
            # and no phase. Still fail-fast — re-raise; the metric never swallows a
            # store error into a quiet 0.
            logger.exception(
                "residual-precoordination detection failed for filler_code=%s", filler
            )
            raise
        if result.is_precoordinated:
            precoordinated.add(filler)
    return precoordinated


@dataclass(frozen=True)
class _RunSetup:
    run_id: str
    source_snapshot: NcitSourceSnapshot
    fingerprint: RunFingerprint
    pending: list[str]
    labels: dict[str, str]


async def _fetch_labels(
    get_labels: GetLabels | None, pending: list[str]
) -> dict[str, str]:
    """Batch-fetch labels for *pending*, or ``{}`` when no label source is wired."""
    if get_labels is None or not pending:
        return {}
    return await get_labels(pending)


async def _require_source_snapshot(
    client: SparqlClient,
    get_source_snapshot: GetSourceSnapshot,
    *,
    expected: NcitSourceSnapshot | None = None,
) -> NcitSourceSnapshot:
    snapshot = await get_source_snapshot()
    current_version = await client.version()
    if current_version != snapshot.ontology_version:
        raise SourceIdentityChangedError(
            "query endpoint ontology version does not match the #181 source proof"
        )
    if expected is not None and snapshot != expected:
        raise SourceIdentityChangedError(
            "NCIt source identity changed during the decomposition run"
        )
    return snapshot


def _resume_identity(
    config: RunConfig,
    snapshot: NcitSourceSnapshot,
    *,
    semantic_types: tuple[str, ...],
    total_limit: int | None,
) -> RunResumeIdentity:
    return RunResumeIdentity(
        source_identity=snapshot.source_identity,
        branch=config.branch,
        semantic_types=semantic_types,
        total_limit=total_limit,
        algorithm_version=_ALGORITHM_VERSION,
        config_version=_CONFIG_VERSION,
        walker_max_depth=config.walker_max_depth,
        output_mode="file" if config.out is not None else "none",
        load_mode="named-graph" if config.load_to_store else "none",
    )


def _canonical_scope(semantic_types: Sequence[str] | None) -> tuple[str, ...]:
    selected = (
        semantic_types if semantic_types is not None else axes.IN_SCOPE_SEMANTIC_TYPES
    )
    return tuple(sorted(selected))


async def _create_fresh_run(
    config: RunConfig,
    client: SparqlClient,
    provenance: ProvenanceStore,
    snapshot: NcitSourceSnapshot,
    *,
    get_source_snapshot: GetSourceSnapshot,
    scope: tuple[str, ...],
    page_size: int,
    total_limit: int | None,
) -> tuple[str, RunFingerprint]:
    run_id = _new_run_id(config.branch)
    codes = await enumerate_in_scope_codes(client, scope, page_size=page_size)
    if total_limit is not None:
        if total_limit <= 0:
            raise ValueError("total_limit must be greater than zero")
        codes = codes[:total_limit]
    if len(codes) != len(set(codes)):
        raise RuntimeError("scope enumeration returned duplicate concept codes")
    await _require_source_snapshot(
        client,
        get_source_snapshot,
        expected=snapshot,
    )
    fingerprint = RunFingerprint(
        source_identity=snapshot.source_identity,
        branch=config.branch,
        semantic_types=scope,
        worklist=tuple(codes),
        total_limit=total_limit,
        algorithm_version=_ALGORITHM_VERSION,
        config_version=_CONFIG_VERSION,
        walker_max_depth=config.walker_max_depth,
        output_mode="file" if config.out is not None else "none",
        load_mode="named-graph" if config.load_to_store else "none",
        emitted_at=datetime.now(UTC),
    )
    await provenance.create_run(run_id, snapshot.ontology_version, fingerprint)
    return run_id, fingerprint


async def _load_pending_run_data(
    provenance: ProvenanceStore,
    run_id: str,
    get_labels: GetLabels | None,
) -> tuple[list[str], dict[str, str]]:
    try:
        pending = await provenance.pending_codes(run_id)
        return pending, await _fetch_labels(get_labels, pending)
    except BaseException as exc:
        try:
            if not await provenance.fail_run(run_id, exc):
                exc.add_note(
                    f"Run setup failure was NOT recorded: run {run_id!r} holds a "
                    "different terminal state, or its row is gone."
                )
        except BaseException as failure_error:
            exc.add_note(
                "Recording the run setup failure also failed: "
                f"{type(failure_error).__name__}: {failure_error}"
            )
        raise


async def _prepare_run(
    config: RunConfig,
    client: SparqlClient,
    provenance: ProvenanceStore,
    *,
    get_source_snapshot: GetSourceSnapshot,
    get_labels: GetLabels | None,
    semantic_types: Sequence[str] | None,
    page_size: int,
    total_limit: int | None,
) -> _RunSetup:
    """Create or reopen one exact source-bound worklist."""
    snapshot = await _require_source_snapshot(client, get_source_snapshot)
    scope = _canonical_scope(semantic_types)
    if config.resume_from:
        run_id = config.resume_from
        fingerprint = await provenance.resume_run(
            run_id,
            _resume_identity(
                config,
                snapshot,
                semantic_types=scope,
                total_limit=total_limit,
            ),
        )
    else:
        run_id, fingerprint = await _create_fresh_run(
            config,
            client,
            provenance,
            snapshot,
            get_source_snapshot=get_source_snapshot,
            scope=scope,
            page_size=page_size,
            total_limit=total_limit,
        )
    pending, labels = await _load_pending_run_data(
        provenance,
        run_id,
        get_labels,
    )
    return _RunSetup(
        run_id=run_id,
        source_snapshot=snapshot,
        fingerprint=fingerprint,
        pending=pending,
        labels=labels,
    )


async def _process_work_item(
    setup: _RunSetup,
    code: str,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    label_lookup: LabelLookup,
    walker_max_depth: int,
) -> None:
    claim = await provenance.claim_work_item(setup.run_id, code)
    if claim is None:
        raise RunStateError(f"work item {setup.run_id!r}/{code!r} could not be claimed")
    try:
        result = await _decompose_one(
            code,
            client,
            label=setup.labels.get(code),
            label_lookup=label_lookup,
            walker_max_depth=walker_max_depth,
        )
        await provenance.complete_work_item(
            setup.run_id,
            code,
            claim,
            decomposition=result.decomposition,
            minted=tuple(result.minted),
        )
    except BaseException as exc:
        logger.exception(
            "decomposition failed for concept_code=%s (run_id=%s)",
            code,
            setup.run_id,
        )
        try:
            await provenance.fail_work_item(setup.run_id, code, claim, exc)
        except BaseException as failure_error:
            exc.add_note(
                "Recording the work-item failure also failed: "
                f"{type(failure_error).__name__}: {failure_error}"
            )
        raise


async def _process_pending_work(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    label_lookup: LabelLookup,
) -> None:
    for code in setup.pending:
        await _process_work_item(
            setup,
            code,
            client,
            provenance,
            label_lookup=label_lookup,
            walker_max_depth=config.walker_max_depth,
        )


async def _reconstructed_metrics(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    get_labels: GetLabels | None,
) -> tuple[RunMetrics, list[Decomposition]]:
    """Rebuild metrics cumulatively from the full persisted worklist."""
    decompositions = await provenance.decompositions_for_run(setup.run_id)
    counts = await provenance.outcome_counts(setup.run_id)
    metrics = RunMetrics(
        total_in_scope=counts.total_in_scope,
        decomposed=counts.decomposed,
        residual=counts.residual,
        minted_count=counts.minted_count,
    )
    precoordinated = await _precoordinated_fillers(
        decompositions,
        client,
        get_labels,
        walker_max_depth=config.walker_max_depth,
    )
    metrics.residual_precoordinated_count = _residual_count(
        decompositions, precoordinated_fillers=precoordinated
    )
    metrics.pct_decomposed = metrics.coverage
    return metrics, decompositions


def _publication_paths(config: RunConfig, run_id: str) -> tuple[Path, Path] | None:
    """Pair the unpublished staging path with its destination, or neither.

    One correlated value: a staging path without a destination (or the reverse) is
    not representable, so publication cannot be silently skipped.
    """
    if config.out is None:
        return None
    return config.out.with_name(f".{config.out.name}.staging-{run_id}"), config.out


def _discard_staging(staging: Path, exc: BaseException) -> None:
    """Remove an unpublished artifact without ever replacing the original error.

    Letting an ``OSError`` escape here would hide a ``SourceIdentityChangedError``
    and route the run to ``fail_run`` instead of ``invalidate_run``.
    """
    try:
        staging.unlink(missing_ok=True)
    except OSError as cleanup_error:
        exc.add_note(
            f"Unpublished staging artifact {staging} could not be removed: "
            f"{cleanup_error}"
        )


async def _finish_run(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    get_source_snapshot: GetSourceSnapshot,
    get_labels: GetLabels | None,
) -> RunMetrics:
    metrics, decompositions = await _reconstructed_metrics(
        setup,
        config,
        client,
        provenance,
        get_labels=get_labels,
    )
    publication = _publication_paths(config, setup.run_id)
    try:
        if publication is not None:
            # Render to an unpublished staging sibling: the artifact must not appear
            # at the operator's path unless the source identity still holds *and* the
            # run completed, otherwise a drifted (mixed-source) run leaves a
            # complete-looking TTL behind after its rows are invalidated.
            await write_ttl(
                decompositions,
                dest=publication[0],
                run_id=setup.run_id,
                emitted_on=setup.fingerprint.emitted_at.date(),
            )
        await _require_source_snapshot(
            client,
            get_source_snapshot,
            expected=setup.source_snapshot,
        )
        finished = await provenance.finish_run(
            setup.run_id,
            source_identity=setup.fingerprint.source_identity,
            metrics=asdict(metrics),
        )
        if not finished:
            raise RuntimeError(
                f"finish_run found no decomp_run row for run_id={setup.run_id!r} "
                f"(branch={config.branch!r})"
            )
    except BaseException as exc:
        if publication is not None:
            _discard_staging(publication[0], exc)
        raise
    if publication is not None:
        try:
            publication[0].replace(publication[1])
        except OSError as publish_error:
            # The run is already recorded complete: say so plainly rather than let
            # the generic run-failure note claim the failure was never recorded.
            publish_error.add_note(
                f"Run {setup.run_id!r} completed but its artifact was not published "
                f"to {publication[1]}; the rendered output remains at "
                f"{publication[0]}."
            )
            raise
    return metrics


async def run_pipeline(
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    get_source_snapshot: GetSourceSnapshot,
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
        get_source_snapshot=get_source_snapshot,
        get_labels=get_labels,
        semantic_types=semantic_types,
        page_size=page_size,
        total_limit=total_limit,
    )

    try:
        await _process_pending_work(
            setup,
            config,
            client,
            provenance,
            label_lookup,
        )
        return await _finish_run(
            setup,
            config,
            client,
            provenance,
            get_source_snapshot=get_source_snapshot,
            get_labels=get_labels,
        )
    except BaseException as exc:
        try:
            if isinstance(exc, SourceIdentityChangedError):
                discarded = await provenance.invalidate_run(setup.run_id, exc)
                if not discarded:
                    exc.add_note(
                        "Partial results were NOT discarded: run "
                        f"{setup.run_id!r} was no longer 'running'. Inspect "
                        "decomp_constituent/decomp_minted_proposal before reuse."
                    )
            else:
                recorded = await provenance.fail_run(setup.run_id, exc)
                if not recorded:
                    exc.add_note(
                        f"Run failure was NOT recorded: run {setup.run_id!r} holds "
                        "a different terminal state, or its row is gone."
                    )
        except BaseException as failure_error:
            exc.add_note(
                "Recording the run failure also failed: "
                f"{type(failure_error).__name__}: {failure_error}"
            )
        raise
