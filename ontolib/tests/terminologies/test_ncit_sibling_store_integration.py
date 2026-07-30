from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.decompose import _source_snapshot

from ontolib.decomposition import stated_queries as stated_queries_module
from ontolib.decomposition.complete_definition import read_complete_definition
from ontolib.decomposition.models import CompleteDefinition, RestrictionDefinitionFact
from ontolib.decomposition.scope import enumerate_scope_codes
from ontolib.decomposition.stated_queries import (
    build_genus_walk_members_query,
    resolve_part_of_pairs,
    walk_genus_chain,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    REJECTED_CANDIDATE_FILENAME,
    CandidateGraph,
    CandidateObservation,
    CandidateValidationPolicy,
    DockerOxigraphRuntime,
    LoaderIdentity,
    SiblingStoreValidationError,
    build_ncit_sibling_store,
    observe_ncit_candidate,
)
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractContextManager

    from ontolib.decomposition.provenance_models import NcitSourceSnapshot
    from ontolib.terminologies.ncit.owl_download import OwlArtifactPairManifest

_ONTOLOGY_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"
_VERSION = "26.test"
_INFERRED = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="{_ONTOLOGY_IRI}">
    <owl:versionInfo>{_VERSION}</owl:versionInfo>
  </owl:Ontology>
  <owl:Class rdf:about="{NCIT_NS}C6135"/>
</rdf:RDF>
""".encode()
_STATED = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="{_ONTOLOGY_IRI}">
    <owl:versionInfo>{_VERSION}</owl:versionInfo>
  </owl:Ontology>
  <owl:Class rdf:about="{NCIT_NS}C14806">
    <owl:deprecated rdf:datatype="http://www.w3.org/2001/XMLSchema#boolean">true</owl:deprecated>
  </owl:Class>
  <owl:Class rdf:about="{NCIT_NS}C6135">
    <owl:equivalentClass>
      <owl:Class>
        <owl:intersectionOf rdf:parseType="Collection">
          <owl:Class rdf:about="{NCIT_NS}C1"/>
          <owl:Restriction>
            <owl:onProperty rdf:resource="{NCIT_NS}R88"/>
            <owl:someValuesFrom rdf:resource="{NCIT_NS}C27970"/>
          </owl:Restriction>
        </owl:intersectionOf>
      </owl:Class>
    </owl:equivalentClass>
  </owl:Class>
</rdf:RDF>
""".encode()


def _identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


async def _m1_walker_evidence(
    client: OxigraphHttpClient,
) -> tuple[
    list[dict[str, str | None]],
    dict[str, int],
    CompleteDefinition,
    set[str],
    int,
    int,
    int,
    str | None,
]:
    root_rows = await client.select_once(
        build_genus_walk_members_query("C27262")[0],
        required_variables={"member"},
    )
    role_counts: dict[str, int] = {}
    for code in ("C6135", "C27787"):
        role_counts[code] = len(
            await walk_genus_chain(client.select, code, max_depth=5)
        )
    complete = await read_complete_definition(client.select, "C27262")
    nested_rows = await client.select(
        f"""
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX owl: <{OWL_NS}>
        SELECT DISTINCT ?concept WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                ?concept owl:equivalentClass ?expression .
                ?expression owl:intersectionOf/rdf:rest*/rdf:first ?member .
                FILTER(isBlank(?member))
                ?member owl:intersectionOf ?nestedList .
            }}
        }}
        """
    )
    nested_codes = {
        row["concept"].removeprefix(NCIT_NS)
        for row in nested_rows
        if row.get("concept", "").startswith(NCIT_NS)
    }
    closure = stated_queries_module._PartOfClosure(
        select_once=client.select_once,
        requested=("C12917", "C36220", "C37060", "C41063", "C41397"),
    )
    assert await closure.resolve() == []
    assert (
        await resolve_part_of_pairs(
            client,
            ("C12917", "C36220", "C37060", "C41063", "C41397"),
        )
        == []
    )
    return (
        list(root_rows),
        role_counts,
        complete,
        nested_codes,
        closure.request_count,
        closure.total_rows,
        len(closure.expanded_codes),
        await client.version(),
    )


def _assert_m1_scope_and_walker_evidence(
    scope_codes: dict[str, tuple[str, ...]],
    c27262_root_rows: list[dict[str, str | None]],
    canonical_role_counts: dict[str, int],
    c27262_complete: CompleteDefinition,
    nested_definition_codes: set[str],
    r82_resource_evidence: tuple[int, int, int, str | None],
) -> None:
    neoplasms = set(scope_codes["neoplasm"])
    diseases = set(scope_codes["disease"])
    assert len(neoplasms) > 15_000
    assert len(diseases) > 22_000
    assert neoplasms < diseases
    assert {"C3262", "C9305", "C2916", "C6135", "C100012"} <= diseases
    assert {"C9305", "C2916", "C6135"} <= neoplasms
    assert {"C3262", "C100012", "C12400"}.isdisjoint(neoplasms)
    assert "C100012" in diseases - neoplasms
    assert "C12400" not in diseases
    assert c27262_root_rows == [
        {
            "member": f"{NCIT_NS}C35501",
            "type": f"{OWL_NS}Class",
        }
    ]
    assert canonical_role_counts["C6135"] > 0
    assert canonical_role_counts["C27787"] > 0
    assert len(nested_definition_codes) == 97
    assert len(nested_definition_codes & neoplasms) == 91
    assert "C27262" in nested_definition_codes
    assert r82_resource_evidence == (11, 40, 35, "26.07d")
    assert (
        len(
            [group for group in c27262_complete.groups if group.anchor_code == "C27262"]
        )
        == 2
    )
    root_group = next(
        group
        for group in c27262_complete.groups
        if group.group_id in c27262_complete.root_group_ids
        and group.anchor_code == "C27262"
    )
    assert len(root_group.child_group_ids) == 1
    c27262_restrictions = {
        (fact.role_code, fact.filler_code)
        for fact in c27262_complete.facts
        if isinstance(fact, RestrictionDefinitionFact) and fact.anchor_code == "C27262"
    }
    assert {
        ("R140", "C36715"),
        ("R141", "C13271"),
        ("R141", "C28452"),
        ("R139", "C37030"),
        ("R142", "C41235"),
    } <= c27262_restrictions


def _write_pair(root: Path, *, stated_bytes: bytes = _STATED) -> Path:
    pair_dir = root / "pair"
    pair_dir.mkdir()
    records: dict[str, dict[str, object]] = {}
    for variant, owl_bytes in (
        ("inferred", _INFERRED),
        ("stated", stated_bytes),
    ):
        archive = pair_dir / f"{variant}.zip"
        owl = pair_dir / f"{variant}.owl"
        archive.write_bytes(f"{variant}-archive".encode())
        owl.write_bytes(owl_bytes)
        record: dict[str, object] = {
            "variant": variant,
            "source_url": f"https://example.test/{variant}.zip",
            "archive_path": str(archive.resolve()),
            "file_path": str(owl.resolve()),
            "archive_size_bytes": archive.stat().st_size,
            "size_bytes": owl.stat().st_size,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "owl_sha256": hashlib.sha256(owl_bytes).hexdigest(),
            "ontology_version": _VERSION,
            "ontology_iri": _ONTOLOGY_IRI,
        }
        record["artifact_identity"] = _identity(
            {
                "variant": variant,
                "source_url": record["source_url"],
                "archive_sha256": record["archive_sha256"],
                "owl_sha256": record["owl_sha256"],
                "ontology_version": _VERSION,
                "ontology_iri": _ONTOLOGY_IRI,
            }
        )
        records[variant] = record
    manifest = {
        "schema_version": 1,
        "manifest_identity": _identity(
            {
                "schema_version": 1,
                "stated": records["stated"]["artifact_identity"],
                "inferred": records["inferred"]["artifact_identity"],
                "ontology_version": _VERSION,
                "ontology_iri": _ONTOLOGY_IRI,
            }
        ),
        "ontology_version": _VERSION,
        "ontology_iri": _ONTOLOGY_IRI,
        **records,
    }
    path = pair_dir / "ncit-artifact-pair.json"
    path.write_text(json.dumps(manifest))
    return path


class _ObservationDouble:
    def __init__(
        self,
        observation: CandidateObservation,
        loader: LoaderIdentity,
    ) -> None:
        self._observation = observation
        self._loader = loader

    def identify_loader(self) -> LoaderIdentity:
        return self._loader

    def load(
        self,
        pair: OwlArtifactPairManifest,
        candidate_path: Path,
        owner: str,
    ) -> None:
        del pair, candidate_path, owner

    async def observe(
        self,
        candidate_path: Path,
        owner: str,
        observer: Callable[[str], Awaitable[CandidateObservation]],
    ) -> CandidateObservation:
        del candidate_path, owner, observer
        return self._observation


def _authored_observation() -> CandidateObservation:
    """The observation a reader of the fixture would predict, authored by hand.

    Every number here is an independent belief about what real Oxigraph reports for
    the two fixture files, not a value copied from a run:

    - ``default_triples`` is 3 because ``SELECT (COUNT(*)) WHERE { ?s ?p ?o }`` sees
      only the default graph (``rdf:type owl:Ontology``, ``owl:versionInfo``, and
      ``rdf:type owl:Class`` for C6135) — it does **not** union the named graph.
    - ``stated_triples`` is 16: the stated file's ontology header (2), the deprecated
      C14806 class (2), and C6135's equivalent-class intersection with its restriction
      (12) as expanded by the RDF/XML collection parse.
    - exactly one named graph exists, and its count equals ``stated_triples``.
    - ``owl:versionInfo`` binds uniquely in each graph.

    Scope of the guarantee: the unit suite's ``_Runtime`` double shares only the
    *structural* beliefs pinned here — one named graph, that it is
    ``STATED_GRAPH_IRI``, a unique version per graph, the required restriction, and
    the stated-only sentinel asymmetry. Its triple and restriction counts are
    synthetic values over a different fixture and are **not** covered by this test.
    """
    return CandidateObservation(
        default_triples=3,
        stated_triples=16,
        named_graphs=(CandidateGraph(graph_iri=STATED_GRAPH_IRI, triples=16),),
        default_version=_VERSION,
        stated_version=_VERSION,
        restriction_count=1,
        has_required_restriction=True,
        default_has_stated_only_sentinel=False,
        stated_has_stated_only_sentinel=True,
    )


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_real_cli_candidate_matches_observation_double(
    oxigraph_sibling_store_root: Path,
    integration_connection_scope: Callable[[str], AbstractContextManager[None]],
) -> None:
    pair_path = _write_pair(oxigraph_sibling_store_root)
    active = oxigraph_sibling_store_root / "oxigraph-ncit"
    active.mkdir()
    sentinel = active / "active-sentinel"
    sentinel.write_text("untouched")
    policy = CandidateValidationPolicy(
        min_default_triples=1,
        max_default_triples=100,
        min_stated_triples=1,
        max_stated_triples=100,
        min_restrictions=1,
        max_restrictions=5,
    )
    real = await build_ncit_sibling_store(
        pair_path,
        active_store_path=active,
        owner="1" * 32,
        policy=policy,
        runtime=DockerOxigraphRuntime(
            connection_scope=integration_connection_scope,
        ),
    )

    # Data-shape contract: the authored belief must match what real Oxigraph reports.
    assert real.observation == _authored_observation()

    # Double fidelity: the same authored input, fed to the double rather than derived
    # from the real result, must reach the same certified verdict.
    second_active = oxigraph_sibling_store_root / "second-active"
    second_active.mkdir()
    doubled = await build_ncit_sibling_store(
        pair_path,
        active_store_path=second_active,
        owner="2" * 32,
        policy=policy,
        runtime=_ObservationDouble(_authored_observation(), real.loader),
    )

    assert real.observation.named_graphs[0].graph_iri == STATED_GRAPH_IRI
    assert doubled.observation == real.observation
    assert doubled.source_identity == real.source_identity
    assert sentinel.read_text() == "untouched"


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_real_store_refutes_a_double_that_overstates_the_candidate(
    oxigraph_sibling_store_root: Path,
    integration_connection_scope: Callable[[str], AbstractContextManager[None]],
) -> None:
    """A double stronger than reality must not be able to certify a build.

    #73 shipped bugs because a hand-made double asserted a guarantee the real tool
    does not provide. Here the double claims the stated-only sentinel is also visible
    in the default graph; real Oxigraph disagrees, so the two verdicts must differ.
    """
    pair_path = _write_pair(oxigraph_sibling_store_root)
    active = oxigraph_sibling_store_root / "oxigraph-ncit"
    active.mkdir()
    policy = CandidateValidationPolicy(
        min_default_triples=1,
        max_default_triples=100,
        min_stated_triples=1,
        max_stated_triples=100,
        min_restrictions=1,
        max_restrictions=5,
    )
    real = await build_ncit_sibling_store(
        pair_path,
        active_store_path=active,
        owner="4" * 32,
        policy=policy,
        runtime=DockerOxigraphRuntime(
            connection_scope=integration_connection_scope,
        ),
    )
    assert real.observation.default_has_stated_only_sentinel is False

    overstated = _authored_observation().model_copy(
        update={"default_has_stated_only_sentinel": True}
    )
    second_active = oxigraph_sibling_store_root / "second-active"
    second_active.mkdir()

    with pytest.raises(SiblingStoreValidationError, match="stated-only"):
        await build_ncit_sibling_store(
            pair_path,
            active_store_path=second_active,
            owner="5" * 32,
            policy=policy,
            runtime=_ObservationDouble(overstated, real.loader),
        )


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_real_malformed_candidate_reaches_required_restriction_gate(
    oxigraph_sibling_store_root: Path,
    integration_connection_scope: Callable[[str], AbstractContextManager[None]],
) -> None:
    pair_path = _write_pair(
        oxigraph_sibling_store_root,
        stated_bytes=_STATED.replace(b"C27970", b"C27971"),
    )
    active = oxigraph_sibling_store_root / "oxigraph-ncit"
    active.mkdir()
    owner = "3" * 32

    with pytest.raises(
        SiblingStoreValidationError,
        match="required C6135 restriction",
    ):
        await build_ncit_sibling_store(
            pair_path,
            active_store_path=active,
            owner=owner,
            policy=CandidateValidationPolicy(
                min_default_triples=1,
                max_default_triples=100,
                min_stated_triples=1,
                max_stated_triples=100,
                min_restrictions=1,
                max_restrictions=5,
            ),
            runtime=DockerOxigraphRuntime(
                connection_scope=integration_connection_scope,
            ),
        )

    candidate = active.parent / f".{active.name}.candidate-{owner}"
    assert (candidate / REJECTED_CANDIDATE_FILENAME).exists()
    assert list(active.iterdir()) == []


@pytest.mark.integration
@pytest.mark.mutating_integration
@pytest.mark.full_build
@pytest.mark.slow
async def test_complete_pinned_ncit_pair_builds_certified_sibling(
    oxigraph_sibling_store_root: Path,
    integration_connection_scope: Callable[[str], AbstractContextManager[None]],
) -> None:
    configured = os.environ.get(
        "ONTOPRISM_NCIT_PAIR_MANIFEST",
        "data/ncit-owl/ncit-artifact-pair.json",
    )
    pair_path = Path(configured).resolve()
    if not pair_path.is_file():
        pytest.fail(
            "complete NCIt pair manifest is required; set ONTOPRISM_NCIT_PAIR_MANIFEST"
        )
    active = oxigraph_sibling_store_root / "oxigraph-ncit"
    active.mkdir()
    sentinel = active / "active-sentinel"
    sentinel.write_text("untouched")

    manifest = await build_ncit_sibling_store(
        pair_path,
        active_store_path=active,
        owner="4" * 32,
        runtime=DockerOxigraphRuntime(
            connection_scope=integration_connection_scope,
        ),
    )

    assert manifest.ontology_version == "26.07d"
    assert manifest.observation.default_triples == 12_980_813
    assert manifest.observation.stated_triples == 10_855_010
    assert manifest.observation.restriction_count == 149_694
    assert len(manifest.observation.named_graphs) == 1
    assert manifest.observation.named_graphs[0].graph_iri == STATED_GRAPH_IRI
    assert manifest.observation.named_graphs[0].triples == 10_855_010
    assert manifest.observation.has_required_restriction is True
    assert manifest.observation.default_has_stated_only_sentinel is False
    assert manifest.observation.stated_has_stated_only_sentinel is True
    assert manifest.loader.cli_version == "oxigraph 0.5.3"
    assert manifest.loader.image.endswith(
        "cc943499d4724fbb348c75c623335c69a047de71c59852413b0d0467d3caebe3"
    )
    runtime = DockerOxigraphRuntime(
        connection_scope=integration_connection_scope,
    )
    scope_codes: dict[str, tuple[str, ...]] = {}
    source_snapshots: list[NcitSourceSnapshot] = []
    c27262_root_rows: list[dict[str, str | None]] = []
    canonical_role_counts: dict[str, int] = {}
    c27262_complete_records: list[CompleteDefinition] = []
    nested_definition_codes: set[str] = set()
    r82_resource_evidence: list[tuple[int, int, int, str | None]] = []

    async def inspect_certified_candidate(endpoint: str) -> CandidateObservation:
        source_snapshots.append(
            await _source_snapshot(
                Path(manifest.candidate_path) / CANDIDATE_MANIFEST_FILENAME,
                endpoint,
            )
        )
        async with OxigraphHttpClient(endpoint) as client:
            scope_codes["neoplasm"] = await enumerate_scope_codes(client, "C3262")
            scope_codes["disease"] = await enumerate_scope_codes(client, "C2991")
            (
                root_rows,
                role_counts,
                complete,
                nested_codes,
                request_count,
                row_count,
                expanded_count,
                following_version,
            ) = await _m1_walker_evidence(client)
            c27262_root_rows.extend(root_rows)
            canonical_role_counts.update(role_counts)
            c27262_complete_records.append(complete)
            nested_definition_codes.update(nested_codes)
            r82_resource_evidence.append(
                (
                    request_count,
                    row_count,
                    expanded_count,
                    following_version,
                )
            )
        return await observe_ncit_candidate(endpoint)

    observation = await runtime.observe(
        Path(manifest.candidate_path),
        manifest.owner,
        inspect_certified_candidate,
    )
    assert observation == manifest.observation
    assert len(source_snapshots) == 1
    source = source_snapshots[0]
    assert source.source_identity == manifest.source_identity
    assert source.ontology_version == "26.07d"
    _assert_m1_scope_and_walker_evidence(
        scope_codes,
        c27262_root_rows,
        canonical_role_counts,
        c27262_complete_records[0],
        nested_definition_codes,
        r82_resource_evidence[0],
    )
    assert sentinel.read_text() == "untouched"
