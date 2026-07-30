"""Unit tests for legacy_writer — TTL rendering from Decomposition objects."""

import os
from datetime import date
from pathlib import Path

import pytest
import rdflib
from rdflib import Literal, URIRef
from rdflib.namespace import OWL

from ontolib.decomposition import vocab
from ontolib.decomposition.axes import MORPHOLOGY_AXIS
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    DefinitionGroup,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
)


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
async def test_file_artifact_is_flushed_and_fsynced_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "out.ttl"
    synced_inodes: list[int] = []
    real_fsync = os.fsync

    def record_fsync(file_descriptor: int) -> None:
        synced_inodes.append(os.fstat(file_descriptor).st_ino)
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    await write_ttl([_decomposition_for_durability()], dest=out, run_id="run-1")

    assert synced_inodes == [out.stat().st_ino]
    assert out.read_text(encoding="utf-8").endswith("\n")


def _decomposition_for_durability() -> Decomposition:
    return Decomposition(
        code="C1",
        semantic_type="Neoplastic Process",
        constituents=[
            Constituent(
                axis="op:PrimarySite",
                filler_code="C12400",
                axis_source="role",
                source_role="R101",
            )
        ],
    )


@pytest.mark.unit
async def test_ttl_publishes_axis_contract_and_constituent_source_role(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out.ttl"
    await write_ttl(
        [
            Decomposition(
                code="C1",
                semantic_type="Neoplastic Process",
                constituents=[
                    Constituent(
                        axis="op:PrimarySite",
                        filler_code="C12400",
                        axis_source="role",
                        source_role="R101",
                    )
                ],
            )
        ],
        dest=out,
    )
    graph = rdflib.Graph().parse(out)
    primary_site = URIRef(f"{vocab.ONTOPRISM_NS}PrimarySite")
    source_role = URIRef("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#R101")

    assert (primary_site, rdflib.RDF.type, OWL.ObjectProperty) in graph
    assert (primary_site, rdflib.RDFS.domain, None) in graph
    assert (primary_site, rdflib.RDFS.range, None) in graph
    assert (primary_site, rdflib.RDFS.comment, None) in graph
    assert (primary_site, URIRef(vocab.NORMALIZED_FROM_ROLE), source_role) in graph
    assert (None, URIRef(vocab.SOURCE_ROLE), source_role) in graph


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
async def test_equivalence_request_preserves_existing_destination(
    tmp_path: Path,
) -> None:
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
    accepted = b"accepted artifact\n"
    out.write_bytes(accepted)

    with pytest.raises(ValueError, match="not available"):
        await write_ttl(decs, dest=out, run_id="run-1", emit_equivalence=True)

    assert out.read_bytes() == accepted


@pytest.mark.unit
async def test_equivalence_request_does_not_write_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(ValueError, match="not available"):
        await write_ttl(
            [Decomposition(code="C6135", semantic_type="Neoplastic Process")],
            emit_equivalence=True,
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.unit
async def test_equivalence_request_does_not_create_destination_parent(
    tmp_path: Path,
) -> None:
    out = tmp_path / "missing" / "out.ttl"

    with pytest.raises(ValueError, match="not available"):
        await write_ttl(
            [Decomposition(code="C6135", semantic_type="Neoplastic Process")],
            dest=out,
            emit_equivalence=True,
        )

    assert not out.parent.exists()


@pytest.mark.unit
async def test_normal_output_never_contains_equivalence(tmp_path: Path) -> None:
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
    graph = rdflib.Graph()
    graph.parse(out, format="turtle")
    assert not any(graph.triples((None, OWL.equivalentClass, None)))
    assert not any(graph.triples((None, OWL.intersectionOf, None)))


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


@pytest.mark.unit
async def test_complete_definition_and_projection_trace_are_rendered(
    tmp_path: Path,
) -> None:
    genus_id = "a" * 64
    restriction_id = "b" * 64
    group_id = "c" * 64
    nested_group_id = "d" * 64
    decs = [
        Decomposition(
            code="C6135",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(
                    axis="R88",
                    filler_code="C27970",
                    axis_source="role",
                    needs_review=True,
                    group="disease-1",
                    source_definition_ids=(restriction_id,),
                )
            ],
            complete_definition=CompleteDefinition(
                root_code="C6135",
                facts=(
                    GenusDefinitionFact(
                        fact_id=genus_id,
                        anchor_code="C6135",
                        group_id=group_id,
                        depth=0,
                        genus_code="C141041",
                        is_defined=True,
                    ),
                    RestrictionDefinitionFact(
                        fact_id=restriction_id,
                        anchor_code="C6135",
                        group_id=nested_group_id,
                        depth=0,
                        role_code="R88",
                        filler_code="C27970",
                    ),
                ),
                groups=(
                    DefinitionGroup(
                        group_id=group_id,
                        anchor_code="C6135",
                        depth=0,
                        child_group_ids=(nested_group_id,),
                    ),
                    DefinitionGroup(
                        group_id=nested_group_id,
                        anchor_code="C6135",
                        depth=0,
                    ),
                ),
                root_group_ids=(group_id,),
            ),
        )
    ]
    out = tmp_path / "out.ttl"
    await write_ttl(decs, dest=out)

    graph = rdflib.Graph()
    graph.parse(out, format="turtle")
    source = URIRef("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C6135")
    restriction = URIRef(f"{vocab.DEFINITION_FACT_NS}{restriction_id}")
    root_group = URIRef(f"{vocab.DEFINITION_GROUP_NS}{group_id}")
    nested_group = URIRef(f"{vocab.DEFINITION_GROUP_NS}{nested_group_id}")
    assert (source, URIRef(vocab.HAS_DEFINITION_FACT), restriction) in graph
    assert (
        restriction,
        URIRef(vocab.FACT_KIND),
        Literal("restriction"),
    ) in graph
    assert (
        restriction,
        URIRef(vocab.DEFINITION_GROUP),
        nested_group,
    ) in graph
    assert (
        source,
        URIRef(vocab.HAS_ROOT_DEFINITION_GROUP),
        root_group,
    ) in graph
    assert (
        root_group,
        URIRef(vocab.HAS_CHILD_DEFINITION_GROUP),
        nested_group,
    ) in graph
    constituent = next(graph.objects(source, URIRef(vocab.HAS_CONSTITUENT)))
    assert (
        constituent,
        URIRef(vocab.NEEDS_REVIEW),
        Literal(True),
    ) in graph
    assert (
        constituent,
        URIRef(vocab.SOURCE_DEFINITION_FACT),
        restriction,
    ) in graph
    assert (
        source,
        URIRef(vocab.PROJECTION_LOSS_COUNT),
        Literal(1),
    ) in graph
