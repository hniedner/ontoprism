"""Behavioral contracts for the C3262 corpus acceptance candidate."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError
from rdflib import Graph, URIRef
from rdflib import Literal as RdfLiteral
from scripts.research.group_review_packet import load_historical_group_review_packet

from ontolib.decomposition import corpus_acceptance as corpus_acceptance_module
from ontolib.decomposition import vocab
from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.corpus_acceptance import (
    AcceptedHumanAcceptanceDecision,
    CandidateMetrics,
    CorpusAcceptanceCandidate,
    CorpusAcceptanceContent,
    CorpusAcceptanceValidationError,
    GateEvaluation,
    PendingHumanAcceptanceDecision,
    PublicationDryRunEvidence,
    PublicationPlaneBinding,
    ReviewRequiredEffectiveExclusion,
    build_accepted_publication_artifact,
    build_effective_artifact,
    build_review_required_exclusions,
    classify_corpus_delta,
    dry_run_corpus_publication,
    finalize_corpus_acceptance_candidate,
    generate_c3262_acceptance_candidate,
    load_human_acceptance_decision,
    require_publication_authorization,
    write_pending_human_acceptance_decision,
)
from ontolib.decomposition.proposal_registry import load_proposal_registry
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    CompletedRunForEvidence,
    RunFingerprint,
)
from ontolib.decomposition.r101_conservation import (
    ClassifiedNonR101Delta,
    NonR101MetadataDelta,
    R101ConservationReport,
    load_r101_conservation_report,
)
from ontolib.terminologies.ncit.sibling_store import (
    NcitSiblingStoreManifest,
    SiblingStoreValidationError,
)

GOLDEN = Path(__file__).with_name("golden")
ROOT = Path(__file__).parents[3]
SHA = "a" * 64


def _classify(report: R101ConservationReport):
    policy = (
        ROOT / "ontolib/src/ontolib/decomposition/data/normalized-group-policy.json"
    )
    return classify_corpus_delta(
        report,
        routing_policy_identity=hashlib.sha256(policy.read_bytes()).hexdigest(),
        proposal_registry=load_proposal_registry(GOLDEN / "proposal-registry.json"),
    )


def _identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _exclusion(
    *,
    filler: str = "C2",
    relation: str = "grouping-disputed",
    disposition: str = "removed-from-effective",
) -> ReviewRequiredEffectiveExclusion:
    pair: dict[str, object] = {
        "axis": "op:Morphology",
        "filler_code": filler,
        "review_relations": (relation,),
        "effective_disposition": disposition,
        "historical_evidence_identities": ("2" * 64,),
        "source_assertion_identities": (
            ("1" * 64,) if disposition == "removed-from-effective" else ()
        ),
        "next_step": (
            "specialist-decision-required-before-inclusion"
            if disposition == "removed-from-effective"
            else (
                "engineering-source-provenance-prerequisite-plus-specialist-decision-"
                "before-inclusion"
            )
        ),
    }
    if disposition == "review-required-non-emitted":
        pair["interpretation"] = "absence-is-scoped-evidence-not-falsehood"
    return ReviewRequiredEffectiveExclusion.model_validate(
        {
            "concept_code": "C1",
            "pair_changes": (pair,),
            "reason": "unresolved-semantic-ambiguity",
            "official_source_preserved": True,
            "human_approval": False,
            "nci_approval": False,
        }
    )


def _proposal_build_kwargs(expected_minted_count: int = 0) -> dict[str, object]:
    return {
        "expected_minted_count": expected_minted_count,
        "proposal_registry": GOLDEN / "proposal-registry.json",
        "proposal_registry_migration": (
            GOLDEN / "proposal-registry-schema2-migration.json"
        ),
        "candidate_source_identity": (
            "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
        ),
    }


def _empty_proposal_delta():  # type: ignore[no-untyped-def]
    binding = corpus_acceptance_module.build_historical_proposal_registry_binding(
        registry_path=GOLDEN / "proposal-registry.json",
        migration_path=GOLDEN / "proposal-registry-schema2-migration.json",
        candidate_source_identity=(
            "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
        ),
    )
    return corpus_acceptance_module.EffectiveProposalDelta(
        registry=binding,
        original_emitted_count=0,
        removed_unreconciled_count=0,
        unreconciled_emitted_count=0,
        accepted_without_evidence_count=0,
        distinct_proposal_ids=(),
        registry_intersection=(),
        removed_assertions=(),
    )


def _mint_line(
    *, subject: str = f"{corpus_acceptance_module.NCIT_NS}C1", axis: str | None = None
) -> str:
    axis = axis or f"{vocab.ONTOPRISM_NS}StageSystem"
    return (
        f"<{subject}> <{vocab.HAS_CONSTITUENT}> "
        f"[<{vocab.AXIS}> <{axis}> ; <{vocab.FILLER}> "
        f"<{vocab.ONTOPRISM_NS}MINT-deadbeef1234> ] .\n"
    )


@pytest.fixture(scope="module")
def exclusions():  # type: ignore[no-untyped-def]
    return build_review_required_exclusions(
        ROOT / "evidence/group-review-packet-26.07d-schema3.json",
        ROOT / "evidence/group-review-rationale-26.07d.md",
        ROOT / "tmp/m1-6-current-full-corpus.ttl",
    )


@pytest.fixture(scope="module")
def report():  # type: ignore[no-untyped-def]
    return load_r101_conservation_report(
        GOLDEN / "neoplasm-r101-v5-conservation.json.gz"
    )


@pytest.fixture(scope="module")
def classification(report, exclusions):  # type: ignore[no-untyped-def]
    return _classify(report)


@pytest.fixture(scope="module")
def candidate(classification, exclusions):  # type: ignore[no-untyped-def]
    content = _content(classification, exclusions)
    return finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )


@pytest.mark.unit
def test_review_required_exclusions_are_exact_pair_scoped_and_source_preserving(
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    assert [item.concept_code for item in exclusions] == [
        "C102870",
        "C198031",
        "C27262",
        "C35756",
    ]
    by_code = {item.concept_code: item for item in exclusions}
    assert [(p.axis, p.filler_code) for p in by_code["C27262"].pair_changes] == [
        ("op:AssociatedRegion", "C41165"),
        ("op:ClinicalFinding", "C36220"),
        ("op:ClinicalFinding", "C41397"),
        ("op:Morphology", "C35501"),
        ("op:Morphology", "C9290"),
    ]
    assert [(p.axis, p.filler_code) for p in by_code["C102870"].pair_changes] == [
        ("op:AssociatedSite", "C12321"),
        ("op:Morphology", "C121619"),
        ("op:Morphology", "C39986"),
        ("op:PrimarySite", "C12404"),
    ]
    assert all(
        pair.review_relations for item in exclusions for pair in item.pair_changes
    )
    assert sum(len(item.pair_changes) for item in exclusions) == 30
    pairs = {
        (item.concept_code, pair.axis, pair.filler_code): pair
        for item in exclusions
        for pair in item.pair_changes
    }
    non_emitted_keys = {
        ("C102870", "op:PrimarySite", "C12404"),
        ("C27262", "op:AssociatedRegion", "C41165"),
        ("C35756", "op:StageSystem", "C141685"),
    }
    assert {
        key
        for key, pair in pairs.items()
        if pair.effective_disposition == "review-required-non-emitted"
    } == non_emitted_keys
    assert all(
        "missing-from-candidate" in pair.review_relations
        for key, pair in pairs.items()
        if key in non_emitted_keys
    )
    packet = load_historical_group_review_packet(
        ROOT / "evidence/group-review-packet-26.07d-schema3.json"
    )
    rows = {row.concept_code: row for row in packet.review_rows}
    for key, pair in pairs.items():
        code, axis, filler = key
        packet_row = rows[code]
        expected_relations = {
            relation
            for relation, packet_pairs in (
                ("grouping-disputed", packet_row.grouping_diagnosis.affected_pairs),
                ("added-to-candidate", packet_row.pair_delta.extra_pairs),
                ("missing-from-candidate", packet_row.pair_delta.missing_pairs),
            )
            if (axis, filler) in packet_pairs
        }
        assert set(pair.review_relations) == expected_relations
    assert all(
        not pair.source_assertion_identities
        and pair.historical_evidence_identities
        and pair.interpretation == "absence-is-scoped-evidence-not-falsehood"
        and pair.next_step
        == (
            "engineering-source-provenance-prerequisite-plus-specialist-decision-"
            "before-inclusion"
        )
        for key, pair in pairs.items()
        if key in non_emitted_keys
    )
    assert all(
        pair.effective_disposition == "removed-from-effective"
        and pair.source_assertion_identities
        for key, pair in pairs.items()
        if key not in non_emitted_keys
    )
    assert all(item.official_source_preserved for item in exclusions)
    assert all(
        item.human_approval is False and item.nci_approval is False
        for item in exclusions
    )
    assert all(
        pair.historical_evidence_identities
        for item in exclusions
        for pair in item.pair_changes
    )


@pytest.mark.unit
def test_effective_artifact_distinguishes_removed_and_non_emitted_liveness(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    source.write_text(
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> "
        "<https://w3id.org/ontoprism/vocab#Morphology> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C2> ] .\n"
    )
    common = {
        "axis": "op:Morphology",
        "review_relations": ("grouping-disputed",),
        "historical_evidence_identities": ("2" * 64,),
    }
    removed = {
        **common,
        "filler_code": "C2",
        "effective_disposition": "removed-from-effective",
        "source_assertion_identities": ("1" * 64,),
        "next_step": "specialist-decision-required-before-inclusion",
    }
    non_emitted = {
        **common,
        "filler_code": "C3",
        "review_relations": ("missing-from-candidate",),
        "effective_disposition": "review-required-non-emitted",
        "source_assertion_identities": (),
        "interpretation": "absence-is-scoped-evidence-not-falsehood",
        "next_step": (
            "engineering-source-provenance-prerequisite-plus-specialist-decision-"
            "before-inclusion"
        ),
    }
    exclusion = ReviewRequiredEffectiveExclusion.model_validate(
        {
            "concept_code": "C1",
            "pair_changes": (removed, non_emitted),
            "reason": "unresolved-semantic-ambiguity",
            "official_source_preserved": True,
            "human_approval": False,
            "nci_approval": False,
        }
    )

    evidence = build_effective_artifact(
        source_artifact=source,
        destination=tmp_path / "effective.ttl",
        exclusions=(exclusion,),
        **_proposal_build_kwargs(),  # type: ignore[arg-type]
    )

    assert evidence.removed_pair_count == 1
    assert evidence.non_emitted_pair_count == 1
    assert evidence.removed_pairs[0].input_present is True
    assert evidence.removed_pairs[0].output_present is False
    assert evidence.non_emitted_pairs[0].input_present is False
    assert evidence.non_emitted_pairs[0].output_present is False


@pytest.mark.unit
def test_effective_artifact_semantically_quarantines_every_mint_assertion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    effective = tmp_path / "effective.ttl"
    source.write_text(
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> "
        "<https://w3id.org/ontoprism/vocab#Morphology> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C2> ] .\n"
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> "
        "<https://w3id.org/ontoprism/vocab#StageSystem> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        "<https://w3id.org/ontoprism/vocab#MINT-deadbeef1234> ] .\n"
    )
    source_before = source.read_bytes()

    observed = build_effective_artifact(
        source_artifact=source,
        destination=effective,
        exclusions=(_exclusion(),),
        **_proposal_build_kwargs(1),  # type: ignore[arg-type]
    )

    assert source.read_bytes() == source_before
    assert "MINT-" not in effective.read_text()
    assert observed.proposal_delta.original_emitted_count == 1
    assert observed.proposal_delta.removed_unreconciled_count == 1
    assert observed.proposal_delta.unreconciled_emitted_count == 0
    assert observed.proposal_delta.accepted_without_evidence_count == 0
    assert observed.proposal_delta.removed_assertions[0].disposition == (
        "excluded-unreconciled"
    )


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["malformed", "duplicate"])
def test_proposal_inventory_fails_closed_for_malformed_or_duplicate_assertion(
    tmp_path: Path, failure: str
) -> None:
    filler = "MINT-not-valid" if failure == "malformed" else "MINT-deadbeef1234"
    mint_line = (
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> "
        "<https://w3id.org/ontoprism/vocab#StageSystem> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        f"<https://w3id.org/ontoprism/vocab#{filler}> ] .\n"
    )
    source = tmp_path / "source.ttl"
    source.write_text(mint_line + (mint_line if failure == "duplicate" else ""))

    with pytest.raises(CorpusAcceptanceValidationError, match=failure):
        corpus_acceptance_module.enumerate_mint_assertions(source.read_bytes())


@pytest.mark.unit
def test_historical_registry_source_mismatch_is_bound_observation_not_failure() -> None:
    binding = corpus_acceptance_module.build_historical_proposal_registry_binding(
        registry_path=GOLDEN / "proposal-registry.json",
        migration_path=GOLDEN / "proposal-registry-schema2-migration.json",
        candidate_source_identity=(
            "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
        ),
    )

    assert binding.registry_schema_version == 2
    assert binding.proposal_count == 7
    assert binding.status_counts == {"locally-approved": 2, "proposed": 5}
    assert binding.accepted_in_ncit_count == 0
    assert binding.no_adoption_evidence is True
    assert binding.source_mismatch_observed is True
    assert binding.reconciliation_envelope_available is False


@pytest.mark.unit
def test_mint_assertion_identity_rejects_tampering() -> None:
    assertion = corpus_acceptance_module.enumerate_mint_assertions(
        _mint_line().encode()
    )[0]

    with pytest.raises(ValidationError, match="MINT assertion identity differs"):
        corpus_acceptance_module.MintProposalAssertion.model_validate(
            assertion.model_dump() | {"assertion_identity": "0" * 64}
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"source_mismatch_observed": False}, "source mismatch observation differs"),
        (
            {"status_counts": {"locally-approved": 1, "proposed": 6}},
            "historical proposal lifecycle counts differ",
        ),
    ],
)
def test_historical_registry_binding_rejects_observation_tampering(
    mutation: dict[str, object], message: str
) -> None:
    payload = _empty_proposal_delta().registry.model_dump()

    with pytest.raises(ValidationError, match=message):
        corpus_acceptance_module.HistoricalProposalRegistryBinding.model_validate(
            payload | mutation
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"original_emitted_count": 1}, "inventory is not exhaustive"),
        ({"removed_unreconciled_count": 1}, "removal count differs"),
        (
            {"distinct_proposal_ids": ("MINT-deadbeef1234",)},
            "identifier inventory differs",
        ),
    ],
)
def test_effective_proposal_delta_rejects_nonexhaustive_inventory(
    mutation: dict[str, object], message: str
) -> None:
    payload = _empty_proposal_delta().model_dump()

    with pytest.raises(ValidationError, match=message):
        corpus_acceptance_module.EffectiveProposalDelta.model_validate(
            payload | mutation
        )


@pytest.mark.unit
def test_historical_registry_binding_refuses_missing_migration(tmp_path: Path) -> None:
    with pytest.raises(
        CorpusAcceptanceValidationError,
        match="historical proposal registry binding is invalid",
    ):
        corpus_acceptance_module.build_historical_proposal_registry_binding(
            registry_path=GOLDEN / "proposal-registry.json",
            migration_path=tmp_path / "missing.json",
            candidate_source_identity="b" * 64,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "statement",
    [
        f"<urn:s> <urn:p> <{vocab.ONTOPRISM_NS}MINT-deadbeef1234> .\n",
        _mint_line(subject="urn:not-an-ncit-concept"),
        _mint_line(axis="urn:not-an-ontoprism-axis"),
    ],
)
def test_proposal_inventory_refuses_nonconstituent_or_malformed_semantics(
    statement: str,
) -> None:
    with pytest.raises(
        CorpusAcceptanceValidationError,
        match="malformed proposal-shaped constituent assertion",
    ):
        corpus_acceptance_module.enumerate_mint_assertions(statement.encode())


@pytest.mark.unit
def test_proposal_inventory_refuses_undecodable_turtle() -> None:
    with pytest.raises(
        CorpusAcceptanceValidationError,
        match="malformed proposal-shaped Turtle statement",
    ):
        corpus_acceptance_module.enumerate_mint_assertions(b"MINT-\xff")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("registered", "message"),
    [
        (True, "registered-unreconciled-survivor"),
        (False, "unregistered-unreconciled-survivor"),
    ],
)
def test_effective_proposal_filter_rejects_any_survivor(
    registered: bool, message: str
) -> None:
    registered_ids = {"MINT-deadbeef1234"} if registered else set()

    with pytest.raises(CorpusAcceptanceValidationError, match=message):
        corpus_acceptance_module._require_no_mint_survivors(
            _mint_line().encode(), registered_ids
        )


@pytest.mark.unit
def test_effective_artifact_rejects_baseline_mint_count_mismatch(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    source.write_bytes(b"")

    with pytest.raises(CorpusAcceptanceValidationError, match="count differs"):
        build_effective_artifact(
            source_artifact=source,
            destination=tmp_path / "effective.ttl",
            exclusions=(_exclusion(),),
            **_proposal_build_kwargs(1),  # type: ignore[arg-type]
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filler", "disposition"),
    [
        ("C2", "review-required-non-emitted"),
        ("C3", "removed-from-effective"),
    ],
)
def test_effective_artifact_rejects_false_pair_dispositions(
    tmp_path: Path,
    filler: str,
    disposition: str,
) -> None:
    source = tmp_path / "source.ttl"
    source.write_text(
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> "
        "<https://w3id.org/ontoprism/vocab#Morphology> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C2> ] .\n"
    )
    pair = {
        "axis": "op:Morphology",
        "filler_code": filler,
        "review_relations": (
            ("missing-from-candidate",)
            if disposition == "review-required-non-emitted"
            else ("grouping-disputed",)
        ),
        "effective_disposition": disposition,
        "source_assertion_identities": ()
        if disposition == "review-required-non-emitted"
        else ("1" * 64,),
        "historical_evidence_identities": ("2" * 64,),
        "next_step": (
            "engineering-source-provenance-prerequisite-plus-specialist-decision-"
            "before-inclusion"
            if disposition == "review-required-non-emitted"
            else "specialist-decision-required-before-inclusion"
        ),
    }
    if disposition == "review-required-non-emitted":
        pair["interpretation"] = "absence-is-scoped-evidence-not-falsehood"
    exclusion = ReviewRequiredEffectiveExclusion.model_validate(
        {
            "concept_code": "C1",
            "pair_changes": (pair,),
            "reason": "unresolved-semantic-ambiguity",
            "official_source_preserved": True,
            "human_approval": False,
            "nci_approval": False,
        }
    )

    with pytest.raises(CorpusAcceptanceValidationError, match="disposition"):
        build_effective_artifact(
            source_artifact=source,
            destination=tmp_path / "effective.ttl",
            exclusions=(exclusion,),
            **_proposal_build_kwargs(),  # type: ignore[arg-type]
        )


@pytest.mark.unit
def test_review_required_exclusions_refuse_missing_target_concept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    packet_path = ROOT / "evidence/group-review-packet-26.07d-schema3.json"
    packet = load_historical_group_review_packet(packet_path)
    incomplete = packet.model_copy(
        update={
            "review_rows": tuple(
                row for row in packet.review_rows if row.concept_code != "C35756"
            )
        }
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "load_historical_group_review_packet",
        lambda _: incomplete,
    )
    source = tmp_path / "source.ttl"
    source.write_bytes(b"")

    with pytest.raises(CorpusAcceptanceValidationError, match="lacks an exclusion"):
        build_review_required_exclusions(
            packet_path,
            ROOT / "evidence/group-review-rationale-26.07d.md",
            source,
        )


@pytest.mark.unit
def test_effective_artifact_withholds_only_exact_excluded_pairs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    effective = tmp_path / "effective.ttl"
    source.write_text(
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> "
        "<https://w3id.org/ontoprism/vocab#Morphology> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C2> ] .\n"
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> "
        "<https://w3id.org/ontoprism/vocab#PrimarySite> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C3> ] .\n"
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> <urn:external-axis> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C4> ] .\n"
        "<urn:unrelated> <urn:predicate> <urn:object> .\n"
    )
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()
    exclusion = _exclusion()

    observed = build_effective_artifact(
        source_artifact=source,
        destination=effective,
        exclusions=(exclusion,),
        **_proposal_build_kwargs(),  # type: ignore[arg-type]
    )

    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before
    assert observed.removed_pair_count == 1
    assert observed.removed_pairs[0].model_dump() == {
        "concept_code": "C1",
        "axis": "op:Morphology",
        "filler_code": "C2",
        "review_relations": ("grouping-disputed",),
        "effective_disposition": "removed-from-effective",
        "input_present": True,
        "output_present": False,
    }
    rendered = effective.read_text()
    assert "vocab#Morphology" not in rendered
    assert "vocab#PrimarySite" in rendered
    assert (
        observed.effective_artifact_identity
        == hashlib.sha256(effective.read_bytes()).hexdigest()
    )


@pytest.mark.unit
def test_effective_artifact_refuses_absent_ambiguous_or_existing_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    destination = tmp_path / "effective.ttl"
    source.write_text(
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        "<https://w3id.org/ontoprism/vocab#hasConstituent> "
        "[<https://w3id.org/ontoprism/vocab#axis> "
        "<https://w3id.org/ontoprism/vocab#Morphology> ; "
        "<https://w3id.org/ontoprism/vocab#filler> "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C2> ] .\n"
    )

    with pytest.raises(CorpusAcceptanceValidationError, match="disposition differs"):
        build_effective_artifact(
            source_artifact=source,
            destination=destination,
            exclusions=(_exclusion(filler="C9"),),
            **_proposal_build_kwargs(),  # type: ignore[arg-type]
        )
    with pytest.raises(CorpusAcceptanceValidationError, match="ambiguous"):
        build_effective_artifact(
            source_artifact=source,
            destination=destination,
            exclusions=(
                _exclusion(),
                _exclusion(relation="added-to-candidate"),
            ),
            **_proposal_build_kwargs(),  # type: ignore[arg-type]
        )
    destination.write_text("already present")
    with pytest.raises(CorpusAcceptanceValidationError, match="destination exists"):
        build_effective_artifact(
            source_artifact=source,
            destination=destination,
            exclusions=(_exclusion(),),
            **_proposal_build_kwargs(),  # type: ignore[arg-type]
        )


def _gate_paths(tmp_path: Path, *, passing: bool) -> dict[str, Path]:
    registry = json.loads((GOLDEN / "proposal-registry.json").read_text())
    payloads: dict[str, object] = {
        "primary_site_audit": {"cardinality_violations": [] if passing else ["C1"]},
        "machine_readiness": {
            "quality_target": {"meets_quality_target": passing},
            "semantic_gate": {
                "entries": (
                    [
                        {
                            "kind": "normalized-group-violation",
                            "status": "clear",
                        }
                    ]
                    if passing
                    else [None]
                )
            },
            "m1_6_improvement": {
                "precision_baseline": {"numerator": 80, "denominator": 106},
                "recall_baseline": {"numerator": 80, "denominator": 153},
                "precision_improved": passing,
                "recall_improved": passing,
                "status": "passed" if passing else "failed",
            },
            "metrics": {
                "exact_pair_precision": {
                    "fraction": {
                        "numerator": 81 if passing else 80,
                        "denominator": 106,
                        "value": (81 if passing else 80) / 106,
                    }
                },
                "exact_pair_recall": {
                    "fraction": {
                        "numerator": 81 if passing else 80,
                        "denominator": 153,
                        "value": (81 if passing else 80) / 153,
                    }
                },
            },
        },
        "gate_liveness": {
            "status": "passed" if passing else "failed",
            "observed_exit_code": 0 if passing else 1,
            "git_head": "a" * 40 if passing else "b" * 40,
        },
        "proposal_registry": registry,
        "current_evidence": {"schema_version": 1},
        "baseline": {"schema_version": 1},
    }
    paths: dict[str, Path] = {}
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload))
        paths[name] = path
    return paths


@pytest.mark.unit
@pytest.mark.parametrize("passing", [True, False])
def test_candidate_gates_derive_each_status_from_named_evidence(
    tmp_path: Path, *, passing: bool
) -> None:
    gates = corpus_acceptance_module._candidate_gates(
        _gate_paths(tmp_path, passing=passing),
        git_head="a" * 40,
        roundtrip_fidelity=None,
        proposal_delta=_empty_proposal_delta(),
    )

    expected = "passed" if passing else "failed"
    assert gates.primary_site_cardinality.status == expected
    assert gates.proposal_provenance.status == "passed"
    assert gates.projection_loss.status == "blocked"
    assert gates.residual.status == "blocked"
    assert gates.fidelity.status == "unavailable"
    assert gates.issue_274_detector.status == expected
    assert gates.m1_6_improvement.status == expected
    assert gates.gate_liveness.status == "passed"
    assert gates.verify_currency.status == ("passed" if passing else "blocked")


@pytest.mark.unit
def test_candidate_gates_fail_closed_at_each_short_circuit(tmp_path: Path) -> None:
    paths = _gate_paths(tmp_path, passing=True)
    readiness = json.loads(paths["machine_readiness"].read_text())
    paths["machine_readiness"].write_text(
        json.dumps(
            readiness
            | {
                "quality_target": None,
                "semantic_gate": {
                    "entries": [
                        {"kind": "other", "status": "clear"},
                        {
                            "kind": "normalized-group-violation",
                            "status": "blocked",
                        },
                    ]
                },
            }
        )
    )
    paths["gate_liveness"].write_text(
        json.dumps({"status": "passed", "observed_exit_code": 1, "git_head": "a" * 40})
    )

    first = corpus_acceptance_module._candidate_gates(
        paths,
        git_head="a" * 40,
        roundtrip_fidelity=None,
        proposal_delta=_empty_proposal_delta(),
    )
    assert first.fidelity.status == "unavailable"
    assert first.issue_274_detector.status == "failed"
    assert first.verify_currency.status == "blocked"

    paths["gate_liveness"].write_text(
        json.dumps({"status": "passed", "observed_exit_code": 0, "git_head": "b" * 40})
    )
    second = corpus_acceptance_module._candidate_gates(
        paths,
        git_head="a" * 40,
        roundtrip_fidelity=None,
        proposal_delta=_empty_proposal_delta(),
    )
    assert second.verify_currency.status == "blocked"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("serialized", "message"),
    [("not-json", "machine readiness is unreadable"), ("[]", "is not an object")],
)
def test_candidate_gates_refuse_malformed_source_evidence(
    tmp_path: Path, serialized: str, message: str
) -> None:
    paths = _gate_paths(tmp_path, passing=True)
    paths["machine_readiness"].write_text(serialized)

    with pytest.raises(CorpusAcceptanceValidationError, match=message):
        corpus_acceptance_module._candidate_gates(
            paths,
            git_head="a" * 40,
            roundtrip_fidelity=None,
            proposal_delta=_empty_proposal_delta(),
        )


@pytest.mark.unit
async def test_candidate_generator_fails_closed_when_certified_inputs_are_absent(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        CorpusAcceptanceValidationError, match="certified acceptance input is absent"
    ):
        await generate_c3262_acceptance_candidate(tmp_path)


@pytest.mark.unit
async def test_candidate_generator_refuses_invalid_git_head_after_all_inputs_exist(
    tmp_path: Path,
) -> None:
    for relative in corpus_acceptance_module._CERTIFIED_ACCEPTANCE_INPUTS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    with pytest.raises(CorpusAcceptanceValidationError, match="git head is invalid"):
        await generate_c3262_acceptance_candidate(tmp_path, git_head="not-a-git-head")
    with pytest.raises(SiblingStoreValidationError, match="unreadable NCIt sibling"):
        await generate_c3262_acceptance_candidate(tmp_path, git_head="a" * 40)


def _stub_candidate_input_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    baseline,  # type: ignore[no-untyped-def]
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
    classification,  # type: ignore[no-untyped-def]
    mismatch: str | None = None,
) -> dict[str, Path]:
    manifest_source = "f" * 64 if mismatch == "source" else baseline.source_identity
    manifest = NcitSiblingStoreManifest.model_construct(source_identity=manifest_source)
    selected_baseline = (
        baseline.model_copy(update={"scope_root": "C1"})
        if mismatch == "scope"
        else baseline
    )
    report_updates = {
        "report-run": {"new_run_id": "another-run"},
        "report-representation": {"new_representation_identity": "f" * 64},
        "report-source": {"source_identity": "f" * 64},
    }
    selected_report = report.model_copy(update=report_updates.get(mismatch or "", {}))
    qualification_identity = (
        "f" * 64
        if mismatch == "qualification"
        else report.comparator_qualification_identity
    )
    fanout_count = (
        baseline.worklist_count - 1 if mismatch == "fanout" else baseline.worklist_count
    )
    artifact_identity = (
        "f" * 64 if mismatch == "artifact" else baseline.artifact_identity
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "validate_ncit_sibling_manifest",
        lambda _path: manifest,
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "load_corpus_baseline",
        lambda _path: selected_baseline,
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "load_r101_conservation_report",
        lambda _path: selected_report,
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "_load_json_object",
        lambda _path, _label: {"qualification_identity": qualification_identity},
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "build_review_required_exclusions",
        lambda _packet, _rationale, _artifact: exclusions,
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "classify_corpus_delta",
        lambda _report, **_kwargs: classification,
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "load_proposal_registry",
        lambda _path: load_proposal_registry(GOLDEN / "proposal-registry.json"),
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "load_fanout_baseline",
        lambda *_args, **_kwargs: SimpleNamespace(scanned_concept_count=fanout_count),
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "_file_identity",
        lambda _path: artifact_identity,
    )
    return {
        name: Path(name)
        for name in (
            "source_manifest",
            "baseline",
            "artifact",
            "r101_report",
            "r101_qualification",
            "review_packet",
            "rationale",
            "policy",
            "proposal_registry",
            "fanout",
        )
    }


@pytest.mark.unit
def test_candidate_input_validation_accepts_every_exact_binding(
    monkeypatch: pytest.MonkeyPatch,
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
    classification,  # type: ignore[no-untyped-def]
) -> None:
    baseline = corpus_acceptance_module.load_corpus_baseline(
        GOLDEN / "neoplasm-current-corpus-baseline.json"
    )
    paths = _stub_candidate_input_boundaries(
        monkeypatch,
        baseline=baseline,
        report=report,
        exclusions=exclusions,
        classification=classification,
    )

    validated = corpus_acceptance_module._validate_candidate_inputs(paths)

    assert validated.baseline == baseline
    assert validated.report == report
    assert validated.classification == classification


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mismatch", "message"),
    [
        ("scope", "certified C3262 baseline differs"),
        ("source", "baseline source identity differs"),
        ("artifact", "certified artifact identity differs"),
        ("report-run", "R101 report run binding differs"),
        ("report-representation", "R101 report representation binding differs"),
        ("report-source", "R101 report source binding differs"),
        ("qualification", "R101 qualification binding differs"),
        ("fanout", "fanout baseline scope differs"),
    ],
)
def test_candidate_input_validation_refuses_each_cross_binding_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
    classification,  # type: ignore[no-untyped-def]
    mismatch: str,
    message: str,
) -> None:
    baseline = corpus_acceptance_module.load_corpus_baseline(
        GOLDEN / "neoplasm-current-corpus-baseline.json"
    )
    paths = _stub_candidate_input_boundaries(
        monkeypatch,
        baseline=baseline,
        report=report,
        exclusions=exclusions,
        classification=classification,
        mismatch=mismatch,
    )

    with pytest.raises(CorpusAcceptanceValidationError, match=message):
        corpus_acceptance_module._validate_candidate_inputs(paths)


@pytest.mark.unit
def test_existing_comparison_is_exhaustively_classified_without_causal_overclaim(
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    report = load_r101_conservation_report(
        GOLDEN / "neoplasm-r101-v5-conservation.json.gz"
    )
    result = _classify(report)

    assert result.structural_object_count == 2_097
    assert result.metadata_object_count == 38_648
    assert result.raw_row_count == 79_393
    assert len(result.classifications) == 40_745
    assert result.category_counts == {
        "evidence-bound-metadata-composite": 2_754,
        "group-identity-rebinding": 35_017,
        "semantic-routing-change": 877,
        "source-role-routed-projection": 2_097,
    }
    assert result.unexplained_blockers == ()
    assert result.causal_attribution == "prohibited"
    assert all(
        item.evidence_identities
        for item in result.classifications
        if item.category != "unexplained-blocker"
    )


@pytest.mark.unit
def test_total_classifier_refuses_duplicate_or_omitted_objects(
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    report = load_r101_conservation_report(
        GOLDEN / "neoplasm-r101-v5-conservation.json.gz"
    )
    result = _classify(report)
    payload = {
        **result.__dict__,
        "classifications": (*result.classifications, result.classifications[0]),
        "classification_identity": SHA,
    }

    with pytest.raises(ValidationError, match="exactly once"):
        type(result).model_validate(payload)

    same_length = result.model_dump()
    same_length["classifications"] = (
        result.classifications[0],
        result.classifications[0],
        *result.classifications[2:],
    )
    with pytest.raises(ValidationError, match="exactly once"):
        type(result).model_validate(same_length)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ("excluded", "source-role-routed-projection"),
        ("minted", "unexplained-blocker"),
        ("unexplained", "unexplained-blocker"),
        ("r101-linked", "r101-occurrence-linked-output-delta"),
    ],
)
def test_structural_classifier_preserves_distinct_evidence_shapes(
    report,  # type: ignore[no-untyped-def]
    shape: str,
    expected: str,
) -> None:
    evidence = report.non_r101_delta_evidence
    row = evidence.rows[0]
    classified = ()
    rows = (row,)
    if shape == "minted":
        rows = (row.model_copy(update={"filler_code": "MINT-781c8c8c6096"}),)
    elif shape == "unexplained":
        rows = (row.model_copy(update={"source_roles": ()}),)
    elif shape == "r101-linked":
        rows = ()
        classified = (
            ClassifiedNonR101Delta.model_construct(
                row=row,
                classification="r101-occurrence-linked-output-delta",
                r101_occurrence_ids=("1" * 64,),
            ),
        )
    shaped_evidence = evidence.model_copy(
        update={
            "rows": rows,
            "metadata_deltas": (),
            "classified_rows": classified,
            "raw_typed_delta_count": 1,
        }
    )
    shaped_report = report.model_copy(
        update={"non_r101_delta_evidence": shaped_evidence}
    )

    result = _classify(shaped_report)

    assert result.category_counts == {expected: 1}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changed_fields", "needs_review", "expected"),
    [
        (
            ("source_definition_ids",),
            True,
            "provenance-evidence-binding-refresh",
        ),
        (("needs_review",), True, "conservative-review-escalation"),
        (("needs_review",), False, "authority-required-review-clearance"),
    ],
)
def test_metadata_classifier_keeps_provenance_clearance_and_unknown_distinct(
    report,  # type: ignore[no-untyped-def]
    changed_fields: tuple[str, ...],
    needs_review: bool,
    expected: str,
) -> None:
    evidence = report.non_r101_delta_evidence
    delta = evidence.metadata_deltas[0]
    old = delta.old.model_copy(
        update={"change": "removed", "needs_review": not needs_review}
    )
    new_updates: dict[str, object] = {
        "change": "added",
        "needs_review": needs_review,
    }
    if changed_fields == ("source_definition_ids",):
        old = old.model_copy(update={"needs_review": needs_review})
        new_updates["source_definition_ids"] = tuple(
            sorted({*old.source_definition_ids, "f" * 64})
        )
    shaped_delta = NonR101MetadataDelta(
        old=old,
        new=old.model_copy(update=new_updates),
        changed_fields=changed_fields,  # type: ignore[arg-type]
    )
    shaped_evidence = evidence.model_copy(
        update={
            "rows": (),
            "metadata_deltas": (shaped_delta,),
            "classified_rows": (),
            "raw_typed_delta_count": 2,
        }
    )
    shaped_report = report.model_copy(
        update={"non_r101_delta_evidence": shaped_evidence}
    )

    result = _classify(shaped_report)

    assert result.category_counts == {expected: 1}


@pytest.mark.unit
def test_metadata_classifier_emits_ordered_evidence_bound_composite_rule(
    report,  # type: ignore[no-untyped-def]
) -> None:
    evidence = report.non_r101_delta_evidence
    delta = next(
        item for item in evidence.metadata_deltas if len(item.changed_fields) > 1
    )
    assert delta.old.source_definition_ids == delta.new.source_definition_ids
    assert delta.old.source_occurrence_ids == delta.new.source_occurrence_ids
    assert delta.old.source_roles == delta.new.source_roles
    shaped = evidence.model_copy(
        update={
            "rows": (),
            "metadata_deltas": (delta,),
            "classified_rows": (),
            "raw_typed_delta_count": 2,
        }
    )

    result = _classify(report.model_copy(update={"non_r101_delta_evidence": shaped}))

    item = result.classifications[0]
    assert item.category == "evidence-bound-metadata-composite"
    assert item.rule_name == (
        "metadata-composite:group-identity-rebinding+semantic-routing-change-v1"
    )
    assert item.evidence_identities


@pytest.mark.unit
def test_structural_classifier_requires_complete_source_role_evidence(
    report,  # type: ignore[no-untyped-def]
) -> None:
    evidence = report.non_r101_delta_evidence
    row = evidence.rows[0].model_copy(update={"source_roles": ()})
    shaped = evidence.model_copy(
        update={
            "rows": (row,),
            "metadata_deltas": (),
            "classified_rows": (),
            "raw_typed_delta_count": 1,
        }
    )

    result = _classify(report.model_copy(update={"non_r101_delta_evidence": shaped}))

    blocker = result.unexplained_blockers[0]
    assert blocker.category == "missing-source-role-evidence"
    assert blocker.reason == "structural row lacks source roles"
    assert blocker.row_key == (
        row.change,
        row.concept_code,
        row.axis,
        row.filler_code,
    )


@pytest.mark.unit
def test_exclusion_evidence_stays_on_effective_view_not_metadata_classification(
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    evidence = report.non_r101_delta_evidence
    delta = evidence.metadata_deltas[0]
    exclusion = exclusions[0].model_copy(
        update={
            "concept_code": delta.new.concept_code,
            "pair_changes": (
                exclusions[0]
                .pair_changes[0]
                .model_copy(
                    update={
                        "axis": delta.new.axis,
                        "filler_code": delta.new.filler_code,
                    }
                ),
            ),
        }
    )
    shaped_evidence = evidence.model_copy(
        update={
            "rows": (),
            "metadata_deltas": (delta,),
            "classified_rows": (),
            "raw_typed_delta_count": 2,
        }
    )
    shaped_report = report.model_copy(
        update={"non_r101_delta_evidence": shaped_evidence}
    )

    result = _classify(shaped_report)

    assert result.category_counts == {"group-identity-rebinding": 1}
    assert not set(
        exclusion.pair_changes[0].historical_evidence_identities
    ).intersection(result.classifications[0].evidence_identities)


@pytest.mark.unit
def test_exclusion_evidence_does_not_leak_to_another_pair_for_same_concept(
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    evidence = report.non_r101_delta_evidence
    delta = evidence.metadata_deltas[0]
    exclusion = exclusions[0].model_copy(
        update={
            "concept_code": delta.new.concept_code,
            "pair_changes": (
                exclusions[0]
                .pair_changes[0]
                .model_copy(update={"axis": "op:AnotherAxis", "filler_code": "C1"}),
            ),
        }
    )
    shaped = evidence.model_copy(
        update={
            "rows": (),
            "metadata_deltas": (delta,),
            "classified_rows": (),
            "raw_typed_delta_count": 2,
        }
    )

    result = _classify(report.model_copy(update={"non_r101_delta_evidence": shaped}))

    assert result.classifications[0].category != ("review-required-effective-exclusion")
    assert not set(result.classifications[0].evidence_identities).intersection(
        exclusion.pair_changes[0].historical_evidence_identities
    )


@pytest.mark.unit
def test_every_classification_binds_a_named_rule_evidence_and_source(
    classification,  # type: ignore[no-untyped-def]
) -> None:
    assert all(item.rule_name for item in classification.classifications)
    assert all(item.evidence_identities for item in classification.classifications)
    assert all(item.source_identities for item in classification.classifications)


@pytest.mark.unit
@pytest.mark.parametrize("changed_fields", [(), ("not-a-real-field",)])
def test_metadata_classifier_fails_closed_for_invalid_changed_fields(
    report,  # type: ignore[no-untyped-def]
    changed_fields: tuple[str, ...],
) -> None:
    evidence = report.non_r101_delta_evidence
    valid = evidence.metadata_deltas[0]
    invalid = NonR101MetadataDelta.model_construct(
        old=valid.old,
        new=valid.new,
        changed_fields=changed_fields,
    )
    shaped = evidence.model_copy(
        update={
            "rows": (),
            "metadata_deltas": (invalid,),
            "classified_rows": (),
            "raw_typed_delta_count": 2,
        }
    )

    with pytest.raises(
        CorpusAcceptanceValidationError, match="metadata changed fields"
    ):
        _classify(
            report.model_copy(update={"non_r101_delta_evidence": shaped}),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("raw_row_count", 79_392, "raw changed-row arithmetic differs"),
        ("category_counts", {}, "classification category counts differ"),
        (
            "unexplained_blockers",
            (
                {
                    "category": "missing-source-role-evidence",
                    "reason": "not the derived inventory",
                    "row_key": ("added", "C1", "op:CellType", "C2"),
                },
            ),
            "unexplained blocker inventory differs",
        ),
        ("classification_identity", SHA, "classification identity differs"),
    ],
)
def test_total_classifier_refuses_inconsistent_partition_evidence(
    classification,  # type: ignore[no-untyped-def]
    field: str,
    value: object,
    message: str,
) -> None:
    payload = classification.model_dump()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        type(classification).model_validate(payload)


@pytest.mark.unit
def test_review_required_exclusion_refuses_noncanonical_pair_or_evidence() -> None:
    common = _exclusion(filler="C1").model_dump()
    pair = common["pair_changes"][0]
    with pytest.raises(ValidationError, match="canonical and unique"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"pair_changes": (pair, pair)}
        )
    invalid_source = dict(pair)
    invalid_source["source_assertion_identities"] = ("not-a-digest",)
    with pytest.raises(ValidationError, match="source assertion identities"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"pair_changes": (invalid_source,)}
        )
    invalid_history = dict(pair)
    invalid_history["historical_evidence_identities"] = ("2" * 64, "2" * 64)
    with pytest.raises(ValidationError, match="historical evidence identities"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"pair_changes": (invalid_history,)}
        )


def _dry_run(candidate_content_identity: str = SHA) -> PublicationDryRunEvidence:
    payload = {
        "schema_version": 1,
        "status": "passed",
        "candidate_content_identity": candidate_content_identity,
        "predecessor_marker_identity": "b" * 64,
        "destination_graph_iri": "https://example.org/effective",
        "artifact_identity": "c" * 64,
        "run_id": "run-1",
        "expected_concept_count": 15_633,
        "represented_concept_count": 14_884,
        "marker_protocol_identity": "d" * 64,
        "recovery_identity": "e" * 64,
        "postgres_read_verified": True,
        "qlever_read_verified": True,
        "postgres_before_identity": "1" * 64,
        "postgres_after_identity": "1" * 64,
        "qlever_before_identity": "2" * 64,
        "qlever_after_identity": "2" * 64,
        "recoverability_status": "passed",
        "publication_writes_performed": False,
    }
    return PublicationDryRunEvidence.model_validate(
        {
            **payload,
            "evidence_identity": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    )


def _content_payload(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> dict[str, object]:
    r101 = {
        "occurrence_count": 43_414,
        "routed_or_collapsed_or_suppressed_count": 43_414,
        "unresolved_count": 0,
        "causal_attribution": "prohibited",
    }
    r101["occurrence_partition_identity"] = _identity(r101)
    return {
        "schema_version": 1,
        "scope": {
            "root": "C3262",
            "version": "stated-genus-subclass-v1",
            "worklist_count": 15_633,
            "worklist_identity": "1" * 64,
        },
        "source": {
            "release": "26.07d",
            "source_identity": "2" * 64,
            "stated_artifact_identity": "3" * 64,
            "inferred_artifact_identity": "4" * 64,
            "sibling_manifest_identity": "5" * 64,
            "extraction_plane": "official-stated",
        },
        "execution": {
            "run_id": "run-1",
            "run_fingerprint_identity": "6" * 64,
            "git_head": "7" * 40,
            "policy_identity": "8" * 64,
        },
        "projection": {
            "artifact_sha256": "9" * 64,
            "representation_identity": "9" * 64,
            "served_semantic_projection_identity": "9" * 64,
            "no_equivalence": True,
        },
        "metrics": {
            "worklist_count": 15_633,
            "decomposed_count": 14_884,
            "atomic_noop_count": 606,
            "residual_count": 1,
            "residual_concept_codes": ("C1",),
            "semantic_excluded_count": 3,
            "unknown_outcome_count": 139,
            "source_occurrence_count": 370_253,
            "selected_occurrence_count": 108_217,
            "emitted_constituent_pair_count": 144_231,
            "complete_fact_count": 845_825,
            "projected_fact_count": 144_231,
            "projection_loss_count": 701_594,
            "projection_loss_rate": 701_594 / 845_825,
            "residual_precoordinated_count": 1,
            "residual_unknown_count": 0,
            "residual_unknown_rate": 0.0,
            "roundtrip_fidelity": None,
            "exact_pair_precision": 111 / 132,
            "exact_pair_recall": 111 / 153,
            "common_pair_partition_agreement": 13 / 18,
            "full_partition_agreement": 3 / 20,
            "sme_include_rate": 48 / 106,
            "minted_count": 2_649,
        },
        "gates": {
            name: {
                "status": "passed",
                "evidence_identity": _identity(name),
                "observation_identity": _identity({"gate": name, "result": "passed"}),
            }
            for name in (
                "primary_site_cardinality",
                "proposal_provenance",
                "projection_loss",
                "residual",
                "fidelity",
                "issue_274_detector",
                "m1_6_improvement",
                "gate_liveness",
                "verify_currency",
            )
        },
        "r101_summary": r101,
        "evidence": {
            "corpus_baseline_identity": "a" * 64,
            "source_artifact_identity": "b" * 64,
            "effective_artifact_evidence_identity": "c" * 64,
            "policy_identity": "d" * 64,
            "detector_identity": "e" * 64,
            "r101_report_identity": "f" * 64,
            "r101_qualification_identity": "1" * 64,
            "primary_site_audit_identity": "2" * 64,
            "proposal_registry_identity": "3" * 64,
            "proposal_registry_migration_identity": "0" * 64,
            "review_packet_identity": "4" * 64,
            "review_decisions_identity": "5" * 64,
            "gate_liveness_evidence_identity": "6" * 64,
            "old_comparator_artifact_identity": "7" * 64,
            "new_comparator_artifact_identity": "8" * 64,
        },
        "proposal_delta": _empty_proposal_delta(),
        "delta_classification": classification,
        "review_required_exclusions": exclusions,
    }


def _content(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> CorpusAcceptanceContent:
    payload = _content_payload(classification, exclusions)
    return CorpusAcceptanceContent.model_validate(
        {**payload, "content_identity": _identity(payload)}
    )


def _publication_run(**changes: object) -> CompletedRunForEvidence:
    fingerprint_values: dict[str, object] = {
        "source_identity": SHA,
        "collapse_policy_identity": "0" * 64,
        "routing_implementation_identity": "1" * 64,
        "mixed_chain_inventory_identity": "2" * 64,
        "stage_sequence_identity": RUN_STAGE_SEQUENCE_IDENTITY,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": branch_spec(DecompositionBranch.NEOPLASM).semantic_types,
        "worklist": ("C1",),
        "total_limit": None,
        "sample_manifest_identity": None,
        "algorithm_version": "decomposition-v5",
        "config_version": "nested-definition-v2",
        "walker_max_depth": 5,
        "output_mode": "file",
        "load_mode": "named-graph",
        "emitted_at": datetime(2026, 9, 16, tzinfo=UTC),
    }
    fingerprint_values.update(cast("dict[str, object]", changes.pop("fingerprint", {})))
    values: dict[str, object] = {
        "run_id": "run-1",
        "ncit_version": "26.07d",
        "fingerprint": RunFingerprint.model_validate(fingerprint_values),
        "representation_identity": SHA,
        "publication_artifact_path": "/not-used",
    }
    values.update(changes)
    return CompletedRunForEvidence.model_validate(values)


def _planes(effective: str = SHA, official: str = SHA) -> PublicationPlaneBinding:
    return PublicationPlaneBinding(
        official_persisted_representation_identity=official,
        effective_representation_identity=effective,
    )


class _PublicationStore:
    def __init__(self, run: CompletedRunForEvidence) -> None:
        self.run = run

    async def completed_run_for_evidence(self, run_id: str) -> CompletedRunForEvidence:
        assert run_id == self.run.run_id
        return self.run


@pytest.mark.unit
def test_publication_authorization_requires_exact_accepted_human_decision() -> None:
    dry_run = _dry_run()
    pending = PendingHumanAcceptanceDecision(
        status="not-requested",
        candidate_identity=SHA,
        publication_dry_run_identity=dry_run.evidence_identity,
    )
    with pytest.raises(
        CorpusAcceptanceValidationError, match="accepted human decision"
    ):
        require_publication_authorization(
            candidate_identity=SHA,
            dry_run=dry_run,
            decision=pending,
            attestation_artifact=Path("not-read-for-pending-decision"),
        )


@pytest.mark.unit
def test_pending_human_decision_file_roundtrips_without_approval(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decision.json"
    dry_run = _dry_run()
    written = write_pending_human_acceptance_decision(
        path,
        candidate_identity=SHA,
        publication_dry_run_identity=dry_run.evidence_identity,
    )

    assert written.status == "not-requested"
    assert load_human_acceptance_decision(path) == written
    assert json.loads(path.read_text())["status"] == "not-requested"


@pytest.mark.unit
def test_accepted_metadata_writer_requires_authorization_and_emits_api_contract(
    tmp_path: Path,
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    source = tmp_path / "effective.ttl"
    output = tmp_path / "accepted.ttl"
    attestation = tmp_path / "independent-attestation.json"
    attestation.write_text('{"authority":"Dr Example","decision":"accepted"}\n')
    attestation_identity = hashlib.sha256(attestation.read_bytes()).hexdigest()
    source.write_text(
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        f'<{vocab.REPRESENTATION_STATUS}> "{vocab.LEGACY_PRECOORDINATED}" .\n'
    )
    content = _content(classification, exclusions)
    dry_run = _dry_run(content.content_identity)
    pending = PendingHumanAcceptanceDecision(
        status="not-requested",
        candidate_identity=SHA,
        publication_dry_run_identity=dry_run.evidence_identity,
    )
    with pytest.raises(
        CorpusAcceptanceValidationError, match="accepted human decision"
    ):
        build_accepted_publication_artifact(
            source_artifact=source,
            destination=output,
            candidate_identity=SHA,
            dry_run=dry_run,
            decision=pending,
            attestation_artifact=attestation,
            source_release="26.07d",
            source_identity="1" * 64,
            run_id="run-1",
            representation_identity="2" * 64,
            publication_identity="3" * 64,
            exclusions=(),
        )

    accepted = AcceptedHumanAcceptanceDecision(
        status="accepted",
        candidate_identity=SHA,
        publication_dry_run_identity=dry_run.evidence_identity,
        accountable_authority="Dr Example",
        decided_at=datetime(2026, 9, 16, tzinfo=UTC),
        attestation_artifact_identity=attestation_identity,
        decision_evidence_identity="4" * 64,
    )
    build_accepted_publication_artifact(
        source_artifact=source,
        destination=output,
        candidate_identity=SHA,
        dry_run=dry_run,
        decision=accepted,
        attestation_artifact=attestation,
        source_release="26.07d",
        source_identity="1" * 64,
        run_id="run-1",
        representation_identity="2" * 64,
        publication_identity="3" * 64,
        exclusions=(),
    )
    graph = Graph().parse(output, format="turtle")
    subject = URIRef("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1")
    assert set(graph.objects(subject, URIRef(vocab.ACCEPTANCE_STATUS))) == {
        RdfLiteral("accepted-effective")
    }
    assert set(graph.objects(subject, URIRef(vocab.ACCEPTANCE_PUBLICATION))) == {
        RdfLiteral("3" * 64)
    }

    with pytest.raises(CorpusAcceptanceValidationError, match="destination exists"):
        build_accepted_publication_artifact(
            source_artifact=source,
            destination=output,
            candidate_identity=SHA,
            dry_run=dry_run,
            decision=accepted,
            attestation_artifact=attestation,
            source_release="26.07d",
            source_identity="1" * 64,
            run_id="run-1",
            representation_identity="2" * 64,
            publication_identity="3" * 64,
            exclusions=(),
        )
    malformed = tmp_path / "malformed.ttl"
    malformed.write_text("not Turtle")
    with pytest.raises(CorpusAcceptanceValidationError, match="not valid Turtle"):
        build_accepted_publication_artifact(
            source_artifact=malformed,
            destination=tmp_path / "malformed-output.ttl",
            candidate_identity=SHA,
            dry_run=dry_run,
            decision=accepted,
            attestation_artifact=attestation,
            source_release="26.07d",
            source_identity="1" * 64,
            run_id="run-1",
            representation_identity="2" * 64,
            publication_identity="3" * 64,
            exclusions=(),
        )

    accepted = AcceptedHumanAcceptanceDecision(
        status="accepted",
        candidate_identity=SHA,
        publication_dry_run_identity=dry_run.evidence_identity,
        accountable_authority="Dr Example",
        decided_at=datetime(2026, 9, 16, tzinfo=UTC),
        attestation_artifact_identity=attestation_identity,
        decision_evidence_identity="1" * 64,
    )
    require_publication_authorization(
        candidate_identity=SHA,
        dry_run=dry_run,
        decision=accepted,
        attestation_artifact=attestation,
    )

    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        require_publication_authorization(
            candidate_identity="2" * 64,
            dry_run=dry_run,
            decision=accepted,
            attestation_artifact=attestation,
        )
    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        require_publication_authorization(
            candidate_identity=SHA,
            dry_run=dry_run,
            decision=accepted.model_copy(
                update={"publication_dry_run_identity": "2" * 64}
            ),
            attestation_artifact=attestation,
        )
    blocked_payload = dry_run.model_dump()
    blocked_payload.update(status="blocked", postgres_read_verified=False)
    blocked_payload["evidence_identity"] = _identity(
        {
            key: value
            for key, value in blocked_payload.items()
            if key != "evidence_identity"
        }
    )
    blocked = PublicationDryRunEvidence.model_validate(blocked_payload)
    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        require_publication_authorization(
            candidate_identity=SHA,
            dry_run=blocked,
            decision=accepted.model_copy(
                update={"publication_dry_run_identity": blocked.evidence_identity}
            ),
            attestation_artifact=attestation,
        )


@pytest.mark.unit
def test_publication_dry_run_evidence_refuses_false_status_or_identity() -> None:
    passed = _dry_run()
    payload = passed.model_dump()
    with pytest.raises(ValidationError, match="status differs"):
        PublicationDryRunEvidence.model_validate(payload | {"status": "blocked"})
    with pytest.raises(ValidationError, match="identity differs"):
        PublicationDryRunEvidence.model_validate(
            payload | {"evidence_identity": "f" * 64}
        )

    blocked_payload = payload | {
        "status": "blocked",
        "postgres_read_verified": False,
        "qlever_read_verified": True,
    }
    blocked_payload["evidence_identity"] = _identity(
        {
            key: value
            for key, value in blocked_payload.items()
            if key != "evidence_identity"
        }
    )
    blocked = PublicationDryRunEvidence.model_validate(blocked_payload)
    assert blocked.status == "blocked"

    qlever_blocked = blocked_payload | {
        "postgres_read_verified": True,
        "qlever_read_verified": False,
    }
    qlever_blocked["evidence_identity"] = _identity(
        {
            key: value
            for key, value in qlever_blocked.items()
            if key != "evidence_identity"
        }
    )
    assert PublicationDryRunEvidence.model_validate(qlever_blocked).status == "blocked"


@pytest.mark.unit
async def test_publication_dry_run_refuses_another_destination_before_reads() -> None:
    with pytest.raises(CorpusAcceptanceValidationError, match="destination differs"):
        await dry_run_corpus_publication(
            candidate_content_identity=SHA,
            run_id="run-1",
            source_identity=SHA,
            planes=_planes(),
            artifact=GOLDEN / "neoplasm-current-corpus-baseline.json",
            destination_graph_iri="https://example.org/wrong",
            expected_codes=(),
            expected_worklist_count=0,
            graph=object(),  # type: ignore[arg-type]
            provenance=object(),  # type: ignore[arg-type]
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("run", "source_identity", "representation_identity", "worklist_count"),
    [
        (_publication_run(), "b" * 64, SHA, 1),
        (_publication_run(representation_identity="b" * 64), SHA, SHA, 1),
        (_publication_run(), SHA, SHA, 2),
    ],
)
async def test_publication_dry_run_refuses_each_persisted_run_binding_mismatch(
    run: CompletedRunForEvidence,
    source_identity: str,
    representation_identity: str,
    worklist_count: int,
) -> None:
    with pytest.raises(CorpusAcceptanceValidationError, match="run binding differs"):
        await dry_run_corpus_publication(
            candidate_content_identity=SHA,
            run_id="run-1",
            source_identity=source_identity,
            planes=_planes(official=representation_identity),
            artifact=GOLDEN / "neoplasm-current-corpus-baseline.json",
            destination_graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
            expected_codes=("C1",),
            expected_worklist_count=worklist_count,
            graph=object(),  # type: ignore[arg-type]
            provenance=_PublicationStore(run),
        )


@pytest.mark.unit
@pytest.mark.parametrize("artifact_kind", ["malformed", "identity-mismatch"])
async def test_publication_dry_run_refuses_invalid_or_unbound_artifact(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    artifact = tmp_path / "candidate.ttl"
    artifact.write_text("not turtle" if artifact_kind == "malformed" else "")
    message = (
        "not valid Turtle"
        if artifact_kind == "malformed"
        else "artifact representation identity differs"
    )

    with pytest.raises(CorpusAcceptanceValidationError, match=message):
        await dry_run_corpus_publication(
            candidate_content_identity=SHA,
            run_id="run-1",
            source_identity=SHA,
            planes=_planes(),
            artifact=artifact,
            destination_graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
            expected_codes=(),
            expected_worklist_count=1,
            graph=object(),  # type: ignore[arg-type]
            provenance=_PublicationStore(_publication_run()),
        )


@pytest.mark.unit
@pytest.mark.parametrize("gate", ["projection_loss", "residual", "fidelity"])
def test_report_only_metric_gate_failure_is_represented_without_blocking_candidate(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
    gate: str,
) -> None:
    payload = _content_payload(classification, exclusions)
    gates = cast("dict[str, object]", payload["gates"]).copy()
    gates[gate] = {
        "status": "failed",
        "evidence_identity": _identity(gate),
        "observation_identity": _identity({"gate": gate, "result": "failed"}),
    }
    payload["gates"] = gates
    content = CorpusAcceptanceContent.model_validate(
        {**payload, "content_identity": _identity(payload)}
    )

    assert isinstance(getattr(content.gates, gate), GateEvaluation)
    candidate = finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )
    assert candidate.status == "ready-for-human-authorization"


@pytest.mark.unit
def test_candidate_metrics_validate_projection_and_unknown_arithmetic() -> None:
    metrics = CandidateMetrics.model_validate(
        {
            "worklist_count": 10,
            "decomposed_count": 6,
            "atomic_noop_count": 1,
            "residual_count": 1,
            "residual_concept_codes": ("C1",),
            "semantic_excluded_count": 1,
            "unknown_outcome_count": 1,
            "source_occurrence_count": 20,
            "selected_occurrence_count": 10,
            "emitted_constituent_pair_count": 7,
            "complete_fact_count": 10,
            "projected_fact_count": 7,
            "projection_loss_count": 3,
            "projection_loss_rate": 0.3,
            "residual_precoordinated_count": 1,
            "residual_unknown_count": 0,
            "residual_unknown_rate": 0.0,
            "roundtrip_fidelity": None,
            "exact_pair_precision": 0.8,
            "exact_pair_recall": 0.6,
            "common_pair_partition_agreement": 0.5,
            "full_partition_agreement": 0.25,
            "sme_include_rate": 0.4,
            "minted_count": 2,
        }
    )
    assert metrics.roundtrip_fidelity is None
    for field, value in (
        ("projection_loss_count", 2),
        ("projection_loss_rate", 0.2),
        ("unknown_outcome_count", 2),
    ):
        with pytest.raises(ValidationError):
            CandidateMetrics.model_validate(metrics.model_dump() | {field: value})


@pytest.mark.unit
def test_unavailable_roundtrip_is_report_only_and_does_not_block_candidate(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    payload = _content_payload(classification, exclusions)
    gates = cast("dict[str, object]", payload["gates"]).copy()
    fidelity = cast("dict[str, object]", gates["fidelity"]).copy()
    fidelity["status"] = "unavailable"
    gates["fidelity"] = fidelity
    payload["gates"] = gates
    content = CorpusAcceptanceContent.model_validate(
        {**payload, "content_identity": _identity(payload)}
    )

    candidate = finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )

    assert candidate.gates.fidelity.status == "unavailable"
    assert candidate.status == "ready-for-human-authorization"


@pytest.mark.unit
def test_candidate_content_refuses_unbound_r101_partition(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    payload = _content_payload(classification, exclusions)
    r101 = cast("dict[str, object]", payload["r101_summary"]).copy()
    r101["occurrence_partition_identity"] = "f" * 64
    payload["r101_summary"] = r101

    with pytest.raises(ValidationError, match="R101 occurrence partition identity"):
        CorpusAcceptanceContent.model_validate(
            {**payload, "content_identity": _identity(payload)}
        )


@pytest.mark.unit
def test_candidate_content_refuses_changed_content_identity(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    payload = _content_payload(classification, exclusions)

    with pytest.raises(ValidationError, match="candidate content identity differs"):
        CorpusAcceptanceContent.model_validate(
            {**payload, "content_identity": "f" * 64}
        )


@pytest.mark.unit
def test_candidate_finalization_is_ready_without_requesting_human(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    content = _content(classification, exclusions)
    candidate = finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )

    assert isinstance(candidate, CorpusAcceptanceCandidate)
    assert candidate.status == "ready-for-human-authorization"
    assert candidate.human_authorization.status == "not-requested"
    assert candidate.candidate_content_identity == content.content_identity
    assert candidate.publication_dry_run.publication_writes_performed is False


@pytest.mark.unit
def test_candidate_finalization_refuses_dry_run_for_other_content(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    content = _content(classification, exclusions)

    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        finalize_corpus_acceptance_candidate(content, _dry_run())


@pytest.mark.unit
def test_failed_dry_run_keeps_candidate_machine_blocked(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    content = _content(classification, exclusions)
    dry_run_payload = _dry_run(content.content_identity).model_dump()
    dry_run_payload.update(
        status="blocked",
        postgres_read_verified=False,
    )
    dry_run_payload["evidence_identity"] = _identity(
        {
            key: value
            for key, value in dry_run_payload.items()
            if key != "evidence_identity"
        }
    )

    candidate = finalize_corpus_acceptance_candidate(
        content, PublicationDryRunEvidence.model_validate(dry_run_payload)
    )

    assert candidate.status == "machine-blocked"


@pytest.mark.unit
def test_unexplained_delta_keeps_candidate_machine_blocked(
    report,  # type: ignore[no-untyped-def]
) -> None:
    evidence = report.non_r101_delta_evidence
    unexplained = evidence.rows[0].model_copy(update={"source_roles": ()})
    shaped_evidence = evidence.model_copy(
        update={
            "rows": (unexplained,),
            "metadata_deltas": (),
            "classified_rows": (),
            "raw_typed_delta_count": 1,
        }
    )
    classification = _classify(
        report.model_copy(update={"non_r101_delta_evidence": shaped_evidence})
    )
    content = _content(classification, ())

    candidate = finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )

    assert classification.unexplained_blockers
    assert candidate.status == "machine-blocked"


@pytest.mark.unit
def test_candidate_refuses_dry_run_bound_to_other_content(
    candidate,  # type: ignore[no-untyped-def]
) -> None:
    payload = candidate.model_dump()
    dry_run = candidate.publication_dry_run.model_dump()
    dry_run["candidate_content_identity"] = "f" * 64
    dry_run["evidence_identity"] = _identity(
        {key: value for key, value in dry_run.items() if key != "evidence_identity"}
    )
    payload["publication_dry_run"] = dry_run

    with pytest.raises(ValidationError, match="dry-run candidate binding differs"):
        CorpusAcceptanceCandidate.model_validate(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "machine-blocked", "candidate status differs"),
        (
            "candidate_content_identity",
            "f" * 64,
            "candidate content identity differs",
        ),
        ("candidate_identity", "f" * 64, "candidate identity differs"),
    ],
)
def test_candidate_refuses_inconsistent_bound_identity(
    candidate,  # type: ignore[no-untyped-def]
    field: str,
    value: object,
    message: str,
) -> None:
    payload = candidate.model_dump()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        CorpusAcceptanceCandidate.model_validate(payload)
