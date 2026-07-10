"""Unit tests for the decomposition run orchestrator (design §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.models import Decomposition
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.run import (
    RunConfig,
    RunMetrics,
    _CandidateResult,
    _persist_candidate,
    enumerate_in_scope_codes,
    run_pipeline,
)
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from pathlib import Path


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

    The walker queries (``build_genus_walk_members_query``) return per-concept
    genus-walk rows: for each code the fake produces one hop-0 genus member
    (the code itself as a named class, making it a defined class) and one
    hop-1 restriction per role defined in ``self._genus_walk[code]``. This
    makes the walker behave equivalently to the old flat query while testing
    the walker's actual query-splitting, parsing, and frontier logic.
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
        part_of_rows: list[dict[str, str | None]] | None = None,
    ) -> None:
        self._version = version
        self._pages = pages if pages is not None else [[]]
        self._semantic_types = semantic_types or {}
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
        self._part_of_rows = part_of_rows or []
        self.queries: list[str] = []

    async def version(self) -> str | None:
        return self._version

    @staticmethod
    def _code_in(query: str) -> str | None:
        for part in query.split("Thesaurus.owl#"):
            token = part.split(">")[0]
            if token and token[0] == "C":
                return token
        return None

    async def select(self, query: str) -> list[dict[str, str | None]]:
        self.queries.append(query)
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
        if "rdf:first ?member" in query:
            code = self._code_in(query)
            return self._genus_walk.get(code or "", [])
        if "BIND(REPLACE(STR(?concept)" in query:
            return self._semantic_type_of_rows
        if "R82>" in query:
            return self._part_of_rows
        if "P106" in query and "VALUES" not in query:
            # Single-code semantic type query (build_semantic_type_query)
            code = self._code_in(query)
            types = self._semantic_types.get(code or "", [])
            return [{"semanticType": t} for t in types]
        raise AssertionError(f"unexpected query: {query}")


def _mock_provenance() -> Any:
    store = MagicMock(spec=ProvenanceStore)
    store.upsert_run = AsyncMock(return_value=1)
    store.upsert_constituents = AsyncMock(return_value=0)
    store.upsert_minted_concept = AsyncMock(return_value=1)
    store.finish_run = AsyncMock(return_value=True)
    store.processed_codes = AsyncMock(return_value=set())
    store.run_version = AsyncMock(return_value=None)
    return store


@pytest.mark.unit
async def test_enumerate_in_scope_codes_pages_until_short_page() -> None:
    client = _FakeClient(pages=[["C1", "C2"]])
    codes = await enumerate_in_scope_codes(
        client, ["Neoplastic Process"], page_size=500
    )
    assert codes == ["C1", "C2"]


@pytest.mark.unit
async def test_enumerate_in_scope_codes_follows_full_pages() -> None:
    full_page = [f"C{i}" for i in range(500)]
    client = _FakeClient(pages=[full_page, ["C500"]])
    codes = await enumerate_in_scope_codes(
        client, ["Neoplastic Process"], page_size=500
    )
    assert len(codes) == 501
    assert codes[-1] == "C500"


@pytest.mark.unit
async def test_run_pipeline_skeleton_returns_metrics() -> None:
    client = _FakeClient(pages=[[]])
    provenance = _mock_provenance()
    config = RunConfig(branch="neoplasm")
    metrics = await run_pipeline(config, client, provenance)
    assert isinstance(metrics, RunMetrics)
    assert metrics.coverage == 0.0


@pytest.mark.unit
async def test_run_pipeline_warns_and_records_unknown_when_version_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _FakeClient(pages=[[]], version=None)
    provenance = _mock_provenance()
    await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    provenance.upsert_run.assert_called_once_with(
        provenance.upsert_run.call_args.args[0], "neoplasm", "unknown"
    )
    assert any("owl:versionInfo" in record.message for record in caplog.records)


@pytest.mark.unit
async def test_run_pipeline_atomic_concept_is_not_decomposed() -> None:
    # In-scope but only one role -> below min_decomposable_axes, atomic.
    client = _FakeClient(
        pages=[["C12400"]],
        semantic_types={"C12400": ["Neoplastic Process"]},
        roles={"C12400": []},
    )
    provenance = _mock_provenance()
    config = RunConfig(branch="neoplasm")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 1
    assert metrics.decomposed == 0
    provenance.upsert_constituents.assert_not_called()


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
    provenance.upsert_run.assert_called_once()
    provenance.upsert_constituents.assert_called_once()
    provenance.finish_run.assert_called_once()
    # dataclasses.asdict() doesn't serialize @property fields — pct_decomposed is a
    # plain field precisely so it survives into the persisted metrics jsonb payload.
    persisted_metrics = provenance.finish_run.call_args.kwargs["metrics"]
    assert persisted_metrics["pct_decomposed"] == 1.0
    assert persisted_metrics["decomposed"] == 1


@pytest.mark.unit
async def test_run_pipeline_semantic_type_of_routes_d19_d20_axis() -> None:
    """R101 fillers with semantic type "Body Part, Organ, or Organ Component"
    stay on R101 (D20 — recognised organ site). Fillers without a recognised
    semantic type route to op:AssociatedRegion (D19 — ambiguous body region)."""
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
            {"code": "C12400", "st": "Body Part, Organ, or Organ Component"},
        ],
    )
    provenance = _mock_provenance()
    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    assert metrics.decomposed == 1
    constituents = provenance.upsert_constituents.call_args.args[2]
    region_fillers = {
        c.filler_code for c in constituents if c.axis == "op:AssociatedRegion"
    }
    assert region_fillers == {"C13063"}
    site_fillers = {c.filler_code for c in constituents if c.axis == "R101"}
    assert site_fillers == {"C12400"}


@pytest.mark.unit
async def test_run_pipeline_raises_if_finish_run_finds_no_manifest_row() -> None:
    # finish_run returning False means the manifest row it expected to update
    # doesn't exist (e.g. run_id mismatch, concurrent delete) — must not be silently
    # ignored, or the run looks "successful" while decomp_run.status never becomes
    # 'complete'.
    client = _FakeClient(pages=[[]])
    provenance = _mock_provenance()
    provenance.finish_run = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match="finish_run"):
        await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)


@pytest.mark.unit
async def test_run_pipeline_propagates_per_concept_failures() -> None:
    class _FailingClient(_FakeClient):
        async def select(self, query: str) -> list[dict[str, str | None]]:
            if "P106" in query and "ORDER BY" not in query:
                raise RuntimeError("simulated SPARQL failure")
            return await super().select(query)

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
    constituents = provenance.upsert_constituents.call_args.args[2]
    site_fillers = {c.filler_code for c in constituents if c.axis == "R101"}
    assert site_fillers == {"C12401"}  # the ancestor C12400 was dropped


@pytest.mark.unit
async def test_run_pipeline_part_of_pairs_collapse_broader_filler() -> None:
    """R101 filler C13063 is part-of C12400 (container), so C12400 should be
    collapsed as the broader concept, leaving only C13063 (the part)."""
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
        part_of_rows=[
            {"whole": _iri("C13063"), "part": _iri("C12400")},
        ],
    )
    provenance = _mock_provenance()
    metrics = await run_pipeline(RunConfig(branch="neoplasm"), client, provenance)
    assert metrics.decomposed == 1
    constituents = provenance.upsert_constituents.call_args.args[2]
    site_fillers = {c.filler_code for c in constituents if c.axis == "R101"}
    assert site_fillers == {"C13063"}  # C12400 collapsed as broader container


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
    provenance.processed_codes = AsyncMock(return_value={"C1"})
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 2
    assert metrics.decomposed == 1
    provenance.upsert_constituents.assert_called_once()
    assert provenance.upsert_constituents.call_args.args[1] == "C6135"


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
    provenance.upsert_minted_concept.assert_called_once()


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
    provenance.upsert_minted_concept.assert_not_called()


@pytest.mark.unit
async def test_run_pipeline_resume_skips_already_processed_codes() -> None:
    client = _FakeClient(pages=[["C1", "C2"]])
    provenance = _mock_provenance()
    provenance.processed_codes = AsyncMock(return_value={"C1"})
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    metrics = await run_pipeline(config, client, provenance)
    # Only C2 is newly processed; C1 is skipped. Neither is in scope here (no roles),
    # so this exercises the skip path rather than the extraction path.
    assert metrics.total_in_scope == 2
    provenance.processed_codes.assert_called_once_with("neoplasm-run-1")
    provenance.upsert_run.assert_called_once_with(
        "neoplasm-run-1", "neoplasm", "26.02d"
    )


@pytest.mark.unit
async def test_run_pipeline_resume_with_matching_version_proceeds() -> None:
    client = _FakeClient(pages=[[]], version="26.02d")
    provenance = _mock_provenance()
    provenance.run_version = AsyncMock(return_value="26.02d")
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 0
    provenance.run_version.assert_called_once_with("neoplasm-run-1")


@pytest.mark.unit
async def test_run_pipeline_resume_with_no_prior_manifest_proceeds() -> None:
    # run_version returns None when the resumed id has no stored manifest yet (e.g. a
    # fresh id reused as --resume by mistake) — nothing to compare against, so this is
    # not a mismatch.
    client = _FakeClient(pages=[[]], version="26.02d")
    provenance = _mock_provenance()
    provenance.run_version = AsyncMock(return_value=None)
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    metrics = await run_pipeline(config, client, provenance)
    assert metrics.total_in_scope == 0


@pytest.mark.unit
async def test_run_pipeline_resume_with_version_mismatch_raises() -> None:
    client = _FakeClient(pages=[[]], version="26.05d")
    provenance = _mock_provenance()
    provenance.run_version = AsyncMock(return_value="26.02d")
    config = RunConfig(branch="neoplasm", resume_from="neoplasm-run-1")
    with pytest.raises(RuntimeError, match="version"):
        await run_pipeline(config, client, provenance)
    provenance.processed_codes.assert_not_called()  # refused before any work


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
    client = _FakeClient(pages=[[]])
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
    assert cfg.out is None
    assert not cfg.load_to_store


@pytest.mark.unit
def test_candidate_result_rejects_minted_without_a_decomposition() -> None:
    with pytest.raises(ValueError, match="minted"):
        _CandidateResult(
            decomposition=None,
            minted=[MintedConcept(axis="op:Laterality", label="Left")],
        )


@pytest.mark.unit
def test_candidate_result_allows_none_with_no_minted() -> None:
    result = _CandidateResult(decomposition=None)
    assert result.decomposition is None
    assert result.minted == []


@pytest.mark.unit
async def test_persist_candidate_empty_constituents_increments_residual() -> None:
    decomposition = Decomposition(
        code="C1", semantic_type="Neoplastic Process", constituents=[]
    )
    metrics = RunMetrics()
    decompositions: list[Decomposition] = []
    provenance = _mock_provenance()
    await _persist_candidate(
        "run-1", "C1", decomposition, [], provenance, metrics, decompositions
    )
    assert metrics.residual == 1
    assert metrics.decomposed == 0
    assert decompositions == []
    provenance.upsert_constituents.assert_not_called()
