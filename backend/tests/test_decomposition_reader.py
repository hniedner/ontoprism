"""Compact source rows avoid property-wide OPTIONAL joins on the published graph."""

from time import perf_counter

import pytest

from backend.config import get_settings
from backend.decomposition_reader import DecompositionReader, _expand_compact_rows
from ontolib.decomposition import vocab
from ontolib.decomposition.read import decomposition_from_rows
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client


async def test_compact_read_preserves_flags_and_constituent_provenance() -> None:
    class Client:
        async def select(self, query, *, required_variables=()):
            return [
                {
                    "subject": "concept",
                    "predicate": vocab.CONCEPT_OUTCOME,
                    "value": "decomposed",
                },
                {
                    "subject": "concept",
                    "predicate": vocab.OUTCOME_REASON,
                    "value": "two fillers",
                },
                {
                    "subject": "concept",
                    "predicate": vocab.REPRESENTATION_STATUS,
                    "value": vocab.LEGACY_PRECOORDINATED,
                },
                {
                    "subject": "concept",
                    "predicate": vocab.PUBLICATION_STATUS,
                    "value": "provisional",
                },
                {
                    "subject": "concept",
                    "predicate": vocab.PUBLICATION_NOTICE,
                    "value": vocab.EXPERT_REVIEW_NOTICE,
                },
                {
                    "subject": "concept",
                    "predicate": vocab.PUBLICATION_RUN,
                    "value": "run-1",
                },
                {"subject": "c1", "predicate": vocab.AXIS, "value": NCIT_NS + "R88"},
                {"subject": "c1", "predicate": vocab.FILLER, "value": NCIT_NS + "C2"},
                {"subject": "c1", "predicate": vocab.AXIS_SOURCE, "value": "role"},
                {
                    "subject": "c1",
                    "predicate": vocab.SOURCE_ROLE,
                    "value": NCIT_NS + "R88",
                },
                {
                    "subject": "f1",
                    "predicate": vocab.REVIEW_FLAG_KIND,
                    "value": "needs-review",
                },
                {
                    "subject": "f1",
                    "predicate": vocab.REVIEW_FLAG_REASON,
                    "value": "ambiguous",
                },
            ]

    result = decomposition_from_rows(
        "C1", await DecompositionReader(Client()).rows_for("C1")
    )
    assert result.run_id == "run-1"
    assert result.constituents[0].filler == "C2"
    assert result.constituents[0].source_roles == ("R88",)
    assert result.review_flags[0].reason == "ambiguous"


@pytest.mark.parametrize(
    "rows",
    [
        [{"subject": "concept", "predicate": vocab.HAS_CONSTITUENT, "value": "broken"}],
        [{"subject": "concept", "predicate": vocab.HAS_REVIEW_FLAG, "value": "broken"}],
        [
            {
                "subject": "concept",
                "predicate": vocab.CONCEPT_OUTCOME,
                "value": "unknown",
            },
            {
                "subject": "concept",
                "predicate": vocab.CONCEPT_OUTCOME,
                "value": "decomposed",
            },
        ],
    ],
)
def test_compact_read_rejects_missing_or_conflicting_source_fields(rows) -> None:
    with pytest.raises(
        ValueError, match=r"missing required|requires a kind|conflicting scalar"
    ):
        decomposition_from_rows("C1", _expand_compact_rows(rows))


@pytest.mark.integration
@pytest.mark.full_store
async def test_compact_read_matches_published_concept_without_slow_optional_scans() -> (
    None
):
    async with ncit_sparql_client(get_settings().ncit_sparql_url) as client:
        start = perf_counter()
        result = decomposition_from_rows(
            "C100054", await DecompositionReader(client).rows_for("C100054")
        )
        assert perf_counter() - start < 2
        assert result.run_id == "neoplasm-dc534534-08e0-4c24-86bc-f5a9ae3841e0"
        assert len(result.constituents) == 7
        assert len(result.review_flags) == 2
        assert result.constituents[0].source_roles == ("R100",)
        absent = decomposition_from_rows(
            "C999999999", await DecompositionReader(client).rows_for("C999999999")
        )
        assert absent.publication_status is None
        assert absent.constituents == []
