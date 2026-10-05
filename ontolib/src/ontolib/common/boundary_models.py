"""Shared strict roots for serialized and externally validated documents."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class StrictBoundaryModel(BaseModel):
    """Boundary document that forbids coercion and unknown fields."""

    model_config = ConfigDict(strict=True, extra="forbid")


class StrictFrozenBoundaryModel(StrictBoundaryModel):
    """Immutable boundary document."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


def pydantic_json_default(value: object) -> object:
    """Convert a Pydantic model for ``json.dumps(default=...)``."""
    if not isinstance(value, BaseModel):
        raise TypeError(f"not a Pydantic model: {type(value).__name__}")
    return value.model_dump(mode="json")


def canonical_json_bytes(
    value: object, *, default: Callable[[object], object] | None = None
) -> bytes:
    """Canonical compact ASCII-safe JSON bytes used for content identities."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=default,
    ).encode("ascii")


def sha256_hex(value: bytes) -> str:
    """Lowercase SHA-256 hex digest of exact bytes."""
    return hashlib.sha256(value).hexdigest()


def canonical_json_sha256(
    value: object, *, default: Callable[[object], object] | None = None
) -> str:
    """SHA-256 over :func:`canonical_json_bytes`."""
    return sha256_hex(canonical_json_bytes(value, default=default))


def pydantic_json_sha256(value: object) -> str:
    """Canonical JSON SHA-256 with Pydantic model conversion."""
    return canonical_json_sha256(value, default=pydantic_json_default)


def canonical_json_line_bytes(value: object) -> bytes:
    """Canonical compact JSON bytes terminated by one newline."""
    return canonical_json_bytes(value) + b"\n"


def file_sha256(path: Path) -> str:
    """Stream a file and return its lowercase SHA-256 hex digest."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
