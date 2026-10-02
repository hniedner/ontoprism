"""Bootstrap smoke test: ontolib is importable and distribution-versioned."""

from importlib.metadata import version

import pytest

import ontolib


@pytest.mark.unit
def test_ontolib_importable_and_versioned() -> None:
    assert version("ontolib")
    assert not hasattr(ontolib, "__version__")
