"""Unit tests for the NCIt OWL-load orchestration (fakes; no live store/network)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from ontolib.terminologies.ncit import owl_load
from ontolib.terminologies.ncit.owl_load import (
    STATED_GRAPH_IRI,
    build_ncit_store,
    load_owl_file,
)

if TYPE_CHECKING:
    from pathlib import Path


class _FakeClient:
    """Captures load() calls instead of hitting a store."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def load(
        self,
        data: object,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None:
        raw = data.read() if hasattr(data, "read") else data
        self.calls.append(
            {"len": len(raw), "content_type": content_type, "graph_iri": graph_iri}
        )


@pytest.mark.unit
async def test_load_owl_file_sends_rdfxml_to_target_graph(tmp_path: Path) -> None:
    owl = tmp_path / "t.owl"
    owl.write_bytes(b"<rdf/>")
    client = _FakeClient()
    await load_owl_file(client, owl, graph_iri="urn:g")  # type: ignore[arg-type]
    assert client.calls == [
        {"len": 6, "content_type": "application/rdf+xml", "graph_iri": "urn:g"}
    ]


@pytest.mark.unit
async def test_build_ncit_store_routes_inferred_default_stated_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fake the download so no network is hit; each variant "downloads" a tmp file.
    class _Result:
        def __init__(self, path: str) -> None:
            self.success = True
            self.file_path = path
            self.error = None

    async def _fake_download(output_dir: Path, *, variant: str, **_: Any) -> _Result:
        p = tmp_path / f"{variant}.owl"
        p.write_bytes(b"<rdf/>")
        return _Result(str(p))

    monkeypatch.setattr(owl_load, "download_ncit_owl", _fake_download)
    client = _FakeClient()
    loaded = await build_ncit_store(client, tmp_path)  # type: ignore[arg-type]

    assert set(loaded) == {"inferred", "stated"}
    graphs = {c["graph_iri"] for c in client.calls}
    # inferred → default graph (None); stated → the distinct named graph.
    assert graphs == {None, STATED_GRAPH_IRI}


@pytest.mark.unit
async def test_build_ncit_store_raises_on_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FailedResult:
        success = False
        file_path = None
        error = "Connection refused"

    async def _fail(_output_dir: Path, *, variant: str, **_: Any) -> _FailedResult:
        return _FailedResult()

    monkeypatch.setattr(owl_load, "download_ncit_owl", _fail)
    with pytest.raises(RuntimeError, match="NCIt inferred OWL download failed"):
        await build_ncit_store(object(), tmp_path)  # type: ignore[arg-type]


@pytest.mark.unit
async def test_build_ncit_store_can_skip_stated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Result:
        def __init__(self, path: str) -> None:
            self.success = True
            self.file_path = path
            self.error = None

    async def _fake_download(output_dir: Path, *, variant: str, **_: Any) -> _Result:
        p = tmp_path / f"{variant}.owl"
        p.write_bytes(b"<rdf/>")
        return _Result(str(p))

    monkeypatch.setattr(owl_load, "download_ncit_owl", _fake_download)
    client = _FakeClient()
    loaded = await build_ncit_store(client, tmp_path, include_stated=False)  # type: ignore[arg-type]
    assert set(loaded) == {"inferred"}
    assert [c["graph_iri"] for c in client.calls] == [None]
