"""Unit tests for legacy_writer — TTL rendering from Decomposition objects."""

from pathlib import Path

import pytest

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
