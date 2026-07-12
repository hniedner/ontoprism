"""Unit tests for SSSOMRecord dataclass (issue #71)."""

from __future__ import annotations

import pytest

from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.vocab import BROAD_MATCH, CLOSE_MATCH, EXACT_MATCH


@pytest.mark.unit
def test_valid_record_constructs() -> None:
    r = SSSOMRecord(
        subject_id="C3262",
        predicate_id=CLOSE_MATCH,
        object_id="UBERON:0002107",
        mapping_justification="semapv:LexicalMatching",
        confidence=0.8,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
    )
    assert r.subject_id == "C3262"
    assert r.predicate_id == CLOSE_MATCH
    assert r.object_id == "UBERON:0002107"
    assert r.lifecycle_state == "proposed"
    assert r.review_status == "unreviewed"
    assert r.author == ""


@pytest.mark.unit
def test_bad_predicate_rejected() -> None:
    with pytest.raises(ValueError, match="predicate_id not allowed"):
        SSSOMRecord(
            subject_id="C3262",
            predicate_id="http://example.com/not-skos",
            object_id="UBERON:0002107",
            mapping_justification="semapv:LexicalMatching",
            confidence=0.8,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
        )


@pytest.mark.unit
def test_bad_lifecycle_rejected() -> None:
    with pytest.raises(ValueError, match="lifecycle_state not allowed"):
        SSSOMRecord(
            subject_id="C3262",
            predicate_id=CLOSE_MATCH,
            object_id="UBERON:0002107",
            mapping_justification="semapv:LexicalMatching",
            confidence=0.8,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
            lifecycle_state="invalid",
        )


@pytest.mark.unit
def test_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="confidence out of range"):
        SSSOMRecord(
            subject_id="C3262",
            predicate_id=CLOSE_MATCH,
            object_id="UBERON:0002107",
            mapping_justification="semapv:LexicalMatching",
            confidence=1.5,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
        )


@pytest.mark.unit
def test_confidence_minimum_accepted() -> None:
    r = SSSOMRecord(
        subject_id="C3262",
        predicate_id=CLOSE_MATCH,
        object_id="UBERON:0002107",
        mapping_justification="semapv:LexicalMatching",
        confidence=0.0,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
    )
    assert r.confidence == 0.0


@pytest.mark.unit
def test_confidence_maximum_accepted() -> None:
    r = SSSOMRecord(
        subject_id="C3262",
        predicate_id=EXACT_MATCH,
        object_id="UBERON:0002107",
        mapping_justification="semapv:ManualMappingCuration",
        confidence=1.0,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
        author="tester",
    )
    assert r.confidence == 1.0
    assert r.author == "tester"


@pytest.mark.unit
def test_broad_match_is_allowed() -> None:
    r = SSSOMRecord(
        subject_id="C3262",
        predicate_id=BROAD_MATCH,
        object_id="UBERON:0002107",
        mapping_justification="semapv:LexicalMatching",
        confidence=0.5,
        subject_source_version="26.02d",
        object_source_version="uberon-2026-01",
    )
    assert r.predicate_id == BROAD_MATCH


@pytest.mark.unit
def test_empty_subject_id_rejected() -> None:
    with pytest.raises(ValueError, match="subject_id must be non-empty"):
        SSSOMRecord(
            subject_id="",
            predicate_id=CLOSE_MATCH,
            object_id="UBERON:0002107",
            mapping_justification="semapv:LexicalMatching",
            confidence=0.8,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
        )


@pytest.mark.unit
def test_empty_object_id_rejected() -> None:
    with pytest.raises(ValueError, match="object_id must be non-empty"):
        SSSOMRecord(
            subject_id="C3262",
            predicate_id=CLOSE_MATCH,
            object_id="",
            mapping_justification="semapv:LexicalMatching",
            confidence=0.8,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
        )


@pytest.mark.unit
def test_empty_mapping_justification_rejected() -> None:
    with pytest.raises(ValueError, match="mapping_justification must be non-empty"):
        SSSOMRecord(
            subject_id="C3262",
            predicate_id=CLOSE_MATCH,
            object_id="UBERON:0002107",
            mapping_justification="",
            confidence=0.8,
            subject_source_version="26.02d",
            object_source_version="uberon-2026-01",
        )


@pytest.mark.unit
def test_empty_subject_source_version_rejected() -> None:
    with pytest.raises(ValueError, match="subject_source_version must be non-empty"):
        SSSOMRecord(
            subject_id="C3262",
            predicate_id=CLOSE_MATCH,
            object_id="UBERON:0002107",
            mapping_justification="semapv:LexicalMatching",
            confidence=0.8,
            subject_source_version="",
            object_source_version="uberon-2026-01",
        )


@pytest.mark.unit
def test_empty_object_source_version_rejected() -> None:
    with pytest.raises(ValueError, match="object_source_version must be non-empty"):
        SSSOMRecord(
            subject_id="C3262",
            predicate_id=CLOSE_MATCH,
            object_id="UBERON:0002107",
            mapping_justification="semapv:LexicalMatching",
            confidence=0.8,
            subject_source_version="26.02d",
            object_source_version="",
        )
