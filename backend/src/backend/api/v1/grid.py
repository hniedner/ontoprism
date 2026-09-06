"""Shared closed query vocabulary for repository grids."""

from typing import Annotated

from pydantic import BeforeValidator

from ontolib.common.grid import ProductPageSize


def _parse_page_size(value: object) -> object:
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    return value


PageSize = Annotated[
    ProductPageSize,
    BeforeValidator(_parse_page_size),
]
