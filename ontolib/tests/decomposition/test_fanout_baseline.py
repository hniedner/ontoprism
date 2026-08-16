from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ontolib.decomposition import fanout_baseline as fanout_module
from ontolib.decomposition.fanout_baseline import (
    BASELINE_SCHEMA_VERSION,
    DISCOVERY_ALGORITHM,
    FanoutBaseline,
    FanoutObservation,
    FanoutRerun,
    _CountingClient,
    _validated_rerun_counts,
    baseline_identity,
    discovery_query_identity,
    generate_fanout_baseline,
    highest_fanout_from_discovery_rows,
    load_fanout_baseline,
    observe_highest_fanout,
    rerun_fanout_concept,
    write_fanout_baseline,
)
from ontolib.decomposition.models import (
    CompleteDefinition,
    DefinitionGroup,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
    canonical_definition_fact_id,
    canonical_definition_group_id,
    canonical_source_occurrence_id,
)
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence


def _complete(code: str, facts: int, occurrences: int) -> CompleteDefinition:
    signatures = [f"restriction:R101:C{90000 + index}" for index in range(facts)]
    group_id = canonical_definition_group_id(code, signatures)
    restriction_facts = tuple(
        RestrictionDefinitionFact(
            fact_id=canonical_definition_fact_id(
                code,
                group_id,
                "restriction",
                "R101",
                f"C{90000 + index}",
            ),
            anchor_code=code,
            group_id=group_id,
            depth=0,
            role_code="R101",
            filler_code=f"C{90000 + index}",
        )
        for index in range(facts)
    )
    source_occurrences = tuple(
        SourceDefinitionOccurrence(
            occurrence_id=canonical_source_occurrence_id(
                code,
                restriction_facts[index % facts].fact_id,
                (0, index),
            ),
            root_code=code,
            source_fact_id=restriction_facts[index % facts].fact_id,
            source_group_id=group_id,
            anchor_code=code,
            depth=0,
            role_code="R101",
            filler_code=restriction_facts[index % facts].filler_code,
            structural_path=(0, index),
            member_position=index,
        )
        for index in range(occurrences)
    )
    return CompleteDefinition(
        root_code=code,
        facts=restriction_facts,
        groups=(
            DefinitionGroup(
                group_id=group_id,
                anchor_code=code,
                depth=0,
                child_group_ids=(),
            ),
        ),
        root_group_ids=(group_id,),
        occurrences=source_occurrences,
    )


def _baseline_document() -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "source_identity": "a" * 64,
        "ontology_release": "26.07d",
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "concept_codes": ["C11"],
        "restriction_fact_count": 3,
        "restriction_occurrence_count": 4,
        "scanned_concept_count": 3,
        "discovery_algorithm": DISCOVERY_ALGORITHM,
        "discovery_query_identity": discovery_query_identity(),
        "logical_select_count_budget": 8,
        "select_once_r82_count_budget": 4,
        "baseline_identity": "",
    }
    document["baseline_identity"] = baseline_identity(document)
    return document


def _write_document(path: Path, document: dict[str, object]) -> None:
    document["baseline_identity"] = baseline_identity(document)
    path.write_text(json.dumps(document))


@pytest.mark.unit
async def test_observer_retains_all_exact_ties_and_occurrence_count() -> None:
    definitions = {
        "C10": _complete("C10", facts=2, occurrences=3),
        "C11": _complete("C11", facts=3, occurrences=4),
        "C12": _complete("C12", facts=3, occurrences=4),
    }

    async def read(code: str) -> CompleteDefinition:
        return definitions[code]

    observation = await observe_highest_fanout(
        ("C10", "C12", "C11"),
        read_definition=read,
    )

    assert observation == FanoutObservation(
        concept_codes=("C11", "C12"),
        restriction_fact_count=3,
        restriction_occurrence_count=4,
        scanned_concept_count=3,
    )


@pytest.mark.unit
async def test_observer_uses_fact_count_to_break_occurrence_tie() -> None:
    definitions = {
        "C10": _complete("C10", facts=2, occurrences=4),
        "C11": _complete("C11", facts=3, occurrences=4),
    }

    async def read(code: str) -> CompleteDefinition:
        return definitions[code]

    observation = await observe_highest_fanout(
        definitions,
        read_definition=read,
    )

    assert observation.concept_codes == ("C11",)
    assert observation.restriction_occurrence_count == 4
    assert observation.restriction_fact_count == 3


@pytest.mark.unit
async def test_observer_names_the_source_concept_that_fails() -> None:
    async def read(_code: str) -> CompleteDefinition:
        raise ValueError("malformed definition")

    with pytest.raises(RuntimeError, match="C10 fanout observation failed"):
        await observe_highest_fanout(("C10",), read_definition=read)


@pytest.mark.unit
def test_baseline_loader_rejects_source_or_discovery_drift(tmp_path: Path) -> None:
    baseline = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "source_identity": "a" * 64,
        "ontology_release": "26.07d",
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "concept_codes": ["C11"],
        "restriction_fact_count": 3,
        "restriction_occurrence_count": 4,
        "scanned_concept_count": 3,
        "discovery_algorithm": DISCOVERY_ALGORITHM,
        "discovery_query_identity": discovery_query_identity(),
        "logical_select_count_budget": 8,
        "select_once_r82_count_budget": 4,
    }
    baseline["baseline_identity"] = baseline_identity(baseline)
    path = tmp_path / "fanout.json"
    path.write_text(json.dumps(baseline))

    loaded = load_fanout_baseline(
        path,
        expected_source_identity="a" * 64,
        expected_release="26.07d",
    )
    assert loaded.concept_codes == ("C11",)

    baseline["source_identity"] = "c" * 64
    baseline["baseline_identity"] = baseline_identity(baseline)
    path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match="source identity"):
        load_fanout_baseline(
            path,
            expected_source_identity="a" * 64,
            expected_release="26.07d",
        )

    baseline["source_identity"] = "a" * 64
    baseline["discovery_algorithm"] = "direct-equivalent-class-v0"
    baseline["baseline_identity"] = baseline_identity(baseline)
    path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match="discovery algorithm"):
        load_fanout_baseline(
            path,
            expected_source_identity="a" * 64,
            expected_release="26.07d",
        )


@pytest.mark.unit
def test_baseline_loader_rejects_measured_value_drift(tmp_path: Path) -> None:
    source = Path(__file__).with_name("golden") / "neoplasm-highest-fanout.json"
    document = json.loads(source.read_text())
    document["logical_select_count_budget"] += 1
    path = tmp_path / "drifted.json"
    path.write_text(json.dumps(document))

    with pytest.raises(ValueError, match="baseline identity"):
        load_fanout_baseline(
            path,
            expected_source_identity=document["source_identity"],
            expected_release=document["ontology_release"],
        )


@pytest.mark.unit
def test_discovery_counts_repeated_and_inherited_restriction_occurrences() -> None:
    rows = [
        {"kind": "restriction", "anchor": "C10", "occurrence": "a"},
        {"kind": "restriction", "anchor": "C10", "occurrence": "b"},
        {"kind": "definedGenus", "anchor": "C10", "genus": "C20"},
        {"kind": "restriction", "anchor": "C20", "occurrence": "c"},
        {"kind": "definedGenus", "anchor": "C11", "genus": "C20"},
    ]

    observation = highest_fanout_from_discovery_rows(("C10", "C11"), rows)

    assert observation.concept_codes == ("C10",)
    assert observation.restriction_occurrence_count == 3
    assert observation.scanned_concept_count == 2


@pytest.mark.unit
def test_discovery_fails_closed_on_defined_genus_cycle() -> None:
    rows = [
        {"kind": "definedGenus", "anchor": "C10", "genus": "C20"},
        {"kind": "definedGenus", "anchor": "C20", "genus": "C10"},
    ]

    with pytest.raises(ValueError, match="cycle"):
        highest_fanout_from_discovery_rows(("C10",), rows)


@pytest.mark.unit
def test_baseline_identity_accepts_exact_model_and_mapping_semantics() -> None:
    document = _baseline_document()
    model = FanoutBaseline(
        **cast("dict[str, object]", document)  # type: ignore[arg-type]
    )

    assert baseline_identity(model) == baseline_identity(document)
    changed = dict(document)
    changed["restriction_occurrence_count"] = 5
    assert baseline_identity(changed) != baseline_identity(document)
    with pytest.raises(TypeError, match="baseline or mapping"):
        baseline_identity([document])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"kind": "restriction", "occurrence": "x"}], "invalid anchor"),
        ([{"kind": "restriction", "anchor": "C10"}], "invalid occurrence"),
        ([{"kind": "definedGenus", "anchor": "C10"}], "invalid genus"),
        ([{"kind": "unknown", "anchor": "C10"}], "invalid kind"),
        ([{"kind": "restriction", "anchor": 10, "occurrence": "x"}], "invalid anchor"),
    ],
)
def test_fixture_discovery_rejects_missing_or_invalid_fields(
    rows: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        highest_fanout_from_discovery_rows(
            ("C10",), cast("list[Mapping[str, str | None]]", rows)
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("anchor", "message"),
    [
        (None, "invalid anchor"),
        ("http://example.test/C10", "invalid anchor"),
        (f"{NCIT_NS}X10", "invalid anchor"),
        (f"{NCIT_NS}Cten", "invalid anchor"),
    ],
)
def test_store_discovery_rejects_invalid_root_codes(
    anchor: str | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        highest_fanout_from_discovery_rows(
            ("C10",), [{"anchor": anchor, "rootExpression": "_:root"}]
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"role": None, "target": None}, "no expression"),
        ({"expression": "_:e", "role": f"{NCIT_NS}R1"}, "incomplete"),
        ({"expression": "_:e", "target": f"{NCIT_NS}C2"}, "incomplete"),
        (
            {
                "expression": "_:e",
                "role": f"{NCIT_NS}R1",
                "target": f"{NCIT_NS}C2",
            },
            "no identity",
        ),
        (
            {
                "expression": "_:e",
                "member": "http://example.test/C20",
                "definedExpression": "_:defined",
            },
            "invalid genus",
        ),
    ],
)
def test_store_discovery_rejects_malformed_member_rows(
    row: dict[str, str | None], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        highest_fanout_from_discovery_rows(("C10",), [row])


@pytest.mark.unit
def test_store_discovery_counts_roots_defined_genera_and_nested_expressions() -> None:
    rows = [
        {"anchor": f"{NCIT_NS}C10", "rootExpression": "_:root10"},
        {"anchor": f"{NCIT_NS}C20", "rootExpression": "_:root20"},
        {
            "expression": "_:root10",
            "cell": "_:cell-a",
            "role": f"{NCIT_NS}R1",
            "target": f"{NCIT_NS}C2",
            "member": f"{NCIT_NS}C20",
            "definedExpression": "_:defined20",
            "nestedExpression": "_:nested",
        },
        {
            "expression": "_:nested",
            "cell": "_:cell-b",
            "role": f"{NCIT_NS}R2",
            "target": f"{NCIT_NS}C3",
        },
        {
            "expression": "_:root20",
            "cell": "_:cell-c",
            "role": f"{NCIT_NS}R3",
            "target": f"{NCIT_NS}C4",
            "nestedExpression": "_:nested",
        },
    ]

    observation = highest_fanout_from_discovery_rows(("C20", "C10"), rows)

    assert observation == FanoutObservation(
        concept_codes=("C10",),
        restriction_fact_count=0,
        restriction_occurrence_count=3,
        scanned_concept_count=2,
    )


@pytest.mark.unit
def test_discovery_fails_closed_on_nested_expression_cycle() -> None:
    rows = [
        {"anchor": f"{NCIT_NS}C10", "rootExpression": "_:a"},
        {"expression": "_:a", "nestedExpression": "_:b"},
        {"expression": "_:b", "nestedExpression": "_:a"},
    ]

    with pytest.raises(ValueError, match="nested expressions contain a cycle"):
        highest_fanout_from_discovery_rows(("C10",), rows)


@pytest.mark.unit
async def test_discovery_and_observer_reject_empty_scope() -> None:
    with pytest.raises(ValueError, match="non-empty scope"):
        highest_fanout_from_discovery_rows((), [])

    async def unread(_code: str) -> CompleteDefinition:
        raise AssertionError("empty scope must not read definitions")

    with pytest.raises(ValueError, match="non-empty scope"):
        await observe_highest_fanout((), read_definition=unread)


@pytest.mark.unit
async def test_observer_reports_sorted_progress_and_exact_tie_order() -> None:
    progress: list[tuple[int, int, str]] = []

    async def read(code: str) -> CompleteDefinition:
        return _complete(code, facts=1, occurrences=1)

    observation = await observe_highest_fanout(
        ("C12", "C10", "C11"),
        read_definition=read,
        progress=lambda index, total, code: progress.append((index, total, code)),
    )

    assert observation.concept_codes == ("C10", "C11", "C12")
    assert progress == [(1, 3, "C10"), (2, 3, "C11"), (3, 3, "C12")]


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, frozenset[str]]] = []

    async def select(
        self, query: str, *, required_variables: Collection[str] = ()
    ) -> Sequence[Mapping[str, str | None]]:
        self.calls.append(("select", query, frozenset(required_variables)))
        return [{"value": "selected"}]

    async def select_once(
        self, query: str, *, required_variables: Collection[str] = ()
    ) -> Sequence[Mapping[str, str | None]]:
        self.calls.append(("select_once", query, frozenset(required_variables)))
        return [{"value": "once"}]


@pytest.mark.unit
async def test_counting_client_forwards_and_counts_only_r82_once_queries() -> None:
    delegate = _RecordingClient()
    counted = _CountingClient(delegate)

    selected = await counted.select("SELECT ordinary", required_variables={"value"})
    once = await counted.select_once("SELECT R82", required_variables={"value"})
    await counted.select_once("SELECT R81")

    assert selected == [{"value": "selected"}]
    assert once == [{"value": "once"}]
    assert counted.logical_select_count == 1
    assert counted.select_once_r82_count == 1
    assert delegate.calls == [
        ("select", "SELECT ordinary", frozenset({"value"})),
        ("select_once", "SELECT R82", frozenset({"value"})),
        ("select_once", "SELECT R81", frozenset()),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("decomposition", "message"),
    [
        (None, "did not decompose"),
        (SimpleNamespace(complete_definition=None), "has no complete record"),
    ],
)
async def test_rerun_rejects_missing_decomposition_records(
    monkeypatch: pytest.MonkeyPatch, decomposition: object, message: str
) -> None:
    async def decompose(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(decomposition=decomposition)

    monkeypatch.setattr(fanout_module, "_decompose_one", decompose)

    with pytest.raises(ValueError, match=message):
        await rerun_fanout_concept(_RecordingClient(), "C10")


@pytest.mark.unit
async def test_rerun_returns_exact_definition_and_query_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def decompose(_code: str, client: object, **kwargs: object) -> object:
        counted = cast("_CountingClient", client)
        label_lookup = cast(
            "Callable[[str], Awaitable[str | None]]", kwargs["label_lookup"]
        )
        assert await label_lookup("unmatched label") is None
        await counted.select("logical-1")
        await counted.select("logical-2")
        await counted.select_once("part-of R82")
        await counted.select_once("not-part-of")
        return SimpleNamespace(
            decomposition=SimpleNamespace(
                complete_definition=_complete("C10", facts=2, occurrences=3)
            )
        )

    monkeypatch.setattr(fanout_module, "_decompose_one", decompose)

    rerun = await rerun_fanout_concept(_RecordingClient(), "C10")

    assert rerun == FanoutRerun(
        concept_code="C10",
        restriction_fact_count=2,
        restriction_occurrence_count=3,
        logical_select_count=2,
        select_once_r82_count=1,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reruns", "message"),
    [
        (
            [FanoutRerun("C10", 2, 2, 4, 1)],
            "discovery and production occurrence counts disagree",
        ),
        (
            [
                FanoutRerun("C10", 2, 3, 4, 1),
                FanoutRerun("C11", 3, 3, 5, 2),
            ],
            "tied occurrence maxima have different fact counts",
        ),
    ],
)
def test_rerun_validation_rejects_discovery_or_tied_fact_mismatch(
    reruns: list[FanoutRerun], message: str
) -> None:
    observation = FanoutObservation(("C10",), 0, 3, 1)

    with pytest.raises(ValueError, match=message):
        _validated_rerun_counts(observation, reruns)


@pytest.mark.unit
def test_rerun_validation_uses_largest_independent_query_budgets() -> None:
    observation = FanoutObservation(("C10", "C11"), 0, 3, 2)
    reruns = [
        FanoutRerun("C10", 2, 3, 9, 1),
        FanoutRerun("C11", 2, 3, 4, 7),
    ]

    assert _validated_rerun_counts(observation, reruns) == (2, 9, 7)


@pytest.mark.unit
async def test_generate_baseline_reports_discovery_and_binds_exact_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress: list[tuple[int, int, str]] = []
    selected_queries: list[str] = []

    async def enumerate_codes(_client: object, root_code: str) -> tuple[str, ...]:
        assert root_code == "C3262"
        return ("C10", "C11")

    async def rerun(_client: object, code: str) -> FanoutRerun:
        return FanoutRerun(code, 2, 3, 5 if code == "C10" else 7, 4)

    class DiscoveryClient(_RecordingClient):
        async def select(
            self, query: str, *, required_variables: Collection[str] = ()
        ) -> Sequence[Mapping[str, str | None]]:
            assert not required_variables
            selected_queries.append(query)
            if len(selected_queries) == 1:
                return []
            return [
                {"kind": "restriction", "anchor": "C10", "occurrence": "a"},
                {"kind": "restriction", "anchor": "C10", "occurrence": "b"},
                {"kind": "restriction", "anchor": "C10", "occurrence": "c"},
                {"kind": "restriction", "anchor": "C11", "occurrence": "x"},
                {"kind": "restriction", "anchor": "C11", "occurrence": "y"},
                {"kind": "restriction", "anchor": "C11", "occurrence": "z"},
            ]

    monkeypatch.setattr(fanout_module, "enumerate_scope_codes", enumerate_codes)
    monkeypatch.setattr(fanout_module, "rerun_fanout_concept", rerun)

    baseline = await generate_fanout_baseline(
        DiscoveryClient(),
        source_identity="source-sha",
        ontology_release="26.07d",
        progress=lambda index, total, label: progress.append((index, total, label)),
    )

    assert len(selected_queries) == 2
    assert progress == [(0, 2, "corpus-query")]
    assert baseline.concept_codes == ("C10", "C11")
    assert baseline.restriction_fact_count == 2
    assert baseline.restriction_occurrence_count == 3
    assert baseline.scanned_concept_count == 2
    assert baseline.logical_select_count_budget == 7
    assert baseline.select_once_r82_count_budget == 4
    assert baseline.baseline_identity == baseline_identity(baseline)


@pytest.mark.unit
def test_writer_emits_canonical_reloadable_document(tmp_path: Path) -> None:
    document = _baseline_document()
    baseline = FanoutBaseline(
        schema_version=BASELINE_SCHEMA_VERSION,
        source_identity="a" * 64,
        ontology_release="26.07d",
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        concept_codes=("C11",),
        restriction_fact_count=3,
        restriction_occurrence_count=4,
        scanned_concept_count=3,
        discovery_algorithm=DISCOVERY_ALGORITHM,
        discovery_query_identity=discovery_query_identity(),
        logical_select_count_budget=8,
        select_once_r82_count_budget=4,
        baseline_identity=cast("str", document["baseline_identity"]),
    )
    path = tmp_path / "fanout.json"

    write_fanout_baseline(path, baseline)

    assert path.read_text().endswith("\n")
    assert json.loads(path.read_text())["concept_codes"] == ["C11"]
    assert (
        load_fanout_baseline(
            path, expected_source_identity="a" * 64, expected_release="26.07d"
        )
        == baseline
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not-json", "unreadable"),
        ("[]", "root must be an object"),
    ],
)
def test_loader_rejects_unreadable_or_nonobject_documents(
    tmp_path: Path, payload: str, message: str
) -> None:
    path = tmp_path / "fanout.json"
    path.write_text(payload)

    with pytest.raises(ValueError, match=message):
        load_fanout_baseline(
            path, expected_source_identity="a" * 64, expected_release="26.07d"
        )


@pytest.mark.unit
def test_loader_rejects_missing_file_and_extra_fields(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="unreadable"):
        load_fanout_baseline(
            missing, expected_source_identity="a" * 64, expected_release="26.07d"
        )

    document = _baseline_document()
    document["unexpected"] = "field"
    path = tmp_path / "extra.json"
    _write_document(path, document)
    with pytest.raises(ValueError, match="fields do not match"):
        load_fanout_baseline(
            path, expected_source_identity="a" * 64, expected_release="26.07d"
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("restriction_fact_count", True, "must be an integer"),
        ("restriction_occurrence_count", -1, "out of range"),
        ("scanned_concept_count", 0, "out of range"),
        ("logical_select_count_budget", "8", "must be an integer"),
        ("select_once_r82_count_budget", -1, "out of range"),
        ("source_identity", "", "non-empty string"),
        ("ontology_release", 2607, "non-empty string"),
    ],
)
def test_loader_rejects_invalid_integer_and_string_fields(
    tmp_path: Path, key: str, value: object, message: str
) -> None:
    document = _baseline_document()
    document[key] = value
    path = tmp_path / "invalid.json"
    _write_document(path, document)

    with pytest.raises(ValueError, match=message):
        load_fanout_baseline(
            path, expected_source_identity="a" * 64, expected_release="26.07d"
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "codes",
    [[], [""], [10], ["C11", "C11"], ["C12", "C11"]],
)
def test_loader_rejects_empty_invalid_duplicate_or_unsorted_codes(
    tmp_path: Path, codes: list[object]
) -> None:
    document = _baseline_document()
    document["concept_codes"] = codes
    path = tmp_path / "invalid-codes.json"
    _write_document(path, document)

    with pytest.raises(ValueError, match="concept codes are invalid"):
        load_fanout_baseline(
            path, expected_source_identity="a" * 64, expected_release="26.07d"
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("schema_version", 2, "schema version"),
        ("branch", "other", "branch"),
        ("scope_root", "C1", "scope root"),
        ("scope_version", "old", "scope version"),
        ("discovery_query_identity", "wrong", "discovery query identity"),
    ],
)
def test_loader_rejects_production_contract_drift(
    tmp_path: Path, key: str, value: object, message: str
) -> None:
    document = _baseline_document()
    document[key] = value
    path = tmp_path / "drift.json"
    _write_document(path, document)

    with pytest.raises(ValueError, match=message):
        load_fanout_baseline(
            path, expected_source_identity="a" * 64, expected_release="26.07d"
        )


@pytest.mark.unit
def test_loader_rejects_release_and_self_identity_mismatch(tmp_path: Path) -> None:
    document = _baseline_document()
    path = tmp_path / "fanout.json"
    _write_document(path, document)

    with pytest.raises(ValueError, match="release does not match"):
        load_fanout_baseline(
            path, expected_source_identity="a" * 64, expected_release="different"
        )

    document["restriction_fact_count"] = 99
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="identity does not match payload"):
        load_fanout_baseline(
            path, expected_source_identity="a" * 64, expected_release="26.07d"
        )
