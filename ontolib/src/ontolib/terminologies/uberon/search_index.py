"""Source-bound PostgreSQL full-text cache for Uberon/CL concepts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from ontolib.common import search_publication
from ontolib.common.grid import categorical_predicate, sql_text_filters
from ontolib.terminologies.uberon.models import (
    UberonSearchHit,
    UberonSearchPage,
    UberonSearchSort,
    UberonSource,
)

UberonSearchPublicationError = search_publication.SearchPublicationError
populate_from_store = search_publication.populate_search_index

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_SEARCH_SQL = r"""
SELECT code, source, label
FROM uberon_search, websearch_to_tsquery('english', :q) AS q
WHERE tsv @@ q
  {column_filters}
"""
_SEARCH_COUNT_SQL = """
SELECT COUNT(*)
FROM uberon_search, websearch_to_tsquery('english', :q) AS q
WHERE tsv @@ q
  {column_filters}
"""
# Relevance prioritizes an exact normalized label, then weighted term rank, then
# shorter labels, and finally label/code as a deterministic identity tie-break.
_SEARCH_ORDERS: dict[UberonSearchSort, str] = {
    "relevance": (
        r"""(lower(label) = lower(btrim(:q, E' \t\r\n"'))) DESC, """
        "ts_rank(tsv, q) DESC, length(label), label, code"
    ),
    "source": "code",
    "code:asc": "code",
    "code:desc": "code DESC",
    "label:asc": "label NULLS LAST, code",
    "label:desc": "label DESC NULLS LAST, code",
}
_SEARCH_TEXT_EXPRESSIONS = {
    "code": "code",
    "label": "label",
    "source": "CASE source WHEN 'cl' THEN 'Cell Ontology' ELSE 'Uberon' END",
}
_UPSERT_SQL = """
INSERT INTO uberon_search (code, source, label, synonyms)
VALUES (:code, :source, :label, :synonyms)
ON CONFLICT (code) DO UPDATE SET source = EXCLUDED.source, label = EXCLUDED.label,
  synonyms = EXCLUDED.synonyms
"""


def _search_filters(
    column_text: dict[str, str], source: UberonSource | None
) -> tuple[str, dict[str, str | list[str]]]:
    predicates, text_params = sql_text_filters(column_text, _SEARCH_TEXT_EXPRESSIONS)
    source_predicate, source_params = categorical_predicate(
        "source",
        [source] if source else [],
        expression="source",
        multiple=False,
        dialect="sql",
    )
    return predicates + source_predicate, {**text_params, **source_params}


class UberonSearchIndex(search_publication.SearchIndexPublication):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(
            session_factory,
            table="uberon_search",
            subject="Uberon/CL",
            upsert_sql=_UPSERT_SQL,
            bind_source_hash=True,
            record_key="code",
        )

    async def search(
        self,
        query: str,
        *,
        source: UberonSource | None = None,
        limit: int = 25,
        offset: int = 0,
        sort: UberonSearchSort = "relevance",
        column_text: dict[str, str] | None = None,
    ) -> UberonSearchPage:
        async with self._sf() as session:
            params: dict[str, str | int | list[str]] = {
                "q": query,
                "limit": limit,
                "offset": offset,
            }
            predicates, bindings = _search_filters(column_text or {}, source)
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
        return UberonSearchPage(
            query=query,
            total=int(count_result.scalar_one()),
            limit=limit,
            offset=offset,
            sort=sort,
            source=source,
            column_text=column_text or {},
            hits=[
                UberonSearchHit(code=row.code, source=row.source, label=row.label)
                for row in rows
            ],
        )
