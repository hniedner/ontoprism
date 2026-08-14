from __future__ import annotations

from typing import Any

import pytest

from ontolib.repositories.icdo.store import CertificationExpectation, IcdoCodeResolution
from ontolib.repositories.xref.p334_alignment import (
    P334AlignmentReport,
    P334CountDriftError,
    P334SourceError,
)
from ontolib.repositories.xref.p334_alignment import (
    publish_p334_alignments as _publish_p334_alignments,
)
from ontolib.repositories.xref.vocab import CLOSE_MATCH, DATABASE_CROSS_REFERENCE


async def publish_p334_alignments(
    *args: object, **kwargs: object
) -> P334AlignmentReport:
    kwargs.setdefault("ncit_source_identity", "a" * 64)
    return await _publish_p334_alignments(*args, **kwargs)  # type: ignore[arg-type]


class _NcitClient:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.select_calls: list[str] = []
        self.loads: list[tuple[bytes, str]] = []

    async def select(self, query: str) -> list[dict[str, str]]:
        self.select_calls.append(query)
        if "active/" in query:
            return []
        return self.rows

    async def version(self) -> str:
        return "26.07d"

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


class _Icdo:
    def __init__(self, resolved: set[str]) -> None:
        self.resolved = resolved
        self.calls: list[set[str]] = []

    async def resolve_active_morphology32_codes(
        self, codes: set[str], expected: CertificationExpectation
    ) -> IcdoCodeResolution:
        assert expected.edition == "3.2"
        self.calls.append(codes)
        return IcdoCodeResolution(
            generation_id="a" * 64,
            serving_sha256="b" * 64,
            resolved_codes=self.resolved,
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
        self.generation_id: str | None = None
        self.run_writes = 0

    async def upsert_run(self, **_kwargs: object) -> int:
        self.run_writes += 1
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
        self.records = list(kwargs["records"])  # type: ignore[arg-type]
        self.generation_id = str(kwargs["generation_id"])
        return True

    async def activate_generation(
        self, _source: str, _generation_id: str, **_: object
    ) -> bool:
        return True


def _row(code: str, value: str) -> dict[str, str]:
    return {
        "concept": f"http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{code}",
        "value": value,
    }


def _expectation() -> CertificationExpectation:
    return CertificationExpectation(
        source_sha256="c" * 64,
        edition="3.2",
        axis="morphology",
        row_count=1143,
        serving_sha256="b" * 64,
    )


@pytest.mark.unit
async def test_p334_refuses_missing_observed_ncit_release_before_icdo_or_writes() -> (
    None
):
    client = _NcitClient([_row("C1", "8000/3")])
    client.version = _none_version  # type: ignore[method-assign]
    store = _Store()
    icdo = _Icdo({"8000/3"})
    with pytest.raises(P334SourceError, match="no release identity"):
        await publish_p334_alignments(
            store,
            client,
            icdo,
            icdo_expected=_expectation(),
            expected_counts=(1, 1),
        )
    assert icdo.calls == []
    assert store.run_writes == 0


async def _none_version() -> None:
    return None


@pytest.mark.unit
async def test_p334_publish_is_batched_typed_many_to_many_and_reports_unresolved() -> (
    None
):
    ncit = _NcitClient(
        [
            _row("C188218", "8248/1"),
            _row("C188218", "8240/3"),
            _row("C188218", "8241/3"),
            _row("C45194", "9680/3"),
            _row("C71720", "9680/3"),
            _row("C26749", "8155/1"),
        ]
    )
    icdo = _Icdo({"8240/3", "8241/3", "8248/1", "9680/3"})
    store = _Store()

    report = await publish_p334_alignments(
        store,
        ncit,
        icdo,
        icdo_expected=_expectation(),
        expected_counts=(4, 6),
        run_id="p334-run",
    )

    p334_queries = [query for query in ncit.select_calls if "P334" in query]
    assert len(p334_queries) == 2
    assert (
        "GRAPH <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl>"
        in p334_queries[0]
    )
    assert "P334" in p334_queries[0]
    assert icdo.calls == [
        {"8240/3", "8241/3", "8248/1", "9680/3", "8155/1"},
        {"8240/3", "8241/3", "8248/1", "9680/3", "8155/1"},
    ]
    assert {(row.subject_id, row.object_id) for row in store.records} == {
        ("C188218", "8240/3"),
        ("C188218", "8241/3"),
        ("C188218", "8248/1"),
        ("C45194", "9680/3"),
        ("C71720", "9680/3"),
    }
    assert all(
        row.subject_system == "ncit"
        and row.subject_source_version == "26.07d"
        and row.object_system == "icdo"
        and row.object_source_version == "3.2"
        and row.predicate_id == CLOSE_MATCH
        and row.lifecycle_state == "proposed"
        and row.mapping_justification == DATABASE_CROSS_REFERENCE
        for row in store.records
    )
    assert report.unresolved[0].model_dump() == {
        "ncit_code": "C26749",
        "icdo_code": "8155/1",
        "reason": "icdo32-morphology-code-not-found",
    }
    assert report.concept_count.model_dump() == {
        "expected": 4,
        "observed": 4,
        "delta": 0,
        "classification": "unchanged",
    }
    assert report.assertion_count.observed == 6
    assert report.published_assertion_count == 5
    assert report.icdo_generation_id == "a" * 64
    assert report.icdo_serving_sha256 == "b" * 64
    assert len(report.ncit_p334_identity) == 64
    assert store.metrics == report.model_dump(mode="json")


@pytest.mark.unit
async def test_p334_generation_is_deterministic_for_source_row_order() -> None:
    rows = [_row("C188218", "8241/3"), _row("C188218", "8240/3")]
    generations: list[str | None] = []
    payloads: list[bytes] = []
    for source_rows in (rows, list(reversed(rows))):
        client = _NcitClient(source_rows)
        store = _Store()
        await publish_p334_alignments(
            store,
            client,
            _Icdo({"8240/3", "8241/3"}),
            icdo_expected=_expectation(),
            expected_counts=(1, 2),
            run_id="same-run",
        )
        generations.append(store.generation_id)
        payloads.append(client.loads[0][0])
    assert generations[0] == generations[1]
    assert payloads[0] == payloads[1]


@pytest.mark.unit
@pytest.mark.parametrize(
    "row",
    [
        {"concept": "https://example.test/C1", "value": "8240/3"},
        _row("not-a-code", "8240/3"),
        {
            "concept": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1",
            "value": "",
        },
    ],
)
async def test_p334_malformed_source_rows_fail_closed_before_icdo_read(
    row: dict[str, str],
) -> None:
    icdo = _Icdo(set())
    with pytest.raises(P334SourceError):
        await publish_p334_alignments(
            _Store(),
            _NcitClient([row]),
            icdo,
            icdo_expected=_expectation(),
        )
    assert icdo.calls == []


@pytest.mark.unit
async def test_p334_publisher_non_code_value_is_explicitly_unresolved() -> None:
    report = await publish_p334_alignments(
        _Store(),
        _NcitClient([_row("C7539", "981-983")]),
        _Icdo(set()),
        icdo_expected=_expectation(),
        expected_counts=(1, 1),
    )
    assert report.unresolved[0].model_dump() == {
        "ncit_code": "C7539",
        "icdo_code": "981-983",
        "reason": "invalid-icdo32-morphology-code",
    }


@pytest.mark.unit
async def test_p334_refuses_ncit_pointer_switch_during_validation() -> None:
    client = _NcitClient([_row("C1", "8000/3")])
    versions = iter(("26.07d", "26.08a"))

    async def switching_version() -> str:
        return next(versions)

    client.version = switching_version  # type: ignore[method-assign]
    store = _Store()
    with pytest.raises(P334SourceError, match="changed during validation"):
        await publish_p334_alignments(
            store,
            client,
            _Icdo({"8000/3"}),
            icdo_expected=_expectation(),
            expected_counts=(1, 1),
        )
    assert store.run_writes == 0


@pytest.mark.unit
async def test_p334_refuses_same_release_same_count_row_mutation() -> None:
    client = _NcitClient([_row("C1", "8000/3")])
    observations = iter(([_row("C1", "8000/3")], [_row("C2", "8000/3")]))

    async def switching_rows(query: str) -> list[dict[str, str]]:
        if "active/" in query:
            return []
        return next(observations)

    client.select = switching_rows  # type: ignore[method-assign]
    store = _Store()
    with pytest.raises(P334SourceError, match="changed during validation"):
        await publish_p334_alignments(
            store,
            client,
            _Icdo({"8000/3"}),
            icdo_expected=_expectation(),
            expected_counts=(1, 1),
        )
    assert store.run_writes == 0


@pytest.mark.unit
@pytest.mark.parametrize("changed_identity", ["generation", "serving"])
async def test_p334_refuses_each_icdo_identity_change_during_validation(
    changed_identity: str,
) -> None:
    class SwitchingIcdo(_Icdo):
        async def resolve_active_morphology32_codes(
            self, codes: set[str], expected: CertificationExpectation
        ) -> IcdoCodeResolution:
            first = not self.calls
            self.calls.append(codes)
            return IcdoCodeResolution(
                generation_id=(
                    "a" * 64 if first or changed_identity != "generation" else "c" * 64
                ),
                serving_sha256=(
                    "b" * 64 if first or changed_identity != "serving" else "d" * 64
                ),
                resolved_codes={"8000/3"},
            )

    store = _Store()
    with pytest.raises(P334SourceError, match="ICD-O source changed"):
        await publish_p334_alignments(
            store,
            _NcitClient([_row("C1", "8000/3")]),
            SwitchingIcdo({"8000/3"}),
            icdo_expected=_expectation(),
            expected_counts=(1, 1),
        )

    assert store.run_writes == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("observed", "classification"),
    [(1161, "unchanged"), (1162, "increased"), (1160, "decreased")],
)
async def test_p334_count_drift_fails_before_any_write_with_typed_independent_deltas(
    observed: int, classification: str
) -> None:
    rows = [
        _row(f"C{index + 1}", f"{index % 10000:04d}/3") for index in range(observed)
    ]
    store = _Store()
    with pytest.raises(P334CountDriftError) as captured:
        await publish_p334_alignments(
            store,
            _NcitClient(rows),
            _Icdo({row["value"] for row in rows}),
            icdo_expected=_expectation(),
        )
    assert captured.value.concept_count.classification == classification
    assert captured.value.assertion_count.classification == (
        "decreased"
        if observed < 1252
        else "unchanged"
        if observed == 1252
        else "increased"
    )
    assert store.run_writes == 0
    assert store.records == []
