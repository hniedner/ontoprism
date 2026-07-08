"""Decomposition engine orchestration and CLI (design section 9).

Pipeline: enumerate in-scope concepts, detect, extract, select,
NLP fallback, mint, write TTL, commit provenance.

Usage:
    pdm run decompose --branch neoplasm [--out path.ttl] [--load]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class RunConfig:
    """Configuration for a decomposition run."""

    branch: str
    out: Path | None = None
    load_to_store: bool = False
    emit_equivalence: bool = False
    resume_from: str | None = None


@dataclass
class RunMetrics:
    """Coverage metrics for a decomposition run."""

    total_in_scope: int = 0
    decomposed: int = 0
    residual: int = 0
    minted_count: int = 0
    pct_decomposed: float = 0.0

    @property
    def coverage(self) -> float:
        """Fraction of in-scope concepts successfully decomposed."""
        if self.total_in_scope == 0:
            return 0.0
        return self.decomposed / self.total_in_scope


async def run_pipeline(
    config: RunConfig,
) -> RunMetrics:
    """Execute the decomposition pipeline for a given branch."""
    metrics = RunMetrics()
    return metrics
