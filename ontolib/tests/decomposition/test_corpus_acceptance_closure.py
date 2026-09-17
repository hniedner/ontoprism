"""End-to-end contracts for evidence-closed C3262 acceptance."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF

from ontolib.decomposition import vocab
from ontolib.decomposition.corpus_acceptance import (
    AssertionEvidenceClosure,
    CertifiedSourceBinding,
    ConceptExclusionDisposition,
    CorpusAcceptanceValidationError,
    EvidenceAmbiguityReport,
    EvidenceGapInventory,
    HumanEvidenceAcceptance,
    MachineEvidenceAcceptance,
    PersistedAssertionAssessment,
    PersistedAssertionEvidence,
    PersistedEvidenceEvaluation,
    RejectedHumanEvidenceAcceptance,
    build_assertion_evidence_closure,
    build_concept_exclusion_dispositions,
    build_machine_acceptance_metadata_artifact,
    build_machine_evidence_acceptance,
    build_r101_occurrence_closure,
    evaluate_persisted_assertion_evidence,
    issue_machine_publication_token,
    require_machine_publication_authorization,
)
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.decomposition.provenance_models import (
    ResidualFillerClassification,
    WorkItemOutcome,
)
from ontolib.decomposition.publication import (
    AcceptancePublicationIntent,
    AcceptancePublicationReceipt,
    PublicationMarker,
    PublicationPreflightError,
    publish_machine_accepted_artifact,
)
from ontolib.decomposition.r101_conservation import (
    R101ConservationReport,
    load_r101_conservation_report,
)
from ontolib.terminologies.namespaces import NCIT_NS

SHA = "a" * 64
SOURCE_IDENTITY = "b" * 64
STATED_IDENTITY = "c" * 64
POLICY_IDENTITY = "d" * 64


@pytest.fixture(scope="module")
def report() -> R101ConservationReport:
    return load_r101_conservation_report(
        Path(__file__).with_name("golden") / "neoplasm-r101-v5-conservation.json.gz"
    )


def _constituent(
    concept: str,
    axis: str,
    filler: str,
    fact: str,
    *,
    needs_review: bool = False,
    axis_source: str = "role",
) -> str:
    review = f" ; <{vocab.NEEDS_REVIEW}> true" if needs_review else ""
    role = (
        f"<{vocab.SOURCE_ROLE}> "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#R101> ; "
        if axis_source == "role"
        else ""
    )
    return (
        f"<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{concept}> "
        f"<{vocab.HAS_CONSTITUENT}> [<{vocab.AXIS}> "
        f"<{vocab.ONTOPRISM_NS}{axis}> ; <{vocab.FILLER}> "
        f"<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{filler}> ; "
        f'<{vocab.AXIS_SOURCE}> "{axis_source}" ; '
        f"{role}"
        f"<{vocab.SOURCE_DEFINITION_FACT}> <{vocab.DEFINITION_FACT_NS}{fact}>"
        f"{review} ] .\n"
    )


def _source() -> CertifiedSourceBinding:
    return CertifiedSourceBinding(
        release="26.07d",
        source_manifest_identity=SHA,
        source_identity=SOURCE_IDENTITY,
        stated_artifact_identity=STATED_IDENTITY,
        certification="expert-curated-ncit-release",
    )


def _persisted(
    concept: str, axis: str, filler: str, fact: str, occurrence: str
) -> PersistedAssertionEvidence:
    return PersistedAssertionEvidence(
        concept_code=concept,
        axis=f"op:{axis}",
        filler_code=filler,
        axis_source="role",
        source_fact_ids=(fact,),
        source_occurrence_ids=(occurrence,),
        occurrence_availability="available",
        complete_definition_identity=None,
        transformation_rule="axis-contract-routing-v1",
        transformation_policy_identity=POLICY_IDENTITY,
        applicability_identity="e" * 64,
    )


def _evaluation(
    *evidence: PersistedAssertionEvidence,
) -> PersistedEvidenceEvaluation:
    return PersistedEvidenceEvaluation.build(
        policy_identity=POLICY_IDENTITY,
        assessments=tuple(
            PersistedAssertionAssessment(
                concept_code=item.concept_code,
                axis=item.axis,
                filler_code=item.filler_code,
                axis_source=item.axis_source,
                source_fact_ids=item.source_fact_ids,
                source_occurrence_ids=item.source_occurrence_ids,
                occurrence_availability=item.occurrence_availability,
                complete_definition_identity=item.complete_definition_identity,
                source_roles=("R101",),
                transformation_rule=item.transformation_rule,
                transformation_policy_identity=(item.transformation_policy_identity),
                applicability_identity=item.applicability_identity,
                gap_reasons=(),
                evidence=item,
            )
            for item in evidence
        ),
    )


@pytest.mark.unit
def test_assertion_closure_cross_checks_parsers_and_keeps_diagnostic_review(
    tmp_path: Path,
) -> None:
    """Bind canonical included, excluded, evidence, and source identities."""
    source = tmp_path / "source.ttl"
    effective = tmp_path / "effective.ttl"
    source.write_text(
        _constituent("C1", "PrimarySite", "C2", "1" * 64)
        + _constituent("C3", "PrimarySite", "C4", "2" * 64, needs_review=True)
    )

    closure = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=effective,
        source=_source(),
        persisted_evidence=_evaluation(
            _persisted("C1", "PrimarySite", "C2", "1" * 64, "3" * 64),
            _persisted("C3", "PrimarySite", "C4", "2" * 64, "4" * 64),
        ),
        concept_exclusions=(),
    )

    assert len(closure.included_assertion_closure) == 2
    assert closure.excluded_assertion_closure == ()
    assert len(closure.evidence_ledger) == 2
    included = closure.included_assertion_closure[0]
    assert (included.concept_code, included.axis, included.filler_code) == (
        "C1",
        "op:PrimarySite",
        "C2",
    )
    assert closure.evidence_ledger[0].assertion_identity == included.assertion_identity
    assert closure.evidence_ledger[0].source == _source()
    assert closure.evidence_ledger[0].source_fact_ids == ("1" * 64,)
    assert closure.evidence_ledger[0].source_occurrence_ids == ("3" * 64,)
    reviewed = next(
        item for item in closure.included_assertion_closure if item.needs_review
    )
    assert reviewed.review_cause == "engine-diagnostic"
    assert closure.evidence_gap_inventory.gaps == ()
    assert closure.unresolved_included_assertion_ids == ()
    assert "C3" in effective.read_text()
    assert hashlib.sha256(effective.read_bytes()).hexdigest() == (
        closure.effective_artifact_identity
    )
    assert (
        closure.rdf_parser_inventory_identity == closure.fast_parser_inventory_identity
    )


@pytest.mark.unit
def test_all_independent_evidence_gaps_are_pair_scoped_in_one_pass(
    tmp_path: Path,
) -> None:
    """Collect every gap while retaining evidenced sibling assertions."""
    source = tmp_path / "source.ttl"
    source.write_text(
        _constituent("C1", "PrimarySite", "C10", "1" * 64)
        + _constituent("C2", "PrimarySite", "C20", "2" * 64)
        + _constituent("C2", "PrimarySite", "C21", "3" * 64)
        + _constituent("C3", "PrimarySite", "C30", "4" * 64)
    )
    decompositions = (
        cast(
            "Decomposition",
            SimpleNamespace(
                code="C1",
                constituents=(
                    Constituent(
                        axis="op:PrimarySite",
                        filler_code="C10",
                        axis_source="role",
                        source_roles=("R101",),
                        source_definition_ids=("1" * 64,),
                        source_occurrence_ids=("a" * 64,),
                    ),
                ),
            ),
        ),
        cast(
            "Decomposition",
            SimpleNamespace(
                code="C2",
                constituents=(
                    Constituent(
                        axis="op:PrimarySite",
                        filler_code="C20",
                        axis_source="role",
                        source_roles=("R101",),
                        source_definition_ids=("2" * 64,),
                        source_occurrence_ids=(),
                    ),
                    Constituent(
                        axis="op:PrimarySite",
                        filler_code="C21",
                        axis_source="role",
                        source_roles=("R101",),
                        source_definition_ids=("3" * 64,),
                        source_occurrence_ids=("b" * 64,),
                    ),
                ),
            ),
        ),
        cast(
            "Decomposition",
            SimpleNamespace(
                code="C3",
                constituents=(
                    Constituent(
                        axis="op:PrimarySite",
                        filler_code="C30",
                        axis_source="role",
                        source_roles=("R100",),
                        source_definition_ids=("4" * 64,),
                        source_occurrence_ids=("c" * 64,),
                    ),
                ),
            ),
        ),
    )

    evaluation = evaluate_persisted_assertion_evidence(
        decompositions, policy_identity=POLICY_IDENTITY
    )
    first = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=tmp_path / "first.ttl",
        source=_source(),
        persisted_evidence=evaluation,
        concept_exclusions=(),
    )
    second = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=tmp_path / "second.ttl",
        source=_source(),
        persisted_evidence=evaluation,
        concept_exclusions=(),
    )

    inventory = first.evidence_gap_inventory
    assert isinstance(inventory, EvidenceGapInventory)
    assert [
        (item.concept_code, item.filler_code, item.reason) for item in inventory.gaps
    ] == [
        ("C2", "C20", "missing-source-occurrence"),
        ("C3", "C30", "evidence-contradiction"),
        ("C3", "C30", "policy-non-applicable"),
    ]
    assert [(item.reason, item.count) for item in inventory.reason_counts] == [
        ("evidence-contradiction", 1),
        ("missing-source-occurrence", 1),
        ("policy-non-applicable", 1),
    ]
    assert inventory.withheld_concept_codes == ("C2", "C3")
    assert inventory == second.evidence_gap_inventory
    assert len(first.included_assertion_closure) == len(first.evidence_ledger) == 2
    assert {
        (item.assertion.concept_code, item.assertion.filler_code)
        for item in first.excluded_assertion_closure
    } == {("C2", "C20"), ("C3", "C30")}
    assert all(
        item.reason == "withheld-evidence-gap"
        for item in first.excluded_assertion_closure
    )
    included_ids = {
        item.assertion_identity for item in first.included_assertion_closure
    }
    assert not included_ids & {item.assertion_identity for item in inventory.gaps}
    output_graph = Graph().parse(tmp_path / "first.ttl", format="turtle")
    output_rows = {
        (
            str(subject).rsplit("#", 1)[-1],
            str(output_graph.value(node, URIRef(vocab.FILLER))).rsplit("#", 1)[-1],
        )
        for subject, node in output_graph.subject_objects(URIRef(vocab.HAS_CONSTITUENT))
    }
    assert output_rows == {("C1", "C10"), ("C2", "C21")}
    assert [
        (item.included_count, item.withheld_count, item.reasons)
        for item in first.completeness_summaries
    ] == [
        (1, 0, ()),
        (1, 1, ("missing-source-occurrence",)),
        (0, 1, ("evidence-contradiction", "policy-non-applicable")),
    ]


@pytest.mark.unit
def test_genus_fact_uses_named_policy_without_role_occurrence() -> None:
    decomposition = cast(
        "Decomposition",
        SimpleNamespace(
            code="C1",
            constituents=(
                Constituent(
                    axis="op:Morphology",
                    filler_code="C2",
                    axis_source="parent",
                    source_definition_ids=("1" * 64,),
                ),
            ),
        ),
    )

    assessment = evaluate_persisted_assertion_evidence(
        (decomposition,), policy_identity=POLICY_IDENTITY
    ).assessments[0]

    assert assessment.gap_reasons == ()
    assert assessment.occurrence_availability == "not-applicable-genus-fact"
    assert assessment.complete_definition_identity is not None
    assert assessment.transformation_rule == "certified-genus-to-morphology-v1"
    assert assessment.applicability_identity != assessment.complete_definition_identity
    assert assessment.evidence is not None


@pytest.mark.unit
def test_missing_persisted_join_is_not_reported_as_missing_occurrence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    source.write_text(
        _constituent("C1", "Morphology", "C2", "1" * 64, axis_source="parent")
    )

    closure = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=tmp_path / "effective.ttl",
        source=_source(),
        persisted_evidence=PersistedEvidenceEvaluation.build(
            policy_identity=POLICY_IDENTITY, assessments=()
        ),
        concept_exclusions=(),
    )

    assert [item.reason for item in closure.evidence_gap_inventory.gaps] == [
        "missing-persisted-assessment"
    ]


@pytest.mark.unit
def test_graph_filter_removes_complete_multiline_constituent_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttl"
    source.write_text(
        f'<{NCIT_NS}C1> <{vocab.ONTOPRISM_NS}label> "preserved metadata" .\n'
        + _constituent("C1", "PrimarySite", "C2", "1" * 64)
        + f"<{NCIT_NS}C1> <{vocab.HAS_CONSTITUENT}> [\n"
        f"  <{vocab.AXIS}> <{vocab.ONTOPRISM_NS}PrimarySite> ;\n"
        f'  <{vocab.FILLER}> <{NCIT_NS}C3> ; <{vocab.AXIS_SOURCE}> "role" ;\n'
        f"  <{vocab.SOURCE_ROLE}> <{NCIT_NS}R101> ;\n"
        f"  <{vocab.SOURCE_DEFINITION_FACT}> <{vocab.DEFINITION_FACT_NS}{'2' * 64}> ;\n"
        f"  <{vocab.ONTOPRISM_NS}detail> "
        f'[ <{vocab.ONTOPRISM_NS}label> "bounded" ] ] .\n'
    )
    evaluation = PersistedEvidenceEvaluation.build(
        policy_identity=POLICY_IDENTITY,
        assessments=(
            _evaluation(
                _persisted("C1", "PrimarySite", "C2", "1" * 64, "3" * 64)
            ).assessments[0],
            PersistedAssertionAssessment(
                concept_code="C1",
                axis="op:PrimarySite",
                filler_code="C3",
                axis_source="role",
                source_fact_ids=("2" * 64,),
                source_occurrence_ids=(),
                occurrence_availability=None,
                complete_definition_identity=None,
                source_roles=("R101",),
                transformation_rule="axis-contract-routing-v1",
                transformation_policy_identity=POLICY_IDENTITY,
                applicability_identity="7" * 64,
                gap_reasons=("missing-source-occurrence",),
                evidence=None,
            ),
        ),
    )

    closure = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=tmp_path / "effective.ttl",
        source=_source(),
        persisted_evidence=evaluation,
        concept_exclusions=(),
    )
    graph = Graph().parse(tmp_path / "effective.ttl", format="turtle")
    assert (
        URIRef(f"{NCIT_NS}C1"),
        URIRef(f"{vocab.ONTOPRISM_NS}label"),
        Literal("preserved metadata"),
    ) in graph
    assert (
        len(tuple(graph.objects(URIRef(f"{NCIT_NS}C1"), URIRef(vocab.HAS_CONSTITUENT))))
        == 1
    )
    assert all(
        str(value) != "bounded"
        for value in graph.objects(None, URIRef(f"{vocab.ONTOPRISM_NS}label"))
    )
    assert closure.inclusion_coverage == 0.5
    assert closure.qualifying_evidence_coverage == 1.0


@pytest.mark.unit
def test_assertion_closure_does_not_materialize_the_full_rdf_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Large unrelated source planes must not enter a general-purpose RDF store."""
    source = tmp_path / "source.ttl"
    unrelated = "".join(
        f"<urn:subject:{index}> <urn:predicate> <urn:object> .\n"
        for index in range(200)
    )
    source.write_text(
        unrelated + _constituent("C1", "PrimarySite", "C2", "1" * 64)
    )
    original_parse = Graph.parse

    def reject_unbounded_store(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        payload = kwargs.get("data")
        if (
            isinstance(payload, bytes)
            and len(payload) > 4_096
            and type(self.store).__name__ == "Memory"
        ):
            raise AssertionError("full artifact entered an unbounded RDF store")
        return original_parse(self, *args, **kwargs)

    monkeypatch.setattr(Graph, "parse", reject_unbounded_store)

    closure = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=tmp_path / "effective.ttl",
        source=_source(),
        persisted_evidence=_evaluation(
            _persisted("C1", "PrimarySite", "C2", "1" * 64, "2" * 64)
        ),
        concept_exclusions=(),
    )

    assert len(closure.included_assertion_closure) == 1
    assert b"<urn:subject:199>" in (tmp_path / "effective.ttl").read_bytes()


@pytest.mark.unit
def test_assertion_closure_refuses_parser_disagreement_and_missing_role_occurrence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusals: parser disagreement and absent exact source occurrence fail closed."""
    source = tmp_path / "source.ttl"
    source.write_text(_constituent("C1", "PrimarySite", "C2", "1" * 64))
    monkeypatch.setattr(
        "ontolib.decomposition.corpus_acceptance._fast_assertion_coordinates",
        lambda _payload: (),
    )
    with pytest.raises(
        CorpusAcceptanceValidationError, match="parser inventories differ"
    ):
        build_assertion_evidence_closure(
            source_artifact=source,
            effective_destination=tmp_path / "effective.ttl",
            source=_source(),
            persisted_evidence=_evaluation(
                _persisted("C1", "PrimarySite", "C2", "1" * 64, "3" * 64),
            ),
            concept_exclusions=(),
        )


@pytest.mark.unit
def test_concept_withholding_is_typed_for_unknown_residual_and_residual_unknown() -> (
    None
):
    """Variants: unknown-outcome, residual, and residual-unknown are not conflated."""
    dispositions = (
        ConceptExclusionDisposition(
            concept_code="C10",
            reason="unknown-outcome",
            official_source_preserved=True,
        ),
        ConceptExclusionDisposition(
            concept_code="C9305",
            reason="residual",
            official_source_preserved=True,
        ),
        ConceptExclusionDisposition(
            concept_code="C11",
            reason="residual-unknown",
            official_source_preserved=True,
        ),
    )
    assert {item.reason for item in dispositions} == {
        "unknown-outcome",
        "residual",
        "residual-unknown",
    }


@pytest.mark.unit
def test_concept_exclusions_are_derived_from_exact_outcomes_and_unknown_fillers() -> (
    None
):
    """Derive exact unknown/residual concepts and affected fillers only."""
    outcomes = (
        WorkItemOutcome(
            run_id="run-1",
            concept_code="C10",
            ordinal=0,
            state="complete",
            outcome="unknown",
            semantic_type=None,
            semantic_types=(),
            is_decomposed=False,
            is_residual=False,
            constituent_count=0,
            minted_count=0,
        ),
        WorkItemOutcome(
            run_id="run-1",
            concept_code="C9305",
            ordinal=1,
            state="complete",
            outcome="residual",
            semantic_type="Neoplastic Process",
            semantic_types=("Neoplastic Process",),
            is_decomposed=False,
            is_residual=True,
            constituent_count=0,
            minted_count=0,
        ),
    )
    residual = (
        ResidualFillerClassification(
            run_id="run-1",
            filler_code="C99",
            ordinal=0,
            source_identity=SOURCE_IDENTITY,
            definition_identity="1" * 64,
            detector_identity="2" * 64,
            classification="unknown",
            unsupported_reason="unsupported constructor",
        ),
    )
    decomposition = cast(
        "Decomposition",
        SimpleNamespace(
            code="C11",
            constituents=(
                SimpleNamespace(filler_code="C99"),
                SimpleNamespace(filler_code="C2"),
            ),
        ),
    )

    observed = build_concept_exclusion_dispositions(
        outcomes=outcomes,
        decompositions=(decomposition,),
        residual_classifications=residual,
    )
    assert [(item.concept_code, item.reason) for item in observed] == [
        ("C10", "unknown-outcome"),
        ("C11", "residual-unknown"),
        ("C9305", "residual"),
    ]


@pytest.mark.unit
def test_persisted_evidence_proves_exact_axis_contract_applicability() -> None:
    """Routing changes require row-level AXIS_CONTRACT role applicability."""
    decomposition = cast(
        "Decomposition",
        SimpleNamespace(
            code="C1",
            constituents=(
                Constituent(
                    axis="op:PrimarySite",
                    filler_code="C2",
                    axis_source="role",
                    source_roles=("R101",),
                    source_definition_ids=("1" * 64,),
                    source_occurrence_ids=("2" * 64,),
                ),
            ),
        ),
    )
    evaluation = evaluate_persisted_assertion_evidence(
        (decomposition,), policy_identity=POLICY_IDENTITY
    )
    evidence = evaluation.assessments[0].evidence
    assert evidence is not None
    assert evidence.transformation_rule == "axis-contract-routing-v1"
    assert evidence.transformation_policy_identity == POLICY_IDENTITY
    assert evidence.applicability_identity != POLICY_IDENTITY

    invalid = cast(
        "Decomposition",
        SimpleNamespace(
            code="C1",
            constituents=(
                replace(decomposition.constituents[0], source_roles=("R100",)),
            ),
        ),
    )
    invalid_evaluation = evaluate_persisted_assertion_evidence(
        (invalid,), policy_identity=POLICY_IDENTITY
    )
    assert invalid_evaluation.assessments[0].gap_reasons == ("policy-non-applicable",)


@pytest.mark.unit
def test_persisted_evidence_withholds_minted_fillers_from_included_closure() -> None:
    """A proposal filler is excluded rather than treated as certified NCIt evidence."""
    decomposition = cast(
        "Decomposition",
        SimpleNamespace(
            code="C1",
            constituents=(
                Constituent(
                    axis="op:PrimarySite",
                    filler_code="MINT-aaaaaaaaaaaa",
                    axis_source="role",
                    source_roles=("R101",),
                    source_definition_ids=("1" * 64,),
                    source_occurrence_ids=("2" * 64,),
                ),
            ),
        ),
    )

    evaluation = evaluate_persisted_assertion_evidence(
        (decomposition,), policy_identity=POLICY_IDENTITY
    )
    assert evaluation.assessments[0].gap_reasons == ("proposal-quarantined",)
    assert evaluation.assessments[0].evidence is None


@pytest.mark.unit
def test_persisted_evidence_inventory_retains_valid_absent_occurrence() -> None:
    """A valid absent occurrence is a typed gap rather than an early exception."""
    decomposition = cast(
        "Decomposition",
        SimpleNamespace(
            code="C1",
            constituents=(
                Constituent(
                    axis="op:PrimarySite",
                    filler_code="C2",
                    axis_source="role",
                    source_roles=("R101",),
                    source_definition_ids=("1" * 64,),
                    source_occurrence_ids=(),
                ),
            ),
        ),
    )

    evaluation = evaluate_persisted_assertion_evidence(
        (decomposition,), policy_identity=POLICY_IDENTITY
    )
    assert evaluation.assessments[0].gap_reasons == ("missing-source-occurrence",)
    assert evaluation.assessments[0].evidence is None


def _empty_closure() -> AssertionEvidenceClosure:
    payload = {
        "source": _source(),
        "source_artifact_identity": "1" * 64,
        "effective_artifact_identity": "2" * 64,
        "rdf_parser_inventory_identity": "3" * 64,
        "fast_parser_inventory_identity": "3" * 64,
        "included_assertion_closure": (),
        "excluded_assertion_closure": (),
        "concept_exclusions": (),
        "evidence_ledger": (),
        "evidence_gap_inventory": EvidenceGapInventory.build(()),
        "completeness_summaries": (),
        "original_candidate_assertion_count": 0,
        "unresolved_included_assertion_ids": (),
        "contradictory_included_assertion_ids": (),
        "ambiguous_included_assertion_ids": (),
    }
    return AssertionEvidenceClosure.model_validate(payload)


@pytest.mark.unit
def test_machine_acceptance_binds_all_identities_and_issues_unforgeable_token() -> None:
    """Bind machine acceptance to closure, evidence, policy, and dry-run."""
    closure = _empty_closure()
    ambiguity = EvidenceAmbiguityReport.from_closure(closure)
    acceptance = build_machine_evidence_acceptance(
        candidate_identity="4" * 64,
        closure=closure,
        exclusions_identity="5" * 64,
        evidence_ledger_identity=closure.evidence_ledger_identity,
        policy_identity=POLICY_IDENTITY,
        dry_run_identity="7" * 64,
        ambiguity=ambiguity,
    )
    assert isinstance(acceptance, MachineEvidenceAcceptance)
    assert acceptance.nci_adoption_claimed is False
    assert acceptance.unresolved_included_count == 0
    token = issue_machine_publication_token(acceptance)
    assert token.acceptance_identity == acceptance.acceptance_identity
    with pytest.raises(TypeError):
        type(token)(acceptance.acceptance_identity, object())  # type: ignore[call-arg]


@pytest.mark.unit
def test_machine_acceptance_refuses_absence_contradiction_or_ambiguity() -> None:
    """Refusal: any unresolved included assertion blocks machine authorization."""
    closure = _empty_closure().model_copy(
        update={"unresolved_included_assertion_ids": ("8" * 64,)}
    )
    ambiguity = EvidenceAmbiguityReport.build(
        unresolved_assertion_ids=("8" * 64,),
        contradictory_assertion_ids=(),
        ambiguous_assertion_ids=(),
    )
    with pytest.raises(CorpusAcceptanceValidationError, match="included closure"):
        build_machine_evidence_acceptance(
            candidate_identity="4" * 64,
            closure=closure,
            exclusions_identity="5" * 64,
            evidence_ledger_identity=closure.evidence_ledger_identity,
            policy_identity=POLICY_IDENTITY,
            dry_run_identity="7" * 64,
            ambiguity=ambiguity,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"original_candidate_assertion_count": 100}, "inclusion coverage"),
        ({"evidence_ledger": ()}, "qualifying evidence coverage"),
    ],
)
def test_machine_acceptance_rejects_low_inclusion_or_evidence_coverage(
    tmp_path: Path, update: dict[str, object], message: str
) -> None:
    source = tmp_path / "source.ttl"
    source.write_text(_constituent("C1", "PrimarySite", "C2", "1" * 64))
    closure = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=tmp_path / "effective.ttl",
        source=_source(),
        persisted_evidence=_evaluation(
            _persisted("C1", "PrimarySite", "C2", "1" * 64, "2" * 64)
        ),
        concept_exclusions=(),
    ).model_copy(update=update)

    with pytest.raises(CorpusAcceptanceValidationError, match=message):
        build_machine_evidence_acceptance(
            candidate_identity="4" * 64,
            closure=closure,
            exclusions_identity="5" * 64,
            evidence_ledger_identity=closure.evidence_ledger_identity,
            policy_identity=POLICY_IDENTITY,
            dry_run_identity="7" * 64,
            ambiguity=EvidenceAmbiguityReport.from_closure(closure),
        )


@pytest.mark.unit
def test_actual_publication_authorization_refuses_missing_or_cross_bound_token() -> (
    None
):
    """Actual write entry refusal: no token or another acceptance/exclusion binding."""
    closure = _empty_closure()
    ambiguity = EvidenceAmbiguityReport.from_closure(closure)
    acceptance = build_machine_evidence_acceptance(
        candidate_identity="4" * 64,
        closure=closure,
        exclusions_identity="5" * 64,
        evidence_ledger_identity=closure.evidence_ledger_identity,
        policy_identity=POLICY_IDENTITY,
        dry_run_identity="7" * 64,
        ambiguity=ambiguity,
    )
    token = issue_machine_publication_token(acceptance)

    require_machine_publication_authorization(
        token=token,
        acceptance=acceptance,
        closure=closure,
    )
    with pytest.raises(CorpusAcceptanceValidationError, match="typed authorization"):
        require_machine_publication_authorization(
            token=None,
            acceptance=acceptance,
            closure=closure,
        )
    other = acceptance.model_copy(update={"exclusions_identity": "8" * 64})
    with pytest.raises(CorpusAcceptanceValidationError, match="token binding"):
        require_machine_publication_authorization(
            token=token,
            acceptance=other,
            closure=closure,
        )


@pytest.mark.unit
def test_acceptance_metadata_is_derived_from_closure_not_caller_statuses(
    tmp_path: Path,
) -> None:
    """Render distinct statuses without claiming NCI adoption."""
    source = tmp_path / "source.ttl"
    raw_effective = tmp_path / "raw-effective.ttl"
    publication = tmp_path / "publication.ttl"
    source.write_text(
        _constituent("C1", "PrimarySite", "C2", "1" * 64)
        + _constituent("C3", "PrimarySite", "C4", "2" * 64, needs_review=True)
        + _constituent("C5", "PrimarySite", "C6", "6" * 64)
    )
    evidence = _evaluation(
        _persisted("C1", "PrimarySite", "C2", "1" * 64, "3" * 64),
        _persisted("C3", "PrimarySite", "C4", "2" * 64, "4" * 64),
    )
    closure = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=raw_effective,
        source=_source(),
        persisted_evidence=PersistedEvidenceEvaluation.build(
            policy_identity=POLICY_IDENTITY,
            assessments=(
                *evidence.assessments,
                PersistedAssertionAssessment(
                    concept_code="C5",
                    axis="op:PrimarySite",
                    filler_code="C6",
                    axis_source="role",
                    source_fact_ids=("6" * 64,),
                    source_occurrence_ids=(),
                    occurrence_availability=None,
                    complete_definition_identity=None,
                    source_roles=("R101",),
                    transformation_rule="axis-contract-routing-v1",
                    transformation_policy_identity=POLICY_IDENTITY,
                    applicability_identity="7" * 64,
                    gap_reasons=("missing-source-occurrence",),
                    evidence=None,
                ),
            ),
        ),
        concept_exclusions=(
            ConceptExclusionDisposition(
                concept_code="C10",
                reason="unknown-outcome",
                official_source_preserved=True,
            ),
            ConceptExclusionDisposition(
                concept_code="C9305",
                reason="residual",
                official_source_preserved=True,
            ),
        ),
    )

    identity = build_machine_acceptance_metadata_artifact(
        closure=closure,
        source_artifact=raw_effective,
        destination=publication,
        run_id="run-1",
        publication_identity="5" * 64,
    )
    graph = Graph().parse(publication, format="turtle")
    acceptance = URIRef(vocab.ACCEPTANCE_STATUS)
    ncit = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
    assert hashlib.sha256(publication.read_bytes()).hexdigest() == identity
    assert set(graph.objects(URIRef(f"{ncit}C1"), acceptance)) == {Literal("projected")}
    assert set(graph.objects(URIRef(f"{ncit}C3"), acceptance)) == {Literal("projected")}
    assert set(
        graph.objects(URIRef(f"{ncit}C3"), URIRef(vocab.ACCEPTANCE_WITHHELD_COUNT))
    ) == {Literal(0)}
    assert set(graph.objects(URIRef(f"{ncit}C5"), acceptance)) == {
        Literal("withheld-evidence-gap")
    }
    assert set(graph.objects(URIRef(f"{ncit}C10"), acceptance)) == {
        Literal("unknown-withheld")
    }
    assert set(graph.objects(URIRef(f"{ncit}C9305"), acceptance)) == {
        Literal("residual-withheld")
    }
    assert "adoption" not in publication.read_text().lower()


@pytest.mark.unit
async def test_machine_authorized_publication_writes_and_verifies_exact_receipt(  # noqa: C901
    tmp_path: Path,
) -> None:
    """The actual graph/file writer requires the exact opaque authorization."""
    source = tmp_path / "source.ttl"
    artifact = tmp_path / "effective.ttl"
    destination = tmp_path / "published.ttl"
    source.write_text(
        _constituent("C1", "PrimarySite", "C2", "1" * 64)
        + f"<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1> "
        f'<{vocab.REPRESENTATION_STATUS}> "{vocab.LEGACY_PRECOORDINATED}" ; '
        f'<{vocab.DECOMPOSED_BY}> "run-1" .\n'
    )
    closure = build_assertion_evidence_closure(
        source_artifact=source,
        effective_destination=artifact,
        source=_source(),
        persisted_evidence=_evaluation(
            _persisted("C1", "PrimarySite", "C2", "1" * 64, "3" * 64),
        ),
        concept_exclusions=(),
    )
    ambiguity = EvidenceAmbiguityReport.from_closure(closure)
    acceptance = build_machine_evidence_acceptance(
        candidate_identity="4" * 64,
        closure=closure,
        exclusions_identity="5" * 64,
        evidence_ledger_identity=closure.evidence_ledger_identity,
        policy_identity=POLICY_IDENTITY,
        dry_run_identity="7" * 64,
        ambiguity=ambiguity,
    )
    token = issue_machine_publication_token(acceptance)

    class Journal:
        def __init__(self) -> None:
            self.intent: AcceptancePublicationIntent | None = None
            self.receipt: AcceptancePublicationReceipt | None = None

        @asynccontextmanager
        async def publication_lock(self):  # type: ignore[no-untyped-def]
            yield

        async def begin_acceptance_publication(
            self, intent: AcceptancePublicationIntent
        ) -> AcceptancePublicationIntent:
            self.intent = self.intent or intent
            return self.intent

        async def finish_acceptance_publication(
            self, receipt: AcceptancePublicationReceipt
        ) -> None:
            self.receipt = receipt

        async def record_acceptance_publication_failure(
            self, acceptance_identity: str, error: BaseException
        ) -> None:
            raise AssertionError((acceptance_identity, error))

    journal = Journal()

    class GraphClient:
        def __init__(self) -> None:
            self.marker: PublicationMarker | None = None
            self.loaded = b""

        async def select_once(self, query: str, *, required_variables=()):  # type: ignore[no-untyped-def]
            del query, required_variables
            if self.marker is None:
                return []
            return [
                {"predicate": str(predicate), "value": str(value)}
                for predicate, value in (
                    (RDF.type, vocab.PUBLICATION_CLASS),
                    (vocab.PUBLICATION_RUN, self.marker.run_id),
                    (vocab.PUBLICATION_SOURCE_IDENTITY, self.marker.source_identity),
                    (
                        vocab.PUBLICATION_REPRESENTATION_IDENTITY,
                        self.marker.representation_identity,
                    ),
                    (vocab.PUBLICATION_BUILT_AT, self.marker.built_at_lexical),
                )
            ]

        async def load(
            self,
            data,  # type: ignore[no-untyped-def]
            *,
            content_type: str,
            graph_iri: str | None = None,
            replace: bool = True,
        ) -> None:
            del content_type, graph_iri, replace
            self.loaded = data if isinstance(data, bytes) else data.read()

        async def update(self, update: str) -> None:
            del update
            assert journal.intent is not None
            self.marker = PublicationMarker.model_validate(
                journal.intent.marker.model_dump()
            )

    graph = GraphClient()
    with pytest.raises(PublicationPreflightError, match="typed authorization"):
        await publish_machine_accepted_artifact(
            token=None,
            acceptance=acceptance,
            closure=closure,
            artifact=artifact,
            destination=destination,
            expected_codes=("C1",),
            run_id="run-1",
            source_identity=SOURCE_IDENTITY,
            client=graph,
            provenance=journal,
            built_at=datetime(2026, 9, 17, tzinfo=UTC),
        )
    assert journal.intent is None
    assert graph.loaded == b""
    assert not destination.exists()

    receipt = await publish_machine_accepted_artifact(
        token=token,
        acceptance=acceptance,
        closure=closure,
        artifact=artifact,
        destination=destination,
        expected_codes=("C1",),
        run_id="run-1",
        source_identity=SOURCE_IDENTITY,
        client=graph,
        provenance=journal,
        built_at=datetime(2026, 9, 17, tzinfo=UTC),
    )

    assert destination.read_bytes() == artifact.read_bytes() == graph.loaded
    assert receipt == journal.receipt
    assert receipt.artifact_identity == closure.effective_artifact_identity
    assert receipt.acceptance_identity == acceptance.acceptance_identity
    assert receipt.marker == receipt.readback_marker


@pytest.mark.unit
def test_human_acceptance_is_discriminated_and_rejection_cannot_authorize() -> None:
    """Human variants apply only to exact escalated assertions and include rejection."""
    accepted = HumanEvidenceAcceptance(
        status="accepted",
        escalated_assertion_ids=("1" * 64,),
        attestation_artifact_identity="2" * 64,
        acceptance_identity="3" * 64,
        nci_adoption_claimed=False,
    )
    rejected = RejectedHumanEvidenceAcceptance(
        status="rejected",
        escalated_assertion_ids=("1" * 64,),
        attestation_artifact_identity="2" * 64,
        rejection_identity="3" * 64,
        nci_adoption_claimed=False,
    )
    assert accepted.status == "accepted"
    assert rejected.status == "rejected"
    with pytest.raises(ValidationError):
        HumanEvidenceAcceptance(
            status="accepted",
            escalated_assertion_ids=(),
            attestation_artifact_identity="2" * 64,
            acceptance_identity="3" * 64,
            nci_adoption_claimed=False,
        )


@pytest.mark.unit
def test_r101_closure_binds_every_occurrence_disposition_rule_and_path(
    report: R101ConservationReport,
) -> None:
    """R101 zero-unresolved is computed from all named per-occurrence dispositions."""
    closure = build_r101_occurrence_closure(report)
    assert len(closure.occurrences) == 43_414
    assert closure.unresolved_occurrence_ids == ()
    assert all(
        item.disposition and item.disposition_reason for item in closure.occurrences
    )
    assert all(item.proof_identity for item in closure.occurrences)
    assert all(
        item.path_identity is not None for item in closure.occurrences if item.r82_path
    )
