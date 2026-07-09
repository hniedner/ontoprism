from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from scripts.research.differentia_extractor import (
    _is_stage_system_filler,
    _load_golden,
    ambiguous_axes,
    extract,
    extract_defining_axes,
)
from scripts.research.differentia_extractor import (
    main as differentia_main,
)

from ontolib.decomposition.walker import Level, Role


class TestIsStageSystemFiller:
    @pytest.mark.unit
    def test_matches_ajcc(self) -> None:
        assert _is_stage_system_filler("AJCC v7 Stage")

    @pytest.mark.unit
    def test_matches_uicc(self) -> None:
        assert _is_stage_system_filler("UICC Stage")

    @pytest.mark.unit
    def test_matches_stage_system_phrase(self) -> None:
        assert _is_stage_system_filler("Some Stage System v2")

    @pytest.mark.unit
    def test_returns_false_for_value_stage(self) -> None:
        assert not _is_stage_system_filler("Stage III")

    @pytest.mark.unit
    def test_returns_false_for_none(self) -> None:
        assert not _is_stage_system_filler(None)

    @pytest.mark.unit
    def test_returns_false_for_empty_string(self) -> None:
        assert not _is_stage_system_filler("")


class TestExtractDefiningAxes:
    @pytest.mark.unit
    def test_keeps_defining_axes_as_is(self) -> None:
        roles = [
            Role(
                code="R101",
                label="Primary Site",
                filler_code="C12400",
                filler_label="Lung",
            )
        ]
        assert extract_defining_axes(roles) == {("R101", "C12400")}

    @pytest.mark.unit
    def test_drops_roles_not_in_defining_axes(self) -> None:
        roles = [
            Role(
                code="R108",
                label="Finding",
                filler_code="C123",
                filler_label="Some Finding",
            ),
            Role(
                code="R101",
                label="Primary Site",
                filler_code="C12400",
                filler_label="Lung",
            ),
        ]
        assert extract_defining_axes(roles) == {("R101", "C12400")}

    @pytest.mark.unit
    def test_relabels_stage_system(self) -> None:
        roles = [
            Role(
                code="R88",
                label="Stage",
                filler_code="C14165",
                filler_label="AJCC v7 Stage",
            ),
        ]
        assert extract_defining_axes(roles) == {("op:StageSystem", "C14165")}

    @pytest.mark.unit
    def test_keeps_value_stage_as_r88(self) -> None:
        roles = [
            Role(
                code="R88",
                label="Stage",
                filler_code="C27970",
                filler_label="Stage III",
            ),
        ]
        assert extract_defining_axes(roles) == {("R88", "C27970")}

    @pytest.mark.unit
    def test_empty_roles_returns_empty_set(self) -> None:
        assert extract_defining_axes([]) == set()

    @pytest.mark.unit
    def test_aggregates_multiple_pairs(self) -> None:
        roles = [
            Role(
                code="R88",
                label="Stage",
                filler_code="C27970",
                filler_label="Stage III",
            ),
            Role(
                code="R101",
                label="Primary Site",
                filler_code="C12400",
                filler_label="Lung",
            ),
        ]
        assert extract_defining_axes(roles) == {("R88", "C27970"), ("R101", "C12400")}


class TestAmbiguousAxes:
    @pytest.mark.unit
    def test_when_one_axis_has_multiple_fillers(self) -> None:
        pairs = {("R88", "C27970"), ("R88", "C14165")}
        assert ambiguous_axes(pairs) == {"R88": {"C27970", "C14165"}}

    @pytest.mark.unit
    def test_when_multiple_axes_have_multiple_fillers(self) -> None:
        pairs = {
            ("R88", "C27970"),
            ("R88", "C14165"),
            ("R101", "C12400"),
            ("R101", "C12401"),
        }
        result = ambiguous_axes(pairs)
        assert "R88" in result
        assert "R101" in result
        assert len(result["R88"]) == 2
        assert len(result["R101"]) == 2

    @pytest.mark.unit
    def test_when_no_ambiguous_axes(self) -> None:
        pairs = {("R88", "C27970"), ("R101", "C12400")}
        assert ambiguous_axes(pairs) == {}

    @pytest.mark.unit
    def test_when_empty_pairs(self) -> None:
        assert ambiguous_axes(set()) == {}


class TestLoadGolden:
    @pytest.mark.unit
    def test_loads_golden_file(self, tmp_path: Any) -> None:
        path = tmp_path / "golden.json"
        data = {"concepts": {"C6135": {"constituents": [["R88", "C27970"]]}}}
        path.write_text(json.dumps(data))
        result = _load_golden(str(path))
        assert result == data["concepts"]


class TestExtract:
    @pytest.mark.unit
    async def test_extract_returns_empty_when_no_levels(self) -> None:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "scripts.research.differentia_extractor.walk_chain",
                AsyncMock(return_value=[]),
            )
            result = await extract(AsyncMock(), "C6135")
        assert result == set()

    @pytest.mark.unit
    async def test_extract_collects_across_levels(self) -> None:
        levels = [
            Level(
                genus_codes=[],
                roles=[
                    Role(
                        code="R88",
                        label="Stage",
                        filler_code="C27970",
                        filler_label="Stage III",
                    )
                ],
            ),
            Level(
                genus_codes=[],
                roles=[
                    Role(
                        code="R101",
                        label="Primary Site",
                        filler_code="C12400",
                        filler_label="Lung",
                    )
                ],
            ),
        ]
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "scripts.research.differentia_extractor.walk_chain",
                AsyncMock(return_value=levels),
            )
            result = await extract(AsyncMock(), "C6135")
        assert result == {("R88", "C27970"), ("R101", "C12400")}

    @pytest.mark.unit
    async def test_extract_drops_non_defining_axes(self) -> None:
        levels = [
            Level(
                genus_codes=[],
                roles=[
                    Role(
                        code="R108",
                        label="Finding",
                        filler_code="C123",
                        filler_label="Some Finding",
                    ),
                    Role(
                        code="R101",
                        label="Primary Site",
                        filler_code="C12400",
                        filler_label="Lung",
                    ),
                ],
            ),
        ]
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "scripts.research.differentia_extractor.walk_chain",
                AsyncMock(return_value=levels),
            )
            result = await extract(AsyncMock(), "C6135")
        assert result == {("R101", "C12400")}


class TestMain:
    @pytest.mark.unit
    async def test_main_runs_with_default_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.argv", ["differentia_extractor.py"])
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with monkeypatch.context() as mp:
            mp.setattr(
                "scripts.research.differentia_extractor.OxigraphHttpClient",
                lambda *a, **kw: client,
            )
            mp.setattr(
                "scripts.research.differentia_extractor.walk_chain",
                AsyncMock(return_value=[]),
            )
            mp.setattr(
                "scripts.research.differentia_extractor._load_golden", lambda p: {}
            )
            await differentia_main()

    @pytest.mark.unit
    async def test_main_prints_vs_golden(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setattr("sys.argv", ["differentia_extractor.py", "C6135"])
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with monkeypatch.context() as mp:
            mp.setattr(
                "scripts.research.differentia_extractor.OxigraphHttpClient",
                lambda *a, **kw: client,
            )
            mp.setattr(
                "scripts.research.differentia_extractor.walk_chain",
                AsyncMock(return_value=[]),
            )
            mp.setattr(
                "scripts.research.differentia_extractor._load_golden",
                lambda p: {"C6135": {"constituents": [["R88", "C27970"]]}},
            )
            await differentia_main()
        captured = capsys.readouterr()
        assert "C6135" in captured.out
        assert "vs golden" in captured.out
