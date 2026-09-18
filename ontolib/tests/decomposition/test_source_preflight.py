from __future__ import annotations

import pytest

from ontolib.decomposition.complete_definition import (
    CompleteDefinitionError,
    DefinitionBoundExceededError,
    UnsupportedDefinitionConstructorError,
)
from ontolib.decomposition.models import (
    CompleteDefinition,
    DefinitionGroup,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
    canonical_definition_fact_id,
    canonical_definition_group_id,
)
from ontolib.decomposition.source_preflight import run_source_preflight


def _definition(code: str, genus: str, filler: str) -> CompleteDefinition:
    group = canonical_definition_group_id(
        code, (f"genus:{genus}:defined", f"restriction:R101:{filler}")
    )
    return CompleteDefinition(
        root_code=code,
        facts=(
            GenusDefinitionFact(
                fact_id=canonical_definition_fact_id(
                    code, group, "genus", genus, "defined"
                ),
                anchor_code=code,
                group_id=group,
                depth=0,
                genus_code=genus,
                is_defined=True,
            ),
            RestrictionDefinitionFact(
                fact_id=canonical_definition_fact_id(
                    code, group, "restriction", "R101", filler
                ),
                anchor_code=code,
                group_id=group,
                depth=0,
                role_code="R101",
                filler_code=filler,
            ),
        ),
        groups=(
            DefinitionGroup(
                group_id=group,
                anchor_code=code,
                depth=0,
            ),
        ),
        root_group_ids=(group,),
    )


@pytest.mark.unit
async def test_preflight_enumerates_closure_and_distinguishes_valid_unknowns() -> None:
    seen: list[str] = []

    async def read(code: str) -> CompleteDefinition:
        seen.append(code)
        if code == "C36081":
            raise UnsupportedDefinitionConstructorError(
                "unsupported owl:unionOf member"
            )
        return _definition(code, "C2", "C36081")

    result = await run_source_preflight(
        ("C1",),
        read_definition=read,
        source_identity="a" * 64,
        reader_identity="b" * 64,
        query_identity="c" * 64,
        tool_identity="qlever-v1",
        walker_max_depth=7,
        max_nodes=4096,
    )

    assert result.checked_codes == ("C1", "C2", "C36081")
    assert result.unsupported_codes == ("C36081",)
    assert result.malformed_codes == ()
    assert result.overflow_codes == ()
    assert result.representative_metrics.residual_precoordination_unknown_count == 1
    assert result.representative_metrics.residual_precoordination is None
    assert seen == ["C1", "C2", "C36081"]


@pytest.mark.unit
async def test_preflight_identity_binds_mixed_chain_inventory() -> None:
    async def read(code: str) -> CompleteDefinition:
        return _definition(code, "C2", "C3")

    shared = {
        "read_definition": read,
        "source_identity": "a" * 64,
        "reader_identity": "b" * 64,
        "query_identity": "c" * 64,
        "tool_identity": "qlever-v1",
        "walker_max_depth": 7,
        "max_nodes": 4096,
    }
    first = await run_source_preflight(
        ("C1",), mixed_chain_inventory_identity="d" * 64, **shared
    )
    second = await run_source_preflight(
        ("C1",), mixed_chain_inventory_identity="e" * 64, **shared
    )

    assert first.mixed_chain_inventory_identity == "d" * 64
    assert first.identity != second.identity


@pytest.mark.unit
async def test_preflight_rejects_malformed_and_overflow() -> None:
    async def read(code: str) -> CompleteDefinition:
        if code == "C1":
            raise CompleteDefinitionError("definition RDF list contains a cycle")
        raise DefinitionBoundExceededError(
            "definition exceeds the 64 member list bound"
        )

    result = await run_source_preflight(
        ("C1", "C2"),
        read_definition=read,
        source_identity="a" * 64,
        reader_identity="b" * 64,
        query_identity="c" * 64,
        tool_identity="qlever-v1",
        walker_max_depth=7,
        max_nodes=4096,
    )

    assert result.unsupported_codes == ()
    assert result.malformed_codes == ("C1",)
    assert result.overflow_codes == ("C2",)
    assert result.concept_work_allowed is False


@pytest.mark.unit
async def test_preflight_does_not_infer_overflow_from_malformed_error_text() -> None:
    async def read(_code: str) -> CompleteDefinition:
        raise CompleteDefinitionError("malformed row contains a bound label")

    result = await run_source_preflight(
        ("C1",),
        read_definition=read,
        source_identity="a" * 64,
        reader_identity="b" * 64,
        query_identity="c" * 64,
        tool_identity="qlever-v1",
        walker_max_depth=7,
        max_nodes=4096,
    )

    assert result.malformed_codes == ("C1",)
    assert result.overflow_codes == ()


@pytest.mark.unit
async def test_preflight_closure_bound_does_not_count_exact_worklist_roots() -> None:
    async def read(code: str) -> CompleteDefinition:
        if code == "C9":
            return _definition(code, code, code)
        return _definition(code, code, "C9")

    result = await run_source_preflight(
        ("C1", "C2", "C3"),
        read_definition=read,
        source_identity="a" * 64,
        reader_identity="b" * 64,
        query_identity="c" * 64,
        tool_identity="qlever-v1",
        walker_max_depth=7,
        max_nodes=1,
    )

    assert result.checked_codes == ("C1", "C2", "C3", "C9")
    assert result.overflow_codes == ()
    assert result.concept_work_allowed is True


@pytest.mark.unit
async def test_preflight_does_not_recurse_through_direct_fillers() -> None:
    async def read(code: str) -> CompleteDefinition:
        if code == "C1":
            return _definition(code, code, "C2")
        return _definition(code, code, "C3")

    result = await run_source_preflight(
        ("C1",),
        read_definition=read,
        source_identity="a" * 64,
        reader_identity="b" * 64,
        query_identity="c" * 64,
        tool_identity="qlever-v1",
        walker_max_depth=7,
        max_nodes=4096,
    )

    assert result.checked_codes == ("C1", "C2")


@pytest.mark.unit
async def test_preflight_identity_binds_a_fail_closed_closure_overflow() -> None:
    async def read(code: str) -> CompleteDefinition:
        return _definition(code, "C2", "C3")

    result = await run_source_preflight(
        ("C1",),
        read_definition=read,
        source_identity="a" * 64,
        reader_identity="b" * 64,
        query_identity="c" * 64,
        tool_identity="qlever-v1",
        walker_max_depth=7,
        max_nodes=1,
    )

    assert result.overflow_codes == ("C1",)
    assert result.concept_work_allowed is False
    assert len(result.identity) == 64
