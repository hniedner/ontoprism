"""Cross-process serialization for tests that scan or mutate the live test tree."""

from __future__ import annotations

import fcntl
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_TREE_SCAN_LOCK = Path(tempfile.gettempdir()) / "ontoprism-tree-scan.lock"


@contextmanager
def exclusive_tree_scan() -> Iterator[None]:
    """Prevent a live-tree scan from overlapping a temporary test-tree mutation."""
    with _TREE_SCAN_LOCK.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
