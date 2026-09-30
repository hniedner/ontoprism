"""Repository-supplied expressions use the one text predicate renderer."""

from ontolib.common.grid import text_predicate


def test_second_repository_supplies_its_own_declared_column_expression() -> None:
    assert (
        text_predicate(
            "source", "Uberon", dialect="sql", expressions={"source": "source_name"}
        )
        == " AND COALESCE(source_name, '') ILIKE :source_text ESCAPE '\\'"
    )
    assert (
        text_predicate(
            "source",
            "Uberon",
            dialect="sparql",
            expressions={"source": "STR(?sourceName)"},
        )
        == 'FILTER(CONTAINS(LCASE(STR(?sourceName)), LCASE("Uberon")))'
    )
