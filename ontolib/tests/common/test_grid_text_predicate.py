"""Repository-supplied expressions use the one text predicate renderer."""

import pytest
from pydantic import TypeAdapter, ValidationError

from ontolib.common.grid import ColumnText, sql_text_filters, text_predicate


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


def test_sql_filters_bind_literal_wildcards_and_backslashes() -> None:
    predicate, params = sql_text_filters({"label": "100%_\\"}, {"label": "name"})
    assert ":label_text" in predicate
    assert params == {"label_text": "%100\\%\\_\\\\%"}
    with pytest.raises(ValueError, match="unsupported"):
        sql_text_filters({"unknown": "text"}, {"label": "name"})


def test_column_text_normalizes_surrounding_spaces_and_rejects_blank() -> None:
    adapter = TypeAdapter(ColumnText)
    assert adapter.validate_python(" melanoma ") == "melanoma"
    with pytest.raises(ValidationError):
        adapter.validate_python("   ")
