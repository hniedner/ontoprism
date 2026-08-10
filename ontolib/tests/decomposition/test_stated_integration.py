"""Integration tests for the decomposition query layer against real QLever.

Two explicit tiers:
- SPARQL builders parse against the bounded disposable engine.
- ``full_store`` contracts run only against configured stated NCIt.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import TYPE_CHECKING

import httpx
import pytest

from ontolib.decomposition import stated_queries
from ontolib.decomposition.complete_definition import (
    build_complete_definition_query,
    read_complete_definition,
)
from ontolib.decomposition.extract import (
    AncestorPair,
    PartOfPair,
    ancestor_pairs_from_rows,
    concepts_from_rows,
    make_is_ancestor,
    part_of_pairs_from_rows,
    semantic_type_of_from_rows,
)
from ontolib.decomposition.filler_selection import select_constituents
from ontolib.decomposition.models import (
    GenusDefinitionFact,
    RestrictionDefinitionFact,
    RoleRestriction,
)
from ontolib.decomposition.run import _decompose_one
from ontolib.decomposition.stated_queries import (
    build_ancestor_pairs_query,
    build_genus_walk_members_query,
    build_in_scope_concepts_query,
    build_part_of_pairs_queries,
    build_part_of_pairs_query,
    build_role_restrictions_query,
    build_semantic_type_of_query,
    build_semantic_type_query,
    resolve_morphology_filler,
    walk_genus_chain,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDFS_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.sparql_http_client import (
    flatten_bindings,
    parse_ask_result,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

_DEFAULT_NCIT_URL = "http://localhost:7888"
_EXPANSION_NODE = re.compile(rf"BIND\(<{re.escape(NCIT_NS)}(C[0-9]+)> AS \?node\)")


def _url() -> str:
    return os.environ.get(
        "NCIT_STATED_SPARQL_URL",
        os.environ.get("NCIT_SPARQL_URL", _DEFAULT_NCIT_URL),
    )


def _reachable(url: str) -> bool:
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/query",
            content=b"ASK {}",
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=2.0,
        )
    except httpx.HTTPError:
        return False
    resp.raise_for_status()
    return True


def _stated_loaded(url: str) -> bool:
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/query",
            content=(f"ASK {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }}".encode()),
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=2.0,
        )
    except httpx.HTTPError:
        return False
    resp.raise_for_status()
    return parse_ask_result(resp.json())


@pytest.mark.unit
def test_stated_store_url_prefers_dedicated_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NCIT_SPARQL_URL", "http://inferred.example")
    monkeypatch.setenv("NCIT_STATED_SPARQL_URL", "http://stated.example")

    assert _url() == "http://stated.example"


@pytest.mark.unit
def test_stated_store_url_falls_back_to_general_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NCIT_SPARQL_URL", "http://general.example")
    monkeypatch.delenv("NCIT_STATED_SPARQL_URL", raising=False)

    assert _url() == "http://general.example"


def _fixture_expansions() -> dict[str, list[tuple[str, str]]]:
    return {
        "C20003": [("whole", "C99206")],
        "C99203": [("parent", "C99204")],
        "C99204": [("whole", "C99205")],
        "C99206": [("whole", "C99207")],
        "C99210": [("whole", "C99211")],
        "C99211": [("whole", "C99210")],
        "C99220": [
            ("whole", "C99221"),
            ("whole", "C99222"),
            ("whole", "C99223"),
        ],
        "C99221": [("whole", "C99223")],
        "C99222": [("whole", "C99223")],
        "C99230": [("whole", "C99231"), ("whole", "C99231")],
    }


class _SingleAttemptClient:
    def __init__(self, select_once: stated_queries.SelectRows) -> None:
        self._select_once = select_once

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]:
        return await self._select_once(
            query,
            required_variables=required_variables,
        )


@pytest.mark.integration
async def test_stated_query_builders_parse_against_disposable_store(
    isolated_qlever_url: str,
) -> None:
    async with ncit_sparql_client(isolated_qlever_url) as client:
        assert isinstance(
            await client.select(build_role_restrictions_query("C6135")), list
        )
        assert isinstance(await client.select(build_semantic_type_query("C6135")), list)
        assert isinstance(
            await client.select(build_ancestor_pairs_query(["C12400", "C12401"])), list
        )
        assert isinstance(
            await client.select(
                build_in_scope_concepts_query(["Neoplastic Process"], limit=5)
            ),
            list,
        )
        for q in build_genus_walk_members_query("C6135"):
            rows = await client.select(q)
            assert isinstance(rows, list)


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_genus_traversal_matches_disposable_owl_list_shape(
    isolated_qlever_url: str,
) -> None:
    fixture = f"""
        @prefix ncit: <{NCIT_NS}> .
        @prefix owl: <{OWL_NS}> .
        @prefix rdfs: <{RDFS_NS}> .

        ncit:C99401 owl:equivalentClass [
            owl:intersectionOf (
                ncit:C99402
                [
                    a owl:Restriction ;
                    owl:onProperty ncit:R101 ;
                    owl:someValuesFrom ncit:C99410
                ]
            )
        ] .
        ncit:C99402
            rdfs:label "Synthetic Carcinoma by AJCC Stage II" ;
            owl:equivalentClass [
                owl:intersectionOf (
                    ncit:C99403
                    [
                        a owl:Restriction ;
                        owl:onProperty ncit:R101 ;
                        owl:someValuesFrom ncit:C99411
                    ]
                )
            ] .
        ncit:C99403 rdfs:label "Synthetic Carcinoma" .
    """
    default_labels = f"""
        @prefix ncit: <{NCIT_NS}> .
        @prefix rdfs: <{RDFS_NS}> .

        ncit:R101 rdfs:label "Disease_Has_Primary_Anatomic_Site" .
        ncit:C99991 rdfs:label "Unrelated Default Label One" .
        ncit:C99992 rdfs:label "Unrelated Default Label Two" .
    """

    async with ncit_sparql_client(isolated_qlever_url) as client:
        await client.load(
            fixture.encode(),
            content_type="text/turtle",
            graph_iri=STATED_GRAPH_IRI,
            replace=False,
        )
        await client.load(
            default_labels.encode(),
            content_type="text/turtle",
            replace=False,
        )
        root_queries = build_genus_walk_members_query("C99401")
        genus_rows = await client.select_once(
            root_queries[0],
            required_variables={"member"},
        )
        restriction_rows = await client.select_once(
            root_queries[1],
            required_variables={"member"},
        )
        roles = await walk_genus_chain(client.select, "C99401", max_depth=3)
        morphology = await resolve_morphology_filler(
            client.select,
            "C99401",
            max_depth=3,
        )

    assert genus_rows == [{"member": f"{NCIT_NS}C99402"}]
    assert len(restriction_rows) == 1
    assert {
        key: value for key, value in restriction_rows[0].items() if key != "member"
    } == {
        "role": f"{NCIT_NS}R101",
        "roleLabel": "Disease_Has_Primary_Anatomic_Site",
        "target": f"{NCIT_NS}C99410",
        "type": f"{OWL_NS}Restriction",
    }
    assert [
        (role.role_code, role.filler_code, role.anchoring_genus) for role in roles
    ] == [
        ("R101", "C99410", "C99401"),
        ("R101", "C99411", "C99402"),
    ]
    assert morphology == "C99403"

    double_rows: dict[str, list[dict[str, str | None]]] = {}
    for code, genus, filler in (
        ("C99401", "C99402", "C99410"),
        ("C99402", "C99403", "C99411"),
    ):
        double_rows[build_complete_definition_query(code)] = [
            {
                "expression": f"_:expression-{code}",
                "parentExpression": None,
                "nestingDepth": "0",
                "position": "0",
                "member": f"{NCIT_NS}{genus}",
                "childExpression": ("_:defined" if genus == "C99402" else None),
                "nestedExpression": None,
                "role": None,
                "target": None,
                "overflow": "false",
            },
            {
                "expression": f"_:expression-{code}",
                "parentExpression": None,
                "nestingDepth": "0",
                "position": "1",
                "member": "_:restriction",
                "childExpression": None,
                "nestedExpression": None,
                "role": f"{NCIT_NS}R101",
                "target": f"{NCIT_NS}{filler}",
                "overflow": "false",
            },
        ]

    async def double_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        if "SELECT ?role ?roleLabel" in query:
            assert set(required_variables) == {"role"}
            return [
                {
                    "role": f"{NCIT_NS}R101",
                    "roleLabel": "Disease_Has_Primary_Anatomic_Site",
                }
            ]
        assert set(required_variables) == {
            "expression",
            "list",
            "cell",
        }
        return double_rows.get(query, [])

    assert await walk_genus_chain(double_select, "C99401", max_depth=3) == roles

    semantic_types = {
        "C99410": "Body Part, Organ, or Organ Component",
        "C99411": "Anatomical Structure",
    }
    constituents = select_constituents(
        roles,
        lambda _ancestor, _descendant: False,
        semantic_type_of=semantic_types.get,
    )
    assert {(item.axis, item.filler_code) for item in constituents} == {
        ("op:PrimarySite", "C99410"),
        ("op:AssociatedRegion", "C99411"),
    }


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_nested_intersection_groups_and_late_members_match_real_qlever(
    isolated_qlever_url: str,
) -> None:
    fixture = f"""
        @prefix ncit: <{NCIT_NS}> .
        @prefix owl: <{OWL_NS}> .
        @prefix rdfs: <{RDFS_NS}> .

        ncit:C99601
            <{NCIT_NS}P106> "Neoplastic Process" ;
            owl:equivalentClass [
                owl:intersectionOf (
                    ncit:C99602
                    ncit:C99603
                    [
                        a owl:Class ;
                        owl:equivalentClass [
                            owl:intersectionOf (
                                [
                                    a owl:Restriction ;
                                    owl:onProperty ncit:R140 ;
                                    owl:someValuesFrom ncit:C99610
                                ]
                                [
                                    a owl:Restriction ;
                                    owl:onProperty ncit:R141 ;
                                    owl:someValuesFrom ncit:C99611
                                ]
                            )
                        ]
                    ]
                    [
                        a owl:Restriction ;
                        owl:onProperty ncit:R101 ;
                        owl:someValuesFrom ncit:C99612
                    ]
                    [
                        a owl:Restriction ;
                        owl:onProperty ncit:R105 ;
                        owl:someValuesFrom ncit:C99613
                    ]
                    [
                        a owl:Restriction ;
                        owl:onProperty ncit:R108 ;
                        owl:someValuesFrom ncit:C99614
                    ]
                    [
                        a owl:Restriction ;
                        owl:onProperty ncit:R139 ;
                        owl:someValuesFrom ncit:C99615
                    ]
                    [
                        a owl:Restriction ;
                        owl:onProperty ncit:R142 ;
                        owl:someValuesFrom ncit:C99616
                    ]
                )
            ] .
        ncit:C99602 rdfs:label "Synthetic Morphology" .
        ncit:C99603 rdfs:label "Synthetic Second Genus" .
        ncit:R101 rdfs:label "Disease_Has_Primary_Anatomic_Site" .
        ncit:R105 rdfs:label "Disease_Has_Abnormal_Cell" .
        ncit:R108 rdfs:label "Disease_Has_Finding" .
        ncit:R139 rdfs:label "Disease_Has_Something" .
        ncit:R140 rdfs:label "Disease_Has_Nested_One" .
        ncit:R141 rdfs:label "Disease_Has_Nested_Two" .
        ncit:R142 rdfs:label "Disease_Has_Late_Member" .
    """

    async with ncit_sparql_client(isolated_qlever_url) as client:
        await client.load(
            fixture.encode(),
            content_type="text/turtle",
            graph_iri=STATED_GRAPH_IRI,
            replace=False,
        )
        direct_primitive_rows = await client.select(
            f"SELECT ?expression WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            f"<{NCIT_NS}C99603> <{OWL_NS}equivalentClass> ?expression . }} }}"
        )
        assert direct_primitive_rows == []
        primitive_rows = await client.select(build_complete_definition_query("C99603"))
        assert primitive_rows == []
        complete = await read_complete_definition(client.select, "C99601")
        roles = await walk_genus_chain(client.select, "C99601", max_depth=3)

    assert {group.anchor_code for group in complete.groups} == {"C99601"}
    assert len(complete.groups) == 2
    assert len(complete.root_group_ids) == 1
    root = next(
        group for group in complete.groups if group.group_id in complete.root_group_ids
    )
    assert len(root.child_group_ids) == 1
    assert {
        (fact.role_code, fact.filler_code)
        for fact in complete.facts
        if isinstance(fact, RestrictionDefinitionFact)
    } == {
        ("R101", "C99612"),
        ("R105", "C99613"),
        ("R108", "C99614"),
        ("R139", "C99615"),
        ("R140", "C99610"),
        ("R141", "C99611"),
        ("R142", "C99616"),
    }
    assert {
        (role.role_code, role.filler_code, role.anchoring_genus) for role in roles
    } == {
        ("R101", "C99612", "C99601"),
        ("R105", "C99613", "C99601"),
        ("R108", "C99614", "C99601"),
        ("R139", "C99615", "C99601"),
        ("R140", "C99610", "C99601"),
        ("R141", "C99611", "C99601"),
        ("R142", "C99616", "C99601"),
    }


@pytest.mark.integration
async def test_part_of_pairs_queries_cover_production_shaped_disposable_store(
    isolated_qlever_url: str,
) -> None:
    # Sorting puts C12510 and C20000-C20014 in the first tile and C32291 in the
    # second, exercising all four tile combinations.
    codes = [
        "C12510",
        *(f"C200{i:02d}" for i in range(15)),
        "C32291",
        "C99101",
        "C99102",
        "C99103",
        "C99104",
        "C99105",
        "C99106",
        "C99250",
        "C99251",
    ]
    async with ncit_sparql_client(isolated_qlever_url) as client:
        rows = []
        for query in build_part_of_pairs_queries(codes):
            rows.extend(await client.select(query))
        empty_rows = await client.select(
            build_part_of_pairs_query(part_codes=[], whole_codes=[])
        )
        direct_raw = await client.select_raw(
            build_part_of_pairs_query(
                part_codes=["C32291"],
                whole_codes=["C12510"],
            )
        )
        direct_rows = flatten_bindings(direct_raw)
        reverse_rows = await client.select(
            build_part_of_pairs_query(
                part_codes=["C12510"],
                whole_codes=["C32291"],
            )
        )
        health = await client.select(
            f"SELECT ?s WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }} LIMIT 1"
        )

    assert Counter(part_of_pairs_from_rows(rows)) == Counter(
        {
            PartOfPair(part="C32291", whole="C12510"): 1,
            PartOfPair(part="C20000", whole="C99106"): 1,
            PartOfPair(part="C20001", whole="C20002"): 1,
            PartOfPair(part="C99101", whole="C99102"): 1,
            PartOfPair(part="C99103", whole="C99104"): 1,
        }
    )
    assert empty_rows == []
    assert direct_rows == [{"part": f"{NCIT_NS}C32291", "whole": f"{NCIT_NS}C12510"}]
    assert direct_raw["head"] == {"vars": ["part", "whole"]}
    assert direct_raw["results"] == {
        "bindings": [
            {
                "part": {"type": "uri", "value": f"{NCIT_NS}C32291"},
                "whole": {"type": "uri", "value": f"{NCIT_NS}C12510"},
            }
        ]
    }
    # QLever adds timing/result-size metadata to a standards-compliant result.
    # The timing is intentionally not pinned, but its real envelope is.
    assert direct_raw["meta"]["result-size-total"] == 1
    assert isinstance(direct_raw["meta"]["query-time-ms"], int)
    assert reverse_rows == []
    assert health
    assert health[0].get("s")


@pytest.mark.integration
async def test_part_of_closure_matches_double_on_production_shaped_store(
    isolated_qlever_url: str,
) -> None:
    codes = [
        "C20003",
        *(f"C300{i:02d}" for i in range(15)),
        "C99203",
        "C99205",
        "C99206",
        "C99207",
        "C99210",
        "C99211",
        "C99220",
        "C99223",
        "C99230",
        "C99231",
        "C99240",
        "C99250",
        "C99251",
    ]
    expansions = _fixture_expansions()
    double_requests: list[tuple[str, ...]] = []
    oversized_store_calls = 0

    async def double_select(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str | None]]:
        assert set(required_variables) == {"node", "kind", "target", "targetType"}
        requested = tuple(dict.fromkeys(_EXPANSION_NODE.findall(query)))
        assert 1 <= len(requested) <= 16
        double_requests.append(requested)
        return [
            {
                "node": f"{NCIT_NS}{code}",
                "kind": kind,
                "target": f"{NCIT_NS}{target}",
                "targetType": "iri",
            }
            for code in requested
            for kind, target in expansions.get(code, ())
        ]

    double_pairs = await stated_queries.resolve_part_of_pairs(
        _SingleAttemptClient(double_select), codes
    )
    async with ncit_sparql_client(isolated_qlever_url) as client:
        actual_pairs = await stated_queries.resolve_part_of_pairs(client, codes)

        async def counted_select(
            query: str,
            *,
            required_variables: Collection[str] = (),
        ) -> list[dict[str, str]]:
            nonlocal oversized_store_calls
            oversized_store_calls += 1
            return await client.select_once(
                query, required_variables=required_variables
            )

        with pytest.raises(ValueError, match=r"expanded-code.*256"):
            await stated_queries.resolve_part_of_pairs(
                _SingleAttemptClient(counted_select),
                (f"C{i}" for i in range(257)),
            )
        health = await client.select(
            f"SELECT ?s WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }} LIMIT 1"
        )

    expected = [
        PartOfPair(part="C20003", whole="C99206"),
        PartOfPair(part="C20003", whole="C99207"),
        PartOfPair(part="C99203", whole="C99205"),
        PartOfPair(part="C99206", whole="C99207"),
        PartOfPair(part="C99210", whole="C99211"),
        PartOfPair(part="C99211", whole="C99210"),
        PartOfPair(part="C99220", whole="C99223"),
        PartOfPair(part="C99230", whole="C99231"),
    ]
    assert actual_pairs == double_pairs == expected
    assert len(double_requests) > 1
    assert oversized_store_calls == 0
    assert health
    assert health[0].get("s")


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_part_of_closure_rejects_row_sentinel_and_malformed_target(
    isolated_qlever_url: str,
) -> None:
    fanout = "\n".join(
        f"<{NCIT_NS}C99300> <{RDFS_NS}subClassOf> [ "
        f"a <{OWL_NS}Restriction> ; <{OWL_NS}onProperty> <{NCIT_NS}R82> ; "
        f"<{OWL_NS}someValuesFrom> <{NCIT_NS}C{99400 + index}> ] ."
        for index in range(257)
    )
    malformed = (
        f"<{NCIT_NS}C99301> <{RDFS_NS}subClassOf> [ "
        f"a <{OWL_NS}Restriction> ; <{OWL_NS}onProperty> <{NCIT_NS}R82> ; "
        f'<{OWL_NS}someValuesFrom> "not an IRI" ] .\n'
        f"<{NCIT_NS}C99302> <{RDFS_NS}subClassOf> [ "
        f"a <{OWL_NS}Restriction> ; <{OWL_NS}onProperty> <{NCIT_NS}R82> ; "
        f'<{OWL_NS}someValuesFrom> "{NCIT_NS}C99303" ] .\n'
        f'<{NCIT_NS}C99304> <{RDFS_NS}subClassOf> "{NCIT_NS}C99305" .'
    )

    async with ncit_sparql_client(isolated_qlever_url) as client:
        await client.load(
            f"{fanout}\n{malformed}".encode(),
            content_type="text/turtle",
            graph_iri=STATED_GRAPH_IRI,
            replace=False,
        )
        with pytest.raises(ValueError, match=r"row.*256"):
            await stated_queries.resolve_part_of_pairs(client, ["C99300"])
        with pytest.raises(ValueError, match="target is not an IRI"):
            await stated_queries.resolve_part_of_pairs(client, ["C99301"])
        with pytest.raises(ValueError, match="target is not an IRI"):
            await stated_queries.resolve_part_of_pairs(client, ["C99302"])
        with pytest.raises(ValueError, match="target is not an IRI"):
            await stated_queries.resolve_part_of_pairs(client, ["C99304"])
        health = await client.select(
            f"SELECT ?s WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }} LIMIT 1"
        )

    assert health
    assert health[0].get("s")


@pytest.mark.integration
@pytest.mark.full_store
async def test_part_of_pairs_query_matches_full_store_and_stays_healthy() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with ncit_sparql_client(url) as client:
        version_rows = await client.select(
            f"SELECT ?version WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            f"?ontology a <{OWL_NS}Ontology> ; "
            f"<{OWL_NS}versionInfo> ?version . }} }}"
        )
        rows = await client.select(
            build_part_of_pairs_query(
                part_codes=["C32291"],
                whole_codes=["C12510"],
            )
        )
        no_match_rows = await client.select(
            build_part_of_pairs_query(
                part_codes=["C32291"],
                whole_codes=["C999999999"],
            )
        )
        health = await client.select(
            f"SELECT ?s WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }} LIMIT 1"
        )

    assert version_rows == [{"version": "26.07d"}]
    assert rows == [
        {
            "part": f"{NCIT_NS}C32291",
            "whole": f"{NCIT_NS}C12510",
        }
    ]
    assert no_match_rows == []
    assert health
    assert health[0].get("s")


@pytest.mark.integration
@pytest.mark.full_store
async def test_part_of_closure_matches_version_pinned_full_store() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    codes = ["C12400", "C13063", "C12418"]
    async with ncit_sparql_client(url) as client:
        version_rows = await client.select(
            f"SELECT ?version WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            f"?ontology a <{OWL_NS}Ontology> ; "
            f"<{OWL_NS}versionInfo> ?version . }} }}"
        )
        assert version_rows == [{"version": "26.07d"}]
        one_edge_rows = await client.select(
            build_part_of_pairs_query(part_codes=codes, whole_codes=codes),
            required_variables={"part", "whole"},
        )
        closure = await stated_queries.resolve_part_of_pairs(client, codes)
        health = await client.select(
            f"SELECT ?s WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ ?s ?p ?o }} }} LIMIT 1"
        )

    assert set(part_of_pairs_from_rows(one_edge_rows)) == {
        PartOfPair(part="C12400", whole="C13063"),
        PartOfPair(part="C13063", whole="C12418"),
    }
    assert closure == [
        PartOfPair(part="C12400", whole="C12418"),
        PartOfPair(part="C12400", whole="C13063"),
        PartOfPair(part="C13063", whole="C12418"),
    ]
    assert health
    assert health[0].get("s")


@pytest.mark.integration
@pytest.mark.full_store
async def test_in_scope_concepts_query_pages_over_the_live_stated_graph() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with ncit_sparql_client(url) as client:
        rows = await client.select(
            build_in_scope_concepts_query(["Neoplastic Process"], limit=5, offset=0)
        )
    codes = concepts_from_rows(rows)
    assert len(codes) <= 5
    assert all(c.startswith("C") for c in codes)


@pytest.mark.integration
@pytest.mark.full_store
async def test_c6135_genus_walk_finds_roles() -> None:
    """The genus-chain walker must find role restrictions for C6135 from the
    stated graph. Previously the flat ``rdfs:subClassOf`` query returned nothing
    for this defined class — the walker is the fix."""
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with ncit_sparql_client(url, query_timeout=180.0) as client:
        roles = await walk_genus_chain(client.select, "C6135", max_depth=6)

    # The walker should find at minimum these core roles from the genus chain:
    filler_codes = {r.filler_code for r in roles}
    assert "C27970" in filler_codes  # R88 — Stage III (from C6135 level)
    assert "C90530" in filler_codes  # R88 — AJCC v7 Stage (from C141041)
    assert "C12400" in filler_codes  # R101 — Thyroid Gland (from C4815)
    # Deep R101 fillers found via recursive genus walk:
    assert "C13063" in filler_codes  # R101 — Neck (from C6077)
    assert "C12418" in filler_codes  # R101 — Head and Neck (from C35850)

    role_pairs = {(role.role_code, role.filler_code) for role in roles}
    assert ("R88", "C27970") in role_pairs
    assert ("R101", "C12400") in role_pairs
    assert ("R103", "C33782") in role_pairs
    assert ("R108", "C47804") in role_pairs
    assert ("R108", "C47807") in role_pairs
    assert all(role_code not in {"R104", "R107"} for role_code, _ in role_pairs)


@pytest.mark.integration
@pytest.mark.full_store
async def test_2607d_lineage_partonomy_does_not_remove_classifiers() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    async with ncit_sparql_client(url) as client:
        assert await client.version() == "26.07d"
        closure = await stated_queries.resolve_part_of_pairs(
            client, ["C12704", "C12705"]
        )
    assert closure == [PartOfPair(part="C12704", whole="C12705")]

    constituents = select_constituents(
        [
            RoleRestriction("R101", "C12704", anchoring_genus="C3809"),
            RoleRestriction("R101", "C12705", anchoring_genus="C215715"),
        ],
        lambda _ancestor, _descendant: False,
        is_part_of=lambda part, whole: (part, whole) == ("C12704", "C12705"),
    )
    assert {item.filler_code for item in constituents} == {"C12704", "C12705"}
    assert all(item.group is None for item in constituents)


@pytest.mark.integration
@pytest.mark.full_store
async def test_2607d_only_r103_role_annotation_declares_non_defining() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    async with ncit_sparql_client(url) as client:
        assert await client.version() == "26.07d"
        rows = await client.select(
            f"SELECT ?role ?note WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            f"?role <{NCIT_NS}P98> ?note . "
            'FILTER(STRSTARTS(STR(?role), "http://ncicb.nci.nih.gov/xml/owl/EVS/'
            'Thesaurus.owl#R")) '
            'FILTER(CONTAINS(LCASE(STR(?note)), "non-defining role")) } }',
            required_variables={"role", "note"},
        )
    assert rows == [
        {
            "role": f"{NCIT_NS}R103",
            "note": (
                "This non-defining role represents non-essential characteristics "
                "which are true in some, but not all, cases, yet have an association "
                "frequent enough to be of interest."
            ),
        }
    ]


@pytest.mark.integration
@pytest.mark.full_store
@pytest.mark.parametrize("concept_code", ["C102870", "C27787"])
async def test_2607d_unsupported_r103_fact_is_conserved_but_not_projected(
    concept_code: str,
) -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")

    async def no_label_match(_surface_form: str) -> str | None:
        return None

    async with ncit_sparql_client(url, query_timeout=120.0) as client:
        assert await client.version() == "26.07d"
        result = await _decompose_one(
            concept_code,
            client,
            label=None,
            label_lookup=no_label_match,
            walker_max_depth=6,
        )

    decomposition = result.decomposition
    assert decomposition is not None
    assert decomposition.complete_definition is not None
    source_pairs = {
        (fact.role_code, fact.filler_code)
        for fact in decomposition.complete_definition.facts
        if isinstance(fact, RestrictionDefinitionFact)
    }
    projected_pairs = {
        (constituent.source_role, constituent.filler_code)
        for constituent in decomposition.constituents
    }
    assert ("R103", "C54105") in source_pairs
    assert ("R103", "C54105") not in projected_pairs


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_store
async def test_2607d_m1_complete_role_audit_remains_source_complete() -> None:
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    concept_codes = (
        "C27262",
        "C102870",
        "C162770",
        "C102883",
        "C115057",
        "C101539",
        "C132677",
        "C181564",
        "C186620",
        "C162226",
        "C206219",
        "C198031",
        "C100054",
        "C100051",
        "C6135",
        "C4791",
        "C35756",
        "C89995",
        "C27787",
        "C115118",
    )
    expected = Counter(
        {
            "R88": 32,
            "R100": 6,
            "R101": 57,
            "R102": 1,
            "R103": 28,
            "R104": 26,
            "R105": 73,
            "R106": 1,
            "R107": 2,
            "R108": 77,
            "R110": 0,
            "R126": 1,
        }
    )
    actual: Counter[str] = Counter()

    async with ncit_sparql_client(url, query_timeout=120.0) as client:
        assert await client.version() == "26.07d"
        for concept_code in concept_codes:
            complete, _roles = await stated_queries.read_complete_genus_chain(
                client.select, concept_code, max_depth=6
            )
            actual.update(
                fact.role_code
                for fact in complete.facts
                if isinstance(fact, RestrictionDefinitionFact)
                and fact.role_code in expected
            )

    assert actual == expected
    assert actual.total() == 304


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_store
async def test_c6135_walked_roles_route_d19_d20_with_semantic_type_of() -> None:
    """After the genus-chain walker, feeding roles through ``select_constituents``
    with ``semantic_type_of`` should apply D19/D20 axis routing.

    Marked @slow because it walks a depth-6 genus chain against the full stated
    build, which can take 30-60s on a cold store.
    """
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with ncit_sparql_client(url) as client:
        roles = await walk_genus_chain(client.select, "C6135", max_depth=6)

        filler_codes = {r.filler_code for r in roles}
        rows = await client.select(build_semantic_type_of_query(list(filler_codes)))
        semantic_type_of = semantic_type_of_from_rows(rows)

        def _st_of(code: str) -> str | None:
            types = semantic_type_of.get(code)
            return types[0] if types else None

        ancestor_pairs: set[AncestorPair] = set()
        if filler_codes:
            ancestor_rows = await client.select(
                build_ancestor_pairs_query(list(filler_codes))
            )
            ancestor_pairs = ancestor_pairs_from_rows(ancestor_rows)

        constituents = select_constituents(
            roles,
            make_is_ancestor(ancestor_pairs),
            parent_morphology=None,
            semantic_type_of=_st_of,
        )

    fillers = {c.filler_code for c in constituents}

    # R88 filler — Stage III
    assert "C27970" in fillers

    # R101 organ filler stays on R101
    assert "C12400" in fillers

    # Deep R101 fillers in "Body Location or Region" route to op:AssociatedRegion
    region_axes = {c.axis for c in constituents if c.filler_code == "C13063"}
    assert "op:AssociatedRegion" in region_axes
    region_axes_12418 = {c.axis for c in constituents if c.filler_code == "C12418"}
    assert "op:AssociatedRegion" in region_axes_12418

    # R101 fillers anchored on a lineage-generic genus (C3809 Neuroendocrine
    # Neoplasm, in C6135's chain) route to op:AssociatedLineageClassification
    # (D20 refinement 1). This exercises the walker's anchoring_genus population
    # end-to-end — the gap that silently disabled lineage routing before.
    lineage = {
        c.filler_code
        for c in constituents
        if c.axis == "op:AssociatedLineageClassification"
    }
    assert "C12704" in lineage  # Endocrine Gland, anchored on C3809


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_store
async def test_resolve_morphology_filler_for_c6135() -> None:
    """The morphology filler for C6135 should be C3879 (Thyroid Gland Medullary
    Carcinoma), not the staging genus C141041.

    Marked @slow for the same reason as test_c6135_walked_roles_route_d19_d20.
    """
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with ncit_sparql_client(url) as client:
        morphology = await resolve_morphology_filler(
            client.select, "C6135", max_depth=6
        )

    # C3879 is "Thyroid Gland Medullary Carcinoma" - the first non-staging genus
    assert morphology == "C3879"


@pytest.mark.integration
@pytest.mark.full_store
async def test_c6135_decomposition_includes_morphology_constituent() -> None:
    """When morphology is resolved, the decomposition should include an
    op:Morphology constituent with axis_source='parent'."""
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with ncit_sparql_client(url) as client:
        roles = await walk_genus_chain(client.select, "C6135", max_depth=6)
        morphology = await resolve_morphology_filler(
            client.select, "C6135", max_depth=6
        )

        filler_codes = {r.filler_code for r in roles}
        rows = await client.select(build_semantic_type_of_query(list(filler_codes)))
        semantic_type_of = semantic_type_of_from_rows(rows)

        def _st_of(code: str) -> str | None:
            types = semantic_type_of.get(code)
            return types[0] if types else None

        ancestor_pairs: set[AncestorPair] = set()
        if filler_codes:
            ancestor_rows = await client.select(
                build_ancestor_pairs_query(list(filler_codes))
            )
            ancestor_pairs = ancestor_pairs_from_rows(ancestor_rows)

        constituents = select_constituents(
            roles,
            make_is_ancestor(ancestor_pairs),
            parent_morphology=morphology,
            semantic_type_of=_st_of,
        )

    # The morphology constituent should be present
    morphology_constituents = [c for c in constituents if c.axis == "op:Morphology"]
    assert len(morphology_constituents) == 1
    assert morphology_constituents[0].filler_code == "C3879"
    assert morphology_constituents[0].axis_source == "parent"


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_store
async def test_c6135_organ_lookup_collapses_broader_associated_region() -> None:
    """Pin the D59 projection while preserving both stated region facts."""
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async def no_label_match(_surface_form: str) -> str | None:
        return None

    async with ncit_sparql_client(url, query_timeout=120.0) as client:
        stated_version = await client.select(
            f"SELECT ?version WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            f"?ontology a <{OWL_NS}Ontology> ; "
            f"<{OWL_NS}versionInfo> ?version . }} }}"
        )
        result = await _decompose_one(
            "C6135",
            client,
            label=None,
            label_lookup=no_label_match,
            walker_max_depth=6,
        )

    assert stated_version == [{"version": "26.07d"}]
    decomposition = result.decomposition
    assert decomposition is not None
    assert decomposition.complete_definition is not None
    by_axis = {
        axis: {
            constituent.filler_code
            for constituent in decomposition.constituents
            if constituent.axis == axis
        }
        for axis in ("op:PrimarySite", "op:AssociatedRegion")
    }
    assert by_axis["op:PrimarySite"] == {"C12400"}
    assert by_axis["op:AssociatedRegion"] == {"C13063"}
    regions = [
        constituent
        for constituent in decomposition.constituents
        if constituent.axis == "op:AssociatedRegion"
    ]
    assert all(
        constituent.group is None
        and constituent.source_role == "R101"
        and constituent.source_definition_ids
        and constituent.needs_review is False
        for constituent in regions
    )
    stated_site_facts = [
        fact
        for fact in decomposition.complete_definition.facts
        if isinstance(fact, RestrictionDefinitionFact)
        and fact.role_code == "R101"
        and fact.filler_code in {"C12400", "C12418", "C13063"}
    ]
    assert {fact.filler_code for fact in stated_site_facts} == {
        "C12400",
        "C12418",
        "C13063",
    }
    assert all(fact.anchor_code and fact.group_id for fact in stated_site_facts)


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_store
async def test_complete_record_matches_real_multi_parent_group_and_review_cases() -> (
    None
):
    """Pin the production shapes that #153 must preserve, not fixture assumptions."""
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async def no_label_match(_surface_form: str) -> str | None:
        return None

    async with ncit_sparql_client(url, query_timeout=120.0) as client:
        stated_version = await client.select(
            f"SELECT ?version WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            f"?ontology a <{OWL_NS}Ontology> ; "
            f"<{OWL_NS}versionInfo> ?version . }} }}"
        )
        assert stated_version == [{"version": "26.07d"}]
        multi_parent = await read_complete_definition(client.select, "C3879")
        grouped_result = await _decompose_one(
            "C136775",
            client,
            label=None,
            label_lookup=no_label_match,
            walker_max_depth=6,
        )
        review_result = await _decompose_one(
            "C27787",
            client,
            label=None,
            label_lookup=no_label_match,
            walker_max_depth=6,
        )

        c3879_genera = {
            fact.genus_code
            for fact in multi_parent.facts
            if isinstance(fact, GenusDefinitionFact) and fact.anchor_code == "C3879"
        }
        assert c3879_genera == {"C160980", "C4815"}

        grouped = grouped_result.decomposition
        assert grouped is not None
        assert grouped.complete_definition is not None
        grouped_regions = [
            constituent
            for constituent in grouped.constituents
            if constituent.axis == "op:AssociatedRegion"
            and constituent.filler_code in {"C12471", "C33209"}
        ]
        assert {constituent.filler_code for constituent in grouped_regions} == {
            "C12471",
            "C33209",
        }, grouped.constituents
        assert all(
            constituent.group == "op:AssociatedRegion"
            and constituent.source_definition_ids
            for constituent in grouped_regions
        )

        review = review_result.decomposition
        assert review is not None
        assert review.complete_definition is not None
        retained_cell_types = [
            constituent
            for constituent in review.constituents
            if constituent.axis == "op:CellType"
            and constituent.filler_code in {"C12917", "C36903"}
        ]
        assert {constituent.filler_code for constituent in retained_cell_types} == {
            "C36903"
        }, review.constituents
        assert all(
            not constituent.needs_review
            and constituent.source_role == "R105"
            and constituent.source_definition_ids
            for constituent in retained_cell_types
        )
        complete_cell_types = {
            fact.filler_code
            for fact in review.complete_definition.facts
            if isinstance(fact, RestrictionDefinitionFact)
            and fact.role_code == "R105"
            and fact.filler_code in {"C12917", "C36903"}
        }
        assert complete_cell_types == {"C12917", "C36903"}


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_store
async def test_ncit_role_metadata_contract_matches_normalization() -> None:
    """Pin the real role identities that distinguish Has from May_Have."""
    url = _url()
    if not _reachable(url):
        pytest.skip(f"NCIt QLever not reachable at {url}")
    if not _stated_loaded(url):
        pytest.skip("stated NCIt graph not loaded (run owl_load with include_stated)")

    async with ncit_sparql_client(url, query_timeout=120.0) as client:
        rows = await client.select(
            f"""
            SELECT ?role ?label WHERE {{
                GRAPH <{STATED_GRAPH_IRI}> {{
                    VALUES ?role {{
                        <{NCIT_NS}R104> <{NCIT_NS}R108>
                        <{NCIT_NS}R114> <{NCIT_NS}R115>
                    }}
                    ?role <{RDFS_NS}label> ?label .
                }}
            }}
            """
        )

    assert {(row["role"].removeprefix(NCIT_NS), row["label"]) for row in rows} == {
        ("R104", "Disease_Has_Normal_Cell_Origin"),
        ("R108", "Disease_Has_Finding"),
        ("R114", "Disease_May_Have_Cytogenetic_Abnormality"),
        ("R115", "Disease_May_Have_Finding"),
    }
