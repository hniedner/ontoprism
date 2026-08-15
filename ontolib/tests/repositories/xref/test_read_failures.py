"""Fail-closed contracts for xref read infrastructure faults."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

import pytest
from sqlalchemy.exc import ProgrammingError

from ontolib.repositories.xref.models import (
    UberonReadIdentity,
    UnavailableXrefGenerationError,
    XrefReadPolicy,
)
from ontolib.repositories.xref.store import XrefStore

pytestmark = pytest.mark.unit


class _BrokenSession:
    async def execute(self, _statement: object) -> None:
        raise ProgrammingError("SELECT", {}, Exception("schema drift"))


class _SessionContext(AbstractAsyncContextManager[_BrokenSession]):
    async def __aenter__(self) -> _BrokenSession:
        return _BrokenSession()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def _sessions() -> _SessionContext:
    return _SessionContext()


@pytest.mark.asyncio
async def test_mapping_read_translates_schema_fault_to_typed_unavailable() -> None:
    store = XrefStore(_sessions)  # type: ignore[arg-type]
    expected = XrefReadPolicy(
        uberon=UberonReadIdentity(
            ncit_source_identity="a" * 64,
            uberon_source_identity="b" * 64,
            uberon_serving_identity="c" * 64,
        )
    )

    with pytest.raises(UnavailableXrefGenerationError, match="storage unavailable"):
        await store.mappings_for_identifiers({"C10000"}, expected=expected)
