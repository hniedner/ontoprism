"""Pinned local data-shape contracts for configured caDSR release archives."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest

from backend.config import get_settings
from ontolib.core.download_cache import CacheManifest, DownloadOutcome, read_manifest
from ontolib.repositories.cadsr.archive import extract_cadsr_archive
from ontolib.repositories.cadsr.build import build_database
from ontolib.repositories.cadsr.download import CADSR_ZIP_FILENAME

pytestmark = [pytest.mark.integration, pytest.mark.full_store, pytest.mark.full_build]

_RELEASE_SHA256 = "68a99b43cd763b063394b545e1ff02f38051927bf8a50c8d7cb1c388e2d39748"
_RELEASE_MEMBER_COUNT = 14
_RELEASE_UNCOMPRESSED_BYTES = 1_318_221_540
_RELEASE_CDE_COUNT = 81_209
_HISTORICAL_SHA256 = "2be552dbc9b906a084c7fd285ecfaa19d452ebe88719d48f8d45168721c184bd"
_HISTORICAL_MEMBER_SIZES = (2_592_992,) + (38,) * 13


def _outcome(archive: Path, url: str) -> DownloadOutcome:
    manifest = read_manifest(archive) or CacheManifest(
        url=url,
        downloaded_at="configured-file",
        size_bytes=archive.stat().st_size,
    )
    return DownloadOutcome(path=str(archive), status="downloaded", manifest=manifest)


def test_configured_cadsr_archive_matches_pinned_release_shape(
    tmp_path: Path,
) -> None:
    settings = get_settings()
    archive = Path(settings.cadsr_data_dir) / CADSR_ZIP_FILENAME

    with extract_cadsr_archive(
        _outcome(archive, settings.cadsr_download_url),
        expected_url=settings.cadsr_download_url,
        workspace_parent=tmp_path,
    ) as extracted:
        assert extracted.source.archive_sha256 == _RELEASE_SHA256
        assert extracted.source.member_count == _RELEASE_MEMBER_COUNT
        assert sum(path.stat().st_size for path in extracted.xml_paths) == (
            _RELEASE_UNCOMPRESSED_BYTES
        )
        candidate = build_database(extracted, tmp_path / "current-candidate.db")

    assert candidate.cde_count == _RELEASE_CDE_COUNT


def test_historical_partial_export_is_rejected_and_candidate_removed(
    tmp_path: Path,
) -> None:
    settings = get_settings()
    archive = Path(settings.cadsr_data_dir) / f"{CADSR_ZIP_FILENAME}.2026-05-10"
    candidate = tmp_path / "candidate.db"

    with extract_cadsr_archive(
        _outcome(archive, settings.cadsr_download_url),
        expected_url=settings.cadsr_download_url,
        workspace_parent=tmp_path / "workspaces",
    ) as extracted:
        assert extracted.source.archive_sha256 == _HISTORICAL_SHA256
        assert tuple(path.stat().st_size for path in extracted.xml_paths) == (
            _HISTORICAL_MEMBER_SIZES
        )
        with pytest.raises(ParseError):
            build_database(extracted, candidate)

    assert not candidate.exists()
