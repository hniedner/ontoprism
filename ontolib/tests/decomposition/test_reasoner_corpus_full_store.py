"""Transient corpus negative control; never changes configured assertions."""

import json
from pathlib import Path
from time import perf_counter

import pytest
from sqlalchemy import text

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.core.robot import RobotSettings
from ontolib.decomposition.reasoner_check import check_projection

pytestmark = [
    pytest.mark.integration,
    pytest.mark.full_store,
    pytest.mark.mutating_integration,
]


async def test_corpus_range_conflict_is_attributed_to_transient_assertion(
    tmp_path, capsys
):
    start = perf_counter()
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT concept_code,axis,filler_code,source_roles "
                            "FROM decomp_constituent WHERE run_id=:run "
                            "ORDER BY concept_code,axis,filler_code"
                        ),
                        {"run": "neoplasm-dc534534-08e0-4c24-86bc-f5a9ae3841e0"},
                    )
                )
                .mappings()
                .all()
            )
        assertions = [dict(row) for row in rows]
    finally:
        await dispose_engine(engine)
    # Fresh class has no descendants to inflate per-assertion attribution work.
    planted = {
        "concept_code": "C999999999351",
        "axis": "op:PrimarySite",
        "filler_code": "C12218",
        "source_roles": ["R101"],
    }
    assert not any(row["concept_code"] == planted["concept_code"] for row in assertions)
    read_done = perf_counter()
    result = check_projection(
        Path(settings.ncit_owl_dir) / "Thesaurus-stated.owl",
        [*assertions, planted],
        tmp_path / "probe",
        RobotSettings().robot_config(),
    )
    with capsys.disabled():
        print(
            json.dumps({k: v for k, v in result.items() if k != "el_profile"}, indent=2)
        )
        print("read_seconds", read_done - start, "test_seconds", perf_counter() - start)
    assert result["new_unsatisfiable"] == [planted["concept_code"]]
    assert result["contradictory_assertions"] == [planted]
