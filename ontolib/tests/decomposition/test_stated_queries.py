"""Unit tests for the stated-graph SPARQL builders (string shape + injection guard)."""

import pytest

from ontolib.decomposition.stated_queries import (
    build_ancestor_pairs_query,
    build_role_restrictions_query,
    build_semantic_type_query,
)
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI


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
