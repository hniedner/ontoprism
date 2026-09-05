"""Shared closed query vocabulary for repository grids."""

from typing import Annotated, Literal

from pydantic import BeforeValidator


def _parse_page_size(value: object) -> object:
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    return value


PageSize = Annotated[
    Literal[10, 25, 50, 100],
    BeforeValidator(_parse_page_size),
]
