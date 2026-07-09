from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from ontolib.decomposition import walker as walker_mod
from ontolib.decomposition.walker import (
    Level,
    Role,
    _code,
    _hop_first,
    _process_frontier,
    one_level,
    walk_chain,
)
from ontolib.decomposition.walker import (
    main as walker_main,
)
from ontolib.terminologies.namespaces import NCIT_NS


def _iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


class TestCode:
    @pytest.mark.unit
    def test_returns_fragment_after_hash(self) -> None:
        assert _code("http://example.org#C123") == "C123"

    @pytest.mark.unit
    def test_returns_full_string_when_no_hash(self) -> None:
        assert _code("http://example.org/C123") == "http://example.org/C123"


class TestHopFirst:
    @pytest.mark.unit
    async def test_0_hops_sends_rdf_first_path(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(return_value=[{"member": _iri("C1")}])
        await _hop_first(client, "C6135", 0)
        call = client.select.call_args[0][0]
        assert "owl:intersectionOf/rdf:first" in call
        assert "C6135" in call
        assert "Thesaurus-stated.owl" in call

    @pytest.mark.unit
    async def test_multi_hops_builds_correct_path(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(return_value=[{"member": _iri("C1")}])
        await _hop_first(client, "C6135", 2)
        call = client.select.call_args[0][0]
        assert "rdf:rest/rdf:rest/rdf:first" in call

    @pytest.mark.unit
    async def test_returns_select_result_directly(self) -> None:
        expected = [{"member": _iri("C1"), "label": "Test"}]
        client = AsyncMock()
        client.select = AsyncMock(return_value=expected)
        result = await _hop_first(client, "C6135", 0)
        assert result == expected

    @pytest.mark.unit
    async def test_includes_optional_bindings_in_query(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(return_value=[])
        await _hop_first(client, "C6135", 1)
        call = client.select.call_args[0][0]
        assert "?is_role" in call
        assert "?rellabel" in call
        assert "?tlabel" in call
        assert "?is_defined" in call
        assert "OPTIONAL" in call


class TestOneLevel:
    @pytest.mark.unit
    async def test_returns_none_when_no_rows(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(return_value=[])
        result = await one_level(client, "C6135")
        assert result is None

    @pytest.mark.unit
    async def test_returns_none_when_all_rows_empty_after_first_hop(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(
            side_effect=[
                [],  # hop 0 returns nothing
            ]
        )
        result = await one_level(client, "C6135")
        assert result is None

    @pytest.mark.unit
    async def test_parses_role_correctly(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(
            side_effect=[
                [
                    {
                        "member": _iri("C99"),
                        "is_role": True,
                        "rel": _iri("R88"),
                        "rellabel": "Stage",
                        "target": _iri("C27970"),
                        "tlabel": "Stage III",
                    }
                ],
                [{"member": _iri("C99")}],  # genus entry
                [],
            ]
        )
        level = await one_level(client, "C6135")
        assert level is not None
        assert len(level.roles) == 1
        assert level.roles[0] == Role(
            code="R88", label="Stage", filler_code="C27970", filler_label="Stage III"
        )
        assert len(level.genus_codes) == 1
        assert level.genus_codes[0] == ("C99", None, False)

    @pytest.mark.unit
    async def test_parses_genus_with_is_defined(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(
            side_effect=[
                [
                    {
                        "member": _iri("C1"),
                        "is_role": False,
                        "is_defined": True,
                        "label": "Neoplasm",
                    }
                ],
                [],
            ]
        )
        level = await one_level(client, "C6135")
        assert level is not None
        assert len(level.genus_codes) == 1
        assert level.genus_codes[0] == ("C1", "Neoplasm", True)

    @pytest.mark.unit
    async def test_parses_primitive_genus_when_is_defined_missing(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(
            side_effect=[
                [{"member": _iri("C2"), "is_role": False, "label": "Primitive"}],
                [],
            ]
        )
        level = await one_level(client, "C6135")
        assert level is not None
        assert level.genus_codes[0] == ("C2", "Primitive", False)

    @pytest.mark.unit
    async def test_stops_at_first_empty_hop(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(return_value=[])
        result = await one_level(client, "C6135")
        assert result is None
        # should only have been called once (hop 0)
        assert client.select.call_count == 1

    @pytest.mark.unit
    async def test_stops_when_member_is_missing(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(
            side_effect=[
                [{"not_member": _iri("C1")}],
                [{"member": _iri("C2")}],
            ]
        )
        level = await one_level(client, "C6135")
        assert level is None

    @pytest.mark.unit
    async def test_collects_roles_with_null_labels(self) -> None:
        client = AsyncMock()
        client.select = AsyncMock(
            side_effect=[
                [
                    {
                        "member": _iri("C99"),
                        "is_role": True,
                        "rel": _iri("R101"),
                        "target": _iri("C12400"),
                    }
                ],
                [],
            ]
        )
        level = await one_level(client, "C6135")
        assert level is not None
        assert len(level.roles) == 1
        assert level.roles[0].label is None
        assert level.roles[0].filler_label is None

    @pytest.mark.unit
    async def test_multiple_genera_at_one_level(self) -> None:
        rows = [
            [{"member": _iri("C1"), "is_role": False, "is_defined": True}],
            [{"member": _iri("C2"), "is_role": False, "is_defined": True}],
            [],
        ]
        client = AsyncMock()
        client.select = AsyncMock(side_effect=rows)
        level = await one_level(client, "C6135")
        assert level is not None
        assert len(level.genus_codes) == 2

    @pytest.mark.unit
    async def test_exhausts_all_hops_without_breaking(self) -> None:
        original = walker_mod._MAX_HOPS
        walker_mod._MAX_HOPS = 3
        client = AsyncMock()
        client.select = AsyncMock(
            side_effect=[
                [{"member": _iri(f"C{i}"), "is_role": False}] for i in range(3)
            ]
        )
        level = await one_level(client, "C6135")
        walker_mod._MAX_HOPS = original
        assert level is not None
        assert len(level.genus_codes) == 3


class TestProcessFrontier:
    @pytest.mark.unit
    async def test_returns_none_when_one_level_returns_none(self) -> None:
        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker.one_level", AsyncMock(return_value=None)
            )
            result = await _process_frontier(client, "C1", set(), [])
        assert result is None

    @pytest.mark.unit
    async def test_adds_defined_unvisited_genus_to_next_frontier(self) -> None:
        level = Level(genus_codes=[("C2", None, True)])
        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker.one_level", AsyncMock(return_value=level)
            )
            visited: set[str] = {"C1"}
            next_frontier: list[str] = []
            result = await _process_frontier(client, "C1", visited, next_frontier)
        assert result is level
        assert "C2" in visited
        assert next_frontier == ["C2"]

    @pytest.mark.unit
    async def test_skips_undefined_genus(self) -> None:
        level = Level(genus_codes=[("C2", None, False)])
        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker.one_level", AsyncMock(return_value=level)
            )
            visited: set[str] = {"C1"}
            next_frontier: list[str] = []
            result = await _process_frontier(client, "C1", visited, next_frontier)
        assert result is level
        assert "C2" not in visited
        assert next_frontier == []

    @pytest.mark.unit
    async def test_skips_already_visited_genus(self) -> None:
        level = Level(genus_codes=[("C2", None, True)])
        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker.one_level", AsyncMock(return_value=level)
            )
            visited: set[str] = {"C1", "C2"}
            next_frontier: list[str] = []
            result = await _process_frontier(client, "C1", visited, next_frontier)
        assert result is level
        assert next_frontier == []

    @pytest.mark.unit
    async def test_adds_multiple_genera(self) -> None:
        level = Level(genus_codes=[("C2", None, True), ("C3", None, True)])
        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker.one_level", AsyncMock(return_value=level)
            )
            visited: set[str] = {"C1"}
            next_frontier: list[str] = []
            result = await _process_frontier(client, "C1", visited, next_frontier)
        assert result is level
        assert next_frontier == ["C2", "C3"]

    @pytest.mark.unit
    async def test_mixed_genera_skips_undefined_and_visited(self) -> None:
        level = Level(
            genus_codes=[("C2", None, False), ("C3", None, True), ("C4", None, True)]
        )
        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker.one_level", AsyncMock(return_value=level)
            )
            visited: set[str] = {"C1", "C3"}
            next_frontier: list[str] = []
            result = await _process_frontier(client, "C1", visited, next_frontier)
        assert result is level
        assert next_frontier == ["C4"]


class TestWalkChain:
    @pytest.mark.unit
    async def test_returns_empty_list_for_nonexistent_code(self) -> None:
        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker._process_frontier",
                AsyncMock(return_value=None),
            )
            levels = await walk_chain(client, "C9999")
        assert levels == []

    @pytest.mark.unit
    async def test_single_level_no_genera(self) -> None:
        level = Level(genus_codes=[], roles=[Role("R88", None, "C27970", None)])
        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker._process_frontier",
                AsyncMock(return_value=level),
            )
            levels = await walk_chain(client, "C1")
        assert levels == [level]

    @pytest.mark.unit
    async def test_breadth_first_traversal(self) -> None:
        c1 = Level(genus_codes=[("C2", None, True), ("C3", None, True)])
        c2 = Level(genus_codes=[("C4", None, True)])
        c3 = Level(genus_codes=[])
        c4 = Level(genus_codes=[])

        side_effects: dict[str, Any] = {"C1": c1, "C2": c2, "C3": c3, "C4": c4}

        async def fake_process(
            client: Any, code: str, visited: set[str], nf: list[str]
        ) -> Level | None:
            level = side_effects.get(code)
            if level is None:
                return None
            for g_code, _label, is_defined in level.genus_codes:
                if is_defined and g_code not in visited:
                    visited.add(g_code)
                    nf.append(g_code)
            return level

        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("ontolib.decomposition.walker._process_frontier", fake_process)
            levels = await walk_chain(client, "C1")
        assert len(levels) == 4
        assert levels == [c1, c2, c3, c4]

    @pytest.mark.unit
    async def test_memoization_skips_already_visited(self) -> None:
        c1 = Level(genus_codes=[("C2", None, True)])
        c2 = Level(genus_codes=[("C1", None, True)])  # circular back to C1

        call_order: list[str] = []

        async def fake_process(
            client: Any, code: str, visited: set[str], nf: list[str]
        ) -> Level | None:
            call_order.append(code)
            level = {"C1": c1, "C2": c2}.get(code)
            if level is None:
                return None
            for g_code, _label, is_defined in level.genus_codes:
                if is_defined and g_code not in visited:
                    visited.add(g_code)
                    nf.append(g_code)
            return level

        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("ontolib.decomposition.walker._process_frontier", fake_process)
            levels = await walk_chain(client, "C1")
        assert call_order == ["C1", "C2"]
        assert len(levels) == 2
        assert levels == [c1, c2]

    @pytest.mark.unit
    async def test_respects_max_depth(self) -> None:
        c1 = Level(genus_codes=[("C2", None, True)])
        c2 = Level(genus_codes=[("C3", None, True)])
        c3 = Level(genus_codes=[])

        call_order: list[str] = []

        async def fake_process(
            client: Any, code: str, visited: set[str], nf: list[str]
        ) -> Level | None:
            call_order.append(code)
            level = {"C1": c1, "C2": c2, "C3": c3}.get(code)
            if level is None:
                return None
            for g_code, _label, is_defined in level.genus_codes:
                if is_defined and g_code not in visited:
                    visited.add(g_code)
                    nf.append(g_code)
            return level

        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("ontolib.decomposition.walker._process_frontier", fake_process)
            levels = await walk_chain(client, "C1", max_depth=1)
        assert call_order == ["C1"]
        assert len(levels) == 1

    @pytest.mark.unit
    async def test_stops_when_frontier_empty(self) -> None:
        async def fake_process(
            client: Any, code: str, visited: set[str], nf: list[str]
        ) -> Level | None:
            return None

        client = AsyncMock()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("ontolib.decomposition.walker._process_frontier", fake_process)
            levels = await walk_chain(client, "C1")
        assert levels == []


class TestMain:
    @staticmethod
    def _mock_client() -> AsyncMock:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    @pytest.mark.unit
    async def test_main_uses_default_code_when_no_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.argv", ["walker.py"])
        level = Level(
            genus_codes=[("C2", None, True)],
            roles=[
                Role(
                    code="R88",
                    label="Stage",
                    filler_code="C27970",
                    filler_label="Stage III",
                )
            ],
        )
        client = self._mock_client()
        with monkeypatch.context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker.OxigraphHttpClient",
                lambda *a, **kw: client,
            )
            mp.setattr(
                "ontolib.decomposition.walker.walk_chain",
                AsyncMock(return_value=[level]),
            )
            await walker_main()

    @pytest.mark.unit
    async def test_main_uses_provided_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.argv", ["walker.py", "C4791"])
        client = self._mock_client()
        with monkeypatch.context() as mp:
            mp.setattr(
                "ontolib.decomposition.walker.OxigraphHttpClient",
                lambda *a, **kw: client,
            )
            mp.setattr(
                "ontolib.decomposition.walker.walk_chain", AsyncMock(return_value=[])
            )
            await walker_main()
