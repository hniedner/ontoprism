from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, cast, get_args

import pytest
import rdflib
from pydantic import ValidationError
from rdflib.namespace import RDFS
from scripts.adjudication import main as adjudication_main

from ontolib.decomposition import proposal_registry as proposal_registry_module
from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.proposal_registry import (
    CertifiedNcitRelease,
    ConceptAdoptionEvidence,
    ConceptProposal,
    CrossOntologyMapping,
    DuplicateCheck,
    DuplicateResult,
    ProposalRegistry,
    ProposalStatus,
    RelationAdoptionEvidence,
    RelationProposal,
    load_proposal_registry,
    relation_proposal_id,
    resolve_proposal_identifier,
    write_proposal_registry,
    write_submission_exports,
)

_SOURCE_IDENTITY = "a" * 64


def _release() -> CertifiedNcitRelease:
    return CertifiedNcitRelease(
        release="26.08a",
        source_identity="b" * 64,
        source_artifact_sha256="c" * 64,
        source_manifest_sha256="d" * 64,
        certification_profile="ontoprism-ncit-official-release-v1",
        certification_evidence_identity="e" * 64,
    )


def _concept_adoption(code: str = "C999999") -> ConceptAdoptionEvidence:
    """Synthetic structural fixture; it makes no real NCI-adoption claim."""
    return ConceptAdoptionEvidence(
        kind="concept",
        official_release=_release(),
        adopted_ncit_code=code,
        adopted_concept_fingerprint="f" * 64,
        adoption_evidence_identity="1" * 64,
        provenance_url="https://example.test/synthetic-ncit-adoption-evidence",
    )


def _relation_adoption(
    iri: str = "http://purl.obolibrary.org/obo/RO_1234567",
    version: str = "26.08a",
) -> RelationAdoptionEvidence:
    """Synthetic structural fixture; it makes no real NCI-adoption claim."""
    return RelationAdoptionEvidence(
        kind="relation",
        official_release=_release(),
        adopted_relation_iri=iri,
        adopted_relation_version=version,
        adopted_assertion_fingerprint="2" * 64,
        adoption_evidence_identity="3" * 64,
        provenance_url="https://example.test/synthetic-ncit-role-adoption-evidence",
    )


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
        axis="op:AssociatedPriorDisease",
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
def test_proposal_status_is_the_exact_closed_lifecycle() -> None:
    assert set(get_args(ProposalStatus)) == {
        "proposed",
        "locally-approved",
        "submitted",
        "accepted-in-ncit",
        "rejected",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", ["proposed", "locally-approved", "submitted", "rejected"]
)
def test_only_an_accepted_concept_may_carry_a_replacement_code(
    status: ProposalStatus,
) -> None:
    """The reject direction of the D60 lifecycle.

    `resolve_proposal_identifier` returns `replacement_ncit_code` whenever it is
    set, so a still-`proposed` record carrying one would publish an NCIt code NCI
    has not assigned.
    """
    with pytest.raises(
        ValidationError, match="only accepted-in-ncit concept may carry"
    ):
        _concept().model_copy(
            update={"status": status, "replacement_ncit_code": "C999999"}
        ).model_validate(
            _concept().model_dump()
            | {"status": status, "replacement_ncit_code": "C999999"}
        )


@pytest.mark.unit
def test_relation_proposal_must_use_its_deterministic_id() -> None:
    with pytest.raises(ValidationError, match="deterministic relation proposal id"):
        RelationProposal.model_validate(
            _relation().model_dump() | {"id": "RELPROP-not-derived"}
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", ["proposed", "locally-approved", "submitted", "rejected"]
)
def test_only_an_accepted_relation_may_carry_a_replacement_identity(
    status: ProposalStatus,
) -> None:
    with pytest.raises(
        ValidationError, match="only accepted-in-ncit relation may carry"
    ):
        RelationProposal.model_validate(
            _relation().model_dump()
            | {
                "status": status,
                "replacement_relation_iri": "http://purl.obolibrary.org/obo/RO_0004026",
                "replacement_relation_version": "2026-08-05",
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize("iri", ["/obo/RO_0004026", "not an iri", "obo/RO_0004026", ""])
def test_replacement_relation_iri_must_be_absolute(iri: str) -> None:
    with pytest.raises(ValidationError, match="must be an absolute IRI"):
        RelationProposal.model_validate(
            _relation().model_dump()
            | {
                "status": "accepted-in-ncit",
                "replacement_relation_iri": iri,
                "replacement_relation_version": "26.08a",
                "adoption_evidence": _relation_adoption(iri=iri).model_dump(),
            }
        )


@pytest.mark.unit
def test_duplicate_check_candidates_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="candidates must be unique"):
        DuplicateCheck.model_validate(
            _duplicate_check(result="equivalent-found", candidates=("C1",)).model_dump()
            | {"candidates": ("C1", "C1")}
        )


@pytest.mark.unit
def test_duplicate_checks_must_use_unique_resources() -> None:
    """One resource checked twice is not two independent corroborations."""
    check = _duplicate_check()
    with pytest.raises(ValidationError, match="unique resources"):
        ConceptProposal.model_validate(
            _concept().model_dump()
            | {"duplicate_checks": (check.model_dump(), check.model_dump())}
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
            "axis": "op:AssociatedPriorDisease",
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
def test_equivalent_found_can_only_close_a_rejected_proposal() -> None:
    equivalent = _duplicate_check(
        result="equivalent-found",
        candidates=("C54110",),
    )
    payload = {
        **_concept().model_dump(),
        "duplicate_checks": (equivalent,),
    }

    with pytest.raises(
        ValidationError, match="equivalent-found proposals must be rejected"
    ):
        ConceptProposal.model_validate(payload)

    rejected = ConceptProposal.model_validate(payload | {"status": "rejected"})
    assert rejected.status == "rejected"


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

    first = tmp_path / "first"
    second = tmp_path / "second"
    write_submission_exports(registry, first)
    write_submission_exports(registry, second)

    assert (first / "ncit-concept-proposals.csv").read_text() == (
        "proposal_id,preferred_name,definition,parent_concepts,semantic_types,"
        "synonyms,source_concepts,source_roles,rationale,status\n"
    )
    relation_csv = (first / "relation-proposals.csv").read_text()
    assert relation_csv.startswith(
        "proposal_id,axis,preferred_name,definition,domain,range,source_roles,"
        "source_examples,rationale,status,submission_target\n"
    )
    assert relation_proposal_id("associated prior disease") in relation_csv
    manifest = json.loads((first / "submission-manifest.json").read_text())
    assert manifest == {
        "ontology_version": "26.07d",
        "registry_identity": registry.registry_identity,
        "relation_proposals": 1,
        "source_identity": _SOURCE_IDENTITY,
        "status": "proposed",
        "concept_proposals": 0,
        "files": {
            name: hashlib.sha256((first / name).read_bytes()).hexdigest()
            for name in (
                "accepted-replacements.json",
                "augmented-ncit-proposals.ttl",
                "ncit-concept-proposals.csv",
                "relation-proposals.csv",
            )
        },
    }
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }


@pytest.mark.unit
def test_submission_manifest_is_promoted_last_and_detects_partial_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    initial = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(_relation(),),
    )
    write_submission_exports(initial, tmp_path)
    original_manifest = (tmp_path / "submission-manifest.json").read_bytes()

    accepted_payload = _relation().model_dump()
    accepted_payload.update(
        status="accepted-in-ncit",
        replacement_relation_iri="http://purl.obolibrary.org/obo/RO_1234567",
        replacement_relation_version="26.08a",
        adoption_evidence=_relation_adoption().model_dump(),
    )
    replacement = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(RelationProposal.model_validate(accepted_payload),),
    )
    real_replace = os.replace

    def fail_manifest_promotion(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        if Path(destination).name == "submission-manifest.json":
            raise OSError("manifest promotion failed")
        real_replace(source, destination)

    monkeypatch.setattr(proposal_registry_module.os, "replace", fail_manifest_promotion)

    with pytest.raises(OSError, match="manifest promotion failed"):
        write_submission_exports(replacement, tmp_path)

    assert (tmp_path / "submission-manifest.json").read_bytes() == original_manifest
    manifest = json.loads(original_manifest)
    assert any(
        hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() != expected
        for name, expected in manifest["files"].items()
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    ["proposed", "locally-approved", "submitted", "accepted-in-ncit", "rejected"],
)
def test_concept_export_matrix_is_governed_by_status(
    tmp_path: Path,
    status: ProposalStatus,
) -> None:
    payload = _concept().model_dump()
    payload["status"] = status
    if status == "accepted-in-ncit":
        payload["replacement_ncit_code"] = "C999999"
        payload["adoption_evidence"] = _concept_adoption().model_dump()
    proposal = ConceptProposal.model_validate(payload)
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(proposal,),
    )

    write_submission_exports(registry, tmp_path)

    concept_csv = (tmp_path / "ncit-concept-proposals.csv").read_text()
    augmented = (tmp_path / "augmented-ncit-proposals.ttl").read_text()
    replacements = json.loads((tmp_path / "accepted-replacements.json").read_text())
    assert (proposal.id in concept_csv) is (status == "proposed")
    assert (proposal.id in augmented) is (
        status in {"locally-approved", "submitted", "accepted-in-ncit"}
    )
    assert ('op:proposalStatus "accepted-in-ncit"' in augmented) is (
        status == "accepted-in-ncit"
    )
    assert 'op:proposalStatus "accepted"' not in augmented
    assert replacements == (
        {proposal.id: "C999999"} if status == "accepted-in-ncit" else {}
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    ["proposed", "locally-approved", "submitted", "accepted-in-ncit", "rejected"],
)
def test_relation_export_matrix_is_governed_by_status(
    tmp_path: Path,
    status: ProposalStatus,
) -> None:
    payload = _relation().model_dump()
    payload["status"] = status
    if status == "accepted-in-ncit":
        payload.update(
            replacement_relation_iri="http://purl.obolibrary.org/obo/RO_1234567",
            replacement_relation_version="26.08a",
            adoption_evidence=_relation_adoption().model_dump(),
        )
    proposal = RelationProposal.model_validate(payload)
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(proposal,),
    )

    write_submission_exports(registry, tmp_path)

    relation_csv = (tmp_path / "relation-proposals.csv").read_text()
    replacements = json.loads((tmp_path / "accepted-replacements.json").read_text())
    assert (proposal.id in relation_csv) is (status == "proposed")
    assert replacements == (
        {
            proposal.id: {
                "identifier": "http://purl.obolibrary.org/obo/RO_1234567",
                "version": "26.08a",
            }
        }
        if status == "accepted-in-ncit"
        else {}
    )


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
def test_augmented_turtle_preserves_every_parent(tmp_path: Path) -> None:
    approved = _concept().model_copy(
        update={
            "status": "locally-approved",
            "parent_concepts": ("C12917", "C12508"),
        }
    )
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(approved,),
    )

    write_submission_exports(registry, tmp_path)

    graph = rdflib.Graph().parse(tmp_path / "augmented-ncit-proposals.ttl")
    subject = rdflib.URIRef("https://w3id.org/ontoprism/vocab#MINT-781c8c8c6096")
    assert set(graph.objects(subject, RDFS.subClassOf)) == {
        rdflib.URIRef("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12917"),
        rdflib.URIRef("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12508"),
    }


@pytest.mark.unit
def test_proposal_rdf_terms_reject_turtle_injection() -> None:
    with pytest.raises(ValidationError, match="mapping concept_id"):
        CrossOntologyMapping(
            system="Example Ontology",
            version="1",
            concept_id="https://example.test/value> . op:injected <https://evil.test/x",
            label="Unsafe mapping",
            predicate="relatedMatch",
            evidence_url="https://example.test/evidence",
        )

    with pytest.raises(ValidationError, match="parent_concepts"):
        ConceptProposal.model_validate(
            {
                **_concept().model_dump(),
                "parent_concepts": ("C12917> ; op:injected op:value",),
            }
        )


@pytest.mark.unit
def test_terminal_concept_requires_matching_replacement_and_adoption_evidence() -> None:
    with pytest.raises(ValidationError, match="accepted-in-ncit concept requires"):
        ConceptProposal.model_validate(
            {**_concept().model_dump(), "status": "accepted-in-ncit"}
        )

    terminal = ConceptProposal.model_validate(
        {
            **_concept().model_dump(),
            "status": "accepted-in-ncit",
            "replacement_ncit_code": "C999999",
            "adoption_evidence": _concept_adoption().model_dump(),
        }
    )
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(terminal,),
    )

    assert resolve_proposal_identifier(registry, terminal.id) == "C999999"

    for mutation, message in (
        ({"adoption_evidence": None}, "requires adoption evidence"),
        (
            {"adoption_evidence": _concept_adoption("C888888").model_dump()},
            "must match adoption evidence",
        ),
        (
            {"adoption_evidence": {"kind": "concept", "adopted_ncit_code": "C999999"}},
            "official_release|Field required",
        ),
    ):
        with pytest.raises(ValidationError, match=message):
            ConceptProposal.model_validate(terminal.model_dump() | mutation)


@pytest.mark.unit
def test_terminal_relation_requires_correlated_identity_version_and_evidence(
    tmp_path: Path,
) -> None:
    payload = {**_relation().model_dump(), "status": "accepted-in-ncit"}

    with pytest.raises(ValidationError, match="accepted-in-ncit relation requires"):
        RelationProposal.model_validate(payload)

    terminal = RelationProposal.model_validate(
        payload
        | {
            "replacement_relation_iri": "http://purl.obolibrary.org/obo/RO_1234567",
            "replacement_relation_version": "26.08a",
            "adoption_evidence": _relation_adoption().model_dump(),
        }
    )
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(terminal,),
    )

    assert resolve_proposal_identifier(registry, terminal.id) == (
        "http://purl.obolibrary.org/obo/RO_1234567"
    )
    write_submission_exports(registry, tmp_path)
    replacements = json.loads((tmp_path / "accepted-replacements.json").read_text())
    assert replacements == {
        terminal.id: {
            "identifier": "http://purl.obolibrary.org/obo/RO_1234567",
            "version": "26.08a",
        }
    }

    for mutation in (
        {"adoption_evidence": None},
        {"replacement_relation_version": "26.09a"},
        {"replacement_relation_iri": "https://example.test/different-role"},
    ):
        with pytest.raises(ValidationError, match="adoption evidence"):
            RelationProposal.model_validate(terminal.model_dump() | mutation)


@pytest.mark.unit
def test_terminal_proposals_reject_missing_and_cross_kind_adoption_evidence() -> None:
    concept = _concept().model_dump() | {
        "status": "accepted-in-ncit",
        "replacement_ncit_code": "C999999",
    }
    with pytest.raises(ValidationError, match="requires adoption evidence"):
        ConceptProposal.model_validate(concept)
    with pytest.raises(ValidationError, match="concept adoption evidence"):
        ConceptProposal.model_validate(
            concept | {"adoption_evidence": _relation_adoption().model_dump()}
        )

    relation = _relation().model_dump() | {
        "status": "accepted-in-ncit",
        "replacement_relation_iri": "http://purl.obolibrary.org/obo/RO_1234567",
        "replacement_relation_version": "26.08a",
    }
    with pytest.raises(ValidationError, match="requires adoption evidence"):
        RelationProposal.model_validate(relation)
    with pytest.raises(ValidationError, match="relation adoption evidence"):
        RelationProposal.model_validate(
            relation | {"adoption_evidence": _concept_adoption().model_dump()}
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "replacement",
    [
        {"replacement_relation_iri": "http://purl.obolibrary.org/obo/RO_1234567"},
        {"replacement_relation_version": "26.08a"},
    ],
)
def test_terminal_relation_rejects_each_partial_replacement_identity(
    replacement: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="requires replacement IRI and version"):
        RelationProposal.model_validate(
            _relation().model_dump()
            | {
                "status": "accepted-in-ncit",
                "adoption_evidence": _relation_adoption().model_dump(),
                **replacement,
            }
        )


@pytest.mark.unit
def test_nonterminal_relation_forbids_adoption_without_replacement_identity() -> None:
    with pytest.raises(ValidationError, match="may carry adoption evidence"):
        RelationProposal.model_validate(
            _relation().model_dump()
            | {"adoption_evidence": _relation_adoption().model_dump()}
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"provenance_url": "relative"}, "provenance URL"),
        (
            {"official_release": {**_release().model_dump(), "release": ""}},
            "official NCIt release",
        ),
        (
            {
                "official_release": {
                    **_release().model_dump(),
                    "certification_profile": "",
                }
            },
            "certification profile",
        ),
    ],
)
def test_adoption_evidence_requires_identified_certification_and_provenance(
    mutation: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ConceptAdoptionEvidence.model_validate(
            _concept_adoption().model_dump() | mutation
        )

    relation = _relation_adoption().model_dump()
    relation["adopted_relation_version"] = "different-release"
    with pytest.raises(ValidationError, match="must match official NCIt release"):
        RelationAdoptionEvidence.model_validate(relation)

    relation = _relation_adoption().model_dump()
    relation["adopted_relation_iri"] = "relative-role"
    with pytest.raises(ValidationError, match="must be an absolute IRI"):
        RelationAdoptionEvidence.model_validate(relation)


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", ["proposed", "locally-approved", "submitted", "rejected"]
)
def test_nonterminal_proposals_forbid_adoption_evidence(
    status: ProposalStatus,
) -> None:
    with pytest.raises(
        ValidationError, match=r"only accepted-in-ncit.*adoption evidence"
    ):
        ConceptProposal.model_validate(
            _concept().model_dump()
            | {"status": status, "adoption_evidence": _concept_adoption().model_dump()}
        )


@pytest.mark.unit
def test_relation_proposal_requires_a_safe_normalized_axis() -> None:
    payload = _relation().model_dump()

    with pytest.raises(ValidationError, match="axis"):
        RelationProposal.model_validate(payload | {"axis": "Associated Prior Disease"})


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
def test_ncit_mapping_uses_the_official_concept_iri_and_rejects_non_codes(
    tmp_path: Path,
) -> None:
    mapping = CrossOntologyMapping(
        system="NCIt",
        version="26.08a",
        concept_id="C1234",
        label="Synthetic NCIt mapping",
        predicate="exactMatch",
        evidence_url="https://example.test/synthetic-ncit-mapping",
    )
    proposal = ConceptProposal.model_validate(
        _concept().model_dump() | {"mappings": (mapping.model_dump(),)}
    )

    write_submission_exports(
        ProposalRegistry(
            source_identity=_SOURCE_IDENTITY,
            ontology_version="26.08a",
            proposals=(proposal.model_copy(update={"status": "locally-approved"}),),
        ),
        tmp_path,
    )

    assert (
        "skos:exactMatch "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1234>"
        in (tmp_path / "augmented-ncit-proposals.ttl").read_text()
    )
    with pytest.raises(ValidationError, match="safe identifier"):
        CrossOntologyMapping.model_validate(
            mapping.model_dump() | {"concept_id": "not-an-ncit-code"}
        )


@pytest.mark.unit
def test_resolution_preserves_unknown_and_unaccepted_proposal_identifiers() -> None:
    proposal = _concept()
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(proposal,),
    )

    assert resolve_proposal_identifier(registry, proposal.id) == proposal.id
    assert resolve_proposal_identifier(registry, "C-unknown") == "C-unknown"


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
    write_proposal_registry(registry, path)

    assert load_proposal_registry(path) == registry

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":2,"schema_version":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_proposal_registry(duplicate)

    payload = registry.model_dump(mode="json", by_alias=True)
    payload["ontology_version"] = "26.08a"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="identity does not match"):
        load_proposal_registry(path)

    for required_field in ("schema_version", "registry_identity"):
        payload = registry.model_dump(mode="json", by_alias=True)
        payload.pop(required_field)
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match=f"missing {required_field}"):
            load_proposal_registry(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "JSON object"),
        (
            {"schema_version": 2, "registry_identity": "0" * 64, "proposals": {}},
            "proposals must be a list",
        ),
        (
            {"schema_version": 2, "registry_identity": "0" * 64, "proposals": [1]},
            "proposal 0 must be an object",
        ),
        (
            {"schema_version": 2, "registry_identity": 1, "proposals": []},
            "SHA-256 digest",
        ),
        (
            {"schema_version": 2, "registry_identity": "bad", "proposals": []},
            "SHA-256 digest",
        ),
    ],
)
def test_registry_loader_rejects_malformed_persisted_container_shapes(
    tmp_path: Path, payload: object, message: str
) -> None:
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_proposal_registry(path)


@pytest.mark.unit
@pytest.mark.parametrize("status", [None, "accepted", "review-required", 1])
def test_persisted_registry_rejects_missing_legacy_and_malformed_status(
    tmp_path: Path, status: object
) -> None:
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(_concept(),),
    )
    payload = registry.model_dump(mode="json", exclude={"registry_identity"})
    proposal = payload["proposals"][0]
    if status is None:
        proposal.pop("status")
    else:
        proposal["status"] = status
    payload["registry_identity"] = proposal_registry_module._identity(
        {key: value for key, value in payload.items() if key != "registry_identity"}
    )
    path = tmp_path / "invalid-registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises((ValueError, ValidationError), match=r"status|literal"):
        load_proposal_registry(path)


@pytest.mark.unit
def test_registry_writer_is_canonical_atomic_and_deterministic(tmp_path: Path) -> None:
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(_concept(), _relation()),
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_proposal_registry(registry, first)
    write_proposal_registry(registry, second)
    initial = first.read_bytes()
    adjudication_main(["write-proposal-registry", str(first)])

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == initial
    assert load_proposal_registry(first) == registry
    assert json.loads(first.read_text())["schema_version"] == 2


@pytest.mark.unit
def test_registry_writer_cleans_staging_file_when_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ProposalRegistry(
        source_identity=_SOURCE_IDENTITY,
        ontology_version="26.07d",
        proposals=(_concept(),),
    )
    target = tmp_path / "registry.json"
    target.write_text("preserved\n", encoding="utf-8")
    real_named_temporary_file = cast(
        "Any", proposal_registry_module.tempfile.NamedTemporaryFile
    )

    class FailingTemporaryFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._temporary = real_named_temporary_file(*args, **kwargs)

        @property
        def name(self) -> str:
            return self._temporary.name

        def __enter__(self) -> FailingTemporaryFile:
            self._temporary.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._temporary.__exit__(*args)

        def write(self, _content: str) -> int:
            raise OSError("injected write failure")

    monkeypatch.setattr(
        proposal_registry_module.tempfile,
        "NamedTemporaryFile",
        FailingTemporaryFile,
    )

    with pytest.raises(OSError, match="injected write failure"):
        write_proposal_registry(registry, target)
    assert target.read_text(encoding="utf-8") == "preserved\n"
    assert tuple(tmp_path.glob(".registry.json.*.tmp")) == ()


@pytest.mark.unit
def test_malformed_registry_cli_writes_no_export(tmp_path: Path) -> None:
    source = tmp_path / "invalid.json"
    output = tmp_path / "exports"
    source.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source_identity": _SOURCE_IDENTITY,
                "ontology_version": "26.07d",
                "proposals": [],
                "registry_identity": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="identity does not match"):
        adjudication_main(["export-proposals", str(source), str(output)])
    assert not output.exists()


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
    write_proposal_registry(registry, path)

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
    assert [
        proposal.id
        for proposal in registry.filter(kind="relation", status="locally-approved")
    ] == ["RELPROP-8637e8500dff"]
    assert len(registry.filter(kind="relation", status="proposed")) == 5
    assert registry.schema_version == 2
    assert registry.registry_identity == (
        "fab02c05906bcca0ed33cc483465640e2348e98bef8c7ad01460c23da3eac7c1"
    )
    assert Counter(proposal.status for proposal in registry.proposals) == {
        "locally-approved": 2,
        "proposed": 5,
    }
    assert all(proposal.status != "accepted" for proposal in registry.proposals)
