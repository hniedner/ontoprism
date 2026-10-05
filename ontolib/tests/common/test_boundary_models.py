from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from ontolib.common.boundary_models import (
    StrictFrozenBoundaryModel,
    canonical_json_bytes,
    canonical_json_sha256,
    file_sha256,
    pydantic_json_default,
    sha256_hex,
)


class _ExampleBoundary(StrictFrozenBoundaryModel):
    count: int


@pytest.mark.unit
def test_strict_frozen_boundary_rejects_coercion_unknown_fields_and_mutation() -> None:
    value = _ExampleBoundary(count=1)

    with pytest.raises(ValidationError):
        _ExampleBoundary.model_validate({"count": "1"})
    with pytest.raises(ValidationError):
        _ExampleBoundary.model_validate({"count": 1, "unknown": True})
    with pytest.raises(ValidationError):
        value.count = 2


@pytest.mark.unit
def test_canonical_json_and_byte_digests_use_the_shared_exact_encoding() -> None:
    payload = {"unicode": "é", "nested": {"b": 2, "a": 1}}
    expected = b'{"nested":{"a":1,"b":2},"unicode":"\\u00e9"}'

    assert canonical_json_bytes(payload) == expected
    assert canonical_json_sha256(payload) == hashlib.sha256(expected).hexdigest()
    assert sha256_hex(b"payload") == hashlib.sha256(b"payload").hexdigest()


@pytest.mark.unit
def test_pydantic_json_default_rejects_non_models() -> None:
    with pytest.raises(TypeError, match="not a Pydantic model"):
        pydantic_json_default({"not": "a model"})


@pytest.mark.unit
def test_file_sha256_streams_the_same_digest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"abc" * 500_000)

    assert file_sha256(artifact) == hashlib.sha256(artifact.read_bytes()).hexdigest()
