import re
from pathlib import Path

import pytest


@pytest.mark.unit
def test_decision_ids_are_unique_and_historical_d70_is_unchanged() -> None:
    decisions = Path("docs/DECISIONS.md").read_text()
    ids = re.findall(r"^### (D[0-9]+)\.", decisions, flags=re.MULTILINE)

    assert len(ids) == len(set(ids))
    historical_d70 = (
        "### D70. Every PDM task loads repository-local Jena and ROBOT defaults"
    )
    assert historical_d70 in decisions
