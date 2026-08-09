"""Async SPARQL transport client for an Oxigraph HTTP endpoint.

Focused on transport only — issuing SPARQL over HTTP and shaping results. Store
lifecycle (load/reload/health polling) and terminology semantics live elsewhere.
This separation is the main improvement over fairdata's single 800-LOC store base,
which fused transport, hierarchy queries, and Docker/ECS reload.

Design:
- one pooled ``httpx.AsyncClient``, created lazily and reused (connection reuse);
- ``select`` retries transport/timeout errors with backoff, while ``select_once``
  provides one transport attempt for callers with an external request budget; a
  non-2xx response is always a hard error (no retries on a 400 SPARQL syntax error);
- ``select``/``select_once`` return flattened ``{var: value}`` rows; ``select_raw``
  returns the full SPARQL-JSON for callers that need datatypes/languages.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Self

import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection
    from types import TracebackType
    from typing import BinaryIO

from ontolib.common.error_handling import retry_with_backoff
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger

logger = get_logger(__name__)

_SPARQL_JSON = "application/sparql-results+json"
_SPARQL_QUERY = "application/sparql-query"
_SPARQL_UPDATE = "application/sparql-update"
_SPARQL_BINDING_TYPES = frozenset({"uri", "bnode", "literal", "typed-literal"})
# Chunk size for streaming a file object to the store (keeps a multi-hundred-MB OWL
# from fully materializing in memory).
_LOAD_CHUNK_BYTES = 1 << 20


async def _aiter_file(handle: BinaryIO) -> AsyncIterator[bytes]:
    """Yield a binary file object in chunks as an async byte stream.

    httpx's ``AsyncClient`` rejects a plain (sync) file handle as request content, so a
    streamed upload must be wrapped in an async iterator.
    """
    while chunk := handle.read(_LOAD_CHUNK_BYTES):
        yield chunk


# A code safe to embed inside a ``<{ns}{code}>`` IRI. Anything that could close the
# IRI or inject SPARQL (``>`` ``{`` ``}`` whitespace) is rejected. Defence in depth
# at the string-building boundary even though upstream routes validate code shape.
_SAFE_CODE = re.compile(r"[A-Za-z0-9:_.\-]+")

_COUNT_ALL = "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }"

# Transport-level failures worth retrying (a closed socket, a dropped connection,
# a timeout). A returned HTTP error status is NOT here — it is deterministic.
_RETRYABLE = (httpx.TransportError, httpx.TimeoutException)


def safe_iri(code: str, namespace: str) -> str:
    """Return ``{namespace}{code}``, rejecting injection-unsafe codes.

    Raises:
        ValueError: if *code* is not drawn from ``[A-Za-z0-9:_.-]``.
    """
    if not _SAFE_CODE.fullmatch(code):
        raise ValueError(f"Unsafe concept code rejected: {code!r}")
    return f"{namespace}{code}"


def _binding_value(cell: object, row_index: int, variable: str) -> str:
    if not isinstance(cell, dict):
        raise StorageError(
            f"malformed SPARQL SELECT response: row {row_index} has invalid cell"
        )
    binding_type = cell.get("type")
    if not isinstance(binding_type, str) or binding_type not in _SPARQL_BINDING_TYPES:
        raise StorageError(
            "malformed SPARQL SELECT response: "
            f"row {row_index} variable {variable!r} has invalid binding type"
        )
    value = cell.get("value")
    if not isinstance(value, str):
        raise StorageError(
            "malformed SPARQL SELECT response: "
            f"row {row_index} variable {variable!r} has no string value"
        )
    return value


def _flatten_binding_row(
    row: object,
    row_index: int,
    variables: frozenset[str],
) -> dict[str, str]:
    if not isinstance(row, dict):
        raise StorageError(
            f"malformed SPARQL SELECT response: row {row_index} is not an object"
        )
    flattened: dict[str, str] = {}
    for variable, cell in row.items():
        if not isinstance(variable, str) or variable not in variables:
            raise StorageError(
                "malformed SPARQL SELECT response: "
                f"row {row_index} has undeclared variable {variable!r}"
            )
        flattened[variable] = _binding_value(cell, row_index, variable)
    return flattened


def _select_document(
    data: object,
) -> tuple[dict[object, object], frozenset[str]]:
    if not isinstance(data, dict):
        raise StorageError("malformed SPARQL SELECT response: root is not an object")
    if "boolean" in data:
        raise StorageError(
            "malformed SPARQL response: contains both SELECT and ASK result forms"
        )
    head = data.get("head")
    if not isinstance(head, dict):
        raise StorageError("malformed SPARQL SELECT response: missing head object")
    raw_variables = head.get("vars")
    if not isinstance(raw_variables, list) or not all(
        isinstance(variable, str) for variable in raw_variables
    ):
        raise StorageError("malformed SPARQL SELECT response: missing variable list")
    return data, frozenset(raw_variables)


def flatten_bindings(
    data: object,
    *,
    required_variables: Collection[str] = (),
) -> list[dict[str, str]]:
    """Flatten a SPARQL-JSON result into ``{var: value}`` rows.

    Only the ``value`` of each binding is kept (datatype/lang dropped). A variable
    absent from a given row is omitted from that row's dict, so callers can tell an
    unbound optional from an empty string.
    """
    document, variables = _select_document(data)
    missing_variables = set(required_variables) - variables
    if missing_variables:
        missing = ", ".join(sorted(missing_variables))
        raise StorageError(
            "malformed SPARQL SELECT response: "
            f"missing required projected variable(s): {missing}"
        )
    results = document.get("results")
    if not isinstance(results, dict):
        raise StorageError("malformed SPARQL SELECT response: missing results object")
    bindings = results.get("bindings")
    if not isinstance(bindings, list):
        raise StorageError("malformed SPARQL SELECT response: missing bindings array")
    return [
        _flatten_binding_row(row, index, variables)
        for index, row in enumerate(bindings)
    ]


def parse_ask_result(data: object) -> bool:
    """Return a SPARQL-JSON ASK result, rejecting malformed response envelopes."""
    if not isinstance(data, dict):
        raise StorageError("malformed SPARQL ASK response: missing boolean result")
    if "results" in data:
        raise StorageError(
            "malformed SPARQL response: contains both ASK and SELECT result forms"
        )
    if not isinstance(data.get("head"), dict):
        raise StorageError("malformed SPARQL ASK response: missing head object")
    result = data.get("boolean")
    if not isinstance(result, bool):
        raise StorageError("malformed SPARQL ASK response: missing boolean result")
    return result


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
        self._update_url = f"{self._endpoint_url}/update"
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

    async def _post_once(self, query: str) -> httpx.Response:
        return await self._get_client().post(
            self._query_url,
            content=query.encode("utf-8"),
            headers={"Content-Type": _SPARQL_QUERY, "Accept": _SPARQL_JSON},
        )

    @retry_with_backoff(retryable_exceptions=_RETRYABLE)
    async def _post(self, query: str) -> httpx.Response:
        return await self._post_once(query)

    async def load(
        self,
        data: bytes | BinaryIO,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None:
        """Bulk-load RDF into the store via the SPARQL Graph Store Protocol.

        *data* may be bytes or a binary file object — httpx streams a file object, so a
        multi-GB OWL never fully materializes in memory. The local reload path (no
        container/ECS restart): ``replace=True`` PUTs (replacing the target graph),
        ``replace=False`` POSTs (merging). Targets the default graph unless *graph_iri*
        is given (e.g. the decomposed named graph).

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
        # bytes go as-is; a file object is streamed via an async iterator (httpx's
        # AsyncClient rejects a sync file handle passed directly as content).
        content: bytes | AsyncIterator[bytes] = (
            data if isinstance(data, bytes) else _aiter_file(data)
        )
        try:
            response = await request(
                url, content=content, headers={"Content-Type": content_type}
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

    async def update(self, update: str) -> None:
        """Execute one SPARQL Update request without automatic replay.

        A transport failure can happen after the server commits, so retrying here
        could replay a non-idempotent update. Callers that need recovery must
        reconcile observable state before deciding whether to issue another request.

        Raises:
            StorageError: on a transport error or a non-2xx response.
        """
        try:
            response = await self._get_client().post(
                self._update_url,
                content=update.encode("utf-8"),
                headers={"Content-Type": _SPARQL_UPDATE},
            )
        except _RETRYABLE as exc:
            raise StorageError(
                f"SPARQL update transport error against {self._update_url}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not response.is_success:
            raise StorageError(
                f"SPARQL update failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )

    async def _select_raw(self, query: str, *, retry: bool) -> dict[str, Any]:
        post = self._post if retry else self._post_once
        try:
            response = await post(query)
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
            data = response.json()
        except ValueError as e:
            raise StorageError(f"SPARQL response was not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise StorageError("SPARQL response root was not an object")
        return data

    async def select_raw(self, query: str) -> dict[str, Any]:
        """Run a SELECT/ASK query and return the raw SPARQL-JSON document."""
        return await self._select_raw(query, retry=True)

    async def select(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]:
        """Run a SELECT query and return flattened ``{var: value}`` rows."""
        return flatten_bindings(
            await self.select_raw(query), required_variables=required_variables
        )

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]:
        """Run one SELECT transport attempt without retrying a failed request."""
        return flatten_bindings(
            await self._select_raw(query, retry=False),
            required_variables=required_variables,
        )

    async def ask(self, query: str) -> bool:
        """Run an ASK query and return its boolean result."""
        return parse_ask_result(await self.select_raw(query))

    async def ask_once(self, query: str) -> bool:
        """Run one ASK transport attempt without retrying a failed request.

        Candidate-store invariants must hold on the first attempt: a store that only
        answers after a retry has not demonstrated the property being certified.
        """
        return parse_ask_result(await self._select_raw(query, retry=False))

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
            return int(value)
        except ValueError as e:
            raise StorageError(f"COUNT value did not parse as int: {value!r}") from e

    async def version(self) -> str | None:
        """Return the store's unique ``owl:versionInfo``, or ``None`` if unset.

        Matches any ``owl:Ontology`` carrying a versionInfo (the NCIt store has one).
        Multiple distinct values are ambiguous and therefore raise ``StorageError``.
        """
        query = (
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
            "SELECT DISTINCT ?v WHERE { "
            "?ont a owl:Ontology ; owl:versionInfo ?v } LIMIT 2"
        )
        rows = await self.select(query, required_variables={"v"})
        if not rows:
            return None
        if len(rows) > 1:
            raise StorageError("VERSION query returned multiple distinct values")
        version = rows[0].get("v")
        if not version:
            raise StorageError("VERSION query returned no 'v' binding")
        return version
