from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ontolib.decomposition.branches import DecompositionBranch
from ontolib.decomposition.pre_resume import EXPECTED_PENDING_DIGEST
from ontolib.decomposition.provenance_models import NcitSourceSnapshot, RunFingerprint
from ontolib.decomposition.resume_dry_run import (
    EXPECTED_PRE_RESUME_PROOF_IDENTITY,
    ResumeSelection,
    ResumeWorkItem,
    build_resume_dry_run,
    canonical_resume_dry_run_json,
    load_pre_resume_proof,
    resume_dry_run_identity,
    validate_resume_selection,
)
from ontolib.decomposition.run import RunConfig, build_resume_identity

SOURCE_IDENTITY = "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"


def _fingerprint() -> RunFingerprint:
    config = RunConfig(
        branch=DecompositionBranch.NEOPLASM,
        out=Path("tmp/neoplasm-r101-v4-full.ttl"),
        walker_max_depth=7,
    )
    return RunFingerprint(
        source_identity=SOURCE_IDENTITY,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        semantic_types=config.semantic_types,
        worklist=("C1", "C2", "C3"),
        algorithm_version=config.algorithm_version,
        config_version="nested-definition-v2",
        walker_max_depth=7,
        output_mode="file",
        load_mode="none",
        emitted_at=datetime(2026, 8, 16, tzinfo=UTC),
    )


def _complete(code: str, ordinal: int) -> ResumeWorkItem:
    return ResumeWorkItem(
        concept_code=code,
        ordinal=ordinal,
        state="complete",
        attempt_count=1,
        semantic_types=("Neoplastic Process",),
        outcome="atomic-no-op",
        is_decomposed=False,
        is_residual=False,
        has_complete_definition=False,
        constituent_count=0,
        minted_count=0,
        completed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )


def _pending(code: str, ordinal: int) -> ResumeWorkItem:
    return ResumeWorkItem(
        concept_code=code,
        ordinal=ordinal,
        state="pending",
        attempt_count=0,
        has_complete_definition=False,
    )


def _proof() -> dict[str, Any]:
    return {
        "proof_identity": EXPECTED_PRE_RESUME_PROOF_IDENTITY,
        "run_id": "run-1",
        "source_identity": SOURCE_IDENTITY,
        "fingerprint_identity": _fingerprint().identity,
        "semantic_identity": "1" * 64,
        "pending_cohort_digest": EXPECTED_PENDING_DIGEST,
        "pending_count": 9733,
        "completed_cohort_digest": "2" * 64,
        "completed_count": 5900,
    }


@pytest.mark.unit
def test_resume_dry_run_reuses_runner_identity_and_selects_only_clean_pending() -> None:
    fingerprint = _fingerprint()
    config = RunConfig(
        branch=DecompositionBranch.NEOPLASM,
        out=Path("tmp/neoplasm-r101-v4-full.ttl"),
        resume_from="run-1",
        walker_max_depth=7,
    )
    expected = build_resume_identity(
        config,
        snapshot=NcitSourceSnapshot(
            source_identity=SOURCE_IDENTITY, ontology_version="26.07d"
        ),
        semantic_types=config.semantic_types,
        total_limit=None,
    )
    selection = validate_resume_selection(
        fingerprint=fingerprint,
        expected_identity=expected,
        work_items=(_complete("C1", 0), _pending("C2", 1), _pending("C3", 2)),
        integrity_counts={
            "completion_metadata_mismatch_count": 0,
            "constituent_count_mismatch_count": 0,
            "minted_count_mismatch_count": 0,
            "child_orphan_count": 0,
        },
        postgres_reads=3,
    )

    assert selection.pending_codes == ("C2", "C3")
    assert selection.selected_complete_count == 0
    assert selection.completed_codes == ("C1",)
    assert selection.postgres_reads == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda rows: setattr(rows[1], "claim_token", "token"), "claim token"),
        (lambda rows: setattr(rows[1], "attempt_count", 1), "attempt"),
        (lambda rows: setattr(rows[0], "completed_at", None), "completion metadata"),
        (lambda rows: setattr(rows[2], "ordinal", 1), "ordinal"),
        (lambda rows: setattr(rows[2], "concept_code", "C9"), "worklist"),
        (lambda rows: setattr(rows[1], "state", "complete"), "completion metadata"),
    ],
)
def test_resume_selection_rejects_torn_or_drifted_rows(mutation, message: str) -> None:
    rows = [
        copy.copy(_complete("C1", 0)),
        copy.copy(_pending("C2", 1)),
        copy.copy(_pending("C3", 2)),
    ]
    mutation(rows)

    with pytest.raises(ValueError, match=message):
        validate_resume_selection(
            fingerprint=_fingerprint(),
            expected_identity=build_resume_identity(
                RunConfig(
                    branch=DecompositionBranch.NEOPLASM,
                    out=Path("tmp/neoplasm-r101-v4-full.ttl"),
                    walker_max_depth=7,
                ),
                snapshot=NcitSourceSnapshot(
                    source_identity=SOURCE_IDENTITY, ontology_version="26.07d"
                ),
                semantic_types=_fingerprint().semantic_types,
                total_limit=None,
            ),
            work_items=tuple(rows),
            integrity_counts={
                "completion_metadata_mismatch_count": 0,
                "constituent_count_mismatch_count": 0,
                "minted_count_mismatch_count": 0,
                "child_orphan_count": 0,
            },
            postgres_reads=3,
        )


@pytest.mark.unit
def test_resume_selection_rejects_integrity_mismatch_and_selected_complete() -> None:
    selection = ResumeSelection(
        fingerprint=_fingerprint(),
        pending_codes=("C2", "C3"),
        completed_codes=("C1",),
        selected_complete_count=1,
        postgres_reads=3,
    )
    with pytest.raises(ValueError, match="selected completed"):
        build_resume_dry_run(
            run_id="run-1",
            proof=_proof(),
            semantic_identity="3" * 64,
            output_path=Path("tmp/neoplasm-r101-v4-full.ttl"),
            selection=selection,
            status="failed",
            error_type="BrokenPipeError",
            error_message="[Errno 32] Broken pipe",
            qlever_reads=9,
        )

    counts = {
        "completion_metadata_mismatch_count": 0,
        "constituent_count_mismatch_count": 1,
        "minted_count_mismatch_count": 0,
        "child_orphan_count": 0,
    }
    with pytest.raises(ValueError, match="integrity"):
        validate_resume_selection(
            fingerprint=_fingerprint(),
            expected_identity=build_resume_identity(
                RunConfig(
                    branch=DecompositionBranch.NEOPLASM,
                    out=Path("tmp/neoplasm-r101-v4-full.ttl"),
                    walker_max_depth=7,
                ),
                snapshot=NcitSourceSnapshot(
                    source_identity=SOURCE_IDENTITY, ontology_version="26.07d"
                ),
                semantic_types=_fingerprint().semantic_types,
                total_limit=None,
            ),
            work_items=(_complete("C1", 0), _pending("C2", 1), _pending("C3", 2)),
            integrity_counts=counts,
            postgres_reads=3,
        )


@pytest.mark.unit
def test_dry_run_binds_proof_semantics_and_configuration_but_not_freshness() -> None:
    selection = ResumeSelection(
        fingerprint=_fingerprint(),
        pending_codes=tuple(f"C{i}" for i in range(9733)),
        completed_codes=tuple(f"X{i}" for i in range(5900)),
        selected_complete_count=0,
        postgres_reads=3,
    )
    proof = _proof()
    proof["pending_cohort_digest"] = (
        __import__("hashlib")
        .sha256("\n".join(selection.pending_codes).encode())
        .hexdigest()
    )
    proof["completed_cohort_digest"] = (
        __import__("hashlib")
        .sha256("\n".join(selection.completed_codes).encode())
        .hexdigest()
    )

    first = build_resume_dry_run(
        run_id="run-1",
        proof=proof,
        semantic_identity="3" * 64,
        output_path=Path("tmp/neoplasm-r101-v4-full.ttl"),
        selection=selection,
        status="failed",
        error_type="BrokenPipeError",
        error_message="[Errno 32] Broken pipe",
        qlever_reads=9,
    )
    second = {**first, "observed_at": "later", "postgres_reads": 99, "qlever_reads": 88}

    assert first["output_path"] == "tmp/neoplasm-r101-v4-full.ttl"
    assert first["walker_max_depth"] == 7
    assert first["branch"] == "neoplasm"
    assert first["pending_count"] == 9733
    assert first["completed_exclusion_count"] == 5900
    assert canonical_resume_dry_run_json(first) == canonical_resume_dry_run_json(second)
    assert resume_dry_run_identity(first) == resume_dry_run_identity(second)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("proof_identity", "0" * 64, "wrong pre-resume proof"),
        ("source_identity", "0" * 64, "proof run/source/fingerprint"),
        ("fingerprint_identity", "0" * 64, "proof run/source/fingerprint"),
        ("pending_count", 9732, "cohort count or digest"),
    ],
)
def test_dry_run_rejects_wrong_proof_or_bound_identity(
    field: str, value: object, message: str
) -> None:
    proof = _proof()
    proof[field] = value
    selection = ResumeSelection(
        fingerprint=_fingerprint(),
        pending_codes=("C2", "C3"),
        completed_codes=("C1",),
        selected_complete_count=0,
        postgres_reads=3,
    )
    with pytest.raises(ValueError, match=message):
        build_resume_dry_run(
            run_id="run-1",
            proof=proof,
            semantic_identity="3" * 64,
            output_path=Path("tmp/neoplasm-r101-v4-full.ttl"),
            selection=selection,
            status="failed",
            error_type="BrokenPipeError",
            error_message="[Errno 32] Broken pipe",
            qlever_reads=9,
        )


def _selection_for_build() -> ResumeSelection:
    return ResumeSelection(
        fingerprint=_fingerprint(),
        pending_codes=("C2", "C3"),
        completed_codes=("C1",),
        selected_complete_count=0,
        postgres_reads=3,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"output_path": Path("tmp/wrong.ttl")}, "output path"),
        ({"status": "running"}, "failure metadata"),
        ({"error_type": "Other"}, "failure metadata"),
        ({"error_message": "changed"}, "failure metadata"),
        ({"qlever_reads": 0}, "execute QLever reads"),
    ],
)
def test_dry_run_rejects_wrong_output_failure_snapshot_or_stale_source(
    overrides: dict[str, object], message: str
) -> None:
    arguments: dict[str, object] = {
        "run_id": "run-1",
        "proof": _proof(),
        "semantic_identity": "3" * 64,
        "output_path": Path("tmp/neoplasm-r101-v4-full.ttl"),
        "selection": _selection_for_build(),
        "status": "failed",
        "error_type": "BrokenPipeError",
        "error_message": "[Errno 32] Broken pipe",
        "qlever_reads": 9,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        build_resume_dry_run(**arguments)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("state", ["running", "failed"])
def test_resume_selection_rejects_inflight_or_failed_work(state: str) -> None:
    row = _pending("C2", 1)
    row.state = state  # type: ignore[assignment]

    with pytest.raises(ValueError, match="running or failed"):
        validate_resume_selection(
            fingerprint=_fingerprint(),
            expected_identity=build_resume_identity(
                RunConfig(
                    branch=DecompositionBranch.NEOPLASM,
                    out=Path("tmp/neoplasm-r101-v4-full.ttl"),
                    walker_max_depth=7,
                ),
                snapshot=NcitSourceSnapshot(
                    source_identity=SOURCE_IDENTITY, ontology_version="26.07d"
                ),
                semantic_types=_fingerprint().semantic_types,
                total_limit=None,
            ),
            work_items=(_complete("C1", 0), row, _pending("C3", 2)),
            integrity_counts={},
            postgres_reads=3,
        )


@pytest.mark.unit
def test_resume_selection_requires_a_fresh_postgres_observation() -> None:
    with pytest.raises(ValueError, match="execute PostgreSQL reads"):
        validate_resume_selection(
            fingerprint=_fingerprint(),
            expected_identity=build_resume_identity(
                RunConfig(
                    branch=DecompositionBranch.NEOPLASM,
                    out=Path("tmp/neoplasm-r101-v4-full.ttl"),
                    walker_max_depth=7,
                ),
                snapshot=NcitSourceSnapshot(
                    source_identity=SOURCE_IDENTITY, ontology_version="26.07d"
                ),
                semantic_types=_fingerprint().semantic_types,
                total_limit=None,
            ),
            work_items=(),
            integrity_counts={},
            postgres_reads=0,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"proof_identity":"a","proof_identity":"b"}', "duplicate"),
        ("[]", "must be a JSON object"),
        ('{"proof_identity":"0"}', "wrong pre-resume proof"),
    ],
)
def test_proof_loader_rejects_duplicate_nonobject_and_unbound_payloads(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "proof.json"
    path.write_text(content)

    with pytest.raises(ValueError, match=message):
        load_pre_resume_proof(path)
