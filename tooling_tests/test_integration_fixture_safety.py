from __future__ import annotations

import pytest
from test_support import qlever_graph


@pytest.mark.unit
def test_graph_preservation_refuses_an_incomplete_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = iter((2, 1))
    monkeypatch.setattr(qlever_graph, "qlever_graph_count", lambda *_args: next(counts))
    monkeypatch.setattr(qlever_graph, "qlever_update", lambda *_args: None)

    with (
        pytest.raises(RuntimeError, match="backup count differs"),
        qlever_graph.preserve_qlever_graph("http://qlever.test", "urn:graph"),
    ):
        pytest.fail("an incomplete backup must refuse before yielding")


@pytest.mark.unit
def test_graph_preservation_keeps_primary_and_cleanup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = iter((0, 0))
    updates = 0

    def update(*_args: object) -> None:
        nonlocal updates
        updates += 1
        if updates == 2:
            raise RuntimeError("restore failed")

    monkeypatch.setattr(qlever_graph, "qlever_graph_count", lambda *_args: next(counts))
    monkeypatch.setattr(qlever_graph, "qlever_update", update)

    with (
        pytest.raises(BaseExceptionGroup) as captured,
        qlever_graph.preserve_qlever_graph("http://qlever.test", "urn:graph"),
    ):
        raise ValueError("test failed")

    assert [str(error) for error in captured.value.exceptions] == [
        "test failed",
        "restore failed",
    ]
