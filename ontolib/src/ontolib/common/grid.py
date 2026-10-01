"""Shared closed product vocabulary and source-safe repository text predicates."""

import json
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, get_args

from pydantic import StringConstraints

ProductPageSize = Literal[10, 25, 50, 100]
PRODUCT_PAGE_SIZES = frozenset(get_args(ProductPageSize))

_TEXT_LIMIT = 100
ColumnText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=_TEXT_LIMIT,
        pattern=r"^[^\x00-\x1f]+$",
    ),
]


def text_predicate(
    column: str,
    value: str,
    *,
    dialect: Literal["sql", "sparql"],
    expressions: Mapping[str, str],
) -> str:
    """Render a substring predicate using trusted repository column expressions.

    Callers validate values with ColumnText. SQL returns an AND conjunct for an
    existing WHERE clause; bind text_param(value) as <column>_text separately.
    """
    if column not in expressions:
        raise ValueError("unsupported grid column")
    if dialect == "sql":
        return (
            f" AND COALESCE({expressions[column]}, '') ILIKE :{column}_text ESCAPE '\\'"
        )
    literal = json.dumps(value, ensure_ascii=True)
    return f"FILTER(CONTAINS(LCASE({expressions[column]}), LCASE({literal})))"


def text_param(value: str) -> str:
    """Escape SQL LIKE wildcards so text means a literal substring."""
    return f"%{value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%"


def sql_text_filters(
    values: Mapping[str, str], expressions: Mapping[str, str]
) -> tuple[str, dict[str, str]]:
    """Return SQL conjuncts together with their literal-substring bindings."""
    return (
        "".join(
            text_predicate(column, value, dialect="sql", expressions=expressions)
            for column, value in values.items()
        ),
        {f"{column}_text": text_param(value) for column, value in values.items()},
    )


def sparql_text_filters(
    values: Mapping[str, str], expressions: Mapping[str, str]
) -> str:
    """Render validated literal-substring predicates for a SPARQL query."""
    return "\n".join(
        text_predicate(column, value, dialect="sparql", expressions=expressions)
        for column, value in values.items()
    )


def sqlite_grid_filters(
    selected: Mapping[str, Sequence[str]],
    text: Mapping[str, str],
    *,
    categorical_expressions: Mapping[str, str],
    text_expressions: Mapping[str, str],
) -> tuple[str, tuple[str, ...]]:
    """Return SQLite predicates and positional bindings for declared grid fields.

    SQLite ``lower`` is ASCII-only, so non-ASCII source text retains case sensitivity.
    """
    unsupported = set(selected) - categorical_expressions.keys()
    unsupported |= set(text) - text_expressions.keys()
    if unsupported:
        raise ValueError("unsupported grid column")
    clauses: list[str] = []
    params: list[str] = []
    for column, values in selected.items():
        if values:
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"{categorical_expressions[column]} IN ({placeholders})")
            params.extend(values)
    for column, value in text.items():
        clauses.append(
            f"instr(lower(COALESCE({text_expressions[column]}, '')), lower(?)) > 0"
        )
        params.append(value)
    return "".join(f" AND {clause}" for clause in clauses), tuple(params)


def categorical_predicate(
    column: str,
    selected: Sequence[str],
    *,
    expression: str,
    array_column: bool,
    dialect: Literal["sql", "sparql"],
) -> tuple[str, dict[str, list[str]]]:
    """Any-of membership over an expression evaluated for each source row.

    SQL array columns use overlap membership; scalar columns use scalar membership.
    Column and expression are trusted repository declarations, never user-supplied query
    text. SPARQL membership is independent of the source expression's storage shape.
    """
    if not selected:
        return "", {}
    if dialect == "sql":
        parameter = f"{column}_selected"
        values = f"CAST(:{parameter} AS text[])"
        predicate = (
            f"{expression} && {values}"
            if array_column
            else f"{expression} = ANY({values})"
        )
        return f" AND {predicate}", {parameter: list(selected)}
    values = ", ".join(json.dumps(value, ensure_ascii=True) for value in selected)
    return f"FILTER({expression} IN ({values}))", {}
