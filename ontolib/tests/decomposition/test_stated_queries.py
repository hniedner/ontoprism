"""Unit tests for the stated-graph SPARQL builders (string shape + injection guard)."""

import pytest

from ontolib.decomposition.stated_queries import (
    _intersection_hop_pattern,
    build_ancestor_pairs_query,
    build_genus_walk_members_query,
    build_in_scope_concepts_query,
    build_part_of_pairs_query,
    build_role_restrictions_query,
    build_semantic_type_of_query,
    build_semantic_type_query,
    resolve_starting_genus,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


@pytest.mark.unit
def test_role_query_is_scoped_to_the_stated_graph() -> None:
    q = build_role_restrictions_query("C6135")
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    # The restriction-traversal pattern (roles are OWL someValuesFrom, not triples).
    assert "owl:onProperty" in q
    assert "owl:someValuesFrom" in q
    # The concept IRI is interpolated safely.
    assert "Thesaurus.owl#C6135" in q


@pytest.mark.unit
def test_role_query_projects_role_label_and_target() -> None:
    q = build_role_restrictions_query("C6135")
    assert "?rel" in q
    assert "?target" in q
    assert "?relLabel" in q


@pytest.mark.unit
def test_semantic_type_query_uses_p106_in_the_stated_graph() -> None:
    q = build_semantic_type_query("C6135")
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    assert "P106" in q
    assert "Thesaurus.owl#C6135" in q


@pytest.mark.unit
def test_ancestor_pairs_query_binds_the_code_set_and_uses_a_transitive_path() -> None:
    q = build_ancestor_pairs_query(["C12400", "C12401"])
    assert "rdfs:subClassOf+" in q  # transitive closure over the stated hierarchy
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    # Both endpoints are restricted to the supplied set via VALUES.
    assert "Thesaurus.owl#C12400" in q
    assert "Thesaurus.owl#C12401" in q


@pytest.mark.unit
def test_ancestor_pairs_query_empty_set_is_valid_and_matches_nothing() -> None:
    q = build_ancestor_pairs_query([])
    assert "VALUES" in q  # an empty VALUES block is valid SPARQL (zero rows)


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder",
    [build_role_restrictions_query, build_semantic_type_query],
)
def test_builders_reject_injection_unsafe_codes(builder) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        builder("C6135> } INJECT {")


@pytest.mark.unit
def test_ancestor_pairs_query_rejects_unsafe_codes() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_ancestor_pairs_query(["C123", "bad code"])


@pytest.mark.unit
def test_in_scope_concepts_query_scoped_to_stated_graph() -> None:
    q = build_in_scope_concepts_query(["Neoplastic Process"])
    assert f"GRAPH <{STATED_GRAPH_IRI}>" in q
    assert "P106" in q
    assert "Neoplastic Process" in q


@pytest.mark.unit
def test_in_scope_concepts_query_binds_multiple_semantic_types() -> None:
    q = build_in_scope_concepts_query(["Neoplastic Process", "Disease or Syndrome"])
    assert "Neoplastic Process" in q
    assert "Disease or Syndrome" in q


@pytest.mark.unit
def test_in_scope_concepts_query_projects_code_and_paginates() -> None:
    q = build_in_scope_concepts_query(["Neoplastic Process"], limit=100, offset=200)
    assert "?concept" in q
    assert "LIMIT 100" in q
    assert "OFFSET 200" in q
    assert "ORDER BY" in q  # deterministic paging


@pytest.mark.unit
def test_in_scope_concepts_query_rejects_injection_unsafe_semantic_type() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_in_scope_concepts_query(['Neoplastic Process" ; DROP {} #'])


@pytest.mark.unit
def test_intersection_hop_pattern_zero() -> None:
    uri = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C6135"
    assert "rdf:first ?member" in _intersection_hop_pattern(uri, 0)
    assert "owl:equivalentClass ?ec" in _intersection_hop_pattern(uri, 0)


@pytest.mark.unit
def test_intersection_hop_pattern_one() -> None:
    uri = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C6135"
    p = _intersection_hop_pattern(uri, 1)
    assert "rdf:first ?member" in p
    assert "rdf:rest" in p
    assert "?mid0" in p


@pytest.mark.unit
def test_intersection_hop_pattern_two() -> None:
    uri = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C6135"
    p = _intersection_hop_pattern(uri, 2)
    assert "rdf:first ?member" in p
    assert "?mid0" in p
    assert "?mid1" in p


@pytest.mark.unit
def test_build_genus_walk_members_query_shape() -> None:
    queries = build_genus_walk_members_query("C6135")
    assert isinstance(queries, list)
    assert len(queries) >= 3
    for q in queries:
        assert "owl:equivalentClass" in q
        assert "owl:intersectionOf" in q
        assert "?member" in q
        assert "?role" in q
        assert "?target" in q
        assert "?roleLabel" in q
        assert f"GRAPH <{STATED_GRAPH_IRI}>" in q


@pytest.mark.unit
def test_build_genus_walk_members_query_rejects_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_genus_walk_members_query("C6135 > INJECT {")


@pytest.mark.unit
def test_semantic_type_of_query_projects_code_and_type() -> None:
    q = build_semantic_type_of_query(["C6135", "C12400"])
    assert "?code" in q
    assert "?st" in q
    assert "VALUES ?concept" in q
    assert "Thesaurus.owl#C6135" in q
    assert "Thesaurus.owl#C12400" in q
    assert "P106" in q


@pytest.mark.unit
def test_semantic_type_of_query_empty_list_returns_valid_query() -> None:
    q = build_semantic_type_of_query([])
    assert "BIND" in q


@pytest.mark.unit
def test_part_of_pairs_query_shape() -> None:
    q = build_part_of_pairs_query(["C6135", "C27970"])
    assert "R82" in q
    assert "rdfs:subClassOf*" in q
    assert "?whole" in q
    assert "?part" in q
    assert "Thesaurus.owl#C6135" in q
    assert "Thesaurus.owl#C27970" in q


@pytest.mark.unit
def test_part_of_pairs_query_empty_list_returns_valid_query() -> None:
    q = build_part_of_pairs_query([])
    assert "BIND" in q


@pytest.mark.unit
def test_part_of_pairs_query_rejects_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_part_of_pairs_query(["bad code"])


@pytest.mark.unit
async def test_resolve_starting_genus_returns_genus_from_hop_0() -> None:
    async def fake_select(query: str) -> list[dict[str, str | None]]:
        assert "C6135" in query
        return [
            {"member": _iri("C141041"), "type": None},
            {
                "member": _iri("C141041"),
                "type": OWL_NS + "Restriction",
                "role": _iri("R88"),
                "target": _iri("C27970"),
            },
        ]

    genus = await resolve_starting_genus(fake_select, "C6135")
    assert genus == "C141041"


@pytest.mark.unit
async def test_resolve_starting_genus_returns_none_when_no_rows() -> None:
    async def fake_select(query: str) -> list[dict[str, str | None]]:
        return []

    genus = await resolve_starting_genus(fake_select, "C6135")
    assert genus is None


@pytest.mark.unit
async def test_resolve_starting_genus_returns_none_when_all_are_restrictions() -> None:
    async def fake_select(query: str) -> list[dict[str, str | None]]:
        return [
            {
                "member": _iri("C141041"),
                "type": OWL_NS + "Restriction",
                "role": _iri("R88"),
                "target": _iri("C27970"),
            },
        ]

    genus = await resolve_starting_genus(fake_select, "C6135")
    assert genus is None


@pytest.mark.unit
async def test_resolve_starting_genus_handles_non_ncit_iri() -> None:
    """Fallback path: genus IRI that does not start with NCIT_NS is returned
    as-is (defensive — all NCIt genuses are in NCIT_NS)."""

    async def fake_select(query: str) -> list[dict[str, str | None]]:
        return [{"member": "http://example.org/foo#C1", "type": None}]

    genus = await resolve_starting_genus(fake_select, "C6135")
    assert genus == "http://example.org/foo#C1"
