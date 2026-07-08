"""Unit tests for the decomposition run orchestrator."""

from __future__ import annotations

import pytest

from ontolib.decomposition.run import (
    RunConfig,
    RunMetrics,
    run_pipeline,
)


@pytest.mark.unit
async def test_run_pipeline_skeleton_returns_metrics() -> None:
    config = RunConfig(branch="neoplasm")
    metrics = await run_pipeline(config)
    assert isinstance(metrics, RunMetrics)
    assert metrics.coverage == 0.0


@pytest.mark.unit
def test_run_metrics_coverage_zero_when_empty() -> None:
    m = RunMetrics()
    assert m.coverage == 0.0


@pytest.mark.unit
def test_run_metrics_coverage_computed_correctly() -> None:
    m = RunMetrics(total_in_scope=100, decomposed=85)
    assert m.coverage == pytest.approx(0.85)


@pytest.mark.unit
def test_run_config_defaults() -> None:
    cfg = RunConfig(branch="neoplasm")
    assert cfg.branch == "neoplasm"
    assert cfg.out is None
    assert not cfg.load_to_store
