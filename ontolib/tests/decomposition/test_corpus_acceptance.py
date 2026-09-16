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
    CorpusAcceptanceCandidate,
    CorpusAcceptanceContent,
    CorpusAcceptanceValidationError,
    ExcludedPairChange,
    GateEvaluation,
    PendingHumanAcceptanceDecision,
    PublicationDryRunEvidence,
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
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    CompletedRunForEvidence,
    RunFingerprint,
)
from ontolib.decomposition.r101_conservation import (
    ClassifiedNonR101Delta,
    NonR101MetadataDelta,
    load_r101_conservation_report,
)
from ontolib.terminologies.ncit.sibling_store import (
    NcitSiblingStoreManifest,
    SiblingStoreValidationError,
)

GOLDEN = Path(__file__).with_name("golden")
ROOT = Path(__file__).parents[3]
SHA = "a" * 64


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
    direction: str = "grouping-disputed",
) -> ReviewRequiredEffectiveExclusion:
    return ReviewRequiredEffectiveExclusion.model_validate(
        {
            "concept_code": "C1",
            "pair_changes": (
                {
                    "axis": "op:Morphology",
                    "filler_code": filler,
                    "comparison_direction": direction,
                },
            ),
            "source_assertion_identities": ("1" * 64,),
            "evidence_identities": ("2" * 64,),
            "reason": "unresolved-semantic-ambiguity",
            "delta": "removed-from-effective",
            "official_source_preserved": True,
            "human_approval": False,
            "nci_approval": False,
        }
    )


@pytest.fixture(scope="module")
def exclusions():  # type: ignore[no-untyped-def]
    return build_review_required_exclusions(
        ROOT / "evidence/group-review-packet-26.07d-schema3.json",
        ROOT / "evidence/group-review-rationale-26.07d.md",
    )


@pytest.fixture(scope="module")
def report():  # type: ignore[no-untyped-def]
    return load_r101_conservation_report(
        GOLDEN / "neoplasm-r101-v5-conservation.json.gz"
    )


@pytest.fixture(scope="module")
def classification(report, exclusions):  # type: ignore[no-untyped-def]
    return classify_corpus_delta(report, exclusions=exclusions)


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
    assert [
        (p.axis, p.filler_code, p.comparison_direction)
        for p in by_code["C27262"].pair_changes
    ] == [
        ("op:AssociatedRegion", "C41165", "missing-from-candidate"),
        ("op:ClinicalFinding", "C36220", "missing-from-candidate"),
        ("op:ClinicalFinding", "C41397", "missing-from-candidate"),
        ("op:Morphology", "C35501", "grouping-disputed"),
        ("op:Morphology", "C9290", "grouping-disputed"),
    ]
    assert [
        (p.axis, p.filler_code, p.comparison_direction)
        for p in by_code["C102870"].pair_changes
    ] == [
        ("op:AssociatedSite", "C12321", "missing-from-candidate"),
        ("op:Morphology", "C121619", "grouping-disputed"),
        ("op:Morphology", "C39986", "grouping-disputed"),
        ("op:PrimarySite", "C12404", "missing-from-candidate"),
    ]
    assert len(by_code["C198031"].pair_changes) == 5
    assert len(by_code["C35756"].pair_changes) == 16
    assert all(item.delta == "removed-from-effective" for item in exclusions)
    assert all(item.official_source_preserved for item in exclusions)
    assert all(
        item.human_approval is False and item.nci_approval is False
        for item in exclusions
    )
    assert all(
        item.source_assertion_identities and item.evidence_identities
        for item in exclusions
    )


@pytest.mark.unit
def test_review_required_exclusions_refuse_missing_target_concept(
    monkeypatch: pytest.MonkeyPatch,
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

    with pytest.raises(CorpusAcceptanceValidationError, match="lacks an exclusion"):
        build_review_required_exclusions(
            packet_path,
            ROOT / "evidence/group-review-rationale-26.07d.md",
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
    exclusion = ReviewRequiredEffectiveExclusion(
        concept_code="C1",
        pair_changes=(
            ExcludedPairChange(
                axis="op:Morphology",
                filler_code="C2",
                comparison_direction="grouping-disputed",
            ),
        ),
        source_assertion_identities=("1" * 64,),
        evidence_identities=("2" * 64,),
        reason="unresolved-semantic-ambiguity",
        delta="removed-from-effective",
        official_source_preserved=True,
        human_approval=False,
        nci_approval=False,
    )

    observed = build_effective_artifact(
        source_artifact=source,
        destination=effective,
        exclusions=(exclusion,),
    )

    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before
    assert observed.removed_pair_count == 1
    assert observed.removed_pairs == (
        ("C1", "op:Morphology", "C2", "grouping-disputed"),
    )
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

    with pytest.raises(CorpusAcceptanceValidationError, match="lacks exact"):
        build_effective_artifact(
            source_artifact=source,
            destination=destination,
            exclusions=(_exclusion(filler="C9"),),
        )
    with pytest.raises(CorpusAcceptanceValidationError, match="ambiguous"):
        build_effective_artifact(
            source_artifact=source,
            destination=destination,
            exclusions=(
                _exclusion(),
                _exclusion(direction="added-to-candidate"),
            ),
        )
    destination.write_text("already present")
    with pytest.raises(CorpusAcceptanceValidationError, match="destination exists"):
        build_effective_artifact(
            source_artifact=source,
            destination=destination,
            exclusions=(_exclusion(),),
        )
    destination.unlink()
    with pytest.raises(
        CorpusAcceptanceValidationError, match="nonempty effective delta"
    ):
        build_effective_artifact(
            source_artifact=source,
            destination=destination,
            exclusions=(_exclusion(filler="C9", direction="missing-from-candidate"),),
        )


def _gate_paths(tmp_path: Path, *, passing: bool) -> dict[str, Path]:
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
        },
        "gate_liveness": {
            "status": "passed" if passing else "failed",
            "observed_exit_code": 0 if passing else 1,
            "git_head": "a" * 40 if passing else "b" * 40,
        },
        "proposal_registry": {"schema_version": 1},
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
        _gate_paths(tmp_path, passing=passing), git_head="a" * 40
    )

    expected = "passed" if passing else "failed"
    assert gates.primary_site_cardinality.status == expected
    assert gates.proposal_provenance.status == "passed"
    assert gates.projection_loss.status == "blocked"
    assert gates.residual.status == "blocked"
    assert gates.fidelity.status == expected
    assert gates.issue_274_detector.status == expected
    assert gates.gate_liveness.status == ("passed" if passing else "blocked")


@pytest.mark.unit
def test_candidate_gates_fail_closed_at_each_short_circuit(tmp_path: Path) -> None:
    paths = _gate_paths(tmp_path, passing=True)
    paths["machine_readiness"].write_text(
        json.dumps(
            {
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

    first = corpus_acceptance_module._candidate_gates(paths, git_head="a" * 40)
    assert first.fidelity.status == "failed"
    assert first.issue_274_detector.status == "failed"
    assert first.gate_liveness.status == "blocked"

    paths["gate_liveness"].write_text(
        json.dumps({"status": "passed", "observed_exit_code": 0, "git_head": "b" * 40})
    )
    second = corpus_acceptance_module._candidate_gates(paths, git_head="a" * 40)
    assert second.gate_liveness.status == "blocked"


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
        corpus_acceptance_module._candidate_gates(paths, git_head="a" * 40)


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
        lambda _packet, _rationale: exclusions,
    )
    monkeypatch.setattr(
        corpus_acceptance_module,
        "classify_corpus_delta",
        lambda _report, *, exclusions: classification,
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
    result = classify_corpus_delta(report, exclusions=exclusions)

    assert result.structural_object_count == 2_097
    assert result.metadata_object_count == 38_648
    assert result.raw_row_count == 79_393
    assert len(result.classifications) == 40_745
    assert result.category_counts == {
        "group-identity-rebinding": 35_017,
        "semantic-routing-change": 877,
        "unexplained-blocker": 4_851,
    }
    assert len(result.unexplained_blockers) == 4_851
    assert result.causal_attribution == "prohibited"


@pytest.mark.unit
def test_total_classifier_refuses_duplicate_or_omitted_objects(
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    report = load_r101_conservation_report(
        GOLDEN / "neoplasm-r101-v5-conservation.json.gz"
    )
    result = classify_corpus_delta(report, exclusions=exclusions)
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
        ("excluded", "review-required-effective-exclusion"),
        ("minted", "proposal-minted-projection"),
        ("unexplained", "unexplained-blocker"),
        ("r101-linked", "r101-occurrence-linked-output-delta"),
    ],
)
def test_structural_classifier_preserves_distinct_evidence_shapes(
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
    shape: str,
    expected: str,
) -> None:
    evidence = report.non_r101_delta_evidence
    row = evidence.rows[0]
    selected_exclusions = ()
    classified = ()
    rows = (row,)
    if shape == "excluded":
        selected_exclusions = (
            exclusions[0].model_copy(
                update={
                    "concept_code": row.concept_code,
                    "pair_changes": (
                        ExcludedPairChange(
                            axis=row.axis,
                            filler_code=row.filler_code,
                            comparison_direction="grouping-disputed",
                        ),
                    ),
                }
            ),
        )
    elif shape == "minted":
        rows = (row.model_copy(update={"filler_code": "MINT-test"}),)
    elif shape == "unexplained":
        rows = (row.model_copy(update={"needs_review": False}),)
    else:
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

    result = classify_corpus_delta(shaped_report, exclusions=selected_exclusions)

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

    result = classify_corpus_delta(shaped_report, exclusions=())

    assert result.category_counts == {expected: 1}


@pytest.mark.unit
def test_metadata_classifier_routes_only_the_exact_excluded_pair(
    report,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    evidence = report.non_r101_delta_evidence
    delta = evidence.metadata_deltas[0]
    exclusion = exclusions[0].model_copy(
        update={
            "concept_code": delta.new.concept_code,
            "pair_changes": (
                ExcludedPairChange(
                    axis=delta.new.axis,
                    filler_code=delta.new.filler_code,
                    comparison_direction="grouping-disputed",
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

    result = classify_corpus_delta(shaped_report, exclusions=(exclusion,))

    assert result.category_counts == {"review-required-effective-exclusion": 1}
    assert set(exclusion.evidence_identities) < set(
        result.classifications[0].evidence_identities
    )


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
                ExcludedPairChange(
                    axis="op:AnotherAxis",
                    filler_code="C1",
                    comparison_direction="grouping-disputed",
                ),
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

    result = classify_corpus_delta(
        report.model_copy(update={"non_r101_delta_evidence": shaped}),
        exclusions=(exclusion,),
    )

    assert result.classifications[0].category != ("review-required-effective-exclusion")
    assert not set(result.classifications[0].evidence_identities).intersection(
        exclusion.evidence_identities
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
        classify_corpus_delta(
            report.model_copy(update={"non_r101_delta_evidence": shaped}),
            exclusions=(),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("raw_row_count", 79_392, "raw changed-row arithmetic differs"),
        ("category_counts", {}, "classification category counts differ"),
        ("unexplained_blockers", (SHA,), "unexplained blocker inventory differs"),
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
    pair = ExcludedPairChange(
        axis="op:Morphology",
        filler_code="C1",
        comparison_direction="grouping-disputed",
    )
    common = {
        "concept_code": "C2",
        "pair_changes": (pair,),
        "source_assertion_identities": ("1" * 64,),
        "evidence_identities": ("2" * 64,),
        "reason": "unresolved-semantic-ambiguity",
        "delta": "removed-from-effective",
        "official_source_preserved": True,
        "human_approval": False,
        "nci_approval": False,
    }
    with pytest.raises(ValidationError, match="canonical and unique"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"pair_changes": (pair, pair)}
        )
    with pytest.raises(ValidationError, match="source assertion identities"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"source_assertion_identities": ("not-a-digest",)}
        )
    with pytest.raises(ValidationError, match="review evidence identities"):
        ReviewRequiredEffectiveExclusion.model_validate(
            common | {"evidence_identities": ("2" * 64, "2" * 64)}
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
            "semantic_excluded_count": 3,
            "unknown_count": 139,
            "source_occurrence_count": 370_253,
            "selected_occurrence_count": 108_217,
            "emitted_constituent_pair_count": 144_231,
            "complete_semantic_fact_count": 845_825,
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
                "gate_liveness",
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
            "review_packet_identity": "4" * 64,
            "review_decisions_identity": "5" * 64,
            "gate_liveness_evidence_identity": "6" * 64,
            "old_comparator_artifact_identity": "7" * 64,
            "new_comparator_artifact_identity": "8" * 64,
        },
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
        decision_evidence_identity="4" * 64,
    )
    build_accepted_publication_artifact(
        source_artifact=source,
        destination=output,
        candidate_identity=SHA,
        dry_run=dry_run,
        decision=accepted,
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
        decision_evidence_identity="1" * 64,
    )
    require_publication_authorization(
        candidate_identity=SHA,
        dry_run=dry_run,
        decision=accepted,
    )

    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        require_publication_authorization(
            candidate_identity="2" * 64,
            dry_run=dry_run,
            decision=accepted,
        )
    with pytest.raises(CorpusAcceptanceValidationError, match="binding differs"):
        require_publication_authorization(
            candidate_identity=SHA,
            dry_run=dry_run,
            decision=accepted.model_copy(
                update={"publication_dry_run_identity": "2" * 64}
            ),
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
            representation_identity=SHA,
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
            representation_identity=representation_identity,
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
            representation_identity=SHA,
            artifact=artifact,
            destination_graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
            expected_codes=(),
            expected_worklist_count=1,
            graph=object(),  # type: ignore[arg-type]
            provenance=_PublicationStore(_publication_run()),
        )


@pytest.mark.unit
@pytest.mark.parametrize("gate", ["projection_loss", "residual", "fidelity"])
def test_failed_mechanical_gate_is_represented_and_blocks_candidate(
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
    assert candidate.status == "machine-blocked"


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
def test_candidate_finalization_preserves_machine_blockers_without_requesting_human(
    classification,  # type: ignore[no-untyped-def]
    exclusions,  # type: ignore[no-untyped-def]
) -> None:
    content = _content(classification, exclusions)
    candidate = finalize_corpus_acceptance_candidate(
        content, _dry_run(content.content_identity)
    )

    assert isinstance(candidate, CorpusAcceptanceCandidate)
    assert candidate.status == "machine-blocked"
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
    unexplained = evidence.rows[0].model_copy(update={"needs_review": False})
    shaped_evidence = evidence.model_copy(
        update={
            "rows": (unexplained,),
            "metadata_deltas": (),
            "classified_rows": (),
            "raw_typed_delta_count": 1,
        }
    )
    classification = classify_corpus_delta(
        report.model_copy(update={"non_r101_delta_evidence": shaped_evidence}),
        exclusions=(),
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
        ("status", "ready-for-human-authorization", "candidate status differs"),
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
