from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts.adjudication import _parser

from ontolib.decomposition.pre_resume import (
    PRE_RESUME_SQL,
    QUERY_VALIDATOR_SYMBOL_NAMES,
    CandidateOccurrence,
    CandidateTuple,
    affected_missing_p106,
    candidate_tuple_identity,
    cohort_identity,
    derive_candidate_population,
    ordered_code_identity,
    query_validator_identity,
    semantic_dependency_identity,
    site_table_identity,
    statement_is_read_only,
)


@pytest.mark.unit
def test_pre_resume_digest_encodings_are_exact() -> None:
    assert ordered_code_identity(("C2", "C1")) == hashlib.sha256(b"C2\nC1").hexdigest()
    candidates = (
        CandidateTuple("C2", "C4", "C6", "C8"),
        CandidateTuple("C1", "C3", "C5", "C7"),
        CandidateTuple("C2", "C4", "C6", "C8"),
    )
    expected = hashlib.sha256(b"C1\tC3\tC5\tC7\nC2\tC4\tC6\tC8").hexdigest()
    assert candidate_tuple_identity(candidates) == expected


@pytest.mark.unit
def test_cohort_identity_binds_labels_counts_and_digests() -> None:
    payload = {
        "completed": {
            "label": "pre-fix-completed",
            "count": 2,
            "digest": "a" * 64,
        },
        "pending": {
            "label": "post-fix-pending",
            "count": 1,
            "digest": "b" * 64,
        },
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert cohort_identity(2, "a" * 64, 1, "b" * 64) == expected


@pytest.mark.unit
def test_every_pre_resume_postgres_statement_is_select_or_cte_read_only() -> None:
    assert PRE_RESUME_SQL
    assert all(
        statement_is_read_only(statement) for statement in PRE_RESUME_SQL.values()
    )
    for statement in (
        "UPDATE decomp_run SET status = 'running'",
        "WITH changed AS (DELETE FROM decomp_run RETURNING id) SELECT * FROM changed",
        "SELECT 1; INSERT INTO decomp_run(id) VALUES ('x')",
    ):
        assert statement_is_read_only(statement) is False


@pytest.mark.unit
def test_integrity_query_covers_every_completion_child_table() -> None:
    integrity_sql = PRE_RESUME_SQL["integrity"]

    assert {
        "decomp_constituent",
        "decomp_minted_proposal",
        "decomp_definition_fact",
        "decomp_definition_group",
        "decomp_definition_group_edge",
        "decomp_source_occurrence",
        "decomp_constituent_occurrence",
    } <= set(integrity_sql.split())


@pytest.mark.unit
def test_missing_p106_predicate_is_live_and_non_authorizable() -> None:
    candidates = (
        CandidateTuple("C1", "C10", "C20", "C30"),
        CandidateTuple("C2", "C11", "C21", "C31"),
    )

    affected = affected_missing_p106(candidates, {"C10": "Body System", "C11": None})

    assert affected == (CandidateTuple("C2", "C11", "C21", "C31"),)

    with pytest.raises(ValueError, match="cover every candidate filler"):
        affected_missing_p106(candidates, {"C10": "Body System"})


@pytest.mark.unit
def test_candidate_population_matches_production_route_order_and_liveness() -> None:
    rows = (
        CandidateOccurrence("C1", "o1", "A1", "O1", "M1"),
        CandidateOccurrence("C1", "o2", "A1", "F1", "M1"),
        CandidateOccurrence("C1", "o3", "A1", "F1", "M1"),
        CandidateOccurrence("C1", "o4", "LINEAGE", "L1", "M1"),
        CandidateOccurrence("C1", "o5", "A1", "S1", "M1"),
        CandidateOccurrence("C2", "o6", "A2", "O2", "M2"),
        CandidateOccurrence("C2", "o7", "A2", "F2", "M2"),
    )

    production = derive_candidate_population(
        rows,
        morphology_to_organ={"M1": "O1", "M2": "O2"},
        morphology_to_subsites={"M1": frozenset({"S1"})},
        lineage_genera=frozenset({"LINEAGE"}),
        apply_route_filters=True,
    )
    sensitivity = derive_candidate_population(
        rows,
        morphology_to_organ={"M1": "O1", "M2": "O2"},
        morphology_to_subsites={"M1": frozenset({"S1"})},
        lineage_genera=frozenset({"LINEAGE"}),
        apply_route_filters=False,
    )

    assert production.counts == (2, 2, 3, 2)
    assert production.tuples == (
        CandidateTuple("C1", "F1", "M1", "O1"),
        CandidateTuple("C2", "F2", "M2", "O2"),
    )
    assert sensitivity.counts == (2, 3, 4, 3)
    assert CandidateTuple("C1", "L1", "M1", "O1") in sensitivity.tuples


@pytest.mark.unit
def test_candidate_population_requires_a_known_organ_and_multi_filler_context() -> None:
    rows = (
        CandidateOccurrence("C1", "o1", "A1", "O1", "M1"),
        CandidateOccurrence("C1", "o2", "A1", "F1", "M1"),
    )

    missing_mapping = derive_candidate_population(
        rows,
        morphology_to_organ={},
        morphology_to_subsites={},
        lineage_genera=frozenset(),
        apply_route_filters=True,
    )
    single_filler = derive_candidate_population(
        rows[:1],
        morphology_to_organ={"M1": "O1"},
        morphology_to_subsites={},
        lineage_genera=frozenset(),
        apply_route_filters=True,
    )

    assert missing_mapping.counts == (0, 0, 0, 0)
    assert single_filler.counts == (0, 0, 0, 0)


@pytest.mark.unit
def test_missing_p106_verdict_counts_source_occurrences_not_only_tuples() -> None:
    population = derive_candidate_population(
        (
            CandidateOccurrence("C1", "o1", "A", "O", "M"),
            CandidateOccurrence("C1", "o2", "A", "F", "M"),
            CandidateOccurrence("C1", "o3", "A", "F", "M"),
        ),
        morphology_to_organ={"M": "O"},
        morphology_to_subsites={},
        lineage_genera=frozenset(),
        apply_route_filters=True,
    )

    verdict = population.missing_p106_verdict({"F": None})

    assert verdict.affected_counts == (1, 1, 2, 1)
    assert verdict.authorizable is False


@pytest.mark.unit
def test_semantic_allowlist_and_site_tables_have_canonical_identities(
    tmp_path: Path,
) -> None:
    root = tmp_path
    first = root / "a.py"
    second = root / "b.py"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    identity, dependencies = semantic_dependency_identity(
        root, (Path("b.py"), Path("a.py"))
    )

    expected_dependencies = {
        "a.py": hashlib.sha256(b"first").hexdigest(),
        "b.py": hashlib.sha256(b"second").hexdigest(),
    }
    assert dependencies == expected_dependencies
    assert (
        identity
        == hashlib.sha256(
            json.dumps(
                expected_dependencies, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert site_table_identity(
        {"C2": "C4", "C1": "C3"},
        {"C2": frozenset({"C6", "C5"})},
    ) == site_table_identity(
        {"C1": "C3", "C2": "C4"},
        {"C2": frozenset({"C5", "C6"})},
    )


@pytest.mark.unit
def test_query_identity_allowlist_covers_acquisition_and_authorization_logic() -> None:
    assert {
        "acquire_candidate_evidence",
        "affected_missing_p106",
        "build_semantic_type_of_query",
        "derive_candidate_population",
        "missing_p106_verdict",
        "parse_semantic_type_rows",
        "proof_invariants",
        "validation_authorization",
    } <= QUERY_VALIDATOR_SYMBOL_NAMES


@pytest.mark.unit
def test_query_identity_rejects_an_unbound_validator_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ontolib.decomposition.pre_resume.QUERY_VALIDATOR_SYMBOL_NAMES",
        QUERY_VALIDATOR_SYMBOL_NAMES | {"invented_validator"},
    )

    with pytest.raises(RuntimeError, match="allowlist drift"):
        query_validator_identity()


@pytest.mark.unit
def test_pre_resume_cli_requires_explicit_read_sources_and_output() -> None:
    args = _parser().parse_args(
        [
            "generate-pre-resume-proof",
            "--source-manifest",
            "candidate.json",
            "--run-id",
            "run-1",
            "--endpoint",
            "http://localhost:7888",
            "--output",
            "tmp/proof.json",
        ]
    )

    assert args.command == "generate-pre-resume-proof"
    assert args.source_manifest == Path("candidate.json")
    assert args.run_id == "run-1"
    assert args.endpoint == "http://localhost:7888"
    assert args.output == Path("tmp/proof.json")


@pytest.mark.unit
def test_resume_dry_run_cli_requires_every_bound_input_and_output() -> None:
    args = _parser().parse_args(
        [
            "dry-run-resume",
            "--source-manifest",
            "candidate.json",
            "--proof",
            "tmp/pre-resume-proof-1.json",
            "--run-id",
            "run-1",
            "--endpoint",
            "http://localhost:7888",
            "--branch",
            "neoplasm",
            "--walker-max-depth",
            "7",
            "--out",
            "tmp/neoplasm-r101-v4-full.ttl",
            "--output",
            "tmp/pre-resume-dry-run-1.json",
        ]
    )

    assert args.command == "dry-run-resume"
    assert args.proof == Path("tmp/pre-resume-proof-1.json")
    assert args.branch == "neoplasm"
    assert args.walker_max_depth == 7
    assert args.out == Path("tmp/neoplasm-r101-v4-full.ttl")
