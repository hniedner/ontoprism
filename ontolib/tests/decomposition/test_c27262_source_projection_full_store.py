from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import rdflib
from scripts.research.current_evidence import CurrentEngineEvidence
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import vocab
from ontolib.decomposition.collapse_policy import NO_COLLAPSE_VETO_POLICY
from ontolib.decomposition.fanout_baseline import _CountingClient
from ontolib.decomposition.models import GenusDefinitionFact
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.run import _decompose_one
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client

pytestmark = [pytest.mark.integration, pytest.mark.full_store]

_CONCEPT = "C27262"
_TRACKED_EVIDENCE = Path(__file__).with_name("golden") / (
    "neoplasm-current-engine-evidence.json"
)
_C9290_FACT_ID = "aad190c812e6e9587657af7cc2ed9aa858a092b649109ea5b5a523543056cacf"


def _morphology_projection(decomposition) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        sorted(
            (item.filler_code, item.source_definition_ids)
            for item in decomposition.constituents
            if item.axis == "op:Morphology"
        )
    )


def _artifact_morphology(path: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    graph = rdflib.Graph().parse(path)
    subject = rdflib.URIRef(f"{NCIT_NS}{_CONCEPT}")
    morphology = rdflib.URIRef(f"{vocab.ONTOPRISM_NS}Morphology")
    rows = []
    for node in graph.objects(subject, rdflib.URIRef(vocab.HAS_CONSTITUENT)):
        if graph.value(node, rdflib.URIRef(vocab.AXIS)) != morphology:
            continue
        filler = cast("rdflib.URIRef", graph.value(node, rdflib.URIRef(vocab.FILLER)))
        source_ids = tuple(
            sorted(
                str(value).removeprefix(f"{vocab.DEFINITION_FACT_NS}{_CONCEPT}/")
                for value in graph.objects(
                    node, rdflib.URIRef(vocab.SOURCE_DEFINITION_FACT)
                )
            )
        )
        rows.append((str(filler).removeprefix(NCIT_NS), source_ids))
    return tuple(sorted(rows))


async def test_c27262_source_projection_is_conserved_through_current_layers() -> None:
    evidence = CurrentEngineEvidence.model_validate_json(_TRACKED_EVIDENCE.read_bytes())
    tracked = next(item for item in evidence.concepts if item.code == _CONCEPT)
    evidence_projection = tuple(
        sorted(
            (
                item.filler,
                tuple(getattr(item, "source_definition_ids", ())),
            )
            for item in tracked.constituents
            if item.axis == "op:Morphology"
        )
    )

    async def no_label_match(_surface: str) -> str | None:
        return None

    async with ncit_sparql_client(
        "http://localhost:7888", query_timeout=180.0
    ) as client:
        counted = _CountingClient(client)
        result = await _decompose_one(
            _CONCEPT,
            cast("Any", counted),
            label=None,
            label_lookup=no_label_match,
            source_identity=evidence.source_identity,
            collapse_policy=NO_COLLAPSE_VETO_POLICY,
            walker_max_depth=5,
        )

    assert result.decomposition is not None
    complete = result.decomposition.complete_definition
    assert complete is not None
    source_genus_facts = tuple(
        sorted(
            (fact.genus_code, fact.fact_id, fact.group_id)
            for fact in complete.facts
            if isinstance(fact, GenusDefinitionFact) and fact.anchor_code == _CONCEPT
        )
    )
    source_projection = tuple(
        (filler, (fact_id,)) for filler, fact_id, _group_id in source_genus_facts
    )

    engine = make_engine(get_settings().database_url)
    try:
        store = ProvenanceStore(make_sessionmaker(engine))
        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT status, publication_artifact_path FROM decomp_run "
                            "WHERE id = :run_id"
                        ),
                        {"run_id": evidence.run_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            persisted_projection = None
            artifact_projection = None
        else:
            decompositions = await store.decompositions_for_run(evidence.run_id)
            persisted_projection = _morphology_projection(
                next(item for item in decompositions if item.code == _CONCEPT)
            )
            artifact_path = Path(row["publication_artifact_path"])
            artifact_projection = (
                _artifact_morphology(artifact_path) if artifact_path.exists() else None
            )
    finally:
        await dispose_engine(engine)

    assert {filler for filler, _fact_id, _group_id in source_genus_facts} == {
        "C35501",
        "C9290",
    }
    assert (
        next(
            fact_id
            for filler, fact_id, _group_id in source_genus_facts
            if filler == "C9290"
        )
        == _C9290_FACT_ID
    )
    assert counted.logical_select_count <= 30
    assert _morphology_projection(result.decomposition) == source_projection
    assert persisted_projection == source_projection
    assert artifact_projection == source_projection
    assert evidence_projection == source_projection
