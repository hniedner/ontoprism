"""Unit tests for the embedding text-builders (pure; no model or DB needed)."""

import pytest

from ontolib.repositories.embeddings.generate import cde_text, ncit_text


@pytest.mark.unit
def test_ncit_text_orders_and_caps_parts() -> None:
    text = ncit_text(
        "Neoplasm",
        [f"syn{i}" for i in range(8)],
        "A tissue growth." * 60,  # long definition → truncated to 500 chars
        "Neoplastic Process",
    )
    parts = text.split(" | ")
    assert parts[0] == "Neoplasm"
    # Only the first 5 synonyms are included.
    assert parts[1:6] == ["syn0", "syn1", "syn2", "syn3", "syn4"]
    assert "syn5" not in parts
    # Definition truncated to 500 chars; semantic type last.
    assert len(parts[6]) == 500
    assert parts[-1] == "Neoplastic Process"


@pytest.mark.unit
def test_ncit_text_omits_empty_optionals() -> None:
    assert ncit_text("Just A Name", [], None, None) == "Just A Name"


@pytest.mark.unit
def test_cde_text_prefers_search_text() -> None:
    assert (
        cde_text("precomputed search", "SN", "Long Name", "def") == "precomputed search"
    )


@pytest.mark.unit
def test_cde_text_falls_back_to_core_fields() -> None:
    assert (
        cde_text(None, "SN", "Long Name", "A definition.")
        == "SN | Long Name | A definition."
    )
    assert cde_text("", "SN", "", "") == "SN"
