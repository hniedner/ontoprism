"""Import smoke for native and application dependencies in the Python 3.14 lane."""

from __future__ import annotations

import importlib
import pathlib
import sys

from scripts.validation.python_versions import PYTHON_COMPATIBILITY_VERSION
from scripts.validation.python_warnings import configure_compatibility_warnings


def main() -> None:
    """Fail unless the compatibility interpreter and required imports are usable."""
    configure_compatibility_warnings()
    version = PYTHON_COMPATIBILITY_VERSION
    expected = tuple(int(value) for value in version.split("."))
    if sys.version_info[:2] != expected:
        actual = sys.version.split()[0]
        raise RuntimeError(f"expected Python {version}, got {actual}")
    module_names = (
        "alembic",
        "asyncpg",
        "fastapi",
        "greenlet",
        "openpyxl",
        "pydantic",
        "pydantic_core",
        "rdflib",
        "sqlalchemy",
        "uvicorn",
        "uvloop",
        "watchfiles",
        "websockets",
        "xlrd",
    )
    imported = tuple(importlib.import_module(name) for name in module_names)
    config_type = importlib.import_module("alembic.config").Config
    app = importlib.import_module("backend.main").app
    config = config_type(pathlib.Path(__file__).resolve().parents[2] / "alembic.ini")
    if config.get_main_option("script_location") != "migrations":
        raise RuntimeError("Alembic script metadata is unavailable")
    if app.title != "ontoprism":
        raise RuntimeError("FastAPI application import returned the wrong app")
    print(
        f"Python {sys.version.split()[0]} compatibility imports: "
        + ", ".join(module.__name__ for module in imported)
    )


if __name__ == "__main__":
    main()
