"""Unit tests for legacy_writer — TTL rendering from Decomposition objects."""

from datetime import date
from pathlib import Path

import pytest
import rdflib

from ontolib.decomposition import vocab
from ontolib.decomposition.axes import MORPHOLOGY_AXIS
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Constituent, Decomposition


@pytest.mark.unit
async def test_single_decomposition_writes_to_file(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
                Constituent(
                    axis=MORPHOLOGY_AXIS,
                    filler_code="C36761",
                    axis_source="parent",
                    most_specific=True,
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="test-run-1")
    content = out.read_text()

    # Structural checks — presence of key triples.
    assert vocab.REPRESENTATION_STATUS in content
    assert vocab.LEGACY_PRECOORDINATED in content
    assert "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C27970>" in content
    assert vocab.FILLER in content
    assert '"parent"' in content
    assert vocab.MOST_SPECIFIC in content


@pytest.mark.unit
async def test_minted_filler_uses_opns_prefix(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C4791",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(
                    axis="op:Laterality",
                    filler_code="MINT-abc123",
                    axis_source="nlp",
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out)
    content = out.read_text()
    assert f"<{vocab.ONTOPRISM_NS}MINT-abc123>" in content


@pytest.mark.unit
async def test_empty_decomposition_writes_status_only(tmp_path: Path) -> None:
    decs = [Decomposition(code="C999", semantic_type=None, constituents=[])]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out)
    content = out.read_text()
    assert "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C999>" in content
    assert vocab.HAS_CONSTITUENT not in content


@pytest.mark.unit
async def test_most_specific_flag_is_rendered(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C123",
            semantic_type="Disease or Syndrome",
            constituents=[
                Constituent(
                    axis="R101",
                    filler_code="C456",
                    axis_source="role",
                    most_specific=True,
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out)
    content = out.read_text()
    assert vocab.MOST_SPECIFIC in content


@pytest.mark.unit
async def test_run_id_is_rendered(tmp_path: Path) -> None:
    decs = [Decomposition(code="C100", semantic_type=None)]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-abc")
    content = out.read_text()
    assert vocab.DECOMPOSED_BY in content
    assert '"run-abc"' in content


@pytest.mark.unit
async def test_axis_source_role_is_rendered(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C100",
            semantic_type=None,
            constituents=[
                Constituent(axis="R88", filler_code="C200", axis_source="role"),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out)
    content = out.read_text()
    assert vocab.AXIS_SOURCE in content
    assert '"role"' in content


@pytest.mark.unit
async def test_writer_never_emits_a_delete(tmp_path: Path) -> None:
    # Structural additivity guarantee (design §8/§11 test_additive_no_deletions): the
    # writer only ever renders triples, never a SPARQL/Turtle delete construct, and the
    # source graphs are never named in its output — only the concept/constituent
    # subjects and the op: vocabulary appear.
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1")
    content = out.read_text()
    assert "DELETE" not in content.upper()


@pytest.mark.unit
async def test_writer_output_is_valid_turtle(tmp_path: Path) -> None:
    # Regression guard: a structurally-plausible-looking string is not necessarily
    # parseable Turtle (this caught real bugs — unbracketed predicate IRIs and an
    # unclosed blank-node list — that pure substring assertions missed).
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
                Constituent(
                    axis=MORPHOLOGY_AXIS,
                    filler_code="C36761",
                    axis_source="parent",
                    most_specific=True,
                ),
                Constituent(
                    axis="op:Laterality", filler_code="MINT-abc123", axis_source="nlp"
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1")

    graph = rdflib.Graph()
    graph.parse(out, format="turtle")  # raises on malformed Turtle
    assert len(graph) > 0


@pytest.mark.unit
async def test_explicit_emitted_on_is_used_over_todays_date(tmp_path: Path) -> None:
    decs = [Decomposition(code="C100", semantic_type=None)]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, emitted_on=date(2020, 1, 1))
    content = out.read_text()
    assert '"2020-01-01"' in content


@pytest.mark.unit
async def test_no_dest_writes_to_stdout_and_returns_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    decs = [Decomposition(code="C100", semantic_type=None)]
    result = await write_ttl(decs)
    assert result is None
    captured = capsys.readouterr()
    assert "C100" in captured.out
