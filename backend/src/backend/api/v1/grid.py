"""Shared closed query vocabulary for repository grids."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BeforeValidator
from sqlalchemy.exc import SQLAlchemyError

from backend.repository_metadata import RepositoryUnhealthy
from backend.repository_registry import GridCapabilities
from ontolib.common.grid import ProductPageSize
from ontolib.core.logging_config import get_logger

logger = get_logger(__name__)


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


class GridService[Ready]:
    """Certify and dispatch reads; adapters retain source queries and row types."""

    def __init__(
        self,
        label: str,
        capabilities: GridCapabilities,
        certify: Callable[[], Awaitable[Ready | RepositoryUnhealthy]],
    ) -> None:
        self.label, self.capabilities, self.certify = label, capabilities, certify

    def validate(
        self,
        operation: Literal["list", "search"],
        sort: str,
        text: Mapping[str, str],
        selected: Mapping[str, str | None],
    ) -> None:
        if sort not in self.capabilities.sorts[operation]:
            raise HTTPException(422, "Undeclared repository sort")
        if any(key not in self.capabilities.filters for key in text):
            raise HTTPException(422, "Undeclared repository text filter")
        for key, value in selected.items():
            self._validate_selection(key, value)

    def _validate_selection(self, key: str, value: str | None) -> None:
        definition = self.capabilities.filters.get(key)
        if definition is None or definition.kind != "categorical":
            raise HTTPException(422, "Undeclared repository categorical filter")
        if value is not None and value not in definition.values:
            raise HTTPException(422, "Undeclared repository filter value")

    async def _ready(self) -> Ready:
        repository = await self.certify()
        if isinstance(repository, RepositoryUnhealthy):
            raise HTTPException(503, repository.model_dump(mode="json"))
        return repository

    @staticmethod
    def _require_detail(result: object, detail: str | None) -> None:
        if detail is not None and result is None:
            raise HTTPException(404, f"Concept not found: {detail}")

    async def read[Result](
        self,
        query: Callable[[Ready], Awaitable[Result]],
        *,
        detail: str | None = None,
        available: Callable[[Ready], Awaitable[bool]] | None = None,
    ) -> Result:
        repository = await self._ready()
        try:
            if available is not None and not await available(repository):
                raise HTTPException(
                    503, f"{self.label} certified search index is unavailable."
                )
            result = await query(repository)
        except SQLAlchemyError as exc:
            logger.warning("%s repository read unavailable: %s", self.label, exc)
            raise HTTPException(
                503, f"{self.label} certified search index is unavailable."
            ) from exc
        except ValueError as exc:
            if detail is None:
                raise
            raise HTTPException(404, f"Invalid code: {detail}") from exc
        self._require_detail(result, detail)
        return result
