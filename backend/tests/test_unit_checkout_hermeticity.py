"""Clean-checkout contracts for decomposition unit-test inputs."""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FILES = (
    "ontolib/tests/decomposition/test_pre_sme_readiness.py",
    "ontolib/tests/decomposition/test_r101_review.py",
    "ontolib/tests/decomposition/test_group_review_evidence.py",
)


def _fixed_ignored_path(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
        and node.args
    ):
        return _fixed_ignored_path(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _fixed_ignored_path(node.right)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("data/", "tmp/"))
    ):
        return node.value
    return None


@pytest.mark.unit
def test_changed_unit_contracts_do_not_read_fixed_gitignored_inputs() -> None:
    """Reject literal repo data/tmp inputs while allowing pytest-owned tmp_path use."""
    violations: list[str] = []
    for relative in _FILES:
        source = (_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Call, ast.BinOp)):
                continue
            ignored_path = _fixed_ignored_path(node)
            if ignored_path is not None:
                violations.append(
                    f"{relative}:{getattr(node, 'lineno', 0)}: {ignored_path}"
                )

    assert violations == []
