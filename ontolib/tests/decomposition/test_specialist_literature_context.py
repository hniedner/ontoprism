from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.research.specialist_literature_context import (
    LiteratureContextSource,
    generate_specialist_literature_context,
)

pytestmark = pytest.mark.unit

SOURCE = Path("scripts/research/data/specialist_literature_context_26_07d.json")


def test_c6135_source_facts_are_neutral_observations_without_answer_cues() -> None:
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())
    dossier = next(row for row in source.dossiers if row.code == "C6135")
    source_facts = "\n".join(
        claim.supported_claim
        for question in dossier.questions
        for claim in question.claims
    ).lower()

    assert "not a generic malignant neuroendocrine-cell operand" not in source_facts
    assert "without making poor differentiation universal" not in source_facts
    assert "not a universal property" not in source_facts
    assert "measured subset rather than a universal property" not in source_facts


def test_c100054_uses_the_stated_source_preferred_label_and_neutral_facts() -> None:
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())
    dossier = next(row for row in source.dossiers if row.code == "C100054")
    claims = [claim for question in dossier.questions for claim in question.claims]
    by_pair = {
        (claim.pair_key.axis, claim.pair_key.filler): [
            item for item in claims if item.pair_key == claim.pair_key
        ]
        for claim in claims
    }

    assert dossier.exact_label == "Conjunctival Melanocytic Intraepithelial Lesion"
    assert (
        "Conjunctival Melanocytic Intraepithelial Neoplasia" not in SOURCE.read_text()
    )
    p4_facts = " ".join(
        item.supported_claim for item in by_pair[("op:ClinicalFinding", "C8326")]
    )
    allowed_statuses = {
        "universal-defining",
        "universal-nondefining",
        "characteristic-nonuniversal",
        "classification-dependent",
        "inapplicable",
        "unresolved",
    }
    all_source_facts = " ".join(item.supported_claim for item in claims).lower()
    assert not any(status in all_source_facts for status in allowed_statuses)
    assert "so atypia degree is classification-dependent" not in p4_facts.lower()
    assert "low-grade atypia" in p4_facts
    assert "high-grade atypia" in p4_facts
    assert set(by_pair) == {
        ("op:ClinicalFinding", "C36027"),
        ("op:ClinicalFinding", "C8326"),
    }
    assert {
        item.citation_id
        for pair in (
            ("op:ClinicalFinding", "C36027"),
            ("op:ClinicalFinding", "C8326"),
        )
        for item in by_pair[pair]
    } == {
        "milman-2023",
        "milman-2023-low-grade",
        "milman-2023-high-grade",
        "mudhar-2024",
    }


def test_c100054_claim_excerpts_and_terms_are_exactly_passage_bound() -> None:
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())
    dossier = next(row for row in source.dossiers if row.code == "C100054")
    citations = {item.citation_id: item for item in dossier.citations}

    for question in dossier.questions:
        assert {(key.axis, key.filler) for key in question.pair_keys} == {
            ("op:ClinicalFinding", "C36027"),
            ("op:ClinicalFinding", "C8326"),
        }
        for claim in question.claims:
            passage = citations[claim.citation_id].exact_passage
            assert claim.support_excerpt in passage
            assert all(term.lower() in passage.lower() for term in claim.evidence_terms)


def test_c100054_bresler_2022_metadata_is_exact_and_access_is_honest() -> None:
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())
    dossier = next(row for row in source.dossiers if row.code == "C100054")
    citation = next(
        item for item in dossier.citations if item.citation_id == "bresler-2022"
    )

    assert citation.bibliography == (
        "Bresler SC et al. “Conjunctival Melanocytic Lesions.” "
        "Arch Pathol Lab Med. 2022;146(5):632-646."
    )
    assert citation.doi == "10.5858/arpa.2021-0006-RA"
    assert citation.pmid == "34424954"
    assert citation.status == "access-restricted"
    assert citation.verified_on is None
    assert "bresler-2021" not in SOURCE.read_text(encoding="utf-8")


def test_c100054_milman_table_five_passage_is_exact_and_newer_sources_are_bound() -> (
    None
):
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())
    dossier = next(row for row in source.dossiers if row.code == "C100054")
    citations = {item.citation_id: item for item in dossier.citations}

    milman = citations["milman-2023"]
    assert milman.url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC10601864/"
    assert "Table 5" in milman.exact_locator
    assert milman.exact_passage == (
        "The term melanoma in situ may be used for (1) the most atypical high-grade "
        "CMILs involving close to full thickness of the epithelium, (2) histologically "
        "obvious melanomas without documented evidence of subepithelial invasion."
    )

    wang = citations["wang-2025"]
    assert wang.status == "cited"
    assert wang.pmid == "40213303; PMCID PMC11981567"
    assert wang.doi == "10.4103/tjo.TJO-D-24-00109"
    assert "nomenclature" in wang.exact_passage.lower()

    kastelan = citations["kastelan-2025"]
    assert kastelan.status == "cited"
    assert kastelan.doi == "10.3389/pore.2025.1612085"
    assert kastelan.pmid == "40831998; PMCID PMC12358323"
    assert "melanoma in situ is now included" in kastelan.exact_passage


def test_tracked_literature_source_is_closed_per_citation_and_semantic_pair() -> None:
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())

    assert tuple(row.code for row in source.dossiers) == (
        "C27262",
        "C102870",
        "C6135",
        "C4791",
        "C100054",
        "C198031",
        "C35756",
    )
    assert all(
        len({citation.citation_id for citation in row.citations}) == len(row.citations)
        for row in source.dossiers
    )
    assert all(
        citation.authority_class
        and citation.bibliography
        and (citation.verified_on if citation.status == "cited" else True)
        and citation.exact_locator
        and citation.exact_passage
        and citation.supports
        and citation.does_not_support
        and citation.limitations
        and citation.conflicts_or_supersession
        for row in source.dossiers
        for citation in row.citations
    )
    assert all(
        question.claims
        and {(claim.pair_key.axis, claim.pair_key.filler) for claim in question.claims}
        == {(key.axis, key.filler) for key in question.pair_keys}
        and all(claim.question_id == question.question_id for claim in question.claims)
        and all(not key.axis.startswith("P") for key in question.pair_keys)
        for row in source.dossiers
        for question in row.questions
    )
    ovarian = next(row for row in source.dossiers if row.code == "C102870")
    morphology = {
        (question.pair_keys[0].axis, question.pair_keys[0].filler)
        for question in ovarian.questions
        if question.question_id.startswith("C102870-MORPHOLOGY-")
    }
    assert morphology == {
        ("op:Morphology", "C121619"),
        ("op:Morphology", "C39986"),
    }

    rendered = SOURCE.read_text(encoding="utf-8")
    assert "Kunikowska" not in rendered
    assert "case report" not in rendered.lower()
    assert "WHO-EYE04" in rendered
    assert "WHO-EYE05" in rendered
    assert "C198032" in rendered
    assert "C198034" in rendered
    assert "C6681" in rendered
    assert "C6682" in rendered
    assert "supply" not in rendered.lower()
    assert "not-found" not in rendered
    assert "research gap" not in rendered.lower()
    assert "10.1186/s13045-024-01571-4" in rendered
    assert "39075565" in rendered
    assert "PMC11287910" in rendered
    assert all(
        any(
            citation.status == "cited"
            and citation.exact_passage
            and not citation.exact_passage.startswith("Unavailable:")
            and citation.authority_order <= 2
            for citation in row.citations
        )
        for row in source.dossiers
    )
    assert all(
        len({(claim.pair_key.axis, claim.pair_key.filler) for claim in question.claims})
        == len(question.pair_keys)
        for row in source.dossiers
        for question in row.questions
    )


def test_question_claim_must_bind_its_exact_pair_and_accessible_source() -> None:
    payload = json.loads(SOURCE.read_bytes())
    question = payload["dossiers"][0]["questions"][0]
    question["claims"][0]["pair_key"] = {
        "axis": "op:Morphology",
        "filler": "C999999",
    }
    with pytest.raises(ValueError, match="claim must bind the question's exact pair"):
        LiteratureContextSource.model_validate_json(json.dumps(payload))


def test_literature_generator_is_deterministic_and_rejects_open_citations(
    tmp_path: Path,
) -> None:
    output = tmp_path / "literature.json"
    first = generate_specialist_literature_context(SOURCE, output)
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())
    assert tuple(
        citation.verified_on
        for dossier in first.dossiers
        for citation in dossier.citations
    ) == tuple(
        citation.verified_on
        for dossier in source.dossiers
        for citation in dossier.citations
    )
    first_bytes = output.read_bytes()
    second = generate_specialist_literature_context(SOURCE, output)
    assert first == second
    assert output.read_bytes() == first_bytes
    assert json.loads(first_bytes)["schema_version"] == 2

    payload = json.loads(SOURCE.read_bytes())
    payload["dossiers"][0]["citations"][0]["status"] = "not-found"
    payload["dossiers"][0]["citations"][0]["exact_passage"] = "fabricated quote"
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not-found citations are not dispatchable"):
        generate_specialist_literature_context(broken, output)


def test_access_restricted_source_has_no_verification_or_quote_surface() -> None:
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())
    restricted = [
        citation
        for dossier in source.dossiers
        for citation in dossier.citations
        if citation.status == "access-restricted"
    ]
    assert restricted
    assert all(citation.verified_on is None for citation in restricted)
    assert all(citation.exact_locator == "ACCESS RESTRICTED" for citation in restricted)
    assert all(citation.exact_passage == "NOT VERIFIED" for citation in restricted)


def test_final_oncology_pass_binds_requested_accessible_passages_to_exact_pairs() -> (
    None
):
    source = LiteratureContextSource.model_validate_json(SOURCE.read_bytes())
    expected = {
        "C6135": {
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC6821118/": {
                ("op:ClinicalFinding", "C41457"),
                ("op:ClinicalFinding", "C47804"),
                ("op:ClinicalFinding", "C47807"),
                ("op:NormalTissueOrigin", "C33782"),
            },
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC8683221/": {
                ("op:ClinicalFinding", "C155863"),
            },
        },
        "C4791": {
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC11905437/": {
                ("op:CellType", "C36899"),
                ("op:CellType", "C36954"),
                ("op:ClinicalFinding", "C36122"),
                ("op:ClinicalFinding", "C53583"),
            },
        },
        "C198031": {
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC4063430/": {
                ("op:NormalTissueOrigin", "C13049"),
                ("op:PrimarySite", "C12431"),
            },
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC10646822/": {
                ("op:Morphology", "C4005"),
            },
        },
        "C35756": {
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC3351680/": {
                ("op:ClinicalFinding", "C3331"),
                ("op:StageValue", "C27978"),
                ("op:StageValue", "C28064"),
            },
        },
    }

    records = {
        (record.code, record.url): record
        for record in source.oncology_accessible_evidence_records
    }
    assert len(records) == 6
    for code, sources in expected.items():
        for url, pair_keys in sources.items():
            record = records[code, url]
            assert record.checked_on == "2026-08-31"
            assert record.exact_short_passage
            assert pair_keys <= {(pair.axis, pair.filler) for pair in record.pair_keys}


def test_literature_generator_normalizes_an_absolute_external_source_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "specialist-literature.json"
    source.write_bytes(SOURCE.read_bytes())
    generated = generate_specialist_literature_context(
        source.resolve(), tmp_path / "generated.json"
    )
    assert generated.source_path == "external/specialist-literature.json"
    assert not Path(generated.source_path).is_absolute()
