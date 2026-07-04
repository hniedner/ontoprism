"""Async SPARQL transport client for an Oxigraph HTTP endpoint.

Focused on transport only — issuing SPARQL over HTTP and shaping results. Store
lifecycle (load/reload/health polling) and terminology semantics live elsewhere.
This separation is the main improvement over fairdata's single 800-LOC store base,
which fused transport, hierarchy queries, and Docker/ECS reload.

Design:
- one pooled ``httpx.AsyncClient``, created lazily and reused (connection reuse);
- transport/timeout errors are retried with backoff; a non-2xx response is a hard
  error (no wasteful retries on a 400 SPARQL syntax error);
- ``select`` returns flattened ``{var: value}`` rows; ``select_raw`` returns the
  full SPARQL-JSON for callers that need datatypes/languages.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Self

import httpx

if TYPE_CHECKING:
    from types import TracebackType

from fairlib.common.error_handling import retry_with_backoff
from fairlib.core.exceptions import StorageError
from fairlib.core.logging_config import get_logger

logger = get_logger(__name__)

_SPARQL_JSON = "application/sparql-results+json"
_SPARQL_QUERY = "application/sparql-query"

# A code safe to embed inside a ``<{ns}{code}>`` IRI. Anything that could close the
# IRI or inject SPARQL (``>`` ``{`` ``}`` whitespace) is rejected. Defence in depth
# at the string-building boundary even though upstream routes validate code shape.
_SAFE_CODE = re.compile(r"^[A-Za-z0-9:_.\-]+$")

_COUNT_ALL = "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }"

# Transport-level failures worth retrying (a closed socket, a dropped connection,
# a timeout). A returned HTTP error status is NOT here — it is deterministic.
_RETRYABLE = (httpx.TransportError, httpx.TimeoutException)


def safe_iri(code: str, namespace: str) -> str:
    """Return ``{namespace}{code}``, rejecting injection-unsafe codes.

    Raises:
        ValueError: if *code* is not drawn from ``[A-Za-z0-9:_.-]``.
    """
    if not _SAFE_CODE.match(code):
        raise ValueError(f"Unsafe concept code rejected: {code!r}")
    return f"{namespace}{code}"


def flatten_bindings(data: dict[str, Any]) -> list[dict[str, str | None]]:
    """Flatten a SPARQL-JSON result into ``{var: value}`` rows.

    Only the ``value`` of each binding is kept (datatype/lang dropped). A variable
    absent from a given row is omitted from that row's dict, so callers can tell an
    unbound optional from an empty string.
    """
    bindings = data.get("results", {}).get("bindings", [])
    return [{var: cell.get("value") for var, cell in row.items()} for row in bindings]


class OxigraphHttpClient:
    """Minimal async SPARQL client over an Oxigraph HTTP endpoint."""

    def __init__(
        self,
        endpoint_url: str,
        *,
        connect_timeout: float = 5.0,
        query_timeout: float = 30.0,
    ) -> None:
        """Create a client for *endpoint_url* (its ``/query`` path is derived)."""
        self._endpoint_url = endpoint_url.rstrip("/")
        self._query_url = f"{self._endpoint_url}/query"
        self._store_url = f"{self._endpoint_url}/store"
        self._timeout = httpx.Timeout(query_timeout, connect=connect_timeout)
        self._client: httpx.AsyncClient | None = None

    @property
    def endpoint_url(self) -> str:
        """The base endpoint URL (no trailing slash)."""
        return self._endpoint_url

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client and its connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry_with_backoff(retryable_exceptions=_RETRYABLE)
    async def _post(self, query: str) -> httpx.Response:
        return await self._get_client().post(
            self._query_url,
            content=query.encode("utf-8"),
            headers={"Content-Type": _SPARQL_QUERY, "Accept": _SPARQL_JSON},
        )

    async def load(
        self,
        data: bytes,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None:
        """Bulk-load RDF into the store via the SPARQL Graph Store Protocol.

        The local reload path (no container/ECS restart): ``replace=True`` PUTs
        (replacing the target graph), ``replace=False`` POSTs (merging). Targets the
        default graph unless *graph_iri* is given (e.g. the decomposed named graph).

        Raises:
            StorageError: on a transport error or a non-2xx response.
        """
        url = (
            f"{self._store_url}?graph={graph_iri}"
            if graph_iri
            else f"{self._store_url}?default"
        )
        client = self._get_client()
        request = client.put if replace else client.post
        try:
            response = await request(
                url, content=data, headers={"Content-Type": content_type}
            )
        except _RETRYABLE as e:
            raise StorageError(
                f"Store load transport error against {self._store_url}: "
                f"{type(e).__name__}: {e}"
            ) from e
        if response.status_code not in (
            HTTPStatus.OK,
            HTTPStatus.CREATED,
            HTTPStatus.NO_CONTENT,
        ):
            raise StorageError(
                f"Store load failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )

    async def select_raw(self, query: str) -> dict[str, Any]:
        """Run a SELECT/ASK query and return the raw SPARQL-JSON document."""
        try:
            response = await self._post(query)
        except _RETRYABLE as e:
            raise StorageError(
                f"SPARQL transport error against {self._query_url}: "
                f"{type(e).__name__}: {e}"
            ) from e
        if response.status_code != HTTPStatus.OK:
            raise StorageError(
                f"SPARQL query failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as e:
            raise StorageError(f"SPARQL response was not valid JSON: {e}") from e

    async def select(self, query: str) -> list[dict[str, str | None]]:
        """Run a SELECT query and return flattened ``{var: value}`` rows."""
        return flatten_bindings(await self.select_raw(query))

    async def ask(self, query: str) -> bool:
        """Run an ASK query and return its boolean result."""
        data = await self.select_raw(query)
        return bool(data.get("boolean", False))

    async def count(self, query: str = _COUNT_ALL) -> int:
        """Run a ``SELECT (COUNT(...) AS ?count)`` query and return the integer.

        Raises:
            StorageError: if the result has no ``count`` binding (query-shape bug,
                not an empty store) or it does not parse as an integer.
        """
        rows = await self.select(query)
        if not rows or "count" not in rows[0]:
            raise StorageError("COUNT query returned no 'count' binding")
        value = rows[0]["count"]
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError) as e:
            raise StorageError(f"COUNT value did not parse as int: {value!r}") from e

    async def version(self) -> str | None:
        """Return the store's ``owl:versionInfo``, or ``None`` if unset.

        Matches any ``owl:Ontology`` carrying a versionInfo (the NCIt store has one).
        """
        query = (
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
            "SELECT ?v WHERE { ?ont a owl:Ontology ; owl:versionInfo ?v } LIMIT 1"
        )
        rows = await self.select(query)
        return rows[0].get("v") if rows else None
