"""Search relevance, against a real Postgres -- the ranking a fake session cannot see.

The hermetic tests (``test_ncit_search_index.py``) drive a fake session, so they pin the
SQL *string* and can never observe what Postgres actually ranks first.  That gap hid a
real defect with two independent halves:

1. ``ncit_search.tsv`` concatenated ``label`` and ``synonyms`` into one *unweighted*
   ``to_tsvector``, and ``ts_rank`` rewards term *frequency* -- so a concept listing the
   term across thirty synonyms outranked the concept whose label **is** the term.
2. Concepts whose labels match with equal frequency then score *identically*, so the
   order fell through to an alphabetical ``label`` tie-break, which is not relevance.

The two protect **disjoint** query classes, so each needs its own gate: the exact-name
tier only fires when the query equals a label, while the weighting governs every partial
query ("breast neoplasm") -- i.e. most real searches.  An earlier cut of this file gave
the winning row a label *equal* to the query in both tests, so the exact-name tier fired
first and short-circuited ``ts_rank``: both tests then passed with migration 0005
reverted, certifying a gate that could never fail.  Hence the weighting test below uses
a winner whose label merely *contains* the term.

The labels are nonsense as well as the term: these rows are committed to the real,
shared ``ncit_search`` cache, and a row leaked by a killed process would otherwise be
served to real users searching for "metastatic tumor".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.terminologies.ncit.search_index import NcitSearchIndex

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

# A nonsense term, so the assertions cannot be perturbed by whatever real corpus happens
# to be loaded in the developer's Postgres.
_TERM = "zorbulax"
_TYPE = "Neoplastic Process"

_INSERT = text(
    "INSERT INTO ncit_search (code, label, semantic_type, synonyms) "
    "VALUES (:code, :label, :semantic_type, :syn) "
    "ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label, "
    "semantic_type = EXCLUDED.semantic_type, synonyms = EXCLUDED.synonyms"
)
# Sweeps rows a *previous* crashed run may have left behind, not only this one's.
_SWEEP = text("DELETE FROM ncit_search WHERE code LIKE 'TEST-%'")


@pytest.fixture(autouse=True)
async def _no_test_rows_left_behind() -> AsyncIterator[None]:
    """Sweep seeded rows before AND after -- a ``finally`` cannot survive a SIGKILL."""
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as session:
            await session.execute(_SWEEP)
            await session.commit()
        yield
        async with sf() as session:
            await session.execute(_SWEEP)
            await session.commit()
    finally:
        await dispose_engine(engine)


async def _ranked(rows: list[dict[str, str]]) -> list[str]:
    """Seed *rows*, search for the term, and return the ranked codes."""
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as session:
            await session.execute(_INSERT, rows)
            await session.commit()
        page = await NcitSearchIndex(sf).search(_TERM, limit=10, offset=0)
        return [hit.code for hit in page.hits]
    finally:
        await dispose_engine(engine)


async def test_a_label_match_beats_a_synonym_only_match() -> None:
    """GATE ON MIGRATION 0005, and on nothing else.

    Neither label *equals* the query, so the exact-name tier is false for both rows and
    cannot decide the order -- only the ``setweight``'d ``tsv`` can.  Revert migration
    0005 and this goes RED: unweighted, ``ts_rank`` counts raw frequency, so the decoy
    (which never uses the term as its name but lists it seven times as a synonym) wins,
    and the real match is buried.  That is the shape of the production bug.
    """
    codes = await _ranked(
        [
            {
                "code": "TEST-NAMED",
                "label": "Zorbulax Quibblewort Frobnicator",
                "semantic_type": _TYPE,
                "syn": "",
            },
            {
                "code": "TEST-DECOY",
                "label": "Snarfblat Wibbling of the Grommet",
                "semantic_type": _TYPE,
                "syn": (
                    "zorbulax snarfblat; zorbulax wibble; zorbulax grommet; "
                    "zorbulax flange; zorbulax widget; zorbulax sprocket; "
                    "zorbulax gasket"
                ),
            },
        ]
    )

    assert codes, f"the index returned nothing for {_TERM!r}"
    assert codes[0] == "TEST-NAMED", (
        "a concept carrying the term in its LABEL must outrank one that only lists it "
        f"as a synonym; got {codes}. Unweighted, ts_rank counts raw frequency -- which "
        "is what put NCIt's 'Neoplasm' behind synonym-heavy concepts"
    )
    assert "TEST-DECOY" in codes, "the synonym match must still be found, ranked below"


async def test_the_concept_named_for_the_term_beats_longer_labels() -> None:
    """GATE ON THE ORDER BY, and on nothing else.

    Both labels carry the term once at weight 'A', so ``ts_rank`` scores them
    *identically* and the weighting cannot separate them -- only the exact-name tier
    can.  Without it the order falls through to the alphabetical ``label`` tie-break,
    which is what left NCIt's "Neoplasm" behind hundreds of equally-ranked
    "... Neoplasm" concepts.
    """
    codes = await _ranked(
        [
            {
                "code": "TEST-EXACT",
                "label": "Zorbulax",
                "semantic_type": _TYPE,
                "syn": "",
            },
            {
                "code": "TEST-LONGER",
                "label": "Blithering Quibblewort Snarfblat Zorbulax",
                "semantic_type": _TYPE,
                "syn": "",
            },
        ]
    )

    assert codes[0] == "TEST-EXACT", (
        "a search for a concept's exact name must return that concept first; got "
        f"{codes}. Equal ts_rank scores fall through to an alphabetical tie-break, "
        "which is not relevance"
    )


async def test_a_trailing_space_does_not_disable_the_exact_name_tier() -> None:
    """``:q`` is the raw user string, and a paste carries whitespace.

    Before ``btrim``, ``lower(label) = lower(:q)`` silently missed for ``"zorbulax "``
    and the query fell straight back to the tie-break that caused the bug -- a failure
    that returns 200 OK with plausible-looking results and no signal at all.
    """
    rows = [
        {"code": "TEST-EXACT", "label": "Zorbulax", "semantic_type": _TYPE, "syn": ""},
        {
            "code": "TEST-LONGER",
            "label": "Blithering Quibblewort Snarfblat Zorbulax",
            "semantic_type": _TYPE,
            "syn": "",
        },
    ]
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as session:
            await session.execute(_INSERT, rows)
            await session.commit()
        page = await NcitSearchIndex(sf).search(f"{_TERM} ", limit=10, offset=0)
    finally:
        await dispose_engine(engine)

    codes = [hit.code for hit in page.hits]
    assert codes[0] == "TEST-EXACT", (
        f"a trailing space disabled the exact-name tier; got {codes}"
    )


async def test_the_index_weights_the_label_above_the_synonyms() -> None:
    """Pin the SCHEMA, not only the behaviour: what did migration 0005 actually build?

    ``ts_rank`` runs happily against an unweighted ``tsvector`` -- no error, only worse
    answers -- so a 0005 that dropped ``setweight``, or transposed 'A' and 'B' (ranking
    synonyms *above* names, strictly worse than the bug we started from), would be
    syntactically valid and silently wrong.  Ask Postgres what the column computes.
    """
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    try:
        async with sf() as session:
            expression = await session.scalar(
                text(
                    "SELECT generation_expression FROM information_schema.columns "
                    "WHERE table_name = 'ncit_search' AND column_name = 'tsv'"
                )
            )
    finally:
        await dispose_engine(engine)

    assert expression, "ncit_search.tsv is not a generated column"
    normalized = " ".join(str(expression).split())
    assert "setweight" in normalized, (
        "ncit_search.tsv is not weighted -- migration 0005 was reverted or never ran, "
        "so every synonym-heavy concept outranks the concept named for the term. Got: "
        f"{normalized}"
    )
    assert normalized.index("'A'") < normalized.index("synonyms"), (
        f"weight 'A' must apply to the label, not the synonyms. Got: {normalized}"
    )
    assert normalized.index("'B'") > normalized.index("label"), (
        "weight 'B' must apply to the synonyms, not the label -- transposed weights "
        f"rank synonyms above names. Got: {normalized}"
    )
