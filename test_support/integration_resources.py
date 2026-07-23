"""Exact ownership identities for disposable integration-test resources."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy.engine import make_url

if TYPE_CHECKING:
    from pathlib import Path

_NONCE = re.compile(r"[0-9a-f]{32}")
_GRAPH_COMPONENT = re.compile(r"[a-z][a-z0-9-]{0,47}")
_PERSISTENT_SQL = re.compile(
    r"\b(?:ALTER\s+TABLE|CREATE\s+(?:DATABASE|EXTENSION|SCHEMA|TABLE)|"
    r"DELETE\s+FROM|DROP\s+(?:DATABASE|SCHEMA|TABLE)|INSERT\s+INTO|TRUNCATE|"
    r"UPDATE\s+[A-Za-z_])",
    re.IGNORECASE,
)
_REPOSITORY_WRITES: Final = frozenset(
    {
        "persist_promotions",
        "populate",
        "quarantine_stale",
        "rebuild",
        "upsert_constituents",
        "upsert_minted_concept",
        "upsert_records",
        "upsert_run",
    }
)
_OXIGRAPH_IMAGE = (
    "ghcr.io/oxigraph/oxigraph@sha256:"
    "cc943499d4724fbb348c75c623335c69a047de71c59852413b0d0467d3caebe3"
)


class ResourceOwnershipError(RuntimeError):
    """A persistent resource is not owned by the current test run."""


@dataclass(frozen=True, slots=True)
class IntegrationResourceOwner:
    """Collision-resistant identity shared by one integration-test run."""

    nonce: str

    def __post_init__(self) -> None:
        if _NONCE.fullmatch(self.nonce) is None:
            raise ValueError("test resource nonce must be 32 lowercase hex characters")

    @property
    def database_name(self) -> str:
        """Return the exact Postgres database owned by this run."""
        return f"ontoprism_test_{self.nonce}"

    @property
    def oxigraph_container_name(self) -> str:
        """Return the exact disposable Oxigraph container name."""
        return f"ontoprism-oxigraph-test-{self.nonce}"

    def graph_iri(self, component: str) -> str:
        """Return a run-owned graph IRI for a validated logical component."""
        if _GRAPH_COMPONENT.fullmatch(component) is None:
            raise ValueError("graph component must be lowercase alphanumeric/hyphen")
        return f"urn:ontoprism:test:{self.nonce}:{component}"

    def verify_database(self, database_name: str, marker: str) -> None:
        """Refuse a database unless both its exact name and marker match this run."""
        if database_name != self.database_name:
            raise ResourceOwnershipError(
                f"database name is not owned by this run: {database_name!r}"
            )
        if marker != self.nonce:
            raise ResourceOwnershipError(
                "database owner marker does not match this run"
            )

    def verify_oxigraph(
        self,
        *,
        label: str | None,
        mounted_data_dir: Path | None,
        expected_data_dir: Path,
        file_marker: str,
    ) -> None:
        """Refuse an Oxigraph target unless all independent owner signals match."""
        if label != self.nonce:
            raise ResourceOwnershipError("Oxigraph container owner label mismatch")
        if (
            mounted_data_dir is None
            or mounted_data_dir.resolve() != expected_data_dir.resolve()
        ):
            raise ResourceOwnershipError("Oxigraph container data mount mismatch")
        if file_marker != self.nonce:
            raise ResourceOwnershipError("Oxigraph data file marker mismatch")

    def postgres_admin_url(self, configured_url: str) -> str:
        """Derive an administrative connection without touching the configured DB."""
        return (
            make_url(configured_url)
            .set(database="postgres")
            .render_as_string(hide_password=False)
        )

    def database_url(self, configured_url: str) -> str:
        """Derive the exact current-run database URL from server credentials."""
        url = make_url(configured_url).set(database=self.database_name)
        return url.render_as_string(
            hide_password=False,
        )

    def oxigraph_run_command(self, data_dir: Path) -> list[str]:
        """Build the pinned, loopback-only disposable Oxigraph command."""
        if not data_dir.is_absolute() or self.nonce not in data_dir.name:
            raise ResourceOwnershipError(
                "Oxigraph data directory is not an absolute current-run-owned path"
            )
        return [
            "docker",
            "run",
            "--detach",
            "--name",
            self.oxigraph_container_name,
            "--label",
            f"org.ontoprism.test-owner={self.nonce}",
            "--publish",
            "127.0.0.1::7878",
            "--volume",
            f"{data_dir}:/data",
            _OXIGRAPH_IMAGE,
            "serve",
            "--location",
            "/data",
            "--bind",
            "0.0.0.0:7878",
        ]


def _is_integration_decorator(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Attribute)
        and decorator.attr == "integration"
        and isinstance(decorator.value, ast.Attribute)
        and decorator.value.attr == "mark"
        and isinstance(decorator.value.value, ast.Name)
        and decorator.value.value.id == "pytest"
    )


def _integration_scopes(tree: ast.Module) -> list[ast.AST]:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in node.targets
            )
            and "pytest.mark.integration" in ast.unparse(node.value)
        ):
            return [tree]

    return [
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and any(_is_integration_decorator(item) for item in node.decorator_list)
    ]


def _call_mutation_reasons(node: ast.Call) -> set[str]:
    reasons: set[str] = set()
    function_name: str | None = None
    if isinstance(node.func, ast.Attribute):
        function_name = node.func.attr
    elif isinstance(node.func, ast.Name):
        function_name = node.func.id

    if function_name == "load":
        reasons.add("Oxigraph write")
    if function_name in {"downgrade", "upgrade", "_alembic"}:
        reasons.add("schema migration")
    if function_name in _REPOSITORY_WRITES:
        reasons.add("repository write")
    if function_name in {"delete", "patch", "put"}:
        reasons.add("HTTP write")
    if function_name == "post" and any(
        isinstance(argument, ast.Constant)
        and isinstance(argument.value, str)
        and (
            "/refresh/ncit/reload" in argument.value
            or "/refresh/ncit/search-index" in argument.value
        )
        for argument in node.args
    ):
        reasons.add("persistent API write")
    return reasons


def _mutation_reasons(scope: ast.AST) -> tuple[str, ...]:
    reasons: set[str] = set()
    for node in ast.walk(scope):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _PERSISTENT_SQL.search(node.value)
        ):
            reasons.add("persistent SQL write")
        if isinstance(node, ast.Call):
            reasons.update(_call_mutation_reasons(node))
    return tuple(sorted(reasons))


def find_unmanifested_mutators(
    root: Path, *, manifested_paths: frozenset[str]
) -> dict[str, tuple[str, ...]]:
    """Find integration tests with persistent-write signals missing from the manifest.

    This deliberately scans only test functions declared as integration tests (or all
    tests in a module with a module-level integration marker), avoiding temporary
    SQLite setup used by hermetic unit tests in otherwise mixed files.
    """
    result: dict[str, tuple[str, ...]] = {}
    for test_root in (root / "backend/tests", root / "ontolib/tests"):
        if not test_root.exists():
            continue
        for path in test_root.rglob("test_*.py"):
            relative = path.relative_to(root).as_posix()
            if relative in manifested_paths:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            reasons = {
                reason
                for scope in _integration_scopes(tree)
                for reason in _mutation_reasons(scope)
            }
            if reasons:
                result[relative] = tuple(sorted(reasons))
    return result
