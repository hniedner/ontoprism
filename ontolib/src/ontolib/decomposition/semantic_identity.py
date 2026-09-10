"""Content-derived identity for decomposition routing and detector semantics."""

from __future__ import annotations

import hashlib
from pathlib import Path

_INVENTORY_VERSION = b"ontoprism-routing-implementation-files-v1\0"
ROUTING_IMPLEMENTATION_FILES = (
    Path("ontolib/src/ontolib/decomposition/axes.py"),
    Path("ontolib/src/ontolib/decomposition/axis_contracts.py"),
    Path("ontolib/src/ontolib/decomposition/branches.py"),
    Path("ontolib/src/ontolib/decomposition/collapse_policy.py"),
    Path("ontolib/src/ontolib/decomposition/complete_definition.py"),
    Path("ontolib/src/ontolib/decomposition/detector.py"),
    Path("ontolib/src/ontolib/decomposition/filler_selection.py"),
    Path("ontolib/src/ontolib/decomposition/models.py"),
    Path("ontolib/src/ontolib/decomposition/site_resolution.py"),
    Path("ontolib/src/ontolib/decomposition/stated_queries.py"),
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def routing_implementation_identity(
    root: Path | None = None,
    *,
    inventory: tuple[Path, ...] = ROUTING_IMPLEMENTATION_FILES,
) -> str:
    """Hash the versioned relative inventory and exact bytes, without metadata."""
    base = root or _repository_root()
    if not inventory or len(inventory) != len(set(inventory)):
        raise ValueError("routing implementation inventory must be unique and nonempty")
    digest = hashlib.sha256(_INVENTORY_VERSION)
    for relative in inventory:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("routing implementation inventory paths must be relative")
        encoded = relative.as_posix().encode("utf-8")
        content = (base / relative).read_bytes()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
