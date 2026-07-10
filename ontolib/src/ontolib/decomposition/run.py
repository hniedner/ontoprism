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
- Morphology-from-parent (design §6, the ``op:Morphology`` axis) is not wired: there is
  no query yet for "nearest morphology-bearing taxonomic parent". ``parent_morphology``
  is always passed as ``None`` to ``filler_selection.select_constituents``.
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
  (including a resume); ``decomposed``/``residual``/``minted_count`` cover only the
  pending subset actually processed *this* invocation. So on a resumed run,
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
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Decomposition

logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ontolib.decomposition.constituent_index import LabelLookup
    from ontolib.decomposition.minting import MintedConcept
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
    """Coverage metrics for a decomposition run (design §10, the subset implemented).

    ``residual`` here is narrower than design §10's ``residual_precoordination``
    ("candidates left with an unresolved multi-aspect label after roles+NLP"): this
    field counts candidates detected as precoordinated that still produced *zero*
    constituents (currently unreachable — see ``_persist_candidate``), not candidates
    with leftover ambiguity after a genuine attempt. The design's actual
    ``residual_precoordination`` metric is not implemented yet.
    """

    total_in_scope: int = 0
    decomposed: int = 0
    residual: int = 0
    minted_count: int = 0
    pct_decomposed: float = 0.0

    @property
    def coverage(self) -> float:
        """Fraction of in-scope concepts successfully decomposed."""
        if self.total_in_scope == 0:
            return 0.0
        return self.decomposed / self.total_in_scope


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
    return f"{branch}-{datetime.now(UTC).isoformat(timespec='seconds')}"


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
    semantic_types = extract.semantic_types_from_rows(
        await client.select(stated_queries.build_semantic_type_query(code))
    )

    # Phase 1: walk the genus chain — works for both primitive and defined
    # classes. For primitive concepts (no owl:equivalentClass), the walker
    # returns zero roles, which is correct — nothing to decompose.
    roles = await stated_queries.walk_genus_chain(
        client.select, code, max_depth=walker_max_depth
    )

    # Phase 1a: resolve immediate genus for the equivalence axiom (the first
    # ``owl:intersectionOf`` member of the starting concept).  ``None`` for
    # primitive concepts — no equivalentClass to read it from.
    genus_code = await stated_queries.resolve_starting_genus(client.select, code)

    # Phase 1a.5: resolve morphology filler from genus chain (design §6).
    # First non-staging genus code, or None if no morphology-bearing parent.
    morphology_filler = await stated_queries.resolve_morphology_filler(
        client.select, code, max_depth=walker_max_depth
    )

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

    result = detector.detect(code, semantic_types, roles, label=label)
    if not result.is_precoordinated:
        return _CandidateResult(decomposition=None)

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
    return _CandidateResult(decomposition=decomposition, minted=minted)


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
        # Not reachable today: reaching is_precoordinated requires >=2 decomposable
        # axes, and every defining role/NLP aspect present always yields >=1
        # constituent (see select_constituents / resolve_aspects) — so this only
        # activates once morphology-from-parent (design §6, not wired here) can
        # contribute an axis with no filler of its own. Kept for that forward
        # compatibility rather than dropped as unreachable.
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
