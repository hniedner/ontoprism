from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from scripts.research import specialist_cadsr_usage
from scripts.research.specialist_cadsr_usage import generate_specialist_cadsr_usage

pytestmark = pytest.mark.unit

if TYPE_CHECKING:
    from pathlib import Path


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE cdes (
            public_id TEXT,
            version TEXT,
            short_name TEXT,
            long_name TEXT,
            context TEXT,
            datatype TEXT
        );
        CREATE TABLE cde_concepts (public_id TEXT, version TEXT, concept_code TEXT);
        INSERT INTO cdes VALUES
            ('1', '1.0', 'First', 'First', NULL, NULL),
            ('2', '1.0', 'Second', 'Second', NULL, NULL),
            ('3', '1.0', 'Third', 'Third', NULL, NULL);
        INSERT INTO cde_concepts VALUES
            ('1', '1.0', 'C27262'), ('2', '1.0', 'C27262'), ('3', '1.0', 'C27262');
        """
    )
    connection.commit()
    connection.close()


def test_cadsr_usage_distinguishes_found_empty_error_and_truncation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cadsr.db"
    _database(database)
    report = generate_specialist_cadsr_usage(
        database_path=database,
        output_path=tmp_path / "usage.json",
        root_codes=("C27262", "C102870"),
        limit=2,
        producing_command="fixed command",
    )
    found, empty = report.rows
    assert found.status == "usage-found"
    assert found.cde_ids == ("1:1.0", "2:1.0")
    assert tuple(item.public_id for item in found.cdes) == ("1", "2")
    assert found.cdes[0].long_name == "First"
    assert found.truncated is True
    assert empty.status == "no-linked-cde"
    assert empty.cde_ids == ()
    assert report.source_identity
    assert report.database_path == "data/cadsr/cde_repository.db"
    assert report.database_sha256
    assert report.query_identity
    assert report.report_identity
    assert "does not determine" in report.interpretation

    database.write_bytes(b"not sqlite")
    failed = generate_specialist_cadsr_usage(
        database_path=database,
        output_path=tmp_path / "failed.json",
        root_codes=("C27262",),
        limit=2,
        producing_command="fixed command",
    )
    assert failed.rows[0].status == "error"
    assert failed.rows[0].error


def test_cadsr_usage_limit_must_be_positive(tmp_path: Path) -> None:
    database = tmp_path / "cadsr.db"
    _database(database)
    with pytest.raises(ValueError, match="positive"):
        generate_specialist_cadsr_usage(
            database_path=database,
            output_path=tmp_path / "usage.json",
            root_codes=("C27262",),
            limit=0,
            producing_command="fixed command",
        )


def test_cadsr_usage_reads_through_repository_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "cadsr.db"
    database.write_bytes(b"repository-owned")
    calls: list[tuple[str, int]] = []

    @dataclass(frozen=True)
    class Source:
        archive_sha256: str

    class Repository:
        def __init__(self, path: Path) -> None:
            assert path == database

        def source_provenance(self) -> Source:
            return Source("abc")

        def find_cdes_by_concept(self, code: str, *, limit: int) -> list[object]:
            calls.append((code, limit))
            return [
                specialist_cadsr_usage.CdeSummary(
                    public_id="3",
                    version="1.0",
                    short_name="Three",
                    long_name="Third",
                ),
                specialist_cadsr_usage.CdeSummary(
                    public_id="4",
                    version="2.0",
                    short_name="Four",
                    long_name="Fourth",
                ),
            ]

    monkeypatch.setattr(specialist_cadsr_usage, "CdeRepository", Repository)
    report = generate_specialist_cadsr_usage(
        database_path=database,
        output_path=tmp_path / "usage.json",
        root_codes=("C27262",),
        limit=1,
        producing_command="fixed command",
    )

    assert calls == [("C27262", 2)]
    assert report.rows[0].cde_ids == ("3:1.0",)
    assert report.rows[0].truncated is True
