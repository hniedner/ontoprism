"""Repository-supplied expressions use the one text predicate renderer."""

import pytest
from pydantic import TypeAdapter, ValidationError

from ontolib.common.grid import (
    ColumnText,
    categorical_predicate,
    sql_text_filters,
    sqlite_grid_filters,
    text_predicate,
)


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


def test_categorical_predicate_supports_declared_scalar_and_array_columns() -> None:
    scalar, scalar_params = categorical_predicate(
        "status",
        ["legacy"],
        expression="representation_status",
        array_column=False,
        dialect="sql",
    )
    array, array_params = categorical_predicate(
        "semantic_type",
        ["Disease", "Neoplasm"],
        expression="semantic_types",
        array_column=True,
        dialect="sql",
    )

    assert scalar == (
        " AND representation_status = ANY(CAST(:status_selected AS text[]))"
    )
    assert scalar_params == {"status_selected": ["legacy"]}
    assert array == (" AND semantic_types && CAST(:semantic_type_selected AS text[])")
    assert array_params == {"semantic_type_selected": ["Disease", "Neoplasm"]}


def test_categorical_sparql_membership_escapes_values_and_empty_is_noop() -> None:
    predicate, params = categorical_predicate(
        "semantic_type",
        ['Disease "type"'],
        expression="?type",
        array_column=True,
        dialect="sparql",
    )

    assert predicate == 'FILTER(?type IN ("Disease \\"type\\""))'
    assert params == {}
    assert categorical_predicate(
        "semantic_type",
        [],
        expression="?type",
        array_column=True,
        dialect="sparql",
    ) == ("", {})


def test_sqlite_filters_bind_declared_categories_and_literal_text() -> None:
    predicate, params = sqlite_grid_filters(
        {"status": ["RELEASED", "DRAFT"]},
        {"name": "100%_\\"},
        categorical_expressions={"status": "cdes.status"},
        text_expressions={"name": "cdes.name"},
    )

    assert "100%_\\" not in predicate
    assert predicate.count("?") == 3
    assert params == ("RELEASED", "DRAFT", "100%_\\")
    with pytest.raises(ValueError, match="unsupported"):
        sqlite_grid_filters(
            {"unknown": ["x"]},
            {},
            categorical_expressions={"status": "cdes.status"},
            text_expressions={},
        )
