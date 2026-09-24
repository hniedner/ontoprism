"""Complete-definition constructor census for a worklist and one-hop dependencies."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ontolib.decomposition.complete_definition import (
    CompleteDefinitionError,
    DefinitionBoundExceededError,
    UnsupportedDefinitionConstructorError,
)
from ontolib.decomposition.models import (
    CompleteDefinition,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
)
from ontolib.decomposition.provenance_models import CompletionRunMetrics

ReadDefinition = Callable[[str], Awaitable[CompleteDefinition]]
PreflightProgress = Callable[[int, int, str], None]
_PROGRESS_INTERVAL = 1000
_NO_MIXED_CHAIN_INVENTORY_IDENTITY = hashlib.sha256(
    b"no-mixed-chain-inventory"
).hexdigest()


class ClosureBudgetExceededError(RuntimeError):
    """The worklist's queued one-hop dependencies exceed the preflight budget."""


class SourcePreflightResult(BaseModel):
    """Census output with caller-supplied source and implementation identifiers."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: int = 1
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    worklist_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    reader_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    mixed_chain_inventory_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_identity: str = Field(min_length=1)
    walker_max_depth: int = Field(gt=0)
    max_nodes: int = Field(gt=0)
    worklist_count: int = Field(ge=0)
    checked_codes: tuple[str, ...]
    supported_codes: tuple[str, ...]
    unsupported_codes: tuple[str, ...]
    unsupported_reasons: dict[str, str]
    malformed_codes: tuple[str, ...]
    overflow_codes: tuple[str, ...]
    representative_metrics: CompletionRunMetrics

    @computed_field
    @property
    def concept_work_allowed(self) -> bool:
        return not self.malformed_codes and not self.overflow_codes

    @computed_field
    @property
    def identity(self) -> str:
        payload = self.model_dump(mode="json", exclude={"identity"})
        return hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()


def _worklist_identity(worklist: tuple[str, ...]) -> str:
    return hashlib.sha256(
        json.dumps(worklist, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _representative_unknown_metrics() -> CompletionRunMetrics:
    return CompletionRunMetrics(
        total_in_scope=1,
        decomposed=1,
        residual=0,
        semantic_excluded=0,
        atomic_noop=0,
        unknown_outcome=0,
        residual_precoordinated_count=0,
        residual_precoordination_unknown_count=1,
        residual_precoordination=None,
        minted_count=0,
        complete_definition_count=1,
        complete_fact_count=1,
        projected_fact_count=1,
        projection_loss_count=0,
        projection_loss_rate=0.0,
        pct_decomposed=1.0,
        roundtrip_fidelity=None,
    )


def _dependencies(definition: CompleteDefinition) -> set[str]:
    return {
        fact.genus_code
        for fact in definition.facts
        if isinstance(fact, GenusDefinitionFact) and fact.is_defined
    } | {
        fact.filler_code
        for fact in definition.facts
        if isinstance(fact, RestrictionDefinitionFact)
    }


class _DefinitionCensus:
    def __init__(self, read_definition: ReadDefinition) -> None:
        self._read_definition = read_definition
        self.supported: set[str] = set()
        self.unsupported: dict[str, str] = {}
        self.malformed: set[str] = set()
        self.overflow: set[str] = set()

    async def read(self, code: str) -> CompleteDefinition | None:
        try:
            definition = await self._read_definition(code)
        except UnsupportedDefinitionConstructorError as exc:
            self.unsupported[code] = str(exc)
            return None
        except DefinitionBoundExceededError:
            self.overflow.add(code)
            return None
        except CompleteDefinitionError:
            self.malformed.add(code)
            return None
        self.supported.add(code)
        return definition

    @property
    def checked_codes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                self.supported
                | self.unsupported.keys()
                | self.malformed
                | self.overflow
            )
        )


def _report_preflight_start(
    progress: PreflightProgress | None, worklist: tuple[str, ...]
) -> None:
    if progress is not None:
        progress(0, len(worklist), worklist[0] if worklist else "none")


def _report_preflight_position(
    progress: PreflightProgress | None,
    position: int | None,
    total: int,
    code: str,
) -> None:
    if (
        progress is not None
        and position is not None
        and (position % _PROGRESS_INTERVAL == 0 or position == total)
    ):
        progress(position, total, code)


def _queue_dependencies(
    definition: CompleteDefinition | None,
    position: int | None,
    *,
    scheduled: set[str],
    queue: deque[tuple[str, int | None]],
    closure_count: int,
    max_nodes: int,
    worklist_count: int,
) -> int:
    if definition is None or position is None:
        return closure_count
    dependencies = sorted(_dependencies(definition) - scheduled)
    updated_count = closure_count + len(dependencies)
    if updated_count > max_nodes:
        raise ClosureBudgetExceededError(
            "source preflight closure needs more than its budget of "
            f"{max_nodes} dependency concepts; it ran out while expanding "
            f"worklist concept {position} of {worklist_count}. The budget is "
            "shared by the whole worklist: no single concept is at fault."
        )
    scheduled.update(dependencies)
    queue.extend((dependency, None) for dependency in dependencies)
    return updated_count


async def run_source_preflight(
    worklist: tuple[str, ...],
    *,
    read_definition: ReadDefinition,
    source_identity: str,
    reader_identity: str,
    query_identity: str,
    tool_identity: str,
    walker_max_depth: int,
    max_nodes: int,
    mixed_chain_inventory_identity: str = _NO_MIXED_CHAIN_INVENTORY_IDENTITY,
    progress: PreflightProgress | None = None,
) -> SourcePreflightResult:
    """Census exact roots plus their defined-genus and filler dependencies.

    ``max_nodes`` bounds the dependency concepts outside the worklist, shared by the
    whole worklist. Dependencies are read once and are not recursively expanded. The
    queue is drained even after unsupported constructors are recorded; only malformed
    or over-bound definitions make ``concept_work_allowed`` false. ``overflow_codes``
    holds concepts whose own complete definition exceeds a reader bound.
    """
    # A worklist concept carries its 1-based position; a dependency carries None and
    # is read but not expanded.
    queue: deque[tuple[str, int | None]] = deque(
        (code, position) for position, code in enumerate(worklist, 1)
    )
    scheduled = set(worklist)
    census = _DefinitionCensus(read_definition)
    closure_count = 0
    _report_preflight_start(progress, worklist)
    while queue:
        code, position = queue.popleft()
        definition = await census.read(code)
        _report_preflight_position(progress, position, len(worklist), code)
        closure_count = _queue_dependencies(
            definition,
            position,
            scheduled=scheduled,
            queue=queue,
            closure_count=closure_count,
            max_nodes=max_nodes,
            worklist_count=len(worklist),
        )
    return SourcePreflightResult(
        source_identity=source_identity,
        worklist_identity=_worklist_identity(worklist),
        reader_identity=reader_identity,
        query_identity=query_identity,
        mixed_chain_inventory_identity=mixed_chain_inventory_identity,
        tool_identity=tool_identity,
        walker_max_depth=walker_max_depth,
        max_nodes=max_nodes,
        worklist_count=len(worklist),
        checked_codes=census.checked_codes,
        supported_codes=tuple(sorted(census.supported)),
        unsupported_codes=tuple(sorted(census.unsupported)),
        unsupported_reasons=dict(sorted(census.unsupported.items())),
        malformed_codes=tuple(sorted(census.malformed)),
        overflow_codes=tuple(sorted(census.overflow)),
        representative_metrics=_representative_unknown_metrics(),
    )
