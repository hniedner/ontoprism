from __future__ import annotations

import asyncio
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


@pytest.mark.unit
async def test_conversion_requires_configured_jena(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ONTOPRISM_JENA_DIR", raising=False)
    destination = tmp_path / "output.nt"
    with pytest.raises(
        publication.PublicationValidationError, match="ONTOPRISM_JENA_DIR"
    ):
        await publication._convert_publication_ntriples(
            tmp_path / "input.ttl", destination
        )
    assert not destination.exists()


@pytest.mark.unit
async def test_conversion_failure_exposes_jena_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTOPRISM_JENA_DIR", str(tmp_path))
    monkeypatch.setattr(publication, "identify_jena_installation", lambda _: None)
    process = AsyncMock()
    process.returncode = 1

    async def launch(*args, **kwargs):
        kwargs["stderr"].write(b"invalid Turtle at line 3")
        return process

    monkeypatch.setattr(publication.asyncio, "create_subprocess_exec", launch)
    with pytest.raises(
        publication.PublicationValidationError, match="invalid Turtle at line 3"
    ):
        await publication._convert_publication_ntriples(
            tmp_path / "input.ttl", tmp_path / "out.nt"
        )


@pytest.mark.unit
@pytest.mark.parametrize("already_exited", [False, True])
async def test_cancelled_conversion_reaps_its_own_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, already_exited: bool
) -> None:
    monkeypatch.setenv("ONTOPRISM_JENA_DIR", str(tmp_path))
    monkeypatch.setattr(publication, "identify_jena_installation", lambda _: None)
    started = asyncio.Event()
    reaped = False

    class Process:
        returncode = 0 if already_exited else None
        killed = False

        async def wait(self):
            nonlocal reaped
            if not started.is_set():
                started.set()
                await asyncio.Event().wait()
            reaped = True

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = Process()
    monkeypatch.setattr(
        publication.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    task = asyncio.create_task(
        publication._convert_publication_ntriples(
            tmp_path / "in.ttl", tmp_path / "out.nt"
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reaped
    assert process.killed is not already_exited
