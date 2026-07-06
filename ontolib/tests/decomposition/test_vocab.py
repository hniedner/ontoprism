"""Unit tests for the decomposition vocabulary contract."""

import pytest

from ontolib.decomposition import vocab
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI


@pytest.mark.unit
def test_namespace_is_the_w3id_persistent_identifier() -> None:
    assert vocab.ONTOPRISM_NS == "https://w3id.org/ontoprism/vocab#"


@pytest.mark.unit
def test_all_op_terms_are_under_the_namespace() -> None:
    terms = [
        vocab.REPRESENTATION_STATUS,
        vocab.DECOMPOSED_ON,
        vocab.DECOMPOSED_BY,
        vocab.HAS_CONSTITUENT,
        vocab.AXIS,
        vocab.FILLER,
        vocab.AXIS_SOURCE,
        vocab.MOST_SPECIFIC,
        vocab.HAS_COMPONENT,
        vocab.DECOMPOSITION_KIND,
    ]
    assert all(t.startswith(vocab.ONTOPRISM_NS) for t in terms)
    # Terms are distinct (no accidental duplication).
    assert len(set(terms)) == len(terms)


@pytest.mark.unit
def test_decomposed_graph_is_distinct_from_source_graphs() -> None:
    assert vocab.DECOMPOSED_GRAPH_IRI not in ("", STATED_GRAPH_IRI)
    assert vocab.DECOMPOSED_GRAPH_IRI.endswith("Thesaurus-decomposed.owl")


@pytest.mark.unit
def test_legacy_precoordinated_marker() -> None:
    assert vocab.LEGACY_PRECOORDINATED == "legacy-precoordinated"
