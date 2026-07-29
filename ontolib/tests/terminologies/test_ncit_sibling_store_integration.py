from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.decompose import _source_snapshot

from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    REJECTED_CANDIDATE_FILENAME,
    CandidateObservation,
    CandidateValidationPolicy,
    DockerOxigraphRuntime,
    LoaderIdentity,
    SiblingStoreValidationError,
    build_ncit_sibling_store,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractContextManager

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

    second_active = oxigraph_sibling_store_root / "second-active"
    second_active.mkdir()
    doubled = await build_ncit_sibling_store(
        pair_path,
        active_store_path=second_active,
        owner="2" * 32,
        policy=policy,
        runtime=_ObservationDouble(real.observation, real.loader),
    )

    assert real.observation.default_version == _VERSION
    assert real.observation.stated_version == _VERSION
    assert real.observation.named_graphs[0].graph_iri == STATED_GRAPH_IRI
    assert real.observation.restriction_count == 1
    assert real.observation.has_required_restriction is True
    assert real.observation.default_has_stated_only_sentinel is False
    assert real.observation.stated_has_stated_only_sentinel is True
    assert doubled.observation == real.observation
    assert doubled.source_identity == real.source_identity
    assert sentinel.read_text() == "untouched"


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
    source = await DockerOxigraphRuntime(
        connection_scope=integration_connection_scope,
    ).observe(
        Path(manifest.candidate_path),
        manifest.owner,
        lambda endpoint: _source_snapshot(
            Path(manifest.candidate_path) / CANDIDATE_MANIFEST_FILENAME,
            endpoint,
        ),
    )
    assert source.source_identity == manifest.source_identity
    assert source.ontology_version == "26.07d"
    assert sentinel.read_text() == "untouched"
