"""Unit tests for the Turtle writer (issue #71)."""

from __future__ import annotations

import pytest

from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.ttl_writer import _object_iri, render_ttl
from ontolib.repositories.xref.vocab import CLOSE_MATCH, EXACT_MATCH
from ontolib.terminologies.namespaces import NCIT_NS


@pytest.mark.unit
def test_render_emits_one_triple_per_record() -> None:
    r = SSSOMRecord(
        subject_id="C3262",
        predicate_id=CLOSE_MATCH,
        object_id="UBERON:0002107",
        mapping_justification="semapv:LexicalMatching",
        confidence=0.8,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
    )
    ttl = render_ttl([r])
    assert f"<{NCIT_NS}C3262>" in ttl
    assert f"<{CLOSE_MATCH}>" in ttl
    assert "<http://purl.obolibrary.org/obo/UBERON_0002107>" in ttl
    assert ttl.endswith("\n")


@pytest.mark.unit
def test_render_multiple_records() -> None:
    records = [
        SSSOMRecord(
            subject_id="C3262",
            predicate_id=CLOSE_MATCH,
            object_id="UBERON:0002107",
            mapping_justification="semapv:LexicalMatching",
            confidence=0.8,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
        ),
        SSSOMRecord(
            subject_id="C12345",
            predicate_id=EXACT_MATCH,
            object_id="CL:0000057",
            mapping_justification="semapv:ManualMappingCuration",
            confidence=1.0,
            subject_source_version="26.02d",
            object_source_version="cl-2026-01",
        ),
    ]
    ttl = render_ttl(records)
    assert f"<{NCIT_NS}C3262>" in ttl
    assert f"<{NCIT_NS}C12345>" in ttl
    assert "<http://purl.obolibrary.org/obo/UBERON_0002107>" in ttl
    assert "<http://purl.obolibrary.org/obo/CL_0000057>" in ttl
    assert ttl.count("\n") == 2


@pytest.mark.unit
def test_render_empty_iterable() -> None:
    ttl = render_ttl([])
    assert ttl == "\n"


@pytest.mark.unit
def test_invalid_curie_raises_value_error() -> None:
    with pytest.raises(ValueError, match="object_id is not a CURIE"):
        _object_iri("not-a-curie")


@pytest.mark.unit
def test_empty_curie_raises_value_error() -> None:
    with pytest.raises(ValueError, match="object_id is not a CURIE"):
        _object_iri("")
