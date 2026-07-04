"""Unit tests for the NCIt role/association SPARQL builders (pure string builders)."""

import pytest

from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.role_queries import (
    build_related_concepts_query,
    build_role_relationships_query,
)


@pytest.mark.unit
def test_role_query_embeds_concept_and_restriction_pattern() -> None:
    query = build_role_relationships_query("C3262", NCIT_NS)
    assert f"<{NCIT_NS}C3262>" in query
    assert "owl:onProperty ?rel" in query
    assert "owl:someValuesFrom ?target" in query
    assert "LIMIT 100" in query


@pytest.mark.unit
def test_related_query_covers_both_associations_and_roles() -> None:
    query = build_related_concepts_query("C3262", NCIT_NS)
    # Association arm: direct predicate, excluding subClassOf.
    assert "FILTER(?relation != rdfs:subClassOf)" in query
    # Role arm: restriction traversal.
    assert "owl:someValuesFrom ?target" in query
    assert "UNION" in query


@pytest.mark.unit
def test_limit_is_parameterizable() -> None:
    assert "LIMIT 25" in build_role_relationships_query("C3262", NCIT_NS, limit=25)


@pytest.mark.unit
@pytest.mark.parametrize("bad_code", ["C1> ?x ?y", "C1}", "C1 UNION", "C1<x>"])
def test_injection_codes_are_rejected(bad_code: str) -> None:
    with pytest.raises(ValueError, match="Unsafe concept code"):
        build_role_relationships_query(bad_code, NCIT_NS)
