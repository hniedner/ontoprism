"""Fail-closed contracts for retired full-ontology HTTP loading."""

from pathlib import Path
from typing import Any

import pytest

from ontolib.terminologies.ncit.owl_load import (
    FullOntologyHttpLoadForbiddenError,
    build_ncit_store,
    load_owl_file,
)


class _RecordingClient:
    def __init__(self) -> None:
        self.loaded = False

    async def load(self, *_args: Any, **_kwargs: Any) -> None:
        self.loaded = True


@pytest.mark.unit
async def test_load_owl_file_fails_before_http_mutation(tmp_path: Path) -> None:
    owl = tmp_path / "Thesaurus-inferred.owl"
    owl.write_bytes(b"large ontology")
    client = _RecordingClient()

    with pytest.raises(
        FullOntologyHttpLoadForbiddenError, match="validated sibling store offline"
    ):
        await load_owl_file(client, owl)  # type: ignore[arg-type]

    assert client.loaded is False


@pytest.mark.unit
async def test_build_ncit_store_fails_before_download_or_http_mutation(
    tmp_path: Path,
) -> None:
    client = _RecordingClient()

    with pytest.raises(
        FullOntologyHttpLoadForbiddenError, match="validated sibling store offline"
    ):
        await build_ncit_store(client, tmp_path)  # type: ignore[arg-type]

    assert client.loaded is False
