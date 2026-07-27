"""Validation and private extraction of a caDSR release archive."""

from __future__ import annotations

import hashlib
import re
import shutil
import stat
import zipfile
from contextlib import contextmanager
from dataclasses import InitVar, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from ontolib.core.download_cache import read_manifest
from ontolib.core.exceptions import StorageError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ontolib.core.download_cache import DownloadOutcome

_MEMBER_PATTERN = re.compile(r"cde_xml_(\d{14})_(\d+)\.xml")
_COPY_CHUNK_SIZE = 1024 * 1024
_ARCHIVE_SEAL = object()


@dataclass(frozen=True, slots=True)
class CadsrSource:
    """Identity and locally verifiable provenance of one caDSR source archive."""

    url: str
    downloaded_at: str
    etag: str | None
    last_modified: str | None
    archive_size: int
    archive_sha256: str
    member_count: int
    member_names_sha256: str
    first_member_timestamp: str
    last_member_timestamp: str

    def __post_init__(self) -> None:
        _validate_source(self)


@dataclass(frozen=True, slots=True)
class ExtractedCadsrArchive:
    """Locally identified source metadata and its exact extracted XML files."""

    source: CadsrSource
    xml_paths: tuple[Path, ...]
    _seal: InitVar[object]

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _ARCHIVE_SEAL:
            raise ValueError("archive must be created by extract_cadsr_archive")
        if len(self.xml_paths) != self.source.member_count:
            raise ValueError("archive member count does not match extracted paths")
        member_names = "\n".join(path.name for path in self.xml_paths).encode()
        if hashlib.sha256(member_names).hexdigest() != self.source.member_names_sha256:
            raise ValueError("archive member identity does not match extracted paths")


@dataclass(frozen=True)
class _ArchiveMember:
    info: zipfile.ZipInfo
    sequence: int
    timestamp: datetime


def _require_hash(name: str, value: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _validate_timestamp_range(source: CadsrSource) -> None:
    try:
        first = datetime.fromisoformat(source.first_member_timestamp)
        last = datetime.fromisoformat(source.last_member_timestamp)
    except ValueError as exc:
        raise ValueError("member timestamp must be ISO-8601") from exc
    if first > last:
        raise ValueError("member timestamp range must be ordered")


def _validate_source(source: CadsrSource) -> None:
    if not source.url.strip():
        raise ValueError("url must be non-empty")
    if not source.downloaded_at.strip():
        raise ValueError("downloaded_at must be non-empty")
    if source.archive_size <= 0:
        raise ValueError("archive_size must be positive")
    if source.member_count <= 0:
        raise ValueError("member_count must be positive")
    _require_hash("archive_sha256", source.archive_sha256)
    _require_hash("member_names_sha256", source.member_names_sha256)
    _validate_timestamp_range(source)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_COPY_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _member_match(info: zipfile.ZipInfo) -> re.Match[str]:
    name = info.filename
    path = PurePosixPath(name)
    unsafe_path = (
        info.is_dir() or path.is_absolute() or path.name != name or "\\" in name
    )
    file_type = (info.external_attr >> 16) & 0o170000
    match = _MEMBER_PATTERN.fullmatch(name)
    if unsafe_path or file_type not in (0, stat.S_IFREG) or match is None:
        raise StorageError(f"unexpected or unsafe caDSR archive member: {name!r}")
    return match


def _parse_member(info: zipfile.ZipInfo) -> _ArchiveMember:
    match = _member_match(info)
    timestamp_text, raw_sequence = match.groups()
    try:
        timestamp = datetime.strptime(timestamp_text, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise StorageError(
            f"invalid timestamp in caDSR archive member: {info.filename!r}"
        ) from exc
    return _ArchiveMember(info, int(raw_sequence), timestamp)


def _validated_members(archive: zipfile.ZipFile) -> list[_ArchiveMember]:
    members: list[_ArchiveMember] = []
    names: set[str] = set()
    sequences: set[int] = set()
    for info in archive.infolist():
        member = _parse_member(info)
        folded_name = info.filename.casefold()
        if folded_name in names:
            raise StorageError(f"unsafe caDSR archive member: {info.filename!r}")
        if member.sequence in sequences:
            raise StorageError(f"duplicate caDSR archive sequence: {member.sequence}")
        names.add(folded_name)
        sequences.add(member.sequence)
        members.append(member)

    if not members:
        raise StorageError("caDSR archive contains no release XML members")
    members.sort(key=lambda member: member.sequence)
    if [member.sequence for member in members] != list(range(1, len(members) + 1)):
        raise StorageError("caDSR archive members are not a contiguous sequence")
    return members


def _source_metadata(
    outcome: DownloadOutcome,
    snapshot: Path,
    members: list[_ArchiveMember],
) -> CadsrSource:
    timestamps = [member.timestamp for member in members]
    names = "\n".join(member.info.filename for member in members).encode()
    manifest = outcome.manifest
    return CadsrSource(
        url=manifest.url,
        downloaded_at=manifest.downloaded_at,
        etag=manifest.etag,
        last_modified=manifest.last_modified,
        archive_size=snapshot.stat().st_size,
        archive_sha256=_sha256(snapshot),
        member_count=len(members),
        member_names_sha256=hashlib.sha256(names).hexdigest(),
        first_member_timestamp=min(timestamps).isoformat(),
        last_member_timestamp=max(timestamps).isoformat(),
    )


def _prepare_workspace(
    outcome: DownloadOutcome, source_path: Path, workspace: Path
) -> ExtractedCadsrArchive:
    try:
        snapshot = workspace / "source.zip"
        shutil.copyfile(source_path, snapshot)
        if snapshot.stat().st_size != outcome.manifest.size_bytes:
            raise StorageError(
                "caDSR archive size does not match its cache manifest: "
                f"{snapshot.stat().st_size} != {outcome.manifest.size_bytes}"
            )

        with zipfile.ZipFile(snapshot) as archive:
            members = _validated_members(archive)
            source = _source_metadata(outcome, snapshot, members)
            extracted_dir = workspace / "xml"
            extracted_dir.mkdir()
            xml_paths: list[Path] = []
            for member in members:
                target = extracted_dir / member.info.filename
                with (
                    archive.open(member.info) as source_stream,
                    target.open("wb") as target_stream,
                ):
                    shutil.copyfileobj(source_stream, target_stream)
                xml_paths.append(target)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise StorageError(f"failed to prepare caDSR archive: {exc}") from exc
    return ExtractedCadsrArchive(source, tuple(xml_paths), _ARCHIVE_SEAL)


def _cleanup_workspace(
    temporary_directory: TemporaryDirectory[str],
    original: BaseException | None = None,
) -> None:
    try:
        temporary_directory.cleanup()
    except BaseException as cleanup_error:
        if original is None:
            raise
        original.add_note(
            f"Failed to remove caDSR extraction workspace: {cleanup_error}"
        )


@contextmanager
def extract_cadsr_archive(
    outcome: DownloadOutcome,
    *,
    expected_url: str,
    workspace_parent: Path,
) -> Iterator[ExtractedCadsrArchive]:
    """Identify and expose one release-shaped archive in a private workspace."""
    manifest = outcome.manifest
    if manifest.url != expected_url:
        raise StorageError(
            f"caDSR cache manifest URL {manifest.url!r} does not match {expected_url!r}"
        )

    source_path = Path(outcome.path)
    if outcome.status != "downloaded" and read_manifest(source_path) is None:
        raise StorageError(
            "cached caDSR publication requires a valid persisted cache manifest"
        )
    workspace_parent.mkdir(parents=True, exist_ok=True)
    temporary_directory = TemporaryDirectory(
        prefix=".cadsr-build-", dir=workspace_parent
    )
    try:
        extracted = _prepare_workspace(
            outcome, source_path, Path(temporary_directory.name)
        )
        yield extracted
    except BaseException as original:
        _cleanup_workspace(temporary_directory, original)
        raise
    else:
        _cleanup_workspace(temporary_directory)
