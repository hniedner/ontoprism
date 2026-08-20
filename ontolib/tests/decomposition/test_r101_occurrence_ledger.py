from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from scripts.adjudication import main as adjudication_main

import ontolib.decomposition.r101_conservation as r101_module
import ontolib.decomposition.stated_queries as stated_queries_module
from ontolib.decomposition.r101_conservation import (
    STRUCTURAL_KEY_FIELDS,
    ContentAuthorization,
    LedgerBuildContext,
    LedgerCounts,
    NonR101DeltaEvidence,
    NonR101DeltaRow,
    OccurrenceInput,
    Pair,
    QueryMetrics,
    R82Path,
    R82PathEdge,
    R101ConservationValidationError,
    R101LedgerSource,
    StructuralOccurrence,
    build_r101_occurrence_ledger,
    load_r101_conservation_report,
    r101_detector_identity,
    r101_ledger_query_identity,
    r101_ledger_tsv_bytes,
    r101_proof_identity,
    read_r101_ledger_tsv,
    validate_r101_consumer_dry_run,
    validate_r101_publication,
    write_r101_occurrence_ledger,
)


def _occurrence(identifier: str = "a", **changes: object) -> StructuralOccurrence:
    values: dict[str, object] = {
        "concept_code": "C1",
        "occurrence_id": identifier * 64,
        "source_fact_id": "b" * 64,
        "source_group_id": "c" * 64,
        "anchor_code": "C1",
        "depth": 2,
        "role_code": "R101",
        "filler_code": "C30",
        "structural_path": (0, 2),
        "member_position": 2,
    }
    values.update(changes)
    return StructuralOccurrence.model_validate(values)


def _input(
    *,
    old: tuple[Pair, ...] = (),
    new: tuple[Pair, ...] = (),
    retained: tuple[Pair, ...] = (),
    identifier: str = "a",
) -> OccurrenceInput:
    occurrence = _occurrence(identifier)
    return OccurrenceInput(
        old_occurrence=occurrence,
        new_occurrence=occurrence,
        old_links=old,
        new_links=new,
        retained_new_r101_links=retained,
    )


def _edge(part: str, whole: str, identifier: str = "d") -> R82PathEdge:
    restriction = f"bn-{identifier}"
    return R82PathEdge(
        part_code=part,
        asserted_part_code=part,
        whole_code=whole,
        restriction_node_id=restriction,
        fact_identity=_fact_identity("e" * 64, part, whole, restriction),
        source_identity="e" * 64,
    )


def _fact_identity(
    source: str, asserted_part: str, whole: str, restriction: str
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "asserted_part": asserted_part,
                "restriction_node": restriction,
                "role_code": "R82",
                "source_identity": source,
                "whole": whole,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _path(*edges: R82PathEdge) -> R82Path:
    return R82Path(edges=edges)


def _context(**changes: object) -> LedgerBuildContext:
    values: dict[str, object] = {
        "source_identity": "e" * 64,
        "source_release_id": "26.07d",
        "old_run_id": "old",
        "old_run_fingerprint_identity": "1" * 64,
        "old_representation_identity": "2" * 64,
        "old_baseline_identity": "3" * 64,
        "new_run_id": "new",
        "new_run_fingerprint_identity": "4" * 64,
        "new_representation_identity": "5" * 64,
        "detector_identity": r101_detector_identity(),
        "pre_resume_proof_identity": "8" * 64,
        "resume_dry_run_identity": "9" * 64,
        "mixed_cohort_identity": "a" * 64,
        "proof_identity": r101_proof_identity("8" * 64, "9" * 64, "a" * 64),
        "adapter_id": "ncit-stated-r82-v1",
        "query_metrics": QueryMetrics(
            postgres_query_count=1,
            qlever_query_count=2,
            max_pair_batch_size=8,
            max_r82_hops=8,
            max_asserted_superclass_hops=20,
        ),
        "non_r101_delta_evidence": _delta_evidence(),
    }
    values.update(changes)
    return LedgerBuildContext.model_validate(values)


def _delta_evidence(
    *rows: NonR101DeltaRow,
    old_run_id: str = "old",
    new_run_id: str = "new",
) -> NonR101DeltaEvidence:
    return NonR101DeltaEvidence(
        old_run_id=old_run_id,
        new_run_id=new_run_id,
        query_identity=r101_ledger_query_identity(),
        rows=rows,
    )


@pytest.mark.unit
def test_full_structural_key_survives_model_json_and_lossless_tsv() -> None:
    pair = Pair(axis="op:PrimarySite", filler_code="C30")
    report = build_r101_occurrence_ledger(
        (_input(new=(pair,)),), paths={}, context=_context()
    )

    restored = read_r101_ledger_tsv(r101_ledger_tsv_bytes(report))
    original_key = tuple(
        getattr(report.occurrences[0], field) for field in STRUCTURAL_KEY_FIELDS
    )
    assert STRUCTURAL_KEY_FIELDS == (
        "concept_code",
        "occurrence_id",
        "source_fact_id",
        "source_group_id",
        "anchor_code",
        "depth",
        "role_code",
        "filler_code",
        "structural_path",
        "member_position",
    )
    assert tuple(getattr(restored[0], field) for field in STRUCTURAL_KEY_FIELDS) == (
        original_key
    )
    assert restored[0] == report.occurrences[0]

    minted = Pair(axis="op:CellOfOrigin", filler_code="MINT-abcdef123456")
    minted_report = build_r101_occurrence_ledger(
        (_input(old=(minted,)),), paths={}, context=_context()
    )
    assert read_r101_ledger_tsv(r101_ledger_tsv_bytes(minted_report))[0].old_links == (
        minted,
    )


@pytest.mark.unit
def test_no_links_in_either_run_is_explicit_unchanged_unprojected_evidence() -> None:
    report = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
    row = report.occurrences[0]
    assert row.disposition == "unchanged-unprojected"
    assert row.disposition_reason == "explicit-no-old-or-new-links"
    assert row.old_links == row.new_links == ()
    assert row.r82_evidence_kind == "none"


@pytest.mark.unit
def test_direct_and_transitive_paths_are_ordered_and_counted_independently() -> None:
    broad1 = Pair(axis="op:PrimarySite", filler_code="C30")
    narrow1 = Pair(axis="op:PrimarySite", filler_code="C20")
    broad2 = Pair(axis="op:PrimarySite", filler_code="C60")
    narrow2 = Pair(axis="op:PrimarySite", filler_code="C40")
    report = build_r101_occurrence_ledger(
        (
            _input(old=(broad1,), retained=(narrow1,), identifier="a"),
            _input(old=(broad2,), retained=(narrow2,), identifier="f"),
        ),
        paths={
            ("C20", "C30"): _path(_edge("C20", "C30")),
            ("C40", "C60"): _path(_edge("C40", "C50", "8"), _edge("C50", "C60", "9")),
        },
        context=_context(),
    )

    assert [item.r82_evidence_kind for item in report.occurrences] == [
        "one-step",
        "closure-only",
    ]
    assert [item.path_length for item in report.occurrences] == [1, 2]
    assert report.counts.one_step == 1
    assert report.counts.closure_only == 1
    assert report.occurrences[1].r82_path == (
        _edge("C40", "C50", "8"),
        _edge("C50", "C60", "9"),
    )

    multi_old = build_r101_occurrence_ledger(
        (
            _input(
                old=(broad1, Pair(axis="op:PrimarySite", filler_code="C31")),
                retained=(narrow1,),
            ),
        ),
        paths={("C20", "C30"): _path(_edge("C20", "C30"))},
        context=_context(),
    )
    assert multi_old.grouping_presentation == ()


@pytest.mark.unit
def test_one_step_and_closure_are_the_only_r82_evidence_count_partitions() -> None:
    assert "retained_direct" not in LedgerCounts.model_fields
    assert "retained-direct" not in json.dumps(
        type(
            build_r101_occurrence_ledger(
                (_input(),), paths={}, context=_context()
            ).occurrences[0]
        ).model_json_schema()
    )


@pytest.mark.unit
def test_r82_edge_carries_replayable_asserted_subject_and_validates_fact_identity() -> (
    None
):
    edge = R82PathEdge.model_validate(
        {
            **_edge("C20", "C30").model_dump(),
            "asserted_part_code": "C20",
        }
    )
    assert edge.asserted_part_code == "C20"
    assert edge.fact_identity == _fact_identity(
        edge.source_identity,
        edge.asserted_part_code,
        edge.whole_code,
        edge.restriction_node_id,
    )
    with pytest.raises(ValidationError, match="R82 fact identity"):
        edge.model_copy(update={"fact_identity": "0" * 64}).model_validate(
            edge.model_copy(update={"fact_identity": "0" * 64}).model_dump()
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "max_hops", "reason"),
    [
        (_path(_edge("C30", "C20")), 8, "reversed-r82"),
        (
            _path(_edge("C20", "C25"), _edge("C26", "C30")),
            8,
            "broken-r82-path",
        ),
        (
            _path(_edge("C20", "C25"), _edge("C25", "C30")),
            1,
            "r82-depth-exceeded",
        ),
        (_path(), 8, "broken-r82-path"),
    ],
)
def test_real_path_mutations_fail_closed_as_exact_unresolved_refusals(
    path: R82Path, max_hops: int, reason: str
) -> None:
    broad = Pair(axis="op:PrimarySite", filler_code="C30")
    narrow = Pair(axis="op:PrimarySite", filler_code="C20")
    report = build_r101_occurrence_ledger(
        (_input(old=(broad,), retained=(narrow,)),),
        paths={("C20", "C30"): path},
        context=_context(
            query_metrics=_context().query_metrics.model_copy(
                update={"max_r82_hops": max_hops}
            )
        ),
    )
    assert report.occurrences[0].disposition == "unresolved"
    assert report.occurrences[0].disposition_reason == reason

    missing_path = build_r101_occurrence_ledger(
        (_input(old=(broad,), retained=(narrow,)),),
        paths={},
        context=_context(),
    )
    assert missing_path.occurrences[0].disposition_reason == "unresolved-disposition"

    wrong_endpoint = build_r101_occurrence_ledger(
        (_input(old=(broad,), retained=(narrow,)),),
        paths={("C20", "C30"): _path(_edge("C21", "C30"))},
        context=_context(),
    )
    assert wrong_endpoint.occurrences[0].disposition_reason == "broken-r82-path"


def _rebind_hand_edited_report(payload: dict[str, Any]) -> str:
    sample = build_r101_occurrence_ledger(
        (_input(),), paths={}, context=_context()
    ).occurrences[0]
    occurrence_type = type(sample)
    occurrences = tuple(
        occurrence_type.model_validate_json(json.dumps(item))
        for item in payload["occurrences"]
    )
    payload["tsv_identity"] = hashlib.sha256(
        r101_module._tsv_content(occurrences)
    ).hexdigest()
    payload["json_identity"] = r101_module._json_identity(payload)
    payload["report_identity"] = r101_module._report_identity(payload)
    return json.dumps(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    ["reversed", "disconnected", "endpoint", "source", "depth", "cross-axis"],
)
def test_report_loader_rejects_rebound_invalid_multi_edge_r82_paths(
    mutation: str,
) -> None:
    broad = Pair(axis="op:PrimarySite", filler_code="C30")
    narrow = Pair(axis="op:PrimarySite", filler_code="C20")
    report = build_r101_occurrence_ledger(
        (_input(old=(broad,), retained=(narrow,)),),
        paths={
            ("C20", "C30"): _path(_edge("C20", "C25", "8"), _edge("C25", "C30", "9"))
        },
        context=_context(),
    )
    payload = report.model_dump(mode="json")
    occurrence = payload["occurrences"][0]
    edges = occurrence["r82_path"]
    if mutation == "reversed":
        edges[:] = [
            _edge("C30", "C25", "a").model_dump(mode="json"),
            _edge("C25", "C20", "b").model_dump(mode="json"),
        ]
    elif mutation == "disconnected":
        edges[1] = _edge("C26", "C30", "a").model_dump(mode="json")
    elif mutation == "endpoint":
        edges[1] = _edge("C25", "C31", "a").model_dump(mode="json")
    elif mutation == "source":
        edges[1]["source_identity"] = "f" * 64
        edges[1]["fact_identity"] = _fact_identity(
            "f" * 64,
            edges[1]["asserted_part_code"],
            edges[1]["whole_code"],
            edges[1]["restriction_node_id"],
        )
    elif mutation == "depth":
        payload["query_metrics"]["max_r82_hops"] = 1
    else:
        occurrence["retained_r82_target"]["axis"] = "op:AssociatedRegion"

    rebound = _rebind_hand_edited_report(payload)
    with pytest.raises(ValidationError, match=r"R82|r82|axis|source|depth|path"):
        type(report).model_validate_json(rebound)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("old", "retained", "path", "source_identity", "reason"),
    [
        (
            Pair(axis="op:PrimarySite", filler_code="C30"),
            Pair(axis="op:AssociatedRegion", filler_code="C20"),
            _path(_edge("C20", "C30")),
            "e" * 64,
            "cross-axis-coverage",
        ),
        (
            Pair(axis="op:PrimarySite", filler_code="C30"),
            Pair(axis="op:PrimarySite", filler_code="C20"),
            _path(
                _edge("C20", "C30", "d").model_copy(
                    update={
                        "source_identity": "f" * 64,
                        "fact_identity": _fact_identity("f" * 64, "C20", "C30", "bn-d"),
                    }
                )
            ),
            "e" * 64,
            "source-identity-mismatch",
        ),
    ],
)
def test_coverage_requires_same_axis_source_identity_and_new_r101_link(
    old: Pair,
    retained: Pair,
    path: R82Path,
    source_identity: str,
    reason: str,
) -> None:
    report = build_r101_occurrence_ledger(
        (_input(old=(old,), retained=(retained,)),),
        paths={(retained.filler_code, old.filler_code): path},
        context=_context(source_identity=source_identity),
    )
    assert report.occurrences[0].disposition_reason == reason

    absent_link = build_r101_occurrence_ledger(
        (_input(old=(old,), retained=()),), paths={}, context=_context()
    )
    assert absent_link.occurrences[0].disposition_reason == "unresolved-disposition"


@pytest.mark.unit
def test_partition_identities_and_tsv_are_deterministic_and_lossless() -> None:
    pair = Pair(axis="op:PrimarySite", filler_code="C30")
    inputs = (_input(new=(pair,), identifier="b"), _input(identifier="a"))
    first = build_r101_occurrence_ledger(inputs, paths={}, context=_context())
    second = build_r101_occurrence_ledger(inputs, paths={}, context=_context())

    assert first == second
    assert r101_ledger_tsv_bytes(first) == r101_ledger_tsv_bytes(second)
    assert read_r101_ledger_tsv(r101_ledger_tsv_bytes(first)) == first.occurrences
    assert first.counts.model_dump() == {
        "total": 2,
        "projected": 1,
        "unchanged_unprojected": 1,
        "covered_by_retained_r82": 0,
        "unresolved": 0,
        "one_step": 0,
        "closure_only": 0,
        "non_r101_delta": 0,
    }
    reader = csv.DictReader(io.StringIO(r101_ledger_tsv_bytes(first).decode()))
    assert len(list(reader)) == 2


@pytest.mark.unit
def test_mechanical_content_and_publication_statuses_are_independent() -> None:
    report = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
    assert report.mechanical_status == "complete"
    assert report.content_authorization == ContentAuthorization(
        status="pending", authorized_digest=None
    )
    assert report.publication_gate == "blocked"
    with pytest.raises(
        R101ConservationValidationError, match="content-authorization-missing"
    ):
        validate_r101_publication(report)

    mismatch = report.model_copy(
        update={
            "content_authorization": ContentAuthorization(
                status="digest-mismatch", authorized_digest="0" * 64
            )
        }
    )
    assert mismatch.content_authorization.status == "digest-mismatch"
    with pytest.raises(
        R101ConservationValidationError,
        match="content-authorization-digest-mismatch",
    ):
        validate_r101_publication(mismatch)

    for status in ("authorized", "digest-mismatch"):
        with pytest.raises(ValidationError, match="requires a digest"):
            ContentAuthorization(status=status, authorized_digest=None)


@pytest.mark.unit
def test_publication_cli_cannot_turn_a_pending_report_into_authorization(
    tmp_path,
) -> None:
    report = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
    report_path = tmp_path / "report.json.gz"
    write_r101_occurrence_ledger(report_path, report)

    with pytest.raises(
        R101ConservationValidationError, match="content-authorization-missing"
    ):
        adjudication_main(
            [
                "validate-r101-publication",
                "--report",
                str(report_path),
                "--authorization-digest",
                report.json_identity,
            ]
        )


@pytest.mark.unit
def test_duplicate_mismatched_and_partial_source_rows_fail_closed() -> None:
    valid = _input()
    with pytest.raises(R101ConservationValidationError, match="duplicate-occurrence"):
        build_r101_occurrence_ledger((valid, valid), paths={}, context=_context())

    mismatch = valid.model_copy(
        update={
            "new_occurrence": valid.new_occurrence.model_copy(
                update={"source_fact_id": "f" * 64}
            )
        }
    )
    with pytest.raises(
        R101ConservationValidationError, match="structural-key-mismatch"
    ):
        build_r101_occurrence_ledger((mismatch,), paths={}, context=_context())

    with pytest.raises(ValidationError):
        StructuralOccurrence.model_validate(
            {"concept_code": "C1", "occurrence_id": "a" * 64}
        )
    with pytest.raises(ValidationError, match="member position"):
        _occurrence(member_position=1)
    pair = Pair(axis="op:PrimarySite", filler_code="C30")
    with pytest.raises(ValidationError, match="duplicate occurrence link"):
        _input(old=(pair, pair))


@pytest.mark.unit
def test_non_r101_delta_and_exact_count_mismatch_fail_closed() -> None:
    delta = NonR101DeltaRow(
        change="added",
        concept_code="C1",
        axis="op:Morphology",
        filler_code="C20",
    )
    report = build_r101_occurrence_ledger(
        (_input(),),
        paths={},
        context=_context(non_r101_delta_evidence=_delta_evidence(delta)),
    )
    assert report.mechanical_status == "incomplete"
    assert report.occurrences[0].disposition_reason == "non-r101-delta"
    with pytest.raises(R101ConservationValidationError, match="non-r101-delta"):
        validate_r101_publication(report)

    payload = report.model_dump()
    payload["counts"]["total"] = 2
    with pytest.raises(ValidationError, match="count-mismatch"):
        type(report).model_validate(payload)


@pytest.mark.unit
def test_delta_rows_bind_query_runs_count_and_gate_malformed_or_omitted_rows() -> None:
    query_identity = getattr(
        r101_module, "r101_ledger_query_identity", lambda: "0" * 64
    )()
    evidence = {
        "old_run_id": "old",
        "new_run_id": "new",
        "query_identity": query_identity,
        "rows": (
            {
                "change": "added",
                "concept_code": "C2",
                "axis": "op:Morphology",
                "filler_code": "C20",
            },
            {
                "change": "removed",
                "concept_code": "C1",
                "axis": "op:Morphology",
                "filler_code": "C10",
            },
        ),
    }
    context_payload = _context().model_dump()
    context_payload["non_r101_delta_evidence"] = evidence
    context = LedgerBuildContext.model_validate(context_payload)

    report = build_r101_occurrence_ledger((_input(),), paths={}, context=context)

    assert report.counts.non_r101_delta == 2
    assert report.non_r101_delta_evidence.model_dump(mode="json") == {
        **evidence,
        "rows": list(evidence["rows"]),
    }
    assert report.mechanical_status == "incomplete"

    for mutation, message in (
        (
            lambda payload: payload["non_r101_delta_evidence"]["rows"].pop(),
            "count-mismatch",
        ),
        (
            lambda payload: payload["non_r101_delta_evidence"]["rows"][0].update(
                {"concept_code": "malformed"}
            ),
            "concept_code",
        ),
        (
            lambda payload: payload["non_r101_delta_evidence"].update(
                {"query_identity": "0" * 64}
            ),
            "query identity",
        ),
        (
            lambda payload: payload["non_r101_delta_evidence"].update(
                {"old_run_id": "other"}
            ),
            "run",
        ),
    ):
        payload = report.model_dump(mode="json")
        mutation(payload)
        rebound = _rebind_hand_edited_report(payload)
        with pytest.raises(ValidationError, match=message):
            type(report).model_validate_json(rebound)

    rows = tuple(NonR101DeltaRow.model_validate(item) for item in evidence["rows"])
    with pytest.raises(ValidationError, match="canonical and unique"):
        NonR101DeltaEvidence(
            old_run_id="old",
            new_run_id="new",
            query_identity=query_identity,
            rows=tuple(reversed(rows)),
        )
    with pytest.raises(ValidationError, match="bind report runs"):
        _context(non_r101_delta_evidence=_delta_evidence(old_run_id="different-old"))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"path_length": 1}, "path length"),
        (
            {"retained_r82_target": Pair(axis="op:PrimarySite", filler_code="C30")},
            "retained target",
        ),
        ({"r82_evidence_kind": "one-step"}, "R82 evidence"),
    ],
)
def test_occurrence_model_rejects_internally_inconsistent_evidence(
    changes: dict[str, object], message: str
) -> None:
    report = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
    payload = {**report.occurrences[0].model_dump(), **changes}
    with pytest.raises(ValidationError, match=message):
        type(report.occurrences[0]).model_validate(payload)


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["evidence-kind", "uncovered-path"])
def test_report_rejects_path_shapes_the_occurrence_model_alone_can_represent(
    mutation: str,
) -> None:
    if mutation == "evidence-kind":
        report = build_r101_occurrence_ledger(
            (
                _input(
                    old=(Pair(axis="op:PrimarySite", filler_code="C30"),),
                    retained=(Pair(axis="op:PrimarySite", filler_code="C20"),),
                ),
            ),
            paths={
                ("C20", "C30"): _path(
                    _edge("C20", "C25", "8"), _edge("C25", "C30", "9")
                )
            },
            context=_context(),
        )
        payload = report.model_dump(mode="json")
        payload["occurrences"][0]["r82_evidence_kind"] = "one-step"
    else:
        report = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
        payload = report.model_dump(mode="json")
        payload["occurrences"][0]["r82_path"] = [
            _edge("C20", "C30").model_dump(mode="json")
        ]
        payload["occurrences"][0]["path_length"] = 1

    rebound = _rebind_hand_edited_report(payload)
    with pytest.raises(ValidationError, match="broken-r82-path"):
        type(report).model_validate_json(rebound)


@pytest.mark.unit
def test_report_rejects_each_independent_top_level_corruption() -> None:
    report = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
    cases = (
        ("detector_identity", "0" * 64, "detector identity"),
        ("structural_key_fields", ("concept_code",), "structural-key-mismatch"),
        ("mechanical_status", "incomplete", "mechanical status"),
        ("publication_gate", "eligible", "publication gate"),
        ("json_identity", "0" * 64, "JSON ledger identity"),
        ("tsv_identity", "0" * 64, "TSV ledger identity"),
        ("report_identity", "0" * 64, "report identity"),
    )
    for field, value, message in cases:
        payload = report.model_dump()
        payload[field] = value
        with pytest.raises(ValidationError, match=message):
            type(report).model_validate(payload)

    for status, digest, message in (
        ("pending", "0" * 64, "pending authorization"),
        ("authorized", "not-a-digest", "SHA-256"),
    ):
        with pytest.raises(ValidationError, match=message):
            ContentAuthorization.model_validate(
                {"status": status, "authorized_digest": digest}
            )

    with pytest.raises(ValidationError, match="prerequisite proof identities"):
        _context(proof_identity="0" * 64)
    with pytest.raises(
        R101ConservationValidationError, match="source-identity-mismatch"
    ):
        r101_proof_identity()


@pytest.mark.unit
def test_publication_rejects_unresolved_and_ineligible_authorized_shapes() -> None:
    old = Pair(axis="op:PrimarySite", filler_code="C30")
    incomplete = build_r101_occurrence_ledger(
        (_input(old=(old,)),), paths={}, context=_context()
    )
    with pytest.raises(R101ConservationValidationError, match="unresolved-disposition"):
        validate_r101_publication(incomplete)

    complete = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
    ineligible = complete.model_copy(
        update={
            "content_authorization": ContentAuthorization(
                status="authorized", authorized_digest=complete.json_identity
            )
        }
    )
    with pytest.raises(
        R101ConservationValidationError, match="content-authorization-missing"
    ):
        validate_r101_publication(ineligible)

    eligible = complete.model_copy(
        update={
            "content_authorization": ContentAuthorization(
                status="authorized", authorized_digest=complete.json_identity
            ),
            "publication_gate": "eligible",
        }
    )
    validate_r101_publication(eligible)


class _ConsumerStore:
    def __init__(self, source: R101LedgerSource) -> None:
        self.source = source

    async def r101_occurrence_ledger(
        self, old_run_id: str, new_run_id: str
    ) -> R101LedgerSource:
        assert (old_run_id, new_run_id) == ("old", "new")
        return self.source


@pytest.mark.unit
async def test_consumer_dry_run_rejects_every_persisted_inventory_drift() -> None:
    item = _input(new=(Pair(axis="op:PrimarySite", filler_code="C30"),))
    report = build_r101_occurrence_ledger((item,), paths={}, context=_context())
    valid = R101LedgerSource(
        occurrences=(item,), non_r101_delta_evidence=_delta_evidence()
    )

    assert (
        await validate_r101_consumer_dry_run(report, _ConsumerStore(valid))
        == report.json_identity
    )
    for source, message in (
        (
            R101LedgerSource(
                occurrences=(item, item), non_r101_delta_evidence=_delta_evidence()
            ),
            "duplicate-occurrence",
        ),
        (
            R101LedgerSource(occurrences=(), non_r101_delta_evidence=_delta_evidence()),
            "inventory",
        ),
        (
            valid.model_copy(
                update={
                    "non_r101_delta_evidence": _delta_evidence(
                        NonR101DeltaRow(
                            change="added",
                            concept_code="C1",
                            axis="op:Morphology",
                            filler_code="C20",
                        )
                    )
                }
            ),
            "non-R101",
        ),
        (
            R101LedgerSource(
                occurrences=(item.model_copy(update={"new_links": ()}),),
                non_r101_delta_evidence=_delta_evidence(),
            ),
            "link inventory",
        ),
    ):
        with pytest.raises(R101ConservationValidationError, match=message):
            await validate_r101_consumer_dry_run(report, _ConsumerStore(source))


@pytest.mark.unit
def test_atomic_deterministic_gzip_write_preserves_existing_output_and_cleans_staging(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
    output = tmp_path / "report.json.gz"
    second = tmp_path / "second.json.gz"

    write_r101_occurrence_ledger(output, report)
    write_r101_occurrence_ledger(second, report)
    expected_json = (
        json.dumps(
            report.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        ).encode()
        + b"\n"
    )
    assert output.read_bytes() == second.read_bytes()
    assert output.read_bytes()[:10] == b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    assert gzip.decompress(output.read_bytes()) == expected_json

    output.write_bytes(b"old-gzip")
    real_replace = os.replace

    def fail_replace(source, destination) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_r101_occurrence_ledger(output, report)
    assert output.read_bytes() == b"old-gzip"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "report.json.gz",
        "second.json.gz",
    ]

    mismatched = report.model_copy(update={"tsv_identity": "0" * 64})
    with pytest.raises(
        R101ConservationValidationError, match="source-identity-mismatch"
    ):
        write_r101_occurrence_ledger(output, mismatched)
    monkeypatch.setattr(os, "replace", real_replace)


@pytest.mark.unit
def test_report_loader_is_strict_gzip_schema3_and_rejects_ambiguous_input(
    tmp_path,
) -> None:
    report = build_r101_occurrence_ledger((_input(),), paths={}, context=_context())
    valid = tmp_path / "valid.json.gz"
    write_r101_occurrence_ledger(valid, report)
    assert load_r101_conservation_report(valid) == report

    raw = tmp_path / "raw.json"
    raw.write_bytes(gzip.decompress(valid.read_bytes()))
    with pytest.raises(R101ConservationValidationError, match="\\.json\\.gz"):
        load_r101_conservation_report(raw)

    malformed = tmp_path / "malformed.json.gz"
    malformed.write_bytes(b"not gzip")
    with pytest.raises(R101ConservationValidationError, match="gzip"):
        load_r101_conservation_report(malformed)

    trailing = tmp_path / "trailing.json.gz"
    trailing.write_bytes(valid.read_bytes() + b"trailing")
    with pytest.raises(R101ConservationValidationError, match=r"trailing|member"):
        load_r101_conservation_report(trailing)

    multiple = tmp_path / "multiple.json.gz"
    multiple.write_bytes(valid.read_bytes() + valid.read_bytes())
    with pytest.raises(R101ConservationValidationError, match=r"trailing|member"):
        load_r101_conservation_report(multiple)

    duplicate = tmp_path / "duplicate.json.gz"
    duplicate.write_bytes(
        gzip.compress(b'{"schema_version":3,"schema_version":3}', mtime=0)
    )
    with pytest.raises(R101ConservationValidationError, match="duplicate JSON key"):
        load_r101_conservation_report(duplicate)

    stale = tmp_path / "stale.json.gz"
    stale.write_bytes(gzip.compress(b'{"schema_version":2}', mtime=0))
    with pytest.raises(ValidationError):
        load_r101_conservation_report(stale)

    payload = report.model_dump(mode="json")
    payload["json_identity"] = "0" * 64
    mismatch = tmp_path / "mismatch.json.gz"
    mismatch.write_bytes(gzip.compress(json.dumps(payload).encode(), mtime=0))
    with pytest.raises(ValidationError, match="source-identity-mismatch"):
        load_r101_conservation_report(mismatch)
    with pytest.raises(R101ConservationValidationError, match="TSV columns"):
        read_r101_ledger_tsv(b"wrong\nvalue\n")
    assert r101_detector_identity()
    assert r101_proof_identity("1" * 64, "2" * 64, "3" * 64)


@pytest.mark.unit
def test_detector_identity_covers_semantic_model_validator_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = r101_detector_identity()
    real_getsource = r101_module.inspect.getsource

    def changed_getsource(value: Any) -> str:
        source = real_getsource(value)
        if value is ContentAuthorization:
            return source + "\n# changed authorization semantics"
        return source

    monkeypatch.setattr(r101_module.inspect, "getsource", changed_getsource)
    assert r101_detector_identity() != before


@pytest.mark.unit
def test_detector_identity_covers_stated_path_resolver_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = r101_detector_identity()
    real_getsource = r101_module.inspect.getsource

    def changed_getsource(value: Any) -> str:
        source = real_getsource(value)
        if value is stated_queries_module.resolve_part_of_paths:
            return source + "\n# changed path resolution semantics"
        return source

    monkeypatch.setattr(r101_module.inspect, "getsource", changed_getsource)
    assert r101_detector_identity() != before


@pytest.mark.unit
def test_report_binds_each_continuation_identity_and_their_proof_identity() -> None:
    prerequisite_identities = ("8" * 64, "9" * 64, "a" * 64)
    report = build_r101_occurrence_ledger(
        (_input(),),
        paths={},
        context=_context(proof_identity=r101_proof_identity(*prerequisite_identities)),
    )

    assert (
        report.pre_resume_proof_identity,
        report.resume_dry_run_identity,
        report.mixed_cohort_identity,
    ) == prerequisite_identities
    assert report.proof_identity == r101_proof_identity(*prerequisite_identities)

    payload = report.model_dump()
    payload["proof_identity"] = "f" * 64
    with pytest.raises(ValidationError, match="prerequisite proof identities"):
        type(report).model_validate(payload)


@pytest.mark.unit
def test_build_refuses_a_stale_detector_identity() -> None:
    with pytest.raises(ValidationError, match="detector identity"):
        build_r101_occurrence_ledger(
            (_input(),),
            paths={},
            context=_context(detector_identity="0" * 64),
        )


@pytest.mark.unit
def test_observed_query_ceilings_fail_closed_at_max_plus_one() -> None:
    with pytest.raises(ValidationError):
        QueryMetrics(
            postgres_query_count=11,
            qlever_query_count=208,
            max_pair_batch_size=8,
            max_r82_hops=8,
            max_asserted_superclass_hops=20,
        )


@pytest.mark.unit
def test_generated_ledger_inventory_sentinels_and_exact_tsv_are_bound() -> None:
    golden = Path(__file__).parent / "golden"
    report = load_r101_conservation_report(
        golden / "neoplasm-r101-v4-conservation.json.gz"
    )
    tsv = r101_ledger_tsv_bytes(report)

    assert report.counts.model_dump() == {
        "total": 43414,
        "projected": 30040,
        "unchanged_unprojected": 10083,
        "covered_by_retained_r82": 3291,
        "unresolved": 0,
        "one_step": 1954,
        "closure_only": 1337,
        "non_r101_delta": 0,
    }
    assert report.query_metrics == QueryMetrics(
        postgres_query_count=3,
        qlever_query_count=177,
        max_pair_batch_size=8,
        max_r82_hops=8,
        max_asserted_superclass_hops=20,
    )
    assert (
        report.json_identity
        == "73ab4652f9489550ab0a5c1f8bb819567268a449e673b49dbaf596dc72da0776"
    )
    assert (
        report.report_identity
        == "40a5260c00c4775f40318eec3c8f313b8abf97909ba057c6f476737f96e56911"
    )
    assert (
        report.tsv_identity
        == "b4182dcc676d8e6ad57234757b774fcee37f857f4d9e593bb6e83200a0b6a73d"
    )
    assert report.mechanical_status == "complete"
    assert report.content_authorization.status == "pending"
    assert report.publication_gate == "blocked"
    assert hashlib.sha256(tsv).hexdigest() == report.tsv_identity
    assert (
        report.pre_resume_proof_identity,
        report.resume_dry_run_identity,
        report.mixed_cohort_identity,
        report.proof_identity,
    ) == (
        "f3c321c38deb8478f7a1abfa5c1edb1ef9ac3daf793d0dfe8d1e758eb62d2018",
        "2f5a0530f72028353a32b050a7e7a06a1880d7bcfe1aad4bcacd902333e7bd98",
        "dda9c71a8a777e451a08fe81e4e2bae799f85e5f2c4984a90e5d95d71784777a",
        "6c7adb9df5472e035d940fb9e4d0d445311d18d2c5a64d5eb087cf61bdd0b3b5",
    )
    assert read_r101_ledger_tsv(tsv) == report.occurrences

    by_concept = {
        code: tuple(item for item in report.occurrences if item.concept_code == code)
        for code in ("C6135", "C101539", "C4791", "C5356", "C5552")
    }
    assert [len(by_concept[code]) for code in by_concept] == [7, 5, 8, 16, 16]
    assert {
        (pair.axis, pair.filler_code)
        for code in ("C6135", "C101539")
        for item in by_concept[code]
        for pair in item.new_links
        if pair.axis == "op:AssociatedRegion"
    } == {("op:AssociatedRegion", "C13063")}
    assert any(
        item.disposition == "covered-by-retained-r82"
        and item.old_links == (Pair(axis="op:PrimarySite", filler_code="C12727"),)
        and item.retained_r82_target
        == Pair(axis="op:PrimarySite", filler_code="C12869")
        for item in by_concept["C4791"]
    )
    assert any(
        item.disposition == "covered-by-retained-r82" for item in by_concept["C5356"]
    )
    assert any(
        item.disposition == "covered-by-retained-r82" for item in by_concept["C5552"]
    )
    with pytest.raises(ValidationError):
        QueryMetrics(
            postgres_query_count=10,
            qlever_query_count=209,
            max_pair_batch_size=8,
            max_r82_hops=8,
            max_asserted_superclass_hops=20,
        )
