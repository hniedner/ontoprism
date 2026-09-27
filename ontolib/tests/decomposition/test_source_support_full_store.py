"""Read-only contract against the published #127 run and configured stated NCIt."""

from collections import Counter

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import get_settings
from ontolib.decomposition.complete_definition import (
    _definition_slice_from_rows,
    _read_anchor_definition_rows,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.terminologies.ncit.client import ncit_sparql_client

pytestmark = [pytest.mark.integration, pytest.mark.full_store]
_RUN = "neoplasm-dc534534-08e0-4c24-86bc-f5a9ae3841e0"


async def test_published_filler_sources_match_live_stated_facts() -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        store = ProvenanceStore(async_sessionmaker(engine))
        run = await store.get_run(_RUN)
        assert run is not None
        assert run.publication_state == "published"
        records = await store.constituent_evidence(_RUN, "C100054")
        assert {r.support for r in records} == {"restriction-backed", "genus-backed"}
        async with ncit_sparql_client(settings.ncit_sparql_url) as client:
            for item in records:
                for source in item.sources:
                    rows = await _read_anchor_definition_rows(
                        client.select_once, source.anchor_code
                    )
                    definition = _definition_slice_from_rows(
                        source.anchor_code,
                        depth=source.depth,
                        rows=rows,
                        root_code=item.concept_code,
                    )
                    assert any(f.fact_id == source.fact_id for f in definition.facts)
                    if source.kind == "restriction":
                        assert any(
                            o.occurrence_id == source.occurrence_id
                            and o.filler_code == item.filler_code
                            and o.role_code == source.role_code
                            for o in definition.occurrences
                        )
        stage = await store.constituent_evidence(_RUN, "C6135")
        minted = next(r for r in stage if r.filler_code == "MINT-8512b41a844d")
        assert minted.support == "not-source-backed"
        assert minted.sources == []
        assert minted.inferred_assertions
    finally:
        await engine.dispose()


async def test_published_delta_covers_all_roles_and_real_collapse_targets() -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        store = ProvenanceStore(async_sessionmaker(engine))
        for code, counts in [
            (
                "C115029",
                {
                    "projected": 6,
                    "represented-through-collapse": 5,
                    "not-projected": 11,
                    "not-considered": 12,
                },
            ),
            ("C100054", {"projected": 6, "not-projected": 1, "not-considered": 2}),
        ]:
            rows = await store.enhancement_delta(_RUN, code)
            assert Counter(row.category for row in rows) == counts
            assert len({row.occurrence_id for row in rows}) == sum(counts.values())
            async with ncit_sparql_client(settings.ncit_sparql_url) as client:
                for anchor in {r.anchor_code for r in rows}:
                    source = next(r for r in rows if r.anchor_code == anchor)
                    definition = _definition_slice_from_rows(
                        anchor_code=anchor,
                        depth=source.depth,
                        rows=await _read_anchor_definition_rows(
                            client.select_once, anchor
                        ),
                        root_code=code,
                    )
                    assert {
                        (r.occurrence_id, r.role_code, r.filler_code)
                        for r in rows
                        if r.anchor_code == anchor
                    } == {
                        (o.occurrence_id, o.role_code, o.filler_code)
                        for o in definition.occurrences
                    }
            targets = await store.constituent_evidence(_RUN, code)
            for row in rows:
                if row.category == "represented-through-collapse":
                    assert not row.links
                    assert (row.normalized_axis, row.retained_filler) in {
                        (t.axis, t.filler_code) for t in targets
                    }
                if row.conservation_reason == "missing-disposition":
                    assert row.reason == "missing-disposition"
                if row.disposition is None and row.role_code == "R104":
                    assert row.category == "not-projected"
                    assert row.walker_max_depth == 7
                    assert row.reason == (
                        "beyond the walker depth bound (D58)"
                        if row.depth >= 7
                        else "dropped by a projection rule (generic filler, held role "
                        "or inherited non-core role); this engine version records "
                        "no per-fact reason"
                    )
    finally:
        await engine.dispose()
