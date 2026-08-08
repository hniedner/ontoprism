from __future__ import annotations

from dataclasses import fields, replace
from typing import cast, get_args

import pytest

from ontolib.decomposition.models import ConceptOutcome, canonical_definition_fact_id
from ontolib.decomposition.semantic_bundles import (
    AdjudicatedSemanticBundle,
    AdjudicatedSemanticContext,
    AvailableMember,
    BundleAxis,
    BundleKind,
    BundlePairAvailability,
    DeferredMember,
    DeferredSemanticBundle,
    EvidenceClaim,
    EvidenceClaimKind,
    EvidenceClaimTarget,
    EvidenceRegistry,
    MemberRole,
    MissingMember,
    NotEvaluatedCandidate,
    ObservedBundleMember,
    ObservedSemanticBundle,
    PairProvenance,
    ProjectedConstituentEvidence,
    ProposedMember,
    RejectedSemanticBundle,
    SemanticBundleCandidate,
    SemanticBundleMember,
    SemanticMetricScore,
    SourceOccurrence,
    StageClassification,
    canonical_restriction_fact_id,
    evaluate_pair_availability,
    score_observed_bundles,
    validate_candidate_evidence,
)

_SOURCE_IDENTITY = "1" * 64
_GROUP_ID = "C115057/C132736:0"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("anchor_code", "group_id", "role_code", "filler_code"),
    [
        ("C132736", "C115057/C132736:0", "R88", "C27966"),
        ("", "", "", ""),
        ("Cå", "group-β", "Rñ", "填充"),
        ("C1\x1fC2", "group\x1f0", "R1\x1fR2", "C3\x1fC4"),
    ],
)
def test_restriction_fact_id_matches_definition_fact_id(
    anchor_code: str,
    group_id: str,
    role_code: str,
    filler_code: str,
) -> None:
    assert canonical_restriction_fact_id(
        anchor_code,
        group_id,
        role_code,
        filler_code,
    ) == canonical_definition_fact_id(
        anchor_code,
        group_id,
        "restriction",
        role_code,
        filler_code,
    )


def _occurrence(
    filler_code: str,
    *,
    root_code: str = "C115057",
    anchor_code: str = "C132736",
    source_role: str = "R88",
) -> SourceOccurrence:
    fact_id = canonical_restriction_fact_id(
        anchor_code,
        _GROUP_ID,
        source_role,
        filler_code,
    )
    return SourceOccurrence(
        source_identity=_SOURCE_IDENTITY,
        ncit_release="26.07d",
        root_code=root_code,
        fact_id=fact_id,
        source_role=source_role,
        filler_code=filler_code,
        anchor_code=anchor_code,
        depth=2,
        source_group_id=_GROUP_ID,
    )


def _member(
    role: MemberRole,
    filler_code: str,
    *,
    claim_ids: tuple[str, ...] = (),
    provenance_status: PairProvenance = "ncit-26.07d",
) -> SemanticBundleMember:
    axis = (
        BundleAxis.STAGE_VALUE
        if role is MemberRole.STAGE_VALUE
        else BundleAxis.STAGE_SYSTEM
    )
    occurrences = () if claim_ids else (_occurrence(filler_code),)
    return SemanticBundleMember(
        role=role,
        axis=axis,
        filler_code=filler_code,
        provenance_status=provenance_status,
        source_occurrences=occurrences,
        evidence_claim_ids=claim_ids,
    )


def _candidate(
    candidate_id: str = "stage-c115057-ajcc-v7",
    *,
    stage_type: str = "C90530",
    stage_value: str = "C27966",
    stage_system_code: str | None = None,
) -> SemanticBundleCandidate:
    return SemanticBundleCandidate(
        candidate_id=candidate_id,
        subject_code="C115057",
        name="AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        kind=BundleKind.CANCER_STAGE,
        classification=StageClassification(ncit_code=stage_system_code or stage_type),
        members=(
            _member(MemberRole.STAGE_TYPE, stage_type),
            _member(MemberRole.STAGE_VALUE, stage_value),
        ),
        evidence_claim_ids=("mcode-stage-structure",),
        evidence_source_ids=("mcode-4.0.0-cancer-stage",),
    )


def _adjudicated(candidate: SemanticBundleCandidate) -> AdjudicatedSemanticBundle:
    return AdjudicatedSemanticBundle(
        candidate=candidate,
        decision_id="decision-1",
        rationale="The reviewer approved this exact framework/value association.",
        reviewer="Example SME",
        reviewed_at="2026-08-05",
    )


@pytest.mark.unit
def test_candidate_rejects_mixed_ncit_source_snapshots() -> None:
    candidate = _candidate()
    second = candidate.members[1]
    occurrence = second.source_occurrences[0]
    mixed_member = replace(
        second,
        source_occurrences=(replace(occurrence, source_identity="2" * 64),),
    )

    with pytest.raises(ValueError, match="one NCIt source snapshot"):
        replace(candidate, members=(candidate.members[0], mixed_member))


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        {"true_positive": -1},
        {"true_positive": 2},
        {"missing": frozenset()},
        {"extra": frozenset({"same"}), "missing": frozenset({"same"})},
    ],
)
def test_semantic_metric_score_rejects_inconsistent_counts_and_sets(
    mutation: dict[str, object],
) -> None:
    valid: dict[str, object] = {
        "expected": 2,
        "actual": 1,
        "true_positive": 1,
        "missing": frozenset({"missing"}),
        "extra": frozenset(),
    }

    with pytest.raises(ValueError, match="semantic metric"):
        SemanticMetricScore(**(valid | mutation))  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"reviewer": ""}, "reviewer"),
        ({"reviewed_at": "August 5"}, "reviewed_at"),
    ],
)
def test_reviewed_bundle_requires_a_dated_reviewer(
    mutation: dict[str, object],
    message: str,
) -> None:
    bundle = _adjudicated(_candidate())

    with pytest.raises(ValueError, match=message):
        replace(bundle, **mutation)


@pytest.mark.unit
def test_review_dispositions_are_distinct_types() -> None:
    accepted = _adjudicated(_candidate())
    common = {
        "candidate": accepted.candidate,
        "decision_id": accepted.decision_id,
        "rationale": accepted.rationale,
        "reviewer": accepted.reviewer,
        "reviewed_at": accepted.reviewed_at,
    }

    assert isinstance(RejectedSemanticBundle(**common), RejectedSemanticBundle)
    assert isinstance(DeferredSemanticBundle(**common), DeferredSemanticBundle)


def _structural_claim() -> EvidenceClaim:
    return EvidenceClaim(
        claim_id="mcode-stage-structure",
        kind=EvidenceClaimKind.STRUCTURE,
        source_id="mcode",
        source_version="4.0.0",
        uri="https://hl7.org/fhir/us/mcode/STU4/",
        assertion="ONTOPrism maps stage framework and value roles to this profile.",
    )


def _projected(
    candidate: SemanticBundleCandidate,
    member_index: int,
    *,
    needs_review: bool = False,
) -> ProjectedConstituentEvidence:
    member = candidate.members[member_index]
    return ProjectedConstituentEvidence(
        axis=member.axis,
        filler_code=member.filler_code,
        needs_review=needs_review,
        relationship_group=None,
        source_role="R88",
        axis_source="role",
        source_fact_ids=(member.source_occurrences[0].fact_id,),
    )


def _observed(candidate: SemanticBundleCandidate) -> ObservedSemanticBundle:
    return ObservedSemanticBundle(
        subject_code=candidate.subject_code,
        kind=candidate.kind,
        classification=candidate.classification,
        members=tuple(
            ObservedBundleMember(
                role=member.role,
                axis=member.axis,
                filler_code=member.filler_code,
                needs_review=False,
                relationship_group="stage",
                source_role="R88",
                axis_source="role",
                source_fact_ids=tuple(
                    occurrence.fact_id for occurrence in member.source_occurrences
                ),
            )
            for member in candidate.members
        ),
    )


@pytest.mark.unit
def test_source_occurrence_requires_canonical_r88_fact_identity() -> None:
    occurrence = _occurrence("C27966")

    assert occurrence.fact_id == canonical_restriction_fact_id(
        occurrence.anchor_code,
        occurrence.source_group_id,
        "R88",
        "C27966",
    )
    with pytest.raises(ValueError, match="canonical restriction identity"):
        replace(occurrence, fact_id="f" * 64)
    with pytest.raises(ValueError, match="R88"):
        _occurrence("C27966", source_role="R101")
    with pytest.raises(ValueError, match="source_group_id must not be empty"):
        replace(occurrence, source_group_id=" ")
    with pytest.raises(ValueError, match="depth must be non-negative"):
        replace(occurrence, depth=-1)


@pytest.mark.unit
def test_semantic_bundle_member_requires_shared_provenance_status() -> None:
    member = _member(MemberRole.STAGE_TYPE, "C90530")

    assert member.provenance_status == "ncit-26.07d"
    with pytest.raises(ValueError, match="provenance status is invalid"):
        replace(
            member,
            provenance_status=cast("PairProvenance", "invented"),
        )


@pytest.mark.unit
def test_stage_candidate_enforces_typed_roles_axes_and_classification() -> None:
    valid = _candidate()

    with pytest.raises(ValueError, match="role requires op:StageValue"):
        replace(valid.members[1], axis=BundleAxis.STAGE_SYSTEM)
    with pytest.raises(ValueError, match="exactly one classification framework"):
        replace(valid, members=(valid.members[1],))
    with pytest.raises(ValueError, match="exactly one stage value"):
        replace(valid, members=(valid.members[0],))


@pytest.mark.unit
def test_stage_classification_stores_only_the_reviewed_ncit_code() -> None:
    classification = StageClassification(ncit_code="C90530")

    assert [field.name for field in fields(StageClassification)] == ["ncit_code"]
    assert classification.authority == "AJCC"
    assert classification.version == "7"
    with pytest.raises(ValueError, match="reviewed stage-system code"):
        StageClassification(ncit_code="C999999")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ncit_code", "authority", "version"),
    [
        ("C90529", "AJCC", "6"),
        ("C90530", "AJCC", "7"),
        ("C140961", "AJCC", "7"),
        ("C132248", "AJCC", "8"),
        ("C180901", "AJCC", "9"),
        ("C186618", "FIGO", "2009"),
        ("C186617", "FIGO", "2018"),
        ("C206211", "FIGO", "2023"),
        ("C141685", "VALG", "limited-extensive"),
    ],
)
def test_stage_classification_derives_packet_authority_and_version(
    ncit_code: str,
    authority: str,
    version: str,
) -> None:
    classification = StageClassification(ncit_code=ncit_code)

    assert (classification.authority, classification.version) == (
        authority,
        version,
    )


@pytest.mark.unit
def test_stage_candidate_accepts_method_instead_of_stage_type() -> None:
    candidate = replace(
        _candidate(),
        members=(
            _member(
                MemberRole.STAGING_METHOD,
                "C141685",
                claim_ids=("mcode-valg-method", "nci-pdq-valg"),
                provenance_status="proposed",
            ),
            _member(MemberRole.STAGE_VALUE, "C28064"),
        ),
        classification=StageClassification(ncit_code="C141685"),
    )

    assert candidate.members[0].role is MemberRole.STAGING_METHOD


@pytest.mark.unit
def test_evidence_registry_validates_exact_member_claims() -> None:
    candidate = replace(
        _candidate(),
        members=(
            _member(
                MemberRole.STAGING_METHOD,
                "C141685",
                claim_ids=("mcode-valg-method",),
                provenance_status="proposed",
            ),
            _member(MemberRole.STAGE_VALUE, "C27966"),
        ),
    )
    structural = _structural_claim()
    method = EvidenceClaim(
        claim_id="mcode-valg-method",
        kind=EvidenceClaimKind.MEMBER,
        source_id="mcode-stage-method",
        source_version="4.0.0",
        uri="https://hl7.org/fhir/us/mcode/STU4/ValueSet-method.html",
        assertion="C141685 is a VALG staging method.",
        target=EvidenceClaimTarget(
            subject_code=None,
            role=MemberRole.STAGING_METHOD,
            filler_code="C141685",
        ),
    )
    registry = EvidenceRegistry(claims=(structural, method))

    validate_candidate_evidence(candidate, registry)
    wrong_target = replace(
        method,
        target=replace(method.target, filler_code="C999") if method.target else None,
    )
    with pytest.raises(ValueError, match="does not support member"):
        validate_candidate_evidence(
            candidate,
            EvidenceRegistry((structural, wrong_target)),
        )
    with pytest.raises(ValueError, match="not structural evidence"):
        validate_candidate_evidence(
            replace(candidate, evidence_claim_ids=(method.claim_id,)),
            registry,
        )
    with pytest.raises(ValueError, match="unknown evidence claim"):
        validate_candidate_evidence(
            replace(candidate, evidence_claim_ids=("missing",)),
            registry,
        )


@pytest.mark.unit
def test_evidence_claims_are_typed_targeted_and_hash_bound() -> None:
    structural = _structural_claim()
    target = EvidenceClaimTarget(
        subject_code="C115057",
        role=MemberRole.STAGE_VALUE,
        filler_code="C27966",
    )

    with pytest.raises(ValueError, match="target role must be typed"):
        replace(target, role=cast("MemberRole", "stage-value"))
    with pytest.raises(ValueError, match="kind must be typed"):
        replace(structural, kind=cast("EvidenceClaimKind", "structure"))
    with pytest.raises(ValueError, match="require an exact target"):
        replace(structural, kind=EvidenceClaimKind.MEMBER)
    with pytest.raises(ValueError, match="cannot target a member"):
        replace(structural, target=target)
    with pytest.raises(ValueError, match="claim IDs must be unique"):
        EvidenceRegistry((structural, structural))

    assert (
        EvidenceRegistry((structural,)).identity
        == EvidenceRegistry((structural,)).identity
    )
    assert (
        structural.claim_identity
        != replace(
            structural,
            assertion="A different exact assertion.",
        ).claim_identity
    )


@pytest.mark.unit
def test_candidate_support_requires_consistent_typed_provenance() -> None:
    candidate = _candidate()
    member = candidate.members[0]

    with pytest.raises(ValueError, match="role must be typed"):
        replace(member, role=cast("MemberRole", "stage-type"))
    with pytest.raises(ValueError, match="axis must be typed"):
        replace(member, axis=cast("BundleAxis", "op:StageSystem"))
    with pytest.raises(ValueError, match="source occurrences must be unique"):
        replace(member, source_occurrences=(member.source_occurrences[0],) * 2)
    with pytest.raises(ValueError, match="filler must match"):
        replace(member, filler_code="C999")
    with pytest.raises(ValueError, match="claim IDs must be unique"):
        replace(member, evidence_claim_ids=("claim", "claim"))
    with pytest.raises(ValueError, match="require a source occurrence"):
        replace(member, source_occurrences=(), evidence_claim_ids=())
    with pytest.raises(ValueError, match="root must match"):
        replace(
            candidate,
            members=(
                replace(
                    member,
                    source_occurrences=(
                        replace(member.source_occurrences[0], root_code="C999"),
                    ),
                ),
                candidate.members[1],
            ),
        )
    with pytest.raises(ValueError, match="cancer-stage model"):
        replace(candidate, kind=cast("BundleKind", "other"))
    with pytest.raises(ValueError, match="require structural evidence"):
        replace(candidate, evidence_claim_ids=())
    with pytest.raises(ValueError, match="claim IDs must be unique"):
        replace(candidate, evidence_claim_ids=("claim", "claim"))
    with pytest.raises(ValueError, match="contain only framework and value"):
        replace(
            candidate,
            members=(
                *candidate.members,
                _member(MemberRole.CLASSIFICATION_CONTEXT, "C198023"),
            ),
        )


@pytest.mark.unit
def test_adjudicated_bundle_is_review_decision_not_candidate_alias() -> None:
    candidate = _candidate()
    adjudicated = _adjudicated(candidate)

    assert adjudicated.semantic_identity == candidate.semantic_identity
    with pytest.raises(ValueError, match="rationale must not be empty"):
        replace(adjudicated, rationale="")


@pytest.mark.unit
def test_flat_constituents_produce_availability_not_observed_bundle() -> None:
    candidate = _candidate()
    evidence = (
        ProjectedConstituentEvidence(
            axis=BundleAxis.STAGE_SYSTEM,
            filler_code="C90530",
            needs_review=False,
            relationship_group=None,
            source_role="R88",
            axis_source="role",
            source_fact_ids=(candidate.members[0].source_occurrences[0].fact_id,),
        ),
        ProjectedConstituentEvidence(
            axis=BundleAxis.STAGE_VALUE,
            filler_code="C27966",
            needs_review=True,
            relationship_group="stage",
            source_role="R88",
            axis_source="role",
            source_fact_ids=(candidate.members[1].source_occurrences[0].fact_id,),
        ),
    )

    result = evaluate_pair_availability(candidate, evidence)

    assert result.status == "deferred"
    assert [member.filler_code for member in result.available_members] == ["C90530"]
    assert [member.filler_code for member in result.deferred_members] == ["C27966"]
    assert result.missing_members == ()
    assert not isinstance(result, ObservedSemanticBundle)


@pytest.mark.unit
def test_pair_availability_reports_missing_without_inventing_association() -> None:
    candidate = _candidate()

    result = evaluate_pair_availability(
        candidate,
        (
            ProjectedConstituentEvidence(
                axis=BundleAxis.STAGE_VALUE,
                filler_code="C27966",
                needs_review=False,
                relationship_group=None,
                source_role="R88",
                axis_source="role",
                source_fact_ids=(),
            ),
        ),
    )

    assert result.status == "incomplete"
    assert [member.filler_code for member in result.missing_members] == ["C90530"]


@pytest.mark.unit
def test_pair_availability_marks_a_proposed_member_without_engine_evidence() -> None:
    candidate = replace(
        _candidate(),
        members=(
            _member(
                MemberRole.STAGING_METHOD,
                "C141685",
                claim_ids=("mcode-valg-method",),
                provenance_status="proposed",
            ),
            _member(MemberRole.STAGE_VALUE, "C27966"),
        ),
    )

    result = evaluate_pair_availability(candidate, (_projected(candidate, 1),))

    # Not "available": the member's concept is an unaccepted proposal, so the pair
    # cannot be projected from NCIt at all (D60).
    assert result.status == "proposed"
    assert [member.filler_code for member in result.proposed_members] == ["C141685"]
    assert isinstance(result.members[0], ProposedMember)
    assert result.deferred_members == ()
    assert result.missing_members == ()


@pytest.mark.unit
def test_missing_member_outranks_deferred_member() -> None:
    """A bundle with both a missing and a deferred member is incomplete, not deferred.

    Routing it to `deferred` would send an unprojectable bundle to SME review as
    though the projection had merely flagged it.
    """
    candidate = _candidate()
    deferred = DeferredMember(
        member=candidate.members[0],
        evidence=replace(_projected(candidate, 0), needs_review=True),
    )
    missing = MissingMember(member=candidate.members[1])

    both = BundlePairAvailability(
        candidate_id=candidate.candidate_id,
        members=(deferred, missing),
    )
    reversed_order = BundlePairAvailability(
        candidate_id=candidate.candidate_id,
        members=(missing, deferred),
    )

    assert both.status == "incomplete"
    assert reversed_order.status == "incomplete"


@pytest.mark.unit
def test_missing_member_rejects_a_proposed_member() -> None:
    """MissingMember and ProposedMember must be disjoint, not merely conventional.

    `_member_without_engine_evidence` routes proposals to ProposedMember, but a
    caller assembling availability directly (a report replay, or the pilot
    deserialising a stored trace) would otherwise report a proposal-only candidate
    as `incomplete` and inflate the missing-member count.
    """
    proposed = _member(
        MemberRole.STAGING_METHOD,
        "C141685",
        claim_ids=("mcode-valg-method",),
        provenance_status="proposed",
    )

    with pytest.raises(ValueError, match="proposed availability requires"):
        MissingMember(member=proposed)


@pytest.mark.unit
@pytest.mark.parametrize("outcome", ["Decomposed", "", "unevaluated", "atomic no-op"])
def test_not_evaluated_candidate_rejects_an_unknown_engine_outcome(
    outcome: str,
) -> None:
    """A mis-cased or misspelled outcome must not become "never evaluated".

    `"Decomposed"` would otherwise slip past the `!= "decomposed"` guard, silently
    reclassifying a candidate the engine did decompose.
    """
    with pytest.raises(ValueError, match="unknown engine outcome"):
        NotEvaluatedCandidate(
            candidate_id="stage-c115057-ajcc-v7",
            engine_outcome=cast("ConceptOutcome", outcome),
        )


@pytest.mark.unit
def test_not_evaluated_candidate_accepts_every_non_decomposed_outcome() -> None:
    for outcome in get_args(ConceptOutcome):
        if outcome == "decomposed":
            with pytest.raises(ValueError, match="must have member availability"):
                NotEvaluatedCandidate(candidate_id="c", engine_outcome=outcome)
            continue
        assert (
            NotEvaluatedCandidate(candidate_id="c", engine_outcome=outcome).status
            == "not-evaluated"
        )


@pytest.mark.unit
def test_bundle_pair_availability_is_one_validated_discriminated_sequence() -> None:
    candidate = _candidate()
    first_evidence = _projected(candidate, 0)
    available = AvailableMember(
        member=candidate.members[0],
        evidence=first_evidence,
    )
    missing = MissingMember(member=candidate.members[1])

    result = BundlePairAvailability(
        candidate_id=candidate.candidate_id,
        members=(available, missing),
    )

    assert result.status == "incomplete"
    assert result.available_members == (candidate.members[0],)
    assert result.missing_members == (candidate.members[1],)
    with pytest.raises(ValueError, match="at least one member"):
        BundlePairAvailability(candidate_id=candidate.candidate_id, members=())
    with pytest.raises(ValueError, match="evidence pair must match"):
        AvailableMember(
            member=candidate.members[0],
            evidence=_projected(candidate, 1),
        )
    with pytest.raises(ValueError, match="review evidence"):
        DeferredMember(
            member=candidate.members[0],
            evidence=first_evidence,
        )
    with pytest.raises(TypeError):
        DeferredMember(member=candidate.members[0])  # type: ignore[call-arg]


@pytest.mark.unit
def test_projection_metadata_is_validated_and_available_pairs_remain_flat() -> None:
    candidate = _candidate()
    first = _projected(candidate, 0)
    second = _projected(candidate, 1)

    assert evaluate_pair_availability(candidate, (first, second)).status == "available"
    with pytest.raises(ValueError, match="pairs must be unique"):
        evaluate_pair_availability(candidate, (first, first))
    with pytest.raises(ValueError, match="axis must be typed"):
        replace(first, axis=cast("BundleAxis", "op:StageSystem"))
    with pytest.raises(ValueError, match="relationship_group must not be empty"):
        replace(first, relationship_group=" ")
    with pytest.raises(ValueError, match="source_role is invalid"):
        replace(first, source_role="role")
    with pytest.raises(ValueError, match="axis_source is invalid"):
        replace(first, axis_source=cast("str", "guess"))
    with pytest.raises(ValueError, match="source fact IDs must be unique"):
        replace(first, source_fact_ids=first.source_fact_ids * 2)


@pytest.mark.unit
def test_observed_bundle_derives_association_identity_from_content() -> None:
    candidate = _candidate()
    observed = _observed(candidate)

    assert observed.association_id == _observed(candidate).association_id
    assert (
        observed.association_id
        != replace(
            observed,
            members=(
                observed.members[1],
                replace(observed.members[0], filler_code="C90529"),
            ),
        ).association_id
    )
    member = observed.members[0]
    with pytest.raises(ValueError, match="role must be typed"):
        replace(member, role=cast("MemberRole", "stage-type"))
    with pytest.raises(ValueError, match="axis must be typed"):
        replace(member, axis=cast("BundleAxis", "op:StageSystem"))
    with pytest.raises(ValueError, match="context-only"):
        replace(member, role=MemberRole.CLASSIFICATION_CONTEXT)
    with pytest.raises(ValueError, match="role requires"):
        replace(member, axis=BundleAxis.STAGE_VALUE)
    with pytest.raises(ValueError, match="relationship_group must not be empty"):
        replace(member, relationship_group=" ")
    with pytest.raises(ValueError, match="source_role is invalid"):
        replace(member, source_role="role")
    with pytest.raises(ValueError, match="axis_source is invalid"):
        replace(member, axis_source=cast("str", "guess"))
    with pytest.raises(ValueError, match="source fact IDs must be unique"):
        replace(member, source_fact_ids=member.source_fact_ids * 2)
    with pytest.raises(ValueError, match="cancer-stage model"):
        replace(observed, kind=cast("BundleKind", "other"))
    assert member.relationship_group == "stage"
    assert member.source_role == "R88"
    assert member.axis_source == "role"


@pytest.mark.unit
def test_observed_score_detects_crossed_associations_with_same_flat_pairs() -> None:
    expected_candidates = (
        _candidate("one", stage_type="C90529", stage_value="C27966"),
        _candidate("two", stage_type="C90530", stage_value="C27970"),
    )
    expected = tuple(_adjudicated(candidate) for candidate in expected_candidates)
    actual = (
        _observed(
            _candidate(
                "wrong-one",
                stage_type="C90529",
                stage_value="C27970",
                stage_system_code="C90529",
            )
        ),
        _observed(
            _candidate(
                "wrong-two",
                stage_type="C90530",
                stage_value="C27966",
                stage_system_code="C90530",
            )
        ),
    )

    result = score_observed_bundles(expected, actual)

    assert result.augmented.exact_bundle.true_positive == 0
    assert result.augmented.association.true_positive == 0
    assert result.augmented.contextual_member.true_positive == 2
    assert result.augmented.contextual_member.expected == 4
    assert result.augmented.contextual_member.actual == 4
    assert result.augmented.exact_bundle.f1 == 0.0
    assert result.augmented.exact_bundle.exact is False


@pytest.mark.unit
def test_semantic_bundle_scores_split_ncit_bound_and_augmented_views() -> None:
    candidate = _candidate()
    locally_approved = replace(
        candidate,
        members=(
            replace(
                candidate.members[0],
                provenance_status="locally-approved",
            ),
            candidate.members[1],
        ),
    )

    result = score_observed_bundles(
        (_adjudicated(locally_approved),),
        (_observed(locally_approved),),
    )

    assert result.ncit_bound.exact_bundle.expected == 0
    assert result.augmented.exact_bundle.true_positive == 1

    proposed = replace(
        locally_approved,
        members=(
            replace(
                locally_approved.members[0],
                provenance_status="proposed",
            ),
            locally_approved.members[1],
        ),
    )
    proposed_result = score_observed_bundles((_adjudicated(proposed),), ())

    assert proposed_result.ncit_bound.exact_bundle.expected == 0
    assert proposed_result.augmented.exact_bundle.expected == 0


@pytest.mark.unit
def test_zero_denominator_semantic_metrics_are_not_defined() -> None:
    result = score_observed_bundles((), ())

    assert result.ncit_bound.exact_bundle.precision is None
    assert result.ncit_bound.exact_bundle.recall is None
    assert result.ncit_bound.exact_bundle.f1 is None
    assert result.ncit_bound.exact_bundle.exact is True
    assert result.augmented == result.ncit_bound


@pytest.mark.unit
def test_semantic_score_rejects_duplicate_expected_and_observed_identities() -> None:
    candidate = _candidate()
    expected = _adjudicated(candidate)
    observed = _observed(candidate)
    duplicate = replace(observed)

    with pytest.raises(ValueError, match=r"adjudicated.*unique"):
        score_observed_bundles((expected, expected), ())
    with pytest.raises(ValueError, match=r"observed.*unique"):
        score_observed_bundles((), (observed, duplicate))


@pytest.mark.unit
def test_semantic_identity_ignores_editorial_and_evidence_order() -> None:
    candidate = _candidate()
    revised = replace(
        candidate,
        name="Editorial rename",
        evidence_claim_ids=("other", "mcode-stage-structure"),
    )

    assert revised.semantic_identity == candidate.semantic_identity


@pytest.mark.unit
def test_context_only_construct_uses_same_typed_source_occurrence() -> None:
    context = AdjudicatedSemanticContext(
        context_id="context-c198031-toronto",
        subject_code="C198031",
        name="Toronto classification context",
        member=SemanticBundleMember(
            role=MemberRole.CLASSIFICATION_CONTEXT,
            axis=BundleAxis.STAGE_SYSTEM,
            filler_code="C198023",
            provenance_status="ncit-26.07d",
            source_occurrences=(
                _occurrence(
                    "C198023",
                    root_code="C198031",
                    anchor_code="C198031",
                ),
            ),
        ),
        rationale="No stage value is asserted, so this is not a semantic bundle.",
    )

    assert context.member.source_occurrences[0].root_code == context.subject_code
    with pytest.raises(ValueError, match="classification-context member"):
        replace(context, member=_member(MemberRole.STAGE_TYPE, "C90530"))


@pytest.mark.unit
def test_semantic_metric_missing_and_extra_sets_must_be_disjoint() -> None:
    """A pair cannot be both absent from and surplus to the same expectation.

    The counts alone cannot catch it: one missing and one extra is consistent
    with `expected == actual == 1, true_positive == 0`.
    """
    with pytest.raises(ValueError, match="missing and extra sets must be disjoint"):
        SemanticMetricScore(
            expected=1,
            actual=1,
            true_positive=0,
            missing=frozenset({"pair-a"}),
            extra=frozenset({"pair-a"}),
        )


@pytest.mark.unit
def test_available_and_deferred_members_reject_a_proposed_member() -> None:
    """The disjointness must hold on every branch, not just the missing one.

    If engine evidence turns up for a pair the registry still calls proposed, the
    registry and the projection disagree; resolving that to "available" would
    report an unaccepted proposal as present.
    """
    candidate = replace(
        _candidate(),
        members=(
            _member(
                MemberRole.STAGING_METHOD,
                "C141685",
                claim_ids=("mcode-valg-method",),
                provenance_status="proposed",
            ),
            _member(MemberRole.STAGE_VALUE, "C27966"),
        ),
    )
    proposed_member = candidate.members[0]
    evidence = ProjectedConstituentEvidence(
        axis=proposed_member.axis,
        filler_code=proposed_member.filler_code,
        needs_review=False,
        relationship_group=None,
        source_role="R88",
        axis_source="role",
        source_fact_ids=(),
    )

    with pytest.raises(ValueError, match="proposed availability requires"):
        AvailableMember(member=proposed_member, evidence=evidence)
    with pytest.raises(ValueError, match="proposed availability requires"):
        DeferredMember(
            member=proposed_member, evidence=replace(evidence, needs_review=True)
        )

    # And the engine/registry disagreement is named rather than resolved.
    with pytest.raises(ValueError, match="proposed but the engine projected it"):
        evaluate_pair_availability(candidate, (evidence,))


@pytest.mark.unit
def test_bundle_availability_rejects_a_member_reported_twice() -> None:
    """One member cannot hold two contradictory availabilities.

    `evaluate_pair_availability` keys off `candidate.members`, but the public
    constructor is reachable from a report replay, and a repeated member would
    also double-count in the report's member tallies.
    """
    candidate = _candidate()
    available = AvailableMember(
        member=candidate.members[0], evidence=_projected(candidate, 0)
    )
    missing = MissingMember(member=candidate.members[0])

    with pytest.raises(ValueError, match="members must be unique"):
        BundlePairAvailability(
            candidate_id=candidate.candidate_id, members=(available, missing)
        )
    with pytest.raises(ValueError, match="members must be unique"):
        BundlePairAvailability(
            candidate_id=candidate.candidate_id, members=(available, available)
        )
