from __future__ import annotations

from typing import Any

import pytest

from ontolib.repositories.xref.publisher_xref import (
    EXPECTED_ASSERTIONS,
    EXPECTED_SOURCE_CLASSES,
    PublisherXrefCountDriftError,
    PublisherXrefSourceError,
    _parse_assertions,
)
from ontolib.repositories.xref.publisher_xref import (
    publish_uberon_xrefs as _publish_uberon_xrefs,
)
from ontolib.repositories.xref.vocab import CLOSE_MATCH, DATABASE_CROSS_REFERENCE


async def publish_uberon_xrefs(*args: object, **kwargs: object) -> Any:
    kwargs.setdefault("ncit_source_identity", "a" * 64)
    kwargs.setdefault("uberon_source_identity", "b" * 64)
    kwargs.setdefault("uberon_serving_identity", "c" * 64)
    kwargs.setdefault("expected_ncit_version", _NCIT_VERSION)
    kwargs.setdefault("expected_uberon_version", _UBERON_VERSION)
    return await _publish_uberon_xrefs(*args, **kwargs)  # type: ignore[arg-type]


_UBERON_VERSION = "http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"
_CL_VERSION = "http://purl.obolibrary.org/obo/cl/releases/2026-06-08/cl.owl"
_NCIT_VERSION = "26.07d"


class _Client:
    def __init__(self, xrefs: list[dict[str, str]], resolved: set[str]) -> None:
        self.xrefs = xrefs
        self.resolved = resolved
        self.select_calls: list[str] = []
        self.loads: list[tuple[bytes, str]] = []

    async def select(self, query: str) -> list[dict[str, str]]:
        self.select_calls.append(query)
        if "active/" in query:
            return []
        if "SELECT DISTINCT ?location ?version" in query:
            return [
                {"location": "default", "version": _NCIT_VERSION},
                {"location": "stated", "version": _NCIT_VERSION},
            ]
        if "SELECT DISTINCT ?ontology ?version" in query:
            return [
                {
                    "ontology": "http://purl.obolibrary.org/obo/uberon.owl",
                    "version": _UBERON_VERSION,
                },
                {
                    "ontology": "http://purl.obolibrary.org/obo/cl.owl",
                    "version": _CL_VERSION,
                },
            ]
        if "hasDbXref" in query:
            return self.xrefs
        return [{"code": code} for code in sorted(self.resolved)]

    async def version(self) -> str:
        return _NCIT_VERSION

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


@pytest.mark.unit
def test_publisher_xref_refuses_unsupported_concept_namespace() -> None:
    with pytest.raises(PublisherXrefSourceError, match="unsupported publisher concept"):
        _parse_assertions(
            [{"upstream": "https://example.test/not-uberon", "xref": "NCIT:C1"}]
        )


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
        self.run_writes = 0

    async def upsert_run(self, **kwargs: object) -> int:
        self.run_writes += 1
        self.source = str(kwargs["source"])
        return 1

    async def update_run_metrics(self, _run_id: str, metrics: dict[str, Any]) -> None:
        self.metrics = metrics

    async def active_generation(self, _source: str) -> str | None:
        return None

    async def set_active_generation(self, *_args: object, **_kwargs: object) -> None:
        return None

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
        {
            "upstream": "http://purl.obolibrary.org/obo/CL_0000057",
            "xref": "NCIT:C12468",
        },
    ]
    uberon = _Client(xrefs, set())
    ncit = _Client([], {"C12468"})
    store = _Store()

    report = await publish_uberon_xrefs(
        store,
        ncit,
        uberon,
        expected_counts=(3, 4),
        run_id="publisher-run",
    )

    assert (
        len([query for query in ncit.select_calls if "VALUES ?concept" in query]) == 2
    )
    assert {(r.subject_id, r.object_id) for r in store.records} == {
        ("UBERON:0000171", "C12468"),
        ("UBERON:0002048", "C12468"),
        ("CL:0000057", "C12468"),
    }
    assert {r.subject_id: r.subject_source_version for r in store.records} == {
        "UBERON:0000171": _UBERON_VERSION,
        "UBERON:0002048": _UBERON_VERSION,
        "CL:0000057": _CL_VERSION,
    }
    assert all(
        r.subject_system == "uberon-cl"
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
        "uberon_assertion_identity": report.uberon_assertion_identity,
        "ncit_target_identity": report.ncit_target_identity,
        "published_assertion_count": 3,
        "unresolved": [
            {
                "uberon_id": "UBERON:0002048",
                "ncit_id": "C99999",
                "reason": "ncit-target-not-found",
            }
        ],
        "source_class_count": {
            "expected": 3,
            "observed": 3,
            "delta": 0,
            "classification": "unchanged",
        },
        "assertion_count": {
            "expected": 4,
            "observed": 4,
            "delta": 0,
            "classification": "unchanged",
        },
    }
    assert store.metrics == report.model_dump(mode="json")
    assert store.source == "uberon-publisher-xref"
    assert len(ncit.loads) == 2


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
        )

    assert ncit.select_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("classes", "assertions", "class_kind", "assertion_kind"),
    [
        (EXPECTED_SOURCE_CLASSES, EXPECTED_ASSERTIONS, "unchanged", "unchanged"),
        (
            EXPECTED_SOURCE_CLASSES + 1,
            EXPECTED_ASSERTIONS,
            "increased",
            "unchanged",
        ),
        (
            EXPECTED_SOURCE_CLASSES - 1,
            EXPECTED_ASSERTIONS - 1,
            "decreased",
            "decreased",
        ),
    ],
)
async def test_publisher_count_drift_is_independent_and_precedes_all_writes(
    classes: int, assertions: int, class_kind: str, assertion_kind: str
) -> None:
    rows = [
        {
            "upstream": f"http://purl.obolibrary.org/obo/UBERON_{index:07d}",
            "xref": f"NCIT:C{index + 1}",
        }
        for index in range(classes)
    ]
    rows.extend(rows[: assertions - classes])
    rows = rows[:assertions]
    store = _Store()
    if (classes, assertions) == (EXPECTED_SOURCE_CLASSES, EXPECTED_ASSERTIONS):
        await publish_uberon_xrefs(
            store,
            _Client([], set()),
            _Client(rows, set()),
            expected_counts=(EXPECTED_SOURCE_CLASSES, EXPECTED_ASSERTIONS),
        )
        assert store.run_writes == 1
        return
    with pytest.raises(PublisherXrefCountDriftError) as captured:
        await publish_uberon_xrefs(store, _Client([], set()), _Client(rows, set()))
    assert captured.value.source_class_count.classification == class_kind
    assert captured.value.assertion_count.classification == assertion_kind
    assert store.run_writes == 0


@pytest.mark.unit
async def test_publisher_refuses_nonunique_observed_uberon_release_before_writes() -> (
    None
):
    row = {
        "upstream": "http://purl.obolibrary.org/obo/UBERON_0002048",
        "xref": "NCIT:C12468",
    }
    uberon = _Client([row], set())

    async def _ambiguous(query: str) -> list[dict[str, str]]:
        if "hasDbXref" in query:
            return [row]
        if "SELECT DISTINCT ?location ?version" in query:
            return [
                {"location": "default", "version": _NCIT_VERSION},
                {"location": "stated", "version": _NCIT_VERSION},
            ]
        return [
            {
                "ontology": "http://purl.obolibrary.org/obo/uberon.owl",
                "version": _UBERON_VERSION,
            },
            {
                "ontology": "http://purl.obolibrary.org/obo/uberon.owl",
                "version": (
                    "http://purl.obolibrary.org/obo/uberon/releases/"
                    "2026-05-01/uberon.owl"
                ),
            },
            {
                "ontology": "http://purl.obolibrary.org/obo/cl.owl",
                "version": _CL_VERSION,
            },
        ]

    uberon.select = _ambiguous  # type: ignore[method-assign]
    store = _Store()
    with pytest.raises(PublisherXrefSourceError, match="ambiguous Uberon version"):
        await publish_uberon_xrefs(
            store,
            _Client([], {"C12468"}),
            uberon,
            expected_counts=(1, 1),
        )
    assert store.run_writes == 0


@pytest.mark.unit
async def test_publisher_refuses_source_pointer_switch_during_validation() -> None:
    row = {
        "upstream": "http://purl.obolibrary.org/obo/UBERON_0002048",
        "xref": "NCIT:C12468",
    }
    ncit = _Client([], {"C12468"})
    versions = iter((_NCIT_VERSION, "26.08a"))
    original_select = ncit.select

    async def switching_version(query: str) -> list[dict[str, str]]:
        if "SELECT DISTINCT ?location ?version" in query:
            version = next(versions)
            return [
                {"location": "default", "version": version},
                {"location": "stated", "version": version},
            ]
        return await original_select(query)

    ncit.select = switching_version  # type: ignore[method-assign]
    store = _Store()
    with pytest.raises(PublisherXrefSourceError, match="changed during validation"):
        await publish_uberon_xrefs(
            store,
            ncit,
            _Client([row], set()),
            expected_counts=(1, 1),
        )
    assert store.run_writes == 0


@pytest.mark.unit
async def test_publisher_refuses_same_release_same_count_assertion_mutation() -> None:
    first = {
        "upstream": "http://purl.obolibrary.org/obo/UBERON_0002048",
        "xref": "NCIT:C12468",
    }
    second = {
        "upstream": "http://purl.obolibrary.org/obo/UBERON_0000955",
        "xref": "NCIT:C12468",
    }
    uberon = _Client([first], set())
    observations = iter(([first], [second]))
    original_select = uberon.select

    async def switching_assertions(query: str) -> list[dict[str, str]]:
        if "hasDbXref" in query:
            return next(observations)
        return await original_select(query)

    uberon.select = switching_assertions  # type: ignore[method-assign]
    store = _Store()
    with pytest.raises(PublisherXrefSourceError, match="changed during validation"):
        await publish_uberon_xrefs(
            store,
            _Client([], {"C12468"}),
            uberon,
            expected_counts=(1, 1),
        )
    assert store.run_writes == 0
