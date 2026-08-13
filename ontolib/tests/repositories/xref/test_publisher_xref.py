from __future__ import annotations

from typing import Any

import pytest

from ontolib.repositories.xref.publisher_xref import (
    PublisherXrefSourceError,
    publish_uberon_xrefs,
)
from ontolib.repositories.xref.vocab import CLOSE_MATCH, DATABASE_CROSS_REFERENCE

_UBERON_VERSION = "http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"
_NCIT_VERSION = "26.07d"


class _Client:
    def __init__(self, xrefs: list[dict[str, str]], resolved: set[str]) -> None:
        self.xrefs = xrefs
        self.resolved = resolved
        self.select_calls: list[str] = []
        self.loads: list[tuple[bytes, str]] = []

    async def select(self, query: str) -> list[dict[str, str]]:
        self.select_calls.append(query)
        if "hasDbXref" in query:
            return self.xrefs
        return [{"code": code} for code in sorted(self.resolved)]

    async def load(
        self,
        data: bytes,
        *,
        content_type: str,
        graph_iri: str,
        replace: bool,
    ) -> None:
        assert content_type == "text/turtle"
        assert replace is True
        self.loads.append((data, graph_iri))


class _Lock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Store:
    def __init__(self) -> None:
        self.records: list[Any] = []
        self.metrics: dict[str, Any] | None = None
        self.source: str | None = None

    async def upsert_run(self, **kwargs: object) -> int:
        self.source = str(kwargs["source"])
        return 1

    async def update_run_metrics(self, _run_id: str, metrics: dict[str, Any]) -> None:
        self.metrics = metrics

    def publication_lock(self, _source: str) -> _Lock:
        return _Lock()

    async def prepare_generation(self, **kwargs: object) -> bool:
        records = kwargs["records"]
        assert isinstance(records, list)
        self.records = records
        return True

    async def activate_generation(
        self, _source: str, _generation_id: str, **_: object
    ) -> bool:
        return True


@pytest.mark.unit
async def test_publisher_xrefs_validate_once_and_report_unresolved() -> None:
    xrefs = [
        {
            "upstream": "http://purl.obolibrary.org/obo/UBERON_0002048",
            "xref": "NCIT:C12468",
        },
        {
            "upstream": "http://purl.obolibrary.org/obo/UBERON_0000171",
            "xref": "NCIT:C12468",
        },
        {
            "upstream": "http://purl.obolibrary.org/obo/UBERON_0002048",
            "xref": "NCIT:C99999",
        },
    ]
    uberon = _Client(xrefs, set())
    ncit = _Client([], {"C12468"})
    store = _Store()

    report = await publish_uberon_xrefs(
        store,
        ncit,
        uberon,
        ncit_version=_NCIT_VERSION,
        uberon_version=_UBERON_VERSION,
        run_id="publisher-run",
    )

    assert len(ncit.select_calls) == 1
    assert "VALUES ?concept" in ncit.select_calls[0]
    assert {(r.subject_id, r.object_id) for r in store.records} == {
        ("UBERON:0000171", "C12468"),
        ("UBERON:0002048", "C12468"),
    }
    assert all(
        r.subject_system == "uberon-cl"
        and r.subject_source_version == _UBERON_VERSION
        and r.object_system == "ncit"
        and r.object_source_version == _NCIT_VERSION
        and r.predicate_id == CLOSE_MATCH
        and r.lifecycle_state == "proposed"
        and r.mapping_justification == DATABASE_CROSS_REFERENCE
        for r in store.records
    )
    assert report.model_dump(mode="json") == {
        "uberon_release": _UBERON_VERSION,
        "ncit_release": _NCIT_VERSION,
        "source_class_count": 2,
        "assertion_count": 3,
        "published_assertion_count": 2,
        "unresolved": [
            {
                "uberon_id": "UBERON:0002048",
                "ncit_id": "C99999",
                "reason": "ncit-target-not-found",
            }
        ],
        "count_delta": "decreased",
        "source_class_delta": -2575,
        "assertion_delta": -2615,
    }
    assert store.metrics == report.model_dump(mode="json")
    assert store.source == "uberon-publisher-xref"
    assert len(ncit.loads) == 1


@pytest.mark.unit
async def test_publisher_xrefs_fail_closed_on_malformed_source_assertion() -> None:
    uberon = _Client(
        [
            {
                "upstream": "http://purl.obolibrary.org/obo/UBERON_0002048",
                "xref": "NCIT:not-a-code",
            }
        ],
        set(),
    )
    ncit = _Client([], set())

    with pytest.raises(PublisherXrefSourceError, match="malformed NCIt target"):
        await publish_uberon_xrefs(
            _Store(),
            ncit,
            uberon,
            ncit_version=_NCIT_VERSION,
            uberon_version=_UBERON_VERSION,
        )

    assert ncit.select_calls == []
