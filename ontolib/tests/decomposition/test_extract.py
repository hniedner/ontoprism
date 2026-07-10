"""Unit tests for the pure row-assembly helpers (SPARQL rows -> models)."""

import pytest

from ontolib.decomposition.extract import (
    ancestor_pairs_from_rows,
    concepts_from_rows,
    genus_walk_rows_to_roles_and_genuses,
    make_is_ancestor,
    part_of_pairs_from_rows,
    roles_from_rows,
    semantic_type_of_from_rows,
    semantic_types_from_rows,
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
        {
            "rel": _iri("R1"),
            "target": f"{NCIT_NS}",
        },  # empty code (IRI ends in #) -> skipped
    ]
    roles = roles_from_rows(rows)
    assert [(r.role_code, r.filler_code, r.role_label) for r in roles] == [
        ("R101", "C12400", None)
    ]


@pytest.mark.unit
def test_semantic_types_from_rows_returns_all_distinct_sorted() -> None:
    rows = [
        {"semanticType": "Neoplastic Process"},
        {"semanticType": "Gene or Genome"},
        {"semanticType": "Neoplastic Process"},  # duplicate collapsed
        {"semanticType": None},  # empty dropped
        {"semanticType": ""},
    ]
    assert semantic_types_from_rows(rows) == ["Gene or Genome", "Neoplastic Process"]


@pytest.mark.unit
def test_semantic_types_from_rows_empty() -> None:
    assert semantic_types_from_rows([]) == []


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


@pytest.mark.unit
def test_concepts_from_rows_extracts_codes_in_order() -> None:
    rows = [{"concept": _iri("C6135")}, {"concept": _iri("C4791")}]
    assert concepts_from_rows(rows) == ["C6135", "C4791"]


@pytest.mark.unit
def test_concepts_from_rows_skips_missing_and_empty() -> None:
    rows = [
        {"concept": _iri("C1")},
        {"concept": None},
        {},
        {"concept": _iri("")},  # empty code (IRI ends in #)
    ]
    assert concepts_from_rows(rows) == ["C1"]


@pytest.mark.unit
def test_concepts_from_rows_empty() -> None:
    assert concepts_from_rows([]) == []


_OWL_RESTRICTION = "http://www.w3.org/2002/07/owl#Restriction"


def _genus_row(code: str) -> dict[str, str | None]:
    return {"member": _iri(code)}


def _restriction_row(
    role: str, target: str, label: str | None = None
) -> dict[str, str | None]:
    row: dict[str, str | None] = {
        "member": "_:b",
        "type": _OWL_RESTRICTION,
        "role": _iri(role),
        "target": _iri(target),
    }
    if label is not None:
        row["roleLabel"] = label
    return row


@pytest.mark.unit
def test_genus_walk_rows_to_roles_and_genuses_classifies() -> None:
    rows = [
        _genus_row("C141041"),
        _restriction_row("R88", "C27970", "Disease_Is_Stage"),
    ]
    roles, genuses = genus_walk_rows_to_roles_and_genuses(rows)
    assert genuses == ["C141041"]
    assert len(roles) == 1
    assert roles[0].role_code == "R88"
    assert roles[0].filler_code == "C27970"
    assert roles[0].role_label == "Disease_Is_Stage"


@pytest.mark.unit
def test_genus_walk_rows_deduplicates_roles() -> None:
    rows = [
        _restriction_row("R88", "C27970"),
        _restriction_row("R88", "C27970"),  # duplicate
        _restriction_row("R88", "C90530"),
    ]
    roles, _ = genus_walk_rows_to_roles_and_genuses(rows)
    assert len(roles) == 2
    assert ("R88", "C27970") in [(r.role_code, r.filler_code) for r in roles]
    assert ("R88", "C90530") in [(r.role_code, r.filler_code) for r in roles]


@pytest.mark.unit
def test_genus_walk_rows_deduplicates_genuses() -> None:
    rows = [
        _genus_row("C141041"),
        _genus_row("C141041"),  # duplicate
        _genus_row("C3879"),
    ]
    _, genuses = genus_walk_rows_to_roles_and_genuses(rows)
    assert genuses == ["C141041", "C3879"]


@pytest.mark.unit
def test_genus_walk_skips_incomplete_rows() -> None:
    rows: list[dict[str, str | None]] = [
        {"member": "_:b", "type": _OWL_RESTRICTION},  # no role + target
        _restriction_row("R88", "C27970"),
    ]
    roles, genuses = genus_walk_rows_to_roles_and_genuses(rows)
    assert genuses == []
    assert len(roles) == 1


@pytest.mark.unit
def test_semantic_type_of_from_rows_parses() -> None:
    rows = [
        {"code": "C6135", "st": "Neoplastic Process"},
        {"code": "C6135", "st": "Disease or Syndrome"},
        {"code": "C12400", "st": "Body Part, Organ, or Organ Component"},
    ]
    result = semantic_type_of_from_rows(rows)
    assert result["C6135"] == ["Neoplastic Process", "Disease or Syndrome"]
    assert result["C12400"] == ["Body Part, Organ, or Organ Component"]


@pytest.mark.unit
def test_semantic_type_of_from_rows_empty() -> None:
    assert semantic_type_of_from_rows([]) == {}


@pytest.mark.unit
def test_semantic_type_of_from_rows_skips_empty_rows() -> None:
    rows: list[dict[str, str | None]] = [
        {"code": "C6135", "st": None},
        {"code": None, "st": "Neoplastic Process"},
        {},
    ]
    assert semantic_type_of_from_rows(rows) == {}


@pytest.mark.unit
def test_part_of_pairs_from_rows_parses() -> None:
    rows = [
        {"whole": _iri("C6135"), "part": _iri("C27970")},
        {"whole": _iri("C6135"), "part": _iri("C12400")},
    ]
    pairs = part_of_pairs_from_rows(rows)
    assert pairs == [(_iri("C6135"), _iri("C27970")), (_iri("C6135"), _iri("C12400"))]


@pytest.mark.unit
def test_part_of_pairs_from_rows_skips_incomplete() -> None:
    rows: list[dict[str, str | None]] = [
        {"whole": _iri("C6135")},  # missing part
        {"part": _iri("C27970")},  # missing whole
        {"whole": None, "part": _iri("C12400")},
    ]
    assert part_of_pairs_from_rows(rows) == []


@pytest.mark.unit
def test_part_of_pairs_from_rows_empty() -> None:
    assert part_of_pairs_from_rows([]) == []
