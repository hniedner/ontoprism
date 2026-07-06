"""Unit tests for the pure row-assembly helpers (SPARQL rows -> models)."""

import pytest

from ontolib.decomposition.extract import (
    ancestor_pairs_from_rows,
    make_is_ancestor,
    roles_from_rows,
    semantic_type_from_rows,
)
from ontolib.terminologies.namespaces import NCIT_NS


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


@pytest.mark.unit
def test_roles_from_rows_parses_codes_and_label() -> None:
    rows = [
        {
            "rel": _iri("R105"),
            "relLabel": "Disease_Has_Abnormal_Cell",
            "target": _iri("C36761"),
        }
    ]
    roles = roles_from_rows(rows)
    assert len(roles) == 1
    assert roles[0].role_code == "R105"
    assert roles[0].filler_code == "C36761"
    assert roles[0].role_label == "Disease_Has_Abnormal_Cell"


@pytest.mark.unit
def test_roles_from_rows_tolerates_missing_label_and_skips_incomplete_rows() -> None:
    rows = [
        {"rel": _iri("R101"), "target": _iri("C12400")},  # no label
        {"rel": _iri("R99")},  # no target -> skipped
    ]
    roles = roles_from_rows(rows)
    assert [(r.role_code, r.filler_code, r.role_label) for r in roles] == [
        ("R101", "C12400", None)
    ]


@pytest.mark.unit
def test_semantic_type_from_rows_returns_first_value_or_none() -> None:
    assert (
        semantic_type_from_rows([{"semanticType": "Neoplastic Process"}])
        == "Neoplastic Process"
    )
    assert semantic_type_from_rows([]) is None


@pytest.mark.unit
def test_semantic_type_from_rows_skips_empty_then_takes_the_first_real_value() -> None:
    rows = [
        {"semanticType": None},
        {"semanticType": ""},
        {"semanticType": "Disease or Syndrome"},
    ]
    assert semantic_type_from_rows(rows) == "Disease or Syndrome"


@pytest.mark.unit
def test_ancestor_pairs_from_rows_skips_incomplete_rows() -> None:
    rows = [
        {"ancestor": _iri("C12401"), "descendant": _iri("C12400")},
        {"ancestor": _iri("C12403")},  # missing descendant -> skipped
        {"descendant": _iri("C12400")},  # missing ancestor -> skipped
    ]
    assert ancestor_pairs_from_rows(rows) == {("C12401", "C12400")}


@pytest.mark.unit
def test_ancestor_pairs_and_predicate() -> None:
    rows = [
        {"ancestor": _iri("C12401"), "descendant": _iri("C12400")},
        {"ancestor": _iri("C12403"), "descendant": _iri("C12400")},
    ]
    pairs = ancestor_pairs_from_rows(rows)
    assert pairs == {("C12401", "C12400"), ("C12403", "C12400")}
    is_ancestor = make_is_ancestor(pairs)
    assert is_ancestor("C12401", "C12400")
    assert not is_ancestor("C12400", "C12401")
