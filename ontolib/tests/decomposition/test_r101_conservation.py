from __future__ import annotations

import asyncio
import datetime
import os
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.corpus_baseline import (
    CorpusBaseline,
    corpus_baseline_identity,
)
from ontolib.decomposition.provenance_models import (
    CompletedRunForEvidence,
    RunFingerprint,
)
from ontolib.decomposition.r101_conservation import (
    CandidateBaseline,
    CollapsedR82Disposition,
    ConceptConservation,
    OccurrenceLinks,
    Pair,
    PairDelta,
    ProjectedDisposition,
    R101ConservationProgress,
    R101ConservationReport,
    R101ConservationValidationError,
    SourceOccurrence,
    UnresolvedLossDisposition,
    analyze_r101_candidates,
    generate_r101_conservation_report,
    r101_conservation_detector_identity,
    r101_conservation_report_identity,
    require_authorizable_r101_report,
    validate_r101_conservation_report,
    write_r101_conservation_report,
)

if TYPE_CHECKING:
    from pathlib import Path


def _fingerprint(**changes: object) -> RunFingerprint:
    values: dict[str, object] = {
        "source_identity": "b" * 64,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": branch_spec(DecompositionBranch.NEOPLASM).semantic_types,
        "worklist": ("C1",),
        "total_limit": None,
        "sample_manifest_identity": None,
        "algorithm_version": "decomposition-v3",
        "config_version": "nested-definition-v2",
        "walker_max_depth": 7,
        "output_mode": "file",
        "load_mode": "named-graph",
        "emitted_at": datetime.datetime(2026, 8, 15, tzinfo=datetime.UTC),
    }
    values.update(changes)
    return RunFingerprint.model_validate(values)


def _run(**changes: object) -> CompletedRunForEvidence:
    values: dict[str, object] = {
        "run_id": "old-run",
        "ncit_version": "26.07d",
        "fingerprint": _fingerprint(),
        "representation_identity": "c" * 64,
        "publication_artifact_path": "old.ttl",
    }
    values.update(changes)
    return CompletedRunForEvidence.model_validate(values)


def _new_run(**changes: object) -> CompletedRunForEvidence:
    values: dict[str, object] = {
        "run_id": "new-run",
        "fingerprint": _fingerprint(algorithm_version="decomposition-v4"),
        "representation_identity": "e" * 64,
        "publication_artifact_path": "new.ttl",
    }
    values.update(changes)
    return _run(**values)


def _baseline(path: Path, run: CompletedRunForEvidence) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": run.run_id,
        "source_identity": run.fingerprint.source_identity,
        "ontology_release": run.ncit_version,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "run_fingerprint_identity": run.fingerprint.identity,
        "representation_identity": run.representation_identity,
        "artifact_identity": run.representation_identity,
        "detector_identity": "d" * 64,
        "worklist_count": len(run.fingerprint.worklist),
        "outcome_counts": {
            "decomposed": len(run.fingerprint.worklist),
            "residual": 0,
            "semantic_excluded": 0,
            "atomic_noop": 0,
            "unknown": 0,
        },
        "emitted_constituent_pair_count": 1,
        "complete_semantic_fact_count": 2,
        "source_occurrence_count": 2,
        "selected_occurrence_count": 1,
        "minted_count": 0,
    }
    baseline = CorpusBaseline.model_validate(
        {**payload, "baseline_identity": corpus_baseline_identity(payload)}
    )
    path.write_text(baseline.model_dump_json())


def _occurrence(identifier: str, filler: str) -> SourceOccurrence:
    return SourceOccurrence(
        occurrence_id=identifier * 64,
        role_code="R101",
        filler_code=filler,
    )


def _links(
    identifier: str,
    *,
    old: tuple[Pair, ...] = (),
    new: tuple[Pair, ...] = (),
) -> OccurrenceLinks:
    return OccurrenceLinks(
        occurrence_id=identifier * 64,
        old_pairs=old,
        new_pairs=new,
    )


@pytest.mark.unit
def test_detector_identity_changes_with_classifier_source_semantics() -> None:
    def classifier_one() -> str:
        return "one"

    def classifier_two() -> str:
        return "two"

    assert r101_conservation_detector_identity(classifier_one) != (
        r101_conservation_detector_identity(classifier_two)
    )


@pytest.mark.unit
def test_detector_identity_changes_with_report_schema_source() -> None:
    class SchemaOne:
        value: str

    class SchemaTwo:
        value: int

    assert r101_conservation_detector_identity(report_model=SchemaOne) != (
        r101_conservation_detector_identity(report_model=SchemaTwo)
    )


@pytest.mark.unit
def test_occurrence_dispositions_are_total_unique_and_evidence_is_nonempty() -> None:
    site = Pair(axis="op:PrimarySite", filler_code="C10")
    candidate = CandidateBaseline(
        concept_code="C1",
        old_pairs=(site,),
        new_pairs=(site,),
        r101_old_pairs=(site,),
        r101_new_pairs=(site,),
        source_occurrences=(_occurrence("a", "C10"), _occurrence("b", "C10")),
        occurrence_links=(
            _links("a", old=(site,), new=(site,)),
            _links("b", old=(site,), new=(site,)),
        ),
    )

    dispositions = candidate.classify_occurrences(
        semantic_types={"C10": None}, live_r82_pairs=()
    )

    assert dispositions == (
        ProjectedDisposition(
            occurrence_id="a" * 64,
            target_pair=site,
            evidence_ids=(f"persisted:new:{'a' * 64}:op:PrimarySite:C10",),
            review_required=True,
        ),
        ProjectedDisposition(
            occurrence_id="b" * 64,
            target_pair=site,
            evidence_ids=(f"persisted:new:{'b' * 64}:op:PrimarySite:C10",),
            review_required=True,
        ),
    )
    with pytest.raises(ValueError, match="evidence"):
        ProjectedDisposition(occurrence_id="a" * 64, target_pair=site, evidence_ids=())
    with pytest.raises(ValueError, match="exactly one"):
        candidate.model_copy(
            update={"occurrence_links": candidate.occurrence_links[:1]}
        ).classify_occurrences(semantic_types={"C10": None}, live_r82_pairs=())


@pytest.mark.unit
def test_duplicate_source_occurrence_identity_is_rejected_before_disposition() -> None:
    occurrence = _occurrence("a", "C10")
    candidate = CandidateBaseline(
        concept_code="C1",
        old_pairs=(),
        new_pairs=(),
        r101_old_pairs=(),
        r101_new_pairs=(),
        source_occurrences=(occurrence, occurrence),
        occurrence_links=(_links("a"),),
    )

    with pytest.raises(R101ConservationValidationError, match="IDs must be unique"):
        candidate.classify_occurrences(
            semantic_types={"C10": "Anatomical Structure"}, live_r82_pairs=()
        )


@pytest.mark.unit
def test_duplicate_source_occurrences_keep_occurrence_specific_dispositions() -> None:
    old = Pair(axis="op:PrimarySite", filler_code="C20")
    retained = Pair(axis="op:PrimarySite", filler_code="C10")
    candidate = CandidateBaseline(
        concept_code="C1",
        old_pairs=(old, retained),
        new_pairs=(retained,),
        r101_old_pairs=(old, retained),
        r101_new_pairs=(retained,),
        source_occurrences=(_occurrence("b", "C20"), _occurrence("a", "C20")),
        occurrence_links=(
            _links("b", old=(old,)),
            _links("a", old=(old,)),
        ),
    )

    dispositions = candidate.classify_occurrences(
        semantic_types={"C20": "Body Part, Organ, or Organ Component"},
        live_r82_pairs=(("C10", "C20"),),
    )

    assert dispositions == tuple(
        CollapsedR82Disposition(
            occurrence_id=identifier * 64,
            broader_pair=old,
            retained_pairs=(retained,),
            evidence_ids=(f"occurrence:{identifier * 64}:live-r82:C10:C20",),
        )
        for identifier in ("a", "b")
    )


@pytest.mark.unit
def test_multiple_narrower_r82_evidence_is_complete_and_deterministic() -> None:
    broad = Pair(axis="op:PrimarySite", filler_code="C30")
    first = Pair(axis="op:PrimarySite", filler_code="C10")
    second = Pair(axis="op:PrimarySite", filler_code="C20")
    candidate = CandidateBaseline(
        concept_code="C1",
        old_pairs=(broad, first, second),
        new_pairs=(second, first),
        r101_old_pairs=(broad, first, second),
        r101_new_pairs=(second, first),
        source_occurrences=(_occurrence("a", "C30"),),
        occurrence_links=(_links("a", old=(broad,)),),
    )

    disposition = candidate.classify_occurrences(
        semantic_types={"C30": "Body Part, Organ, or Organ Component"},
        live_r82_pairs=(("C20", "C30"), ("C10", "C30")),
    )[0]

    assert isinstance(disposition, CollapsedR82Disposition)
    assert disposition.retained_pairs == (first, second)
    assert disposition.evidence_ids == (
        f"occurrence:{'a' * 64}:live-r82:C10:C30",
        f"occurrence:{'a' * 64}:live-r82:C20:C30",
    )


@pytest.mark.unit
def test_r101_and_non_r101_pair_deltas_are_separate_and_non_r101_blocks() -> None:
    r101_old = Pair(axis="op:PrimarySite", filler_code="C10")
    r101_new = Pair(axis="op:AssociatedRegion", filler_code="C10")
    morphology = Pair(axis="op:Morphology", filler_code="C900")
    candidate = CandidateBaseline(
        concept_code="C1",
        old_pairs=(r101_old, morphology),
        new_pairs=(r101_new,),
        r101_old_pairs=(r101_old,),
        r101_new_pairs=(r101_new,),
        source_occurrences=(_occurrence("a", "C10"),),
        occurrence_links=(_links("a", old=(r101_old,), new=(r101_new,)),),
    )

    analysis = candidate.conservation_analysis(
        semantic_types={"C10": "Body Location or Region"}, live_r82_pairs=()
    )

    assert {(item.change, item.pair) for item in analysis.r101_pair_delta} == {
        ("removed", r101_old),
        ("added", r101_new),
    }
    assert [(item.change, item.pair) for item in analysis.non_r101_pair_delta] == [
        ("removed", morphology)
    ]
    assert analysis.authorizable is False


@pytest.mark.unit
def test_missing_semantic_key_fails_closed_but_explicit_none_is_reviewable() -> None:
    pair = Pair(axis="op:PrimarySite", filler_code="C10")
    candidate = CandidateBaseline(
        concept_code="C1",
        old_pairs=(),
        new_pairs=(pair,),
        r101_old_pairs=(),
        r101_new_pairs=(pair,),
        source_occurrences=(_occurrence("a", "C10"),),
        occurrence_links=(_links("a", new=(pair,)),),
    )

    with pytest.raises(
        R101ConservationValidationError, match="missing semantic lookup"
    ):
        candidate.classify_occurrences(semantic_types={}, live_r82_pairs=())
    disposition = candidate.classify_occurrences(
        semantic_types={"C10": None}, live_r82_pairs=()
    )[0]
    assert isinstance(disposition, ProjectedDisposition)
    assert disposition.review_required is True


@pytest.mark.unit
def test_unresolved_loss_serializes_but_validator_blocks() -> None:
    old = Pair(axis="op:PrimarySite", filler_code="C10")
    candidate = CandidateBaseline(
        concept_code="C1",
        old_pairs=(old,),
        new_pairs=(),
        r101_old_pairs=(old,),
        r101_new_pairs=(),
        source_occurrences=(_occurrence("a", "C10"),),
        occurrence_links=(_links("a", old=(old,)),),
    )

    analysis = candidate.conservation_analysis(
        semantic_types={"C10": "Body Part, Organ, or Organ Component"},
        live_r82_pairs=(),
    )

    assert isinstance(analysis.occurrence_dispositions[0], UnresolvedLossDisposition)
    assert analysis.occurrence_dispositions[0].review_required is True
    assert analysis.authorizable is False
    with pytest.raises(R101ConservationValidationError, match="unresolved loss"):
        validate_r101_conservation_report((analysis,))
    with pytest.raises(R101ConservationValidationError, match="unresolved loss"):
        require_authorizable_r101_report((analysis,))


@pytest.mark.unit
def test_single_primary_known_reroute_is_a_candidate() -> None:
    old = Pair(axis="op:PrimarySite", filler_code="C10")
    new = Pair(axis="op:AssociatedRegion", filler_code="C10")
    candidate = CandidateBaseline(
        concept_code="C1",
        old_pairs=(old,),
        new_pairs=(new,),
        r101_old_pairs=(old,),
        r101_new_pairs=(new,),
        source_occurrences=(_occurrence("a", "C10"),),
        occurrence_links=(_links("a", old=(old,), new=(new,)),),
    )

    async def no_r82(_codes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        return ()

    analyses = asyncio.run(
        analyze_r101_candidates(
            (candidate,),
            semantic_types={"C10": "Body Location or Region"},
            resolve_live_r82=no_r82,
        )
    )
    assert [item.concept_code for item in analyses] == ["C1"]


@pytest.mark.unit
async def test_r82_resolution_is_batched_without_per_concept_queries() -> None:
    candidates = tuple(
        CandidateBaseline(
            concept_code=f"C{index + 1000}",
            old_pairs=(Pair(axis="op:PrimarySite", filler_code=f"C{index + 2000}"),),
            new_pairs=(),
            r101_old_pairs=(
                Pair(axis="op:PrimarySite", filler_code=f"C{index + 2000}"),
            ),
            r101_new_pairs=(),
            source_occurrences=(
                SourceOccurrence(
                    occurrence_id=f"{index:064x}",
                    role_code="R101",
                    filler_code=f"C{index + 2000}",
                ),
            ),
            occurrence_links=(
                OccurrenceLinks(
                    occurrence_id=f"{index:064x}",
                    old_pairs=(
                        Pair(axis="op:PrimarySite", filler_code=f"C{index + 2000}"),
                    ),
                    new_pairs=(),
                ),
            ),
        )
        for index in range(300)
    )
    calls: list[tuple[str, ...]] = []

    async def live_r82(codes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        calls.append(codes)
        assert len(codes) <= 256
        return ()

    await analyze_r101_candidates(
        candidates,
        semantic_types={
            f"C{index + 2000}": "Anatomical Structure" for index in range(300)
        },
        resolve_live_r82=live_r82,
    )

    assert len(calls) == 2


@pytest.mark.unit
async def test_r82_resolution_rejects_one_concept_above_external_query_bound() -> None:
    occurrences = tuple(
        SourceOccurrence(
            occurrence_id=f"{index:064x}",
            role_code="R101",
            filler_code=f"C{index + 2000}",
        )
        for index in range(257)
    )
    candidate = CandidateBaseline(
        concept_code="C1000",
        old_pairs=(),
        new_pairs=(),
        r101_old_pairs=(),
        r101_new_pairs=(),
        source_occurrences=occurrences,
        occurrence_links=tuple(
            OccurrenceLinks(
                occurrence_id=occurrence.occurrence_id,
                old_pairs=(),
                new_pairs=(),
            )
            for occurrence in occurrences
        ),
    )

    with pytest.raises(
        R101ConservationValidationError, match="exceeds R82 batch bound"
    ):
        await analyze_r101_candidates(
            (candidate,),
            semantic_types={
                occurrence.filler_code: "Anatomical Structure"
                for occurrence in occurrences
            },
            resolve_live_r82=lambda _codes: pytest.fail("must reject before QLever"),
        )


@pytest.mark.unit
async def test_analysis_preserves_unrelated_pairs_and_includes_every_r101_concept() -> (
    None
):
    unrelated = Pair(axis="op:Morphology", filler_code="C900")
    candidates = (
        CandidateBaseline(
            concept_code="C1",
            old_pairs=(
                unrelated,
                Pair(axis="op:PrimarySite", filler_code="C10"),
                Pair(axis="op:PrimarySite", filler_code="C11"),
            ),
            new_pairs=(
                unrelated,
                Pair(axis="op:PrimarySite", filler_code="C10"),
                Pair(axis="op:AssociatedRegion", filler_code="C11"),
            ),
            r101_old_pairs=(
                Pair(axis="op:PrimarySite", filler_code="C10"),
                Pair(axis="op:PrimarySite", filler_code="C11"),
            ),
            source_occurrences=(_occurrence("a", "C10"), _occurrence("b", "C11")),
        ),
        CandidateBaseline(
            concept_code="C2",
            old_pairs=(Pair(axis="op:PrimarySite", filler_code="C20"),),
            new_pairs=(Pair(axis="op:PrimarySite", filler_code="C20"),),
            r101_old_pairs=(Pair(axis="op:PrimarySite", filler_code="C20"),),
            source_occurrences=(_occurrence("c", "C20"),),
        ),
        CandidateBaseline(
            concept_code="C3",
            old_pairs=(Pair(axis="op:AssociatedRegion", filler_code="C30"),),
            new_pairs=(Pair(axis="op:PrimarySite", filler_code="C30"),),
            r101_old_pairs=(Pair(axis="op:AssociatedRegion", filler_code="C30"),),
            source_occurrences=(_occurrence("d", "C30"),),
        ),
        CandidateBaseline(
            concept_code="C4",
            old_pairs=(
                Pair(axis="op:AssociatedLineageClassification", filler_code="C40"),
            ),
            new_pairs=(
                Pair(axis="op:AssociatedLineageClassification", filler_code="C40"),
            ),
            r101_old_pairs=(
                Pair(axis="op:AssociatedLineageClassification", filler_code="C40"),
            ),
            source_occurrences=(_occurrence("e", "C40"),),
        ),
    )

    async def live_r82(_codes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        return ()

    analyses = await analyze_r101_candidates(
        candidates,
        semantic_types={
            "C10": "Body Part, Organ, or Organ Component",
            "C11": "Body Location or Region",
            "C20": "Body Part, Organ, or Organ Component",
            "C30": None,
            "C40": None,
        },
        resolve_live_r82=live_r82,
    )

    assert [analysis.concept_code for analysis in analyses] == ["C1", "C2", "C3", "C4"]
    assert unrelated in analyses[0].new_pairs
    assert set(analyses[0].new_pairs) == {
        unrelated,
        Pair(axis="op:PrimarySite", filler_code="C10"),
        Pair(axis="op:AssociatedRegion", filler_code="C11"),
    }
    assert analyses[3].new_pairs == candidates[3].old_pairs


@pytest.mark.unit
async def test_c4791_exact_change_collapses_heart_to_left_atrium() -> None:
    candidate = CandidateBaseline(
        concept_code="C4791",
        old_pairs=(
            Pair(axis="op:Morphology", filler_code="C27085"),
            Pair(axis="op:PrimarySite", filler_code="C12727"),
            Pair(axis="op:PrimarySite", filler_code="C12869"),
        ),
        new_pairs=(
            Pair(axis="op:Morphology", filler_code="C27085"),
            Pair(axis="op:PrimarySite", filler_code="C12869"),
        ),
        r101_old_pairs=(
            Pair(axis="op:PrimarySite", filler_code="C12727"),
            Pair(axis="op:PrimarySite", filler_code="C12869"),
        ),
        r101_new_pairs=(Pair(axis="op:PrimarySite", filler_code="C12869"),),
        source_occurrences=(
            _occurrence("a", "C12727"),
            _occurrence("b", "C12869"),
            _occurrence("c", "C12728"),
            _occurrence("d", "C12471"),
            _occurrence("e", "C12964"),
        ),
        occurrence_links=(
            _links("a", old=(Pair(axis="op:PrimarySite", filler_code="C12727"),)),
            _links(
                "b",
                old=(Pair(axis="op:PrimarySite", filler_code="C12869"),),
                new=(Pair(axis="op:PrimarySite", filler_code="C12869"),),
            ),
            _links("c"),
            _links("d"),
            _links("e"),
        ),
    )

    async def live_r82(codes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        assert codes == ("C12471", "C12727", "C12728", "C12869", "C12964")
        return (
            ("C12728", "C12727"),
            ("C12869", "C12727"),
            ("C12869", "C12728"),
        )

    analyses = await analyze_r101_candidates(
        (candidate,),
        semantic_types={
            "C12727": "Body Part, Organ, or Organ Component",
            "C12869": "Body Part, Organ, or Organ Component",
            "C12728": "Body Part, Organ, or Organ Component",
            "C12471": "Tissue",
            "C12964": "Tissue",
        },
        resolve_live_r82=live_r82,
    )
    conservation = candidate.conservation_analysis(
        semantic_types=analyses[0].semantic_types,
        live_r82_pairs=analyses[0].live_r82_pairs,
    )
    assert conservation.occurrence_dispositions[0] == CollapsedR82Disposition(
        occurrence_id="a" * 64,
        broader_pair=Pair(axis="op:PrimarySite", filler_code="C12727"),
        retained_pairs=(Pair(axis="op:PrimarySite", filler_code="C12869"),),
        evidence_ids=(f"occurrence:{'a' * 64}:live-r82:C12869:C12727",),
    )


@pytest.mark.unit
def test_stale_pre_completion_conservation_artifact_is_schema_invalid() -> None:
    stale = {
        "schema_version": 1,
        "source_occurrence_schema_version": 1,
        "source_identity": "a" * 64,
        "ontology_release": "26.07d",
        "old_run_id": "old-run",
        "old_run_fingerprint_identity": "b" * 64,
        "old_representation_identity": "c" * 64,
        "old_baseline_identity": "d" * 64,
        "old_algorithm_version": "decomposition-v3",
        "new_algorithm_version": "decomposition-v4",
        "new_detector_identity": "e" * 64,
        "candidate_count": 0,
        "changed_concept_count": 0,
        "changed_pair_count": 0,
        "changes": [],
        "report_identity": "f" * 64,
    }
    with pytest.raises(ValidationError):
        R101ConservationReport.model_validate(stale)


def _minimal_report() -> R101ConservationReport:
    payload: dict[str, object] = {
        "schema_version": 2,
        "source_occurrence_schema_version": 1,
        "source_identity": "a" * 64,
        "ontology_release": "26.07d",
        "old_run_id": "old",
        "old_run_fingerprint_identity": "b" * 64,
        "old_representation_identity": "c" * 64,
        "old_baseline_identity": "d" * 64,
        "old_algorithm_version": "decomposition-v3",
        "new_run_id": "new",
        "new_run_fingerprint_identity": "e" * 64,
        "new_representation_identity": "f" * 64,
        "new_algorithm_version": "decomposition-v4",
        "new_detector_identity": r101_conservation_detector_identity(),
        "pre_resume_proof_identity": "1" * 64,
        "resume_dry_run_identity": "2" * 64,
        "mixed_cohort_identity": "3" * 64,
        "candidate_count": 0,
        "occurrence_count": 0,
        "r101_changed_pair_count": 0,
        "non_r101_changed_pair_count": 0,
        "unresolved_loss_count": 0,
        "authorizable": True,
        "concepts": (),
    }
    return R101ConservationReport.model_validate(
        {**payload, "report_identity": r101_conservation_report_identity(payload)}
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 99, "unsupported R101"),
        ("source_occurrence_schema_version", 99, "unsupported source occurrence"),
        ("candidate_count", 1, "candidate count"),
        ("occurrence_count", 1, "counts do not match"),
        ("authorizable", False, "authorization"),
    ],
)
def test_report_rejects_schema_count_and_authorization_corruption(
    field: str, value: object, message: str
) -> None:
    payload = _minimal_report().model_dump()
    payload[field] = value
    payload["report_identity"] = r101_conservation_report_identity(payload)

    with pytest.raises(ValidationError, match=message):
        R101ConservationReport.model_validate(payload)


@pytest.mark.unit
def test_report_rejects_payload_identity_corruption() -> None:
    payload = _minimal_report().model_dump()
    payload["report_identity"] = "0" * 64

    with pytest.raises(ValidationError, match="identity does not match"):
        R101ConservationReport.model_validate(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "pairs", "message"),
    [
        (
            "old_pairs",
            (Pair(axis="op:PrimarySite", filler_code="C1"),) * 2,
            "old normalized",
        ),
        (
            "r101_old_pairs",
            (Pair(axis="op:PrimarySite", filler_code="C1"),) * 2,
            "old R101-derived",
        ),
        (
            "new_pairs",
            (Pair(axis="op:PrimarySite", filler_code="C1"),) * 2,
            "new normalized",
        ),
    ],
)
def test_candidate_baseline_rejects_duplicate_normalized_origins(
    field: str, pairs: tuple[Pair, ...], message: str
) -> None:
    values: dict[str, object] = {
        "concept_code": "C1",
        "old_pairs": (),
        "new_pairs": (),
        "r101_old_pairs": (),
        "r101_new_pairs": (),
        "source_occurrences": (),
        "occurrence_links": (),
        field: pairs,
    }

    with pytest.raises(ValidationError, match=message):
        CandidateBaseline.model_validate(values)


@pytest.mark.unit
def test_candidate_baseline_rejects_r101_origins_absent_from_normalized_pairs() -> None:
    pair = Pair(axis="op:PrimarySite", filler_code="C1")
    for field in ("r101_old_pairs", "r101_new_pairs"):
        with pytest.raises(ValidationError, match="must be normalized"):
            CandidateBaseline(
                concept_code="C1",
                old_pairs=(),
                new_pairs=(),
                r101_old_pairs=(pair,) if field == "r101_old_pairs" else (),
                r101_new_pairs=(pair,) if field == "r101_new_pairs" else (),
                source_occurrences=(),
                occurrence_links=(),
            )


@pytest.mark.unit
def test_occurrence_links_reject_duplicate_old_and_new_projections() -> None:
    pair = Pair(axis="op:PrimarySite", filler_code="C1")
    for field in ("old_pairs", "new_pairs"):
        with pytest.raises(ValidationError, match="unique pairs"):
            OccurrenceLinks(
                occurrence_id="a" * 64,
                old_pairs=(pair, pair) if field == "old_pairs" else (),
                new_pairs=(pair, pair) if field == "new_pairs" else (),
            )


@pytest.mark.unit
def test_non_r101_delta_reaches_authorization_validator_block() -> None:
    analysis = ConceptConservation(
        concept_code="C1",
        occurrence_dispositions=(),
        r101_pair_delta=(),
        non_r101_pair_delta=(
            PairDelta(
                change="removed",
                pair=Pair(axis="op:Morphology", filler_code="C10"),
            ),
        ),
        authorizable=False,
    )

    with pytest.raises(R101ConservationValidationError, match="non-R101 pair delta"):
        validate_r101_conservation_report((analysis,))


@pytest.mark.unit
def test_concept_authorization_cannot_disagree_with_blocking_content() -> None:
    with pytest.raises(ValidationError, match="authorizable"):
        ConceptConservation(
            concept_code="C1",
            occurrence_dispositions=(),
            r101_pair_delta=(),
            non_r101_pair_delta=(),
            authorizable=False,
        )


@pytest.mark.unit
def test_report_write_keeps_existing_output_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _minimal_report()
    output = tmp_path / "report.json"
    output.write_text("original\n")

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_r101_conservation_report(output, report)

    assert output.read_text() == "original\n"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.unit
def test_persisted_old_pair_accepts_minted_filler() -> None:
    candidate = CandidateBaseline.model_validate(
        {
            "concept_code": "C1",
            "old_pairs": (
                {
                    "axis": "op:CellOfOrigin",
                    "filler_code": "MINT-abcdef123456",
                },
            ),
            "new_pairs": (),
            "r101_old_pairs": (),
            "source_occurrences": (
                _occurrence("a", "C10"),
                _occurrence("b", "C11"),
            ),
        }
    )

    assert candidate.old_pairs[0].filler_code == "MINT-abcdef123456"


class _Store:
    def __init__(
        self,
        run: CompletedRunForEvidence,
        new_run: CompletedRunForEvidence | None = None,
    ) -> None:
        self.run = run
        self.new_run = new_run
        self.queries = 0

    async def completed_run_for_evidence(self, run_id: str) -> CompletedRunForEvidence:
        if run_id == self.run.run_id:
            return self.run
        assert self.new_run is not None
        assert run_id == self.new_run.run_id
        return self.new_run

    async def r101_conservation_candidates(
        self, old_run_id: str, new_run_id: str
    ) -> tuple[CandidateBaseline, ...]:
        self.queries += 1
        assert old_run_id == self.run.run_id
        assert self.new_run is not None
        assert new_run_id == self.new_run.run_id
        return (
            CandidateBaseline(
                concept_code="C1",
                old_pairs=(Pair(axis="op:PrimarySite", filler_code="C10"),),
                new_pairs=(
                    Pair(axis="op:AssociatedRegion", filler_code="C10"),
                    Pair(axis="op:PrimarySite", filler_code="C11"),
                ),
                r101_old_pairs=(Pair(axis="op:PrimarySite", filler_code="C10"),),
                r101_new_pairs=(
                    Pair(axis="op:AssociatedRegion", filler_code="C10"),
                    Pair(axis="op:PrimarySite", filler_code="C11"),
                ),
                source_occurrences=(
                    _occurrence("a", "C10"),
                    _occurrence("b", "C11"),
                ),
                occurrence_links=(
                    _links(
                        "a",
                        old=(Pair(axis="op:PrimarySite", filler_code="C10"),),
                        new=(Pair(axis="op:AssociatedRegion", filler_code="C10"),),
                    ),
                    _links(
                        "b",
                        new=(Pair(axis="op:PrimarySite", filler_code="C11"),),
                    ),
                ),
            ),
        )


class _LossStore(_Store):
    async def r101_conservation_candidates(
        self, old_run_id: str, new_run_id: str
    ) -> tuple[CandidateBaseline, ...]:
        self.queries += 1
        old = Pair(axis="op:PrimarySite", filler_code="C10")
        return (
            CandidateBaseline(
                concept_code="C1",
                old_pairs=(old,),
                new_pairs=(),
                r101_old_pairs=(old,),
                r101_new_pairs=(),
                source_occurrences=(_occurrence("a", "C10"),),
                occurrence_links=(_links("a", old=(old,)),),
            ),
        )


@pytest.mark.unit
async def test_report_binds_baseline_v3_rerun_v4_and_uses_one_candidate_query(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    run = _run(
        fingerprint=_fingerprint(
            worklist=("C1",),
        ),
    )
    new_run = _new_run(
        fingerprint=_fingerprint(
            worklist=("C1",), algorithm_version="decomposition-v4"
        ),
    )
    _baseline(baseline_path, run)
    store = _Store(run, new_run)

    async def semantic_types(codes: tuple[str, ...]) -> dict[str, str | None]:
        assert codes == ("C10", "C11")
        return {"C10": "Body Location or Region", "C11": None}

    async def live_r82(_codes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        return ()

    report = await generate_r101_conservation_report(
        baseline_path=baseline_path,
        run_id=run.run_id,
        new_run_id=new_run.run_id,
        expected_source_identity=run.fingerprint.source_identity,
        expected_release="26.07d",
        pre_resume_proof_identity="1" * 64,
        resume_dry_run_identity="2" * 64,
        mixed_cohort_identity="3" * 64,
        store=store,
        resolve_semantic_types=semantic_types,
        resolve_live_r82=live_r82,
    )

    assert store.queries == 1
    assert report.old_algorithm_version == "decomposition-v3"
    assert report.new_algorithm_version == "decomposition-v4"
    assert report.old_run_fingerprint_identity == run.fingerprint.identity
    assert report.old_representation_identity == run.representation_identity
    assert report.new_run_id == new_run.run_id
    assert report.new_run_fingerprint_identity == new_run.fingerprint.identity
    assert report.new_representation_identity == new_run.representation_identity
    assert report.new_detector_identity == r101_conservation_detector_identity()
    assert report.pre_resume_proof_identity == "1" * 64
    assert report.resume_dry_run_identity == "2" * 64
    assert report.mixed_cohort_identity == "3" * 64
    assert report.candidate_count == 1
    assert report.occurrence_count == 2
    assert report.r101_changed_pair_count == 3
    assert report.non_r101_changed_pair_count == 0
    assert report.unresolved_loss_count == 0
    assert report.authorizable is True
    assert [item.kind for item in report.concepts[0].occurrence_dispositions] == [
        "projected",
        "projected",
    ]
    assert report.report_identity


@pytest.mark.unit
async def test_known_reroute_does_not_issue_unneeded_live_r82_query(
    tmp_path: Path,
) -> None:
    run = _run()
    baseline_path = tmp_path / "baseline.json"
    _baseline(baseline_path, run)
    live_queries = 0

    async def semantic_types(codes: tuple[str, ...]) -> dict[str, str | None]:
        return dict.fromkeys(codes, "Body Part, Organ, or Organ Component")

    async def live_r82(_codes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        nonlocal live_queries
        live_queries += 1
        return ()

    report = await generate_r101_conservation_report(
        baseline_path=baseline_path,
        run_id=run.run_id,
        new_run_id="new-run",
        expected_source_identity=run.fingerprint.source_identity,
        expected_release=run.ncit_version,
        pre_resume_proof_identity="1" * 64,
        resume_dry_run_identity="2" * 64,
        mixed_cohort_identity="3" * 64,
        store=_Store(run, _new_run()),
        resolve_semantic_types=semantic_types,
        resolve_live_r82=live_r82,
    )

    assert live_queries == 0
    assert report.r101_changed_pair_count == 3


@pytest.mark.unit
async def test_report_emits_progress_and_heartbeat_while_rerun_is_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    baseline_path = tmp_path / "baseline.json"
    _baseline(baseline_path, run)
    release = asyncio.Event()
    heartbeat_seen = asyncio.Event()
    progress: list[R101ConservationProgress] = []

    async def semantic_types(codes: tuple[str, ...]) -> dict[str, str | None]:
        return dict.fromkeys(codes)

    async def live_r82(_codes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        await release.wait()
        return ()

    def record(event: R101ConservationProgress) -> None:
        progress.append(event)
        if event.phase == "heartbeat":
            heartbeat_seen.set()

    monkeypatch.setattr(
        "ontolib.decomposition.r101_conservation._PROGRESS_HEARTBEAT_SECONDS",
        0.001,
    )
    task = asyncio.create_task(
        generate_r101_conservation_report(
            baseline_path=baseline_path,
            run_id=run.run_id,
            new_run_id="new-run",
            expected_source_identity=run.fingerprint.source_identity,
            expected_release=run.ncit_version,
            pre_resume_proof_identity="1" * 64,
            resume_dry_run_identity="2" * 64,
            mixed_cohort_identity="3" * 64,
            store=_LossStore(run, _new_run()),
            resolve_semantic_types=semantic_types,
            resolve_live_r82=live_r82,
            progress=record,
        )
    )
    await heartbeat_seen.wait()
    release.set()
    await task

    assert progress[0].phase == "started"
    assert any(event.phase == "heartbeat" for event in progress)
    assert progress[-1].phase == "completed"
    assert progress[-1].completed == progress[-1].total == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("run", "message"),
    [
        (_run(fingerprint=_fingerprint(algorithm_version="decomposition-v2")), "v3"),
        (_run(ncit_version="26.08a"), "release"),
        (_run(fingerprint=_fingerprint(source_identity="f" * 64)), "source"),
    ],
)
async def test_report_refuses_wrong_baseline_run(
    tmp_path: Path, run: CompletedRunForEvidence, message: str
) -> None:
    baseline_path = tmp_path / "baseline.json"
    valid_run = _run()
    _baseline(baseline_path, valid_run)
    run = run.model_copy(update={"run_id": valid_run.run_id})

    with pytest.raises(R101ConservationValidationError, match=message):
        await generate_r101_conservation_report(
            baseline_path=baseline_path,
            run_id="old-run",
            new_run_id="new-run",
            expected_source_identity=valid_run.fingerprint.source_identity,
            expected_release="26.07d",
            pre_resume_proof_identity="1" * 64,
            resume_dry_run_identity="2" * 64,
            mixed_cohort_identity="3" * 64,
            store=_Store(run, _new_run()),
            resolve_semantic_types=lambda _codes: pytest.fail(
                "must not resolve semantics"
            ),
            resolve_live_r82=lambda _codes: pytest.fail("must not query R82"),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "drift",
    [
        {"config_version": "different-config"},
        {"walker_max_depth": 8},
        {"semantic_types": ("Disease or Syndrome",)},
        {"output_mode": "none", "load_mode": "none"},
        {"load_mode": "none"},
    ],
)
async def test_report_refuses_every_semantic_fingerprint_dimension_drift(
    tmp_path: Path, drift: dict[str, object]
) -> None:
    run = _run()
    baseline_path = tmp_path / "baseline.json"
    _baseline(baseline_path, run)
    new_fingerprint = _fingerprint(algorithm_version="decomposition-v4", **drift)

    with pytest.raises(R101ConservationValidationError, match="fingerprint dimension"):
        await generate_r101_conservation_report(
            baseline_path=baseline_path,
            run_id=run.run_id,
            new_run_id="new-run",
            expected_source_identity=run.fingerprint.source_identity,
            expected_release=run.ncit_version,
            pre_resume_proof_identity="1" * 64,
            resume_dry_run_identity="2" * 64,
            mixed_cohort_identity="3" * 64,
            store=_Store(run, _new_run(fingerprint=new_fingerprint)),
            resolve_semantic_types=lambda _codes: pytest.fail(
                "must not resolve semantics"
            ),
            resolve_live_r82=lambda _codes: pytest.fail("must not query R82"),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("new_run", "message"),
    [
        (
            _new_run(fingerprint=_fingerprint(algorithm_version="decomposition-v5")),
            "must use decomposition v4",
        ),
        (_new_run(ncit_version="26.08a"), "source or release"),
    ],
)
async def test_report_rejects_wrong_replay_algorithm_or_release(
    tmp_path: Path, new_run: CompletedRunForEvidence, message: str
) -> None:
    run = _run()
    baseline_path = tmp_path / "baseline.json"
    _baseline(baseline_path, run)

    with pytest.raises(R101ConservationValidationError, match=message):
        await generate_r101_conservation_report(
            baseline_path=baseline_path,
            run_id=run.run_id,
            new_run_id=new_run.run_id,
            expected_source_identity=run.fingerprint.source_identity,
            expected_release=run.ncit_version,
            pre_resume_proof_identity="1" * 64,
            resume_dry_run_identity="2" * 64,
            mixed_cohort_identity="3" * 64,
            store=_Store(run, new_run),
            resolve_semantic_types=lambda _codes: pytest.fail(
                "must reject before semantic lookup"
            ),
            resolve_live_r82=lambda _codes: pytest.fail("must reject before R82"),
        )
