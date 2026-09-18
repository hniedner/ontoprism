"""Fail-fast complete-definition constructor census for an exact workset."""

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
_NO_MIXED_CHAIN_INVENTORY_IDENTITY = hashlib.sha256(
    b"no-mixed-chain-inventory"
).hexdigest()


class ClosureBudgetExceededError(RuntimeError):
    """The worklist's dependency closure is larger than the preflight budget."""


class SourcePreflightResult(BaseModel):
    """Identity-bound census output, including every rejected source code."""

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


async def _read_classified_definition(
    code: str,
    read_definition: ReadDefinition,
    *,
    supported: set[str],
    unsupported: dict[str, str],
    malformed: set[str],
    overflow: set[str],
) -> CompleteDefinition | None:
    try:
        definition = await read_definition(code)
    except UnsupportedDefinitionConstructorError as exc:
        unsupported[code] = str(exc)
        return None
    except DefinitionBoundExceededError:
        overflow.add(code)
        return None
    except CompleteDefinitionError:
        malformed.add(code)
        return None
    supported.add(code)
    return definition


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
) -> SourcePreflightResult:
    """Census exact roots plus conservative defined-genus and filler closure.

    ``max_nodes`` bounds the dependency concepts outside the worklist, shared by the
    whole worklist; when the worklist needs more, the census stops with
    ``ClosureBudgetExceededError``. ``overflow_codes`` holds only concepts whose own
    complete definition exceeds a reader bound (``DefinitionBoundExceededError``).
    """
    # A worklist concept carries its 1-based position; a dependency carries 0 and is
    # read but not expanded.
    queue = deque((code, position) for position, code in enumerate(worklist, 1))
    scheduled = set(worklist)
    supported: set[str] = set()
    unsupported: dict[str, str] = {}
    malformed: set[str] = set()
    overflow: set[str] = set()
    closure_count = 0
    while queue:
        code, position = queue.popleft()
        definition = await _read_classified_definition(
            code,
            read_definition,
            supported=supported,
            unsupported=unsupported,
            malformed=malformed,
            overflow=overflow,
        )
        if definition is None or not position:
            continue
        dependencies = sorted(_dependencies(definition) - scheduled)
        closure_count += len(dependencies)
        if closure_count > max_nodes:
            raise ClosureBudgetExceededError(
                "source preflight closure needs more than its budget of "
                f"{max_nodes} dependency concepts; it ran out while expanding "
                f"worklist concept {position} of {len(worklist)}. The budget is "
                "shared by the whole worklist: no single concept is at fault."
            )
        scheduled.update(dependencies)
        queue.extend((dependency, 0) for dependency in dependencies)
    checked = tuple(sorted(supported | unsupported.keys() | malformed | overflow))
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
        checked_codes=checked,
        supported_codes=tuple(sorted(supported)),
        unsupported_codes=tuple(sorted(unsupported)),
        unsupported_reasons=dict(sorted(unsupported.items())),
        malformed_codes=tuple(sorted(malformed)),
        overflow_codes=tuple(sorted(overflow)),
        representative_metrics=_representative_unknown_metrics(),
    )
