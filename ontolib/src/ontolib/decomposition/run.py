"""Decomposition engine orchestration and CLI (design section 9).

Pipeline: enumerate in-scope concepts, detect, extract, select,
NLP fallback, mint, write TTL, commit provenance.

Usage:
    pdm run decompose --source-manifest <candidate>/.ontoprism-ncit-candidate.json \
        --branch neoplasm [--out path.ttl] [--load] [--resume RUN_ID]

Scope of this orchestrator (documented boundaries, not oversights):
- Extraction uses the bounded complete-definition reader through
  ``stated_queries.read_complete_genus_chain`` to traverse named-genus and anonymous
  nested ``owl:intersectionOf`` groups once, collecting role restrictions from the same
  record later persisted as provenance. A ``_CORE_NEOPLASM_ROLES`` boundary filter
  prevents over-collection of generic neoplasm biology from deep genus ancestors.
- Morphology-from-parent (design §6, the ``op:Morphology`` axis) is wired:
  ``stated_queries.resolve_morphology_fillers`` walks every co-equal genus branch to
  its first non-staging genus, parent-derived fillers pass through projection-validity
  assessment before accepted ``op:Morphology`` constituents are appended, and
  ``detector.detect`` counts the axis once.
- File and optional named-graph publication are coordinated inside ``run_pipeline``.
  A complete artifact is rendered and validated first, the graph is replaced through
  a run-scoped staging graph and one transactional update, the file is atomically
  replaced, and only then is the run marked complete. Failures after intent journaling
  but before completion remain separately recorded and resumable.
- ``--resume`` consumes the immutable materialized worklist for a matching
  running/failed run. Every concept has an explicit state, including concepts producing
  no constituents. Per-concept replacement and completion are one fenced transaction.
- Metrics and output are reconstructed cumulatively from the full persisted worklist,
  so an interrupted/resumed run is equivalent to a fresh run over the same fingerprint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID, uuid4

from ontolib.core.logging_config import get_logger
from ontolib.decomposition import (
    axes,
    axis_diagnostics,
    complete_definition,
    constituent_index,
    detector,
    extract,
    nlp_fallback,
    stated_queries,
)
from ontolib.decomposition import (
    filler_selection as fs,
)
from ontolib.decomposition import (
    scope as hierarchy_scope,
)
from ontolib.decomposition.branches import (
    DecompositionAlgorithm,
    DecompositionBranch,
    ScopeRoot,
    ScopeVersion,
    branch_spec,
    parse_branch,
)
from ontolib.decomposition.collapse_policy import (
    CollapseVetoPolicy,
    load_packaged_collapse_veto_policy,
)
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.mixed_chain_inventory import (
    load_mixed_chain_inventory,
    mixed_chain_worklist_identity,
    require_mixed_chain_preflight,
)
from ontolib.decomposition.models import (
    CompleteDefinition,
    ConceptOutcome,
    Decomposition,
)
from ontolib.decomposition.normalized_group_policy import (
    ActiveNormalizedGroupPolicy,
    apply_normalized_group_policy,
    load_packaged_normalized_group_policy,
)
from ontolib.decomposition.projection_validity import (
    ProjectionAssessment,
    UnknownProjectionEvidence,
    freeze_projection_assessments,
)
from ontolib.decomposition.provenance import RunStateError
from ontolib.decomposition.provenance_models import (
    NO_MIXED_CHAIN_INVENTORY_IDENTITY,
    RUN_STAGE_SEQUENCE_IDENTITY,
    CompletionRunMetrics,
    FreshAdmitted,
    FullRunExecutionIdentity,
    NcitSourceSnapshot,
    Refused,
    ResidualFillerClassification,
    RunFingerprint,
    RunResumeIdentity,
    RunStageName,
)
from ontolib.decomposition.publication import (
    PublicationFinalizationError,
    PublicationGraphClient,
    PublicationPreflightError,
    publish_artifact,
)
from ontolib.decomposition.r101_run_conservation import (
    R101ConservationRecord,
    classify_r101_conservation,
)
from ontolib.decomposition.semantic_identity import routing_implementation_identity
from ontolib.decomposition.source_preflight import (
    ClosureBudgetExceededError,
    SourcePreflightResult,
    run_source_preflight,
)

logger = get_logger(__name__)

_PROGRESS_HEARTBEAT_SECONDS = 15.0
_SOURCE_PREFLIGHT_MAX_CLOSURE_NODES = 20_000

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from pathlib import Path

    from ontolib.decomposition.constituent_index import LabelLookup
    from ontolib.decomposition.minting import MintedConcept
    from ontolib.decomposition.models import Constituent, RoleRestriction
    from ontolib.decomposition.provenance import ProvenanceStore
    from ontolib.decomposition.sampling import DecompositionSampleManifest

# Batch code -> preferred label (design's NLP fallback needs the label; the detector's
# advisory label_multi_aspect signal needs it too). Injected so this module has no
# hard dependency on a concrete graph-store class — see the module docstring.
GetLabels = Callable[[list[str]], Awaitable[dict[str, str]]]
GetSourceSnapshot = Callable[[], Awaitable[NcitSourceSnapshot]]

_CONFIG_VERSION = "nested-definition-v2"


class SourceIdentityChangedError(RuntimeError):
    """The query source no longer matches the #181 identity pinned by the run."""


class RunPublicationError(RuntimeError):
    """Artifact publication failed and remains retryable on the running run."""


class SourcePreflightRejectedError(RuntimeError):
    """The completed source census found malformed or over-bound definitions."""


class RunAdmissionRefusedError(RuntimeError):
    """The authoritative provenance boundary refused this invocation."""


class SparqlClient(Protocol):
    """The minimal client surface this orchestrator needs (structural typing).

    ``SparqlHttpClient`` satisfies this; tests supply a lightweight fake.
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
    PublicationGraphClient,
    Protocol,
):
    """SPARQL client with a non-retrying SELECT path for bounded closure."""


async def _never_resolves(_: str) -> str | None:
    """Default ``label_lookup`` — always mint (never guess a false match)."""
    return None


def _validate_rehearsal_config(config: RunConfig) -> None:
    if not config.rehearsal:
        return
    if config.load_to_store:
        raise ValueError("a rehearsal never publishes to the store")
    if config.resume_from is not None:
        raise ValueError("a rehearsal is a throwaway run and cannot be resumed")
    if config.out is not None:
        raise ValueError("a rehearsal does not accept an output path")


def _validate_sample_config(config: RunConfig) -> None:
    sample = config.sample_manifest
    if sample is None:
        return
    if _sample_output_missing(config):
        raise ValueError("a sample run requires an output path")
    if config.load_to_store:
        raise ValueError("a sample run cannot load into the configured store")
    if sample.branch != config.branch.value:
        raise ValueError("sample manifest does not match run branch")
    # Unreachable today, and deliberately kept: `ScopeVersion` is a single-value
    # Literal and `DecompositionSampleManifest` derives `scope_root` from its own
    # branch, so a manifest that passes the branch check above always agrees here.
    # Adding a second scope version makes this the only thing stopping a manifest
    # walked under one version from being run under another.
    if (
        sample.scope_root != config.scope_root
        or sample.scope_version != config.scope_version
    ):
        raise ValueError("sample manifest does not match run hierarchy scope")


def _sample_output_missing(config: RunConfig) -> bool:
    return config.out is None and not config.rehearsal


def _output_mode(config: RunConfig) -> Literal["none", "file"]:
    return "file" if config.out is not None and not config.rehearsal else "none"


class RunConfig:
    """Configuration for a decomposition run.

    ``load_to_store`` publishes the validated artifact through a staging graph before
    run completion. It therefore requires ``out`` and is part of the immutable run
    fingerprint. Equivalence emission is quarantined until a separate
    equivalence-validation step can prove the complete representation is exact.
    """

    def __init__(
        self,
        branch: DecompositionBranch | str,
        out: Path | None = None,
        load_to_store: bool = False,
        emit_equivalence: bool = False,
        resume_from: str | None = None,
        walker_max_depth: int = 7,
        sample_manifest: DecompositionSampleManifest | None = None,
        mixed_chain_inventory_path: Path | None = None,
        rehearsal: bool = False,
    ) -> None:
        self.branch = parse_branch(branch)
        self.out = out
        self.load_to_store = load_to_store
        self.emit_equivalence = emit_equivalence
        self.resume_from = resume_from
        self.walker_max_depth = walker_max_depth
        self.sample_manifest = sample_manifest
        self.mixed_chain_inventory_path = mixed_chain_inventory_path
        # A rehearsal runs the pipeline as a throwaway: it is admitted afresh every
        # time, never publishes, never promotes its mint proposals, never resumes.
        self.rehearsal = rehearsal
        _validate_rehearsal_config(self)
        if self.emit_equivalence:
            raise ValueError(
                "equivalence emission is not available until a separate validation "
                "step can establish exact completeness"
            )
        if self.load_to_store and self.out is None:
            raise ValueError("load_to_store requires an output path")
        _validate_sample_config(self)

    @property
    def semantic_types(self) -> tuple[str, ...]:
        """Canonical algorithm-applicability types selected by this branch."""
        return branch_spec(self.branch).semantic_types

    @property
    def scope_root(self) -> ScopeRoot:
        """NCIt root whose stated named-class DAG defines the branch population."""
        return branch_spec(self.branch).root_code

    @property
    def scope_version(self) -> ScopeVersion:
        """Version of the hierarchy-edge and closure contract."""
        return branch_spec(self.branch).scope_version

    @property
    def algorithm(self) -> DecompositionAlgorithm:
        """Algorithm selected by this branch."""
        return branch_spec(self.branch).algorithm

    @property
    def algorithm_version(self) -> str:
        """Version of the selected algorithm, persisted in the run identity."""
        return branch_spec(self.branch).algorithm_version


class RunMetrics:
    """Coverage metrics for a decomposition run (design §10).

    **Two distinct residual counters — do not conflate them:**

    * ``residual`` — a concept detected as pre-coordinated that produced *zero*
      constituents. A degenerate safety net (currently unreachable: every defining role
      or NLP aspect yields >=1 constituent). NOT design
      §10's residual metric.
    * ``residual_precoordinated_count`` / :attr:`residual_precoordination` — **D37's
      metric**: decomposed concepts at least one of whose *emitted constituents is
      itself* classified as pre-coordinated by the same detector. The rate is
      unavailable when ``residual_precoordination_unknown_count`` records a
      decomposition containing a filler whose valid OWL constructor the detector cannot
      interpret; unknown is never reported as atomic. This is "is what we produced
      actually atomic?" (irreducibility), the counterpart of
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
      why the morphology/genus path is the relevant place to investigate a zero result.

    ``roundtrip_fidelity`` is unavailable for the current curated projection. Numeric
    values from historical runs remain readable, but new runs record ``None`` until
    a separate validation step proves exact equivalence from the complete record.
    """

    __hash__ = None  # type: ignore[assignment]

    def __init__(
        self,
        *,
        total_in_scope: int = 0,
        decomposed: int = 0,
        residual: int = 0,
        semantic_excluded: int = 0,
        atomic_noop: int = 0,
        unknown_outcome: int = 0,
        residual_precoordinated_count: int = 0,
        residual_precoordination_unknown_count: int = 0,
        minted_count: int = 0,
        complete_definition_count: int = 0,
        complete_fact_count: int = 0,
        projected_fact_count: int = 0,
        projection_loss_count: int = 0,
        projection_loss_rate: float = 0.0,
        pct_decomposed: float = 0.0,
        roundtrip_fidelity: None = None,
    ) -> None:
        self.total_in_scope = total_in_scope
        self.decomposed = decomposed
        self.residual = residual
        self.semantic_excluded = semantic_excluded
        self.atomic_noop = atomic_noop
        self.unknown_outcome = unknown_outcome
        self.residual_precoordinated_count = residual_precoordinated_count
        self.residual_precoordination_unknown_count = (
            residual_precoordination_unknown_count
        )
        self.minted_count = minted_count
        self.complete_definition_count = complete_definition_count
        self.complete_fact_count = complete_fact_count
        self.projected_fact_count = projected_fact_count
        self.projection_loss_count = projection_loss_count
        self.projection_loss_rate = projection_loss_rate
        self.pct_decomposed = pct_decomposed
        self.roundtrip_fidelity = roundtrip_fidelity

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RunMetrics) and _persisted_metrics(
            self
        ) == _persisted_metrics(other)

    @property
    def coverage(self) -> float:
        """Fraction of in-scope concepts successfully decomposed."""
        if self.total_in_scope == 0:
            return 0.0
        return self.decomposed / self.total_in_scope

    @property
    def residual_precoordination(self) -> float | None:
        """D37: fraction of decomposed concepts that are residually pre-coordinated.

        Detector-relative (see the class docstring). ``0.0`` when nothing decomposed —
        honestly zero, not undefined.
        """
        if self.residual_precoordination_unknown_count:
            return None
        if self.decomposed == 0:
            return 0.0
        return self.residual_precoordinated_count / self.decomposed


def _require_candidate_outcome_shape(
    decomposition: Decomposition | None,
    outcome: ConceptOutcome,
) -> None:
    if outcome == "decomposed":
        _require_decomposed_candidate(decomposition)
        return
    if outcome == "residual":
        _require_residual_candidate(decomposition)
        return
    if outcome not in {"decomposed", "residual"} and decomposition is not None:
        raise ValueError(
            "_CandidateResult: non-decomposition outcomes cannot carry a decomposition"
        )


def _require_decomposed_candidate(decomposition: Decomposition | None) -> None:
    if decomposition is None or not decomposition.constituents:
        raise ValueError(
            "_CandidateResult: decomposed outcome requires at least one constituent"
        )


def _require_residual_candidate(decomposition: Decomposition | None) -> None:
    if decomposition is None or decomposition.constituents:
        raise ValueError(
            "_CandidateResult: residual outcome requires exactly zero constituents"
        )


def _require_candidate_mint_shape(
    outcome: ConceptOutcome,
    minted: tuple[MintedConcept, ...],
) -> None:
    if outcome != "decomposed" and minted:
        raise ValueError(
            "_CandidateResult: minted concepts require a decomposed outcome"
        )


@dataclass(frozen=True, slots=True)
class _CandidateResult:
    decomposition: Decomposition | None
    outcome: ConceptOutcome
    semantic_types: tuple[str, ...]
    minted: tuple[MintedConcept, ...] = ()
    r101_conservation: R101ConservationRecord | None = None
    complete_definition: CompleteDefinition | None = None

    def __post_init__(self) -> None:
        _require_candidate_outcome_shape(self.decomposition, self.outcome)
        object.__setattr__(self, "minted", tuple(self.minted))
        _require_candidate_mint_shape(self.outcome, self.minted)


def _new_run_id(branch: DecompositionBranch | str) -> str:
    return f"{branch}-{uuid4()}"


async def enumerate_in_scope_codes(
    client: DecompositionSparqlClient,
    root_code: str,
) -> list[str]:
    """Materialize a hierarchy-defined branch population from stated named edges."""
    return list(await hierarchy_scope.enumerate_scope_codes(client, root_code))


async def _detect_concept(
    code: str,
    client: SparqlClient,
    *,
    label: str | None,
    walker_max_depth: int,
) -> tuple[
    detector.DetectionResult,
    list[RoleRestriction],
    tuple[str, ...],
    CompleteDefinition,
    tuple[str, ...],
]:
    """Run the detector on *code*: semantic types, genus-chain roles, and morphology.

    Returns the ``DetectionResult`` plus the ``roles`` and morphology fillers the
    caller reuses, so this same machinery classifies both a decomposition candidate
    (in :func:`_decompose_one`) and, unchanged, each emitted constituent's filler when
    computing ``residual_precoordination`` (D37): the metric is only meaningful if a
    constituent is judged by the *same* detector as the concept it came from.
    """
    semantic_types = await _semantic_types_for_concept(client, code)
    definition, roles = await stated_queries.read_complete_genus_chain(
        client.select, code, max_depth=walker_max_depth
    )
    morphology_fillers = await stated_queries.resolve_morphology_fillers(
        client.select, definition, max_depth=walker_max_depth
    )
    result = detector.detect(
        code,
        semantic_types,
        roles,
        has_parent_morphology=bool(morphology_fillers),
        label=label,
    )
    return result, roles, morphology_fillers, definition, semantic_types


async def _semantic_types_for_concept(
    client: SparqlClient, code: str
) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(
                extract.semantic_types_from_rows(
                    await client.select(
                        stated_queries.build_semantic_type_query(code),
                        required_variables={"semanticType"},
                    )
                )
            )
        )
    )


def _non_candidate_outcome(
    semantic_types: tuple[str, ...],
) -> ConceptOutcome:
    if any(axes.is_in_scope(value) for value in semantic_types):
        return "atomic-no-op"
    return "semantic-excluded"


async def _detect_candidate_or_unknown(
    code: str,
    client: DecompositionSparqlClient,
    *,
    label: str | None,
    walker_max_depth: int,
) -> (
    tuple[
        detector.DetectionResult,
        list[RoleRestriction],
        tuple[str, ...],
        CompleteDefinition,
        tuple[str, ...],
    ]
    | _CandidateResult
):
    try:
        return await _detect_concept(
            code, client, label=label, walker_max_depth=walker_max_depth
        )
    except complete_definition.UnsupportedDefinitionConstructorError:
        return _CandidateResult(
            decomposition=None,
            outcome="unknown",
            semantic_types=await _semantic_types_for_concept(client, code),
        )


def _candidate_filler_codes(
    roles: list[RoleRestriction], morphology_fillers: tuple[str, ...]
) -> set[str]:
    codes = {role.filler_code for role in roles}
    return codes | set(morphology_fillers)


async def _filler_semantic_types(
    client: DecompositionSparqlClient, filler_codes: set[str]
) -> dict[str, list[str]]:
    if not filler_codes:
        return {}
    rows = await client.select(
        stated_queries.build_semantic_type_of_query(list(filler_codes)),
        required_variables={"code", "st"},
    )
    return extract.semantic_type_of_from_rows(rows)


def _semantic_type_resolver(
    semantic_types: dict[str, list[str]],
) -> Callable[[str], str | None]:
    def resolve(filler_code: str) -> str | None:
        types = semantic_types.get(filler_code)
        if not types:
            return None
        if axes.ORGAN_SEMANTIC_TYPE in types:
            return axes.ORGAN_SEMANTIC_TYPE
        return min(types)

    return resolve


def _projection_keys(plan: fs.RoutedPlan) -> set[tuple[str, str]]:
    routed = {
        (occurrence.normalized_axis, occurrence.restriction.filler_code)
        for occurrence in plan.occurrences
    }
    morphologies = {
        (axes.MORPHOLOGY_AXIS, filler) for filler in plan.parent_morphologies
    }
    return routed | morphologies


def _projection_assessments(
    plan: fs.RoutedPlan,
    diagnostic_source: axis_diagnostics.AxisDiagnosticSource,
    detector_identity: str,
) -> Mapping[tuple[str, str], ProjectionAssessment]:
    keys = _projection_keys(plan)
    atomicity_by_filler = {
        filler: UnknownProjectionEvidence(
            status="unknown",
            reason="not-classified-for-issue-replay",
            filler_code=filler,
            detector_identity=detector_identity,
        )
        for _axis, filler in keys
    }
    return freeze_projection_assessments(
        ProjectionAssessment(
            axis_range=diagnostic_source.classify(axis=axis, filler_code=filler),
            atomicity=atomicity_by_filler[filler],
        )
        for axis, filler in keys
    )


async def _routed_selection(
    code: str,
    client: DecompositionSparqlClient,
    roles: list[RoleRestriction],
    morphology_fillers: tuple[str, ...],
    semantic_type_of: Callable[[str], str | None],
    source_identity: str,
    collapse_policy: CollapseVetoPolicy,
    diagnostic_source: axis_diagnostics.AxisDiagnosticSource,
    detector_identity: str,
) -> fs.RoutedSelection:
    routed_plan = fs.build_routed_plan(
        roles,
        semantic_type_of=semantic_type_of,
        parent_morphologies=morphology_fillers,
        concept_code=code,
        source_identity=source_identity,
        collapse_policy=collapse_policy,
    )
    ancestor_pairs = await _specificity_ancestor_pairs(client, routed_plan)
    part_of = await _part_of_pairs(client, routed_plan)
    return await _select_with_r82_evidence(
        client=client,
        routed_plan=routed_plan,
        ancestor_pairs=ancestor_pairs,
        part_of=part_of,
        source_identity=source_identity,
        diagnostic_source=diagnostic_source,
        detector_identity=detector_identity,
    )


async def _specificity_ancestor_pairs(
    client: DecompositionSparqlClient, routed_plan: fs.RoutedPlan
) -> set[extract.AncestorPair]:
    specificity_codes = {
        filler
        for _axis_name, fillers in routed_plan.specificity_groups
        for filler in fillers
    }
    if not specificity_codes:
        return set()
    return set(
        extract.ancestor_pairs_from_rows(
            await client.select(
                stated_queries.build_ancestor_pairs_query(specificity_codes),
                required_variables={"ancestor", "descendant"},
            )
        )
    )


async def _part_of_pairs(
    client: DecompositionSparqlClient, routed_plan: fs.RoutedPlan
) -> set[tuple[str, str]]:
    r82_codes = sorted(
        {
            filler
            for _axis_name, fillers in routed_plan.comparison_groups
            for filler in fillers
        }
    )
    part_of_pairs = await stated_queries.resolve_part_of_pairs(client, r82_codes)
    return {(pair.part, pair.whole) for pair in part_of_pairs}


async def _select_with_r82_evidence(
    *,
    client: DecompositionSparqlClient,
    routed_plan: fs.RoutedPlan,
    ancestor_pairs: set[extract.AncestorPair],
    part_of: set[tuple[str, str]],
    source_identity: str,
    diagnostic_source: axis_diagnostics.AxisDiagnosticSource,
    detector_identity: str,
) -> fs.RoutedSelection:
    assessments = _projection_assessments(
        routed_plan, diagnostic_source, detector_identity
    )
    selected = fs.select_assessed_routed_plan(
        routed_plan,
        extract.make_is_ancestor(ancestor_pairs),
        assessments=assessments,
        is_part_of=lambda part, whole: (part, whole) in part_of,
    )
    required_paths = {
        (item.retained_filler, item.source_filler)
        for item in selected.dispositions
        if item.kind == "collapsed-r82"
    }
    if not required_paths:
        return selected
    path_resolution = await stated_queries.resolve_part_of_paths(
        client,
        required_paths,
        source_identity=source_identity,
    )
    return replace(
        selected,
        dispositions=tuple(
            replace(
                item,
                r82_path=(
                    path_resolution.paths[
                        (item.retained_filler, item.source_filler)
                    ].edges
                    if item.kind == "collapsed-r82"
                    and (item.retained_filler, item.source_filler)
                    in path_resolution.paths
                    else ()
                ),
            )
            for item in selected.dispositions
        ),
    )


async def _decompose_one(
    code: str,
    client: DecompositionSparqlClient,
    *,
    label: str | None,
    label_lookup: LabelLookup,
    source_identity: str,
    collapse_policy: CollapseVetoPolicy,
    diagnostic_source: axis_diagnostics.AxisDiagnosticSource,
    detector_identity: str,
    walker_max_depth: int = 7,
    normalized_group_policy: ActiveNormalizedGroupPolicy | None = None,
) -> _CandidateResult:
    """Detect, extract, and resolve one concept. ``decomposition`` is ``None`` when the
    concept is not a decomposition candidate at all (atomic — never counted as residual,
    only a candidate that yields zero constituents is residual)."""
    # Phase 1: detect (semantic types + genus-chain roles + morphology-from-parent).
    # For primitive concepts (no owl:equivalentClass) the walker returns zero roles,
    # which is correct — nothing to decompose.
    detected = await _detect_candidate_or_unknown(
        code, client, label=label, walker_max_depth=walker_max_depth
    )
    if isinstance(detected, _CandidateResult):
        return detected
    result, roles, morphology_fillers, definition, semantic_types = detected

    # Phase 1a: batch-resolve semantic_type_of for D20 axis routing.
    filler_codes = _candidate_filler_codes(roles, morphology_fillers)
    semantic_type_of = await _filler_semantic_types(client, filler_codes)

    if not result.is_precoordinated:
        return _CandidateResult(
            decomposition=None,
            outcome=_non_candidate_outcome(semantic_types),
            semantic_types=semantic_types,
            r101_conservation=classify_r101_conservation(
                definition=definition,
                constituents=(),
                dispositions=(),
                unchanged_reason="concept-not-decomposed",
            ),
            complete_definition=definition,
        )

    routed_selection = await _routed_selection(
        code,
        client,
        roles,
        morphology_fillers,
        _semantic_type_resolver(semantic_type_of),
        source_identity,
        collapse_policy,
        diagnostic_source,
        detector_identity,
    )
    role_constituents = list(routed_selection.constituents)

    aspects = nlp_fallback.parse_label_aspects(label)
    nlp_constituents, minted = await constituent_index.resolve_aspects(
        aspects, label_lookup
    )
    curated = complete_definition.trace_curated_projection(
        [*role_constituents, *nlp_constituents],
        definition,
    )
    curated = _bind_source_groups(curated, definition)

    decomposition = Decomposition(
        code=code,
        semantic_type=result.semantic_type,
        constituents=curated,
        complete_definition=definition,
        occurrence_dispositions=routed_selection.dispositions,
    )
    decomposition = _apply_group_policy(
        decomposition, normalized_group_policy, source_identity
    )
    return _CandidateResult(
        decomposition=decomposition,
        outcome="decomposed" if decomposition.constituents else "residual",
        semantic_types=semantic_types,
        minted=tuple(minted),
        r101_conservation=classify_r101_conservation(
            definition=definition,
            constituents=tuple(decomposition.constituents),
            dispositions=tuple(decomposition.occurrence_dispositions),
        ),
        complete_definition=definition,
    )


def _bind_source_groups(
    constituents: list[Constituent], definition: CompleteDefinition
) -> list[Constituent]:
    source_groups_by_fact = {fact.fact_id: fact.group_id for fact in definition.facts}
    return [
        replace(
            constituent,
            source_group_ids=tuple(
                sorted(
                    {
                        source_groups_by_fact[source_id]
                        for source_id in constituent.source_definition_ids
                    }
                )
            ),
        )
        for constituent in constituents
    ]


def _apply_group_policy(
    decomposition: Decomposition,
    policy: ActiveNormalizedGroupPolicy | None,
    source_identity: str,
) -> Decomposition:
    active_policy = policy or load_packaged_normalized_group_policy()
    if active_policy.source_identity != source_identity:
        raise SourceIdentityChangedError(
            "normalized group policy source identity differs from run source identity"
        )
    return apply_normalized_group_policy(decomposition, active_policy).decomposition


def _residual_count(
    decompositions: Sequence[Decomposition],
    *,
    precoordinated_fillers: set[str],
) -> int:
    """D37: how many decompositions have >=1 constituent that is itself pre-coordinated.

    Pure: the persisted residual-classification stage supplies the classified set.
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


class _RunSetup:
    def __init__(
        self,
        *,
        run_id: str,
        source_snapshot: NcitSourceSnapshot,
        fingerprint: RunFingerprint,
        collapse_policy: CollapseVetoPolicy,
        pending: list[str],
        labels: dict[str, str],
        diagnostic_source: axis_diagnostics.AxisDiagnosticSource,
        normalized_group_policy: ActiveNormalizedGroupPolicy | None = None,
    ) -> None:
        self.run_id = run_id
        self.source_snapshot = source_snapshot
        self.fingerprint = fingerprint
        self.collapse_policy = collapse_policy
        self.pending = list(pending)
        self.labels = dict(labels)
        self.diagnostic_source = diagnostic_source
        self.normalized_group_policy = normalized_group_policy


@dataclass(frozen=True, slots=True)
class RunProgress:
    """Observable progress over one exact persisted worklist."""

    run_id: str
    phase: Literal["started", "heartbeat", "completed"]
    concept_code: str
    completed: int
    total: int
    session_completed: int
    elapsed_seconds: float


ProgressCallback = Callable[[RunProgress], None]


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


def build_resume_identity(
    config: RunConfig,
    snapshot: NcitSourceSnapshot,
    *,
    semantic_types: tuple[str, ...],
    total_limit: int | None,
    collapse_policy: CollapseVetoPolicy,
) -> RunResumeIdentity:
    sample_identity = (
        config.sample_manifest.identity if config.sample_manifest is not None else None
    )
    return RunResumeIdentity(
        schema_version=5 if sample_identity is not None else 4,
        source_identity=snapshot.source_identity,
        collapse_policy_identity=collapse_policy.policy_identity,
        routing_implementation_identity=routing_implementation_identity(),
        mixed_chain_inventory_identity=(
            (
                _required_mixed_chain_inventory_identity(
                    config,
                    source_identity=snapshot.source_identity,
                    worklist=(),
                )
                if config.mixed_chain_inventory_path is not None
                else None
            )
            or NO_MIXED_CHAIN_INVENTORY_IDENTITY
        ),
        stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
        branch=config.branch.value,
        scope_root=config.scope_root,
        scope_version=config.scope_version,
        semantic_types=semantic_types,
        total_limit=total_limit,
        sample_manifest_identity=sample_identity,
        algorithm_version=config.algorithm_version,
        config_version=_CONFIG_VERSION,
        walker_max_depth=config.walker_max_depth,
        output_mode=_output_mode(config),
        load_mode="named-graph" if config.load_to_store else "none",
    )


def _requested_fingerprint(
    config: RunConfig,
    snapshot: NcitSourceSnapshot,
    *,
    semantic_types: tuple[str, ...],
    total_limit: int | None,
    worklist: tuple[str, ...],
    collapse_policy: CollapseVetoPolicy,
) -> RunFingerprint:
    return RunFingerprint(
        schema_version=5 if config.sample_manifest is not None else 4,
        rehearsal_nonce=uuid4().hex if config.rehearsal else None,
        source_identity=snapshot.source_identity,
        collapse_policy_identity=collapse_policy.policy_identity,
        routing_implementation_identity=routing_implementation_identity(),
        mixed_chain_inventory_identity=(
            _required_mixed_chain_inventory_identity(
                config,
                source_identity=snapshot.source_identity,
                worklist=worklist,
            )
            or NO_MIXED_CHAIN_INVENTORY_IDENTITY
        ),
        stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
        branch=config.branch.value,
        scope_root=config.scope_root,
        scope_version=config.scope_version,
        semantic_types=semantic_types,
        worklist=worklist,
        total_limit=total_limit,
        sample_manifest_identity=(
            config.sample_manifest.identity
            if config.sample_manifest is not None
            else None
        ),
        algorithm_version=config.algorithm_version,
        config_version=_CONFIG_VERSION,
        walker_max_depth=config.walker_max_depth,
        output_mode=_output_mode(config),
        load_mode="named-graph" if config.load_to_store else "none",
        emitted_at=datetime.now(UTC),
    )


async def _standard_worklist(
    config: RunConfig,
    client: DecompositionSparqlClient,
    total_limit: int | None,
) -> list[str]:
    codes = await enumerate_in_scope_codes(client, config.scope_root)
    if not codes:
        raise RuntimeError("scope enumeration returned no concepts")
    if total_limit is not None:
        if total_limit <= 0:
            raise ValueError("total_limit must be greater than zero")
        codes = codes[:total_limit]
    if len(codes) != len(set(codes)):
        raise RuntimeError("scope enumeration returned duplicate concept codes")
    return codes


def _require_sample_source(
    sample: DecompositionSampleManifest,
    snapshot: NcitSourceSnapshot,
) -> None:
    if sample.source_identity != snapshot.source_identity:
        raise SourceIdentityChangedError(
            "sample manifest source identity does not match the revalidated source"
        )
    if sample.ontology_version != snapshot.ontology_version:
        raise SourceIdentityChangedError(
            "sample manifest ontology version does not match the revalidated source"
        )


def _require_sample_scope(
    sample: DecompositionSampleManifest,
    scope_codes: list[str],
) -> None:
    if len(scope_codes) != len(set(scope_codes)):
        raise RuntimeError("scope enumeration returned duplicate concept codes")
    scope_code_set = set(scope_codes)
    outside_scope = tuple(code for code in sample.codes if code not in scope_code_set)
    if outside_scope:
        raise ValueError(
            "sample concepts outside the configured hierarchy scope: "
            + ", ".join(outside_scope)
        )


async def _validated_sample_worklist(
    config: RunConfig,
    client: DecompositionSparqlClient,
    snapshot: NcitSourceSnapshot,
) -> tuple[str, ...] | None:
    """Validate a review manifest against the complete branch scope, and (except for
    a rehearsal) against the live source."""
    sample = config.sample_manifest
    if sample is None:
        return None
    # A rehearsal borrows only the cohort's codes; their live-scope check below is
    # what matters, not the source the sample was reviewed against.
    if not config.rehearsal:
        _require_sample_source(sample, snapshot)
    scope_codes = await enumerate_in_scope_codes(client, config.scope_root)
    _require_sample_scope(sample, scope_codes)
    return sample.codes


async def _load_pending_run_data(
    provenance: ProvenanceStore,
    run_id: str,
    get_labels: GetLabels | None,
) -> tuple[list[str], dict[str, str]]:
    try:
        pending = await provenance.pending_codes(run_id)
        return pending, await _fetch_labels(get_labels, pending)
    except BaseException as exc:
        await _journal_without_masking(
            exc, "run setup failure", _record_setup_failure(provenance, run_id, exc)
        )
        raise


async def _record_setup_failure(
    provenance: ProvenanceStore, run_id: str, exc: BaseException
) -> None:
    if not await provenance.fail_run(run_id, exc):
        exc.add_note(
            f"Run setup failure was NOT recorded: run {run_id!r} holds a "
            "different terminal state, or its row is gone."
        )


async def _prepare_run(
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    get_source_snapshot: GetSourceSnapshot,
    get_labels: GetLabels | None,
    total_limit: int | None,
    snapshot: NcitSourceSnapshot,
    collapse_policy: CollapseVetoPolicy,
    fresh_worklist: tuple[str, ...],
    diagnostic_source: axis_diagnostics.AxisDiagnosticSource,
    normalized_group_policy: ActiveNormalizedGroupPolicy | None = None,
) -> _RunSetup:
    """Admit exactly one source-bound worklist through the shared DB boundary."""
    if config.sample_manifest is not None and total_limit is not None:
        raise ValueError("sample manifest and total_limit are mutually exclusive")
    semantic_types = config.semantic_types
    fingerprint = _requested_fingerprint(
        config,
        snapshot,
        semantic_types=semantic_types,
        total_limit=total_limit,
        worklist=fresh_worklist,
        collapse_policy=collapse_policy,
    )
    await _require_source_snapshot(client, get_source_snapshot, expected=snapshot)
    execution = FullRunExecutionIdentity.from_fingerprint(fingerprint)
    admission = await provenance.admit_run(
        _new_run_id(config.branch),
        snapshot.ontology_version,
        fingerprint,
        execution,
        resume_run_id=config.resume_from,
    )
    if isinstance(admission, Refused):
        raise RunAdmissionRefusedError(
            f"decomposition admission refused: {admission.reason.value}; "
            "inspect existing runs and use --resume with the exact compatible run"
        )
    run_id = admission.run_id
    if not isinstance(admission, FreshAdmitted):
        fingerprint = await provenance.fingerprint_for_run(run_id)
    pending, labels = await _load_pending_run_data(
        provenance,
        run_id,
        get_labels,
    )
    return _RunSetup(
        run_id=run_id,
        source_snapshot=snapshot,
        fingerprint=fingerprint,
        collapse_policy=collapse_policy,
        pending=pending,
        labels=labels,
        diagnostic_source=diagnostic_source,
        normalized_group_policy=normalized_group_policy,
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
            source_identity=setup.source_snapshot.source_identity,
            collapse_policy=setup.collapse_policy,
            diagnostic_source=setup.diagnostic_source,
            detector_identity=setup.fingerprint.routing_implementation_identity,
            walker_max_depth=walker_max_depth,
            normalized_group_policy=setup.normalized_group_policy,
        )
        await provenance.complete_work_item(
            setup.run_id,
            code,
            claim,
            decomposition=result.decomposition,
            outcome=result.outcome,
            semantic_types=result.semantic_types,
            minted=result.minted,
            r101_conservation=result.r101_conservation,
            observed_definition=result.complete_definition,
        )
    except BaseException as exc:
        logger.exception(
            "decomposition failed for concept_code=%s (run_id=%s)",
            code,
            setup.run_id,
        )
        await _journal_without_masking(
            exc,
            "work-item failure",
            provenance.fail_work_item(setup.run_id, code, claim, exc),
        )
        raise


async def _process_pending_work(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    label_lookup: LabelLookup,
    progress: ProgressCallback | None,
) -> None:
    total = len(setup.fingerprint.worklist)
    initially_complete = total - len(setup.pending)
    started_at = time.monotonic()
    for session_index, code in enumerate(setup.pending):
        _report_progress(
            progress,
            setup,
            phase="started",
            code=code,
            completed=initially_complete + session_index,
            session_completed=session_index,
            started_at=started_at,
        )
        task = asyncio.create_task(
            _process_work_item(
                setup,
                code,
                client,
                provenance,
                label_lookup=label_lookup,
                walker_max_depth=config.walker_max_depth,
            )
        )
        while not task.done():
            done, _pending = await asyncio.wait(
                {task}, timeout=_PROGRESS_HEARTBEAT_SECONDS
            )
            if not done:
                _report_progress(
                    progress,
                    setup,
                    phase="heartbeat",
                    code=code,
                    completed=initially_complete + session_index,
                    session_completed=session_index,
                    started_at=started_at,
                )
        await task
        _report_progress(
            progress,
            setup,
            phase="completed",
            code=code,
            completed=initially_complete + session_index + 1,
            session_completed=session_index + 1,
            started_at=started_at,
        )


def _report_progress(
    callback: ProgressCallback | None,
    setup: _RunSetup,
    *,
    phase: Literal["started", "heartbeat", "completed"],
    code: str,
    completed: int,
    session_completed: int,
    started_at: float,
) -> None:
    if callback is None:
        return
    callback(
        RunProgress(
            run_id=setup.run_id,
            phase=phase,
            concept_code=code,
            completed=completed,
            total=len(setup.fingerprint.worklist),
            session_completed=session_completed,
            elapsed_seconds=time.monotonic() - started_at,
        )
    )


def _unsupported_definition_identity(
    filler: str, source_identity: str, detector_identity: str, reason: str
) -> str:
    payload = {
        "filler_code": filler,
        "source_identity": source_identity,
        "detector_identity": detector_identity,
        "classification": "valid-unsupported",
        "reason": reason,
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


async def _base_run_data(
    setup: _RunSetup, provenance: ProvenanceStore
) -> tuple[RunMetrics, list[Decomposition]]:
    decompositions = await provenance.decompositions_for_run(setup.run_id)
    counts = await provenance.outcome_counts(setup.run_id)
    metrics = RunMetrics(
        total_in_scope=counts.total_in_scope,
        decomposed=counts.decomposed,
        residual=counts.residual,
        semantic_excluded=counts.semantic_excluded,
        atomic_noop=counts.atomic_noop,
        unknown_outcome=counts.unknown_outcome,
        minted_count=counts.minted_count,
    )
    metrics.complete_definition_count = sum(
        item.complete_definition is not None for item in decompositions
    )
    metrics.complete_fact_count = sum(
        item.complete_fact_count for item in decompositions
    )
    metrics.projected_fact_count = sum(
        item.projected_fact_count for item in decompositions
    )
    metrics.projection_loss_count = (
        metrics.complete_fact_count - metrics.projected_fact_count
    )
    metrics.projection_loss_rate = (
        metrics.projection_loss_count / metrics.complete_fact_count
        if metrics.complete_fact_count
        else 0.0
    )
    metrics.pct_decomposed = metrics.coverage
    return metrics, decompositions


async def _classify_residual_filler(
    filler: str,
    client: DecompositionSparqlClient,
    *,
    label: str | None,
    walker_max_depth: int,
    source_identity: str,
    detector_identity: str,
) -> tuple[str, str, str | None]:
    try:
        result, _roles, _morphologies, definition, _types = await _detect_concept(
            filler,
            client,
            label=label,
            walker_max_depth=walker_max_depth,
        )
    except complete_definition.UnsupportedDefinitionConstructorError as exc:
        reason = str(exc)
        return (
            "unknown",
            _unsupported_definition_identity(
                filler, source_identity, detector_identity, reason
            ),
            reason,
        )
    return (
        "precoordinated" if result.is_precoordinated else "atomic",
        definition.identity,
        None,
    )


async def _materialize_residual_filler(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    filler: str,
    *,
    label: str | None,
    detector_identity: str,
) -> None:
    claim = await provenance.claim_residual_filler(setup.run_id, filler)
    if claim is None:
        raise RunStateError(f"residual filler {filler!r} could not be claimed")
    try:
        classification, definition_identity, reason = await _classify_residual_filler(
            filler,
            client,
            label=label,
            walker_max_depth=config.walker_max_depth,
            source_identity=setup.fingerprint.source_identity,
            detector_identity=detector_identity,
        )
        await provenance.complete_residual_filler(
            setup.run_id,
            filler,
            claim,
            definition_identity=definition_identity,
            classification=classification,
            unsupported_reason=reason,
        )
    except BaseException as exc:
        await _journal_without_masking(
            exc,
            "residual filler failure",
            provenance.fail_residual_filler(setup.run_id, filler, claim, exc),
        )
        raise


def _classified_residual_sets(
    rows: Sequence[ResidualFillerClassification],
) -> tuple[set[str], set[str], dict[str, str]]:
    precoordinated = {
        row.filler_code for row in rows if row.classification == "precoordinated"
    }
    unknown = {row.filler_code for row in rows if row.classification == "unknown"}
    reasons = {
        row.filler_code: row.unsupported_reason
        for row in rows
        if row.unsupported_reason is not None
    }
    return precoordinated, unknown, reasons


async def _materialize_residual_classifications(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    decompositions: Sequence[Decomposition],
    *,
    get_labels: GetLabels | None,
    progress: Callable[[int, int, str], None] | None,
) -> tuple[set[str], set[str], dict[str, str]]:
    fillers = tuple(_store_resident_constituent_fillers(decompositions))
    detector_identity = setup.fingerprint.routing_implementation_identity
    await provenance.initialize_residual_fillers(
        setup.run_id,
        fillers,
        source_identity=setup.fingerprint.source_identity,
        detector_identity=detector_identity,
    )
    pending = await provenance.pending_residual_fillers(setup.run_id)
    labels = await _fetch_labels(get_labels, pending)
    for index, filler in enumerate(pending):
        if progress is not None:
            progress(index, len(pending), filler)
        await _materialize_residual_filler(
            setup,
            config,
            client,
            provenance,
            filler,
            label=labels.get(filler),
            detector_identity=detector_identity,
        )
        if progress is not None:
            progress(index + 1, len(pending), filler)
    rows = await provenance.residual_filler_classifications(setup.run_id)
    if len(rows) != len(fillers):
        raise RunStateError("residual filler classification inventory is incomplete")
    return _classified_residual_sets(rows)


def _publication_paths(config: RunConfig, run_id: str) -> tuple[Path, Path] | None:
    """Pair the unpublished staging path with its destination, or neither.

    One correlated value: a staging path without a destination (or the reverse) is
    not representable, so publication cannot be silently skipped.
    """
    if config.out is None or config.rehearsal:
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


async def _write_staging_artifact(
    publication: tuple[Path, Path] | None,
    decompositions: Sequence[Decomposition],
    setup: _RunSetup,
    provenance: ProvenanceStore,
) -> None:
    if publication is None:
        return
    try:
        publications = await provenance.concept_publications_for_run(setup.run_id)
        await write_ttl(
            decompositions,
            dest=publication[0],
            run_id=setup.run_id,
            emitted_on=setup.fingerprint.emitted_at.date(),
            publications=publications,
        )
    except BaseException as exc:
        _discard_staging(publication[0], exc)
        raise


async def _verify_final_source_snapshot(
    setup: _RunSetup,
    client: DecompositionSparqlClient,
    get_source_snapshot: GetSourceSnapshot,
    publication: tuple[Path, Path] | None,
) -> None:
    try:
        await _require_source_snapshot(
            client,
            get_source_snapshot,
            expected=setup.source_snapshot,
        )
    except BaseException as exc:
        if publication is not None:
            _discard_staging(publication[0], exc)
        raise


def _persisted_metrics(metrics: RunMetrics) -> dict[str, object]:
    return {
        "total_in_scope": metrics.total_in_scope,
        "decomposed": metrics.decomposed,
        "residual": metrics.residual,
        "semantic_excluded": metrics.semantic_excluded,
        "atomic_noop": metrics.atomic_noop,
        "unknown_outcome": metrics.unknown_outcome,
        "residual_precoordinated_count": metrics.residual_precoordinated_count,
        "residual_precoordination_unknown_count": (
            metrics.residual_precoordination_unknown_count
        ),
        "minted_count": metrics.minted_count,
        "complete_definition_count": metrics.complete_definition_count,
        "complete_fact_count": metrics.complete_fact_count,
        "projected_fact_count": metrics.projected_fact_count,
        "projection_loss_count": metrics.projection_loss_count,
        "projection_loss_rate": metrics.projection_loss_rate,
        "pct_decomposed": metrics.pct_decomposed,
        "roundtrip_fidelity": metrics.roundtrip_fidelity,
        "residual_precoordination": metrics.residual_precoordination,
    }


async def _publish_or_complete_run(
    *,
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    decompositions: Sequence[Decomposition],
    metrics: dict[str, object],
    publication: tuple[Path, Path] | None,
) -> None:
    if publication is not None:
        try:
            await publish_artifact(
                run_id=setup.run_id,
                source_identity=setup.fingerprint.source_identity,
                artifact=publication[0],
                destination=publication[1],
                expected_codes=set(setup.fingerprint.worklist),
                metrics=metrics,
                load_to_store=config.load_to_store,
                client=client,
                provenance=provenance,
            )
        except Exception as publish_error:
            if isinstance(
                publish_error,
                PublicationPreflightError | PublicationFinalizationError,
            ):
                raise
            raise RunPublicationError(
                f"Run {setup.run_id!r} publication failed and remains retryable"
            ) from publish_error
        return
    finished = await provenance.finish_run(
        setup.run_id,
        source_identity=setup.fingerprint.source_identity,
        metrics=metrics,
    )
    if not finished:
        raise RuntimeError(
            f"finish_run found no decomp_run row for run_id={setup.run_id!r} "
            f"(branch={config.branch!r})"
        )


async def _completed_stage_output(
    provenance: ProvenanceStore, run_id: str, stage: str
) -> tuple[str, dict[str, object]]:
    rows = await provenance.run_stages(run_id)
    row = next(item for item in rows if item.stage == stage)
    if (
        row.state != "complete"
        or row.output_identity is None
        or row.output_payload is None
    ):
        raise RunStateError(f"stage {stage!r} is not complete")
    return row.output_identity, row.output_payload


async def _journal_without_masking(
    exc: BaseException, what: str, record: Awaitable[object]
) -> None:
    """Await a provenance write made on behalf of ``exc`` without letting the
    write's own failure replace ``exc``; that failure becomes a note instead.

    A cancellation that interrupts the write still cancels: it gains a note naming
    the interrupted record and ``exc``, and propagates with ``exc`` as its cause so
    neither is lost. The write itself is not retried or shielded (contrast
    ``publication._record_failure_without_masking``), so it may never have landed.
    D90 in ``docs/DECISIONS.md`` says which abandoned writes a later resume recovers
    and which leave only the cancellation's note and its cause, unless a later failure
    record for the same cancellation lands and persists them.
    """
    try:
        await record
    except asyncio.CancelledError as cancellation:
        cancellation.add_note(
            f"Cancelled while recording the {what}: {type(exc).__name__}: {exc}"
        )
        raise cancellation from exc
    except BaseException as failure_error:
        exc.add_note(
            f"Recording the {what} also failed: "
            f"{type(failure_error).__name__}: {failure_error}"
        )


async def _record_stage_failure(
    provenance: ProvenanceStore,
    run_id: str,
    stage: RunStageName,
    claim: UUID,
    exc: BaseException,
) -> None:
    await _journal_without_masking(
        exc, f"{stage} stage failure", provenance.fail_stage(run_id, stage, claim, exc)
    )


async def _preflight_stage(
    setup: _RunSetup,
    config: RunConfig,
    provenance: ProvenanceStore,
    preflight: SourcePreflightResult,
) -> str:
    """Seal ``preflight`` (computed before admission) as the run's preflight stage. If
    an earlier attempt already sealed the stage, use that stored result instead and
    ignore ``preflight``. Either result must still pass the preflight gate and, when
    the config names a mixed-chain inventory, match that inventory's identity."""
    input_identity = setup.fingerprint.identity
    claim = await provenance.claim_stage(setup.run_id, "preflight", input_identity)
    if claim is None:
        output_identity, payload = await _completed_stage_output(
            provenance, setup.run_id, "preflight"
        )
        result = SourcePreflightResult.model_validate_json(json.dumps(payload))
    else:
        try:
            result = preflight
            payload = result.model_dump(mode="json", exclude_computed_fields=True)
            output_identity = await provenance.complete_stage(
                setup.run_id, "preflight", claim, payload
            )
        except BaseException as exc:
            await _record_stage_failure(
                provenance, setup.run_id, "preflight", claim, exc
            )
            raise
    _require_preflight_allowed(result)
    required_inventory = _required_mixed_chain_inventory_identity(
        config,
        source_identity=setup.fingerprint.source_identity,
        worklist=setup.fingerprint.worklist,
    )
    if (
        required_inventory is not None
        and result.mixed_chain_inventory_identity != required_inventory
    ):
        raise SourcePreflightRejectedError(
            "source preflight rejected stale mixed-chain inventory"
        )
    return output_identity


async def _source_preflight_result(
    config: RunConfig,
    client: DecompositionSparqlClient,
    worklist: tuple[str, ...],
    *,
    source_identity: str,
    routing_identity: str,
) -> SourcePreflightResult:
    async def read_definition(code: str) -> CompleteDefinition:
        return await complete_definition.read_complete_definition(
            client.select,
            code,
        )

    inventory_identity = _required_mixed_chain_inventory_identity(
        config,
        source_identity=source_identity,
        worklist=worklist,
    )
    kwargs = (
        {"mixed_chain_inventory_identity": inventory_identity}
        if inventory_identity is not None
        else {}
    )
    try:
        return await run_source_preflight(
            worklist,
            read_definition=read_definition,
            source_identity=source_identity,
            reader_identity=routing_identity,
            query_identity=routing_identity,
            tool_identity=await client.version() or "missing-version",
            walker_max_depth=config.walker_max_depth,
            max_nodes=_SOURCE_PREFLIGHT_MAX_CLOSURE_NODES,
            **kwargs,
        )
    except ClosureBudgetExceededError as exc:
        exc.add_note(
            "The budget is _SOURCE_PREFLIGHT_MAX_CLOSURE_NODES in "
            "ontolib/src/ontolib/decomposition/run.py: raise the budget or narrow "
            "the worklist."
        )
        raise


def _required_mixed_chain_inventory_identity(
    config: RunConfig,
    *,
    source_identity: str,
    worklist: tuple[str, ...],
) -> str | None:
    if config.mixed_chain_inventory_path is None:
        return None
    try:
        inventory = load_mixed_chain_inventory(config.mixed_chain_inventory_path)
        require_mixed_chain_preflight(
            inventory,
            source_identity=source_identity,
            worklist_identity=mixed_chain_worklist_identity(worklist),
            worklist_count=len(worklist),
        )
    except (OSError, ValueError) as exc:
        raise SourcePreflightRejectedError(
            f"source preflight rejected mixed-chain inventory: {exc}"
        ) from exc
    return inventory.identity


def _require_preflight_allowed(result: SourcePreflightResult) -> None:
    if not result.concept_work_allowed:
        raise SourcePreflightRejectedError(
            "source preflight rejected malformed="
            f"{','.join(result.malformed_codes)} overflow="
            f"{','.join(result.overflow_codes)}"
        )


async def _concept_workset_stage(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    label_lookup: LabelLookup,
    progress: ProgressCallback | None,
    input_identity: str,
) -> str:
    claim = await provenance.claim_stage(
        setup.run_id, "concept-workset", input_identity
    )
    if claim is None:
        output_identity, _payload = await _completed_stage_output(
            provenance, setup.run_id, "concept-workset"
        )
        return output_identity
    try:
        await _process_pending_work(
            setup, config, client, provenance, label_lookup, progress
        )
        pending = await provenance.pending_codes(setup.run_id)
        if pending:
            raise RunStateError("concept stage completed with non-complete work items")
        return await provenance.complete_stage(
            setup.run_id,
            "concept-workset",
            claim,
            {
                "run_fingerprint_identity": setup.fingerprint.identity,
                "complete_count": len(setup.fingerprint.worklist),
            },
        )
    except BaseException as exc:
        await _record_stage_failure(
            provenance, setup.run_id, "concept-workset", claim, exc
        )
        raise


async def _residual_classification_stage(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    concept_identity: str,
    get_labels: GetLabels | None,
    residual_progress: Callable[[int, int, str], None] | None,
) -> tuple[RunMetrics, list[Decomposition], str, set[str]]:
    residual_claim = await provenance.claim_stage(
        setup.run_id, "residual-classification", concept_identity
    )
    try:
        metrics, decompositions = await _base_run_data(setup, provenance)
        precoordinated, unknown, unknown_reasons = await _residual_sets(
            setup,
            config,
            client,
            provenance,
            decompositions,
            claim_new=residual_claim is not None,
            get_labels=get_labels,
            residual_progress=residual_progress,
        )
        metrics.residual_precoordinated_count = _residual_count(
            decompositions, precoordinated_fillers=precoordinated
        )
        metrics.residual_precoordination_unknown_count = _residual_count(
            decompositions, precoordinated_fillers=unknown
        )
        residual_payload: dict[str, object] = {
            "residual_precoordinated_count": metrics.residual_precoordinated_count,
            "residual_precoordination_unknown_count": (
                metrics.residual_precoordination_unknown_count
            ),
            "precoordinated_filler_codes": sorted(precoordinated),
            "unknown_filler_codes": sorted(unknown),
            "unknown_reasons": unknown_reasons,
        }
        residual_identity = await _seal_residual_stage(
            setup, provenance, residual_claim, residual_payload
        )
    except BaseException as exc:
        if residual_claim is not None:
            await _record_stage_failure(
                provenance, setup.run_id, "residual-classification", residual_claim, exc
            )
        raise
    return metrics, decompositions, residual_identity, unknown


async def _residual_sets(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    decompositions: Sequence[Decomposition],
    *,
    claim_new: bool,
    get_labels: GetLabels | None,
    residual_progress: Callable[[int, int, str], None] | None,
) -> tuple[set[str], set[str], dict[str, str]]:
    if claim_new:
        return await _materialize_residual_classifications(
            setup,
            config,
            client,
            provenance,
            decompositions,
            get_labels=get_labels,
            progress=residual_progress,
        )
    rows = await provenance.residual_filler_classifications(setup.run_id)
    return _classified_residual_sets(rows)


async def _seal_residual_stage(
    setup: _RunSetup,
    provenance: ProvenanceStore,
    claim: UUID | None,
    payload: dict[str, object],
) -> str:
    if claim is not None:
        return await provenance.complete_stage(
            setup.run_id, "residual-classification", claim, payload
        )
    identity, persisted_payload = await _completed_stage_output(
        provenance, setup.run_id, "residual-classification"
    )
    if persisted_payload != payload:
        raise RunStateError(
            "persisted residual stage differs from filler classifications"
        )
    return identity


async def _metrics_stage(
    setup: _RunSetup,
    provenance: ProvenanceStore,
    metrics: RunMetrics,
    residual_identity: str,
    residual_unknown_codes: set[str],
) -> tuple[str, dict[str, object]]:
    metrics_claim = await provenance.claim_stage(
        setup.run_id, "metrics", residual_identity
    )
    try:
        completion_metrics = CompletionRunMetrics.model_validate(
            _persisted_metrics(metrics)
        )
        concept_unknown_codes = await provenance.unknown_outcome_codes(setup.run_id)
        if len(concept_unknown_codes) != completion_metrics.unknown_outcome:
            raise RunStateError("unknown outcome codes do not match completion metrics")
        # Before the artifact and publication stages: finish_run repeats these checks,
        # but only after the public graph has been replaced.
        completion_ready = await provenance.require_completion_preconditions(
            setup.run_id, setup.fingerprint.source_identity
        )
        if not completion_ready:
            raise RunStateError(
                f"completion preflight found no decomp_run row for "
                f"run_id={setup.run_id!r}"
            )
        await provenance.require_completion_recount(setup.run_id, completion_metrics)
        persisted_metrics = completion_metrics.model_dump(mode="json")
        metrics_payload: dict[str, object] = {
            "metrics": persisted_metrics,
            "unknown_policy": "allow-enumerated-valid-unsupported",
            "concept_unknown_codes": list(concept_unknown_codes),
            "residual_unknown_filler_codes": sorted(residual_unknown_codes),
            "publication_eligible": True,
        }
        if metrics_claim is not None:
            metrics_identity = await provenance.complete_stage(
                setup.run_id, "metrics", metrics_claim, metrics_payload
            )
        else:
            metrics_identity, sealed_metrics = await _completed_stage_output(
                provenance, setup.run_id, "metrics"
            )
            if sealed_metrics != metrics_payload:
                raise RunStateError(
                    "persisted metrics stage differs from persisted run outputs"
                )
    except BaseException as exc:
        if metrics_claim is not None:
            await _record_stage_failure(
                provenance, setup.run_id, "metrics", metrics_claim, exc
            )
        raise
    return metrics_identity, persisted_metrics


async def _artifact_stage(
    setup: _RunSetup,
    config: RunConfig,
    provenance: ProvenanceStore,
    decompositions: list[Decomposition],
    metrics_identity: str,
) -> tuple[str, tuple[Path, Path] | None]:
    publication = _publication_paths(config, setup.run_id)
    artifact_claim = await provenance.claim_stage(
        setup.run_id, "artifact", metrics_identity
    )
    if artifact_claim is not None:
        try:
            await _write_staging_artifact(
                publication, decompositions, setup, provenance
            )
            artifact_payload = _artifact_payload(publication)
            artifact_identity = await provenance.complete_stage(
                setup.run_id, "artifact", artifact_claim, artifact_payload
            )
        except BaseException as exc:
            await _record_stage_failure(
                provenance, setup.run_id, "artifact", artifact_claim, exc
            )
            raise
    else:
        artifact_identity, artifact_payload = await _completed_stage_output(
            provenance, setup.run_id, "artifact"
        )
        _require_sealed_artifact(publication, artifact_payload)
    return artifact_identity, publication


def _artifact_payload(publication: tuple[Path, Path] | None) -> dict[str, object]:
    if publication is None:
        return {"output_mode": "none"}
    return {
        "output_mode": "file",
        "staging_path": str(publication[0]),
        "sha256": hashlib.sha256(publication[0].read_bytes()).hexdigest(),
    }


def _require_sealed_artifact(
    publication: tuple[Path, Path] | None, payload: dict[str, object]
) -> None:
    if publication is None:
        return
    candidate = publication[0] if publication[0].exists() else publication[1]
    if not candidate.exists():
        raise RunPublicationError("sealed artifact is missing or changed")
    if hashlib.sha256(candidate.read_bytes()).hexdigest() != payload.get("sha256"):
        raise RunPublicationError("sealed artifact is missing or changed")


async def _publication_stage(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    artifact_identity: str,
    publication: tuple[Path, Path] | None,
    decompositions: list[Decomposition],
    persisted_metrics: dict[str, object],
    get_source_snapshot: GetSourceSnapshot,
) -> None:
    publication_claim = await provenance.claim_stage(
        setup.run_id, "publication", artifact_identity
    )
    if publication_claim is None:
        return
    try:
        await _verify_final_source_snapshot(
            setup, client, get_source_snapshot, publication
        )
        await _publish_or_complete_run(
            setup=setup,
            config=config,
            client=client,
            provenance=provenance,
            decompositions=decompositions,
            metrics=persisted_metrics,
            publication=publication,
        )
        await provenance.complete_stage(
            setup.run_id,
            "publication",
            publication_claim,
            {"publication_state": "published" if publication else "not_requested"},
        )
    except PublicationFinalizationError as exc:
        # Raised after the run finished and published; the stage completed too.
        await _journal_without_masking(
            exc,
            "publication stage completion",
            provenance.complete_stage(
                setup.run_id,
                "publication",
                publication_claim,
                {"publication_state": "published"},
            ),
        )
        raise
    except BaseException as exc:
        await _record_stage_failure(
            provenance, setup.run_id, "publication", publication_claim, exc
        )
        raise


async def _checkpointed_finish_run(
    setup: _RunSetup,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    concept_identity: str,
    get_source_snapshot: GetSourceSnapshot,
    get_labels: GetLabels | None,
    residual_progress: Callable[[int, int, str], None] | None,
) -> RunMetrics:
    (
        metrics,
        decompositions,
        residual_identity,
        residual_unknown_codes,
    ) = await _residual_classification_stage(
        setup,
        config,
        client,
        provenance,
        concept_identity=concept_identity,
        get_labels=get_labels,
        residual_progress=residual_progress,
    )
    metrics_identity, persisted_metrics = await _metrics_stage(
        setup,
        provenance,
        metrics,
        residual_identity,
        residual_unknown_codes,
    )
    artifact_identity, publication = await _artifact_stage(
        setup, config, provenance, decompositions, metrics_identity
    )
    await _publication_stage(
        setup,
        config,
        client,
        provenance,
        artifact_identity=artifact_identity,
        publication=publication,
        decompositions=decompositions,
        persisted_metrics=persisted_metrics,
        get_source_snapshot=get_source_snapshot,
    )
    return metrics


def _validate_run_request(config: RunConfig, total_limit: int | None) -> None:
    if config.load_to_store and total_limit is not None:
        raise ValueError("total_limit cannot be combined with load_to_store")
    if config.sample_manifest is not None and total_limit is not None:
        raise ValueError("sample manifest and total_limit are mutually exclusive")


async def _qualify_collapse_policy(
    policy: CollapseVetoPolicy,
    client: DecompositionSparqlClient,
    *,
    source_identity: str,
    walker_max_depth: int,
) -> None:
    """Qualify each distinct policy concept once, before this invocation writes any run
    state."""
    occurrences = []
    for concept_code in sorted({entry.concept_code for entry in policy.entries}):
        _result, _roles, _morphology, definition, _types = await _detect_concept(
            concept_code,
            client,
            label=None,
            walker_max_depth=walker_max_depth,
        )
        occurrences.extend(definition.occurrences)
    policy.qualify_live_occurrences(occurrences, source_identity=source_identity)


async def _active_collapse_policy(
    requested: CollapseVetoPolicy | None,
    client: DecompositionSparqlClient,
    snapshot: NcitSourceSnapshot,
    walker_max_depth: int,
) -> CollapseVetoPolicy:
    policy = requested or load_packaged_collapse_veto_policy()
    await _qualify_collapse_policy(
        policy,
        client,
        source_identity=snapshot.source_identity,
        walker_max_depth=walker_max_depth,
    )
    return policy


async def _policy_codes_still_to_do(
    policy: ActiveNormalizedGroupPolicy,
    config: RunConfig,
    provenance: ProvenanceStore,
    worklist: tuple[str, ...],
) -> list[str]:
    """The worklist's policy-bound concepts; on a resume only the unfinished ones, since
    a finished concept's result is persisted and must not block the rest of the run."""
    by_code = policy.by_code
    bound = [code for code in worklist if code in by_code]
    if not bound or config.resume_from is None:
        return bound
    pending = set(await provenance.pending_codes(config.resume_from))
    return [code for code in bound if code in pending]


async def _qualify_group_policy(
    policy: ActiveNormalizedGroupPolicy,
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    worklist: tuple[str, ...],
    source_identity: str,
    collapse_policy: CollapseVetoPolicy,
    diagnostic_source: axis_diagnostics.AxisDiagnosticSource,
    get_labels: GetLabels | None,
    label_lookup: LabelLookup,
) -> None:
    """Decompose each policy-bound concept the run still has to do, persisting nothing,
    before this invocation writes any run state: a decomposition its policy row rejects
    fails here, not when the worklist reaches the concept."""
    bound = await _policy_codes_still_to_do(policy, config, provenance, worklist)
    labels = await _fetch_labels(get_labels, bound)
    untouched = (
        "no run state was written"
        if config.resume_from is None
        else f"run {config.resume_from!r} was not modified"
    )
    for code in bound:
        try:
            await _decompose_one(
                code,
                client,
                label=labels.get(code),
                label_lookup=label_lookup,
                source_identity=source_identity,
                collapse_policy=collapse_policy,
                diagnostic_source=diagnostic_source,
                detector_identity=routing_implementation_identity(),
                walker_max_depth=config.walker_max_depth,
                normalized_group_policy=policy,
            )
        except BaseException as exc:
            exc.add_note(
                f"Raised by the group-policy dry run of {code!r}, before admission: "
                f"{untouched}."
            )
            raise


async def _fresh_preflight(
    config: RunConfig,
    client: DecompositionSparqlClient,
    snapshot: NcitSourceSnapshot,
    total_limit: int | None,
) -> tuple[tuple[str, ...], SourcePreflightResult]:
    sample_worklist = await _validated_sample_worklist(config, client, snapshot)
    worklist = (
        tuple(await _standard_worklist(config, client, total_limit))
        if sample_worklist is None
        else sample_worklist
    )
    routing_identity = routing_implementation_identity()
    result = await _source_preflight_result(
        config,
        client,
        worklist,
        source_identity=snapshot.source_identity,
        routing_identity=routing_identity,
    )
    _require_preflight_allowed(result)
    return worklist, result


async def _resume_preflight(
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    snapshot: NcitSourceSnapshot,
    run_id: str,
) -> tuple[tuple[str, ...], SourcePreflightResult]:
    persisted = await provenance.fingerprint_for_run(run_id)
    if persisted.rehearsal_nonce is not None:
        raise RunStateError(
            f"decomposition run {run_id!r} is a rehearsal; rehearsals "
            "are throwaway runs and cannot be resumed"
        )
    sample_worklist = await _validated_sample_worklist(config, client, snapshot)
    if sample_worklist is not None and sample_worklist != persisted.worklist:
        raise SourcePreflightRejectedError(
            "sample manifest worklist does not match the persisted run"
        )
    result = await _source_preflight_result(
        config,
        client,
        persisted.worklist,
        source_identity=snapshot.source_identity,
        routing_identity=routing_implementation_identity(),
    )
    _require_preflight_allowed(result)
    return persisted.worklist, result


async def _record_pipeline_failure(
    provenance: ProvenanceStore, setup: _RunSetup, exc: BaseException
) -> None:
    if isinstance(exc, SourceIdentityChangedError):
        advice = (
            "Inspect the run's decomp_constituent, decomp_minted_proposal, "
            "decomp_definition_* and decomp_work_item rows before reuse."
        )
        try:
            recorded = await provenance.invalidate_run(setup.run_id, exc)
        except BaseException:
            exc.add_note(
                f"Invalidating run {setup.run_id!r} did not complete; partial "
                f"results may survive. {advice}"
            )
            raise
        message = (
            "Partial results were NOT discarded: run "
            f"{setup.run_id!r} was no longer 'running'. {advice}"
        )
    else:
        recorded = await provenance.fail_run(setup.run_id, exc)
        message = (
            f"Run failure was NOT recorded: run {setup.run_id!r} holds "
            "a different terminal state, or its row is gone."
        )
    if not recorded:
        exc.add_note(message)


async def run_pipeline(
    config: RunConfig,
    client: DecompositionSparqlClient,
    provenance: ProvenanceStore,
    *,
    get_source_snapshot: GetSourceSnapshot,
    get_labels: GetLabels | None = None,
    label_lookup: LabelLookup = _never_resolves,
    total_limit: int | None = None,
    progress: ProgressCallback | None = None,
    residual_progress: Callable[[int, int, str], None] | None = None,
    collapse_policy: CollapseVetoPolicy | None = None,
    normalized_group_policy: ActiveNormalizedGroupPolicy | None = None,
) -> RunMetrics:
    """Execute the decomposition pipeline for a given branch (design §9).

    ``get_labels`` batch-resolves code -> preferred label for the NLP fallback and the
    detector's label signal; when omitted, every concept is decomposed roles-only (no
    NLP fallback is attempted for any concept). ``label_lookup`` resolves an NLP
    surface form to an existing concept code; the default never resolves (always
    mints) — the conservative choice per design §7.2. ``total_limit`` caps how many
    enumerated codes are processed — a full in-scope enumeration is tens of thousands
    of concepts (assessment §3.3); use this for a manual/smoke run. A truncated run
    cannot publish to the configured graph.
    """
    _validate_run_request(config, total_limit)
    snapshot = await _require_source_snapshot(client, get_source_snapshot)
    active_collapse_policy = await _active_collapse_policy(
        collapse_policy, client, snapshot, config.walker_max_depth
    )
    active_group_policy = (
        normalized_group_policy or load_packaged_normalized_group_policy()
    )
    if active_group_policy.source_identity != snapshot.source_identity:
        raise SourceIdentityChangedError(
            "normalized group policy source identity differs from run source identity"
        )
    if config.resume_from is None:
        fresh_worklist, fresh_preflight = await _fresh_preflight(
            config, client, snapshot, total_limit
        )
    else:
        fresh_worklist, fresh_preflight = await _resume_preflight(
            config, client, provenance, snapshot, config.resume_from
        )
    diagnostic_source = await axis_diagnostics.read_axis_diagnostic_source(
        client, snapshot.source_identity
    )
    await _qualify_group_policy(
        active_group_policy,
        config,
        client,
        provenance,
        worklist=fresh_worklist,
        source_identity=snapshot.source_identity,
        collapse_policy=active_collapse_policy,
        diagnostic_source=diagnostic_source,
        get_labels=get_labels,
        label_lookup=label_lookup,
    )
    setup = await _prepare_run(
        config,
        client,
        provenance,
        get_source_snapshot=get_source_snapshot,
        get_labels=get_labels,
        total_limit=total_limit,
        snapshot=snapshot,
        collapse_policy=active_collapse_policy,
        fresh_worklist=fresh_worklist,
        diagnostic_source=diagnostic_source,
        normalized_group_policy=active_group_policy,
    )

    try:
        preflight_identity = await _preflight_stage(
            setup, config, provenance, fresh_preflight
        )
        concept_identity = await _concept_workset_stage(
            setup,
            config,
            client,
            provenance,
            label_lookup,
            progress,
            preflight_identity,
        )
        return await _checkpointed_finish_run(
            setup,
            config,
            client,
            provenance,
            concept_identity=concept_identity,
            get_source_snapshot=get_source_snapshot,
            get_labels=get_labels,
            residual_progress=residual_progress,
        )
    except RunPublicationError, PublicationFinalizationError:
        # Retryable failures are already journaled; finalization failures occur after
        # completion. Neither may demote the decomposition run via fail_run.
        raise
    except BaseException as exc:
        await _journal_without_masking(
            exc, "run failure", _record_pipeline_failure(provenance, setup, exc)
        )
        raise
