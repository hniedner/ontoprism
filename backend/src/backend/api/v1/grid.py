"""Shared closed query vocabulary for repository grids."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Literal, Never

from fastapi import HTTPException
from pydantic import BeforeValidator
from sqlalchemy.exc import SQLAlchemyError

from backend.repository_metadata import RepositoryUnhealthy
from backend.repository_registry import (
    REPOSITORY_MANIFEST_PATH,
    GridCapabilities,
    load_repository_registry,
)
from ontolib.common.grid import ProductPageSize
from ontolib.core.exceptions import StorageError
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


def _selection_values(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def present_text(**values: str | None) -> dict[str, str]:
    """Drop absent validated column-text query parameters."""
    return {key: value for key, value in values.items() if value is not None}


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
        selected: Mapping[str, str | list[str] | None],
    ) -> None:
        if sort not in self.capabilities.sorts[operation]:
            raise HTTPException(422, "Undeclared repository sort")
        if any(key not in self.capabilities.filters for key in text):
            raise HTTPException(422, "Undeclared repository text filter")
        for key, value in selected.items():
            self._validate_selection(key, value)

    def _validate_selection(self, key: str, value: str | list[str] | None) -> None:
        definition = self.capabilities.filters.get(key)
        if definition is None or definition.kind != "categorical":
            raise HTTPException(422, "Undeclared repository categorical filter")
        values = _selection_values(value)
        if not definition.source_domain and any(
            v not in definition.values for v in values
        ):
            raise HTTPException(422, "Undeclared repository filter value")

    async def ready(self) -> Ready:
        repository = await self.certify()
        if isinstance(repository, RepositoryUnhealthy):
            raise HTTPException(503, repository.model_dump(mode="json"))
        return repository

    async def read[Result](
        self,
        query: Callable[[Ready], Awaitable[Result]],
        *,
        detail: str | None = None,
        available: Callable[[Ready], Awaitable[bool]] | None = None,
    ) -> Result:
        repository = await self.ready()
        try:
            result = await self._execute(repository, query, available)
        except SQLAlchemyError as exc:
            logger.warning("%s repository read unavailable: %s", self.label, exc)
            raise HTTPException(
                503, f"{self.label} certified search index is unavailable."
            ) from exc
        except StorageError as exc:
            logger.warning("%s repository read failed: %s", self.label, exc)
            raise HTTPException(
                502,
                f"{self.label} repository returned an invalid or unavailable response.",
            ) from exc
        except LookupError as exc:
            self._raise_detail_error(detail, "Concept not found", exc)
        except ValueError as exc:
            self._raise_detail_error(detail, "Invalid code", exc)
        return self._require_detail(result, detail)

    async def _execute[Result](
        self,
        repository: Ready,
        query: Callable[[Ready], Awaitable[Result]],
        available: Callable[[Ready], Awaitable[bool]] | None,
    ) -> Result:
        if available is not None and not await available(repository):
            raise HTTPException(
                503, f"{self.label} certified search index is unavailable."
            )
        return await query(repository)

    @staticmethod
    def _raise_detail_error(
        detail: str | None, message: str, cause: Exception
    ) -> Never:
        if detail is None:
            raise cause
        raise HTTPException(404, f"{message}: {detail}") from cause

    @staticmethod
    def _require_detail[Result](result: Result, detail: str | None) -> Result:
        if detail is not None and result is None:
            raise HTTPException(404, f"Concept not found: {detail}")
        return result


def declared_grid[Ready](
    repository_id: str,
    certify: Callable[[], Awaitable[Ready | RepositoryUnhealthy]],
) -> GridService[Ready]:
    """Build one grid guard from the tracked capability declaration."""
    descriptor = next(
        entry
        for entry in load_repository_registry(REPOSITORY_MANIFEST_PATH)
        if entry.id == repository_id
    )
    if descriptor.capabilities is None:
        raise RuntimeError(f"{descriptor.label} grid capabilities are missing")
    return GridService(descriptor.label, descriptor.capabilities, certify)
