"""Bootstrap smoke test: ontolib is importable and versioned."""

import pytest

import ontolib


@pytest.mark.unit
def test_ontolib_importable_and_versioned() -> None:
    assert ontolib.__version__
