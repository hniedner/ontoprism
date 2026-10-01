"""Shared closed query vocabulary for repository grids."""

import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import cache
from typing import Annotated, Literal, cast

from fastapi import HTTPException
from pydantic import BeforeValidator, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from backend.repository_metadata import RepositoryUnhealthy
from backend.repository_registry import (
    REPOSITORY_MANIFEST_PATH,
    GridCapabilities,
    GridFilter,
    load_repository_registry,
)
from ontolib.common.grid import ProductPageSize
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger

logger = get_logger(__name__)
_UNCERTIFIED = object()


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


def _selection_values(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


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
        source_domains: (
            Mapping[str, Callable[[Ready], Awaitable[Sequence[str]]]] | None
        ) = None,
    ) -> None:
        self.label, self.capabilities, self.certify = label, capabilities, certify
        self.source_domains = source_domains or {}
        self._certified: Ready | RepositoryUnhealthy | object = _UNCERTIFIED

    async def validate(
        self,
        operation: Literal["list", "search"],
        sort: str,
        text: Mapping[str, str],
        selected: Mapping[str, str | Sequence[str] | None],
    ) -> None:
        if sort not in self.capabilities.sorts[operation]:
            raise HTTPException(422, "Undeclared repository sort")
        if any(
            key not in self.capabilities.filters
            or self.capabilities.filters[key].text_parameter is None
            for key in text
        ):
            raise HTTPException(422, "Undeclared repository text filter")
        for key, value in selected.items():
            await self._validate_selection(key, value)

    async def _validate_selection(
        self, key: str, value: str | Sequence[str] | None
    ) -> None:
        definition = self.capabilities.filters.get(key)
        if definition is None or definition.kind != "categorical":
            raise HTTPException(422, "Undeclared repository categorical filter")
        values = _selection_values(value)
        if not definition.multiple and len(values) > 1:
            raise HTTPException(
                422, "Repository filter does not accept multiple values"
            )
        domain = await self._selection_domain(definition, bool(values))
        if any(v not in domain for v in values):
            raise HTTPException(422, "Undeclared repository filter value")

    async def _selection_domain(
        self, definition: GridFilter, selected: bool
    ) -> Mapping[str, object] | Sequence[str]:
        if not definition.source_domain or not selected:
            return definition.values
        resolver = self.source_domains.get(definition.source_domain)
        if resolver is None:
            raise RuntimeError(
                f"{self.label} source domain {definition.source_domain!r} is missing"
            )
        repository = await self.ready()
        return await self._guarded_read(lambda: resolver(repository))

    async def ready(self) -> Ready:
        if self._certified is _UNCERTIFIED:
            self._certified = await self.certify()
        repository = self._certified
        if isinstance(repository, RepositoryUnhealthy):
            raise HTTPException(503, repository.model_dump(mode="json"))
        return cast("Ready", repository)

    async def read[Result](
        self,
        query: Callable[[Ready], Awaitable[Result]],
        *,
        available: Callable[[Ready], Awaitable[bool]] | None = None,
    ) -> Result:
        repository = await self.ready()
        return await self._guarded_read(
            lambda: self._execute(repository, query, available)
        )

    async def read_sync[Result](self, query: Callable[[Ready], Result]) -> Result:
        """Certify, then run a blocking repository adapter in the worker pool."""
        repository = await self.ready()
        return await self._guarded_read(lambda: run_in_threadpool(query, repository))

    async def _guarded_read[Result](
        self, query: Callable[[], Awaitable[Result]]
    ) -> Result:
        try:
            result = await query()
        except (SQLAlchemyError, sqlite3.DatabaseError) as exc:
            logger.exception("%s repository read unavailable", self.label)
            raise HTTPException(
                503, f"{self.label} certified search index is unavailable."
            ) from exc
        except StorageError as exc:
            logger.exception("%s repository read failed", self.label)
            raise HTTPException(
                502,
                f"{self.label} repository returned an invalid or unavailable response.",
            ) from exc
        except ValidationError as exc:
            logger.exception("%s repository response is invalid", self.label)
            raise HTTPException(
                502,
                f"{self.label} repository returned an invalid or unavailable response.",
            ) from exc
        return result

    async def filter_domain(self, key: str) -> list[str]:
        """Return one declared categorical domain after certification."""
        definition = self.capabilities.filters.get(key)
        if definition is None or definition.kind != "categorical":
            raise HTTPException(422, "Undeclared repository categorical filter")
        return list(await self._selection_domain(definition, True))

    async def read_detail[Result](
        self,
        query: Callable[[Ready], Awaitable[Result | None]],
        *,
        detail: str,
        noun: str = "Concept",
    ) -> Result:
        result = await self.read(query)
        if result is None:
            raise HTTPException(404, f"{noun} not found: {detail}")
        return result

    async def read_detail_sync[Result](
        self,
        query: Callable[[Ready], Result | None],
        *,
        detail: str,
        noun: str = "Concept",
    ) -> Result:
        result = await self.read_sync(query)
        if result is None:
            raise HTTPException(404, f"{noun} not found: {detail}")
        return result

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


@cache
def _declared(repository_id: str):
    descriptor = next(
        (
            entry
            for entry in load_repository_registry(REPOSITORY_MANIFEST_PATH)
            if entry.id == repository_id
        ),
        None,
    )
    if descriptor is None:
        raise RuntimeError(f"Repository {repository_id!r} is not declared")
    return descriptor


def declared_grid[Ready](
    repository_id: str,
    certify: Callable[[], Awaitable[Ready | RepositoryUnhealthy]],
    source_domains: (
        Mapping[str, Callable[[Ready], Awaitable[Sequence[str]]]] | None
    ) = None,
    *,
    dataset: str | None = None,
) -> GridService[Ready]:
    """Build one grid guard from the tracked capability declaration."""
    descriptor = _declared(repository_id)
    capabilities = (
        (descriptor.capabilities_by_dataset or {}).get(dataset)
        if dataset is not None
        else descriptor.capabilities
    )
    if capabilities is None:
        raise RuntimeError(f"{descriptor.label} grid capabilities are missing")
    return GridService(descriptor.label, capabilities, certify, source_domains)
