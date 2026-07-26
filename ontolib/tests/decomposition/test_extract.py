"""Unit tests for the pure row-assembly helpers (SPARQL rows -> models)."""

import pytest

from ontolib.decomposition.extract import (
    AncestorPair,
    PartOfPair,
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
def test_roles_from_rows_tolerates_missing_label() -> None:
    rows = [
        {"rel": _iri("R101"), "target": _iri("C12400")},  # no label
    ]
    roles = roles_from_rows(rows)
    assert [(r.role_code, r.filler_code, r.role_label) for r in roles] == [
        ("R101", "C12400", None)
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "row",
    [
        {"rel": _iri("R99")},
        {"target": _iri("C12400")},
        {"rel": _iri("R1"), "target": f"{NCIT_NS}"},
    ],
)
def test_roles_from_rows_rejects_missing_required_binding(
    row: dict[str, str | None],
) -> None:
    with pytest.raises(ValueError, match="missing required"):
        roles_from_rows([row])


@pytest.mark.unit
def test_semantic_types_from_rows_returns_all_distinct_sorted() -> None:
    rows = [
        {"semanticType": "Neoplastic Process"},
        {"semanticType": "Gene or Genome"},
        {"semanticType": "Neoplastic Process"},  # duplicate collapsed
    ]
    assert semantic_types_from_rows(rows) == ["Gene or Genome", "Neoplastic Process"]


@pytest.mark.unit
def test_semantic_types_from_rows_empty() -> None:
    assert semantic_types_from_rows([]) == []


@pytest.mark.unit
@pytest.mark.parametrize("row", [{}, {"semanticType": None}, {"semanticType": ""}])
def test_semantic_types_from_rows_rejects_missing_required_binding(
    row: dict[str, str | None],
) -> None:
    with pytest.raises(ValueError, match="semanticType"):
        semantic_types_from_rows([row])


@pytest.mark.unit
@pytest.mark.parametrize(
    "row",
    [{"ancestor": _iri("C12403")}, {"descendant": _iri("C12400")}],
)
def test_ancestor_pairs_from_rows_rejects_missing_required_binding(
    row: dict[str, str | None],
) -> None:
    with pytest.raises(ValueError, match="missing required"):
        ancestor_pairs_from_rows([row])


@pytest.mark.unit
def test_ancestor_pairs_and_predicate() -> None:
    rows = [
        {"ancestor": _iri("C12401"), "descendant": _iri("C12400")},
        {"ancestor": _iri("C12403"), "descendant": _iri("C12400")},
    ]
    pairs = ancestor_pairs_from_rows(rows)
    assert pairs == {
        AncestorPair(ancestor="C12401", descendant="C12400"),
        AncestorPair(ancestor="C12403", descendant="C12400"),
    }
    is_ancestor = make_is_ancestor(pairs)
    assert is_ancestor("C12401", "C12400")
    assert not is_ancestor("C12400", "C12401")


@pytest.mark.unit
def test_concepts_from_rows_extracts_codes_in_order() -> None:
    rows = [{"concept": _iri("C6135")}, {"concept": _iri("C4791")}]
    assert concepts_from_rows(rows) == ["C6135", "C4791"]


@pytest.mark.unit
@pytest.mark.parametrize("row", [{}, {"concept": None}, {"concept": _iri("")}])
def test_concepts_from_rows_rejects_missing_required_binding(
    row: dict[str, str | None],
) -> None:
    with pytest.raises(ValueError, match="concept"):
        concepts_from_rows([row])


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
@pytest.mark.parametrize(
    ("member", "message"),
    [
        ("https://example.org/vocab#C1", "member is not an NCIt IRI"),
        (_iri("R82"), "member is not an NCIt concept code"),
        (_iri("Cfoo"), "member is not an NCIt concept code"),
    ],
)
def test_genus_walk_rows_rejects_non_ncit_concept(
    member: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        genus_walk_rows_to_roles_and_genuses([{"member": member, "type": None}])


@pytest.mark.unit
@pytest.mark.parametrize(
    "row",
    [
        {},
        {"member": "_:b", "type": _OWL_RESTRICTION},
        {"member": "_:b", "type": _OWL_RESTRICTION, "role": _iri("R88")},
    ],
)
def test_genus_walk_rejects_missing_required_binding(
    row: dict[str, str | None],
) -> None:
    with pytest.raises(ValueError, match="missing required"):
        genus_walk_rows_to_roles_and_genuses([row])


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
@pytest.mark.parametrize(
    "row",
    [
        {"code": "C6135", "st": None},
        {"code": None, "st": "Neoplastic Process"},
        {},
    ],
)
def test_semantic_type_of_from_rows_rejects_missing_required_binding(
    row: dict[str, str | None],
) -> None:
    with pytest.raises(ValueError, match="missing required"):
        semantic_type_of_from_rows([row])


@pytest.mark.unit
def test_part_of_pairs_from_rows_parses() -> None:
    rows = [
        {"part": _iri("C6135"), "whole": _iri("C27970")},
        {"part": _iri("C6135"), "whole": _iri("C12400")},
    ]
    pairs = part_of_pairs_from_rows(rows)
    assert pairs == [
        PartOfPair(part="C6135", whole="C27970"),
        PartOfPair(part="C6135", whole="C12400"),
    ]


@pytest.mark.unit
def test_part_of_pair_requires_directional_keywords() -> None:
    with pytest.raises(TypeError):
        PartOfPair("C6135", "C27970")  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize("binding", ["part", "whole"])
@pytest.mark.parametrize("bad_code", ["R82", "Cfoo", "C123x", "C\uff11\uff12\uff13"])
def test_part_of_pair_rejects_non_concept_code(
    binding: str,
    bad_code: str,
) -> None:
    values = {"part": "C6135", "whole": "C27970"}
    values[binding] = bad_code
    with pytest.raises(ValueError, match=f"{binding} is not an NCIt concept code"):
        PartOfPair(part=values["part"], whole=values["whole"])


@pytest.mark.unit
@pytest.mark.parametrize(
    "row",
    [
        {"part": _iri("C6135")},
        {"whole": _iri("C27970")},
        {"part": None, "whole": _iri("C27970")},
        {"part": _iri("C6135"), "whole": None},
    ],
)
def test_part_of_pairs_from_rows_rejects_incomplete(
    row: dict[str, str | None],
) -> None:
    with pytest.raises(ValueError, match="missing required part/whole binding"):
        part_of_pairs_from_rows([row])


@pytest.mark.unit
@pytest.mark.parametrize("binding", ["part", "whole"])
def test_part_of_pairs_from_rows_rejects_non_ncit_iri(binding: str) -> None:
    row: dict[str, str | None] = {
        "part": _iri("C6135"),
        "whole": _iri("C27970"),
    }
    row[binding] = "https://example.org/vocab#C1"
    with pytest.raises(ValueError, match=f"{binding} is not an NCIt IRI"):
        part_of_pairs_from_rows([row])


@pytest.mark.unit
@pytest.mark.parametrize("binding", ["part", "whole"])
def test_part_of_pairs_from_rows_rejects_empty_ncit_code(binding: str) -> None:
    row: dict[str, str | None] = {
        "part": _iri("C6135"),
        "whole": _iri("C27970"),
    }
    row[binding] = NCIT_NS
    with pytest.raises(ValueError, match="missing required part/whole binding"):
        part_of_pairs_from_rows([row])


@pytest.mark.unit
@pytest.mark.parametrize("binding", ["part", "whole"])
@pytest.mark.parametrize("bad_code", ["R82", "Cfoo", "C123x", "C\uff11\uff12\uff13"])
def test_part_of_pairs_from_rows_rejects_non_concept_code(
    binding: str,
    bad_code: str,
) -> None:
    row: dict[str, str | None] = {
        "part": _iri("C6135"),
        "whole": _iri("C27970"),
    }
    row[binding] = _iri(bad_code)
    with pytest.raises(ValueError, match=f"{binding} is not an NCIt concept code"):
        part_of_pairs_from_rows([row])


@pytest.mark.unit
def test_part_of_pairs_from_rows_empty() -> None:
    assert part_of_pairs_from_rows([]) == []
