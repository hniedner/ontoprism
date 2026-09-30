"""Shared closed product vocabulary and source-safe repository text predicates."""

import json
from collections.abc import Mapping
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
