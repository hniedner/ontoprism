from pathlib import Path

import pytest

from backend.config import get_settings
from ontolib.repositories.icdo.congruence import build_congruence_report
from ontolib.repositories.icdo.ingest import ingest_icdo4
from ontolib.repositories.icdo.store import canonical_sha256
from ontolib.terminologies.sparql_http_client import SparqlHttpClient
from ontolib.terminologies.uberon.graph_store import UberonGraphStore

pytestmark = [pytest.mark.integration, pytest.mark.full_store]
SOURCE = Path(__file__).parents[4] / "tmp/data/icdo"


@pytest.mark.integration
async def test_real_406_code_report_is_complete_source_bound_and_non_mapping() -> None:
    topography = ingest_icdo4(
        SOURCE / "ICD-O-4.zip",
        morphology_annex_path=SOURCE / "Morphology_annexes.xlsx",
        topography_annex_path=SOURCE / "Topography_annexes.xlsx",
    ).topography
    settings = get_settings()
    client = SparqlHttpClient.for_qlever(settings.uberon_sparql_url)
    store = UberonGraphStore(client)
    records: list[dict[str, str]] = []
    try:
        offset = 0
        while True:
            batch = await store.congruence_records(limit=5000, offset=offset)
            if not batch:
                break
            records.extend(
                {
                    key: value or ""
                    for key, value in row.items()
                    if key in {"code", "label", "synonyms", "parents"}
                }
                for row in batch
            )
            offset += 5000
    finally:
        await client.aclose()
    report = build_congruence_report(
        topography,
        icdo_serving_identity=canonical_sha256(topography),
        uberon_serving_identity=settings.uberon_expected_serving_sha256,
        uberon_records=tuple(records),
    )
    assert report.total == 406
    assert sum(report.counts.values()) == 406
    assert len({row.code for row in report.rows}) == 406
    assert (
        next(row for row in report.rows if row.code == "C80.9").classification
        == "intentionally-unresolved"
    )
    assert {row.classification for row in report.rows} >= {
        "one-supported-candidate",
        "multiple-candidates",
        "no-candidate",
        "broader-narrower-mismatch",
        "intentionally-unresolved",
    }
    assert all(not hasattr(row, "predicate") for row in report.rows)
