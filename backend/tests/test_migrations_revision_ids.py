"""Static consistency checks for Alembic migration revision ids.

No DB needed — these import each migration module and check its ``revision``
against its own filename. Guards against a revision id drifting from the filename
(e.g. a typo), which would silently produce a confusing Alembic history and break the
moment a future migration's ``down_revision`` assumes the filename-derived id.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _load_module(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_files() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.py"))


@pytest.mark.unit
@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.stem)
def test_migration_revision_matches_filename(path: Path) -> None:
    module = _load_module(path)
    assert module.revision == path.stem, (
        f"{path.name}: revision {module.revision!r} does not match filename "
        f"stem {path.stem!r}"
    )


@pytest.mark.unit
def test_migration_down_revision_chain_has_no_duplicates() -> None:
    modules = [_load_module(p) for p in _migration_files()]
    down_revisions = [m.down_revision for m in modules if m.down_revision is not None]
    assert len(down_revisions) == len(set(down_revisions)), (
        "duplicate down_revision — the migration chain has branched or a revision "
        "id collides with another migration's down_revision"
    )
