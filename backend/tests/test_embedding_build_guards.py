"""Behavioral guards for source preparation and publication boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
import subprocess
import zipfile
from contextlib import closing
from types import SimpleNamespace
from typing import TYPE_CHECKING
from xml.etree.ElementTree import ParseError

import pytest
from scripts.data_build import (
    _build_cadsr,
    _build_ncit_sibling,
    _build_owl,
    _code_commit,
    _dispose_cadsr_engine,
    _prepare_owl_artifacts,
    _require_ncit_source,
    _require_stable_cadsr_source,
    _require_stable_ncit_source,
)

from ontolib.core.download_cache import CacheManifest, DownloadOutcome
from ontolib.core.exceptions import StorageError
from ontolib.repositories.embeddings.publication import Corpus

if TYPE_CHECKING:
    from pathlib import Path

_CADSR_URL = "https://example.test/releasedCDEsXML-OD.zip"


@pytest.mark.unit
async def test_cadsr_engine_disposal_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    finish = asyncio.Event()
    disposed = asyncio.Event()

    async def delayed_disposal(_engine: object) -> None:
        entered.set()
        await finish.wait()
        disposed.set()

    monkeypatch.setattr("scripts.data_build.dispose_engine", delayed_disposal)

    task = asyncio.create_task(
        _dispose_cadsr_engine(object())  # type: ignore[arg-type]
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert disposed.is_set()


def _cadsr_outcome(archive: Path) -> DownloadOutcome:
    return DownloadOutcome(
        path=str(archive),
        status="downloaded",
        manifest=CacheManifest(
            url=_CADSR_URL,
            downloaded_at="2026-07-26T00:00:00+00:00",
            size_bytes=archive.stat().st_size,
            etag='"source-v1"',
            last_modified="Thu, 02 Jul 2026 02:19:40 GMT",
        ),
    )


def _cde_xml(public_id: str) -> str:
    return (
        "<DataElementsList><DataElement>"
        f"<PUBLICID>{public_id}</PUBLICID><VERSION>1</VERSION>"
        f"<PREFERREDNAME>CDE_{public_id}</PREFERREDNAME>"
        "</DataElement></DataElementsList>"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "count", "message"),
    [
        (None, 204_373, "no owl:versionInfo"),
        ("wrong", 204_373, "version does not match"),
        ("26.02d", 4_752, "count does not match"),
    ],
)
def test_ncit_source_preflight_rejects_unpublishable_release(
    version: str | None, count: int, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _require_ncit_source(
            version,
            count,
            expected_version="26.02d",
            expected_count=204_373,
        )


@pytest.mark.unit
def test_ncit_source_stability_detects_same_count_content_drift() -> None:
    with pytest.raises(RuntimeError, match="source changed"):
        _require_stable_ncit_source(
            ("26.02d", 204_373, "before"),
            ("26.02d", 204_373, "after"),
        )


@pytest.mark.unit
def test_cadsr_source_stability_detects_file_drift() -> None:
    with pytest.raises(RuntimeError, match="source changed"):
        _require_stable_cadsr_source(("before", 79_827), ("after", 79_827))


@pytest.mark.unit
def test_code_commit_requires_clean_repo_and_returns_exact_head(tmp_path) -> None:  # type: ignore[no-untyped-def]
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True)  # noqa: S603
    (tmp_path / "tracked.txt").write_text("clean")
    subprocess.run([git, "-C", str(tmp_path), "add", "."], check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [
            git,
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "test",
        ],
        check=True,
    )
    expected = subprocess.run(  # noqa: S603
        [git, "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert _code_commit(tmp_path) == expected
    (tmp_path / "tracked.txt").write_text("dirty")
    with pytest.raises(RuntimeError, match="clean worktree"):
        _code_commit(tmp_path)


@pytest.mark.unit
async def test_owl_preparation_propagates_a_failed_pair_download(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    async def download(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            success=False, stated=None, inferred=None, error="pair failed"
        )

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            ncit_owl_dir=str(tmp_path),
            ncit_owl_base_url="http://example.invalid",
            ncit_owl_max_retries=0,
            database_url="postgresql+asyncpg://unused",
            ncit_sparql_url="http://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_ncit_owl_pair", download)

    with pytest.raises(RuntimeError, match="pair failed"):
        await _build_owl()

    assert not (tmp_path / "ncit-artifact-pair.json").exists()


@pytest.mark.unit
async def test_owl_candidates_are_prepared_to_distinct_paths(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    inferred = tmp_path / "downloaded-inferred.owl"
    stated = tmp_path / "downloaded-stated.owl"

    async def download(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            success=True,
            inferred=SimpleNamespace(file_path=str(inferred)),
            stated=SimpleNamespace(file_path=str(stated)),
            manifest_path=str(tmp_path / "ncit-artifact-pair.json"),
            error=None,
        )

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            ncit_owl_dir=str(tmp_path),
            ncit_owl_base_url="http://example.invalid",
            ncit_owl_max_retries=0,
            database_url="postgresql+asyncpg://unused",
            ncit_sparql_url="http://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_ncit_owl_pair", download)

    prepared = await _prepare_owl_artifacts()

    assert prepared == {
        "inferred": inferred,
        "stated": stated,
        "manifest": tmp_path / "ncit-artifact-pair.json",
    }
    assert prepared["inferred"] != prepared["stated"]


@pytest.mark.unit
async def test_owl_preparation_refuses_a_pair_missing_an_extracted_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    """A "successful" pair without both extracted OWL paths must not be usable."""

    async def download(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            success=True,
            inferred=SimpleNamespace(file_path=str(tmp_path / "inferred.owl")),
            stated=SimpleNamespace(file_path=None),
            manifest_path=str(tmp_path / "ncit-artifact-pair.json"),
            error=None,
        )

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            ncit_owl_dir=str(tmp_path),
            ncit_owl_base_url="http://example.invalid",
            ncit_owl_max_retries=0,
            database_url="postgresql+asyncpg://unused",
            ncit_sparql_url="http://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_ncit_owl_pair", download)

    with pytest.raises(RuntimeError, match="artifact-pair download failed"):
        await _prepare_owl_artifacts()


@pytest.mark.unit
async def test_ncit_sibling_command_uses_certified_pair_and_configured_active_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = tmp_path / "ncit-owl" / "ncit-artifact-pair.json"
    pair.parent.mkdir()
    pair.write_text("pair")
    active = tmp_path / "oxigraph-ncit"
    active.mkdir()
    calls: list[tuple[Path, Path]] = []

    async def build(
        pair_manifest_path: Path,
        *,
        active_store_path: Path,
        runtime: object,
    ) -> SimpleNamespace:
        del runtime
        calls.append((pair_manifest_path, active_store_path))
        return SimpleNamespace(
            candidate_path=str(tmp_path / "candidate"),
            source_identity="source-identity",
        )

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            ncit_owl_dir=str(pair.parent),
            ncit_store_dir=str(active),
        ),
    )
    monkeypatch.setattr("scripts.data_build.build_ncit_sibling_store", build)

    result = await _build_ncit_sibling()

    assert calls == [(pair, active)]
    assert result.candidate_path == str(tmp_path / "candidate")


@pytest.mark.unit
def test_cadsr_candidate_failure_preserves_existing_source_before_replacement(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    archive = tmp_path / "cdes.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("cde_xml_20260701120000_1.xml", "not XML")
    destination = tmp_path / "cde.db"
    destination.write_text("accepted")
    lock_entries: list[str] = []

    async def download(*_args: object, **_kwargs: object) -> DownloadOutcome:
        return _cadsr_outcome(archive)

    async def coordinate(  # type: ignore[no-untyped-def]
        _session_factory, corpus, *, prepare, replace
    ):
        del replace
        assert corpus is Corpus.CADSR
        lock_entries.append("entered")
        await prepare()

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            cadsr_data_dir=str(tmp_path / "data"),
            cadsr_download_url=_CADSR_URL,
            cadsr_db_path=str(destination),
            database_url="postgresql+asyncpg://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_cadsr_cdes", download)
    monkeypatch.setattr(
        "scripts.data_build.coordinate_corpus_source_replacement", coordinate
    )
    monkeypatch.setattr("scripts.data_build.make_engine", lambda _url: object())
    monkeypatch.setattr(
        "scripts.data_build.make_sessionmaker", lambda _engine: object()
    )

    async def dispose(_engine: object) -> None:
        raise RuntimeError("dispose also failed")

    monkeypatch.setattr("scripts.data_build.dispose_engine", dispose)

    with pytest.raises(ParseError, match="syntax error") as captured:
        _build_cadsr()

    assert destination.read_text() == "accepted"
    assert lock_entries == ["entered"]
    assert any("dispose also failed" in note for note in captured.value.__notes__)
    assert list(tmp_path.glob(".cde.db.*.candidate")) == []


@pytest.mark.unit
def test_cadsr_validation_failure_never_reaches_replacement(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    archive = tmp_path / "cdes.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("cde_xml_20260701120000_1.xml", _cde_xml("100"))
    destination = tmp_path / "cde.db"
    destination.write_text("accepted")
    events: list[str] = []

    async def download(*_args: object, **_kwargs: object) -> DownloadOutcome:
        return _cadsr_outcome(archive)

    async def coordinate(  # type: ignore[no-untyped-def]
        _session_factory, corpus, *, prepare, replace
    ):
        assert corpus is Corpus.CADSR
        events.append("entered")
        candidate = await prepare()
        events.append("prepared")
        replace(candidate)

    def fail_validation(_connection: object) -> None:
        raise StorageError("injected candidate validation failure")

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            cadsr_data_dir=str(tmp_path / "data"),
            cadsr_download_url=_CADSR_URL,
            cadsr_db_path=str(destination),
            database_url="postgresql+asyncpg://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_cadsr_cdes", download)
    monkeypatch.setattr(
        "scripts.data_build.coordinate_corpus_source_replacement", coordinate
    )
    monkeypatch.setattr(
        "ontolib.repositories.cadsr.build._check_row_content", fail_validation
    )
    monkeypatch.setattr("scripts.data_build.make_engine", lambda _url: object())
    monkeypatch.setattr(
        "scripts.data_build.make_sessionmaker", lambda _engine: object()
    )

    async def dispose(_engine: object) -> None:
        return None

    monkeypatch.setattr("scripts.data_build.dispose_engine", dispose)
    with pytest.raises(StorageError, match="injected candidate validation failure"):
        _build_cadsr()

    assert destination.read_text() == "accepted"
    assert events == ["entered"]
    assert list(tmp_path.glob(".cde.db.*.candidate")) == []


@pytest.mark.unit
def test_cadsr_complete_candidate_replaces_source_through_coordinator_callback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    archive = tmp_path / "cdes.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("cde_xml_20260701120000_1.xml", _cde_xml("100"))
    persistent_extract = tmp_path / "data" / "extracted"
    persistent_extract.mkdir(parents=True)
    stale = persistent_extract / "stale.xml"
    stale.write_text(_cde_xml("999"))
    destination = tmp_path / "cde.db"
    with closing(sqlite3.connect(destination)) as accepted:
        accepted.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        accepted.execute("INSERT INTO marker VALUES ('accepted')")
        accepted.commit()
    events: list[str] = []
    old_reader_results: list[str] = []

    async def download(*_args: object, **_kwargs: object) -> DownloadOutcome:
        return _cadsr_outcome(archive)

    async def coordinate(  # type: ignore[no-untyped-def]
        _session_factory, corpus, *, prepare, replace
    ):
        assert corpus is Corpus.CADSR
        events.append("enter")
        candidate = await prepare()
        events.append("prepared")
        with closing(sqlite3.connect(destination)) as old_reader:
            assert old_reader.execute("SELECT value FROM marker").fetchone() == (
                "accepted",
            )
            replace(candidate)
            old_reader_results.append(
                str(old_reader.execute("SELECT value FROM marker").fetchone()[0])
            )
        events.append("replaced")
        events.append("exit")
        return candidate

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            cadsr_data_dir=str(tmp_path / "data"),
            cadsr_download_url=_CADSR_URL,
            cadsr_db_path=str(destination),
            database_url="postgresql+asyncpg://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_cadsr_cdes", download)
    monkeypatch.setattr(
        "scripts.data_build.coordinate_corpus_source_replacement", coordinate
    )
    monkeypatch.setattr("scripts.data_build.make_engine", lambda _url: object())
    monkeypatch.setattr(
        "scripts.data_build.make_sessionmaker", lambda _engine: object()
    )

    async def dispose(_engine):  # type: ignore[no-untyped-def]
        raise RuntimeError("dispose failed after commit")

    monkeypatch.setattr("scripts.data_build.dispose_engine", dispose)
    _build_cadsr()

    with closing(sqlite3.connect(destination)) as conn:
        assert conn.execute("SELECT public_id FROM cdes").fetchall() == [("100",)]
        assert conn.execute("SELECT archive_sha256 FROM cadsr_source").fetchone() == (
            hashlib.sha256(archive.read_bytes()).hexdigest(),
        )
    assert stale.read_text() == _cde_xml("999")
    assert old_reader_results == ["accepted"]
    assert events == ["enter", "prepared", "replaced", "exit"]


@pytest.mark.unit
@pytest.mark.parametrize("suffix", ["-journal", "-shm", "-wal"])
def test_cadsr_destination_sidecar_aborts_replacement_and_preserves_source(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    suffix: str,
) -> None:
    archive = tmp_path / "cdes.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("cde_xml_20260701120000_1.xml", _cde_xml("100"))
    destination = tmp_path / "cde.db"
    destination.write_text("accepted")
    sidecar = tmp_path / f"cde.db{suffix}"
    sidecar.write_text("uncheckpointed")
    events: list[str] = []
    downloads: list[str] = []

    async def download(*_args: object, **_kwargs: object) -> DownloadOutcome:
        downloads.append("called")
        return _cadsr_outcome(archive)

    async def coordinate(  # type: ignore[no-untyped-def]
        _session_factory, corpus, *, prepare, replace
    ):
        del replace
        assert corpus is Corpus.CADSR
        events.append("enter")
        await prepare()

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            cadsr_data_dir=str(tmp_path / "data"),
            cadsr_download_url=_CADSR_URL,
            cadsr_db_path=str(destination),
            database_url="postgresql+asyncpg://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_cadsr_cdes", download)
    monkeypatch.setattr(
        "scripts.data_build.coordinate_corpus_source_replacement", coordinate
    )
    monkeypatch.setattr("scripts.data_build.make_engine", lambda _url: object())
    monkeypatch.setattr(
        "scripts.data_build.make_sessionmaker", lambda _engine: object()
    )

    async def dispose(_engine):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr("scripts.data_build.dispose_engine", dispose)
    with pytest.raises(RuntimeError, match="SQLite sidecars"):
        _build_cadsr()

    assert destination.read_text() == "accepted"
    assert sidecar.read_text() == "uncheckpointed"
    assert events == ["enter"]
    assert downloads == []
    assert list(tmp_path.glob(".cde.db.*.candidate")) == []
