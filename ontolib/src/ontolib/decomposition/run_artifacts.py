"""Create-only filesystem records for bounded decomposition run artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict

_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_RUN_ID = re.compile(
    r"neoplasm-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_COMPLETE = ".complete"
_MANIFEST = "manifest.json"
_CLAIM = ".create-only-claim"
_RESERVED_ARTIFACT_PARTS = {_COMPLETE, _MANIFEST, _CLAIM, ".claims", ".staging"}
_CONCURRENT_WAIT_SECONDS = 10.0
_CONTROL_CODEPOINT_LIMIT = 32


class ArtifactError(ValueError):
    """Base class for immutable artifact contract refusals."""


class ArtifactPathError(ArtifactError):
    """An artifact locator is unsafe or ambiguous."""


class ArtifactConflictError(ArtifactError):
    """A claimed immutable generation differs from requested bytes."""


class PartialGenerationError(ArtifactError):
    """A generation exists without a valid completion binding."""


def _plain_token(value: str, label: str) -> str:
    if _TOKEN.fullmatch(value) is None or any(
        ord(char) < _CONTROL_CODEPOINT_LIMIT for char in value
    ):
        raise ArtifactPathError(f"{label} is not a safe token")
    return value


def _relative_path(value: str) -> str:
    if not value or "\\" in value or _contains_control_character(value):
        raise ArtifactPathError("artifact path contains invalid characters")
    path = PurePosixPath(value)
    if path.is_absolute() or not _has_normalized_parts(path):
        raise ArtifactPathError("artifact path must be normalized and relative")
    return path.as_posix()


def _artifact_relative_path(value: str) -> str:
    relative = _relative_path(value)
    if any(part in _RESERVED_ARTIFACT_PARTS for part in PurePosixPath(relative).parts):
        raise ArtifactPathError("artifact path uses a reserved control name")
    return relative


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < _CONTROL_CODEPOINT_LIMIT for char in value)


def _has_normalized_parts(path: PurePosixPath) -> bool:
    return all(part not in {"", ".", ".."} for part in path.parts)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _strict_fields(payload: dict[str, Any], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise ArtifactError(f"{label} fields are not schema v1")


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ArtifactError(f"{label} must be a string")
    return value


def _required_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ArtifactError(f"{label} must be an array")
    return value


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    relative_path: str
    size: int
    sha256: str

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        _artifact_relative_path(self.relative_path)
        if self.size < 0 or _SHA256.fullmatch(self.sha256) is None:
            raise ArtifactError("artifact size or SHA-256 is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "size": self.size,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ArtifactError("artifact record must be an object")
        _strict_fields(value, {"relative_path", "size", "sha256"}, "artifact record")
        if (
            not isinstance(value["relative_path"], str)
            or not isinstance(value["size"], int)
            or not isinstance(value["sha256"], str)
        ):
            raise ArtifactError("artifact record has invalid field types")
        return cls(
            relative_path=value["relative_path"],
            size=value["size"],
            sha256=value["sha256"],
        )


class ParentManifestBinding(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    family: str
    generation_id: str
    manifest_path: str
    manifest_identity: str

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        _plain_token(self.family, "parent family")
        _plain_token(self.generation_id, "parent generation ID")
        _relative_path(self.manifest_path)
        if _SHA256.fullmatch(self.manifest_identity) is None:
            raise ArtifactError("parent manifest identity is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "generation_id": self.generation_id,
            "manifest_path": self.manifest_path,
            "manifest_identity": self.manifest_identity,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ArtifactError("parent binding must be an object")
        fields = {"family", "generation_id", "manifest_path", "manifest_identity"}
        _strict_fields(value, fields, "parent binding")
        if any(not isinstance(value[field], str) for field in fields):
            raise ArtifactError("parent binding fields must be strings")
        return cls(**value)


class GeneratorBinding(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    identity: str
    command: tuple[str, ...]

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if (
            not self.identity
            or not self.command
            or any(not item or "\x00" in item for item in self.command)
        ):
            raise ArtifactError("generator identity and command are required")

    def to_dict(self) -> dict[str, object]:
        return {"identity": self.identity, "command": list(self.command)}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ArtifactError("generator binding must be an object")
        _strict_fields(value, {"identity", "command"}, "generator binding")
        command = value["command"]
        if (
            not isinstance(value["identity"], str)
            or not isinstance(command, list)
            or any(not isinstance(item, str) for item in command)
        ):
            raise ArtifactError("generator binding has invalid field types")
        return cls(identity=value["identity"], command=tuple(command))


class SourceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    identity: str

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        _plain_token(self.name, "source name")
        if not self.identity or any(
            ord(char) < _CONTROL_CODEPOINT_LIMIT for char in self.identity
        ):
            raise ArtifactError("source identity is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "identity": self.identity}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ArtifactError("source identity must be an object")
        _strict_fields(value, {"name", "identity"}, "source identity")
        if not isinstance(value["name"], str) or not isinstance(value["identity"], str):
            raise ArtifactError("source identity fields must be strings")
        return cls(name=value["name"], identity=value["identity"])


class RetentionBinding(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    retention_class: str
    owner: str
    expires_at: str | None

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        _plain_token(self.retention_class, "retention class")
        _plain_token(self.owner, "retention owner")
        if self.expires_at is not None and (
            not self.expires_at
            or any(ord(char) < _CONTROL_CODEPOINT_LIMIT for char in self.expires_at)
        ):
            raise ArtifactError("retention expiry is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "class": self.retention_class,
            "owner": self.owner,
            "no_expiry": self.expires_at is None,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ArtifactError("retention binding must be an object")
        _strict_fields(
            value, {"class", "owner", "no_expiry", "expires_at"}, "retention binding"
        )
        retention_class = _required_string(value["class"], "retention class")
        owner = _required_string(value["owner"], "retention owner")
        no_expiry = value["no_expiry"]
        if not isinstance(no_expiry, bool):
            raise ArtifactError("retention no-expiry must be a boolean")
        expiry_value = value["expires_at"]
        expiry = (
            None
            if expiry_value is None
            else _required_string(expiry_value, "retention expiry")
        )
        if no_expiry is not (expiry is None):
            raise ArtifactError("retention no-expiry and expiry disagree")
        return cls(retention_class=retention_class, owner=owner, expires_at=expiry)


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["artifact-manifest"]
    family: str
    generation_id: str
    run_id: str | None
    parents: tuple[ParentManifestBinding, ...]
    generator: GeneratorBinding
    sources: tuple[SourceIdentity, ...]
    artifact_records: tuple[ArtifactRecord, ...]
    retention: RetentionBinding
    completion_state: Literal["complete"]
    manifest_identity: str

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._validate_contract()

    def _validate_contract(self) -> None:
        self._validate_header()
        _plain_token(self.family, "family")
        _plain_token(self.generation_id, "generation ID")
        if self.run_id is not None and _RUN_ID.fullmatch(self.run_id) is None:
            raise ArtifactError("run ID is invalid")
        self._validate_artifact_records()
        self._validate_sources()
        self._validate_identity()

    def _validate_header(self) -> None:
        valid = (
            self.schema_version == 1
            and self.record_type == "artifact-manifest"
            and self.completion_state == "complete"
        )
        if not valid:
            raise ArtifactError("manifest schema, type, or completion state is invalid")

    def _validate_artifact_records(self) -> None:
        paths = [item.relative_path for item in self.artifact_records]
        if not paths or len(paths) != len(set(paths)):
            raise ArtifactPathError(
                "artifact paths must be nonempty and contain no duplicate"
            )

    def _validate_sources(self) -> None:
        if len({item.name for item in self.sources}) != len(self.sources):
            raise ArtifactError("source identity names must be unique")

    def _validate_identity(self) -> None:
        expected = _sha256(_canonical_bytes(self.identity_payload()))
        if self.manifest_identity != expected:
            raise ArtifactConflictError(
                "manifest identity does not match canonical content"
            )

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type,
            "family": self.family,
            "generation_id": self.generation_id,
            "run_id": self.run_id,
            "parents": [item.to_dict() for item in self.parents],
            "generator": self.generator.to_dict(),
            "sources": [item.to_dict() for item in self.sources],
            "artifact_records": [item.to_dict() for item in self.artifact_records],
            "retention": self.retention.to_dict(),
            "completion_state": self.completion_state,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.identity_payload(), "manifest_identity": self.manifest_identity}

    @classmethod
    def create(
        cls,
        *,
        family: str,
        generation_id: str,
        run_id: str | None,
        parents: tuple[ParentManifestBinding, ...],
        generator: GeneratorBinding,
        sources: tuple[SourceIdentity, ...],
        artifact_records: tuple[ArtifactRecord, ...],
        retention: RetentionBinding,
    ) -> Self:
        payload: dict[str, object] = {
            "schema_version": 1,
            "record_type": "artifact-manifest",
            "family": family,
            "generation_id": generation_id,
            "run_id": run_id,
            "parents": parents,
            "generator": generator,
            "sources": sources,
            "artifact_records": artifact_records,
            "retention": retention,
            "completion_state": "complete",
        }
        identity_payload = {
            **payload,
            "parents": [item.to_dict() for item in parents],
            "generator": generator.to_dict(),
            "sources": [item.to_dict() for item in sources],
            "artifact_records": [item.to_dict() for item in artifact_records],
            "retention": retention.to_dict(),
        }
        identity = _sha256(_canonical_bytes(identity_payload))
        return cls(
            schema_version=1,
            record_type="artifact-manifest",
            family=family,
            generation_id=generation_id,
            run_id=run_id,
            parents=parents,
            generator=generator,
            sources=sources,
            artifact_records=artifact_records,
            retention=retention,
            completion_state="complete",
            manifest_identity=identity,
        )

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ArtifactError("manifest must be an object")
        fields = {
            "schema_version",
            "record_type",
            "family",
            "generation_id",
            "run_id",
            "parents",
            "generator",
            "sources",
            "artifact_records",
            "retention",
            "completion_state",
            "manifest_identity",
        }
        _strict_fields(value, fields, "manifest")
        parents = _required_list(value["parents"], "manifest parents")
        sources = _required_list(value["sources"], "manifest sources")
        records = _required_list(value["artifact_records"], "manifest artifacts")
        return cls(
            schema_version=value["schema_version"],
            record_type=value["record_type"],
            family=value["family"],
            generation_id=value["generation_id"],
            run_id=value["run_id"],
            parents=tuple(ParentManifestBinding.from_dict(item) for item in parents),
            generator=GeneratorBinding.from_dict(value["generator"]),
            sources=tuple(SourceIdentity.from_dict(item) for item in sources),
            artifact_records=tuple(ArtifactRecord.from_dict(item) for item in records),
            retention=RetentionBinding.from_dict(value["retention"]),
            completion_state=value["completion_state"],
            manifest_identity=value["manifest_identity"],
        )


class ArtifactUnavailableRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["unavailable-artifact"]
    family: str
    run_id: str
    expected_sha256: str
    last_known_path: str
    reason: Literal["overwritten-before-immutable-retention"]
    references: tuple[str, ...]

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._validate_header()
        self._validate_identity()
        self._validate_references()

    def _validate_header(self) -> None:
        valid = (
            self.schema_version == 1
            and self.record_type == "unavailable-artifact"
            and self.reason == "overwritten-before-immutable-retention"
        )
        if not valid:
            raise ArtifactError("unavailable record schema is invalid")

    def _validate_identity(self) -> None:
        _plain_token(self.family, "family")
        if _RUN_ID.fullmatch(self.run_id) is None:
            raise ArtifactError("unavailable run or digest is invalid")
        if _SHA256.fullmatch(self.expected_sha256) is None:
            raise ArtifactError("unavailable run or digest is invalid")
        _relative_path(self.last_known_path)

    def _validate_references(self) -> None:
        if any(
            not value or _contains_control_character(value) for value in self.references
        ):
            raise ArtifactError("unavailable references are invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type,
            "family": self.family,
            "run_id": self.run_id,
            "expected_sha256": self.expected_sha256,
            "last_known_path": self.last_known_path,
            "reason": self.reason,
            "references": list(self.references),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ArtifactError("unavailable record must be an object")
        fields = {
            "schema_version",
            "record_type",
            "family",
            "run_id",
            "expected_sha256",
            "last_known_path",
            "reason",
            "references",
        }
        _strict_fields(value, fields, "unavailable record")
        if not isinstance(value["references"], list) or any(
            not isinstance(item, str) for item in value["references"]
        ):
            raise ArtifactError("unavailable references must be strings")
        return cls(**{**value, "references": tuple(value["references"])})


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PartialGenerationError(f"artifact record cannot be read: {path}") from exc
    if not isinstance(value, dict):
        raise ArtifactError("artifact record must be an object")
    return value


def load_artifact_record(path: Path) -> ArtifactManifest | ArtifactUnavailableRecord:
    value = _load_json(path)
    if value.get("record_type") == "artifact-manifest":
        return ArtifactManifest.from_dict(value)
    if value.get("record_type") == "unavailable-artifact":
        return ArtifactUnavailableRecord.from_dict(value)
    raise ArtifactError("unknown artifact record type")


def _regular_source(path: Path) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ArtifactPathError(f"artifact source is unavailable: {path}") from exc
    if stat.S_ISLNK(details.st_mode):
        raise ArtifactPathError(f"artifact source is a symlink: {path}")
    if not stat.S_ISREG(details.st_mode):
        raise ArtifactPathError(f"artifact source is not a regular file: {path}")
    return details


def _record_source(path: Path, relative: str) -> ArtifactRecord:
    details = _regular_source(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    after = _regular_source(path)
    if (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ArtifactConflictError("artifact source changed while being recorded")
    return ArtifactRecord(
        relative_path=relative,
        size=details.st_size,
        sha256=digest.hexdigest(),
    )


def _write_fsynced(path: Path, data: bytes) -> None:
    # This is intentionally separate from replacement-style atomic_write helpers:
    # immutable generation publication must fail if any destination already exists.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_complete(
    directory: Path, expected: ArtifactManifest | None = None
) -> ArtifactManifest:
    loaded = _load_completed_manifest(directory)
    _verify_manifest_artifacts(directory, loaded)
    _verify_generation_inventory(directory, loaded)
    if expected is not None and loaded != expected:
        raise ArtifactConflictError("existing complete generation differs")
    return loaded


def _load_completed_manifest(directory: Path) -> ArtifactManifest:
    marker = directory / _COMPLETE
    manifest_path = directory / _MANIFEST
    try:
        _regular_source(marker)
        _regular_source(manifest_path)
    except ArtifactPathError as exc:
        raise PartialGenerationError(f"generation is incomplete: {directory}") from exc
    loaded = load_artifact_record(manifest_path)
    if not isinstance(loaded, ArtifactManifest):
        raise ArtifactConflictError("generation manifest has the wrong record type")
    if marker.read_text(encoding="ascii") != loaded.manifest_identity + "\n":
        raise ArtifactConflictError("completion marker and manifest identity differ")
    return loaded


def _verify_manifest_artifacts(directory: Path, manifest: ArtifactManifest) -> None:
    for record in manifest.artifact_records:
        path = directory / record.relative_path
        observed = _record_source(path, record.relative_path)
        if observed != record:
            raise ArtifactConflictError(
                f"artifact bytes differ: {record.relative_path}"
            )


def _verify_generation_inventory(directory: Path, manifest: ArtifactManifest) -> None:
    expected_files = {
        _COMPLETE,
        _MANIFEST,
        _CLAIM,
        *(record.relative_path for record in manifest.artifact_records),
    }
    observed_files = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    }
    if observed_files != expected_files:
        raise ArtifactConflictError("generation file inventory differs")


def _acquire_generation_claim(family_root: Path, generation_id: str) -> Path:
    claims = family_root / ".claims"
    claims.mkdir(exist_ok=True)
    claim = claims / generation_id
    deadline = time.monotonic() + _CONCURRENT_WAIT_SECONDS
    while True:
        try:
            claim.mkdir()
            return claim
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ArtifactConflictError(
                    "concurrent generation claim did not complete"
                ) from None
            time.sleep(0.01)


def publish_generation(
    *,
    artifacts_root: Path,
    family: str,
    generation_id: str,
    run_id: str | None,
    artifact_sources: dict[str, Path],
    parents: tuple[ParentManifestBinding, ...],
    generator: GeneratorBinding,
    sources: tuple[SourceIdentity, ...],
    retention: RetentionBinding,
) -> ArtifactManifest:
    """Publish one immutable generation; identical retries are read-only."""
    _plain_token(family, "family")
    _plain_token(generation_id, "generation ID")
    normalized = [
        (_artifact_relative_path(relative), path)
        for relative, path in artifact_sources.items()
    ]
    if len(normalized) != len({relative for relative, _ in normalized}):
        raise ArtifactPathError("artifact paths contain a duplicate")
    family_root = artifacts_root / family
    staging_root = family_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{generation_id}-", dir=staging_root))
    final = family_root / generation_id
    claim: Path | None = None
    try:
        _stage_artifacts(staging, normalized)
        records = tuple(
            _record_source(staging / relative, relative)
            for relative, _source in normalized
        )
        manifest = ArtifactManifest.create(
            family=family,
            generation_id=generation_id,
            run_id=run_id,
            parents=parents,
            generator=generator,
            sources=sources,
            artifact_records=records,
            retention=retention,
        )
        manifest_bytes = _canonical_bytes(manifest.to_dict())
        _write_fsynced(staging / _MANIFEST, manifest_bytes)
        _fsync_directory(staging)
        claim = _acquire_generation_claim(family_root, generation_id)
        return _publish_claimed_generation(
            final=final,
            family_root=family_root,
            staging=staging,
            normalized=normalized,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if claim is not None:
            claim.rmdir()


def _stage_artifacts(staging: Path, sources: list[tuple[str, Path]]) -> None:
    for relative, source in sources:
        _regular_source(source)
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _write_fsynced(destination, source.read_bytes())


def _publish_claimed_generation(
    *,
    final: Path,
    family_root: Path,
    staging: Path,
    normalized: list[tuple[str, Path]],
    manifest: ArtifactManifest,
    manifest_bytes: bytes,
) -> ArtifactManifest:
    try:
        final.mkdir()
    except FileExistsError:
        return _verify_complete(final, manifest)
    identity_bytes = manifest.manifest_identity.encode("ascii") + b"\n"
    _write_fsynced(final / _CLAIM, identity_bytes)
    _copy_staged_artifacts(final, staging, normalized)
    _write_fsynced(final / _MANIFEST, manifest_bytes)
    _fsync_directory(final)
    _write_fsynced(final / _COMPLETE, identity_bytes)
    _fsync_directory(final)
    _fsync_directory(family_root)
    return _verify_complete(final, manifest)


def _copy_staged_artifacts(
    final: Path, staging: Path, normalized: list[tuple[str, Path]]
) -> None:
    for relative, _source in normalized:
        source = staging / relative
        destination = final / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _write_fsynced(destination, source.read_bytes())
        _fsync_directory(destination.parent)


def resolve_parent_manifest(
    path: Path, expected_identity: str | None = None
) -> ArtifactManifest:
    """Resolve and byte-verify an exact completed parent generation."""
    directory = path.parent
    loaded = _verify_complete(directory)
    if path.name != _MANIFEST or (
        expected_identity is not None and loaded.manifest_identity != expected_identity
    ):
        raise ArtifactConflictError("parent manifest identity or path differs")
    return loaded


def write_unavailable_record(path: Path, record: ArtifactUnavailableRecord) -> None:
    """Write an honest create-only record that deliberately has no artifact locator."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical_bytes(record.to_dict())
    try:
        _write_fsynced(path, data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ArtifactConflictError("existing unavailable record differs") from None
    _fsync_directory(path.parent)


def _replace_reconciled_record(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _reference_reconciliation_bytes(
    expected_stale: ArtifactUnavailableRecord,
    corrected: ArtifactUnavailableRecord,
) -> tuple[bytes, bytes]:
    stale_payload = expected_stale.to_dict()
    corrected_payload = corrected.to_dict()
    stale_references = stale_payload.pop("references")
    corrected_references = corrected_payload.pop("references")
    if (
        stale_references != []
        or not isinstance(corrected_references, list)
        or not corrected_references
        or stale_payload != corrected_payload
    ):
        raise ArtifactConflictError(
            "unavailable reconciliation may only add missing references"
        )
    return _canonical_bytes(expected_stale.to_dict()), _canonical_bytes(
        corrected.to_dict()
    )


def _write_superseded_audit(path: Path, stale_bytes: bytes) -> Path:
    audit = path.parent / "superseded" / f"{_sha256(stale_bytes)}.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit_directory = audit.parent.lstat()
    if stat.S_ISLNK(audit_directory.st_mode) or not stat.S_ISDIR(
        audit_directory.st_mode
    ):
        raise ArtifactConflictError("unavailable reconciliation audit is unsafe")
    _fsync_directory(path.parent)
    try:
        _write_fsynced(audit, stale_bytes)
    except FileExistsError:
        if audit.read_bytes() != stale_bytes:
            raise ArtifactConflictError(
                "unavailable reconciliation audit differs"
            ) from None
    _fsync_directory(audit.parent)
    return audit


def _read_reconciliation_target(path: Path) -> bytes:
    try:
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise ArtifactConflictError(
                "unavailable reconciliation target is not a regular file"
            )
        return path.read_bytes()
    except OSError as exc:
        raise ArtifactConflictError(
            "unavailable record is absent during reconciliation"
        ) from exc


def _verify_existing_reconciliation_audit(
    audit: Path, stale_bytes: bytes
) -> Path | None:
    if not audit.exists():
        return None
    if audit.read_bytes() != stale_bytes:
        raise ArtifactConflictError("unavailable reconciliation audit differs")
    return audit


def _replace_unavailable_record(
    path: Path, stale_bytes: bytes, corrected_bytes: bytes
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".reconcile", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(corrected_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != stale_bytes:
            raise ArtifactConflictError("unavailable reconciliation changed during CAS")
        _replace_reconciled_record(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile_missing_unavailable_references(
    *,
    path: Path,
    expected_stale: ArtifactUnavailableRecord,
    corrected: ArtifactUnavailableRecord,
) -> Path | None:
    """CAS-repair only an empty-reference unavailable record, preserving old bytes."""
    stale_bytes, corrected_bytes = _reference_reconciliation_bytes(
        expected_stale, corrected
    )
    current = _read_reconciliation_target(path)
    audit = path.parent / "superseded" / f"{_sha256(stale_bytes)}.json"
    if current == corrected_bytes:
        return _verify_existing_reconciliation_audit(audit, stale_bytes)
    if current != stale_bytes:
        raise ArtifactConflictError("unavailable reconciliation CAS identity differs")
    audit = _write_superseded_audit(path, stale_bytes)
    _replace_unavailable_record(path, stale_bytes, corrected_bytes)
    if path.read_bytes() != corrected_bytes:
        raise ArtifactConflictError("unavailable reconciliation replacement differs")
    return audit


def _embedded_run_ids(path: Path) -> set[str]:
    run_ids: set[str] = set()
    overlap = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            data = overlap + chunk
            run_ids.update(
                match.decode("ascii")
                for match in re.findall(_RUN_ID.pattern.encode(), data)
            )
            overlap = data[-80:]
    return run_ids


def write_legacy_in_place_manifest(
    *,
    path: Path,
    repository_root: Path,
    artifact_path: Path,
    run_id: str,
    persisted_representation_identity: str,
    persisted_artifact_path: str,
    source_identity: str,
    generator: GeneratorBinding,
) -> ArtifactManifest:
    """Record, without moving it, an existing DB-bound critical corpus artifact."""
    relative = _legacy_relative_path(repository_root, artifact_path)
    record = _record_source(artifact_path, relative)
    _verify_legacy_binding(
        artifact_path=artifact_path,
        record=record,
        run_id=run_id,
        persisted_representation_identity=persisted_representation_identity,
        persisted_artifact_path=persisted_artifact_path,
    )
    if path.exists():
        _regular_source(path)
        loaded = load_artifact_record(path)
        if not isinstance(loaded, ArtifactManifest):
            raise ArtifactConflictError("existing legacy sidecar has the wrong type")
        expected_existing = _legacy_manifest(
            artifact_path=artifact_path,
            run_id=run_id,
            persisted_artifact_path=persisted_artifact_path,
            source_identity=source_identity,
            generator=loaded.generator,
            record=record,
        )
        if loaded != expected_existing:
            raise ArtifactConflictError("existing legacy sidecar differs")
        return loaded
    manifest = _legacy_manifest(
        artifact_path=artifact_path,
        run_id=run_id,
        persisted_artifact_path=persisted_artifact_path,
        source_identity=source_identity,
        generator=generator,
        record=record,
    )
    data = _canonical_bytes(manifest.to_dict())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_fsynced(path, data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ArtifactConflictError("existing legacy sidecar differs") from None
    _fsync_directory(path.parent)
    return manifest


def _legacy_manifest(
    *,
    artifact_path: Path,
    run_id: str,
    persisted_artifact_path: str,
    source_identity: str,
    generator: GeneratorBinding,
    record: ArtifactRecord,
) -> ArtifactManifest:
    manifest = ArtifactManifest.create(
        family="legacy-in-place",
        generation_id=f"{artifact_path.stem}-{run_id}",
        run_id=run_id,
        parents=(),
        generator=generator,
        sources=(
            SourceIdentity(name="ncit", identity=source_identity),
            SourceIdentity(
                name="persisted-artifact-path", identity=persisted_artifact_path
            ),
        ),
        artifact_records=(record,),
        retention=RetentionBinding(
            retention_class="critical-full-corpus",
            owner="decomposition",
            expires_at=None,
        ),
    )
    return manifest


def _legacy_relative_path(repository_root: Path, artifact_path: Path) -> str:
    root = repository_root.resolve()
    artifact = artifact_path.resolve()
    if root not in artifact.parents:
        raise ArtifactPathError("legacy artifact is outside the repository")
    return artifact.relative_to(root).as_posix()


def _verify_legacy_binding(
    *,
    artifact_path: Path,
    record: ArtifactRecord,
    run_id: str,
    persisted_representation_identity: str,
    persisted_artifact_path: str,
) -> None:
    if _embedded_run_ids(artifact_path) != {run_id}:
        raise ArtifactConflictError("legacy artifact run membership differs")
    if record.sha256 != persisted_representation_identity:
        raise ArtifactConflictError(
            "legacy artifact digest differs from persisted identity"
        )
    if not persisted_artifact_path or _contains_control_character(
        persisted_artifact_path
    ):
        raise ArtifactConflictError("legacy persisted artifact path is invalid")
