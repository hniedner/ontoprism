"""Unit tests for the decomposition read-query builder."""

import pytest

from ontolib.decomposition import vocab
from ontolib.decomposition.read_queries import (
    build_compact_decomposition_query,
)


@pytest.mark.unit
def test_query_is_scoped_to_the_decomposed_graph() -> None:
    q = build_compact_decomposition_query("C6135")
    assert f"GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}>" in q
    assert "Thesaurus.owl#C6135" in q


@pytest.mark.unit
def test_query_projects_status_and_constituent_fields() -> None:
    q = build_compact_decomposition_query("C6135")
    for var in (
        "?subject",
        "?predicate",
        "?value",
    ):
        assert var in q
    assert "?group" not in q


@pytest.mark.unit
def test_query_uses_the_op_predicates() -> None:
    q = build_compact_decomposition_query("C6135")
    assert vocab.PUBLICATION_MARKER in q
    assert vocab.HAS_CONSTITUENT in q
    assert vocab.HAS_REVIEW_FLAG in q


@pytest.mark.unit
def test_query_rejects_injection_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_compact_decomposition_query("C6135> } INJECT {")
