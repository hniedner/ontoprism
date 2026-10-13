"""Read-only release deltas from publisher history and ROBOT axiom reports."""

import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator

from ontolib.common.boundary_models import StrictFrozenBoundaryModel
from ontolib.core.robot import RobotConfig, run_robot
from ontolib.terminologies.namespaces import NCIT_NS

_HISTORY_COLUMNS = 6
_TOKEN = re.compile(r'<[^<>\s]+>|"(?:\\.|[^"\\])*"|[()]|[^\s()"<>]+')
PositiveInteger = Annotated[int, Field(gt=0)]
NonnegativeInteger = Annotated[int, Field(ge=0)]
ReportSide = Literal["left", "right"]


def _append_lexeme(stack: list[list], lexeme: str) -> None:
    if lexeme == "(":
        child: list = []
        stack[-1].append(child)
        stack.append(child)
    elif lexeme == ")":
        if len(stack) == 1:
            raise ValueError("unbalanced ROBOT axiom")
        stack.pop()
    else:
        stack[-1].append(lexeme)


def parse_expression(source: str) -> tuple[str, list]:
    """Read balanced functional-syntax structure without interpreting OWL semantics."""
    tokens = list(_TOKEN.finditer(source))
    stack: list[list] = [[]]
    end = 0
    for match in tokens:
        if source[end : match.start()].strip():
            raise ValueError("malformed ROBOT axiom token")
        end = match.end()
        _append_lexeme(stack, match[0])
    if len(stack) != 1 or source[end:].strip():
        raise ValueError("unbalanced ROBOT axiom")
    match stack[0]:
        case [str(kind), list(args)] as root:
            _check_structure(root)
            return kind, args
        case _:
            raise ValueError("malformed ROBOT axiom expression")


def _check_structure(parts: list) -> None:
    for index, part in enumerate(parts):
        if isinstance(part, list):
            previous = parts[index - 1] if index else None
            _check_constructor(previous, part)
            _check_structure(part)
        elif re.fullmatch(r"[A-Za-z]+", part):
            if index + 1 == len(parts) or not isinstance(parts[index + 1], list):
                raise ValueError("malformed ROBOT axiom arguments")


def _check_constructor(name: object, arguments: list) -> None:
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z]+", name)
        or not arguments
    ):
        raise ValueError("malformed ROBOT axiom constructor")


def named_classes(values: list) -> list[str]:
    return [
        value[len(NCIT_NS) + 1 : -1]
        for value in values
        if isinstance(value, str)
        and re.fullmatch(r"<" + re.escape(NCIT_NS) + r"C[0-9]+>", value)
    ]


def _logical_subjects(source: str) -> list[str]:
    kind, args = parse_expression(source)
    if kind not in {"SubClassOf", "EquivalentClasses"}:
        return []
    while args and args[0] == "Annotation":
        args = args[2:]
    return named_classes(args[:1] if kind == "SubClassOf" else args)


class AxiomDelta(StrictFrozenBoundaryModel):
    removed: tuple[str, ...]
    added: tuple[str, ...]


def _report_line(
    line: str, side: ReportSide | None, counts: dict, rows: dict
) -> ReportSide:
    header = re.fullmatch(
        r"(\d+) axioms in (left|right) ontology "
        r"but not in (right|left) ontology:",
        line,
    )
    if header:
        side = cast("ReportSide", header[2])  # The regex admits only these two values.
        if side in counts or side == header[3]:
            raise ValueError("duplicate ROBOT section")
        counts[side] = int(header[1])
    elif side and line.startswith("- " if side == "left" else "+ "):
        parse_expression(line[2:])
        rows[side].append(line[2:])
    else:
        raise ValueError("unrecognized ROBOT diff output")
    return side


def parse_robot_report(report: str) -> AxiomDelta:
    """Keep full functional-syntax expressions, checking both declared row counts."""
    if report.strip() == "Ontologies are identical":
        return AxiomDelta(removed=(), added=())
    rows: dict[str, list[str]] = {"left": [], "right": []}
    counts: dict[str, int] = {}
    side: ReportSide | None = None
    for line in filter(str.strip, report.splitlines()):
        side = _report_line(line, side, counts, rows)
    if counts.keys() != rows.keys() or any(counts[s] != len(rows[s]) for s in rows):
        raise ValueError("ROBOT diff count differs from report rows")
    return AxiomDelta(removed=tuple(rows["left"]), added=tuple(rows["right"]))


def robot_diff(left: Path, right: Path, config: RobotConfig) -> AxiomDelta:
    result = run_robot(
        [
            "diff",
            "--left",
            str(left),
            "--right",
            str(right),
            "--format",
            "plain",
        ],
        config,
    )
    if result.returncode:
        raise RuntimeError(f"ROBOT diff exited {result.returncode}: {result.stderr}")
    return parse_robot_report(result.stdout)


class HistoryEvent(StrictFrozenBoundaryModel):
    """One publisher event, including survivor self-references."""

    concept: str = Field(pattern=r"^C[0-9]+$")
    action: Literal["create", "modify", "merge", "split", "retire"]
    date: date
    reference: str | None = Field(default=None, pattern=r"^C[0-9]+$")

    @model_validator(mode="after")
    def _successor(self) -> Self:
        if self.action in {"merge", "split"} and self.reference is None:
            raise ValueError("merge/split history requires a reference code")
        return self


def parse_history(source: str) -> tuple[HistoryEvent, ...]:
    """Require the observed six-column NCI export, without discarding extra data."""
    events = []
    for number, line in enumerate(source.splitlines(), 1):
        fields = line.split("|")
        if len(fields) != _HISTORY_COLUMNS or fields[1] or fields[5]:
            raise ValueError(
                f"history line {number}: expected six columns with empty 2/6"
            )
        concept, _, action, occurred, reference, _ = fields
        events.append(
            HistoryEvent.model_validate(
                {
                    "concept": concept,
                    "action": action,
                    "date": datetime.strptime(occurred, "%d-%b-%y").date(),
                    "reference": None if reference == "null" else reference,
                }
            )
        )
    if not events:
        raise ValueError("history source is empty")
    return tuple(events)


def _cohort(
    before: set[str], after: set[str], events: tuple[HistoryEvent, ...]
) -> dict:
    scoped = tuple(e for e in events if e.concept in before | after)
    return {
        "scope_before": len(before),
        "scope_after": len(after),
        "entered": sorted(after - before),
        "exited": sorted(before - after),
        "retired": sorted({e.concept for e in scoped if e.action == "retire"} & before),
        **_successors(scoped),
    }


def _successors(events: tuple[HistoryEvent, ...]) -> dict:
    result: dict[str, dict[str, set[str]]] = {
        "merge": defaultdict(set),
        "split": defaultdict(set),
    }
    for event in events:
        if event.action not in result or event.reference is None:
            continue
        if event.action == "merge" and event.concept != event.reference:
            result["merge"][event.concept].add(event.reference)
        if event.action == "split":
            result["split"][event.concept].add(event.reference)
    return _sorted_successors(result)


def _sorted_successors(result: dict) -> dict:
    return {
        name: {c: sorted(v) for c, v in sorted(result[action].items())}
        for action, name in (("merge", "merged_into"), ("split", "split_into"))
    }


def summarize_delta(
    before_release: str,
    after_release: str,
    before_codes: set[str],
    after_codes: set[str],
    before_scope: set[str],
    after_scope: set[str],
    axioms: AxiomDelta,
    events: tuple[HistoryEvent, ...],
) -> dict:
    """Join lifecycle and membership; preserve whole nested axioms, not flat triples."""
    logical: dict[str, dict[str, list[str]]] = {}
    for side, expressions in (("removed", axioms.removed), ("added", axioms.added)):
        for expression in expressions:
            for subject in _logical_subjects(expression):
                logical.setdefault(subject, {"removed": [], "added": []})[side].append(
                    expression
                )
    whole = _cohort(before_codes, after_codes, events)
    whole.update(
        added=sorted(after_codes - before_codes),
        removed=sorted(before_codes - after_codes),
        changed_definitions=sorted(logical.keys() & before_codes & after_codes),
    )
    branch = _cohort(before_scope, after_scope, events)
    branch["added"] = sorted((after_codes - before_codes) & after_scope)
    branch["changed_definitions"] = sorted(
        logical.keys() & before_codes & after_codes & (before_scope | after_scope)
    )
    return {
        "from": before_release,
        "to": after_release,
        "whole_release": whole,
        "neoplasm": branch,
        "axiom_counts": {"removed": len(axioms.removed), "added": len(axioms.added)},
        "axioms": axioms.model_dump(mode="json"),
        "logical_changes": dict(sorted(logical.items())),
        "history_event_counts": dict(Counter(e.action for e in events)),
        "history": [e.model_dump(mode="json") for e in events],
    }
