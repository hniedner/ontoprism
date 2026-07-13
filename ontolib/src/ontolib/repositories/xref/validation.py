"""Non-circular validation harness: EL profile gate + ELK classification.

A4.2 / #73 — profile check via ROBOT before ELK classify, then candidate promotion.

**The reasoner boundary is three-valued, not two.** A merge can be *accepted*,
*rejected by the reasoner* (non-EL, or unsatisfiable), or **the reasoner may never have
run at all** (no Java, corrupt jar, OOM, unwritable temp dir, a renamed ROBOT
subcommand).  Collapsing the third state into the second is the defect that hid three
separate bugs in this module's history: a failed reasoner call looks exactly like "no
candidate qualified for promotion", so the run reports a clean zero and the operator
concludes the corpus is bad.  Environment failure therefore raises
:exc:`ReasonerUnavailableError`; only a genuine verdict is returned.
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

from ontolib.repositories.xref.evidence import is_independent
from ontolib.repositories.xref.vocab import EXACT_MATCH

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.repositories.xref.evidence import Evidence
    from ontolib.repositories.xref.models import SSSOMRecord

logger = logging.getLogger(__name__)

_ROBOT = shutil.which("robot") or "robot"

# ROBOT can wedge on a pathological merge; a hang is an environment failure, not a
# verdict, and this runs once per candidate.
ROBOT_TIMEOUT_S = int(os.environ.get("ROBOT_TIMEOUT_S", "300"))

# Substrings that mark a genuine *verdict* in ROBOT's output.  Anything else on a
# non-zero exit is an environment failure and must be raised, never silently read as
# "the ontology is bad".
_PROFILE_VIOLATION_MARKERS = ("violation", "not in profile")
_UNSATISFIABLE_MARKERS = ("unsatisfiable", "inconsistent", "incoherent")


class ReasonerUnavailableError(RuntimeError):
    """ROBOT/ELK could not be run, or failed for a reason unrelated to the ontology.

    This is emphatically **not** a verdict about the merge.  It is raised so that an
    infrastructure failure can never be recorded as "this candidate did not qualify".
    """


def _robot_cmd() -> str:
    """Return the resolved ``robot`` executable path."""
    return _ROBOT


def _marks(output: str, markers: tuple[str, ...]) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in markers)


def _refuse_if_killed(result: subprocess.CompletedProcess[str], what: str) -> None:
    """A process killed by a signal never rendered a judgment about anything.

    Checked *before* the marker match, because a dying JVM's partial output can contain
    the marker words by accident — `VerifyError: Inconsistent stackmap frames` carries
    "inconsistent", and a crash dump carries "violation". Reading those as a verdict is
    exactly the laundering this module forbids.
    """
    if result.returncode < 0:
        raise ReasonerUnavailableError(
            f"ROBOT ({what}) was killed by signal {-result.returncode} (OOM-killer? "
            "JVM crash?). Its partial output is not a verdict, whatever words it "
            f"happens to contain.\nstderr: {result.stderr.strip()[:1000]}"
        )


def _run_robot(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke ROBOT, converting any *inability to run it* into an explicit error."""
    try:
        return subprocess.run(  # noqa: S603 — trusted input
            [_robot_cmd(), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=ROBOT_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise ReasonerUnavailableError(
            f"`{_robot_cmd()}` is not on PATH — the EL/ELK gate cannot run. "
            "See docs/DATA_SETUP.md (ROBOT + Java 21)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReasonerUnavailableError(
            f"ROBOT timed out after {ROBOT_TIMEOUT_S}s: {' '.join(args)}. "
            "A hang is an environment failure, not a verdict."
        ) from exc


def to_el_profile_and_check(ontology_path: str) -> bool:
    """Is *ontology_path* in the OWL 2 EL profile?  (ROBOT ``validate-profile``.)

    The command is ``validate-profile``; ``profile`` is not a ROBOT subcommand and
    fails for every input, which would silently reject every merge.

    Returns ``True``/``False`` for a genuine verdict.  Raises
    :exc:`ReasonerUnavailableError` if ROBOT could not run or failed for any reason
    that is not a reported profile violation — the diagnostics are surfaced, never
    swallowed.
    """
    result = _run_robot(
        ["validate-profile", "--profile", "EL", "--input", ontology_path]
    )
    if result.returncode == 0:
        return True

    _refuse_if_killed(result, "validate-profile")
    if not _marks(result.stdout + result.stderr, _PROFILE_VIOLATION_MARKERS):
        raise ReasonerUnavailableError(
            f"`robot validate-profile` exited {result.returncode} without reporting a "
            "profile violation — this is NOT an EL verdict, it is an environment "
            f"failure.\nstdout: {result.stdout.strip()[:1000]}\n"
            f"stderr: {result.stderr.strip()[:1000]}"
        )

    logger.warning(
        "EL profile violation in %s:\n%s",
        ontology_path,
        (result.stdout or result.stderr).strip()[:1000],
    )
    return False


def classify(ontology_path: str | Path) -> Path | None:
    """Classify *ontology_path* with ELK via ROBOT.

    Returns the path to the inferred ontology, or ``None`` if the reasoner **refuted**
    the merge (unsatisfiable/incoherent — the satisfiability gate).  Raises
    :exc:`ReasonerUnavailableError` for any other failure: a rejected merge and a
    broken reasoner must never be reported as the same thing.
    """
    fd, path = tempfile.mkstemp(suffix=".owl")
    os.close(fd)
    out_path = Path(path)

    try:
        result = _run_robot(
            [
                "reason",
                "--reasoner",
                "ELK",
                # Keep the asserted axioms in the output.  ROBOT otherwise deletes an
                # asserted `A ⊑ B` whose inference makes it redundant — and when the
                # merge *entails* `A ≡ B`, its default axiom-generators ("subclass")
                # never write the equivalence back, so A and B end up connected by
                # nothing at all.  Our "did ROBOT actually classify this?" check would
                # then false-alarm on a perfectly sound run (a cross-plane cycle needs
                # only one anchor and one bad candidate), fail the whole run, skip the
                # D29 sweep, and tell the operator to check their Java install.
                "--remove-redundant-subclass-axioms",
                "false",
                "--input",
                str(ontology_path),
                "--output",
                str(out_path),
            ]
        )
    except ReasonerUnavailableError:
        # do not leak one temp file per failing candidate
        out_path.unlink(missing_ok=True)
        raise

    if result.returncode == 0:
        return out_path

    out_path.unlink(missing_ok=True)
    _refuse_if_killed(result, "reason")
    if _marks(result.stdout + result.stderr, _UNSATISFIABLE_MARKERS):
        logger.warning(
            "ELK refuted the merge %s (unsatisfiable):\n%s",
            ontology_path,
            (result.stderr or result.stdout).strip()[:1000],
        )
        return None

    raise ReasonerUnavailableError(
        f"`robot reason` exited {result.returncode} without reporting unsatisfiable "
        "classes — this is NOT a verdict about the merge (OOM, JVM crash, unwritable "
        f"output?).\nstdout: {result.stdout.strip()[:1000]}\n"
        f"stderr: {result.stderr.strip()[:1000]}"
    )


def validate_and_classify(ontology_path: str) -> Path | None:
    """Profile gate -> classify.

    Returns the classified output path, or ``None`` iff the reasoner **refuted** the
    merge (it escapes EL, or it is unsatisfiable) — in which case :func:`classify` is
    never called for a non-EL input.  An unusable reasoner raises rather than
    returning ``None`` (see :exc:`ReasonerUnavailableError`).
    """
    if not to_el_profile_and_check(ontology_path):
        return None
    return classify(ontology_path)


def promote_candidate(
    record: SSSOMRecord,
    evidence: Sequence[Evidence],
    *,
    el_valid: bool,
) -> SSSOMRecord | None:
    """Return a promoted copy of *record* iff the evidence is independent and EL-valid.

    Both gates are hard (D28).  *evidence* must satisfy
    :func:`ontolib.repositories.xref.evidence.is_independent` — a SKOS annotation can
    never be evidence for the bridge it annotates, which :class:`Evidence` enforces at
    construction.  *el_valid* reflects whether the merged fragment carrying the curated
    ``owl:equivalentClass`` bridge passed the EL profile + satisfiability gate and
    classified.  Anything else stays a proposed ``closeMatch``.
    """
    if not el_valid:
        return None
    if not is_independent(evidence):
        return None
    return replace(record, predicate_id=EXACT_MATCH, lifecycle_state="validated")
