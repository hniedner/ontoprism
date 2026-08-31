"""Generate bounded, read-only caDSR usage evidence for specialist rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ontolib.repositories.cadsr.repository import CdeRepository


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CadsrUsageRow(_StrictModel):
    code: str
    status: Literal["usage-found", "no-linked-cde", "error"]
    cde_ids: tuple[str, ...]
    truncated: bool
    error: str | None


class SpecialistCadsrUsageReport(_StrictModel):
    schema_version: Literal[1]
    database_path: str
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    producing_command: str
    query_limit: int = Field(ge=1)
    rows: tuple[CadsrUsageRow, ...]
    interpretation: Literal[
        "caDSR usage does not determine clinical or ontology correctness."
    ]


def _canonical(model: BaseModel) -> bytes:
    return (
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True).encode()
        + b"\n"
    )


def generate_specialist_cadsr_usage(
    *,
    database_path: Path,
    output_path: Path,
    root_codes: tuple[str, ...],
    limit: int,
    producing_command: str,
) -> SpecialistCadsrUsageReport:
    """Use a parameterized limit+1 query and preserve empty/error distinctions."""
    if limit <= 0:
        raise ValueError("caDSR usage limit must be positive")
    database_bytes = database_path.read_bytes()
    repository = CdeRepository(database_path)
    rows: list[CadsrUsageRow] = []
    try:
        source = repository.source_provenance()
        provenance = json.dumps(asdict(source), sort_keys=True)
    except sqlite3.Error:
        provenance = "source-row-unavailable"
    for code in root_codes:
        try:
            found = repository.find_cde_ids_by_concept(code, limit=limit + 1)
        except sqlite3.Error as exc:
            rows.append(
                CadsrUsageRow(
                    code=code,
                    status="error",
                    cde_ids=(),
                    truncated=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        rows.append(
            CadsrUsageRow(
                code=code,
                status="usage-found" if found else "no-linked-cde",
                cde_ids=tuple(found[:limit]),
                truncated=len(found) > limit,
                error=None,
            )
        )
    if rows and all(row.status == "error" for row in rows):
        provenance = "database-error"
    identity_payload = database_bytes + b"\0" + provenance.encode()
    report = SpecialistCadsrUsageReport(
        schema_version=1,
        database_path=database_path.as_posix(),
        source_identity=hashlib.sha256(identity_payload).hexdigest(),
        producing_command=producing_command,
        query_limit=limit,
        rows=tuple(rows),
        interpretation=(
            "caDSR usage does not determine clinical or ontology correctness."
        ),
    )
    payload = _canonical(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not output_path.exists() or output_path.read_bytes() != payload:
        output_path.write_bytes(payload)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root-code", required=True, action="append")
    parser.add_argument("--limit", required=True, type=int)
    parser.add_argument("--producing-command", required=True)
    args = parser.parse_args()
    generate_specialist_cadsr_usage(
        database_path=args.database,
        output_path=args.output,
        root_codes=tuple(args.root_code),
        limit=args.limit,
        producing_command=args.producing_command,
    )


if __name__ == "__main__":
    main()
