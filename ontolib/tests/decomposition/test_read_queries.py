"""Unit tests for the decomposition read-query builder."""

import pytest

from ontolib.decomposition.read_queries import (
    build_compact_decomposition_query,
)


@pytest.mark.unit
def test_query_rejects_injection_unsafe_code() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nsafe"):
        build_compact_decomposition_query("C6135> } INJECT {")
