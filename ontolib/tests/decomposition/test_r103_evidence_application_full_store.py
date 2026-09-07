import json
from pathlib import Path

import pytest

from ontolib.decomposition.r103_evidence_application import (
    build_applied_policy_report,
    build_authority_artifact,
    build_candidate_artifact,
    build_corroboration_normalization,
    build_source_inventory,
    query_candidate_codes,
    query_source_inventory,
)
from ontolib.decomposition.r103_specificity_review import (
    load_specificity_review,
    load_specificity_review_target,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client

GOLDEN = Path("ontolib/tests/decomposition/golden")
OWL = Path("data/ncit-owl/Thesaurus-stated.owl")
MANIFEST = Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")


@pytest.mark.integration
@pytest.mark.full_store
async def test_r103_application_artifacts_match_qlever_and_independent_real_xml() -> (
    None
):
    async with ncit_sparql_client(
        "http://localhost:7888", query_timeout=180.0
    ) as client:
        inventory_rows = await query_source_inventory(client, row_bound=100)
        candidate_rows = await query_candidate_codes(
            client, row_bound=10_000, page_size=500
        )

    inventory = build_source_inventory(
        OWL, MANIFEST, qlever_rows=inventory_rows, row_bound=100
    )
    candidates = build_candidate_artifact(
        OWL, MANIFEST, qlever_rows=candidate_rows, row_bound=10_000
    )
    authority = build_authority_artifact(
        GOLDEN / "r103-review-state-26.07d.json",
        GOLDEN / "r103-review-state-26.07d-rev2.json",
        GOLDEN / "proposal-registry-schema2-migration.json",
        inventory,
    )
    corroboration = build_corroboration_normalization(
        GOLDEN / "r103-c3264-corroboration-26.07d.json", authority
    )
    application = build_applied_policy_report(
        inventory=inventory,
        authority=authority,
        proposal_registry_path=GOLDEN / "proposal-registry.json",
        migration_path=GOLDEN / "proposal-registry-schema2-migration.json",
        oracle_path=GOLDEN / "neoplasm-adjudicated.json",
    )

    assert {
        (row.subject_code, row.role_code, row.filler_code) for row in inventory.rows
    } >= {
        ("C2860", "R103", "C12950"),
        ("C3264", "R103", "C12950"),
        ("C3716", "R103", "C34228"),
    }
    assert candidates.candidate_count > 0
    assert corroboration.upstream_verified is False
    assert application.authorization is False
    assert application.publication_attempted is False

    tracked = (
        (GOLDEN / "r103-source-inventory-26.07d.json", inventory),
        (GOLDEN / "r103-c12950-candidates-26.07d.json", candidates),
        (GOLDEN / "r103-authority-normalized-26.07d.json", authority),
        (GOLDEN / "r103-corroboration-normalized-26.07d.json", corroboration),
        (GOLDEN / "r103-applied-policy-26.07d.json", application),
    )
    for path, artifact in tracked:
        assert json.loads(path.read_text(encoding="ascii")) == artifact.model_dump(
            mode="json"
        )

    target = load_specificity_review_target(
        GOLDEN / "r103-c2860-specificity-target-26.07d.json",
        inventory=inventory,
        candidates=candidates,
        authority=authority,
    )
    selected = load_specificity_review(
        GOLDEN / "r103-c2860-specificity-selected-26.07d.json",
        target=target,
        inventory=inventory,
        candidates=candidates,
        authority=authority,
        application=application,
        revision_path=GOLDEN / "r103-review-state-26.07d-rev2.json",
    )
    assert selected.selected_option == "qualify-global-most-specific-claim"
    assert selected.enumerated_candidate_count == candidates.candidate_count == 16
    assert selected.applied_policy_identity == application.artifact_identity
