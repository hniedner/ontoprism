"""Contracts for validating and privately extracting caDSR source archives."""

from __future__ import annotations

import hashlib
import stat
import zipfile
from dataclasses import replace
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest

from ontolib.core.download_cache import CacheManifest, DownloadOutcome
from ontolib.core.exceptions import StorageError
from ontolib.repositories.cadsr.archive import CadsrSource, extract_cadsr_archive

if TYPE_CHECKING:
    from pathlib import Path

_URL = "https://example.test/releasedCDEsXML-OD.zip"
_XML = b"<DataElementsList><DataElement/></DataElementsList>"


def _archive(path: Path, members: list[tuple[str, bytes]]) -> DownloadOutcome:
    with zipfile.ZipFile(path, "w") as stream:
        for name, content in members:
            stream.writestr(name, content)
    return DownloadOutcome(
        path=str(path),
        status="downloaded",
        manifest=CacheManifest(
            url=_URL,
            downloaded_at="2026-07-26T00:00:00+00:00",
            size_bytes=path.stat().st_size,
            etag='"source-v1"',
            last_modified="Thu, 02 Jul 2026 02:19:40 GMT",
        ),
    )


@pytest.mark.security
@pytest.mark.parametrize(
    "member",
    [
        "../escape.xml",
        "/absolute.xml",
        "C:\\escape.xml",
        "\\\\server\\share.xml",
        "nested/cde.xml",
        "notes.txt",
    ],
)
def test_archive_rejects_unsafe_member_before_extraction(
    tmp_path: Path, member: str
) -> None:
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    outcome = _archive(tmp_path / "source.zip", [(member, _XML)])

    with (
        pytest.raises(StorageError, match="unsafe caDSR archive member"),
        extract_cadsr_archive(outcome, expected_url=_URL, workspace_parent=workspace),
    ):
        pytest.fail("unsafe archive was exposed to the builder")

    assert list(workspace.iterdir()) == []
    assert not (tmp_path / "escape.xml").exists()


@pytest.mark.unit
def test_archive_uses_fresh_workspace_and_only_yields_current_members(
    tmp_path: Path,
) -> None:
    persistent = tmp_path / "extracted"
    persistent.mkdir()
    stale = persistent / "stale.xml"
    stale.write_text("stale release")
    outcome = _archive(
        tmp_path / "source.zip",
        [("cde_xml_20260701120000_1.xml", _XML)],
    )

    with extract_cadsr_archive(
        outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
    ) as extracted:
        extracted_root = extracted.xml_paths[0].parent
        assert [path.name for path in extracted.xml_paths] == [
            "cde_xml_20260701120000_1.xml"
        ]
        assert extracted.xml_paths[0].read_bytes() == _XML
        assert extracted_root != persistent
        assert stale.read_text() == "stale release"
        forged = tmp_path / extracted.xml_paths[0].name
        forged.write_bytes(_XML)
        with pytest.raises(TypeError, match="InitVar '_seal'"):
            replace(extracted, xml_paths=(forged,))

    assert not extracted_root.exists()
    assert stale.read_text() == "stale release"


@pytest.mark.unit
def test_archive_retains_computed_and_upstream_source_identity(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    outcome = _archive(
        archive,
        [
            ("cde_xml_20260701120000_1.xml", _XML),
            ("cde_xml_20260701130000_2.xml", _XML),
        ],
    )

    with extract_cadsr_archive(
        outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
    ) as extracted:
        source = extracted.source
        assert source.url == _URL
        assert source.etag == '"source-v1"'
        assert source.last_modified == "Thu, 02 Jul 2026 02:19:40 GMT"
        assert source.archive_size == archive.stat().st_size
        assert source.archive_sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
        assert source.member_count == 2
        assert (
            source.member_names_sha256
            == hashlib.sha256(
                b"cde_xml_20260701120000_1.xml\ncde_xml_20260701130000_2.xml"
            ).hexdigest()
        )
        assert source.first_member_timestamp == "2026-07-01T12:00:00"
        assert source.last_member_timestamp == "2026-07-01T13:00:00"


@pytest.mark.unit
def test_archive_accepts_a_sequence_that_crosses_midnight(tmp_path: Path) -> None:
    outcome = _archive(
        tmp_path / "source.zip",
        [
            ("cde_xml_20260509235959_1.xml", _XML),
            ("cde_xml_20260510000001_2.xml", _XML),
        ],
    )

    with extract_cadsr_archive(
        outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
    ) as extracted:
        assert len(extracted.xml_paths) == 2


@pytest.mark.unit
def test_archive_rejects_incomplete_member_sequence(tmp_path: Path) -> None:
    outcome = _archive(
        tmp_path / "source.zip",
        [
            ("cde_xml_20260701120000_1.xml", _XML),
            ("cde_xml_20260701130000_3.xml", _XML),
        ],
    )

    with (
        pytest.raises(StorageError, match="contiguous sequence"),
        extract_cadsr_archive(
            outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
        ),
    ):
        pytest.fail("incomplete archive was exposed to the builder")


@pytest.mark.unit
def test_archive_rejects_empty_archive(tmp_path: Path) -> None:
    outcome = _archive(tmp_path / "source.zip", [])

    with (
        pytest.raises(StorageError, match="contains no release XML members"),
        extract_cadsr_archive(
            outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
        ),
    ):
        pytest.fail("empty archive was exposed to the builder")


@pytest.mark.unit
def test_archive_rejects_invalid_member_calendar_timestamp(tmp_path: Path) -> None:
    outcome = _archive(
        tmp_path / "source.zip",
        [("cde_xml_20260230120000_1.xml", _XML)],
    )

    with (
        pytest.raises(StorageError, match="invalid timestamp"),
        extract_cadsr_archive(
            outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
        ),
    ):
        pytest.fail("invalid timestamp was exposed to the builder")


@pytest.mark.unit
def test_archive_rejects_manifest_that_does_not_identify_cached_bytes(
    tmp_path: Path,
) -> None:
    outcome = _archive(
        tmp_path / "source.zip",
        [("cde_xml_20260701120000_1.xml", _XML)],
    )
    outcome.manifest.size_bytes += 1

    with (
        pytest.raises(StorageError, match="size does not match"),
        extract_cadsr_archive(
            outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
        ),
    ):
        pytest.fail("mismatched cache manifest was accepted")


@pytest.mark.unit
def test_archive_rejects_manifest_for_another_url(tmp_path: Path) -> None:
    outcome = _archive(
        tmp_path / "source.zip",
        [("cde_xml_20260701120000_1.xml", _XML)],
    )

    with (
        pytest.raises(StorageError, match="manifest URL"),
        extract_cadsr_archive(
            outcome,
            expected_url="https://other.example.test/cadsr.zip",
            workspace_parent=tmp_path / "workspaces",
        ),
    ):
        pytest.fail("mismatched source URL was accepted")


@pytest.mark.unit
def test_offline_archive_requires_a_persisted_cache_manifest(tmp_path: Path) -> None:
    outcome = _archive(
        tmp_path / "source.zip",
        [("cde_xml_20260701120000_1.xml", _XML)],
    ).model_copy(update={"status": "offline"})

    with (
        pytest.raises(StorageError, match="persisted cache manifest"),
        extract_cadsr_archive(
            outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
        ),
    ):
        pytest.fail("synthetic offline provenance was accepted")


@pytest.mark.security
@pytest.mark.parametrize(
    "file_type",
    [
        stat.S_IFDIR,
        stat.S_IFLNK,
        stat.S_IFIFO,
        stat.S_IFSOCK,
        stat.S_IFCHR,
        stat.S_IFBLK,
    ],
)
def test_archive_rejects_non_regular_member_metadata(
    tmp_path: Path, file_type: int
) -> None:
    archive = tmp_path / "source.zip"
    info = zipfile.ZipInfo("cde_xml_20260701120000_1.xml")
    info.create_system = 3
    info.external_attr = (file_type | 0o644) << 16
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(info, _XML)
    outcome = DownloadOutcome(
        path=str(archive),
        status="downloaded",
        manifest=CacheManifest(
            url=_URL,
            downloaded_at="2026-07-26T00:00:00+00:00",
            size_bytes=archive.stat().st_size,
        ),
    )

    with (
        pytest.raises(StorageError, match="unexpected or unsafe"),
        extract_cadsr_archive(
            outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
        ),
    ):
        pytest.fail("non-regular archive member was accepted")


@pytest.mark.unit
def test_archive_body_error_remains_primary_when_workspace_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _archive(
        tmp_path / "source.zip",
        [("cde_xml_20260701120000_1.xml", _XML)],
    )
    real_temporary_directory = TemporaryDirectory

    class CleanupFailingTemporaryDirectory(real_temporary_directory):
        def cleanup(self) -> None:
            super().cleanup()
            raise OSError("injected workspace cleanup failure")

    monkeypatch.setattr(
        "ontolib.repositories.cadsr.archive.TemporaryDirectory",
        CleanupFailingTemporaryDirectory,
    )

    with (
        pytest.raises(ValueError, match="consumer failed") as captured,
        extract_cadsr_archive(
            outcome, expected_url=_URL, workspace_parent=tmp_path / "workspaces"
        ),
    ):
        raise ValueError("consumer failed")

    assert any(
        "injected workspace cleanup failure" in note
        for note in captured.value.__notes__
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"url": " "}, "url"),
        ({"downloaded_at": ""}, "downloaded_at"),
        ({"archive_size": 0}, "archive_size"),
        ({"archive_sha256": "not-a-hash"}, "archive_sha256"),
        ({"member_count": 0}, "member_count"),
        ({"member_names_sha256": "b" * 63}, "member_names_sha256"),
        ({"first_member_timestamp": "invalid"}, "member timestamp"),
        (
            {
                "first_member_timestamp": "2026-07-02T00:00:00",
                "last_member_timestamp": "2026-07-01T00:00:00",
            },
            "timestamp range",
        ),
    ],
)
def test_source_provenance_rejects_invalid_identity(
    changes: dict[str, object], message: str
) -> None:
    valid = CadsrSource(
        url=_URL,
        downloaded_at="2026-07-26T00:00:00+00:00",
        etag='"source-v1"',
        last_modified="Thu, 02 Jul 2026 02:19:40 GMT",
        archive_size=123,
        archive_sha256="a" * 64,
        member_count=1,
        member_names_sha256="b" * 64,
        first_member_timestamp="2026-07-01T00:00:00",
        last_member_timestamp="2026-07-01T00:00:00",
    )

    with pytest.raises(ValueError, match=message):
        replace(valid, **changes)
