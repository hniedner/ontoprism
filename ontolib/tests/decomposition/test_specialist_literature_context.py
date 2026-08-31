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
