"""Read-only configured caDSR embedding-source fingerprint contract."""

import pytest

from backend.config import get_settings
from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.repositories.embeddings.generate import cadsr_source_fingerprint

pytestmark = [pytest.mark.integration, pytest.mark.full_store, pytest.mark.full_build]

_CADSR_SOURCE_FINGERPRINT = (
    "eae0cd8f67e11452f38a9b0de78d8d3d17b057f2da1979a6ffcee430193603b3"
)


def test_cadsr_embedding_source_count_sentinel_and_fingerprint() -> None:
    settings = get_settings()
    count, fingerprint = cadsr_source_fingerprint(settings.cadsr_db_path)
    sentinel = CdeRepository(settings.cadsr_db_path).get_cde("2517527", "4")

    assert count == settings.cadsr_embedding_expected_rows == 79_827
    assert fingerprint == _CADSR_SOURCE_FINGERPRINT
    assert sentinel is not None
