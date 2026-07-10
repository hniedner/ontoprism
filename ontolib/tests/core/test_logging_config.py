from __future__ import annotations

import logging

import pytest

from ontolib.core.logging_config import get_logger


@pytest.mark.unit
def test_get_logger_nests_external_name() -> None:
    logger = get_logger("requests")
    assert logger.name == "ontolib.requests"
    assert isinstance(logger, logging.Logger)


@pytest.mark.unit
def test_get_logger_returns_ontolib_root_directly() -> None:
    logger = get_logger("ontolib")
    assert logger.name == "ontolib"


@pytest.mark.unit
def test_get_logger_returns_submodule_directly() -> None:
    logger = get_logger("ontolib.terminologies.ncit")
    assert logger.name == "ontolib.terminologies.ncit"
