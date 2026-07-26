"""Typed decomposition reads over the internal NCIt SPARQL transport."""

from collections.abc import Collection
from typing import Protocol

from ontolib.decomposition.read_queries import build_decomposition_query


class _SelectClient(Protocol):
    async def select(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]: ...


class DecompositionReader:
    """Expose a code-based read without giving API routers a raw query method."""

    def __init__(self, client: _SelectClient) -> None:
        self._client = client

    async def rows_for(self, concept_code: str) -> list[dict[str, str]]:
        """Return decomposition rows for one injection-safe NCIt code."""
        return await self._client.select(
            build_decomposition_query(concept_code),
            required_variables={
                "status",
                "decomposedOn",
                "axis",
                "filler",
                "axisSource",
                "mostSpecific",
            },
        )
