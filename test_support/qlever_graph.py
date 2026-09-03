"""Fail-closed preservation of disposable QLever named graphs."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator


def qlever_update(url: str, statement: str) -> None:
    response = httpx.post(
        f"{url}/update",
        content=statement.encode(),
        headers={"Content-Type": "application/sparql-update"},
        timeout=30,
    )
    response.raise_for_status()


def qlever_graph_count(url: str, graph: str) -> int:
    query = f"SELECT (COUNT(*) AS ?count) WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}"
    response = httpx.post(
        f"{url}/",
        content=query.encode(),
        headers={
            "Content-Type": "application/sparql-query",
            "Accept": "application/sparql-results+json",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        value = payload["results"]["bindings"][0]["count"]["value"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("QLever graph count response is malformed") from exc
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("QLever graph count is not an integer") from exc
    if count < 0:
        raise RuntimeError("QLever graph count cannot be negative")
    return count


@contextmanager
def preserve_qlever_graph(url: str, graph: str) -> Iterator[None]:
    """Restore *graph* exactly, reporting both test and cleanup failures."""
    backup = f"{graph}/test-backup/{uuid.uuid4().hex}"
    original_count = qlever_graph_count(url, graph)
    qlever_update(
        url,
        f"CLEAR SILENT GRAPH <{backup}>; "
        f"ADD SILENT GRAPH <{graph}> TO GRAPH <{backup}>",
    )
    backup_count = qlever_graph_count(url, backup)
    if backup_count != original_count:
        raise RuntimeError(
            "QLever graph backup count differs from live graph: "
            f"live={original_count}, backup={backup_count}"
        )

    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc

    cleanup_error: BaseException | None = None
    try:
        qlever_update(
            url,
            f"CLEAR SILENT GRAPH <{graph}>; "
            f"ADD SILENT GRAPH <{backup}> TO GRAPH <{graph}>; "
            f"DROP SILENT GRAPH <{backup}>",
        )
        restored_count = qlever_graph_count(url, graph)
        if restored_count != original_count:
            raise RuntimeError(
                "QLever restored graph count differs from preserved graph: "
                f"preserved={original_count}, restored={restored_count}"
            )
    except BaseException as exc:
        cleanup_error = exc

    if primary_error is not None and cleanup_error is not None:
        raise BaseExceptionGroup(
            "test body and QLever graph restoration both failed",
            (primary_error, cleanup_error),
        )
    if cleanup_error is not None:
        raise cleanup_error.with_traceback(cleanup_error.__traceback__)
    if primary_error is not None:
        raise primary_error.with_traceback(primary_error.__traceback__)
