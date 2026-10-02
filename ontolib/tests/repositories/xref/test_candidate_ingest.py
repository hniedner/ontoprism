"""Tests for the Uberon/CL candidate ingest pipeline (PR-A3, issue #72).

Written BEFORE production code per STRICT TDD -- run to see them fail,
then implement ``candidate_ingest`` until they pass.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from ontolib.repositories.xref import candidate_ingest as candidate_ingest_module
from ontolib.repositories.xref.candidate_ingest import (
    CandidateInventory,
    CandidateSourceInventoryError,
    _build_xref_index,
    _iri_to_curie,
    build_uberon_xref_query,
    candidate_coverage_report,
    fetch_uberon_xrefs,
    generate_candidates,
    ingest_candidates,
)
from ontolib.repositories.xref.models import CandidateContext, SSSOMRecord
from ontolib.repositories.xref.source_versions import (
    MappingSourceVersionError,
    MappingSourceVersions,
    read_mapping_source_versions,
)
from ontolib.repositories.xref.vocab import (
    CLOSE_MATCH,
    COMPOSITE_MATCHING,
    DATABASE_CROSS_REFERENCE,
    LEXICAL_MATCHING,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDFS_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

# -- Mock SPARQL client -------------------------------------------------


class _MockClient:
    """Mock ``SparqlHttpClient`` returning canned SPARQL results.

    ``select`` matches each query against *responses* keys by containment;
    the first matching key wins.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.select_calls: list[str] = []

    async def select(self, query: str) -> list[dict[str, str]]:
        self.select_calls.append(query)
        for key, rows in self._responses.items():
            if key in query:
                return rows
        return []

    async def __aenter__(self) -> _MockClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


@pytest.mark.unit
async def test_uberon_xref_projection_fails_closed_on_partial_row() -> None:
    client = _MockClient(
        {
            "hasDbXref": [
                {
                    "upstream": "http://purl.obolibrary.org/obo/UBERON_0002048",
                    "xref": "NCIT:C12468",
                },
                {"upstream": "http://purl.obolibrary.org/obo/UBERON_0000171"},
            ]
        }
    )

    with pytest.raises(CandidateSourceInventoryError, match="incomplete xref row"):
        await fetch_uberon_xrefs(client)  # type: ignore[arg-type]


# -- Shared test data ---------------------------------------------------

_NCIT_VERSION = "26.07d"
_UBERON_VERSION = "http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"
_CL_VERSION = "http://purl.obolibrary.org/obo/cl/releases/2026-06-08/cl.owl"
_UBERON_ONTOLOGY = "http://purl.obolibrary.org/obo/uberon.owl"
_CL_ONTOLOGY = "http://purl.obolibrary.org/obo/cl.owl"
_VERSIONS = MappingSourceVersions(
    ncit=_NCIT_VERSION,
    uberon=_UBERON_VERSION,
    cl=_CL_VERSION,
)


def _ncit_version_rows(
    *, default: str = _NCIT_VERSION, stated: str = _NCIT_VERSION
) -> list[dict[str, str]]:
    return [
        {"location": "default", "version": default},
        {"location": "stated", "version": stated},
    ]


def _upstream_version_rows() -> list[dict[str, str]]:
    return [
        {"ontology": _UBERON_ONTOLOGY, "version": _UBERON_VERSION},
        {"ontology": _CL_ONTOLOGY, "version": _CL_VERSION},
    ]


def _inventory(*fillers: str, role: str = "R101") -> CandidateInventory:
    routes = {
        "R100": "op:AssociatedSite",
        "R101": "op:PrimarySite",
        "R102": "op:MetastaticSite",
        "R103": "op:NormalTissueOrigin",
        "R104": "op:CellOrigin",
    }
    return CandidateInventory(
        contexts=tuple(
            CandidateContext(
                source_role=role,
                source_filler=filler,
                normalized_axis=routes[role],
            )
            for filler in fillers
        ),
        excluded_counts=(),
    )


# Small hand-built fixture of known xref pairs.
_XREF_FIXTURE: list[tuple[str, str, str, str]] = [
    (
        "C3262",
        "http://purl.obolibrary.org/obo/UBERON_0002107",
        "NCIT:C3262",
        "UBERON:0002107",
    ),
    (
        "C12345",
        "http://purl.obolibrary.org/obo/CL_0000057",
        "NCIT:C12345",
        "CL:0000057",
    ),
]

# Fillers with no xref (matched via lexical).
_LEXICAL_FIXTURE: list[tuple[str, str, str, str]] = [
    ("C54321", "Liver", "http://purl.obolibrary.org/obo/UBERON_0000948", "liver"),
]

_ALL_FILLERS = {row[0] for row in _XREF_FIXTURE} | {row[0] for row in _LEXICAL_FIXTURE}


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_candidate_inventory_routes_direct_nested_and_mixed_roles(
    isolated_qlever_url: str,
) -> None:
    fixture = f"""
        @prefix ncit: <{NCIT_NS}> .
        @prefix owl: <{OWL_NS}> .
        @prefix rdfs: <{RDFS_NS}> .

        ncit:C99751 owl:equivalentClass [
            owl:intersectionOf (
                ncit:C99760
                [ a owl:Restriction ; owl:onProperty ncit:R103 ;
                  owl:someValuesFrom ncit:C99761 ]
            )
        ] .
        ncit:C99752 owl:equivalentClass [
            owl:intersectionOf (
                ncit:C99760
                [ a owl:Class ; owl:equivalentClass [
                    owl:intersectionOf ([
                        a owl:Restriction ;
                        owl:onProperty ncit:R104 ;
                        owl:someValuesFrom ncit:C99762
                    ])
                ] ]
            )
        ] .
        ncit:C99753 owl:equivalentClass [
            owl:intersectionOf (
                ncit:C99760
                [ a owl:Restriction ; owl:onProperty ncit:R101 ;
                  owl:someValuesFrom ncit:C99763 ]
                [ a owl:Restriction ; owl:onProperty ncit:R105 ;
                  owl:someValuesFrom ncit:C99764 ]
            )
        ] .
        ncit:C99754 owl:equivalentClass [
            owl:intersectionOf (
                ncit:C99760
                [ owl:unionOf (ncit:C99763 ncit:C99764) ]
            )
        ] .
        ncit:C99755 owl:equivalentClass [
            owl:intersectionOf (
                ncit:C99760
                [ a owl:Restriction ; owl:onProperty ncit:R999 ;
                  owl:someValuesFrom ncit:C99765 ]
            )
        ] .
        ncit:C99756 owl:equivalentClass [
            owl:intersectionOf (
                ncit:C99760
                [ a owl:Restriction ; owl:onProperty ncit:R999 ;
                  owl:someValuesFrom ncit:C99765 ]
            )
        ] .
    """

    async with ncit_sparql_client(isolated_qlever_url) as client:
        await client.load(
            fixture.encode(),
            content_type="text/turtle",
            graph_iri=STATED_GRAPH_IRI,
            replace=False,
        )
        inventory = await candidate_ingest_module.extract_candidate_inventory(
            client.select,
            ("C99751", "C99752", "C99753", "C99754", "C99755", "C99756"),
        )

    assert {
        (context.source_role, context.source_filler, context.normalized_axis)
        for context in inventory.contexts
    } == {
        ("R103", "C99761", "op:NormalTissueOrigin"),
        ("R104", "C99762", "op:CellOrigin"),
        ("R101", "C99763", "op:PrimarySite"),
    }
    assert inventory.excluded_by_role == {"R105": 1}
    assert inventory.unrouted_by_role == {"R999": 1}
    assert inventory.unknown_by_reason == {"unsupported-definition-constructor": 1}


# -- Tests: Query structure ---------------------------------------------


@pytest.mark.unit
def test_uberon_xref_query_structure() -> None:
    """The Uberon xref SPARQL query has the expected shape."""
    query = build_uberon_xref_query()
    assert "hasDbXref" in query
    assert "NCIT:" in query
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
    """Xref entries not starting with NCIT: are skipped."""
    xrefs = [
        {
            "upstream": "http://purl.obolibrary.org/obo/UBERON_0002107",
            "xref": "NCIT:C3262",
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
        ncit, uberon, _VERSIONS, inventory=_inventory(*(t[0] for t in _XREF_FIXTURE))
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
        ncit, uberon, _VERSIONS, inventory=_inventory(*(t[0] for t in _XREF_FIXTURE))
    )
    for r in records:
        assert r.subject_source_version == _NCIT_VERSION
        expected = _CL_VERSION if r.object_id.startswith("CL:") else _UBERON_VERSION
        assert r.object_source_version == expected
        assert r.author == "xref-ingest-A3"


@pytest.mark.unit
async def test_each_candidate_has_its_own_source_ontology_version() -> None:
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?fillerCode": [
                {"fillerCode": "C3262"},
                {"fillerCode": "C12345"},
            ],
            "SELECT ?code ?label WHERE": [],
        }
    )
    uberon = _MockClient(
        {
            "hasDbXref": [
                {"upstream": upstream, "xref": xref}
                for _, upstream, xref, _ in _XREF_FIXTURE
            ]
        }
    )

    records, _ = await generate_candidates(
        ncit,
        uberon,
        _VERSIONS,
        inventory=_inventory("C3262", "C12345"),
    )

    assert {record.object_id: record.object_source_version for record in records} == {
        "UBERON:0002107": _UBERON_VERSION,
        "CL:0000057": _CL_VERSION,
    }


@pytest.mark.unit
async def test_role_filler_routes_survive_generation_and_report_exclusions() -> None:
    included = (
        CandidateContext("R101", "C3262", "op:PrimarySite"),
        CandidateContext("R103", "C3262", "op:NormalTissueOrigin"),
    )
    inventory = CandidateInventory(
        contexts=included,
        excluded_counts=(("R105", 2),),
        unrouted_counts=(("R999", 1),),
    )
    ncit = _MockClient({"SELECT ?code ?label WHERE": []})
    uberon = _MockClient(
        {
            "hasDbXref": [
                {
                    "upstream": "http://purl.obolibrary.org/obo/UBERON_0002107",
                    "xref": "NCIT:C3262",
                }
            ]
        }
    )

    records, filler_to_source = await generate_candidates(
        ncit,
        uberon,
        _VERSIONS,
        inventory=inventory,
    )
    report = candidate_coverage_report(
        inventory.fillers,
        records,
        filler_to_source,
        inventory,
    )

    assert records[0].candidate_contexts == included
    assert report["extracted_candidates_by_role"] == {"R101": 1, "R103": 1}
    assert report["generated_candidates_by_role"] == {"R101": 1, "R103": 1}
    assert report["excluded_candidates_by_role"] == {"R105": 2}
    assert report["excluded_r105_candidates"] == 2
    assert report["unrouted_candidates_by_role"] == {"R999": 1}


@pytest.mark.unit
async def test_versions_are_read_from_the_three_real_ontology_header_shapes() -> None:
    ncit = _MockClient({"SELECT DISTINCT ?location ?version": _ncit_version_rows()})
    uberon = _MockClient(
        {"SELECT DISTINCT ?ontology ?version": _upstream_version_rows()}
    )

    versions = await read_mapping_source_versions(
        ncit,
        uberon,
        expected_ncit_version=_NCIT_VERSION,
        expected_uberon_version=_UBERON_VERSION,
    )

    assert versions.ncit == _NCIT_VERSION
    assert versions.uberon == _UBERON_VERSION
    assert versions.cl == _CL_VERSION
    assert "owl:versionInfo" in ncit.select_calls[0]
    assert _UBERON_ONTOLOGY in uberon.select_calls[0]
    assert _CL_ONTOLOGY in uberon.select_calls[0]
    assert "owl:versionIRI" in uberon.select_calls[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ncit_rows", "upstream_rows", "message"),
    [
        (
            _ncit_version_rows(stated="26.06d"),
            _upstream_version_rows(),
            "NCIt default and stated versions differ",
        ),
        (
            _ncit_version_rows(),
            [*_upstream_version_rows(), {"ontology": _CL_ONTOLOGY, "version": "x"}],
            "ambiguous CL version",
        ),
        (
            _ncit_version_rows(),
            [
                {"ontology": _UBERON_ONTOLOGY, "version": _UBERON_VERSION},
                {"ontology": _CL_ONTOLOGY, "version": _UBERON_VERSION},
            ],
            "CL version IRI does not identify CL",
        ),
        (
            _ncit_version_rows(),
            [
                {
                    "ontology": _UBERON_ONTOLOGY,
                    "version": (
                        "http://purl.obolibrary.org/obo/uberon/releases/"
                        "2026-05-01/uberon.owl"
                    ),
                },
                {"ontology": _CL_ONTOLOGY, "version": _CL_VERSION},
            ],
            "Uberon ontology version does not match its certified source",
        ),
        (
            _ncit_version_rows(),
            [{"ontology": _UBERON_ONTOLOGY, "version": _UBERON_VERSION}, {}],
            "incomplete ontology version row",
        ),
    ],
)
async def test_source_version_preflight_rejects_ambiguous_or_mismatched_metadata(
    ncit_rows: list[dict[str, str]],
    upstream_rows: list[dict[str, str]],
    message: str,
) -> None:
    ncit = _MockClient({"SELECT DISTINCT ?location ?version": ncit_rows})
    uberon = _MockClient({"SELECT DISTINCT ?ontology ?version": upstream_rows})

    with pytest.raises(MappingSourceVersionError, match=message):
        await read_mapping_source_versions(
            ncit,
            uberon,
            expected_ncit_version=_NCIT_VERSION,
            expected_uberon_version=_UBERON_VERSION,
        )


@pytest.mark.unit
async def test_missing_cl_version_refuses_ingest_before_any_write() -> None:
    ncit = _MockClient({"SELECT DISTINCT ?location ?version": _ncit_version_rows()})
    uberon = _MockClient(
        {
            "SELECT DISTINCT ?ontology ?version": [
                {"ontology": _UBERON_ONTOLOGY, "version": _UBERON_VERSION}
            ]
        }
    )
    store = AsyncMock()

    with pytest.raises(MappingSourceVersionError, match="missing CL version"):
        await ingest_candidates(  # type: ignore[arg-type]
            store,
            cast("Any", ncit),
            uberon,
            _NCIT_VERSION,
            _UBERON_VERSION,
            ncit_source_identity="a" * 64,
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
            observe_source_identities=AsyncMock(
                return_value=("a" * 64, "b" * 64, "c" * 64)
            ),
        )

    store.upsert_run.assert_not_awaited()


@pytest.mark.unit
async def test_changed_source_identity_refuses_ingest_before_any_write() -> None:
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?location ?version": _ncit_version_rows(),
            "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C3262"}],
            "SELECT ?code ?label WHERE": [],
        }
    )
    uberon = _MockClient(
        {
            "SELECT DISTINCT ?ontology ?version": _upstream_version_rows(),
            "hasDbXref": [
                {
                    "upstream": "http://purl.obolibrary.org/obo/UBERON_0002107",
                    "xref": "NCIT:C3262",
                }
            ],
        }
    )
    store = AsyncMock()

    with pytest.raises(ValueError, match="source identity changed"):
        await ingest_candidates(  # type: ignore[arg-type]
            store,
            cast("Any", ncit),
            uberon,
            _NCIT_VERSION,
            _UBERON_VERSION,
            ncit_source_identity="a" * 64,
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
            observe_source_identities=AsyncMock(
                return_value=("a" * 64, "d" * 64, "c" * 64)
            ),
            inventory=_inventory("C3262"),
        )

    store.upsert_run.assert_not_awaited()


@pytest.mark.unit
async def test_unavailable_source_identity_refuses_ingest_before_any_write() -> None:
    store = AsyncMock()

    with pytest.raises(ValueError, match="String should match pattern"):
        await ingest_candidates(  # type: ignore[arg-type]
            store,
            cast("Any", _MockClient({})),
            _MockClient({}),
            _NCIT_VERSION,
            _UBERON_VERSION,
            ncit_source_identity="",
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
            observe_source_identities=AsyncMock(),
        )

    store.upsert_run.assert_not_awaited()


@pytest.mark.unit
async def test_partial_upstream_row_refuses_ingest_before_any_write() -> None:
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?location ?version": _ncit_version_rows(),
            "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C3262"}],
        }
    )
    uberon = _MockClient(
        {
            "SELECT DISTINCT ?ontology ?version": _upstream_version_rows(),
            "hasDbXref": [
                {"upstream": "http://purl.obolibrary.org/obo/UBERON_0002107"}
            ],
        }
    )
    store = AsyncMock()

    with pytest.raises(CandidateSourceInventoryError, match="incomplete xref row"):
        await ingest_candidates(  # type: ignore[arg-type]
            store,
            cast("Any", ncit),
            uberon,
            _NCIT_VERSION,
            _UBERON_VERSION,
            ncit_source_identity="a" * 64,
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
            observe_source_identities=AsyncMock(
                return_value=("a" * 64, "b" * 64, "c" * 64)
            ),
            inventory=_inventory("C3262"),
        )

    store.upsert_run.assert_not_awaited()


@pytest.mark.unit
async def test_unknown_obo_iri_is_not_misclassified_as_an_uberon_mapping() -> None:
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C1"}],
            "SELECT ?code ?label WHERE": [],
        }
    )
    uberon = _MockClient(
        {
            "hasDbXref": [
                {
                    "upstream": "http://purl.obolibrary.org/obo/GO_0008150",
                    "xref": "NCIT:C1",
                }
            ]
        }
    )

    with pytest.raises(CandidateSourceInventoryError, match="unknown source ontology"):
        await generate_candidates(
            ncit,
            uberon,
            _VERSIONS,
            inventory=_inventory("C1"),
        )


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
        ncit, uberon, _VERSIONS, inventory=_inventory(*(t[0] for t in _XREF_FIXTURE))
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
        ncit,
        uberon,
        _VERSIONS,
        inventory=_inventory(*(t[0] for t in _LEXICAL_FIXTURE)),
    )

    assert len(records) == 1
    r = records[0]
    assert r.mapping_justification == "semapv:LexicalMatching"
    assert r.confidence == 0.5
    assert r.object_id == "UBERON:0000948"
    assert filler_to_source["C54321"] == "lexical"


@pytest.mark.unit
async def test_lexical_candidates_exclude_foreign_obo_prefixes() -> None:
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C54321"}],
            "SELECT ?code ?label WHERE": [{"code": "C54321", "label": "liver"}],
        }
    )
    uberon = _MockClient(
        {
            "hasDbXref": [],
            "SELECT ?concept ?label WHERE": [
                {
                    "concept": "http://purl.obolibrary.org/obo/UBERON_0000948",
                    "label": "liver",
                },
                {
                    "concept": "http://purl.obolibrary.org/obo/GO_0008150",
                    "label": "liver",
                },
            ],
        }
    )

    records, _ = await generate_candidates(
        ncit, uberon, _VERSIONS, inventory=_inventory("C54321")
    )

    assert [(row.object_system, row.object_id) for row in records] == [
        ("uberon-cl", "UBERON:0000948")
    ]


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
        ncit, uberon, _VERSIONS, inventory=_inventory("C99999")
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
        ncit, uberon, _VERSIONS, inventory=_inventory("C77777")
    )

    assert len(records) == 0
    assert filler_to_source.get("C77777") == "none"


# -- Tests: source agreement (#73 / D33 Option 1, D34) ------------------
#
# Both passes now run over ALL fillers.  Where they converge on the same pair, that pair
# was produced by two independent processes and is recorded as ONE composite candidate —
# `concept_xref` is keyed on (run_id, subject_id, predicate_id, object_id), so two rows
# differing only in `mapping_justification` collide on the primary key and the second is
# silently discarded.  Emitting both records would therefore lose the agreement at the
# store; the composite record is what carries it through.

_LUNG_IRI = "http://purl.obolibrary.org/obo/UBERON_0002048"
_BRAIN_IRI = "http://purl.obolibrary.org/obo/UBERON_0000955"


@pytest.mark.unit
async def test_agreeing_xref_and_label_yield_one_composite_candidate() -> None:
    """The two-signal case: Uberon xrefs NCIT:C12468 *and* the labels match."""
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C12468"}],
            "SELECT ?code ?label WHERE": [{"code": "C12468", "label": "Lung"}],
        }
    )
    uberon = _MockClient(
        {
            "hasDbXref": [{"upstream": _LUNG_IRI, "xref": "NCIT:C12468"}],
            "SELECT ?concept ?label WHERE": [{"concept": _LUNG_IRI, "label": "lung"}],
        }
    )

    records, filler_to_source = await generate_candidates(
        ncit, uberon, _VERSIONS, inventory=_inventory("C12468")
    )

    assert len(records) == 1, "one pair must yield one row, or the store drops one"
    assert records[0].subject_id == "C12468"
    assert records[0].object_id == "UBERON:0002048"
    assert records[0].mapping_justification == COMPOSITE_MATCHING
    assert records[0].confidence > 0.9  # stronger than either source alone
    assert filler_to_source["C12468"] == "both"


@pytest.mark.unit
async def test_the_lexical_pass_also_runs_over_a_filler_that_has_an_xref() -> None:
    """The xref and the label can point at *different* upstream classes.

    The old `fillers - matched_via_xref` partition suppressed the lexical candidate
    entirely whenever any xref existed, so this disagreement was invisible: the xref
    candidate was published unchallenged.  Now both are proposed, neither can promote
    (each carries a single signal), and they contest the same subject.
    """
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C12468"}],
            "SELECT ?code ?label WHERE": [{"code": "C12468", "label": "Lung"}],
        }
    )
    uberon = _MockClient(
        {
            "hasDbXref": [{"upstream": _LUNG_IRI, "xref": "NCIT:C12468"}],
            "SELECT ?concept ?label WHERE": [{"concept": _BRAIN_IRI, "label": "lung"}],
        }
    )

    records, filler_to_source = await generate_candidates(
        ncit, uberon, _VERSIONS, inventory=_inventory("C12468")
    )

    assert {(r.object_id, r.mapping_justification) for r in records} == {
        ("UBERON:0002048", DATABASE_CROSS_REFERENCE),
        ("UBERON:0000955", LEXICAL_MATCHING),
    }
    assert filler_to_source["C12468"] == "both"


@pytest.mark.unit
async def test_an_xref_filler_whose_label_matches_nothing_stays_xref() -> None:
    """A composite justification is minted ONLY when two passes really did converge."""
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?fillerCode": [{"fillerCode": "C12468"}],
            "SELECT ?code ?label WHERE": [{"code": "C12468", "label": "Lung"}],
        }
    )
    uberon = _MockClient(
        {
            "hasDbXref": [{"upstream": _LUNG_IRI, "xref": "NCIT:C12468"}],
            "SELECT ?concept ?label WHERE": [{"concept": _BRAIN_IRI, "label": "brain"}],
        }
    )

    records, filler_to_source = await generate_candidates(
        ncit, uberon, _VERSIONS, inventory=_inventory("C12468")
    )

    assert len(records) == 1
    assert records[0].mapping_justification == DATABASE_CROSS_REFERENCE
    assert records[0].confidence == 0.9
    assert filler_to_source["C12468"] == "xref"


@pytest.mark.unit
async def test_ingest_refuses_empty_filler_inventory_before_writing() -> None:
    ncit = _MockClient(
        {
            "SELECT DISTINCT ?location ?version": _ncit_version_rows(),
            "SELECT DISTINCT ?fillerCode": [],
        }
    )
    uberon = _MockClient(
        {
            "SELECT DISTINCT ?ontology ?version": _upstream_version_rows(),
            "hasDbXref": [],
        }
    )
    store = AsyncMock()

    with pytest.raises(
        CandidateSourceInventoryError, match="NCIt filler inventory is empty"
    ):
        await ingest_candidates(  # type: ignore[arg-type]
            store,
            cast("Any", ncit),
            uberon,
            _NCIT_VERSION,
            _UBERON_VERSION,
            ncit_source_identity="a" * 64,
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
            observe_source_identities=AsyncMock(
                return_value=("a" * 64, "b" * 64, "c" * 64)
            ),
            inventory=_inventory(),
        )
    store.upsert_run.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    "inventory",
    [
        CandidateInventory(contexts=(), excluded_counts=(("R105", 1),)),
        CandidateInventory(
            contexts=(),
            excluded_counts=(),
            unknown_counts=(("unsupported-definition-constructor", 1),),
        ),
    ],
    ids=("all-excluded", "all-unknown"),
)
async def test_ingest_refuses_inventory_without_routed_fillers_before_writing(
    inventory: CandidateInventory,
) -> None:
    ncit = _MockClient({"SELECT DISTINCT ?location ?version": _ncit_version_rows()})
    uberon = _MockClient(
        {"SELECT DISTINCT ?ontology ?version": _upstream_version_rows()}
    )
    store = AsyncMock()

    with pytest.raises(
        CandidateSourceInventoryError,
        match="NCIt filler inventory has no routed fillers",
    ):
        await ingest_candidates(  # type: ignore[arg-type]
            store,
            cast("Any", ncit),
            uberon,
            _NCIT_VERSION,
            _UBERON_VERSION,
            ncit_source_identity="a" * 64,
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
            observe_source_identities=AsyncMock(
                return_value=("a" * 64, "b" * 64, "c" * 64)
            ),
            inventory=inventory,
        )

    store.upsert_run.assert_not_awaited()


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
            mapping_justification="https://ontoprism.org/vocab#PublisherDatabaseCrossReference",
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

    report = candidate_coverage_report(
        fillers,
        records,
        filler_to_source,
        _inventory(*sorted(fillers)),
    )
    assert report["total_fillers"] == 10
    assert report["via_xref"] == 5
    assert report["via_lexical_only"] == 2
    assert report["no_candidate"] == 3
    expected = report["via_xref"] + report["via_lexical_only"] + report["no_candidate"]
    assert expected == report["total_fillers"]
    assert report["candidate_recall"] == 0.7


@pytest.mark.unit
def test_coverage_report_counts_the_pairs_two_sources_agree_on() -> None:
    """`source_agreement_pairs` is the count that makes D33's yield legible.

    It is the only bucket that can promote without curation or structural corroboration,
    so a run in which it is zero has (again) promoted nothing but curated pairs — and
    must say so rather than leave the operator to infer it from `promoted`.
    """
    fillers = {"C1", "C2", "C3", "C4"}
    records = [
        SSSOMRecord(
            subject_id=subject,
            predicate_id=CLOSE_MATCH,
            object_id=obj,
            mapping_justification=justification,
            confidence=confidence,
            subject_source_version=_NCIT_VERSION,
            object_source_version=_UBERON_VERSION,
        )
        for subject, obj, justification, confidence in (
            ("C1", "UBERON:0002048", COMPOSITE_MATCHING, 0.95),
            ("C2", "UBERON:0000955", DATABASE_CROSS_REFERENCE, 0.9),
            ("C3", "UBERON:0000948", LEXICAL_MATCHING, 0.5),
        )
    ]
    source = {"C1": "both", "C2": "xref", "C3": "lexical", "C4": "none"}

    report = candidate_coverage_report(
        fillers,
        records,
        source,
        _inventory(*sorted(fillers)),
    )

    assert report["source_agreement_pairs"] == 1
    # a "both" filler holds an xref candidate, so it still counts under via_xref —
    # the three buckets must keep partitioning the filler set exactly
    assert report["via_xref"] == 2
    assert report["via_lexical_only"] == 1
    assert report["no_candidate"] == 1
    assert (
        report["via_xref"] + report["via_lexical_only"] + report["no_candidate"]
        == report["total_fillers"]
    )
    assert report["candidate_recall"] == 0.75


@pytest.mark.unit
def test_coverage_report_empty_fillers() -> None:
    """Empty filler set produces zero counts and 0.0 recall."""
    report = candidate_coverage_report(set(), [], {}, _inventory())
    assert report["total_fillers"] == 0
    assert report["via_xref"] == 0
    assert report["via_lexical_only"] == 0
    assert report["no_candidate"] == 0
    assert report["candidate_recall"] == 0.0
