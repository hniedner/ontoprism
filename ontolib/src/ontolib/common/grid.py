"""Shared closed product vocabulary and source-safe repository text predicates."""

import json
from typing import Annotated, Literal, get_args

from pydantic import Field

ProductPageSize = Literal[10, 25, 50, 100]
PRODUCT_PAGE_SIZES = frozenset(get_args(ProductPageSize))

_TEXT_LIMIT = 100
_CONTROL_LIMIT = 32
_COLUMNS = {
    "code": {"sql": "code", "sparql": "STRAFTER(STR(?concept), '#')"},
    "label": {"sql": "label", "sparql": "STR(?label)"},
    "representation_status": {
        "sql": "representation_status",
        "sparql": "STR(?representationStatusValue)",
    },
}
ColumnText = Annotated[
    str, Field(min_length=1, max_length=_TEXT_LIMIT, pattern=r"^[^\x00-\x1f]+$")
]


def valid_column_text(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= _TEXT_LIMIT
        and all(ord(char) >= _CONTROL_LIMIT for char in value)
    )


def text_predicate(
    column: str, value: str | None, *, dialect: Literal["sql", "sparql"]
) -> str:
    """Render a bounded case-insensitive substring predicate for a declared column."""
    if column not in _COLUMNS:
        raise ValueError("unsupported grid column")
    if value is None:
        return ""
    if not valid_column_text(value):
        raise ValueError("invalid column text")
    if dialect == "sql":
        return (
            f" AND COALESCE({_COLUMNS[column]['sql']}, '') "
            f"ILIKE :{column}_text ESCAPE '\\'"
        )
    literal = json.dumps(value, ensure_ascii=True)
    return f"FILTER(CONTAINS(LCASE({_COLUMNS[column]['sparql']}), LCASE({literal})))"


def text_param(value: str) -> str:
    """Escape SQL LIKE wildcards so text means a literal substring."""
    return f"%{value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%"
