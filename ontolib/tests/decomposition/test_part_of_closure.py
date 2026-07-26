from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

import pytest

from ontolib.core.exceptions import StorageError
from ontolib.decomposition import extract, stated_queries
from ontolib.decomposition.extract import PartOfPair
from ontolib.terminologies.namespaces import NCIT_NS

Row = dict[str, str | None]
RowFactory = Callable[[tuple[str, ...], int], list[Row]]

_NODE_BINDING = re.compile(rf"BIND\(<{re.escape(NCIT_NS)}(C[0-9]+)> AS \?node\)")


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


class _ExpansionStore:
    def __init__(
        self,
        expansions: Mapping[str, Iterable[tuple[str, str]]] | None = None,
        *,
        row_factory: RowFactory | None = None,
    ) -> None:
        self._expansions = expansions or {}
        self._row_factory = row_factory
        self.calls: list[tuple[tuple[str, ...], frozenset[str]]] = []

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[Row]:
        required = frozenset(required_variables)
        codes = tuple(dict.fromkeys(_NODE_BINDING.findall(query)))
        self.calls.append((codes, required))
        assert required == {"node", "kind", "target", "targetType"}
        assert 1 <= len(codes) <= 16
        if self._row_factory is not None:
            return self._row_factory(codes, len(self.calls))
        return [
            {
                "node": _iri(code),
                "kind": kind,
                "target": _iri(target),
                "targetType": "iri",
            }
            for code in codes
            for kind, target in self._expansions.get(code, ())
        ]


class _SingleAttemptClient:
    def __init__(self, select_once: stated_queries.SelectRows) -> None:
        self._select_once = select_once

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]:
        return await self._select_once(
            query,
            required_variables=required_variables,
        )


@pytest.mark.unit
async def test_part_of_closure_handles_graph_shapes_deterministically() -> None:
    expansions = {
        "C100": [("whole", "C101")],
        "C101": [("whole", "C102")],
        "C110": [("parent", "C111")],
        "C111": [("whole", "C112")],
        "C120": [("whole", "C121")],
        "C121": [("whole", "C120")],
        "C130": [("whole", "C131"), ("whole", "C132")],
        "C131": [("whole", "C133")],
        "C132": [("whole", "C133")],
        "C140": [("whole", "C141"), ("whole", "C141")],
        "C160": [("parent", "C161")],
        "C161": [("whole", "C162")],
        "C162": [("parent", "C163")],
        "C163": [("whole", "C164")],
    }
    store = _ExpansionStore(expansions)
    requested = [
        "C100",
        "C101",
        "C102",
        "C110",
        "C112",
        "C120",
        "C121",
        "C130",
        "C133",
        "C140",
        "C141",
        "C150",
        "C160",
        "C164",
        *(f"C20{i:02d}" for i in range(17)),
    ]

    first = await stated_queries.resolve_part_of_pairs(store, requested)
    second = await stated_queries.resolve_part_of_pairs(
        _ExpansionStore(expansions), reversed(requested)
    )

    assert (
        first
        == second
        == [
            PartOfPair(part="C100", whole="C101"),
            PartOfPair(part="C100", whole="C102"),
            PartOfPair(part="C101", whole="C102"),
            PartOfPair(part="C110", whole="C112"),
            PartOfPair(part="C120", whole="C121"),
            PartOfPair(part="C121", whole="C120"),
            PartOfPair(part="C130", whole="C133"),
            PartOfPair(part="C140", whole="C141"),
            PartOfPair(part="C160", whole="C164"),
        ]
    )
    assert len(store.calls) > 1
    assert all(
        required == {"node", "kind", "target", "targetType"}
        for _, required in store.calls
    )


@pytest.mark.unit
async def test_part_of_closure_empty_input_does_not_query_store() -> None:
    store = _ExpansionStore()
    assert await stated_queries.resolve_part_of_pairs(store, []) == []
    assert store.calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_code", ["R82", "Cfoo", "C123x", "C\uff11\uff12\uff13"]
)
async def test_part_of_closure_rejects_invalid_input_before_store(
    invalid_code: str,
) -> None:
    store = _ExpansionStore()
    with pytest.raises(ValueError, match=r"Unsafe|NCIt concept code"):
        await stated_queries.resolve_part_of_pairs(store, [invalid_code])
    assert store.calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            {"node": _iri("C1"), "kind": "whole", "target": _iri("C2")},
            "targetType",
        ),
        (
            {"node": _iri("C1"), "kind": "whole", "targetType": "iri"},
            "target",
        ),
        (
            {
                "node": _iri("C1"),
                "kind": "sideways",
                "target": _iri("C2"),
                "targetType": "iri",
            },
            "kind",
        ),
        (
            {
                "node": _iri("C1"),
                "kind": "whole",
                "target": _iri("C2"),
                "targetType": "non-iri",
            },
            "target is not an IRI",
        ),
        (
            {
                "node": "https://example.org/C1",
                "kind": "whole",
                "target": _iri("C2"),
                "targetType": "iri",
            },
            "node is not an NCIt IRI",
        ),
        (
            {
                "node": _iri("C1"),
                "kind": "whole",
                "target": _iri("R82"),
                "targetType": "iri",
            },
            "target is not an NCIt concept code",
        ),
        (
            {
                "node": _iri("C999"),
                "kind": "whole",
                "target": _iri("C2"),
                "targetType": "iri",
            },
            "unexpected R82 expansion node",
        ),
    ],
)
async def test_part_of_closure_rejects_malformed_rows(
    row: Row,
    message: str,
) -> None:
    store = _ExpansionStore(row_factory=lambda _codes, _call: [row])
    with pytest.raises(ValueError, match=message):
        await stated_queries.resolve_part_of_pairs(store, ["C1", "C2"])


@pytest.mark.unit
async def test_part_of_closure_requires_projection_and_propagates_store_error() -> None:
    calls = 0

    async def malformed_projection(
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[Row]:
        nonlocal calls
        del query
        calls += 1
        assert set(required_variables) == {"node", "kind", "target", "targetType"}
        raise StorageError("missing required projected variable: target")

    with pytest.raises(StorageError, match="projected variable"):
        await stated_queries.resolve_part_of_pairs(
            _SingleAttemptClient(malformed_projection), ["C1", "C2"]
        )
    assert calls == 1


@pytest.mark.unit
async def test_part_of_closure_rejects_expanded_code_bound_before_store() -> None:
    store = _ExpansionStore()
    with pytest.raises(ValueError, match=r"expanded-code.*256"):
        await stated_queries.resolve_part_of_pairs(store, (f"C{i}" for i in range(257)))
    assert store.calls == []


@pytest.mark.unit
async def test_part_of_closure_accepts_256_initial_codes() -> None:
    store = _ExpansionStore()
    assert (
        await stated_queries.resolve_part_of_pairs(store, (f"C{i}" for i in range(256)))
        == []
    )
    assert len(store.calls) == 16


@pytest.mark.unit
async def test_part_of_closure_accepts_256_cumulative_expansion_codes() -> None:
    expansions = {"C1": [("whole", f"C{1000 + index}") for index in range(255)]}
    store = _ExpansionStore(expansions)

    assert await stated_queries.resolve_part_of_pairs(store, ["C1"]) == []
    assert len(store.calls) == 17


@pytest.mark.unit
async def test_part_of_closure_rejects_dynamic_257th_expansion_code() -> None:
    expansions = {"C1": [("whole", f"C{1000 + index}") for index in range(256)]}
    store = _ExpansionStore(expansions)

    with pytest.raises(ValueError, match=r"expanded-code.*256"):
        await stated_queries.resolve_part_of_pairs(store, ["C1"])
    assert len(store.calls) == 1


@pytest.mark.unit
async def test_part_of_closure_rejects_query_body_bound_before_store() -> None:
    store = _ExpansionStore()
    with pytest.raises(ValueError, match=r"query body.*65536"):
        await stated_queries.resolve_part_of_pairs(store, [f"C{'1' * 70_000}"])
    assert store.calls == []


@pytest.mark.unit
async def test_part_of_closure_rejects_row_bound() -> None:
    def oversized(_codes: tuple[str, ...], _call: int) -> list[Row]:
        return [
            {
                "node": _iri("C1"),
                "kind": "whole",
                "target": _iri(f"C{1000 + i}"),
                "targetType": "iri",
            }
            for i in range(257)
        ]

    with pytest.raises(ValueError, match=r"row.*256"):
        await stated_queries.resolve_part_of_pairs(
            _ExpansionStore(row_factory=oversized), ["C1"]
        )


@pytest.mark.unit
async def test_part_of_closure_accepts_eighth_r82_hop() -> None:
    expansions = {f"C{1000 + i}": [("whole", f"C{1001 + i}")] for i in range(8)}
    assert await stated_queries.resolve_part_of_pairs(
        _ExpansionStore(expansions), ["C1000", "C1008"]
    ) == [PartOfPair(part="C1000", whole="C1008")]


@pytest.mark.unit
async def test_part_of_closure_rejects_ninth_r82_hop() -> None:
    expansions = {f"C{1000 + i}": [("whole", f"C{1001 + i}")] for i in range(9)}
    with pytest.raises(ValueError, match=r"R82 hop.*8"):
        await stated_queries.resolve_part_of_pairs(
            _ExpansionStore(expansions), ["C1000", "C1009"]
        )


@pytest.mark.unit
async def test_part_of_closure_rejects_unrequested_ninth_hop() -> None:
    expansions = {f"C{1000 + i}": [("whole", f"C{1001 + i}")] for i in range(10)}
    with pytest.raises(ValueError, match=r"R82 hop.*8"):
        await stated_queries.resolve_part_of_pairs(
            _ExpansionStore(expansions), ["C1000", "C1010"]
        )


@pytest.mark.unit
async def test_part_of_closure_rejects_ninth_superclass_hop() -> None:
    expansions = {f"C{2000 + i}": [("parent", f"C{2001 + i}")] for i in range(9)}
    expansions["C2009"] = [("whole", "C2010")]
    with pytest.raises(ValueError, match=r"superclass hop.*8"):
        await stated_queries.resolve_part_of_pairs(
            _ExpansionStore(expansions), ["C2000", "C2010"]
        )


@pytest.mark.unit
async def test_part_of_closure_rejects_request_bound_before_65th_call() -> None:
    expansions: dict[str, list[tuple[str, str]]] = {}
    for stage in range(8):
        root = 3000 + stage * 20
        for depth in range(8):
            expansions[f"C{root + depth}"] = [("parent", f"C{root + depth + 1}")]
        next_root = 3000 + (stage + 1) * 20 if stage < 7 else 3999
        expansions[f"C{root + 8}"] = [("whole", f"C{next_root}")]
    store = _ExpansionStore(expansions)

    with pytest.raises(ValueError, match=r"request.*64"):
        await stated_queries.resolve_part_of_pairs(store, ["C3000", "C3999"])

    assert len(store.calls) == 64


@pytest.mark.unit
async def test_part_of_closure_rejects_total_row_memory_bound() -> None:
    requested = [f"C{500000 + i}" for i in range(240)]
    expansions: dict[str, list[tuple[str, str]]] = {}
    for index, code in enumerate(requested):
        expansions[code] = [
            ("whole", requested[(index + offset + 1) % len(requested)])
            for offset in range(16)
        ]
    expansions[requested[0]][-1] = ("whole", "C500240")
    expansions["C500240"] = [
        *(("whole", target) for target in requested[:239]),
        ("whole", "C500241"),
        *(("parent", target) for target in requested[:16]),
    ]
    expansions["C500241"] = [("whole", requested[0])]

    store = _ExpansionStore(expansions)
    with pytest.raises(ValueError, match=r"total row.*4096"):
        await stated_queries.resolve_part_of_pairs(store, requested)
    assert len(store.calls) == 17


@pytest.mark.unit
def test_part_of_expansion_query_enforces_batch_boundary() -> None:
    assert "FILTER(false)" in stated_queries.build_part_of_expansion_query([])
    with pytest.raises(ValueError, match=r"at most 16"):
        stated_queries.build_part_of_expansion_query(f"C{i}" for i in range(17))


@pytest.mark.unit
def test_part_of_expansion_query_is_constant_anchored_and_row_limited() -> None:
    query = stated_queries.build_part_of_expansion_query(["C2", "C1"])

    assert "VALUES ?node" not in query
    assert [
        line.strip() for line in query.splitlines() if "rdfs:subClassOf" in line
    ] == [
        f"<{NCIT_NS}C1> rdfs:subClassOf ?target .",
        f"<{NCIT_NS}C1> rdfs:subClassOf ?restriction .",
        f"<{NCIT_NS}C2> rdfs:subClassOf ?target .",
        f"<{NCIT_NS}C2> rdfs:subClassOf ?restriction .",
    ]
    assert query.count(f"BIND(<{NCIT_NS}C1> AS ?node)") == 2
    assert query.count(f"BIND(<{NCIT_NS}C2> AS ?node)") == 2
    assert [
        line.strip() for line in query.splitlines() if line.strip().startswith("LIMIT")
    ] == ["LIMIT 257"]


@pytest.mark.unit
def test_part_of_expansion_parser_returns_typed_deduplicated_rows() -> None:
    parsed = extract.part_of_expansions_from_rows(
        [
            {
                "node": _iri("C1"),
                "kind": "parent",
                "target": _iri("C2"),
                "targetType": "iri",
            },
            {
                "node": _iri("C1"),
                "kind": "whole",
                "target": _iri("C3"),
                "targetType": "iri",
            },
            {
                "node": _iri("C1"),
                "kind": "whole",
                "target": _iri("C3"),
                "targetType": "iri",
            },
        ]
    )
    assert parsed == {
        extract.PartOfExpansion(node="C1", kind="parent", target="C2"),
        extract.PartOfExpansion(node="C1", kind="whole", target="C3"),
    }


@pytest.mark.unit
@pytest.mark.parametrize("binding", ["node", "target"])
def test_part_of_expansion_rejects_non_concept_code(binding: str) -> None:
    values = {"node": "C1", "target": "C2"}
    values[binding] = "R82"
    with pytest.raises(ValueError, match=f"{binding} is not an NCIt concept code"):
        extract.PartOfExpansion(
            node=values["node"], kind="whole", target=values["target"]
        )


@pytest.mark.unit
def test_part_of_expansion_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError, match="kind is invalid"):
        extract.PartOfExpansion(
            node="C1",
            kind="sideways",  # type: ignore[arg-type]
            target="C2",
        )
