from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.v1.ncit import MappingEntry
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH

pytestmark = pytest.mark.unit


def _entry(**changes: object) -> MappingEntry:
    values: dict[str, object] = {
        "object_id": "UBERON:0002046",
        "system": "uberon",
        "version": "2026-06-19",
        "predicate": EXACT_MATCH,
        "lifecycle": "validated",
        "confidence": 0.95,
    }
    values.update(changes)
    return MappingEntry.model_validate(values)


@pytest.mark.parametrize(
    ("predicate", "lifecycle", "expected"),
    [
        (EXACT_MATCH, "validated", True),
        (EXACT_MATCH, "active", True),
        (EXACT_MATCH, "quarantined", False),
        (CLOSE_MATCH, "validated", False),
    ],
)
def test_mapping_entry_serializes_identity_as_computed_invariant(
    predicate: str, lifecycle: str, expected: bool
) -> None:
    entry = _entry(predicate=predicate, lifecycle=lifecycle)

    assert entry.is_identity is expected
    assert entry.model_dump(mode="json")["is_identity"] is expected


def test_mapping_entry_rejects_caller_supplied_identity() -> None:
    with pytest.raises(ValidationError) as error:
        _entry(is_identity=False)

    assert error.value.errors(include_url=False)[0]["loc"] == ()
    assert error.value.errors(include_url=False)[0]["msg"] == (
        "Value error, is_identity must match predicate and lifecycle"
    )


def test_mapping_entry_serialized_output_roundtrips_exactly() -> None:
    entry = _entry()

    assert MappingEntry.model_validate(entry.model_dump(mode="json")) == entry
