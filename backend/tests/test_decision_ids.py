import re
from pathlib import Path

import pytest


@pytest.mark.unit
def test_decision_ids_are_unique() -> None:
    decisions = Path("docs/DECISIONS.md").read_text()
    ids = re.findall(r"^### (D[0-9]+)\.", decisions, flags=re.MULTILINE)

    assert len(ids) == len(set(ids))
