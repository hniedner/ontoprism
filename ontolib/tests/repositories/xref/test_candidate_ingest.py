"""Tests for the Uberon/CL candidate ingest pipeline (PR-A3, issue #72).

Written BEFORE production code per STRICT TDD -- run to see them fail,
then implement ``candidate_ingest`` until they pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from ontolib.repositories.xref.candidate_ingest import (
    _build_xref_index,
    _iri_to_curie,
    build_filler_codes_query,
    build_uberon_xref_query,
    candidate_coverage_report,
    generate_candidates,
)
from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.vocab import CLOSE_MATCH

# -- Mock SPARQL client -------------------------------------------------


class _MockClient:
    """Mock ``OxigraphHttpClient`` returning canned SPARQL results.

    ``select`` matches each query against *responses* keys by containment;
    the first matching key wins.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.select_calls: list[str] = []

    async def select(self, query: str) -> list[dict[str, str | None]]:
        self.select_calls.append(query)
        for key, rows in self._responses.items():
            if key in query:
                return rows
        return []

    async def __aenter__(self) -> _MockClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


# -- Shared test data ---------------------------------------------------

_NCIT_VERSION = "26.02d"
_UBERON_VERSION = "uberon-2026-01"

# Small hand-built fixture of known xref pairs.
_XREF_FIXTURE: list[tuple[str, str, str, str]] = [
    (
        "C3262",
        "http://purl.obolibrary.org/obo/UBERON_0002107",
        "NCI:C3262",
        "UBERON:0002107",
    ),
    ("C12345", "http://purl.obolibrary.org/obo/CL_0000057", "NCI:C12345", "CL:0000057"),
]

# Fillers with no xref (matched via lexical).
_LEXICAL_FIXTURE: list[tuple[str, str, str, str]] = [
    ("C54321", "Liver", "http://purl.obolibrary.org/obo/UBERON_0000948", "liver"),
]

_ALL_FILLERS = {row[0] for row in _XREF_FIXTURE} | {row[0] for row in _LEXICAL_FIXTURE}


# -- Tests: Query structure ---------------------------------------------


@pytest.mark.unit
def test_filler_query_has_expected_shape() -> None:
    """The filler SPARQL query contains the target roles and a DISTINCT."""
    query = build_filler_codes_query()
    assert "R101" in query
    assert "R100" in query
    assert "R102" in query
    assert "R105" in query
    assert "DISTINCT" in query
    assert "owl:someValuesFrom" in query
    assert "owl:Restriction" in query


@pytest.mark.unit
def test_uberon_xref_query_structure() -> None:
    """The Uberon xref SPARQL query has the expected shape."""
    query = build_uberon_xref_query()
    assert "hasDbXref" in query
    assert "NCI:" in query
    assert "oboInOwl" in query


# -- Tests: IRI conversion ----------------------------------------------


@pytest.mark.unit
def test_iri_to_curie() -> None:
    """OBO IRI is correctly converted to CURIE."""
    result = _iri_to_curie("http://purl.obolibrary.org/obo/UBERON_0002107")
    assert result == "UBERON:0002107"

    result = _iri_to_curie("http://purl.obolibrary.org/obo/CL_0000057")
    assert result == "CL:0000057"

    assert _iri_to_curie("http://example.com/foo") is None
    assert _iri_to_curie("http://purl.obolibrary.org/obo/") is None


# -- Tests: xref index --------------------------------------------------


@pytest.mark.unit
def test_build_xref_index_skips_non_nci() -> None:
    """Xref entries not starting with NCI: are skipped."""
    xrefs = [
        {
            "upstream": "http://purl.obolibrary.org/obo/UBERON_0002107",
            "xref": "NCI:C3262",
        },
        {"upstream": "http://purl.obolibrary.org/obo/CL_0000057", "xref": "SNOMED:123"},
    ]
    index = _build_xref_index(xrefs)
    assert "C3262" in index
    assert "SNOMED:123" not in index


# -- Tests: candidate generation ----------------------------------------


@pytest.mark.unit
async def test_xref_candidates_are_closematch_only() -> None:
    """No generated candidate has predicate_id != CLOSE_MATCH."""
    ncit_responses = {
        "SELECT DISTINCT ?fillerCode": [{"fillerCode": t[0]} for t in _XREF_FIXTURE],
        "SELECT ?code ?label WHERE": [],
    }
    uberon_responses = {
        "hasDbXref": [{"upstream": t[1], "xref": t[2]} for t in _XREF_FIXTURE],
    }
    ncit = _MockClient(ncit_responses)
    uberon = _MockClient(uberon_responses)

    records, *_ = await generate_candidates(
        ncit, uberon, _NCIT_VERSION, _UBERON_VERSION
    )
    assert all(r.predicate_id == CLOSE_MATCH for r in records)


@pytest.mark.unit
async def test_every_record_has_versions_and_justification() -> None:
    """Every output record has both source versions and a justification."""
    ncit_responses = {
        "SELECT DISTINCT ?fillerCode": [{"fillerCode": t[0]} for t in _XREF_FIXTURE],
        "SELECT ?code ?label WHERE": [],
    }
    uberon_responses = {
        "hasDbXref": [{"upstream": t[1], "xref": t[2]} for t in _XREF_FIXTURE],
    }
    ncit = _MockClient(ncit_responses)
    uberon = _MockClient(uberon_responses)

    records, *_ = await generate_candidates(
        ncit, uberon, _NCIT_VERSION, _UBERON_VERSION
    )
    for r in records:
        assert r.subject_source_version == _NCIT_VERSION
        assert r.object_source_version == _UBERON_VERSION
        assert r.author == "xref-ingest-A3"


@pytest.mark.unit
async def test_xref_sourced_candidates_are_high_precision() -> None:
    """Every xref-sourced candidate reproduces the correct upstream code."""
    ncit_responses = {
        "SELECT DISTINCT ?fillerCode": [{"fillerCode": t[0]} for t in _XREF_FIXTURE],
        "SELECT ?code ?label WHERE": [],
    }
    uberon_responses = {
        "hasDbXref": [{"upstream": t[1], "xref": t[2]} for t in _XREF_FIXTURE],
    }
    ncit = _MockClient(ncit_responses)
    uberon = _MockClient(uberon_responses)

    records, filler_to_source = await generate_candidates(
        ncit, uberon, _NCIT_VERSION, _UBERON_VERSION
    )

    # Build lookup: subject_id -> set of object_ids
    subjects: dict[str, set[str]] = {}
    for r in records:
        subjects.setdefault(r.subject_id, set()).add(r.object_id)

    for ncit_code, _, _, expected_obj in _XREF_FIXTURE:
        assert ncit_code in subjects
        assert expected_obj in subjects[ncit_code]
        assert filler_to_source[ncit_code] == "xref"


@pytest.mark.unit
async def test_lexical_candidates_have_correct_justification() -> None:
    """Lexical-match candidates use LexicalMatching and 0.5 confidence."""
    ncit_responses = {
        "SELECT DISTINCT ?fillerCode": [{"fillerCode": t[0]} for t in _LEXICAL_FIXTURE],
        "SELECT ?code ?label WHERE": [
            {"code": t[0], "label": t[1]} for t in _LEXICAL_FIXTURE
        ],
    }
    uberon_responses = {
        "hasDbXref": [],
        "SELECT ?concept ?label WHERE": [
            {"concept": t[2], "label": t[3]} for t in _LEXICAL_FIXTURE
        ],
    }
    ncit = _MockClient(ncit_responses)
    uberon = _MockClient(uberon_responses)

    records, filler_to_source = await generate_candidates(
        ncit, uberon, _NCIT_VERSION, _UBERON_VERSION
    )

    assert len(records) == 1
    r = records[0]
    assert r.mapping_justification == "semapv:LexicalMatching"
    assert r.confidence == 0.5
    assert r.object_id == "UBERON:0000948"
    assert filler_to_source["C54321"] == "lexical"


@pytest.mark.unit
async def test_filler_with_label_not_found_in_upstream() -> None:
    """Filler has a label but no upstream label matches -> source='none'."""
    ncit_responses = {
        "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C99999"}],
        "SELECT ?code ?label WHERE": [{"code": "C99999", "label": "NoMatchLabel"}],
    }
    uberon_responses = {
        "hasDbXref": [],
        "SELECT ?concept ?label WHERE": [
            {
                "concept": "http://purl.obolibrary.org/obo/UBERON_0000948",
                "label": "liver",
            },
        ],
    }
    ncit = _MockClient(ncit_responses)
    uberon = _MockClient(uberon_responses)

    records, filler_to_source = await generate_candidates(
        ncit, uberon, _NCIT_VERSION, _UBERON_VERSION
    )

    assert len(records) == 0
    assert filler_to_source.get("C99999") == "none"


@pytest.mark.unit
async def test_filler_without_label_is_none() -> None:
    """Filler has no label at all -> source='none'."""
    ncit_responses = {
        "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C77777"}],
        "SELECT ?code ?label WHERE": [],
    }
    uberon_responses = {
        "hasDbXref": [],
        "SELECT ?concept ?label WHERE": [
            {
                "concept": "http://purl.obolibrary.org/obo/UBERON_0000948",
                "label": "liver",
            },
        ],
    }
    ncit = _MockClient(ncit_responses)
    uberon = _MockClient(uberon_responses)

    records, filler_to_source = await generate_candidates(
        ncit, uberon, _NCIT_VERSION, _UBERON_VERSION
    )

    assert len(records) == 0
    assert filler_to_source.get("C77777") == "none"


@pytest.mark.unit
async def test_generate_candidates_handles_empty_fillers() -> None:
    """No filler codes yields no records."""
    ncit = _MockClient({"SELECT DISTINCT ?fillerCode": []})
    uberon = _MockClient({"hasDbXref": []})

    records, filler_to_source = await generate_candidates(
        ncit, uberon, _NCIT_VERSION, _UBERON_VERSION
    )
    assert len(records) == 0
    assert len(filler_to_source) == 0


# -- Tests: coverage report ---------------------------------------------


@pytest.mark.unit
def test_coverage_report_shape() -> None:
    """Unit check: coverage report has correct structure and arithmetic."""
    fillers = {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10"}
    records = [
        SSSOMRecord(
            subject_id=f,
            predicate_id=CLOSE_MATCH,
            object_id=f"UBERON:{i:07d}",
            mapping_justification="semapv:DatabaseCrossReference",
            confidence=0.9,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
            author="xref-ingest-A3",
        )
        for i, f in enumerate(["C1", "C2", "C3", "C4", "C5"])
    ]
    filler_to_source = {
        "C1": "xref",
        "C2": "xref",
        "C3": "xref",
        "C4": "xref",
        "C5": "xref",
        "C6": "lexical",
        "C7": "lexical",
        "C8": "none",
        "C9": "none",
        "C10": "none",
    }

    report = candidate_coverage_report(fillers, records, filler_to_source)
    assert report["total_fillers"] == 10
    assert report["via_xref"] == 5
    assert report["via_lexical_only"] == 2
    assert report["no_candidate"] == 3
    expected = report["via_xref"] + report["via_lexical_only"] + report["no_candidate"]
    assert expected == report["total_fillers"]
    assert report["candidate_recall"] == 0.7


@pytest.mark.unit
def test_coverage_report_empty_fillers() -> None:
    """Empty filler set produces zero counts and 0.0 recall."""
    report = candidate_coverage_report(set(), [], {})
    assert report["total_fillers"] == 0
    assert report["via_xref"] == 0
    assert report["via_lexical_only"] == 0
    assert report["no_candidate"] == 0
    assert report["candidate_recall"] == 0.0


@pytest.mark.integration
async def test_coverage_report_shape_and_split() -> None:
    """Integration: live stores produce a valid coverage report."""
    pytest.skip("requires running Oxigraph :7888/:7889 containers")


def test_candidate_recall_not_regressed() -> None:
    """Placeholder -- baseline not yet recorded in #72."""
    pytest.skip("baseline not yet recorded in #72")


@pytest.mark.integration
async def test_ingest_candidates_pipeline() -> None:
    """Integration: full pipeline with live DB + Oxigraph."""
    pytest.skip("requires running Postgres :5433 and Oxigraph :7888/:7889 containers")


# -- Tests: store.update_run_metrics ------------------------------------


@pytest.mark.integration
async def test_update_run_metrics_roundtrip() -> None:
    """Integration: metrics can be stored and read back."""
    pytest.skip("requires running Postgres :5433 container")
