"""Behavioral guards for embedding publication source preflight/stability."""

import shutil
import subprocess
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from scripts.data_build import (
    _build_cadsr,
    _build_owl,
    _code_commit,
    _require_ncit_source,
    _require_stable_cadsr_source,
    _require_stable_ncit_source,
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
async def test_owl_preparation_failure_never_enters_replacement_lock(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    inferred = tmp_path / "Thesaurus.owl"
    inferred.write_text("inferred")
    calls = 0
    lock_entries: list[str] = []

    async def download(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(success=True, file_path=str(inferred), error=None)
        return SimpleNamespace(success=False, file_path=None, error="stated failed")

    @asynccontextmanager
    async def replacing(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        lock_entries.append("entered")
        yield

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            ncit_owl_dir=str(tmp_path),
            ncit_owl_base_url="http://example.invalid",
            database_url="postgresql+asyncpg://unused",
            ncit_sparql_url="http://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_ncit_owl", download)
    monkeypatch.setattr("scripts.data_build.replacing_corpus_source", replacing)

    with pytest.raises(RuntimeError, match="stated failed"):
        await _build_owl()

    assert lock_entries == []


@pytest.mark.unit
async def test_owl_candidates_load_together_inside_replacement_lock(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    inferred = tmp_path / "downloaded-inferred.owl"
    stated = tmp_path / "downloaded-stated.owl"
    inferred.write_text("inferred")
    stated.write_text("stated")
    downloads = iter((inferred, stated))
    events: list[str] = []

    async def download(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        path = next(downloads)
        return SimpleNamespace(success=True, file_path=str(path), error=None)

    @asynccontextmanager
    async def replacing(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        events.append("enter")
        yield
        events.append("exit")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    async def load(_client, path, *, graph_iri=None):  # type: ignore[no-untyped-def]
        events.append(f"load:{path.name}:{graph_iri or 'default'}")

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            ncit_owl_dir=str(tmp_path),
            ncit_owl_base_url="http://example.invalid",
            database_url="postgresql+asyncpg://unused",
            ncit_sparql_url="http://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_ncit_owl", download)
    monkeypatch.setattr("scripts.data_build.replacing_corpus_source", replacing)
    monkeypatch.setattr("scripts.data_build.OxigraphHttpClient", lambda _url: _Client())
    monkeypatch.setattr("scripts.data_build.load_owl_file", load)
    monkeypatch.setattr("scripts.data_build.make_engine", lambda _url: object())
    monkeypatch.setattr(
        "scripts.data_build.make_sessionmaker", lambda _engine: object()
    )

    async def dispose(_engine):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr("scripts.data_build.dispose_engine", dispose)
    await _build_owl()

    assert events == [
        "enter",
        "load:Thesaurus-inferred.owl:default",
        "load:Thesaurus-stated.owl:http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl",
        "exit",
    ]


@pytest.mark.unit
def test_cadsr_candidate_failure_preserves_existing_source_and_skips_lock(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    archive = tmp_path / "cdes.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("cde.xml", "<DataElementsList/>")
    destination = tmp_path / "cde.db"
    destination.write_text("accepted")
    lock_entries: list[str] = []

    async def download(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(path=str(archive))

    @asynccontextmanager
    async def replacing(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        lock_entries.append("entered")
        yield

    def fail_build(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("candidate failed")

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            cadsr_data_dir=str(tmp_path / "data"),
            cadsr_download_url="http://example.invalid",
            cadsr_db_path=str(destination),
            database_url="postgresql+asyncpg://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_cadsr_cdes", download)
    monkeypatch.setattr("scripts.data_build.replacing_corpus_source", replacing)
    monkeypatch.setattr("scripts.data_build.build_database", fail_build)

    with pytest.raises(RuntimeError, match="candidate failed"):
        _build_cadsr()

    assert destination.read_text() == "accepted"
    assert lock_entries == []


@pytest.mark.unit
def test_cadsr_complete_candidate_replaces_source_inside_lock(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    archive = tmp_path / "cdes.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("cde.xml", "<DataElementsList/>")
    destination = tmp_path / "cde.db"
    destination.write_text("accepted")
    events: list[str] = []

    async def download(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(path=str(archive))

    @asynccontextmanager
    async def replacing(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        events.append("enter")
        yield
        events.append("exit")

    def build(_xml, candidate):  # type: ignore[no-untyped-def]
        candidate.write_text("candidate")
        return 1

    monkeypatch.setattr(
        "scripts.data_build.get_settings",
        lambda: SimpleNamespace(
            cadsr_data_dir=str(tmp_path / "data"),
            cadsr_download_url="http://example.invalid",
            cadsr_db_path=str(destination),
            database_url="postgresql+asyncpg://unused",
        ),
    )
    monkeypatch.setattr("scripts.data_build.download_cadsr_cdes", download)
    monkeypatch.setattr("scripts.data_build.replacing_corpus_source", replacing)
    monkeypatch.setattr("scripts.data_build.build_database", build)
    monkeypatch.setattr("scripts.data_build.make_engine", lambda _url: object())
    monkeypatch.setattr(
        "scripts.data_build.make_sessionmaker", lambda _engine: object()
    )

    async def dispose(_engine):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr("scripts.data_build.dispose_engine", dispose)
    _build_cadsr()

    assert destination.read_text() == "candidate"
    assert events == ["enter", "exit"]
