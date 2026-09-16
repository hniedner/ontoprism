"""Read-only configured-store contracts for C3262 acceptance inputs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import vocab
from ontolib.decomposition.corpus_acceptance import (
    dry_run_corpus_publication,
    observe_full_store_acceptance_inputs,
)
from ontolib.decomposition.corpus_baseline import load_corpus_baseline
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.terminologies.ncit.client import ncit_sparql_client

pytestmark = [pytest.mark.integration, pytest.mark.full_store]


@pytest.mark.full_store
async def test_c3262_scope_source_exclusions_and_highest_fanout_are_exact() -> None:
    root = Path(__file__).parents[3]
    url = os.environ.get("NCIT_STATED_SPARQL_URL", "http://localhost:7888")
    async with ncit_sparql_client(url) as client:
        observed = await observe_full_store_acceptance_inputs(
            client,
            source_manifest=root / "data/qlever-ncit/.ontoprism-ncit-candidate.json",
            baseline=(
                root / "ontolib/tests/decomposition/golden/"
                "neoplasm-current-corpus-baseline.json"
            ),
            artifact=root / "tmp/m1-6-current-full-corpus.ttl",
            report=(
                root / "ontolib/tests/decomposition/golden/"
                "neoplasm-r101-v5-conservation.json.gz"
            ),
            review_packet=root / "evidence/group-review-packet-26.07d-schema3.json",
            fanout_baseline=root
            / "ontolib/tests/decomposition/golden/neoplasm-highest-fanout.json",
        )

    assert observed.scope_root == "C3262"
    assert observed.worklist_count == 15_633
    assert observed.exclusion_concepts == (
        "C102870",
        "C198031",
        "C27262",
        "C35756",
    )
    assert observed.official_source_assertion_count > 0
    assert observed.highest_fanout_codes == ("C9379", "C9423")
    assert observed.logical_select_count <= 31
    assert observed.r82_select_count <= 9


@pytest.mark.full_store
async def test_existing_corpus_dry_run_reads_postgres_and_qlever_boundaries() -> None:
    root = Path(__file__).parents[3]
    baseline = load_corpus_baseline(
        root
        / "ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json"
    )
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        aggregate = await store.corpus_baseline_aggregate(baseline.run_id)
        url = os.environ.get("NCIT_SPARQL_URL", "http://localhost:7888")
        async with ncit_sparql_client(url) as client:
            evidence = await dry_run_corpus_publication(
                candidate_content_identity="a" * 64,
                run_id=baseline.run_id,
                source_identity=baseline.source_identity,
                representation_identity=baseline.representation_identity,
                artifact=root / "tmp/m1-6-current-full-corpus.ttl",
                destination_graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
                expected_codes=aggregate.decomposed_codes,
                expected_worklist_count=baseline.worklist_count,
                graph=client,
                provenance=store,
            )
    finally:
        await dispose_engine(engine)

    assert evidence.status == "passed"
    assert evidence.artifact_identity == baseline.artifact_identity
    assert evidence.expected_concept_count == 15_633
    assert evidence.represented_concept_count == 14_884
    assert evidence.publication_writes_performed is False
