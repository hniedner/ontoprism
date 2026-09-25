from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from ontolib.decomposition import publication

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
async def test_staging_chunks_preserve_lines_replace_then_append(
    tmp_path: Path,
) -> None:
    payload = (
        b'<urn:a> <urn:p> "one" .\n<urn:b> <urn:p> "two" .\n<urn:c> <urn:p> "three" .\n'
    )
    path = tmp_path / "input.nt"
    path.write_bytes(payload)
    loads = []

    class Client:
        async def load(
            self, data, *, content_type, graph_iri, replace, timeout_seconds
        ):
            assert timeout_seconds == 900
            assert content_type == "application/n-triples"
            assert graph_iri == "urn:staging"
            loads.append((data.read(), replace))

    await publication._load_ntriples_chunks(
        Client(), path, "urn:staging", chunk_bytes=40
    )
    assert b"".join(data for data, _ in loads) == payload
    assert [replace for _, replace in loads] == [True, False, False]
    assert all(data.endswith(b"\n") and len(data) <= 40 for data, _ in loads)


@pytest.mark.unit
async def test_failed_chunk_names_index_and_range(tmp_path: Path) -> None:
    path = tmp_path / "input.nt"
    line = b'<urn:test:a> <urn:test:p> "one" .\n'
    path.write_bytes(line * 3)
    client = AsyncMock()
    client.load.side_effect = [None, OSError("upload lost")]
    with pytest.raises(publication.PublicationValidationError) as error:
        await publication._load_ntriples_chunks(
            client, path, "urn:test:staging", chunk_bytes=len(line)
        )
    assert f"chunk 2 bytes [{len(line)}, {2 * len(line)})" in str(error.value)
    assert isinstance(error.value.__cause__, OSError)
    assert client.load.await_count == 2


@pytest.mark.unit
async def test_empty_load_replaces_stale_staging(tmp_path: Path) -> None:
    path = tmp_path / "empty.nt"
    path.write_bytes(b"")
    seen = []

    async def load(data, **kwargs):
        seen.append((data.read(), kwargs["replace"]))

    client = AsyncMock()
    client.load.side_effect = load
    await publication._load_ntriples_chunks(client, path, "urn:test:staging")
    assert seen == [(b"", True)]


@pytest.mark.unit
async def test_overlong_line_fails_without_partial_upload(tmp_path: Path) -> None:
    path = tmp_path / "long.nt"
    path.write_bytes(b'<urn:test:a> <urn:test:p> "long" .\n')
    client = AsyncMock()
    with pytest.raises(publication.PublicationValidationError, match="line exceeds"):
        await publication._load_ntriples_chunks(
            client, path, "urn:test:g", chunk_bytes=10
        )
    client.load.assert_not_awaited()
