"""Import smoke for native and application dependencies in the Python 3.14 lane."""

from __future__ import annotations

import sys
from pathlib import Path

import alembic
import asyncpg
import fastapi
import greenlet
import lxml
import openpyxl
import pydantic
import pydantic_core
import rdflib
import xlrd
from alembic.config import Config

from backend.main import app


def main() -> None:
    """Fail unless the compatibility interpreter and required imports are usable."""
    if sys.version_info[:2] != (3, 14):
        raise RuntimeError(f"expected Python 3.14, got {sys.version.split()[0]}")
    config = Config(Path(__file__).resolve().parents[2] / "alembic.ini")
    if config.get_main_option("script_location") != "migrations":
        raise RuntimeError("Alembic script metadata is unavailable")
    if app.title != "ontoprism":
        raise RuntimeError("FastAPI application import returned the wrong app")
    imported = (
        alembic,
        asyncpg,
        fastapi,
        greenlet,
        lxml,
        openpyxl,
        pydantic,
        pydantic_core,
        rdflib,
        xlrd,
    )
    print(
        f"Python {sys.version.split()[0]} compatibility imports: "
        + ", ".join(module.__name__ for module in imported)
    )


if __name__ == "__main__":
    main()
