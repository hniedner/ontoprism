"""Read-only configured-store contracts for C3262 acceptance inputs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import vocab
from ontolib.decomposition.corpus_acceptance import (
    CertifiedSourceBinding,
    PublicationPlaneBinding,
    build_assertion_evidence_closure,
    build_concept_exclusion_dispositions,
    build_effective_artifact,
    build_review_required_exclusions,
    dry_run_corpus_publication,
    evaluate_persisted_assertion_evidence,
    observe_full_store_acceptance_inputs,
)
from ontolib.decomposition.corpus_baseline import load_corpus_baseline
from ontolib.decomposition.fanout_baseline import load_fanout_baseline
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.sibling_store import validate_ncit_sibling_manifest

pytestmark = [pytest.mark.integration, pytest.mark.full_store]


@pytest.mark.full_store
async def test_real_corpus_has_complete_gap_inventory_and_zero_included_gaps(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[3]
    baseline = load_corpus_baseline(root / "tmp/m1-6-current-corpus-baseline.json")
    manifest_path = root / "data/qlever-ncit/.ontoprism-ncit-candidate.json"
    manifest = validate_ncit_sibling_manifest(manifest_path)
    policy_identity = hashlib.sha256(
        (root / "ontolib/src/ontolib/decomposition/axis_contracts.py").read_bytes()
    ).hexdigest()
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        outcomes = tuple(await store.work_item_outcomes(baseline.run_id))
        decompositions = tuple(await store.decompositions_for_run(baseline.run_id))
        residual = tuple(await store.residual_filler_classifications(baseline.run_id))
    finally:
        await dispose_engine(engine)

    evaluation = evaluate_persisted_assertion_evidence(
        decompositions, policy_identity=policy_identity
    )
    closure = build_assertion_evidence_closure(
        source_artifact=root / "tmp/m1-6-current-full-corpus.ttl",
        effective_destination=tmp_path / "evidence-closed.ttl",
        source=CertifiedSourceBinding(
            release=manifest.ontology_version,
            source_manifest_identity=hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            source_identity=manifest.source_identity,
            stated_artifact_identity=manifest.stated_artifact.artifact_identity,
            certification="expert-curated-ncit-release",
        ),
        persisted_evidence=evaluation,
        concept_exclusions=build_concept_exclusion_dispositions(
            outcomes=outcomes,
            decompositions=decompositions,
            residual_classifications=residual,
        ),
    )

    inventory = closure.evidence_gap_inventory
    observed_counts = {item.reason: item.count for item in inventory.reason_counts}
    assert "review-required" not in observed_counts
    assert "missing-source-occurrence" in observed_counts
    assert closure.original_candidate_assertion_count == (
        len(closure.included_assertion_closure)
        + len(closure.excluded_assertion_closure)
    )
    assert closure.inclusion_coverage > 0
    assert closure.qualifying_evidence_coverage == 1
    assert not any(
        item.concept_code == "C100051"
        and item.axis == "op:Morphology"
        and item.filler_code == "C9385"
        and item.reason == "missing-source-occurrence"
        for item in inventory.gaps
    )
    assert len(closure.included_assertion_closure) == len(closure.evidence_ledger)
    assert not {
        item.assertion_identity for item in closure.included_assertion_closure
    } & {item.assertion_identity for item in inventory.gaps}
    assert all(
        item.official_source_preserved for item in closure.excluded_assertion_closure
    )


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
    budget = load_fanout_baseline(
        root / "ontolib/tests/decomposition/golden/neoplasm-highest-fanout.json",
        expected_source_identity=(
            "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
        ),
        expected_release="26.07d",
    )
    assert observed.logical_select_count <= budget.logical_select_count_budget
    assert observed.r82_select_count <= budget.select_once_r82_count_budget


@pytest.mark.full_store
def test_effective_artifact_removes_exact_disputed_pairs_without_source_mutation(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[3]
    source = root / "tmp/m1-6-current-full-corpus.ttl"
    source_before = source.read_bytes()
    exclusions = build_review_required_exclusions(
        root / "evidence/group-review-packet-26.07d-schema3.json",
        root / "evidence/group-review-rationale-26.07d.md",
        source,
    )

    observed = build_effective_artifact(
        source_artifact=source,
        destination=tmp_path / "effective.ttl",
        exclusions=exclusions,
        expected_minted_count=2_649,
        proposal_registry=(
            root / "ontolib/tests/decomposition/golden/proposal-registry.json"
        ),
        proposal_registry_migration=(
            root / "ontolib/tests/decomposition/golden/"
            "proposal-registry-schema2-migration.json"
        ),
        candidate_source_identity=(
            "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
        ),
    )

    assert source.read_bytes() == source_before
    assert observed.removed_pair_count == 27
    assert observed.non_emitted_pair_count == 3
    assert observed.proposal_delta.original_emitted_count == 2_649
    assert observed.proposal_delta.removed_unreconciled_count == 2_649
    assert len(observed.proposal_delta.distinct_proposal_ids) == 219
    assert hashlib.sha256(
        json.dumps(
            observed.proposal_delta.distinct_proposal_ids,
            separators=(",", ":"),
        ).encode()
    ).hexdigest() == (
        "7fae9f74ed28639f0d69dc467cef346d2745fbc63b3f09715f3576aee2335f65"
    )
    assert observed.proposal_delta.registry_intersection == ()
    assert observed.proposal_delta.unreconciled_emitted_count == 0
    assert {
        (item.concept_code, item.axis, item.filler_code)
        for item in observed.non_emitted_pairs
    } == {
        ("C102870", "op:PrimarySite", "C12404"),
        ("C27262", "op:AssociatedRegion", "C41165"),
        ("C35756", "op:StageSystem", "C141685"),
    }
    assert all(
        item.input_present and not item.output_present
        for item in observed.removed_pairs
    )
    assert all(
        not item.input_present and not item.output_present
        for item in observed.non_emitted_pairs
    )
    assert {item.concept_code for item in observed.removed_pairs} == {
        "C102870",
        "C198031",
        "C27262",
        "C35756",
    }


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
                planes=PublicationPlaneBinding(
                    official_persisted_representation_identity=(
                        baseline.representation_identity
                    ),
                    effective_representation_identity=baseline.representation_identity,
                ),
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
