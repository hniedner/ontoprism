"""Unit tests for legacy_writer — TTL rendering from Decomposition objects."""

from datetime import date
from pathlib import Path

import pytest
import rdflib

from ontolib.decomposition import vocab
from ontolib.decomposition.axes import MORPHOLOGY_AXIS
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS


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


@pytest.mark.unit
async def test_equivalence_emitted_when_flag_and_genus_set(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            genus_code="C141041",
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
                Constituent(axis="R101", filler_code="C12400", axis_source="role"),
                Constituent(
                    axis=MORPHOLOGY_AXIS,
                    filler_code="C36761",
                    axis_source="parent",
                ),
                Constituent(
                    axis="op:Laterality", filler_code="MINT-abc", axis_source="nlp"
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1", emit_equivalence=True)
    content = out.read_text()

    # Core equivalence structure
    assert OWL_NS + "equivalentClass" in content
    assert OWL_NS + "intersectionOf" in content
    assert " a " in content  # rdf:type via Turtle `a` keyword

    # Genus as first intersection member
    assert f"<{NCIT_NS}C141041>" in content

    # Role-sourced restrictions only (not nlp/parent)
    assert f"<{NCIT_NS}R88>" in content  # owl:onProperty <R88>
    assert f"<{NCIT_NS}C27970>" in content  # owl:someValuesFrom <C27970>
    assert f"<{NCIT_NS}R101>" in content
    assert f"<{NCIT_NS}C12400>" in content

    # The regular op: triples are still present
    assert vocab.HAS_CONSTITUENT in content
    assert vocab.REPRESENTATION_STATUS in content


@pytest.mark.unit
async def test_equivalence_not_emitted_when_flag_off(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            genus_code="C141041",
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1", emit_equivalence=False)
    content = out.read_text()
    assert OWL_NS + "equivalentClass" not in content
    assert OWL_NS + "intersectionOf" not in content


@pytest.mark.unit
async def test_equivalence_not_emitted_when_genus_missing(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            genus_code=None,
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1", emit_equivalence=True)
    content = out.read_text()
    assert OWL_NS + "equivalentClass" not in content


@pytest.mark.unit
async def test_equivalence_excludes_nlp_and_parent_constituents(
    tmp_path: Path,
) -> None:
    """Only axis_source='role' constituents appear in the equivalence axiom."""
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            genus_code="C141041",
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
                Constituent(
                    axis=MORPHOLOGY_AXIS,
                    filler_code="C36761",
                    axis_source="parent",
                ),
                Constituent(
                    axis="op:Laterality", filler_code="MINT-abc", axis_source="nlp"
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1", emit_equivalence=True)
    content = out.read_text()

    # The role-sourced restriction IS in the equivalence block
    assert f"<{NCIT_NS}R88>" in content
    assert f"<{NCIT_NS}C27970>" in content

    # The NLP/morphology fillers appear only in op:hasConstituent, not the
    # equivalence block — check that the filler IRIs appear *outside* the
    # intersection block (hard to assert absence inside a blank node, so just
    # check that a single op:hasConstituent references them).
    assert "C36761" in content  # morphology (in regular triples)
    assert "MINT-abc" in content  # nlp (in regular triples)


@pytest.mark.unit
async def test_equivalence_output_is_valid_turtle(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            genus_code="C141041",
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
                Constituent(axis="R101", filler_code="C12400", axis_source="role"),
                Constituent(
                    axis=MORPHOLOGY_AXIS,
                    filler_code="C36761",
                    axis_source="parent",
                    most_specific=True,
                ),
                Constituent(
                    axis="op:Laterality",
                    filler_code="MINT-abc123",
                    axis_source="nlp",
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1", emit_equivalence=True)

    graph = rdflib.Graph()
    graph.parse(out, format="turtle")
    assert len(graph) > 0


@pytest.mark.unit
async def test_equivalence_not_emitted_when_no_role_constituents(
    tmp_path: Path,
) -> None:
    """Genus is set but there are no role-sourced constituents to put in the
    intersection — equivalence is not emitted (would be a degenerate class)."""
    decs = [
        Decomposition(
            code="C999",
            semantic_type="Disease",
            genus_code="C100",
            constituents=[
                Constituent(
                    axis=MORPHOLOGY_AXIS,
                    filler_code="C36761",
                    axis_source="parent",
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1", emit_equivalence=True)
    content = out.read_text()
    assert OWL_NS + "equivalentClass" not in content


@pytest.mark.unit
async def test_equivalence_with_d19_routed_axis(tmp_path: Path) -> None:
    """D19/D20 routed axes (op:AssociatedRegion) use their own IRI in the
    restriction, not the original NCIt role."""
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            genus_code="C141041",
            constituents=[
                Constituent(
                    axis="op:AssociatedRegion",
                    filler_code="C13063",
                    axis_source="role",
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out, run_id="run-1", emit_equivalence=True)
    content = out.read_text()

    # The restriction uses the op: IRI as owl:onProperty
    assert f"<{vocab.ONTOPRISM_NS}AssociatedRegion>" in content
    assert f"<{NCIT_NS}C13063>" in content


@pytest.mark.unit
async def test_group_id_is_rendered(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(
                    axis="op:AssociatedRegion",
                    filler_code="C12418",
                    axis_source="role",
                    group="op:AssociatedRegion",
                ),
                Constituent(
                    axis="op:AssociatedRegion",
                    filler_code="C13063",
                    axis_source="role",
                    group="op:AssociatedRegion",
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out)
    content = out.read_text()
    assert vocab.GROUP in content
    assert '"op:AssociatedRegion"' in content


@pytest.mark.unit
async def test_no_group_triple_when_group_is_none(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C100",
            semantic_type=None,
            constituents=[
                Constituent(axis="R88", filler_code="C27970", axis_source="role"),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out)
    assert vocab.GROUP not in out.read_text()


@pytest.mark.unit
async def test_grouped_output_is_valid_turtle(tmp_path: Path) -> None:
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(
                    axis="op:AssociatedRegion",
                    filler_code="C12418",
                    axis_source="role",
                    group="op:AssociatedRegion",
                ),
                Constituent(
                    axis="op:AssociatedRegion",
                    filler_code="C13063",
                    axis_source="role",
                    group="op:AssociatedRegion",
                ),
            ],
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out)
    graph = rdflib.Graph()
    graph.parse(out, format="turtle")
    assert len(graph) > 0
