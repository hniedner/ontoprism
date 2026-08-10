"""Unit tests for the decomposition run orchestrator (design §9)."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from ontolib.decomposition import axes
from ontolib.decomposition import run as run_module
from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.decomposition.provenance import ProvenanceStore, RunStateError
from ontolib.decomposition.provenance_models import (
    NcitSourceSnapshot,
    RunFingerprint,
    RunOutcomeCounts,
    RunSummary,
)
from ontolib.decomposition.publication import PublicationPreflightError
from ontolib.decomposition.run import (
    RunConfig,
    RunMetrics,
    RunPublicationError,
    SourceIdentityChangedError,
    _CandidateResult,
    _new_run_id,
    _precoordinated_fillers,
    _residual_count,
    _store_resident_constituent_fillers,
    enumerate_in_scope_codes,
)
from ontolib.decomposition.run import (
    run_pipeline as _run_pipeline_impl,
)
from ontolib.decomposition.sampling import (
    REQUIRED_SAMPLE_STRATA,
    DecompositionSampleManifest,
    SampleConcept,
)
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import Collection


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


def _role(rel: str, label: str, target: str) -> dict[str, str | None]:
    return {"rel": _iri(rel), "relLabel": label, "target": _iri(target)}


def _old_role_to_genus_walk_row(
    role_row: dict[str, str | None],
) -> dict[str, str | None]:
    """Convert an old flat role row (``?rel``/``?relLabel``/``?target``) into a
    genus-walk hop-1 row (``?member`` bnode with ``?role``/``?roleLabel``/
    ``?target``)."""
    return {
        "member": "_:b",
        "type": "http://www.w3.org/2002/07/owl#Restriction",
        "role": role_row.get("rel"),
        "roleLabel": role_row.get("relLabel"),
        "target": role_row.get("target"),
    }


class _FakeClient:
    """Branches on query-text markers, matching the repo's fake-client convention
    (see ``test_oxigraph_client_http.py``).

    The complete-definition query returns one canonical root group per concept and
    preserves every configured role. The older direct member rows remain only for the
    separate morphology resolver, whose contract intentionally follows named genera.
    """

    def __init__(
        self,
        *,
        version: str | None = "26.02d",
        pages: list[list[str]] | None = None,
        semantic_types: dict[str, list[str]] | None = None,
        roles: dict[str, list[dict[str, str | None]]] | None = None,
        ancestors: list[dict[str, str | None]] | None = None,
        semantic_type_of_rows: list[dict[str, str | None]] | None = None,
        part_of_expansions: dict[str, list[tuple[str, str]]] | None = None,
        label_rows: list[dict[str, str | None]] | None = None,
    ) -> None:
        self._version = version
        self._pages = pages if pages is not None else [[]]
        self._semantic_types = semantic_types or {}
        self._label_rows = label_rows or []
        self._role_labels: dict[str, str] = {}
        self._complete_rows: dict[str, list[dict[str, str | None]]] = {}
        for code, role_rows in (roles or {}).items():
            for role_row in role_rows:
                role_iri = role_row["rel"]
                role_label = role_row.get("relLabel")
                if role_iri is not None and role_label is not None:
                    self._role_labels[role_iri.removeprefix(NCIT_NS)] = role_label
            self._complete_rows[code] = [
                {
                    "expression": f"_:complete-{code}",
                    "parentExpression": None,
                    "nestingDepth": "0",
                    "position": str(position),
                    "member": f"_:complete-{code}-r{position}",
                    "role": role_row["rel"],
                    "target": role_row["target"],
                    "childExpression": None,
                    "nestedExpression": None,
                    "overflow": "false",
                }
                for position, role_row in enumerate(role_rows)
            ]
        # Convert old role format to genus-walk rows
        self._genus_walk: dict[str, list[dict[str, str | None]]] = {}
        for code, role_rows in (roles or {}).items():
            rows: list[dict[str, str | None]] = [
                # hop 0: genus (the code itself — synthetic, makes it a
                # defined class so the walker recurses)
                {"member": _iri(code), "isDefined": "true"},
            ]
            for r in role_rows:
                rows.append(_old_role_to_genus_walk_row(r))
            self._genus_walk[code] = rows

        self._ancestors = ancestors or []
        self._semantic_type_of_rows = semantic_type_of_rows or []
        self._part_of_expansions = part_of_expansions or {}
        self.queries: list[str] = []
        self.required_variables: list[frozenset[str]] = []
        self.query_requirements: list[tuple[str, frozenset[str]]] = []
        self.single_attempt_queries: list[str] = []

    async def version(self) -> str | None:
        return self._version

    @staticmethod
    def _code_in(query: str) -> str | None:
        for part in query.split("Thesaurus.owl#"):
            token = part.split(">")[0]
            if token and token[0] == "C":
                return token
        return None

    async def select(  # noqa: C901, PLR0911 — query-routing test helper
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        required = frozenset(required_variables)
        self.required_variables.append(required)
        self.query_requirements.append((query, required))
        self.queries.append(query)
        if "?overflowChild" in query:
            return []
        if "SELECT DISTINCT ?child ?parent" in query:
            if "rdfs:subClassOf ?parent" not in query:
                return []
            codes = [code for page in self._pages for code in page]
            return [
                {"child": _iri("C3262"), "parent": _iri("C2991")},
                *({"child": _iri(code), "parent": _iri("C3262")} for code in codes),
            ]
        if "ORDER BY ?concept" in query:
            offset = int(query.split("OFFSET")[1].split(maxsplit=1)[0])
            page_index = offset // 500
            return (
                [{"concept": _iri(c)} for c in self._pages[page_index]]
                if page_index < len(self._pages)
                else []
            )
        if "rdfs:subClassOf+" in query:
            return self._ancestors
        if (
            "SELECT DISTINCT ?expression ?parentExpression" in query
            and "?requestedNestingDepth" in query
        ):
            code = self._code_in(query)
            return self._complete_rows.get(code or "", [])
        if "SELECT ?role ?roleLabel" in query:
            return [
                {"role": _iri(role_code), "roleLabel": label}
                for role_code, label in self._role_labels.items()
                if f"#{role_code}>" in query
            ]
        if "rdf:first ?member" in query:
            code = self._code_in(query)
            return self._genus_walk.get(code or "", [])
        if "BIND(REPLACE(STR(?concept)" in query:
            return self._semantic_type_of_rows
        if "SELECT DISTINCT ?node ?kind ?target" in query:
            codes = tuple(
                dict.fromkeys(re.findall(r"BIND\(<[^>]+#(C[0-9]+)> AS \?node\)", query))
            )
            return [
                {
                    "node": _iri(code),
                    "kind": kind,
                    "target": _iri(target),
                    "targetType": "iri",
                }
                for code in codes
                for kind, target in self._part_of_expansions.get(code, ())
            ]
        if "rdfs:label" in query and "GRAPH" in query:
            return self._label_rows
        if "P106" in query and "VALUES" not in query:
            # Single-code semantic type query (build_semantic_type_query)
            code = self._code_in(query)
            types = self._semantic_types.get(code or "", [])
            return [{"semanticType": t} for t in types]
        raise AssertionError(f"unexpected query: {query}")

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        self.single_attempt_queries.append(query)
        return await self.select(query, required_variables=required_variables)


def _mark_run_row_missing(store: Any, state: dict[str, Any]) -> None:
    """Model `finish_run` finding no row: the run id no longer exists.

    The store returns ``False`` only in that case, so `fail_run` must then also
    report that nothing was recorded.
    """

    async def finish_run(*_args: Any, **_kwargs: Any) -> bool:
        state["status"] = "missing"
        return False

    store.finish_run = AsyncMock(side_effect=finish_run)


def _install_lifecycle_doubles(store: Any, state: dict[str, Any]) -> None:
    """Give the run-state doubles the store's real contract.

    `fail_run` only demotes a still-running run and reports whether the run *is*
    recorded as failed once it returns; a double that always returned True would
    certify a guarantee the store does not give. `finish_run` here models the
    success case; use `_mark_run_row_missing` for the store's ``False``, and
    override it directly for the `RunStateError`/`RunIdentityMismatchError`
    refusals, which are pinned against real PostgreSQL instead.
    """

    async def finish_run(*_args: Any, **_kwargs: Any) -> bool:
        state["status"] = "complete"
        return True

    async def fail_run(run_id: str, error: BaseException) -> bool:
        if state["status"] == "failed":
            # Already demoted (fail_work_item): the failure *is* recorded.
            state["failed"] = (run_id, type(error).__name__, str(error))
            return True
        if state["status"] != "running":
            # Complete, or the row is gone: nothing was recorded.
            return False
        state["status"] = "failed"
        state["failed"] = (run_id, type(error).__name__, str(error))
        return True

    async def invalidate_run(run_id: str, error: BaseException) -> bool:
        if state["status"] != "running":
            return False
        state["status"] = "failed"
        state["invalidated"] = (run_id, type(error).__name__, str(error))
        return True

    store.finish_run = AsyncMock(side_effect=finish_run)
    store.fail_work_item = AsyncMock()
    store.fail_run = AsyncMock(side_effect=fail_run)
    store.invalidate_run = AsyncMock(side_effect=invalidate_run)


def _install_publication_doubles(store: Any, state: dict[str, Any]) -> None:
    async def get_run(run_id: str) -> RunSummary | None:
        if state["status"] == "missing":
            return None
        fingerprint = state["fingerprint"]
        return RunSummary(
            id=run_id,
            branch="neoplasm",
            status=state["status"],
            ncit_version="26.02d",
            started_at=datetime(2026, 7, 30, tzinfo=UTC),
            source_identity=(
                fingerprint.source_identity if fingerprint is not None else "a" * 64
            ),
            publication_state=state["publication_state"],
            representation_identity=state["representation_identity"],
            publication_artifact_path=state["publication_artifact_path"],
            publication_built_at=state["publication_built_at"],
            publication_predecessor=state["publication_predecessor"],
            publication_predecessor_captured=state["publication_predecessor_captured"],
        )

    async def begin_publication(
        _run_id: str,
        *,
        representation_identity: str,
        artifact_path: str,
        built_at: datetime,
        predecessor: object,
    ) -> None:
        state["publication_state"] = "publishing"
        state["representation_identity"] = representation_identity
        state["publication_artifact_path"] = artifact_path
        state["publication_built_at"] = built_at
        state["publication_predecessor"] = predecessor
        state["publication_predecessor_captured"] = True

    async def record_publication_failure(_run_id: str, error: BaseException) -> None:
        state["publication_state"] = "failed"
        state["publication_failure"] = error

    store.get_run = AsyncMock(side_effect=get_run)
    store.begin_publication = AsyncMock(side_effect=begin_publication)
    store.record_publication_failure = AsyncMock(side_effect=record_publication_failure)


def _mock_provenance() -> Any:
    store = MagicMock(spec=ProvenanceStore)
    state: dict[str, Any] = {
        "fingerprint": None,
        "pending": [],
        "decompositions": [],
        "decomposed": 0,
        "residual": 0,
        "semantic_excluded": 0,
        "atomic_noop": 0,
        "minted": 0,
        "failed": None,
        "invalidated": None,
        "status": "running",
        "publication_state": "pending",
        "publication_built_at": None,
        "representation_identity": None,
        "publication_artifact_path": None,
        "publication_predecessor": None,
        "publication_predecessor_captured": False,
    }

    _install_lifecycle_doubles(store, state)
    _install_publication_doubles(store, state)

    async def create_run(
        run_id: str,
        ncit_version: str,
        fingerprint: RunFingerprint,
    ) -> None:
        state["fingerprint"] = fingerprint
        state["pending"] = list(fingerprint.worklist)
        del run_id, ncit_version

    async def resume_run(
        run_id: str,
        expected: object,
    ) -> RunFingerprint:
        del run_id, expected
        fingerprint = state["fingerprint"]
        if fingerprint is None:
            fingerprint = RunFingerprint(
                source_identity="a" * 64,
                branch="neoplasm",
                scope_root="C3262",
                scope_version="stated-genus-subclass-v1",
                semantic_types=tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES)),
                worklist=(),
                total_limit=None,
                algorithm_version="decomposition-v1",
                config_version="axes-v1",
                walker_max_depth=5,
                output_mode="none",
                load_mode="none",
                emitted_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
            )
            state["fingerprint"] = fingerprint
        return fingerprint

    async def complete_work_item(
        run_id: str,
        code: str,
        claim: UUID,
        *,
        decomposition: Decomposition | None,
        outcome: str,
        semantic_types: tuple[str, ...],
        minted: tuple[MintedConcept, ...],
    ) -> None:
        del claim
        if decomposition is not None:
            if decomposition.constituents:
                state["decomposed"] += 1
                state["decompositions"].append(decomposition)
            else:
                state["residual"] += 1
        elif outcome == "semantic-excluded":
            state["semantic_excluded"] += 1
        elif outcome == "atomic-no-op":
            state["atomic_noop"] += 1
        del run_id, code, semantic_types
        state["minted"] += len(minted)

    async def outcome_counts(_run_id: str) -> RunOutcomeCounts:
        fingerprint = state["fingerprint"]
        return RunOutcomeCounts(
            total_in_scope=len(fingerprint.worklist) if fingerprint else 0,
            decomposed=state["decomposed"],
            residual=state["residual"],
            semantic_excluded=state["semantic_excluded"],
            atomic_noop=state["atomic_noop"],
            minted_count=state["minted"],
        )

    store.create_run = AsyncMock(side_effect=create_run)
    store.resume_run = AsyncMock(side_effect=resume_run)
    store.pending_codes = AsyncMock(side_effect=lambda _run_id: state["pending"])
    store.claim_work_item = AsyncMock(return_value=UUID(int=1))
    store.complete_work_item = AsyncMock(side_effect=complete_work_item)
    store.decompositions_for_run = AsyncMock(
        side_effect=lambda _run_id: state["decompositions"]
    )
    store.outcome_counts = AsyncMock(side_effect=outcome_counts)
    store._test_state = state
    return store


def _source_snapshot(identity: str = "a" * 64) -> NcitSourceSnapshot:
    return NcitSourceSnapshot(
        source_identity=identity,
        ontology_version="26.02d",
    )


def _sample_manifest(
    *codes: str,
    source_identity: str = "a" * 64,
    ontology_version: str = "26.02d",
) -> DecompositionSampleManifest:
    concepts = tuple(
        SampleConcept(
            code=code,
            strata=(
                tuple(sorted(REQUIRED_SAMPLE_STRATA))
                if index == 0
                else ("atomic-no-op",)
            ),
            rationale=f"Review concept {code}.",
        )
        for index, code in enumerate(codes)
    )
    return DecompositionSampleManifest(
        name="ncit-26.02d-review",
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        source_identity=source_identity,
        ontology_version=ontology_version,
        selection_method="explicit-stratified",
        seed=None,
        concepts=concepts,
    )


def _set_resume_worklist(
    provenance: Any,
    *,
    worklist: tuple[str, ...],
    pending: list[str],
) -> None:
    provenance._test_state["fingerprint"] = RunFingerprint(
        source_identity="a" * 64,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        semantic_types=tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES)),
        worklist=worklist,
        total_limit=None,
        algorithm_version="decomposition-v1",
        config_version="axes-v1",
        walker_max_depth=5,
        output_mode="none",
        load_mode="none",
        emitted_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )
    provenance._test_state["pending"] = pending


async def _stable_source_snapshot() -> NcitSourceSnapshot:
    return _source_snapshot()


async def run_pipeline(
    config: RunConfig,
    client: _FakeClient,
    provenance: Any,
    *,
    get_source_snapshot: Any = _stable_source_snapshot,
    **kwargs: Any,
) -> RunMetrics:
    """Keep individual behavior tests concise while production requires a proof."""
    return await _run_pipeline_impl(
        config,
        client,
        provenance,
        get_source_snapshot=get_source_snapshot,
        **kwargs,
    )


@pytest.mark.unit
async def test_enumerate_in_scope_codes_uses_named_hierarchy_closure() -> None:
    client = MagicMock()
    client.select_once = AsyncMock(
        side_effect=[
            [{"child": _iri("C3262"), "parent": _iri("C2991")}],
            [{"child": _iri("C9305"), "parent": _iri("C3262")}],
            [{"child": _iri("C6135"), "parent": _iri("C9305")}],
            [],
            [],
            [],
            [],
            [],
        ]
    )

    codes = await enumerate_in_scope_codes(client, "C3262")

    assert codes == ["C6135", "C9305"]


@pytest.mark.unit
async def test_enumerate_in_scope_codes_aborts_on_missing_hierarchy_binding() -> None:
    client = MagicMock()
    client.select_once = AsyncMock(return_value=[{}])

    with pytest.raises(RuntimeError, match="child"):
        await enumerate_in_scope_codes(client, "C3262")
    assert client.select_once.await_args.kwargs["required_variables"] == {
        "child",
        "parent",
    }


@pytest.mark.unit
def test_run_config_rejects_equivalence_emission() -> None:
    with pytest.raises(ValueError, match="not available"):
        RunConfig(branch="neoplasm", emit_equivalence=True)


@pytest.mark.unit
async def test_run_pipeline_skeleton_returns_metrics() -> None:
    client = _FakeClient(pages=[["C0"]])
    provenance = _mock_provenance()
    config = RunConfig(branch="neoplasm")
    metrics = await run_pipeline(config, client, provenance)
    assert isinstance(metrics, RunMetrics)
    assert metrics.coverage == 0.0
    assert metrics.roundtrip_fidelity is None
    assert (
        provenance.finish_run.await_args.kwargs["metrics"]["roundtrip_fidelity"] is None
    )


@pytest.mark.unit
async def test_run_pipeline_rejects_endpoint_without_proved_version() -> None:
    client = _FakeClient(pages=[[]], version=None)
    provenance = _mock_provenance()
    with pytest.raises(SourceIdentityChangedError, match="version"):
        await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    provenance.create_run.assert_not_awaited()


@pytest.mark.unit
async def test_run_pipeline_atomic_concept_is_not_decomposed() -> None:
    # In-scope but only one role -> below min_decomposable_axes, atomic.
    client = _FakeClient(
        pages=[["C12400"]],
        semantic_types={
            "C12400": [
                "Neoplastic Process",
                "Disease or Syndrome",
                "Neoplastic Process",
            ]
        },
        roles={"C12400": []},
    )
    provenance = _mock_provenance()
    config = RunConfig(branch="neoplasm")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 1
    assert metrics.decomposed == 0
    completed = provenance.complete_work_item.await_args.kwargs
    assert completed["decomposition"] is None
    assert completed["outcome"] == "atomic-no-op"
    assert completed["semantic_types"] == (
        "Disease or Syndrome",
        "Neoplastic Process",
    )
    assert metrics.atomic_noop == 1
    assert metrics.semantic_excluded == 0


@pytest.mark.unit
async def test_run_pipeline_persists_semantic_exclusion_as_a_distinct_outcome() -> None:
    client = _FakeClient(
        pages=[["C162770"]],
        semantic_types={"C162770": ["Finding"]},
        roles={"C162770": []},
    )
    provenance = _mock_provenance()

    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)

    completed = provenance.complete_work_item.await_args.kwargs
    assert completed["decomposition"] is None
    assert completed["outcome"] == "semantic-excluded"
    assert completed["semantic_types"] == ("Finding",)
    assert metrics.total_in_scope == 1
    assert metrics.decomposed == 0


@pytest.mark.unit
async def test_run_pipeline_morphology_counts_as_decomposable_axis() -> None:
    """A single-role concept with a morphology-bearing parent is detected as
    precoordinated because morphology-from-parent supplies the second axis."""
    client = _FakeClient(
        pages=[["C1"]],
        semantic_types={
            "C1": ["Neoplastic Process"],
            "C99": ["Neoplastic Process"],
        },
        roles={
            "C1": [
                _role("R101", "Has_Primary_Site", "C2"),
            ]
        },
        # genus chain: C1's first intersectionOf member is C99 (distinct from
        # C1), with a non-staging label → morphology_filler resolves to C99
        label_rows=[{"label": "Medullary Carcinoma"}],
    )
    # Override genus-walk rows so C1's first member is C99 (its genus),
    # not C1 itself.
    c1_rows = [
        {"member": _iri("C99")},
        _old_role_to_genus_walk_row(_role("R101", "Has_Primary_Site", "C2")),
    ]
    client._genus_walk["C1"] = c1_rows
    provenance = _mock_provenance()
    config = RunConfig(branch="neoplasm")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 1
    assert metrics.decomposed == 1


@pytest.mark.unit
async def test_run_pipeline_decomposes_a_precoordinated_concept() -> None:
    client = _FakeClient(
        pages=[["C6135"]],
        semantic_types={"C6135": ["Neoplastic Process"]},
        roles={
            "C6135": [
                _role("R88", "Has_Stage", "C27970"),
                _role("R101", "Has_Primary_Site", "C12400"),
            ]
        },
    )
    provenance = _mock_provenance()
    config = RunConfig(branch="neoplasm")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 1
    assert metrics.decomposed == 1
    assert metrics.residual == 0
    assert metrics.coverage == 1.0
    assert metrics.complete_definition_count == 1
    assert metrics.complete_fact_count == 2
    assert metrics.projected_fact_count == 2
    assert metrics.projection_loss_count == 0
    provenance.create_run.assert_awaited_once()
    provenance.complete_work_item.assert_awaited_once()
    provenance.finish_run.assert_called_once()
    # dataclasses.asdict() doesn't serialize @property fields — pct_decomposed is a
    # plain field precisely so it survives into the persisted metrics jsonb payload.
    persisted_metrics = provenance.finish_run.call_args.kwargs["metrics"]
    assert persisted_metrics["pct_decomposed"] == 1.0
    assert persisted_metrics["decomposed"] == 1
    assert persisted_metrics["roundtrip_fidelity"] is None
    assert metrics.roundtrip_fidelity is None
    decomposition = provenance.complete_work_item.await_args.kwargs["decomposition"]
    assert decomposition.complete_definition is not None
    assert decomposition.complete_fact_count == 2
    assert all(
        constituent.source_definition_ids for constituent in decomposition.constituents
    )


@pytest.mark.unit
async def test_run_pipeline_semantic_type_of_routes_d19_d20_axis() -> None:
    """R101 organ fillers normalize to ``op:PrimarySite`` (D20).

    Fillers without a recognized organ semantic type route to
    ``op:AssociatedRegion`` (D19).
    """
    client = _FakeClient(
        pages=[["C1"]],
        semantic_types={"C1": ["Neoplastic Process"]},
        roles={
            "C1": [
                _role("R101", "Has_Primary_Site", "C12400"),
                _role("R101", "Has_Primary_Site", "C13063"),
                _role("R88", "Has_Stage", "C27970"),
            ]
        },
        semantic_type_of_rows=[
            {"code": "C12400", "st": "Anatomical Structure"},
            {"code": "C12400", "st": "Body Part, Organ, or Organ Component"},
        ],
    )
    provenance = _mock_provenance()
    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    assert metrics.decomposed == 1
    constituents = provenance.complete_work_item.await_args.kwargs[
        "decomposition"
    ].constituents
    region_fillers = {
        c.filler_code for c in constituents if c.axis == "op:AssociatedRegion"
    }
    assert region_fillers == {"C13063"}
    site_fillers = {c.filler_code for c in constituents if c.axis == "op:PrimarySite"}
    assert site_fillers == {"C12400"}


@pytest.mark.unit
async def test_run_pipeline_raises_if_finish_run_finds_no_manifest_row() -> None:
    # finish_run returning False means the manifest row it expected to update
    # doesn't exist (e.g. run_id mismatch, concurrent delete) — must not be silently
    # ignored, or the run looks "successful" while decomp_run.status never becomes
    # 'complete'.
    client = _FakeClient(pages=[["C0"]])
    provenance = _mock_provenance()
    _mark_run_row_missing(provenance, provenance._test_state)
    with pytest.raises(RuntimeError, match="finish_run"):
        await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)


@pytest.mark.unit
async def test_run_pipeline_propagates_per_concept_failures() -> None:
    class _FailingClient(_FakeClient):
        async def select(
            self,
            query: str,
            *,
            required_variables: Collection[str] = (),
        ) -> list[dict[str, str | None]]:
            if "P106" in query and "ORDER BY" not in query:
                raise RuntimeError("simulated SPARQL failure")
            return await super().select(query, required_variables=required_variables)

    client = _FailingClient(pages=[["C1"]])
    provenance = _mock_provenance()
    with pytest.raises(RuntimeError, match="simulated SPARQL failure"):
        await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    # Not marked complete — the failure must be visible in decomp_run.status.
    provenance.finish_run.assert_not_called()


@pytest.mark.unit
async def test_run_pipeline_most_specific_selection_uses_live_ancestor_pairs() -> None:
    # Exercises the seam _decompose_one wires between the ancestor-pairs SPARQL
    # response and filler_selection: C12400 is a stated ancestor of C12401 on the
    # same axis, so only the leaf (C12401) should survive into the constituents.
    client = _FakeClient(
        pages=[["C1"]],
        semantic_types={"C1": ["Neoplastic Process"]},
        roles={
            "C1": [
                _role("R101", "Has_Primary_Site", "C12400"),
                _role("R101", "Has_Primary_Site", "C12401"),
                _role("R88", "Has_Stage", "C3"),
            ]
        },
        ancestors=[{"ancestor": _iri("C12400"), "descendant": _iri("C12401")}],
    )
    provenance = _mock_provenance()
    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    assert metrics.decomposed == 1
    constituents = provenance.complete_work_item.await_args.kwargs[
        "decomposition"
    ].constituents
    site_fillers = {c.filler_code for c in constituents if c.axis == "op:PrimarySite"}
    assert site_fillers == {"C12401"}  # the ancestor C12400 was dropped


@pytest.mark.unit
async def test_run_pipeline_part_of_closure_collapses_transitive_wholes() -> None:
    client = _FakeClient(
        pages=[["C1"]],
        semantic_types={"C1": ["Neoplastic Process"]},
        roles={
            "C1": [
                _role("R101", "Has_Primary_Site", "C12400"),
                _role("R101", "Has_Primary_Site", "C13063"),
                _role("R101", "Has_Primary_Site", "C12418"),
                _role("R88", "Has_Stage", "C27970"),
                _role("R135", "Disease_Excludes_Primary_Anatomic_Site", "C9000"),
            ]
        },
        part_of_expansions={
            "C12400": [("whole", "C13063")],
            "C13063": [("whole", "C12418")],
            **{f"C{9000 + hop}": [("whole", f"C{9001 + hop}")] for hop in range(9)},
        },
    )
    provenance = _mock_provenance()
    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    assert metrics.decomposed == 1
    constituents = provenance.complete_work_item.await_args.kwargs[
        "decomposition"
    ].constituents
    site_fillers = {c.filler_code for c in constituents if c.axis == "op:PrimarySite"}
    assert site_fillers == {"C12400"}
    assert all(c.filler_code != "C9000" for c in constituents)
    assert all(
        "C9000" not in query and "C27970" not in query
        for query in client.single_attempt_queries
    )


@pytest.mark.unit
async def test_run_pipeline_preserves_cyclic_fillers_for_review() -> None:
    client = _FakeClient(
        pages=[["C1"]],
        semantic_types={"C1": ["Neoplastic Process"]},
        roles={
            "C1": [
                _role("R101", "Has_Primary_Site", "C120"),
                _role("R101", "Has_Primary_Site", "C121"),
                _role("R101", "Has_Primary_Site", "C130"),
                _role("R88", "Has_Stage", "C27970"),
            ]
        },
        part_of_expansions={
            "C120": [("whole", "C121")],
            "C121": [("whole", "C120")],
        },
        semantic_type_of_rows=[
            {"code": code, "st": "Body Part, Organ, or Organ Component"}
            for code in ("C120", "C121", "C130")
        ],
    )
    provenance = _mock_provenance()

    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)

    assert metrics.decomposed == 1
    constituents = provenance.complete_work_item.await_args.kwargs[
        "decomposition"
    ].constituents
    sites = [c for c in constituents if c.axis == "op:PrimarySite"]
    assert {c.filler_code for c in sites} == {"C120", "C121", "C130"}
    assert all(c.needs_review and not c.most_specific for c in sites)


@pytest.mark.unit
async def test_run_pipeline_closure_preserves_cross_batch_pair() -> None:
    site_codes = ["C10000", *(f"C200{i:02d}" for i in range(15)), "C99999"]
    client = _FakeClient(
        pages=[["C1"]],
        semantic_types={"C1": ["Neoplastic Process"]},
        roles={
            "C1": [
                *(_role("R101", "Has_Primary_Site", code) for code in site_codes),
                _role("R88", "Has_Stage", "C27970"),
            ]
        },
        part_of_expansions={
            "C10000": [("whole", "C15000")],
            "C15000": [("whole", "C99999")],
        },
    )
    provenance = _mock_provenance()

    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)

    assert metrics.decomposed == 1

    def requirements_for(query_fragment: str) -> set[frozenset[str]]:
        return {
            required
            for query, required in client.query_requirements
            if query_fragment in query
        }

    assert requirements_for("SELECT DISTINCT ?child ?parent") == {
        frozenset({"child", "parent"})
    }
    assert requirements_for("?overflowChild") == {frozenset({"overflowChild"})}
    assert requirements_for("SELECT ?semanticType") == {frozenset({"semanticType"})}
    assert requirements_for("BIND(REPLACE(STR(?concept)") == {frozenset({"code", "st"})}
    assert requirements_for("rdfs:subClassOf+") == {
        frozenset({"ancestor", "descendant"})
    }
    assert requirements_for("SELECT DISTINCT ?node ?kind ?target") == {
        frozenset({"node", "kind", "target", "targetType"})
    }
    constituents = provenance.complete_work_item.await_args.kwargs[
        "decomposition"
    ].constituents
    fillers = {c.filler_code for c in constituents}
    assert "C10000" in fillers
    assert "C99999" not in fillers
    expansion_queries = [
        query
        for query in client.queries
        if "SELECT DISTINCT ?node ?kind ?target" in query
    ]
    assert len(expansion_queries) > 1
    assert expansion_queries == [
        query
        for query in client.single_attempt_queries
        if "SELECT DISTINCT ?node ?kind ?target" in query
    ]
    assert all(
        len(set(re.findall(r"BIND\(<[^>]+#(C[0-9]+)> AS \?node\)", query))) <= 16
        for query in expansion_queries
    )


@pytest.mark.unit
async def test_run_pipeline_resume_skips_processed_but_still_decomposes_pending() -> (
    None
):
    # Combines the skip-filter with the full decompose-and-persist path: C1 is
    # already processed (skipped), C6135 is new and genuinely decomposes.
    client = _FakeClient(
        pages=[["C1", "C6135"]],
        semantic_types={"C6135": ["Neoplastic Process"]},
        roles={
            "C6135": [
                _role("R88", "Has_Stage", "C27970"),
                _role("R101", "Has_Primary_Site", "C12400"),
            ]
        },
    )
    provenance = _mock_provenance()
    _set_resume_worklist(
        provenance,
        worklist=("C1", "C6135"),
        pending=["C6135"],
    )
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 2
    assert metrics.decomposed == 1
    provenance.complete_work_item.assert_awaited_once()
    assert provenance.complete_work_item.await_args.args[1] == "C6135"


@pytest.mark.unit
async def test_run_pipeline_excludes_role_does_not_count_as_defining() -> None:
    # A single Has_* role plus an Excludes_* role must NOT reach min_decomposable_axes
    # (Excludes_* is a negative axiom, not a constituent — axes.py).
    client = _FakeClient(
        pages=[["C1"]],
        semantic_types={"C1": ["Neoplastic Process"]},
        roles={
            "C1": [
                _role("R101", "Has_Primary_Site", "C2"),
                _role("R135", "Disease_Excludes_Finding", "C3"),
            ]
        },
    )
    provenance = _mock_provenance()
    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    assert metrics.decomposed == 0


@pytest.mark.unit
async def test_run_pipeline_out_of_scope_semantic_type_is_skipped() -> None:
    client = _FakeClient(
        pages=[["C1"]],
        semantic_types={"C1": ["Amino Acid, Peptide, or Protein"]},
        roles={"C1": [{"rel": _iri("R1"), "target": _iri("C2")}]},
    )
    provenance = _mock_provenance()
    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    assert metrics.total_in_scope == 1
    assert metrics.decomposed == 0
    assert metrics.residual == 0  # never a precoordination candidate to begin with
    assert metrics.semantic_excluded == 1
    assert metrics.atomic_noop == 0


@pytest.mark.unit
async def test_run_pipeline_nlp_fallback_mints_when_no_label_lookup_given() -> None:
    # Two roles already make C4791 a candidate on their own; its label additionally
    # carries a laterality aspect ("Left") that only the NLP fallback recovers.
    client = _FakeClient(
        pages=[["C4791"]],
        semantic_types={"C4791": ["Neoplastic Process"]},
        roles={
            "C4791": [
                _role("R101", "Has_Primary_Site", "C2"),
                _role("R88", "Has_Stage", "C3"),
            ]
        },
    )
    provenance = _mock_provenance()

    async def get_labels(codes: list[str]) -> dict[str, str]:
        return {"C4791": "Left Atrial Myxoma"}

    metrics = await run_pipeline(
        RunConfig(branch="neoplasm"), client, provenance, get_labels=get_labels
    )
    assert metrics.decomposed == 1
    # "Left" minted — no label_lookup given, so the default never resolves.
    assert metrics.minted_count == 1
    assert len(provenance.complete_work_item.await_args.kwargs["minted"]) == 1


@pytest.mark.unit
async def test_run_pipeline_nlp_aspect_resolves_via_label_lookup() -> None:
    client = _FakeClient(
        pages=[["C4791"]],
        semantic_types={"C4791": ["Neoplastic Process"]},
        roles={
            "C4791": [
                _role("R101", "Has_Primary_Site", "C2"),
                _role("R88", "Has_Stage", "C3"),
            ]
        },
    )
    provenance = _mock_provenance()

    async def get_labels(codes: list[str]) -> dict[str, str]:
        return {"C4791": "Left Atrial Myxoma"}

    async def label_lookup(term: str) -> str | None:
        return "C99" if term == "Left" else None

    metrics = await run_pipeline(
        RunConfig(branch="neoplasm"),
        client,
        provenance,
        get_labels=get_labels,
        label_lookup=label_lookup,
    )
    assert metrics.decomposed == 1
    assert metrics.minted_count == 0
    assert provenance.complete_work_item.await_args.kwargs["minted"] == ()


@pytest.mark.unit
async def test_run_pipeline_resume_skips_already_processed_codes() -> None:
    client = _FakeClient(pages=[["C1", "C2"]])
    provenance = _mock_provenance()
    _set_resume_worklist(
        provenance,
        worklist=("C1", "C2"),
        pending=["C2"],
    )
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    metrics = await run_pipeline(config, client, provenance)
    # Only C2 is newly processed; C1 is skipped. Neither is in scope here (no roles),
    # so this exercises the skip path rather than the extraction path.
    assert metrics.total_in_scope == 2
    provenance.resume_run.assert_awaited_once()
    provenance.create_run.assert_not_awaited()


@pytest.mark.unit
async def test_run_pipeline_resume_with_matching_version_proceeds() -> None:
    client = _FakeClient(pages=[[]], version="26.02d")
    provenance = _mock_provenance()
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 0
    provenance.resume_run.assert_awaited_once()


@pytest.mark.unit
async def test_run_pipeline_resume_with_no_prior_manifest_is_rejected() -> None:
    client = _FakeClient(pages=[[]], version="26.02d")
    provenance = _mock_provenance()
    provenance.resume_run = AsyncMock(
        side_effect=RunStateError("decomposition run does not exist")
    )
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    with pytest.raises(RunStateError, match="does not exist"):
        await run_pipeline(config, client, provenance)


@pytest.mark.unit
async def test_run_pipeline_resume_with_version_mismatch_raises() -> None:
    client = _FakeClient(pages=[[]], version="26.05d")
    provenance = _mock_provenance()
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    with pytest.raises(SourceIdentityChangedError, match="version"):
        await run_pipeline(config, client, provenance)
    provenance.resume_run.assert_not_awaited()


@pytest.mark.unit
async def test_run_pipeline_writes_ttl_when_out_is_set(tmp_path: Path) -> None:
    client = _FakeClient(
        pages=[["C6135"]],
        semantic_types={"C6135": ["Neoplastic Process"]},
        roles={
            "C6135": [
                {"rel": _iri("R88"), "relLabel": "Has_Stage", "target": _iri("C27970")},
                {
                    "rel": _iri("R101"),
                    "relLabel": "Has_Primary_Site",
                    "target": _iri("C12400"),
                },
            ]
        },
    )
    provenance = _mock_provenance()
    out = tmp_path / "out.ttl"
    config = RunConfig(branch="neoplasm", out=out)
    await run_pipeline(config, client, provenance)
    assert out.exists()
    content = out.read_text()
    assert "C6135" in content


@pytest.mark.unit
async def test_run_pipeline_no_out_does_not_write_a_file(tmp_path: Path) -> None:
    client = _FakeClient(pages=[["C0"]])
    provenance = _mock_provenance()
    await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
async def test_run_pipeline_total_limit_caps_codes_processed() -> None:
    # A full corpus enumeration can be ~26k concepts (assessment); total_limit lets a
    # manual/smoke run cap the work without changing the enumeration query itself.
    client = _FakeClient(pages=[["C1", "C2", "C3"]])
    provenance = _mock_provenance()
    metrics = await run_pipeline(
        RunConfig(branch="neoplasm"), client, provenance, total_limit=2
    )
    assert metrics.total_in_scope == 2


# ── residual_precoordination (D37, #126) ──────────────────────────────


def _decomp(code: str, *filler_codes: str) -> Decomposition:
    return Decomposition(
        code=code,
        semantic_type="Neoplastic Process",
        constituents=[
            Constituent(axis="R101", filler_code=f, axis_source="role")
            for f in filler_codes
        ],
    )


@pytest.mark.unit
def test_residual_count_flags_a_decomposition_whose_constituent_is_precoordinated() -> (
    None
):
    """GATE LIVENESS (D37): the metric must be NON-ZERO on input that should trigger it.

    A concept that decomposed, but one of whose emitted constituents is *itself* a
    pre-coordinated concept, is residually pre-coordinated — decomposition bottomed out
    on a compound. This is the whole point of the metric; if it can only ever read 0 it
    is not a metric (the #73 vacuous-gate lesson).
    """
    decompositions = [
        _decomp("C1", "C9001", "C9002"),  # one constituent is precoordinated
        _decomp("C2", "C9001"),  # fully atomic
    ]
    assert _residual_count(decompositions, precoordinated_fillers={"C9002"}) == 1


@pytest.mark.unit
def test_minted_fillers_are_excluded_and_do_not_change_the_count() -> None:
    """``MINT-*`` fillers never exist in the store — detecting one is wasted round-trips
    that always read atomic — so they are dropped before detection. Dropping them cannot
    change the metric: a minted filler was never in the pre-coordinated set to begin
    with. Locks that value-invariance against a future detector that stops gating on
    in-scope (which would otherwise start classifying MINT- codes).
    """
    d = Decomposition(
        code="C1",
        semantic_type="Neoplastic Process",
        constituents=[
            Constituent(axis="R101", filler_code="C2001", axis_source="role"),
            Constituent(
                axis="op:Laterality", filler_code="MINT-0abc12345def", axis_source="nlp"
            ),
        ],
    )
    # The MINT- filler is dropped before detection, so it can never enter the
    # pre-coordinated set — which is what makes the exclusion value-preserving: the
    # numerator is computed only over the codes that survive here.
    assert _store_resident_constituent_fillers([d]) == ["C2001"]


@pytest.mark.unit
def test_residual_count_is_zero_when_every_constituent_is_atomic() -> None:
    """Reject branch: a decomposition all of whose constituents are atomic is not
    residual — this is the state a fully-reduced ontology should converge toward."""
    decompositions = [_decomp("C1", "C9011", "C9012"), _decomp("C2", "C9013")]
    assert _residual_count(decompositions, precoordinated_fillers=set()) == 0


@pytest.mark.unit
def test_residual_precoordination_is_the_fraction_of_decomposed_concepts() -> None:
    m = RunMetrics(decomposed=4, residual_precoordinated_count=1)
    assert m.residual_precoordination == pytest.approx(0.25)


@pytest.mark.unit
def test_residual_precoordination_is_zero_when_nothing_decomposed() -> None:
    """No division by zero, and 'nothing decomposed' is honestly 0, not undefined."""
    assert RunMetrics().residual_precoordination == 0.0


@pytest.mark.unit
async def test_precoordinated_fillers_detects_a_compound_constituent() -> None:
    """GATE LIVENESS through the REAL detector: a constituent filler that is itself
    in-scope with >=2 defining roles comes back pre-coordinated, while an atomic filler
    does not. This proves the metric can fire end-to-end (detection wiring), not only in
    the pure counting logic — the difference the #73 vacuous-gate history turns on.
    """
    decompositions = [_decomp("C2000", "C2001", "C2002")]
    client = _FakeClient(
        semantic_types={
            "C2001": ["Neoplastic Process"],  # compound: in scope + 2 roles
            "C2002": ["Neoplastic Process"],  # atomic: in scope, no defining roles
        },
        roles={
            "C2001": [
                _role("R101", "Has_Primary_Site", "C3001"),
                _role("R100", "Has_Associated_Site", "C3002"),
            ],
            "C2002": [],
        },
    )

    async def _labels(codes: list[str]) -> dict[str, str]:
        return {c: f"label-{c}" for c in codes}

    precoordinated = await _precoordinated_fillers(
        decompositions, client, _labels, walker_max_depth=5
    )
    assert precoordinated == {"C2001"}
    assert _residual_count(decompositions, precoordinated_fillers=precoordinated) == 1


@pytest.mark.unit
async def test_precoordinated_fillers_reraises_with_context_on_detection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store failure during the metric post-pass must surface LOUDLY, naming the
    filler — never vanish into a quiet 0 (the #73 cardinal sin). The post-pass runs
    outside the main loop's try/except, so it logs its own context then re-raises.
    """

    store_error = RuntimeError("store down")

    class _BoomClient:
        async def select(
            self,
            query: str,
            *,
            required_variables: Collection[str] = (),
        ) -> list[dict[str, str | None]]:
            del query, required_variables
            raise store_error

        async def version(self) -> str | None:
            return "x"

    decompositions = [_decomp("C1", "C9099")]
    log_exception = MagicMock()
    monkeypatch.setattr(run_module.logger, "exception", log_exception)
    with pytest.raises(RuntimeError, match="store down") as raised:
        await _precoordinated_fillers(
            decompositions, _BoomClient(), None, walker_max_depth=5
        )
    assert raised.value is store_error
    log_exception.assert_called_once_with(
        "residual-precoordination detection failed for filler_code=%s", "C9099"
    )


@pytest.mark.unit
async def test_run_pipeline_wires_residual_precoordination_end_to_end() -> None:
    """SEAM: the metric must be set by a real ``run_pipeline`` call, not only by the
    isolated helpers. Deleting the post-pass wiring in ``run_pipeline`` leaves every
    isolated test green — this is the only test that fails when the wiring is dropped.

    C6135 decomposes; its R101 site filler C12400 is itself in-scope with two defining
    roles, so it is pre-coordinated — C6135's decomposition bottomed out on a compound.
    """
    client = _FakeClient(
        pages=[["C6135"]],
        semantic_types={
            "C6135": ["Neoplastic Process"],
            "C12400": ["Neoplastic Process"],  # the constituent filler is in scope…
        },
        roles={
            "C6135": [
                _role("R88", "Has_Stage", "C27970"),
                _role("R101", "Has_Primary_Site", "C12400"),
            ],
            "C12400": [  # …and compound: two defining roles -> pre-coordinated
                _role("R101", "Has_Primary_Site", "C3001"),
                _role("R100", "Has_Associated_Site", "C3002"),
            ],
        },
    )
    provenance = _mock_provenance()
    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)

    assert metrics.decomposed == 1
    assert metrics.residual_precoordinated_count == 1
    assert metrics.residual_precoordination == pytest.approx(1.0)
    # Persist both numerator and derived rate so every read surface has one schema.
    persisted = provenance.finish_run.call_args.kwargs["metrics"]
    assert persisted["residual_precoordinated_count"] == 1
    assert persisted["residual_precoordination"] == pytest.approx(1.0)
    assert set(persisted) == {
        "total_in_scope",
        "decomposed",
        "residual",
        "semantic_excluded",
        "atomic_noop",
        "unknown_outcome",
        "residual_precoordinated_count",
        "residual_precoordination",
        "minted_count",
        "complete_definition_count",
        "complete_fact_count",
        "projected_fact_count",
        "projection_loss_count",
        "projection_loss_rate",
        "pct_decomposed",
        "roundtrip_fidelity",
    }


@pytest.mark.unit
def test_run_metrics_coverage_zero_when_empty() -> None:
    m = RunMetrics()
    assert m.coverage == 0.0


@pytest.mark.unit
def test_run_metrics_coverage_computed_correctly() -> None:
    m = RunMetrics(total_in_scope=100, decomposed=85)
    assert m.coverage == pytest.approx(0.85)


@pytest.mark.unit
def test_run_config_defaults() -> None:
    cfg = RunConfig(branch="neoplasm")
    assert cfg.branch == "neoplasm"
    assert cfg.scope_root == "C3262"
    assert cfg.scope_version == "stated-genus-subclass-v1"
    assert cfg.semantic_types == tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES))
    assert cfg.algorithm == "axis-qualified"
    assert cfg.out is None
    assert not cfg.load_to_store


@pytest.mark.unit
def test_sample_run_config_is_review_only_and_scope_bound(tmp_path: Path) -> None:
    sample = _sample_manifest("C1")

    with pytest.raises(ValueError, match="requires an output path"):
        RunConfig(branch="neoplasm", sample_manifest=sample)
    with pytest.raises(ValueError, match="cannot load"):
        RunConfig(
            branch="neoplasm",
            out=tmp_path / "review.ttl",
            load_to_store=True,
            sample_manifest=sample,
        )
    with pytest.raises(ValueError, match="does not match run branch"):
        RunConfig(
            branch="disease",
            out=tmp_path / "review.ttl",
            sample_manifest=sample,
        )


@pytest.mark.unit
def test_disease_config_uses_the_broader_root_with_the_same_algorithm() -> None:
    neoplasm = RunConfig(branch="neoplasm")
    disease = RunConfig(branch="disease")

    assert disease.scope_root == "C2991"
    assert disease.scope_root != neoplasm.scope_root
    assert disease.algorithm == neoplasm.algorithm
    assert disease.semantic_types == neoplasm.semantic_types


@pytest.mark.unit
def test_candidate_result_rejects_minted_without_a_decomposition() -> None:
    with pytest.raises(ValueError, match="minted"):
        _CandidateResult(
            decomposition=None,
            outcome="atomic-no-op",
            semantic_types=("Neoplastic Process",),
            minted=[MintedConcept(axis="op:Laterality", label="Left")],
        )


@pytest.mark.unit
def test_candidate_result_enforces_outcome_specific_shapes() -> None:
    empty = Decomposition(code="C1", semantic_type="Neoplastic Process")
    populated = _decomp("C1", "C2")
    minted = [MintedConcept(axis="op:Laterality", label="Left")]

    with pytest.raises(ValueError, match=r"decomposed.*constituent"):
        _CandidateResult(empty, "decomposed", ("Neoplastic Process",))
    with pytest.raises(ValueError, match=r"residual.*zero constituents"):
        _CandidateResult(populated, "residual", ("Neoplastic Process",))
    with pytest.raises(ValueError, match=r"minted.*decomposed"):
        _CandidateResult(empty, "residual", ("Neoplastic Process",), minted)
    # A non-decomposition outcome has, by definition, produced no decomposition;
    # carrying one would contradict the outcome the row is about to record.
    for outcome in ("semantic-excluded", "atomic-no-op", "unknown"):
        with pytest.raises(
            ValueError, match="non-decomposition outcomes cannot carry a decomposition"
        ):
            _CandidateResult(populated, cast("Any", outcome), ("Neoplastic Process",))


@pytest.mark.unit
def test_candidate_result_preserves_typed_atomic_no_op() -> None:
    result = _CandidateResult(
        decomposition=None,
        outcome="atomic-no-op",
        semantic_types=("Neoplastic Process",),
    )
    assert result.decomposition is None
    assert result.outcome == "atomic-no-op"
    assert result.semantic_types == ("Neoplastic Process",)
    assert result.minted == []


@pytest.mark.unit
def test_run_ids_are_collision_safe_within_one_clock_tick() -> None:
    first = _new_run_id("neoplasm")
    second = _new_run_id("neoplasm")

    assert first != second
    assert first.startswith("neoplasm-")
    assert second.startswith("neoplasm-")


@pytest.mark.unit
async def test_fresh_run_materializes_zero_output_and_rechecks_source() -> None:
    client = _FakeClient(pages=[["C0"]])
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=["C0"])
    provenance.claim_work_item = AsyncMock(return_value=UUID(int=1))
    provenance.complete_work_item = AsyncMock()
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=1,
            decomposed=0,
            residual=0,
            minted_count=0,
        )
    )
    provenance.fail_run = AsyncMock()
    source = AsyncMock(return_value=_source_snapshot())

    metrics = await run_pipeline(
        RunConfig(branch="neoplasm"),
        client,
        provenance,
        get_source_snapshot=source,
    )

    fingerprint = provenance.create_run.await_args.args[2]
    assert isinstance(fingerprint, RunFingerprint)
    assert fingerprint.worklist == ("C0",)
    assert fingerprint.source_identity == "a" * 64
    assert fingerprint.config_version == "nested-definition-v2"
    assert fingerprint.output_mode == "none"
    assert fingerprint.load_mode == "none"
    provenance.complete_work_item.assert_awaited_once()
    assert provenance.complete_work_item.await_args.kwargs["decomposition"] is None
    assert metrics.total_in_scope == 1
    assert source.await_count == 3
    provenance.fail_run.assert_not_awaited()


@pytest.mark.unit
async def test_resume_uses_persisted_worklist_without_reenumerating_scope() -> None:
    emitted_at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    fingerprint = RunFingerprint(
        source_identity="a" * 64,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        semantic_types=tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES)),
        worklist=("C0", "C1"),
        total_limit=None,
        algorithm_version="decomposition-v1",
        config_version="axes-v1",
        walker_max_depth=5,
        output_mode="none",
        load_mode="none",
        emitted_at=emitted_at,
    )
    client = _FakeClient(pages=[["MUST-NOT-BE-ENUMERATED"]])
    provenance = _mock_provenance()
    provenance.resume_run = AsyncMock(return_value=fingerprint)
    provenance.pending_codes = AsyncMock(return_value=["C1"])
    provenance.claim_work_item = AsyncMock(return_value=UUID(int=2))
    provenance.complete_work_item = AsyncMock()
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=2,
            decomposed=0,
            residual=0,
            minted_count=0,
        )
    )
    provenance.fail_run = AsyncMock()

    await run_pipeline(
        RunConfig(branch="neoplasm", resume_from="run-1"),
        client,
        provenance,
        get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
    )

    assert all("ORDER BY ?concept" not in query for query in client.queries)
    provenance.complete_work_item.assert_awaited_once()
    assert provenance.complete_work_item.await_args.args[1] == "C1"


@pytest.mark.unit
async def test_sample_resume_revalidates_scope_and_manifest_identity(
    tmp_path: Path,
) -> None:
    sample = _sample_manifest("C2", "C1")
    fingerprint = RunFingerprint(
        schema_version=3,
        source_identity="a" * 64,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        semantic_types=tuple(sorted(axes.IN_SCOPE_SEMANTIC_TYPES)),
        worklist=sample.codes,
        total_limit=None,
        sample_manifest_identity=sample.identity,
        algorithm_version="axis-qualified-v1",
        config_version="nested-definition-v2",
        walker_max_depth=5,
        output_mode="file",
        load_mode="none",
        emitted_at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )
    client = _FakeClient(pages=[["C1", "C2", "C3"]])
    provenance = _mock_provenance()
    provenance._test_state["fingerprint"] = fingerprint
    provenance._test_state["pending"] = ["C1"]
    provenance._test_state["semantic_excluded"] = 1
    provenance.resume_run = AsyncMock(return_value=fingerprint)

    metrics = await run_pipeline(
        RunConfig(
            branch="neoplasm",
            out=tmp_path / "review.ttl",
            resume_from="review-run-1",
            sample_manifest=sample,
        ),
        client,
        provenance,
    )

    expected = provenance.resume_run.await_args.args[1]
    assert metrics.total_in_scope == 2
    assert expected.schema_version == 3
    assert expected.sample_manifest_identity == sample.identity
    assert expected.config_version == "nested-definition-v2"
    assert any("SELECT DISTINCT ?child ?parent" in query for query in client.queries)
    provenance.create_run.assert_not_awaited()
    assert provenance.complete_work_item.await_args.args[1] == "C1"


@pytest.mark.unit
async def test_source_swap_after_work_leaves_run_failed_and_incomplete() -> None:
    client = _FakeClient(pages=[["C0"]])
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=[])
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=0,
            decomposed=0,
            residual=0,
            minted_count=0,
        )
    )
    source = AsyncMock(
        side_effect=[
            _source_snapshot(),
            _source_snapshot(),
            _source_snapshot("b" * 64),
        ]
    )

    with pytest.raises(SourceIdentityChangedError, match="changed"):
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            client,
            provenance,
            get_source_snapshot=source,
        )

    provenance.invalidate_run.assert_awaited_once()
    assert provenance._test_state["invalidated"][1:] == (
        "SourceIdentityChangedError",
        "NCIt source identity changed during the decomposition run",
    )
    provenance.fail_run.assert_not_awaited()
    provenance.finish_run.assert_not_awaited()


@pytest.mark.unit
async def test_source_swap_at_completion_leaves_no_publishable_artifact(
    tmp_path: Path,
) -> None:
    """Drift must not leave a complete-looking TTL at the operator's --out path.

    The rows are invalidated, so an artifact surviving at ``--out`` would name a run
    that no longer has any constituents and could still be hand-loaded or shipped.
    """
    out = tmp_path / "decomposed.ttl"
    client = _FakeClient(pages=[["C1"]])
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=[])
    provenance.decompositions_for_run = AsyncMock(
        return_value=[
            Decomposition(
                code="C1",
                semantic_type="Neoplastic Process",
                constituents=[
                    Constituent(
                        axis="op:PrimarySite",
                        filler_code="C12345",
                        axis_source="role",
                        source_role="R101",
                    )
                ],
            )
        ]
    )
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=1,
            decomposed=1,
            residual=0,
            minted_count=0,
        )
    )
    source = AsyncMock(
        side_effect=[
            _source_snapshot(),
            _source_snapshot(),
            _source_snapshot("b" * 64),
        ]
    )

    with pytest.raises(SourceIdentityChangedError, match="changed"):
        await run_pipeline(
            RunConfig(branch="neoplasm", out=out),
            client,
            provenance,
            get_source_snapshot=source,
        )

    provenance.invalidate_run.assert_awaited_once()
    provenance.finish_run.assert_not_awaited()
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
async def test_final_status_failure_surfaces_marker_ahead_for_reconciliation(
    tmp_path: Path,
) -> None:
    """External publication may lead the DB; that state must fail visibly."""
    out = tmp_path / "decomposed.ttl"
    client = _FakeClient(pages=[["C1"]])
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=[])
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=0,
            decomposed=0,
            residual=0,
            minted_count=0,
        )
    )
    _mark_run_row_missing(provenance, provenance._test_state)

    with pytest.raises(RunPublicationError, match="remains retryable") as exc_info:
        await run_pipeline(
            RunConfig(branch="neoplasm", out=out),
            client,
            provenance,
            get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
        )

    assert out.exists()
    assert not list(tmp_path.glob("*.staging-*"))
    assert isinstance(exc_info.value.__cause__, RunStateError)
    provenance.record_publication_failure.assert_awaited_once()
    provenance.fail_run.assert_not_awaited()


@pytest.mark.unit
async def test_surviving_partial_results_are_reported_on_the_raised_error() -> None:
    """`invalidate_run` returning False means the drifted rows were NOT discarded.

    Dropping that result would leave the operator with only a drift error and no
    indication that mixed-source constituents are still in PostgreSQL.
    """
    client = _FakeClient(pages=[["C0"]])
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=[])
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=0, decomposed=0, residual=0, minted_count=0
        )
    )
    provenance.invalidate_run = AsyncMock(return_value=False)
    source = AsyncMock(
        side_effect=[
            _source_snapshot(),
            _source_snapshot(),
            _source_snapshot("b" * 64),
        ]
    )

    with pytest.raises(SourceIdentityChangedError) as exc_info:
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            client,
            provenance,
            get_source_snapshot=source,
        )

    assert any(
        "Partial results were NOT discarded" in note
        for note in exc_info.value.__notes__
    )


@pytest.mark.unit
async def test_unrecorded_run_failure_is_reported_on_the_raised_error() -> None:
    """A run in some other terminal state means the failure was never recorded."""
    client = _FakeClient(pages=[["C1"]])
    provenance = _mock_provenance()
    provenance.fail_run = AsyncMock(return_value=False)

    async def fail_labels(_codes: list[str]) -> dict[str, str]:
        raise RuntimeError("label store unavailable")

    with pytest.raises(RuntimeError, match="label store unavailable") as exc_info:
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            client,
            provenance,
            get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
            get_labels=fail_labels,
        )

    assert any("was NOT recorded" in note for note in exc_info.value.__notes__)


@pytest.mark.unit
async def test_non_positive_total_limit_is_rejected_before_a_run_exists() -> None:
    """``total_limit=0`` would materialize a run that instantly "completes" over zero
    concepts and report 0% coverage as a real result."""
    client = _FakeClient(pages=[["C1"]])
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()

    with pytest.raises(ValueError, match="total_limit must be greater than zero"):
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            client,
            provenance,
            get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
            total_limit=0,
        )

    provenance.create_run.assert_not_awaited()


@pytest.mark.unit
async def test_limited_run_cannot_replace_the_public_graph(tmp_path: Path) -> None:
    provenance = _mock_provenance()
    source = AsyncMock(side_effect=AssertionError("source was inspected"))

    with pytest.raises(ValueError, match=r"total_limit.*load_to_store"):
        await run_pipeline(
            RunConfig(
                branch="neoplasm",
                out=tmp_path / "partial.ttl",
                load_to_store=True,
            ),
            _FakeClient(pages=[["C1", "C2"]]),
            provenance,
            get_source_snapshot=source,
            total_limit=1,
        )

    source.assert_not_awaited()
    provenance.create_run.assert_not_awaited()


@pytest.mark.unit
async def test_empty_standard_scope_is_rejected_before_run_creation() -> None:
    provenance = _mock_provenance()

    with pytest.raises(RuntimeError, match="scope enumeration returned no concepts"):
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            _FakeClient(pages=[[]]),
            provenance,
            get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
        )

    provenance.create_run.assert_not_awaited()


@pytest.mark.unit
async def test_sample_and_total_limit_are_rejected_before_source_or_provenance(
    tmp_path: Path,
) -> None:
    provenance = _mock_provenance()
    source = AsyncMock(side_effect=AssertionError("source was inspected"))

    with pytest.raises(ValueError, match="mutually exclusive"):
        await run_pipeline(
            RunConfig(
                branch="neoplasm",
                out=tmp_path / "review.ttl",
                sample_manifest=_sample_manifest("C1"),
            ),
            _FakeClient(pages=[["C1"]]),
            provenance,
            get_source_snapshot=source,
            total_limit=1,
        )

    source.assert_not_awaited()
    provenance.create_run.assert_not_awaited()
    provenance.resume_run.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sample", "message"),
    [
        (_sample_manifest("C1", source_identity="b" * 64), "source identity"),
        (_sample_manifest("C1", ontology_version="26.05d"), "ontology version"),
    ],
)
async def test_sample_source_drift_is_rejected_before_provenance(
    tmp_path: Path,
    sample: DecompositionSampleManifest,
    message: str,
) -> None:
    provenance = _mock_provenance()

    with pytest.raises(SourceIdentityChangedError, match=message):
        await run_pipeline(
            RunConfig(
                branch="neoplasm",
                out=tmp_path / "review.ttl",
                sample_manifest=sample,
            ),
            _FakeClient(pages=[["C1"]]),
            provenance,
        )

    provenance.create_run.assert_not_awaited()
    provenance.resume_run.assert_not_awaited()


@pytest.mark.unit
async def test_sample_rejects_out_of_scope_code_before_provenance(
    tmp_path: Path,
) -> None:
    provenance = _mock_provenance()

    with pytest.raises(
        ValueError,
        match=r"outside the configured hierarchy scope.*C9",
    ):
        await run_pipeline(
            RunConfig(
                branch="neoplasm",
                out=tmp_path / "review.ttl",
                sample_manifest=_sample_manifest("C1", "C9"),
            ),
            _FakeClient(pages=[["C1"]]),
            provenance,
        )

    provenance.create_run.assert_not_awaited()


@pytest.mark.unit
async def test_sample_order_and_identity_are_persisted_as_exact_worklist(
    tmp_path: Path,
) -> None:
    sample = _sample_manifest("C2", "C1")
    provenance = _mock_provenance()

    metrics = await run_pipeline(
        RunConfig(
            branch="neoplasm",
            out=tmp_path / "review.ttl",
            sample_manifest=sample,
        ),
        _FakeClient(pages=[["C1", "C2", "C3"]]),
        provenance,
    )

    fingerprint = provenance._test_state["fingerprint"]
    assert metrics.total_in_scope == 2
    assert fingerprint.schema_version == 3
    assert fingerprint.worklist == ("C2", "C1")
    assert fingerprint.sample_manifest_identity == sample.identity
    assert fingerprint.total_limit is None


@pytest.mark.unit
async def test_duplicate_hierarchy_edges_materialize_one_work_item() -> None:
    """Polyhierarchy/duplicate source edges cannot duplicate the exact worklist."""
    client = _FakeClient(pages=[["C1", "C1"]])
    provenance = _mock_provenance()

    await run_pipeline(
        RunConfig(branch="neoplasm"),
        client,
        provenance,
        get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
    )

    fingerprint = provenance._test_state["fingerprint"]
    assert fingerprint.worklist == ("C1",)


@pytest.mark.unit
async def test_duplicate_scope_enumeration_is_rejected_before_run_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final materialization guard must remain live even if enumeration regresses.

    The hierarchy walker currently deduplicates edges itself, but the immutable
    worklist must not depend on that collaborator continuing to do so.
    """
    provenance = _mock_provenance()
    monkeypatch.setattr(
        run_module,
        "enumerate_in_scope_codes",
        AsyncMock(return_value=["C1", "C1"]),
    )

    with pytest.raises(RuntimeError, match="duplicate concept codes"):
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            _FakeClient(),
            provenance,
        )

    provenance.create_run.assert_not_awaited()


@pytest.mark.unit
async def test_work_item_failure_recording_error_is_attached_to_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = _mock_provenance()
    provenance.fail_work_item = AsyncMock(
        side_effect=RuntimeError("work-item journal unavailable")
    )

    async def fail_decomposition(*_args: object, **_kwargs: object) -> object:
        raise ValueError("malformed stated definition")

    monkeypatch.setattr(run_module, "_decompose_one", fail_decomposition)

    with pytest.raises(ValueError, match="malformed stated definition") as exc_info:
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            _FakeClient(pages=[["C1"]]),
            provenance,
        )

    assert any(
        "work-item journal unavailable" in note for note in exc_info.value.__notes__
    )
    provenance.fail_run.assert_awaited_once()


@pytest.mark.unit
async def test_serialization_and_run_journal_double_fault_preserves_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "decomposed.ttl"
    provenance = _mock_provenance()
    provenance.fail_run = AsyncMock(side_effect=RuntimeError("run journal unavailable"))

    async def fail_write(
        _decompositions: object,
        dest: Path,
        **_kwargs: object,
    ) -> None:
        dest.write_text("partial artifact", encoding="utf-8")
        raise OSError("serialization interrupted")

    monkeypatch.setattr(run_module, "write_ttl", fail_write)

    with pytest.raises(OSError, match="serialization interrupted") as exc_info:
        await run_pipeline(
            RunConfig(branch="neoplasm", out=out),
            _FakeClient(pages=[["C0"]]),
            provenance,
        )

    assert any("run journal unavailable" in note for note in exc_info.value.__notes__)
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
async def test_staging_cleanup_failure_never_replaces_the_drift_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OSError from cleanup must not turn drift into an ordinary failure.

    If it did, `run_pipeline` would call `fail_run` instead of `invalidate_run` and
    the drifted run's mixed-source rows would survive in PostgreSQL.
    """
    out = tmp_path / "decomposed.ttl"
    client = _FakeClient(pages=[["C0"]])
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=[])
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=0, decomposed=0, residual=0, minted_count=0
        )
    )

    def explode(self: Path, **_kwargs: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "unlink", explode)
    source = AsyncMock(
        side_effect=[
            _source_snapshot(),
            _source_snapshot(),
            _source_snapshot("b" * 64),
        ]
    )

    with pytest.raises(SourceIdentityChangedError) as exc_info:
        await run_pipeline(
            RunConfig(branch="neoplasm", out=out),
            client,
            provenance,
            get_source_snapshot=source,
        )

    provenance.invalidate_run.assert_awaited_once()
    provenance.fail_run.assert_not_awaited()
    assert any("could not be removed" in note for note in exc_info.value.__notes__)


@pytest.mark.unit
async def test_publication_failure_is_recorded_as_retryable_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rename failure remains a publication retry, not a completed run."""
    out = tmp_path / "decomposed.ttl"
    client = _FakeClient(pages=[["C0"]])
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=[])
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=0, decomposed=0, residual=0, minted_count=0
        )
    )

    def explode(_source: object, _target: object) -> None:
        raise OSError("is a directory")

    monkeypatch.setattr(os, "replace", explode)

    with pytest.raises(RunPublicationError, match="remains retryable") as exc_info:
        await run_pipeline(
            RunConfig(branch="neoplasm", out=out),
            client,
            provenance,
            get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
        )

    provenance.finish_run.assert_not_awaited()
    provenance.record_publication_failure.assert_awaited_once()
    provenance.fail_run.assert_not_awaited()
    assert isinstance(exc_info.value.__cause__, OSError)
    assert not getattr(exc_info.value, "__notes__", [])
    # The complete staging artifact survives so a matching resume can reconcile it.
    staging = next(path for path in tmp_path.iterdir() if ".staging-" in path.name)
    assert staging.exists()
    assert not out.exists()


@pytest.mark.unit
async def test_artifact_validation_failure_fails_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "decomposed.ttl"
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=[])
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=0, decomposed=0, residual=0, minted_count=0
        )
    )

    monkeypatch.setattr(
        "ontolib.decomposition.publication._validated_artifact_payload",
        MagicMock(side_effect=PublicationPreflightError("invalid artifact")),
    )

    with pytest.raises(PublicationPreflightError, match="invalid artifact"):
        await run_pipeline(
            RunConfig(branch="neoplasm", out=out),
            _FakeClient(pages=[["C0"]]),
            provenance,
            get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
        )

    provenance.fail_run.assert_awaited_once()
    provenance.record_publication_failure.assert_not_awaited()
    assert not out.exists()


@pytest.mark.unit
async def test_publication_cancellation_is_not_wrapped_as_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "decomposed.ttl"
    provenance = _mock_provenance()
    provenance.create_run = AsyncMock()
    provenance.pending_codes = AsyncMock(return_value=[])
    provenance.decompositions_for_run = AsyncMock(return_value=[])
    provenance.outcome_counts = AsyncMock(
        return_value=RunOutcomeCounts(
            total_in_scope=0, decomposed=0, residual=0, minted_count=0
        )
    )
    monkeypatch.setattr(
        run_module,
        "publish_artifact",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await run_pipeline(
            RunConfig(branch="neoplasm", out=out),
            _FakeClient(pages=[["C0"]]),
            provenance,
            get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
        )

    provenance.fail_run.assert_awaited_once()
    assert not out.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    "branch",
    ["", "regimen", "experimental", "neo/plasm", "neo\\plasm"],
)
def test_run_config_rejects_every_unsupported_branch_before_a_run_exists(
    branch: str,
) -> None:
    """Cosmetic and unimplemented branch labels must not create provenance."""
    with pytest.raises(ValueError, match="unsupported decomposition branch"):
        RunConfig(branch=branch)


@pytest.mark.unit
async def test_a_failed_failure_record_is_reported_on_the_original_error() -> None:
    """A double fault must not lose the identity of the secondary failure.

    The primary error still propagates; without the note the operator would not
    learn that the run was additionally left unmarked in PostgreSQL.
    """
    client = _FakeClient(pages=[["C1"]])
    provenance = _mock_provenance()
    provenance.fail_run = AsyncMock(side_effect=RuntimeError("postgres unreachable"))

    async def fail_labels(_codes: list[str]) -> dict[str, str]:
        raise RuntimeError("label store unavailable")

    with pytest.raises(RuntimeError, match="label store unavailable") as exc_info:
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            client,
            provenance,
            get_source_snapshot=AsyncMock(return_value=_source_snapshot()),
            get_labels=fail_labels,
        )

    assert any("postgres unreachable" in note for note in exc_info.value.__notes__)


@pytest.mark.unit
def test_run_config_rejects_load_without_output_path() -> None:
    """``load_to_store`` without ``out`` would persist "loaded a file never written"."""
    with pytest.raises(ValueError, match="load_to_store requires an output path"):
        RunConfig(branch="neoplasm", load_to_store=True)


@pytest.mark.unit
async def test_label_failure_after_manifest_marks_run_failed() -> None:
    client = _FakeClient(pages=[["C1"]])
    provenance = _mock_provenance()

    async def fail_labels(_codes: list[str]) -> dict[str, str]:
        raise RuntimeError("label store unavailable")

    with pytest.raises(RuntimeError, match="label store unavailable"):
        await run_pipeline(
            RunConfig(branch="neoplasm"),
            client,
            provenance,
            get_labels=fail_labels,
        )

    provenance.fail_run.assert_awaited_once()
    assert provenance._test_state["failed"][1:] == (
        "RuntimeError",
        "label store unavailable",
    )
    provenance.finish_run.assert_not_awaited()


@pytest.mark.unit
async def test_unclaimable_work_item_marks_run_failed() -> None:
    client = _FakeClient(pages=[["C1"]])
    provenance = _mock_provenance()
    provenance.claim_work_item = AsyncMock(return_value=None)

    with pytest.raises(RunStateError, match="could not be claimed"):
        await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)

    provenance.fail_run.assert_awaited_once()
    assert provenance._test_state["failed"][1:] == (
        "RunStateError",
        provenance._test_state["failed"][2],
    )
    assert "could not be claimed" in provenance._test_state["failed"][2]
    provenance.finish_run.assert_not_awaited()
