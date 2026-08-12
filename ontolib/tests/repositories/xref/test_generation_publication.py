from __future__ import annotations

import pytest

from ontolib.repositories.xref.publication import generation_graph_iri


@pytest.mark.unit
def test_generation_graph_is_source_specific_and_injection_safe() -> None:
    uberon = generation_graph_iri("uberon publisher", "a" * 64)
    icdo = generation_graph_iri("icdo/p334", "a" * 64)

    assert uberon != icdo
    assert "uberon-publisher" in uberon
    assert "icdo-p334" in icdo
    assert " " not in uberon
    assert "/p334" not in icdo


@pytest.mark.unit
@pytest.mark.parametrize("generation_id", ["", "abc", "g" * 64])
def test_generation_graph_refuses_invalid_generation_identity(
    generation_id: str,
) -> None:
    with pytest.raises(ValueError, match="generation"):
        generation_graph_iri("uberon", generation_id)
