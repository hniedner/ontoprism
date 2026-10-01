"""Materialized full-text search over NCIt concepts (Postgres tsvector + GIN)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from ontolib.common import search_publication
from ontolib.common.grid import categorical_predicate, sql_text_filters
from ontolib.terminologies.ncit.models import (
    RepositorySearchSort,
    RepresentationStatus,
    SearchHit,
    SearchPage,
)

populate_from_store = search_publication.populate_search_index

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# websearch_to_tsquery gives users familiar query syntax (quoted phrases, OR, -term)
# while being injection-safe. Relevance ordering has four load-bearing tiers:
#
# 1. An EXACT NAME MATCH wins. `ts_rank` scores by weighted term frequency, so every
#    concept whose label contains the term once scores *identically* -- searching
#    "neoplasm" tied C3262 (whose label IS "Neoplasm") with hundreds of "... Neoplasm"
#    concepts. `:q` is the RAW user string, so the probe must normalize it: a trailing
#    space, a newline or tab from a paste, or a quoted phrase ("neoplasm") each made the
#    comparison miss and dropped C3262 straight back behind "Abdominal Neoplasm,
#    Excluding Pancreas Neoplasm" -- which wins on ts_rank because it says the word
#    twice. Tier 3 does NOT rescue that: ts_rank separates them before length is ever
#    consulted. Hence btrim over whitespace AND quotes.
#    Genuine DSL queries (`OR`, `-x`) still never match a label, by design -- they carry
#    more than one term, so no single concept is "the" name for them.
# 2. THEN WEIGHTED RELEVANCE. `tsv` is `setweight`ed at the index (migration
#    0005_search_weights): label 'A', synonyms 'B'. Without it, a concept listing the
#    term across thirty synonyms outranked the concept *named* for it, because raw
#    frequency was all that counted. This is the tier that governs every *partial*
#    query ("breast neoplasm"), which is most real searches -- tier 1 cannot help there.
# 3. THEN THE SHORTER LABEL. Equal relevance breaks toward the more specific name, not
#    toward the alphabet. Without this, a `ts_rank` tie fell through to alphabetical
#    order, which is not relevance -- it is what buried "Neoplasm" behind hundreds of
#    equally-scored "... Neoplasm" concepts, and it is what a quoted or operator query
#    (which tier 1 cannot rescue) would still hit.
# 4. `label`, then `code`, purely to make the order TOTAL. `label` is not unique in
#    NCIt (204,373 rows, 204,238 distinct labels), so without the primary key the sort
#    is underdetermined and a tied row can appear on two pages of a LIMIT/OFFSET walk,
#    or on none.
_SEARCH_SQL = r"""
    SELECT code, label, semantic_types, representation_status
    FROM ncit_search, websearch_to_tsquery('english', :q) AS q
    WHERE tsv @@ q
      {column_filters}
"""
_SEARCH_COUNT_SQL = r"""
    SELECT COUNT(*)
    FROM ncit_search, websearch_to_tsquery('english', :q) AS q
    WHERE tsv @@ q
      {column_filters}
"""
_SEARCH_ORDERS: dict[RepositorySearchSort, str] = {
    "relevance": (
        r"""(lower(label) = lower(btrim(:q, E' \t\r\n"'))) DESC, """
        "ts_rank(tsv, q) DESC, length(label), label, code"
    ),
    "source": "code",
    "code:asc": "code",
    "code:desc": "code DESC",
    "label:asc": "label NULLS LAST, code",
    "label:desc": "label DESC NULLS LAST, code",
    "semantic_type:asc": (
        "NULLIF(array_to_string(semantic_types, ', '), '') COLLATE \"C\" "
        "NULLS LAST, code"
    ),
    "semantic_type:desc": (
        "NULLIF(array_to_string(semantic_types, ', '), '') COLLATE \"C\" DESC "
        "NULLS LAST, code"
    ),
}
_SEARCH_TEXT_EXPRESSIONS = {
    "code": "code",
    "label": "label",
    "semantic_type": "array_to_string(semantic_types, ', ')",
    "representation_status": (
        "CASE WHEN representation_status = 'legacy-precoordinated' "
        "THEN 'Legacy pre-coordinated' ELSE '' END"
    ),
}

_UPSERT_SQL = """
    INSERT INTO ncit_search (
        code, label, semantic_types, synonyms, representation_status
    )
    VALUES (
        :code, :label, :semantic_types, :synonyms, :representation_status
    )
    ON CONFLICT (code) DO UPDATE SET
        label = EXCLUDED.label,
        semantic_types = EXCLUDED.semantic_types,
        synonyms = EXCLUDED.synonyms,
        representation_status = EXCLUDED.representation_status
"""


def _search_filters(
    column_text: dict[str, str] | None,
    representation_status: RepresentationStatus | None,
    semantic_types: list[str] | None,
) -> tuple[str, dict[str, str | list[str]]]:
    predicates, text_params = sql_text_filters(
        column_text or {}, _SEARCH_TEXT_EXPRESSIONS
    )
    params: dict[str, str | list[str]] = dict(text_params)
    selections = (
        (
            "representation_status",
            [representation_status] if representation_status else [],
            False,
        ),
        ("semantic_types", semantic_types or [], True),
    )
    for column, selected, array_column in selections:
        predicate, bindings = categorical_predicate(
            column,
            selected,
            expression=column,
            array_column=array_column,
            dialect="sql",
        )
        predicates += predicate
        params.update(bindings)
    return predicates, params


class NcitSearchIndex(search_publication.SearchIndexPublication):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(
            session_factory,
            table="ncit_search",
            subject="NCIt",
            upsert_sql=_UPSERT_SQL,
            record_key="code",
        )

    async def search(
        self,
        query: str,
        *,
        limit: int = 25,
        offset: int = 0,
        representation_status: RepresentationStatus | None = None,
        sort: RepositorySearchSort = "relevance",
        column_text: dict[str, str] | None = None,
        semantic_types: list[str] | None = None,
    ) -> SearchPage:
        """Search one page and count all matches with two bounded SQL statements."""
        async with self._sf() as session:
            params = {
                "q": query,
                "limit": limit,
                "offset": offset,
            }
            predicates, bindings = _search_filters(
                column_text, representation_status, semantic_types
            )
            params.update(bindings)
            count_result = await session.execute(
                text(_SEARCH_COUNT_SQL.format(column_filters=predicates)), params
            )
            sql = (
                f"{_SEARCH_SQL.format(column_filters=predicates)}\n"
                f"ORDER BY {_SEARCH_ORDERS[sort]} "
                "LIMIT :limit OFFSET :offset"
            )
            result = await session.execute(
                text(sql),
                params,
            )
            rows = result.all()
        total = int(count_result.scalar_one())
        hits = [
            SearchHit(
                code=row.code,
                label=row.label,
                semantic_types=row.semantic_types,
                matched_synonym=None,
                representation_status=row.representation_status,
            )
            for row in rows
        ]
        return SearchPage(
            query=query,
            total=total,
            limit=limit,
            offset=offset,
            sort=sort,
            representation_status=representation_status,
            column_text=column_text or {},
            semantic_types=semantic_types or [],
            hits=hits,
        )
