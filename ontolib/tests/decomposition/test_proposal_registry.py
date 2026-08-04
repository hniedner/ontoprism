from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.adjudication import main as adjudication_main

from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.proposal_registry import (
    ConceptProposal,
    CrossOntologyMapping,
    DuplicateCheck,
    DuplicateResult,
    ProposalRegistry,
    RelationProposal,
    load_proposal_registry,
    relation_proposal_id,
    resolve_proposal_identifier,
    write_submission_exports,
)

_SOURCE_IDENTITY = "a" * 64


def _duplicate_check(
    *,
    resource: str = "NCIt",
    result: DuplicateResult = "no-equivalent",
    candidates: tuple[str, ...] = (),
) -> DuplicateCheck:
    return DuplicateCheck(
        resource=resource,
        version="26.07d",
        query="Malignant Non-Seminomatous Germ Cell",
        result=result,
        candidates=candidates,
        evidence_url="https://api-evsrest.nci.nih.gov/api/v1/concept/ncit/search",
    )


def _concept() -> ConceptProposal:
    return ConceptProposal(
        id=MintedConcept(
            axis="op:CellType",
            label="Malignant Non-Seminomatous Germ Cell",
        ).id,
        axis="op:CellType",
        preferred_name="Malignant Non-Seminomatous Germ Cell",
        definition=("A malignant germ cell with non-seminomatous differentiation."),
        parent_concepts=("C12917",),
        semantic_types=("Cell",),
        synonyms=("Malignant Nonseminomatous Germ Cell",),
        source_concepts=("C27787",),
        source_roles=("R105",),
        rationale="No existing NCIt cell concept expresses the required intersection.",
        duplicate_checks=(_duplicate_check(),),
        mappings=(
            CrossOntologyMapping(
                system="SNOMED CT US",
                version="2025-09-01",
                concept_id="128766005",
                label="Germ cell tumor, nonseminomatous",
                predicate="relatedMatch",
                evidence_url=(
                    "https://evsexplore.semantics.cancer.gov/evsexplore/concept/"
                    "snomedct_us/128766005"
                ),
            ),
        ),
        submission_target="NCIt",
    )


def _relation() -> RelationProposal:
    return RelationProposal(
        id=relation_proposal_id("associated prior disease"),
        preferred_name="associated prior disease",
        definition=(
            "Relates a disease to a distinct disease that existed earlier and from "
            "which the subject disease arose or transformed."
        ),
        domain="C7057",
        range="C7057",
        source_roles=("R126",),
        source_examples=("C172130->C27262",),
        rationale="R126 conflates temporal transformation with other associations.",
        duplicate_checks=(_duplicate_check(resource="RO", result="no-equivalent"),),
        submission_target="RO",
    )


@pytest.mark.unit
def test_registry_filters_typed_proposals_and_exports_submission_packets() -> None:
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(_concept(), _relation()),
    )

    assert registry.filter(kind="concept", status="proposed") == (_concept(),)
    assert registry.filter(kind="relation") == (_relation(),)
    assert registry.ncit_submission_rows() == (
        {
            "proposal_id": "MINT-781c8c8c6096",
            "preferred_name": "Malignant Non-Seminomatous Germ Cell",
            "definition": (
                "A malignant germ cell with non-seminomatous differentiation."
            ),
            "parent_concepts": "C12917",
            "semantic_types": "Cell",
            "synonyms": "Malignant Nonseminomatous Germ Cell",
            "source_concepts": "C27787",
            "source_roles": "R105",
            "rationale": (
                "No existing NCIt cell concept expresses the required intersection."
            ),
            "status": "proposed",
        },
    )
    assert registry.relation_submission_rows() == (
        {
            "proposal_id": relation_proposal_id("associated prior disease"),
            "preferred_name": "associated prior disease",
            "definition": (
                "Relates a disease to a distinct disease that existed earlier and "
                "from which the subject disease arose or transformed."
            ),
            "domain": "C7057",
            "range": "C7057",
            "source_roles": "R126",
            "source_examples": "C172130->C27262",
            "rationale": (
                "R126 conflates temporal transformation with other associations."
            ),
            "status": "proposed",
            "submission_target": "RO",
        },
    )


@pytest.mark.unit
def test_registry_requires_duplicate_evidence_and_unique_proposal_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate_checks"):
        ConceptProposal(
            id="MINT-3a7f2c8e901d",
            axis="op:CellType",
            preferred_name="Missing Cell",
            definition="A missing cell definition.",
            parent_concepts=("C12917",),
            semantic_types=("Cell",),
            source_concepts=("C27787",),
            source_roles=("R105",),
            rationale="Missing from NCIt.",
            duplicate_checks=(),
            mappings=(),
            submission_target="NCIt",
        )

    with pytest.raises(ValidationError, match="deterministic concept proposal id"):
        ConceptProposal(
            **{
                **_concept().model_dump(),
                "id": "MINT-3a7f2c8e901d",
            }
        )

    with pytest.raises(ValidationError, match="proposal ids must be unique"):
        ProposalRegistry(
            source_identity=_SOURCE_IDENTITY,
            ontology_version="26.07d",
            proposals=(_concept(), _concept()),
        )


@pytest.mark.unit
def test_duplicate_check_requires_candidates_when_an_equivalent_exists() -> None:
    with pytest.raises(ValidationError, match="requires at least one candidate"):
        _duplicate_check(result="equivalent-found")
    with pytest.raises(ValidationError, match="possible-match requires"):
        _duplicate_check(result="possible-match")
    with pytest.raises(ValidationError, match="must not carry candidates"):
        _duplicate_check(result="no-equivalent", candidates=("C1",))


@pytest.mark.unit
def test_submission_exports_are_deterministic_and_proposed_only(
    tmp_path: Path,
) -> None:
    rejected = _concept().model_copy(update={"status": "rejected"})
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(_relation(), rejected),
    )

    write_submission_exports(registry, tmp_path)

    assert (tmp_path / "ncit-concept-proposals.csv").read_text() == (
        "proposal_id,preferred_name,definition,parent_concepts,semantic_types,"
        "synonyms,source_concepts,source_roles,rationale,status\n"
    )
    relation_csv = (tmp_path / "relation-proposals.csv").read_text()
    assert relation_csv.startswith(
        "proposal_id,preferred_name,definition,domain,range,source_roles,"
        "source_examples,rationale,status,submission_target\n"
    )
    assert relation_proposal_id("associated prior disease") in relation_csv
    manifest = json.loads((tmp_path / "submission-manifest.json").read_text())
    assert manifest == {
        "ontology_version": "26.07d",
        "registry_identity": registry.registry_identity,
        "relation_proposals": 1,
        "source_identity": _SOURCE_IDENTITY,
        "status": "proposed",
        "concept_proposals": 0,
    }


@pytest.mark.unit
def test_locally_approved_concept_is_augmented_and_replaceable(
    tmp_path: Path,
) -> None:
    approved = _concept().model_copy(update={"status": "locally-approved"})
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(approved,),
    )

    write_submission_exports(registry, tmp_path)

    ttl = (tmp_path / "augmented-ncit-proposals.ttl").read_text()
    assert "MINT-781c8c8c6096" in ttl
    assert 'op:proposalStatus "locally-approved"' in ttl
    assert "skos:relatedMatch <http://snomed.info/id/128766005>" in ttl
    assert resolve_proposal_identifier(registry, approved.id) == approved.id
    replacements = json.loads((tmp_path / "accepted-replacements.json").read_text())
    assert replacements == {}


@pytest.mark.unit
def test_accepted_concept_requires_and_resolves_to_ncit_replacement() -> None:
    with pytest.raises(ValidationError, match="accepted concept requires replacement"):
        ConceptProposal.model_validate(
            {**_concept().model_dump(), "status": "accepted"}
        )

    accepted = ConceptProposal.model_validate(
        {
            **_concept().model_dump(),
            "status": "accepted",
            "replacement_ncit_code": "C999999",
        }
    )
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(accepted,),
    )

    assert resolve_proposal_identifier(registry, accepted.id) == "C999999"


@pytest.mark.unit
def test_concept_proposal_requires_versioned_external_mapping() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        ConceptProposal(**{**_concept().model_dump(), "mappings": ()})

    with pytest.raises(ValidationError, match="mapping version"):
        CrossOntologyMapping(
            system="SNOMED CT US",
            version="",
            concept_id="128766005",
            label="Germ cell tumor, nonseminomatous",
            predicate="relatedMatch",
            evidence_url="https://example.test",
        )


@pytest.mark.unit
def test_registry_load_rejects_duplicate_json_keys_and_tampering(
    tmp_path: Path,
) -> None:
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(_concept(),),
    )
    path = tmp_path / "registry.json"
    path.write_text(registry.model_dump_json(by_alias=True), encoding="utf-8")

    assert load_proposal_registry(path) == registry

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_proposal_registry(duplicate)

    payload = registry.model_dump(mode="json", by_alias=True)
    payload["ontology_version"] = "26.08a"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="identity does not match"):
        load_proposal_registry(path)


@pytest.mark.unit
def test_adjudication_cli_validates_and_exports_proposal_registry(
    tmp_path: Path,
) -> None:
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(_concept(), _relation()),
    )
    path = tmp_path / "registry.json"
    output = tmp_path / "exports"
    path.write_text(registry.model_dump_json(), encoding="utf-8")

    adjudication_main(["export-proposals", str(path), str(output)])

    assert (output / "ncit-concept-proposals.csv").is_file()
    assert (output / "relation-proposals.csv").is_file()
    assert (output / "submission-manifest.json").is_file()


@pytest.mark.unit
def test_tracked_proposal_registry_remains_valid() -> None:
    registry = load_proposal_registry(
        Path(__file__).with_name("golden") / "proposal-registry.json"
    )

    assert registry.source_identity == (
        "f54dd2910a31245a30cea094dc72ce6a5c8d7b5a9c4e484007a35a1c343624c8"
    )
    assert len(registry.filter(kind="concept", status="locally-approved")) == 1
    assert len(registry.filter(kind="relation", status="proposed")) == 6
