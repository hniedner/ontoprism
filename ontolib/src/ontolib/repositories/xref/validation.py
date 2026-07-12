"""Non-circular validation harness: EL profile gate + ELK classification.

A4.2 — profile check via ROBOT before ELK classify, then candidate promotion.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from ontolib.repositories.xref.vocab import EXACT_MATCH

if TYPE_CHECKING:
    from ontolib.repositories.xref.models import SSSOMRecord

logger = logging.getLogger(__name__)

_ROBOT = shutil.which("robot") or "robot"


def _robot_cmd() -> str:
    """Return the resolved ``robot`` executable path."""
    return _ROBOT


def to_el_profile_and_check(ontology_path: str) -> bool:
    """Check whether *ontology_path* is in the OWL 2 EL profile via ROBOT.

    Returns ``True`` if the ontology passes the profile gate, ``False``
    otherwise (also logs a warning).
    """
    result = subprocess.run(  # noqa: S603 — trusted input
        [_robot_cmd(), "profile", "--input", ontology_path, "--profile", "EL"],
        capture_output=True,
        text=True,
        check=False,
    )
    ok = result.returncode == 0
    if not ok:
        logger.warning("Ontology %s is not in OWL 2 EL profile", ontology_path)
    return ok


def classify(ontology_path: str | Path) -> Path:
    """Classify *ontology_path* with ELK reasoner via ROBOT.

    Returns the path to the inferred ontology (a temporary file).
    Raises :exc:`subprocess.CalledProcessError` if ROBOT fails.
    """
    fd, path = tempfile.mkstemp(suffix=".owl")
    os.close(fd)
    out_path = Path(path)
    subprocess.run(  # noqa: S603 — trusted input
        [
            _robot_cmd(),
            "reason",
            "--reasoner",
            "ELK",
            "--input",
            str(ontology_path),
            "--output",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out_path


def validate_and_classify(ontology_path: str) -> Path | None:
    """Profile gate → classify pipeline.

    Returns the classified output path iff the ontology passes the EL profile
    gate, otherwise ``None`` (and :func:`classify` is **never** called).
    """
    if not to_el_profile_and_check(ontology_path):
        return None
    return classify(ontology_path)


def promote_candidate(
    record: SSSOMRecord,
    evidence: list[str],
    el_valid: bool = False,
) -> SSSOMRecord | None:
    """Return a promoted copy of *record* iff evidence is independent and EL-valid.

    A SKOS annotation may **never** serve as its own equivalence evidence — if
    the record's own ``predicate_id`` appears in *evidence* the candidate is
    rejected.  The *el_valid* flag reflects whether a prior EL profile gate +
    ELK classification confirmed the pair as equivalent.
    """
    if record.predicate_id in evidence:
        return None
    if not el_valid:
        return None
    return replace(record, predicate_id=EXACT_MATCH, lifecycle_state="validated")
