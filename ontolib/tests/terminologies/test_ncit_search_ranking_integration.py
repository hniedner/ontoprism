"""Search relevance, against a real Postgres -- the ranking a fake session cannot see.

The hermetic tests (``test_ncit_search_index.py``) drive a fake session, so they pin the
SQL *string* and can never observe what Postgres actually ranks first.  That gap hid a
real defect on two levels:

1. ``ncit_search.tsv`` concatenated ``label`` and ``synonyms`` into one *unweighted*
   ``to_tsvector``, and ``ts_rank`` rewards term *frequency* -- so a concept listing
   "neoplasm" across thirty synonyms outranked the concept whose label **is**
   "Neoplasm".
2. Every concept whose label contains the term once then scores *identically*, so the
   order fell through to the ``label`` tie-break, i.e. to alphabetical order.

On the live store the two together put C3262 (Neoplasm) at rank ~256 for the query
``neoplasm``, behind "Metastatic Thyrotroph Pituitary Neuroendocrine Tumor" -- whose
label does not contain the word at all.  The seeded CI fixture is small enough that
C3262 came back anyway, so every test passed.  Only a real index over a real corpus
shows it, which is why this file is integration-marked.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.terminologies.ncit.search_index import NcitSearchIndex

pytestmark = pytest.mark.integration

# A nonsense term, so the assertions cannot be perturbed by whatever real corpus happens
# to be loaded in the developer's Postgres.
_TERM = "zorbulax"
_EXACT = "TEST-EXACT"
_DECOY = "TEST-DECOY"
_LONGER = "TEST-LONGER"

_TYPE = "Neoplastic Process"

# The decoy's label does NOT contain the term -- it matches on synonyms alone, and
# repeatedly. That is the real shape of the bug: the concepts burying the exact match
# matched through their synonym lists, not through their names.
_SYNONYM_ROWS = [
    {"code": _EXACT, "label": "Zorbulax", "semantic_type": _TYPE, "syn": ""},
    {
        "code": _DECOY,
        "label": "Metastatic Compound Tumor of the Left Region",
        "semantic_type": _TYPE,
        "syn": (
            "zorbulax neoplasm; zorbulax lesion; zorbulax mass; zorbulax growth; "
            "zorbulax tumor; zorbulax carcinoma; zorbulax sarcoma"
        ),
    },
]

# Both labels carry the term once, at the same weight -- so ts_rank ties them and only
# the tie-break decides.
_LABEL_ROWS = [
    {"code": _EXACT, "label": "Zorbulax", "semantic_type": _TYPE, "syn": ""},
    {
        "code": _LONGER,
        "label": "Childhood Malignant Central Nervous System Zorbulax",
        "semantic_type": _TYPE,
        "syn": "",
    },
]

_INSERT = text(
    "INSERT INTO ncit_search (code, label, semantic_type, synonyms) "
    "VALUES (:code, :label, :semantic_type, :syn) "
    "ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label, "
    "semantic_type = EXCLUDED.semantic_type, synonyms = EXCLUDED.synonyms"
)
_DELETE = text("DELETE FROM ncit_search WHERE code = ANY(:codes)")


async def _search_over(rows: list[dict[str, str]], codes: list[str]) -> list[str]:
    """Seed *rows*, search for the term, clean up, and return the ranked codes."""
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as session:
            await session.execute(_INSERT, rows)
            await session.commit()
        try:
            page = await NcitSearchIndex(sf).search(_TERM, limit=10, offset=0)
            return [hit.code for hit in page.hits]
        finally:
            async with sf() as session:
                await session.execute(_DELETE, {"codes": codes})
                await session.commit()
    finally:
        await dispose_engine(engine)


async def test_an_exact_label_beats_a_synonym_only_match() -> None:
    """The concept *named* Zorbulax must beat one that merely mentions it seven times.

    With an unweighted ``tsv`` this fails: ``ts_rank`` counts occurrences, so the decoy
    -- which never uses the term as its name -- wins, and the concept the user actually
    searched for is buried.  Weighting the label ('A') above the synonyms ('B') is what
    makes the ranking mean what a reader assumes it means.
    """
    codes = await _search_over(_SYNONYM_ROWS, [_EXACT, _DECOY])

    assert codes, f"the index returned nothing for {_TERM!r}"
    assert codes[0] == _EXACT, (
        f"the concept whose LABEL is the search term must rank first; got {codes}. "
        "An exact name match buried under a synonym-heavy concept is what put NCIt's "
        "'Neoplasm' at rank ~256 for the query 'neoplasm'"
    )
    assert _DECOY in codes, "the synonym match must still be found, just ranked below"


async def test_the_concept_named_for_the_term_beats_longer_labels_containing_it() -> (
    None
):
    """Weighting the label is not enough on its own -- the tie-break decides.

    Both rows match the term in their label, at the same weight and the same frequency,
    so ``ts_rank`` scores them *identically* and the order falls through to the
    ``label``
    tie-break -- i.e. to alphabetical order, which is not relevance.  That is what left
    NCIt's "Neoplasm" behind hundreds of equally-ranked "... Neoplasm" concepts.
    """
    codes = await _search_over(_LABEL_ROWS, [_EXACT, _LONGER])

    assert codes[0] == _EXACT, (
        "a search for a concept's exact name must return that concept first; got "
        f"{codes}. Equal ts_rank scores fall through to an alphabetical tie-break, "
        "which is not relevance"
    )
