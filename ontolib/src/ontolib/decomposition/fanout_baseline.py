"""Source-qualified observation of the production scope's scale boundary."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast

from ontolib.decomposition.branches import DecompositionBranch, branch_spec
from ontolib.decomposition.collapse_policy import NO_COLLAPSE_VETO_POLICY
from ontolib.decomposition.models import RestrictionDefinitionFact
from ontolib.decomposition.run import _decompose_one
from ontolib.decomposition.scope import (
    build_scope_edge_queries,
    build_scope_overflow_query,
    enumerate_scope_codes,
)
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDF_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
    from pathlib import Path

    from ontolib.decomposition.models import CompleteDefinition
    from ontolib.decomposition.run import DecompositionSparqlClient

BASELINE_SCHEMA_VERSION = 1
DISCOVERY_ALGORITHM = "production-complete-definition-corpus-closure-v1"


class FanoutClient(Protocol):
    async def select(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]: ...

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]: ...


@dataclass(frozen=True, slots=True)
class FanoutObservation:
    concept_codes: tuple[str, ...]
    restriction_fact_count: int
    restriction_occurrence_count: int
    scanned_concept_count: int


@dataclass(frozen=True, slots=True)
class FanoutRerun:
    concept_code: str
    restriction_fact_count: int
    restriction_occurrence_count: int
    logical_select_count: int
    select_once_r82_count: int


@dataclass(frozen=True, slots=True)
class FanoutBaseline:
    schema_version: int
    source_identity: str
    ontology_release: str
    branch: str
    scope_root: str
    scope_version: str
    concept_codes: tuple[str, ...]
    restriction_fact_count: int
    restriction_occurrence_count: int
    scanned_concept_count: int
    discovery_algorithm: str
    discovery_query_identity: str
    logical_select_count_budget: int
    select_once_r82_count_budget: int
    baseline_identity: str


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def baseline_identity(value: object) -> str:
    """Bind every exact observed baseline field except the identity itself."""
    if isinstance(value, FanoutBaseline):
        payload = asdict(value)
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        raise TypeError("fanout baseline identity requires a baseline or mapping")
    payload.pop("baseline_identity", None)
    return _canonical_digest(payload)


def discovery_query_identity() -> str:
    """Identify every query template used by the exhaustive observation."""
    return _canonical_digest(
        {
            "algorithm": DISCOVERY_ALGORITHM,
            "complete_definition_queries": build_fanout_discovery_queries(),
            "scope_edge_queries": build_scope_edge_queries(),
            "scope_overflow_query": build_scope_overflow_query(),
        }
    )


def build_fanout_discovery_queries() -> tuple[str, str]:
    """Return bounded global queries for roots and local expression members."""
    roots = f"""
PREFIX rdf: <{RDF_NS}>
PREFIX owl: <{OWL_NS}>
SELECT ?anchor ?rootExpression WHERE {{
  GRAPH <{STATED_GRAPH_IRI}> {{
    ?anchor owl:equivalentClass ?rootExpression .
    ?rootExpression owl:intersectionOf ?list .
    FILTER(STRSTARTS(STR(?anchor), "{NCIT_NS}"))
  }}
}}
"""
    members = f"""
PREFIX rdf: <{RDF_NS}>
PREFIX owl: <{OWL_NS}>
SELECT ?expression ?cell ?member ?role ?target ?definedExpression ?nestedExpression
WHERE {{
  GRAPH <{STATED_GRAPH_IRI}> {{
    ?expression owl:intersectionOf ?list .
    ?list rdf:rest* ?cell .
    ?cell rdf:first ?member .
    OPTIONAL {{
      ?member owl:onProperty ?role ; owl:someValuesFrom ?target .
    }}
    OPTIONAL {{
      FILTER(isIRI(?member))
      ?member owl:equivalentClass ?definedExpression .
    }}
    OPTIONAL {{
      FILTER(isBlank(?member))
      ?member owl:equivalentClass? ?nestedExpression .
      ?nestedExpression owl:intersectionOf ?nestedList .
    }}
  }}
}}
"""
    return roots, members


def _row_code(value: str | None, binding: str) -> str:
    if value is None or not value.startswith(NCIT_NS):
        raise ValueError(f"fanout discovery row has invalid {binding}")
    code = value.removeprefix(NCIT_NS)
    if not code.startswith("C") or not code[1:].isdigit():
        raise ValueError(f"fanout discovery row has invalid {binding}")
    return code


def _required_fixture_value(row: Mapping[str, str | None], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"fanout discovery row has invalid {key}")
    return value


def _add_fixture_discovery_row(
    row: Mapping[str, str | None],
    occurrences: dict[str, set[str]],
    defined_genera: dict[str, set[str]],
) -> None:
    raw_anchor = _required_fixture_value(row, "anchor")
    if row.get("kind") == "restriction":
        occurrences[raw_anchor].add(_required_fixture_value(row, "occurrence"))
        return
    if row.get("kind") == "definedGenus":
        defined_genera[raw_anchor].add(_required_fixture_value(row, "genus"))
        return
    raise ValueError("fanout discovery row has invalid kind")


def _record_store_restriction(
    row: Mapping[str, str | None],
    expression: str,
    occurrences: dict[str, set[str]],
) -> None:
    role, target = row.get("role"), row.get("target")
    if role is None and target is None:
        return
    if role is None or target is None:
        raise ValueError("fanout discovery restriction is incomplete")
    occurrence = row.get("cell")
    if occurrence is None:
        raise ValueError("fanout discovery occurrence has no identity")
    occurrences[expression].add(str(occurrence))


def _add_store_discovery_row(
    row: Mapping[str, str | None],
    roots: dict[str, set[str]],
    occurrences: dict[str, set[str]],
    defined_genera: dict[str, set[str]],
    nested_expressions: dict[str, set[str]],
) -> None:
    if row.get("rootExpression") is not None:
        anchor = _row_code(row.get("anchor"), "anchor")
        roots[anchor].add(str(row["rootExpression"]))
        return
    expression = row.get("expression")
    if expression is None:
        raise ValueError("fanout discovery member has no expression")
    _record_store_restriction(row, expression, occurrences)
    if row.get("definedExpression") is not None:
        defined_genera[expression].add(_row_code(row.get("member"), "genus"))
    if row.get("nestedExpression") is not None:
        nested_expressions[expression].add(str(row["nestedExpression"]))


def _discovery_graph(
    rows: Sequence[Mapping[str, str | None]],
) -> tuple[
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
]:
    roots: dict[str, set[str]] = defaultdict(set)
    occurrences: dict[str, set[str]] = defaultdict(set)
    defined_genera: dict[str, set[str]] = defaultdict(set)
    nested_expressions: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if "kind" in row:
            _add_fixture_discovery_row(row, occurrences, defined_genera)
        else:
            _add_store_discovery_row(
                row, roots, occurrences, defined_genera, nested_expressions
            )
    return roots, occurrences, defined_genera, nested_expressions


def _expression_closure(
    occurrences: Mapping[str, set[str]],
    nested_expressions: Mapping[str, set[str]],
) -> Callable[[str], frozenset[tuple[str, str]]]:
    memo: dict[str, frozenset[tuple[str, str]]] = {}
    visiting: set[str] = set()

    def resolve(expression: str) -> frozenset[tuple[str, str]]:
        if expression in memo:
            return memo[expression]
        if expression in visiting:
            raise ValueError("fanout discovery nested expressions contain a cycle")
        visiting.add(expression)
        result = {
            (expression, occurrence)
            for occurrence in occurrences.get(expression, set())
        }
        for child in nested_expressions.get(expression, set()):
            result.update(resolve(child))
        visiting.remove(expression)
        memo[expression] = frozenset(result)
        return memo[expression]

    return resolve


def highest_fanout_from_discovery_rows(
    scope_codes: Collection[str],
    rows: Sequence[Mapping[str, str | None]],
) -> FanoutObservation:
    """Compute complete inherited occurrence fanout from corpus-local observations."""
    roots, occurrences, defined_genera, nested_expressions = _discovery_graph(rows)

    memo: dict[str, frozenset[tuple[str, str]]] = {}
    visiting: set[str] = set()

    expression_occurrences = _expression_closure(occurrences, nested_expressions)

    def inherited(code: str) -> frozenset[tuple[str, str]]:
        if code in memo:
            return memo[code]
        if code in visiting:
            raise ValueError("fanout discovery defined-genus graph contains a cycle")
        visiting.add(code)
        if roots:
            result: set[tuple[str, str]] = set()
            for expression in roots.get(code, set()):
                result.update(expression_occurrences(expression))
                for genus in defined_genera.get(expression, set()):
                    result.update(inherited(genus))
        else:
            result = {(code, occurrence) for occurrence in occurrences.get(code, set())}
            for genus in defined_genera.get(code, set()):
                result.update(inherited(genus))
        visiting.remove(code)
        memo[code] = frozenset(result)
        return memo[code]

    ordered_codes = tuple(sorted(scope_codes))
    if not ordered_codes:
        raise ValueError("fanout observation requires a non-empty scope")
    counts = {code: len(inherited(code)) for code in ordered_codes}
    maximum = max(counts.values())
    return FanoutObservation(
        concept_codes=tuple(code for code in ordered_codes if counts[code] == maximum),
        restriction_fact_count=0,
        restriction_occurrence_count=maximum,
        scanned_concept_count=len(ordered_codes),
    )


def _restriction_counts(complete: CompleteDefinition) -> tuple[int, int]:
    return (
        sum(isinstance(fact, RestrictionDefinitionFact) for fact in complete.facts),
        len(complete.occurrences),
    )


async def observe_highest_fanout(
    concept_codes: Collection[str],
    *,
    read_definition: Callable[[str], Awaitable[CompleteDefinition]],
    progress: Callable[[int, int, str], None] | None = None,
) -> FanoutObservation:
    """Read every scoped concept and retain all maxima by occurrence then fact count."""
    ordered_codes = tuple(sorted(concept_codes))
    if not ordered_codes:
        raise ValueError("fanout observation requires a non-empty scope")
    best_count = (-1, -1)
    tied_codes: list[str] = []
    for index, code in enumerate(ordered_codes, start=1):
        try:
            complete = await read_definition(code)
        except Exception as exc:
            raise RuntimeError(f"{code} fanout observation failed: {exc}") from exc
        fact_count, occurrence_count = _restriction_counts(complete)
        count = (occurrence_count, fact_count)
        if count > best_count:
            best_count = count
            tied_codes = [code]
        elif count == best_count:
            tied_codes.append(code)
        if progress is not None:
            progress(index, len(ordered_codes), code)
    return FanoutObservation(
        concept_codes=tuple(tied_codes),
        restriction_fact_count=best_count[1],
        restriction_occurrence_count=best_count[0],
        scanned_concept_count=len(ordered_codes),
    )


class _CountingClient:
    def __init__(self, client: FanoutClient) -> None:
        self._client = client
        self.logical_select_count = 0
        self.select_once_r82_count = 0

    async def select(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]:
        self.logical_select_count += 1
        return await self._client.select(query, required_variables=required_variables)

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Sequence[Mapping[str, str | None]]:
        if "R82" in query:
            self.select_once_r82_count += 1
        return await self._client.select_once(
            query, required_variables=required_variables
        )


async def rerun_fanout_concept(
    client: FanoutClient,
    concept_code: str,
) -> FanoutRerun:
    """Run one observed maximum through the unchanged production decomposition path."""
    counted = _CountingClient(client)

    async def no_label_match(_surface_form: str) -> str | None:
        return None

    result = await _decompose_one(
        concept_code,
        cast("DecompositionSparqlClient", counted),
        label=None,
        label_lookup=no_label_match,
        source_identity="0" * 64,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )
    if result.decomposition is None:
        raise ValueError(f"highest-fanout concept {concept_code} did not decompose")
    complete = result.decomposition.complete_definition
    if complete is None:
        raise ValueError(
            f"highest-fanout concept {concept_code} has no complete record"
        )
    fact_count, occurrence_count = _restriction_counts(complete)
    return FanoutRerun(
        concept_code=concept_code,
        restriction_fact_count=fact_count,
        restriction_occurrence_count=occurrence_count,
        logical_select_count=counted.logical_select_count,
        select_once_r82_count=counted.select_once_r82_count,
    )


async def generate_fanout_baseline(
    client: FanoutClient,
    *,
    source_identity: str,
    ontology_release: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> FanoutBaseline:
    spec = branch_spec(DecompositionBranch.NEOPLASM)
    codes = await enumerate_scope_codes(client, spec.root_code)
    if progress is not None:
        progress(0, len(codes), "corpus-query")
    discovery_rows = []
    for query in build_fanout_discovery_queries():
        discovery_rows.extend(await client.select(query))
    observation = highest_fanout_from_discovery_rows(codes, discovery_rows)
    reruns = [
        await rerun_fanout_concept(client, code) for code in observation.concept_codes
    ]
    fact_count, logical_budget, r82_budget = _validated_rerun_counts(
        observation, reruns
    )
    baseline = FanoutBaseline(
        schema_version=BASELINE_SCHEMA_VERSION,
        source_identity=source_identity,
        ontology_release=ontology_release,
        branch=DecompositionBranch.NEOPLASM.value,
        scope_root=spec.root_code,
        scope_version=spec.scope_version,
        concept_codes=observation.concept_codes,
        restriction_fact_count=fact_count,
        restriction_occurrence_count=observation.restriction_occurrence_count,
        scanned_concept_count=observation.scanned_concept_count,
        discovery_algorithm=DISCOVERY_ALGORITHM,
        discovery_query_identity=discovery_query_identity(),
        logical_select_count_budget=logical_budget,
        select_once_r82_count_budget=r82_budget,
        baseline_identity="",
    )
    return replace(baseline, baseline_identity=baseline_identity(baseline))


def _validated_rerun_counts(
    observation: FanoutObservation, reruns: list[FanoutRerun]
) -> tuple[int, int, int]:
    occurrence_counts = {item.restriction_occurrence_count for item in reruns}
    fact_counts = {item.restriction_fact_count for item in reruns}
    if occurrence_counts != {observation.restriction_occurrence_count}:
        raise ValueError(
            "discovery and production occurrence counts disagree: "
            f"discovered={observation.restriction_occurrence_count}, "
            f"concepts={observation.concept_codes}, reruns={reruns}"
        )
    if len(fact_counts) != 1:
        raise ValueError("tied occurrence maxima have different fact counts")
    return (
        fact_counts.pop(),
        max(item.logical_select_count for item in reruns),
        max(item.select_once_r82_count for item in reruns),
    )


def write_fanout_baseline(path: Path, baseline: FanoutBaseline) -> None:
    payload = asdict(baseline)
    payload["concept_codes"] = list(baseline.concept_codes)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _require_int(document: dict[str, object], key: str, *, positive: bool) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"fanout baseline {key} must be an integer")
    if value < (1 if positive else 0):
        raise ValueError(f"fanout baseline {key} is out of range")
    return value


def _require_string(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"fanout baseline {key} must be a non-empty string")
    return value


def _validate_baseline_contract(document: dict[str, object]) -> None:
    if document.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError("fanout baseline schema version does not match code")
    spec = branch_spec(DecompositionBranch.NEOPLASM)
    expected = {
        "branch": DecompositionBranch.NEOPLASM.value,
        "scope_root": spec.root_code,
        "scope_version": spec.scope_version,
        "discovery_algorithm": DISCOVERY_ALGORITHM,
        "discovery_query_identity": discovery_query_identity(),
    }
    for key, value in expected.items():
        if document.get(key) != value:
            label = key.replace("_", " ")
            raise ValueError(f"fanout baseline {label} does not match production")


def _concept_codes(document: dict[str, object]) -> tuple[str, ...]:
    raw_codes = document.get("concept_codes")
    if (
        not isinstance(raw_codes, list)
        or not raw_codes
        or any(not isinstance(code, str) or not code for code in raw_codes)
        or raw_codes != sorted(set(raw_codes))
    ):
        raise ValueError("fanout baseline concept codes are invalid")
    return tuple(raw_codes)


def load_fanout_baseline(
    path: Path,
    *,
    expected_source_identity: str,
    expected_release: str,
) -> FanoutBaseline:
    """Load the canonical observation and reject any identity or contract drift."""
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"fanout baseline is unreadable: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("fanout baseline root must be an object")
    expected_keys = set(FanoutBaseline.__dataclass_fields__)
    if set(document) != expected_keys:
        raise ValueError("fanout baseline fields do not match its schema")
    identity = _require_string(document, "baseline_identity")
    if identity != baseline_identity(document):
        raise ValueError("fanout baseline identity does not match payload")
    source_identity = _require_string(document, "source_identity")
    if source_identity != expected_source_identity:
        raise ValueError("fanout baseline source identity does not match candidate")
    release = _require_string(document, "ontology_release")
    if release != expected_release:
        raise ValueError("fanout baseline release does not match candidate")
    _validate_baseline_contract(document)
    concept_codes = _concept_codes(document)
    return FanoutBaseline(
        schema_version=_require_int(document, "schema_version", positive=True),
        source_identity=source_identity,
        ontology_release=release,
        branch=_require_string(document, "branch"),
        scope_root=_require_string(document, "scope_root"),
        scope_version=_require_string(document, "scope_version"),
        concept_codes=concept_codes,
        restriction_fact_count=_require_int(
            document, "restriction_fact_count", positive=False
        ),
        restriction_occurrence_count=_require_int(
            document, "restriction_occurrence_count", positive=False
        ),
        scanned_concept_count=_require_int(
            document, "scanned_concept_count", positive=True
        ),
        discovery_algorithm=DISCOVERY_ALGORITHM,
        discovery_query_identity=discovery_query_identity(),
        logical_select_count_budget=_require_int(
            document, "logical_select_count_budget", positive=True
        ),
        select_once_r82_count_budget=_require_int(
            document, "select_once_r82_count_budget", positive=False
        ),
        baseline_identity=identity,
    )
