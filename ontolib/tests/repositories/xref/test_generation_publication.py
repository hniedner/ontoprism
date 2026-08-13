from __future__ import annotations

import pytest

from ontolib.repositories.xref.publication import (
    XrefPublicationError,
    active_graph_iri,
    generation_graph_iri,
    rdf_active_generation,
)


@pytest.mark.unit
def test_generation_graph_is_source_specific_and_injection_safe() -> None:
    uberon = generation_graph_iri("uberon publisher", "a" * 64)
    icdo = generation_graph_iri("icdo/p334", "a" * 64)

    assert uberon != icdo
    assert "uberon-publisher" in uberon
    assert "icdo-p334" in icdo
    assert " " not in uberon
    assert "/p334" not in icdo


@pytest.mark.unit
@pytest.mark.parametrize("generation_id", ["", "abc", "g" * 64])
def test_generation_graph_refuses_invalid_generation_identity(
    generation_id: str,
) -> None:
    with pytest.raises(ValueError, match="generation"):
        generation_graph_iri("uberon", generation_id)


@pytest.mark.unit
@pytest.mark.parametrize(
    "rows",
    [
        [{"source": "https://example.test/wrong", "predicate": "p", "g": "g"}],
        [{"source": "s", "predicate": "https://example.test/wrong", "g": "g"}],
        [{"source": "s", "predicate": "p", "g": "g", "extra": "value"}],
        [
            {"source": "s", "predicate": "p", "g": "g"},
            {"source": "s", "predicate": "p", "g": "g"},
        ],
    ],
)
async def test_rdf_active_generation_rejects_every_non_exact_pointer_row(
    rows: list[dict[str, str]],
) -> None:
    source = "strict-pointer"
    subject = active_graph_iri(source)
    predicate = (
        "http://ncicb.nci.nih.gov/xml/owl/EVS/"
        "Thesaurus-upstream-xref.owl/activeGeneration"
    )
    graph = generation_graph_iri(source, "a" * 64)
    replacements = {"s": subject, "p": predicate, "g": graph}
    observed = [
        {key: replacements.get(value, value) for key, value in row.items()}
        for row in rows
    ]

    class Client:
        async def select(self, query: str) -> list[dict[str, str]]:
            assert "SELECT ?source ?predicate ?g" in query
            return observed

    with pytest.raises(XrefPublicationError, match="pointer"):
        await rdf_active_generation(Client(), source)  # type: ignore[arg-type]


@pytest.mark.unit
async def test_rdf_active_generation_accepts_only_the_exact_pointer_statement() -> None:
    source = "strict-pointer"
    subject = active_graph_iri(source)
    predicate = (
        "http://ncicb.nci.nih.gov/xml/owl/EVS/"
        "Thesaurus-upstream-xref.owl/activeGeneration"
    )
    generation_id = "a" * 64

    class Client:
        async def select(self, _query: str) -> list[dict[str, str]]:
            return [
                {
                    "source": subject,
                    "predicate": predicate,
                    "g": generation_graph_iri(source, generation_id),
                }
            ]

    assert await rdf_active_generation(Client(), source) == generation_id  # type: ignore[arg-type]
