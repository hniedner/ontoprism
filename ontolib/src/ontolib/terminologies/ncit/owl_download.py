"""Download the NCIt OWL ontology from NCI EVS as part of the refresh mechanism.

NCI Enterprise Vocabulary Services publishes the Thesaurus as zipped OWL/RDF at
``https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/`` under a CC BY 4.0 licence. Two variants
matter here:

- ``stated``   → ``Thesaurus.OWL.zip``     — the asserted axioms; what the decomposition
  engine needs (no inferred-closure bleed, see DECISIONS D4).
- ``inferred`` → ``ThesaurusInf.OWL.zip``  — the materialised closure; what the running
  store currently holds.

The downloader fetches the zip through the metadata-aware cache
(:func:`ontolib.core.download_cache.cached_download`) — a conditional request reuses an
unchanged remote (304), and an unreachable remote falls back to the cached copy — then
streams each variant to a distinct ``.owl`` path. The pair API hashes and same-release
binds both artifacts into a revalidatable manifest. Full-ontology HTTP loading is
deliberately unavailable; offline sibling-store construction is a separate workflow.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4
from xml.etree.ElementTree import ParseError

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import iterparse
from pydantic import BaseModel, ConfigDict

from ontolib.core.download_cache import (
    DownloadOutcome,
    cached_download,
    manifest_path,
)
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

logger = get_logger(__name__)

DEFAULT_OWL_BASE_URL = "https://evs.nci.nih.gov/ftp1/NCI_Thesaurus"
PAIR_MANIFEST_FILENAME = "ncit-artifact-pair.json"
PAIR_MANIFEST_SCHEMA_VERSION = 1

# variant -> EVS zip filename
_VARIANT_ZIPS = {
    "stated": "Thesaurus.OWL.zip",
    "inferred": "ThesaurusInf.OWL.zip",
}
_VARIANT_MEMBERS = {
    "stated": "Thesaurus.owl",
    "inferred": "ThesaurusInferred.owl",
}
_VARIANT_OWL_FILES = {
    "stated": "Thesaurus-stated.owl",
    "inferred": "Thesaurus-inferred.owl",
}

_CONNECT_TIMEOUT = 30.0  # probe_owl_version HEAD timeout
_STREAM_CHUNK_BYTES = 1024 * 1024
_OWL_NS = "http://www.w3.org/2002/07/owl#"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_ZIP_MODE_MASK = 0o170000
_ZIP_SYMLINK_MODE = 0o120000


class OwlVersionInfo(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    """Remote OWL artifact metadata from a HEAD probe."""

    url: str
    size_bytes: int | None = None
    last_modified: str | None = None


class OwlDownloadResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    """Outcome of an OWL download: the extracted file, or an error.

    ``source_last_modified`` / ``source_etag`` echo the cached source's version markers
    (from the download manifest) so a caller can see *which version* is on disk.
    """

    success: bool
    variant: str
    source_url: str | None = None
    archive_path: str | None = None
    file_path: str | None = None
    archive_size_bytes: int | None = None
    size_bytes: int | None = None
    archive_sha256: str | None = None
    owl_sha256: str | None = None
    ontology_version: str | None = None
    ontology_iri: str | None = None
    artifact_identity: str | None = None
    cached: bool = False
    # True when the remote was unreachable and a possibly-stale cached copy was served —
    # a degraded (not fresh) success the caller/operator should be able to see.
    offline: bool = False
    source_last_modified: str | None = None
    source_etag: str | None = None
    error: str | None = None


class OwlArtifactRecord(BaseModel):
    """Immutable provenance needed to identify and revalidate one artifact."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    variant: Literal["stated", "inferred"]
    source_url: str
    archive_path: str
    file_path: str
    archive_size_bytes: int
    size_bytes: int
    archive_sha256: str
    owl_sha256: str
    ontology_version: str
    ontology_iri: str
    artifact_identity: str
    source_last_modified: str | None = None
    source_etag: str | None = None


class OwlArtifactPairManifest(BaseModel):
    """A same-release stated/inferred artifact pair and its deterministic identity."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = PAIR_MANIFEST_SCHEMA_VERSION
    manifest_identity: str
    ontology_version: str
    ontology_iri: str
    stated: OwlArtifactRecord
    inferred: OwlArtifactRecord


class OwlPairDownloadResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    """Outcome of downloading and binding both NCIt release variants."""

    success: bool
    stated: OwlDownloadResult | None = None
    inferred: OwlDownloadResult | None = None
    ontology_version: str | None = None
    ontology_iri: str | None = None
    manifest_path: str | None = None
    manifest_identity: str | None = None
    error: str | None = None


def owl_download_url(variant: str, base_url: str = DEFAULT_OWL_BASE_URL) -> str:
    """Return the EVS download URL for the given OWL *variant*.

    Raises:
        ValueError: if *variant* is not ``stated`` or ``inferred``.
    """
    try:
        filename = _VARIANT_ZIPS[variant]
    except KeyError as exc:
        raise ValueError(
            f"Unknown OWL variant {variant!r}; expected one of {sorted(_VARIANT_ZIPS)}"
        ) from exc
    return f"{base_url.rstrip('/')}/{filename}"


async def probe_owl_version(url: str) -> OwlVersionInfo:
    """HEAD the OWL artifact and report its size / last-modified (best effort)."""
    async with httpx.AsyncClient() as client:
        response = await client.head(
            url, follow_redirects=True, timeout=_CONNECT_TIMEOUT
        )
        response.raise_for_status()
    raw_size = response.headers.get("content-length")
    return OwlVersionInfo(
        url=url,
        size_bytes=int(raw_size) if raw_size else None,
        last_modified=response.headers.get("last-modified"),
    )


# A verdict about artifact content, identity, or local availability — never an HTTP
# transport error. Content and identity failures are terminal because re-fetching the
# same URL returns the same bytes; a missing or unreadable local file needs the
# artifact re-prepared, not the request retried.
class OwlContentError(StorageError):
    """An NCIt artifact or artifact pair cannot be safely identified."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_STREAM_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _ontology_iri_event(
    event: str, element: Element, current: str | None
) -> str | None:
    if event != "start":
        return current
    if element.tag != f"{{{_OWL_NS}}}Ontology":
        return current
    return element.get(f"{{{_RDF_NS}}}about")


def _ontology_version_event(
    event: str, element: Element, current: str | None
) -> str | None:
    if event != "end":
        return current
    if element.tag != f"{{{_OWL_NS}}}versionInfo":
        return current
    return (element.text or "").strip() or None


def _missing_identity(ontology_iri: str | None, ontology_version: str | None) -> bool:
    return not ontology_iri or not ontology_version


def _ontology_identity(owl_path: Path) -> tuple[str, str]:
    ontology_iri: str | None = None
    ontology_version: str | None = None
    try:
        with owl_path.open("rb") as stream:
            for event, element in iterparse(stream, events=("start", "end")):
                ontology_iri = _ontology_iri_event(event, element, ontology_iri)
                ontology_version = _ontology_version_event(
                    event, element, ontology_version
                )
                if all((ontology_iri, ontology_version)):
                    break
                if event == "end":
                    element.clear()
    except (ParseError, DefusedXmlException) as exc:
        raise OwlContentError(f"Malformed OWL XML in {owl_path.name}: {exc}") from exc
    if _missing_identity(ontology_iri, ontology_version):
        raise OwlContentError(
            f"OWL artifact {owl_path.name} lacks ontology IRI or owl:versionInfo"
        )
    return cast("str", ontology_iri), cast("str", ontology_version)


def _unsafe_zip_member(member: zipfile.ZipInfo) -> bool:
    mode = (member.external_attr >> 16) & _ZIP_MODE_MASK
    return member.is_dir() or mode == _ZIP_SYMLINK_MODE


def _select_owl_member(archive: zipfile.ZipFile, variant: str) -> zipfile.ZipInfo:
    owl_members = [
        info for info in archive.infolist() if info.filename.lower().endswith(".owl")
    ]
    expected = _VARIANT_MEMBERS[variant]
    if len(owl_members) != 1:
        found = [info.filename for info in owl_members]
        raise OwlContentError(
            f"Expected OWL member {expected!r} for {variant}, found {found!r}"
        )
    member = owl_members[0]
    if member.filename != expected:
        raise OwlContentError(
            f"Expected OWL member {expected!r} for {variant}, "
            f"found {[member.filename]!r}"
        )
    if _unsafe_zip_member(member):
        raise OwlContentError(
            f"Expected a regular OWL member for {variant}, found {member.filename!r}"
        )
    return member


def _stream_owl_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    output_dir: Path,
    variant: str,
) -> Path:
    final = output_dir / _VARIANT_OWL_FILES[variant]
    temporary = output_dir / f".{final.name}.{uuid4().hex}.tmp"
    try:
        with archive.open(member) as source, temporary.open("wb") as target:
            while chunk := source.read(_STREAM_CHUNK_BYTES):
                target.write(chunk)
        temporary.replace(final)
    finally:
        temporary.unlink(missing_ok=True)
    return final


def _extract_owl(zip_path: Path, output_dir: Path, variant: str) -> Path:
    """Extract the Thesaurus ``.owl`` member from *zip_path* into *output_dir*.

    Raises:
        OwlContentError: the archive has no single, correctly named, regular
            ``.owl`` member, or is encrypted/unsupported.
        zipfile.BadZipFile: the archive is corrupt/truncated (retryable upstream).
        OSError: a filesystem error moving the extracted file.
    """
    try:
        with zipfile.ZipFile(zip_path) as archive:
            member = _select_owl_member(archive, variant)
            return _stream_owl_member(archive, member, output_dir, variant)
    except (RuntimeError, NotImplementedError) as exc:
        # Encrypted or unsupported-compression archive: structurally unusable, so
        # terminal (re-downloading the same URL won't help) — not a corrupt-bytes retry.
        raise OwlContentError(f"Unusable archive {zip_path.name}: {exc}") from exc


def _make_result(
    variant: str, url: str, zip_path: Path, owl: Path, outcome: DownloadOutcome
) -> OwlDownloadResult:
    archive_sha256 = _sha256_file(zip_path)
    owl_sha256 = _sha256_file(owl)
    ontology_iri, ontology_version = _ontology_identity(owl)
    identity = _identity(
        {
            "variant": variant,
            "source_url": url,
            "archive_sha256": archive_sha256,
            "owl_sha256": owl_sha256,
            "ontology_version": ontology_version,
            "ontology_iri": ontology_iri,
        }
    )
    return OwlDownloadResult(
        success=True,
        variant=variant,
        source_url=url,
        archive_path=str(zip_path.resolve()),
        file_path=str(owl.resolve()),
        archive_size_bytes=zip_path.stat().st_size,
        size_bytes=owl.stat().st_size,
        archive_sha256=archive_sha256,
        owl_sha256=owl_sha256,
        ontology_version=ontology_version,
        ontology_iri=ontology_iri,
        artifact_identity=identity,
        cached=outcome.status != "downloaded",  # revalidated (304) or offline
        offline=outcome.status == "offline",
        source_last_modified=outcome.manifest.last_modified,
        source_etag=outcome.manifest.etag,
    )


def _drop_cache(zip_path: Path) -> None:
    """Delete a bad archive and its manifest so the next call re-downloads."""
    zip_path.unlink(missing_ok=True)
    manifest_path(zip_path).unlink(missing_ok=True)


def _identity(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _require_artifact(result: OwlDownloadResult) -> OwlArtifactRecord:
    required = {
        "source_url": result.source_url,
        "archive_path": result.archive_path,
        "file_path": result.file_path,
        "archive_size_bytes": result.archive_size_bytes,
        "size_bytes": result.size_bytes,
        "archive_sha256": result.archive_sha256,
        "owl_sha256": result.owl_sha256,
        "ontology_version": result.ontology_version,
        "ontology_iri": result.ontology_iri,
        "artifact_identity": result.artifact_identity,
    }
    missing = [name for name, value in required.items() if value is None]
    if not result.success or missing:
        raise OwlContentError(
            f"Incomplete {result.variant} artifact record: {', '.join(missing)}"
        )
    if result.variant not in _VARIANT_ZIPS:
        raise OwlContentError(f"Unexpected artifact variant {result.variant!r}")
    return OwlArtifactRecord(
        variant=cast('Literal["stated", "inferred"]', result.variant),
        source_url=cast("str", result.source_url),
        archive_path=cast("str", result.archive_path),
        file_path=cast("str", result.file_path),
        archive_size_bytes=cast("int", result.archive_size_bytes),
        size_bytes=cast("int", result.size_bytes),
        archive_sha256=cast("str", result.archive_sha256),
        owl_sha256=cast("str", result.owl_sha256),
        ontology_version=cast("str", result.ontology_version),
        ontology_iri=cast("str", result.ontology_iri),
        artifact_identity=cast("str", result.artifact_identity),
        source_last_modified=result.source_last_modified,
        source_etag=result.source_etag,
    )


def _artifact_identity(record: OwlArtifactRecord) -> str:
    return _identity(
        {
            "variant": record.variant,
            "source_url": record.source_url,
            "archive_sha256": record.archive_sha256,
            "owl_sha256": record.owl_sha256,
            "ontology_version": record.ontology_version,
            "ontology_iri": record.ontology_iri,
        }
    )


def _pair_identity(stated: OwlArtifactRecord, inferred: OwlArtifactRecord) -> str:
    return _identity(
        {
            "schema_version": PAIR_MANIFEST_SCHEMA_VERSION,
            "stated": stated.artifact_identity,
            "inferred": inferred.artifact_identity,
            "ontology_version": stated.ontology_version,
            "ontology_iri": stated.ontology_iri,
        }
    )


def _bind_pair(
    stated: OwlDownloadResult, inferred: OwlDownloadResult
) -> OwlArtifactPairManifest:
    stated_record = _require_artifact(stated)
    inferred_record = _require_artifact(inferred)
    if stated_record.variant != "stated" or inferred_record.variant != "inferred":
        raise OwlContentError("NCIt artifact variants are swapped")
    if stated_record.ontology_version != inferred_record.ontology_version:
        raise OwlContentError(
            "NCIt artifact version mismatch: "
            f"{stated_record.ontology_version!r} != "
            f"{inferred_record.ontology_version!r}"
        )
    if stated_record.ontology_iri != inferred_record.ontology_iri:
        raise OwlContentError(
            "NCIt artifact ontology IRI mismatch: "
            f"{stated_record.ontology_iri!r} != {inferred_record.ontology_iri!r}"
        )
    return OwlArtifactPairManifest(
        manifest_identity=_pair_identity(stated_record, inferred_record),
        ontology_version=stated_record.ontology_version,
        ontology_iri=stated_record.ontology_iri,
        stated=stated_record,
        inferred=inferred_record,
    )


def _write_pair_manifest(output_dir: Path, manifest: OwlArtifactPairManifest) -> Path:
    destination = output_dir / PAIR_MANIFEST_FILENAME
    temporary = output_dir / f".{PAIR_MANIFEST_FILENAME}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(manifest.model_dump(), indent=2, sort_keys=True) + "\n"
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination.resolve()


def _read_pair_manifest(manifest_path_: Path) -> OwlArtifactPairManifest:
    try:
        return OwlArtifactPairManifest.model_validate_json(manifest_path_.read_text())
    except (OSError, ValueError) as exc:
        raise OwlContentError(
            f"Unreadable NCIt artifact-pair manifest {manifest_path_}: {exc}"
        ) from exc


def _validate_artifact_record(
    expected_variant: Literal["stated", "inferred"],
    record: OwlArtifactRecord,
) -> None:
    if record.variant != expected_variant:
        raise OwlContentError("NCIt artifact variants are swapped")
    archive = Path(record.archive_path)
    owl = Path(record.file_path)
    try:
        archive_hash = _sha256_file(archive)
        owl_hash = _sha256_file(owl)
    except OSError as exc:
        raise OwlContentError(
            f"Missing or unreadable {expected_variant} NCIt artifact: {exc}"
        ) from exc
    if (archive_hash, owl_hash) != (record.archive_sha256, record.owl_sha256):
        raise OwlContentError(
            f"{expected_variant} NCIt artifact SHA-256 does not match manifest"
        )
    if _artifact_identity(record) != record.artifact_identity:
        raise OwlContentError(
            f"{expected_variant} NCIt artifact identity does not match manifest"
        )
    ontology_iri, ontology_version = _ontology_identity(owl)
    if (ontology_iri, ontology_version) != (
        record.ontology_iri,
        record.ontology_version,
    ):
        raise OwlContentError(
            f"{expected_variant} NCIt ontology identity does not match manifest"
        )


def validate_ncit_owl_pair(manifest_path_: Path) -> OwlArtifactPairManifest:
    """Revalidate a persisted pair manifest and every bound file."""
    manifest = _read_pair_manifest(manifest_path_)
    if manifest.schema_version != PAIR_MANIFEST_SCHEMA_VERSION:
        raise OwlContentError(
            f"Unsupported NCIt pair manifest schema {manifest.schema_version}"
        )
    _validate_artifact_record("stated", manifest.stated)
    _validate_artifact_record("inferred", manifest.inferred)
    rebound = _bind_pair(
        OwlDownloadResult(success=True, **manifest.stated.model_dump()),
        OwlDownloadResult(success=True, **manifest.inferred.model_dump()),
    )
    if rebound.manifest_identity != manifest.manifest_identity:
        raise OwlContentError("NCIt artifact-pair manifest identity does not match")
    return manifest


async def download_ncit_owl(
    output_dir: Path,
    *,
    variant: str = "inferred",
    base_url: str = DEFAULT_OWL_BASE_URL,
    max_retries: int = 3,
) -> OwlDownloadResult:
    """Download and extract the NCIt OWL *variant* into *output_dir*.

    Uses the metadata-aware cache (:func:`ontolib.core.download_cache.cached_download`):
    an unchanged remote answers 304 and the cached zip is reused; an unreachable remote
    falls back to the cached zip. Any failure is returned as ``success=False`` (never
    raised) so the caller/endpoint can report it cleanly. ``cached`` is True when the
    result came from the cache (revalidated or offline) rather than a fresh download.
    """
    try:
        url = owl_download_url(variant, base_url)
    except ValueError as exc:
        logger.error("Invalid NCIt OWL variant %r: %s", variant, exc)
        return OwlDownloadResult(success=False, variant=variant, error=str(exc))
    zip_path = output_dir / _VARIANT_ZIPS[variant]

    try:
        outcome = await cached_download(url, zip_path, max_retries=max_retries)
    except (StorageError, OSError) as exc:
        logger.error("NCIt OWL download failed: %s", exc)
        return OwlDownloadResult(success=False, variant=variant, error=str(exc))
    if outcome.manifest.url != url:
        error = (
            f"Cached {variant} artifact source URL {outcome.manifest.url!r} "
            f"does not match requested URL {url!r}"
        )
        logger.error(error)
        return OwlDownloadResult(success=False, variant=variant, error=error)

    try:
        owl = _extract_owl(zip_path, output_dir, variant)
    except (OwlContentError, zipfile.BadZipFile, OSError) as exc:
        _drop_cache(zip_path)  # never leave a bad archive cached
        logger.error("NCIt OWL archive unusable: %s", exc)
        return OwlDownloadResult(success=False, variant=variant, error=str(exc))

    try:
        return _make_result(variant, url, zip_path, owl, outcome)
    except (OwlContentError, OSError) as exc:
        logger.error("NCIt OWL identity validation failed: %s", exc)
        return OwlDownloadResult(success=False, variant=variant, error=str(exc))


async def download_ncit_owl_pair(
    output_dir: Path,
    *,
    base_url: str = DEFAULT_OWL_BASE_URL,
    max_retries: int = 3,
) -> OwlPairDownloadResult:
    """Download, same-release-bind, persist, and revalidate both NCIt variants."""
    output_dir.mkdir(parents=True, exist_ok=True)
    inferred = await download_ncit_owl(
        output_dir,
        variant="inferred",
        base_url=base_url,
        max_retries=max_retries,
    )
    if not inferred.success:
        return OwlPairDownloadResult(
            success=False, inferred=inferred, error=inferred.error
        )
    stated = await download_ncit_owl(
        output_dir,
        variant="stated",
        base_url=base_url,
        max_retries=max_retries,
    )
    if not stated.success:
        return OwlPairDownloadResult(
            success=False, stated=stated, inferred=inferred, error=stated.error
        )
    try:
        manifest = _bind_pair(stated, inferred)
        path = _write_pair_manifest(output_dir, manifest)
        validate_ncit_owl_pair(path)
    except (OwlContentError, OSError) as exc:
        logger.error("NCIt OWL pair validation failed: %s", exc)
        return OwlPairDownloadResult(
            success=False, stated=stated, inferred=inferred, error=str(exc)
        )
    return OwlPairDownloadResult(
        success=True,
        stated=stated,
        inferred=inferred,
        ontology_version=manifest.ontology_version,
        ontology_iri=manifest.ontology_iri,
        manifest_path=str(path),
        manifest_identity=manifest.manifest_identity,
    )
