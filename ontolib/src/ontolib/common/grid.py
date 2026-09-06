"""Shared closed product vocabulary for repository grid pagination."""

from typing import Literal, get_args

ProductPageSize = Literal[10, 25, 50, 100]
PRODUCT_PAGE_SIZES = frozenset(get_args(ProductPageSize))
