"""Shared closed product vocabulary and source-safe repository text predicates."""

import json
from collections.abc import Mapping
from typing import Annotated, Literal, get_args

from pydantic import Field

ProductPageSize = Literal[10, 25, 50, 100]
PRODUCT_PAGE_SIZES = frozenset(get_args(ProductPageSize))

_TEXT_LIMIT = 100
ColumnText = Annotated[
    str, Field(min_length=1, max_length=_TEXT_LIMIT, pattern=r"^[^\x00-\x1f]+$")
]


def text_predicate(
    column: str,
    value: str,
    *,
    dialect: Literal["sql", "sparql"],
    expressions: Mapping[str, str],
) -> str:
    """Render a bounded case-insensitive substring predicate for a declared column."""
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
