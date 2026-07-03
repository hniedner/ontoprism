#!/usr/bin/env python3
"""Pre-commit hook to detect test anti-patterns using AST analysis.

Hard failures (block the commit):
1. Tests whose only assertions are mock.assert_called* / assert_awaited* / call_count
2. Module docstrings containing "improve coverage" or "to X%"
3. Tests whose only assertion is assert callable(...)

Warnings (advisory):
4. Tests with >5 @patch / with patch(...) usages
"""

import ast
import re
import sys
from pathlib import Path

MOCK_ASSERT_PREFIXES = ("assert_called", "assert_awaited")
COVERAGE_DOCSTRING_RE = re.compile(r"(improve\s+coverage|to\s+\d+\s*%)", re.IGNORECASE)

MIN_ARGS = 2


class TestQualityVisitor(ast.NodeVisitor):
    """AST visitor that checks test functions for anti-patterns."""

    def __init__(self, filename: str, source_lines: list[str]) -> None:
        """Initialize visitor."""
        self.filename = filename
        self.source_lines = source_lines
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check test functions for anti-patterns."""
        if not node.name.startswith("test_"):
            self.generic_visit(node)
            return

        assertions = self._collect_assertions(node)

        if assertions:
            self._check_mock_only_assertions(node, assertions)
            self._check_callable_only_assertions(node, assertions)

        self._check_excessive_patches(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Delegate to visit_FunctionDef."""
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def _collect_assertions(self, node: ast.AST) -> list[ast.AST]:
        """Collect all assertion nodes from a function body."""
        assertions: list[ast.AST] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                assertions.append(child)
            elif isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                func = child.value.func
                if self._is_mock_assert(func):
                    assertions.append(child)
        return assertions

    def _is_mock_assert(self, func: ast.expr) -> bool:
        """Check if a call is a mock assertion (mock.assert_called*)."""
        if isinstance(func, ast.Attribute):
            return func.attr.startswith(MOCK_ASSERT_PREFIXES)
        return False

    def _is_mock_call_count_assert(self, node: ast.Assert) -> bool:
        """Check if an assert is `assert mock.call_count ...`."""
        test = node.test
        # assert x.call_count == N
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Attribute):
            return test.left.attr == "call_count"
        # assert x.call_count
        if isinstance(test, ast.Attribute):
            return test.attr == "call_count"
        return False

    def _is_mock_expr_assertion(self, a: ast.AST) -> bool:
        """Return True if node is a mock.assert_called* expression statement."""
        return (
            isinstance(a, ast.Expr)
            and isinstance(a.value, ast.Call)
            and self._is_mock_assert(a.value.func)
        )

    def _is_mock_only_assertion(self, a: ast.AST) -> bool:
        """Return True if node is exclusively a mock-style assertion."""
        if self._is_mock_expr_assertion(a):
            return True
        return isinstance(a, ast.Assert) and self._is_mock_call_count_assert(a)

    def _check_mock_only_assertions(
        self, node: ast.FunctionDef, assertions: list[ast.AST]
    ) -> None:
        """FAIL if every assertion is a mock assertion or call_count.

        ontoprism blocks mock-only tests outright (not a warning): a test whose only
        assertions are ``mock.assert_called*`` / ``call_count`` verifies the mock, not
        the system. Assert on real return values / observable behavior instead.
        """
        if all(self._is_mock_only_assertion(a) for a in assertions):
            self.failures.append(
                f"{self.filename}:{node.lineno}: "
                f"test '{node.name}' only asserts mock interactions — "
                f"add assertions on actual behavior or return values"
            )

    def _check_callable_only_assertions(
        self, node: ast.FunctionDef, assertions: list[ast.AST]
    ) -> None:
        """FAIL if every assertion is `assert callable(...)`."""
        all_callable = True
        for a in assertions:
            if isinstance(a, ast.Assert) and self._is_callable_assert(a.test):
                continue
            all_callable = False
            break

        if all_callable:
            self.failures.append(
                f"{self.filename}:{node.lineno}: "
                f"test '{node.name}' only asserts callable() — "
                f"add assertions that verify actual behavior"
            )

    def _is_callable_assert(self, node: ast.expr) -> bool:
        """Check if expression is callable(...)."""
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "callable":
                return True
        return False

    def _count_decorator_patches(self, node: ast.FunctionDef) -> int:
        """Count @patch decorators on a function."""
        return sum(1 for d in node.decorator_list if self._is_patch_call(d))

    def _count_with_patches(self, node: ast.FunctionDef) -> int:
        """Count `with patch(...)` context managers inside a function."""
        count = 0
        for child in ast.walk(node):
            if isinstance(child, ast.With):
                count += sum(
                    1 for item in child.items if self._is_patch_call(item.context_expr)
                )
        return count

    def _check_excessive_patches(self, node: ast.FunctionDef) -> None:
        """WARN if a test function has >5 @patch / with patch(...)."""
        patch_count = self._count_decorator_patches(node) + self._count_with_patches(
            node
        )
        max_patches = 5
        if patch_count > max_patches:
            self.warnings.append(
                f"{self.filename}:{node.lineno}: "
                f"test '{node.name}' uses {patch_count} patches "
                f"(>{max_patches}) — consider reducing dependencies "
                f"or refactoring the code under test"
            )

    def _is_patch_call(self, node: ast.expr) -> bool:
        """Check if an expression is a patch() or patch.object() call."""
        if isinstance(node, ast.Call):
            return self._is_patch_ref(node.func)
        return self._is_patch_ref(node)

    def _is_patch_attr(self, node: ast.Attribute) -> bool:
        """Check if an Attribute node references patch."""
        if node.attr == "patch":
            return True
        if isinstance(node.value, ast.Attribute) and node.value.attr == "patch":
            return True
        return isinstance(node.value, ast.Name) and node.value.id == "patch"

    def _is_patch_ref(self, node: ast.expr) -> bool:
        """Check if a node references unittest.mock.patch."""
        if isinstance(node, ast.Attribute):
            return self._is_patch_attr(node)
        return isinstance(node, ast.Name) and node.id == "patch"


def check_module_docstring(filepath: Path, tree: ast.Module) -> str | None:
    """FAIL if module docstring contains coverage-gaming language."""
    docstring = ast.get_docstring(tree)
    if docstring and COVERAGE_DOCSTRING_RE.search(docstring):
        return (
            f"{filepath}:1: module docstring contains coverage-gaming "
            f"language — tests should describe what they verify, "
            f"not coverage targets"
        )
    return None


def check_file(filepath: Path) -> tuple[list[str], list[str]]:
    """Check a test file for quality anti-patterns."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    if not content.strip():
        return [], []

    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError:
        return [], []

    failures: list[str] = []
    warnings: list[str] = []

    docstring_failure = check_module_docstring(filepath, tree)
    if docstring_failure:
        failures.append(docstring_failure)

    source_lines = content.splitlines()
    visitor = TestQualityVisitor(str(filepath), source_lines)
    visitor.visit(tree)
    failures.extend(visitor.failures)
    warnings.extend(visitor.warnings)

    return failures, warnings


def _collect_results(filepaths: list[str]) -> tuple[list[str], list[str]]:
    """Collect failures and warnings from a list of file path strings."""
    all_failures: list[str] = []
    all_warnings: list[str] = []
    for filepath_str in filepaths:
        filepath = Path(filepath_str)
        if not filepath.exists() or filepath.suffix != ".py":
            continue
        failures, warnings = check_file(filepath)
        all_failures.extend(failures)
        all_warnings.extend(warnings)
    return all_failures, all_warnings


def _print_warnings(all_warnings: list[str]) -> None:
    """Print collected warnings to stderr."""
    print("\nTest Quality Warnings:\n", file=sys.stderr)
    for warning in all_warnings:
        print(f"  WARNING: {warning}", file=sys.stderr)
    print(file=sys.stderr)


def _print_failures(all_failures: list[str]) -> None:
    """Print collected failures to stderr."""
    print("\nTest Quality Failures:\n", file=sys.stderr)
    for failure in all_failures:
        print(f"  FAIL: {failure}", file=sys.stderr)
    print(
        "\nTests should verify behavior, not just mock "
        "interactions or coverage targets.\n",
        file=sys.stderr,
    )


def main() -> int:
    """Run test quality checks on provided files."""
    if len(sys.argv) < MIN_ARGS:
        print("Usage: check_test_quality.py <file1.py> [file2.py ...]")
        return 0

    all_failures, all_warnings = _collect_results(sys.argv[1:])

    if all_warnings:
        _print_warnings(all_warnings)
    if all_failures:
        _print_failures(all_failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
