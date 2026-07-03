"""Bootstrap smoke test: fairlib is importable and versioned."""

import pytest

import fairlib


@pytest.mark.unit
def test_fairlib_importable_and_versioned() -> None:
    assert fairlib.__version__
