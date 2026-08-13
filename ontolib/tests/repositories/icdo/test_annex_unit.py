from pathlib import Path

import pytest
from openpyxl import Workbook

from ontolib.repositories.icdo.annex import reconcile_morphology_annex
from ontolib.repositories.icdo.models import CanonicalDataset, IcdoRecord, SourceShape

pytestmark = pytest.mark.unit


def _dataset(edition: str, codes: tuple[str, ...]) -> CanonicalDataset:
    return CanonicalDataset(
        edition=edition,
        axis="morphology",
        records=tuple(
            IcdoRecord(
                code=code,
                level="morphology",
                base_morphology=code[:4],
                specificity=code[4] if edition == "4.0" else None,
                behaviour=code[-1],
                preferred=code,
            )
            for code in codes
        ),
        source_shape=SourceShape(
            sheet_names=(), headers=(), merged_ranges=(), trailing_blank_rows=0
        ),
        source_sha256=("a" if edition == "3.2" else "b") * 64,
    )


def test_wildcard_change_and_deletion_reconcile_to_correct_editions(
    tmp_path: Path,
) -> None:
    workbook = Workbook()
    change = workbook.active
    change.title = "Morphology code changes"
    change.append(["title"])
    change.append(["ICDO4", "Level", "Term", "ICDO3.2"])
    change.append(["80410/3", "Preferred", "Small cell", "8041/3"])
    deleted = workbook.create_sheet("Deleted morphology codes")
    deleted.append(["title"])
    deleted.append(["ICDO3.2", "Reason"])
    deleted.append(["8041/3", "moved"])
    path = tmp_path / "annex.xlsx"
    workbook.save(path)

    report = reconcile_morphology_annex(
        path,
        old=_dataset("3.2", ("8041/3",)),
        new=_dataset("4.0", ("80410/3",)),
    )
    assert report.checked_rows == 2
    assert report.unresolved == ()
