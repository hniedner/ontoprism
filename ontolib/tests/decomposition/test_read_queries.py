"""Unit tests for the decomposition read-query builder."""

import pytest

from ontolib.decomposition import vocab
from ontolib.decomposition.read_queries import build_decomposition_query


@pytest.mark.unit
def test_query_is_scoped_to_the_decomposed_graph() -> None:
    q = build_decomposition_query("C6135")
    assert f"GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}>" in q
    assert "Thesaurus.owl#C6135" in q


@pytest.mark.unit
def test_query_projects_status_and_constituent_fields() -> None:
    q = build_decomposition_query("C6135")
    for var in (
        "?status",
        "?decomposedOn",
        "?axis",
        "?filler",
        "?axisSource",
        "?mostSpecific",
    ):
        assert var in q


@pytest.mark.unit
def test_query_uses_the_op_predicates() -> None:
    q = build_decomposition_query("C6135")
    assert vocab.REPRESENTATION_STATUS in q
    assert vocab.HAS_CONSTITUENT in q
    assert vocab.FILLER in q


@pytest.mark.unit
def test_query_rejects_injection_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_decomposition_query("C6135> } INJECT {")
