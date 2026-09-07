# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ontolib.decomposition import r103_evidence_application as application_module
from ontolib.decomposition.r103_evidence_application import (
    R103ApplicationError,
    build_applied_policy_report,
    build_authority_artifact,
    build_candidate_artifact,
    build_corroboration_normalization,
    build_source_inventory,
    load_applied_policy_report,
    load_authority_artifact,
    load_candidate_artifact,
    load_corroboration_normalization,
    load_source_inventory,
    query_candidate_codes,
    query_source_inventory,
    write_artifact,
)

GOLDEN = Path(__file__).with_name("golden")
REV1 = GOLDEN / "r103-review-state-26.07d.json"
REV2 = GOLDEN / "r103-review-state-26.07d-rev2.json"
HISTORICAL_CORROBORATION = GOLDEN / "r103-c3264-corroboration-26.07d.json"
MIGRATION = GOLDEN / "proposal-registry-schema2-migration.json"
REGISTRY = GOLDEN / "proposal-registry.json"
ORACLE = GOLDEN / "neoplasm-adjudicated.json"


class _SelectClient:
    def __init__(self, *responses: list[dict[str, str]]) -> None:
        self.responses = list(responses)
        self.queries: list[tuple[str, set[str]]] = []

    async def select(
        self,
        query: str,
        *,
        required_variables: set[str] = set(),  # noqa: B006
    ) -> list[dict[str, str]]:
        self.queries.append((query, required_variables))
        return self.responses.pop(0)


def _manifest(path: Path, owl: Path) -> None:
    raw = owl.read_bytes()
    path.write_text(
        json.dumps(
            {
                "ontology_version": "26.07d",
                "source_identity": "a" * 64,
                "graph_layout": {"stated_graph_iri": "urn:stated"},
                "stated_artifact": {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "artifact_identity": "b" * 64,
                },
                "loader": {"tool": {"name": "qlever", "version": "test"}},
            }
        ),
        encoding="utf-8",
    )


def _owl(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
 xmlns:owl="http://www.w3.org/2002/07/owl#"
 xmlns:n="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#">
 <owl:Class rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12950">
  <rdfs:label>Embryonic Tissue</rdfs:label><n:P97>Root definition.</n:P97>
 </owl:Class>
 <owl:Class rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C200">
  <rdfs:label>Candidate Two</rdfs:label><n:P97>Two.</n:P97><n:P106>Tissue</n:P106>
  <rdfs:subClassOf rdf:resource="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C100"/>
 </owl:Class>
 <owl:Class rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C100">
  <rdfs:label>Candidate One</rdfs:label><n:P97>One.</n:P97><n:P106>Tissue</n:P106>
  <rdfs:subClassOf rdf:resource="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12950"/>
 </owl:Class>
 <owl:Class rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C2860">
  <rdfs:label>Adrenal Rest Tumor</rdfs:label><n:P97>Disease.</n:P97>
  <owl:equivalentClass><owl:Class><owl:intersectionOf rdf:parseType="Collection">
   <rdf:Description rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1"/>
   <owl:Restriction><owl:onProperty rdf:resource="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#R103"/><owl:someValuesFrom rdf:resource="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12950"/></owl:Restriction>
  </owl:intersectionOf></owl:Class></owl:equivalentClass>
 </owl:Class>
 <owl:Class rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C999">
  <rdfs:label>Direct Disease</rdfs:label><n:P97>Direct.</n:P97>
  <rdfs:subClassOf><owl:Restriction><owl:onProperty rdf:resource="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#R103"/><owl:someValuesFrom rdf:resource="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C34228"/></owl:Restriction></rdfs:subClassOf>
 </owl:Class>
 <owl:ObjectProperty rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#R103"><rdfs:label>Origin</rdfs:label><n:P97>Role.</n:P97></owl:ObjectProperty>
 <owl:Class rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C34228"><rdfs:label>Neuroectoderm</rdfs:label><n:P97>Filler.</n:P97></owl:Class>
</rdf:RDF>""",
        encoding="utf-8",
    )


@pytest.mark.unit
def test_source_inventory_and_candidates_are_complete_source_only_and_deterministic(
    tmp_path: Path,
) -> None:
    owl = tmp_path / "stated.owl"
    manifest = tmp_path / "manifest.json"
    _owl(owl)
    _manifest(manifest, owl)
    qlever_inventory = (
        ("C2860", "R103", "C12950", "equivalent-class-intersection"),
        ("C999", "R103", "C34228", "direct-subclass-restriction"),
    )
    inventory = build_source_inventory(
        owl, manifest, qlever_rows=qlever_inventory, row_bound=10
    )
    assert [
        (r.subject_code, r.encoding, r.member_position) for r in inventory.rows
    ] == [
        ("C2860", "equivalent-class-intersection", 1),
        ("C999", "direct-subclass-restriction", 0),
    ]
    raw = json.dumps(inventory.model_dump(mode="json"))
    assert all(key not in raw for key in ("current_state", '"policy"', '"outcome"'))
    first = tmp_path / "inventory-1.json"
    second = tmp_path / "inventory-2.json"
    write_artifact(first, inventory)
    write_artifact(second, inventory)
    assert first.read_bytes() == second.read_bytes()
    assert load_source_inventory(first) == inventory

    candidates = build_candidate_artifact(
        owl,
        manifest,
        qlever_rows=(("C100",), ("C200",)),
        row_bound=10,
    )
    assert [(r.code, r.path) for r in candidates.candidates] == [
        ("C100", ("C12950", "C100")),
        ("C200", ("C12950", "C100", "C200")),
    ]
    assert candidates.status == "candidates-enumerated-human-selection-required"
    assert '"verdict"' not in json.dumps(candidates.model_dump(mode="json"))
    candidate_path = tmp_path / "candidates.json"
    write_artifact(candidate_path, candidates)
    assert load_candidate_artifact(candidate_path) == candidates

    with pytest.raises(R103ApplicationError, match="QLever/XML parity"):
        build_candidate_artifact(
            owl, manifest, qlever_rows=(("C100",), ("C404",)), row_bound=10
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_qlever_reads_are_bounded_counted_and_paged() -> None:
    inventory_client = _SelectClient(
        [
            {
                "subject": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C2860",
                "role": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#R103",
                "filler": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12950",
                "encoding": "equivalent-class-intersection",
            }
        ]
    )
    assert await query_source_inventory(inventory_client, row_bound=2) == (
        ("C2860", "R103", "C12950", "equivalent-class-intersection"),
    )
    assert "LIMIT 3" in inventory_client.queries[0][0]
    assert inventory_client.queries[0][1] == {
        "subject",
        "role",
        "filler",
        "encoding",
    }

    candidate_client = _SelectClient(
        [{"count": "3"}],
        [
            {"candidate": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C100"},
            {"candidate": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C200"},
        ],
        [{"candidate": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C300"}],
    )
    assert await query_candidate_codes(candidate_client, row_bound=4, page_size=2) == (
        ("C100",),
        ("C200",),
        ("C300",),
    )
    assert "LIMIT 2 OFFSET 0" in candidate_client.queries[1][0]
    assert "LIMIT 2 OFFSET 2" in candidate_client.queries[2][0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_qlever_read_guards_reject_overflow_malformed_counts_and_page_drift() -> (
    None
):
    with pytest.raises(R103ApplicationError, match="inventory bound"):
        await query_source_inventory(_SelectClient([]), row_bound=0)
    with pytest.raises(R103ApplicationError, match="inventory exceeds bound"):
        await query_source_inventory(
            _SelectClient(
                [
                    {
                        "subject": "x",
                        "role": "x",
                        "filler": "x",
                        "encoding": "x",
                    },
                    {
                        "subject": "x",
                        "role": "x",
                        "filler": "x",
                        "encoding": "x",
                    },
                ]
            ),
            row_bound=1,
        )
    with pytest.raises(R103ApplicationError, match="non-NCIt"):
        await query_source_inventory(
            _SelectClient(
                [
                    {
                        "subject": "urn:not-ncit",
                        "role": "urn:not-ncit",
                        "filler": "urn:not-ncit",
                        "encoding": "direct-subclass-restriction",
                    }
                ]
            ),
            row_bound=1,
        )

    for row_bound, page_size in ((0, 1), (2, 0)):
        with pytest.raises(R103ApplicationError, match="bounds are invalid"):
            await query_candidate_codes(
                _SelectClient([]), row_bound=row_bound, page_size=page_size
            )
    with pytest.raises(R103ApplicationError, match="page exceeds"):
        await query_candidate_codes(_SelectClient([]), row_bound=2, page_size=3)
    for rows in ([], [{"count": "1"}, {"count": "1"}]):
        with pytest.raises(R103ApplicationError, match="count query shape"):
            await query_candidate_codes(_SelectClient(rows), row_bound=2, page_size=1)
    for row in ({}, {"count": "not-an-integer"}):
        with pytest.raises(R103ApplicationError, match="count is malformed"):
            await query_candidate_codes(_SelectClient([row]), row_bound=2, page_size=1)
    with pytest.raises(R103ApplicationError, match="enumeration exceeds"):
        await query_candidate_codes(
            _SelectClient([{"count": "3"}]), row_bound=2, page_size=1
        )
    with pytest.raises(R103ApplicationError, match="page/count parity"):
        await query_candidate_codes(
            _SelectClient(
                [{"count": "2"}],
                [
                    {
                        "candidate": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C100"
                    }
                ],
                [],
            ),
            row_bound=2,
            page_size=1,
        )


def _rewrite_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _source_files(tmp_path: Path) -> tuple[Path, Path]:
    owl = tmp_path / "stated.owl"
    manifest = tmp_path / "manifest.json"
    _owl(owl)
    _manifest(manifest, owl)
    return owl, manifest


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "bound", "message"),
    [
        ((), 0, "bound must be positive"),
        (
            (
                ("C2860", "R103", "C12950", "equivalent-class-intersection"),
                ("C2860", "R103", "C12950", "equivalent-class-intersection"),
            ),
            10,
            "duplicate semantic",
        ),
        (
            (("wrong", "R103", "C12950", "equivalent-class-intersection"),),
            10,
            "non-target",
        ),
        (
            (("C2860", "R999", "C12950", "equivalent-class-intersection"),),
            10,
            "non-target",
        ),
        (
            (("C2860", "R103", "C999", "equivalent-class-intersection"),),
            10,
            "non-target",
        ),
        ((("C2860", "R103", "C12950", "unknown"),), 10, "unknown QLever"),
        (
            (("C2860", "R103", "C34228", "equivalent-class-intersection"),),
            10,
            "QLever/XML parity",
        ),
    ],
)
def test_source_builder_rejects_unbounded_duplicate_malformed_or_drifting_rows(
    tmp_path: Path,
    rows: tuple[tuple[str, str, str, str], ...],
    bound: int,
    message: str,
) -> None:
    owl, manifest = _source_files(tmp_path)

    with pytest.raises(R103ApplicationError, match=message):
        build_source_inventory(owl, manifest, qlever_rows=rows, row_bound=bound)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(ontology_version="wrong"), "source release"),
        (lambda value: value.pop("stated_artifact"), "lacks stated source"),
        (
            lambda value: value["stated_artifact"].update(sha256="0" * 64),
            "stated source identity mismatch",
        ),
        (lambda value: value.update(graph_layout=[]), "lacks stated source"),
    ],
)
def test_source_builder_rejects_unbound_manifests(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    owl, manifest = _source_files(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    mutation(payload)
    _rewrite_json(manifest, payload)

    with pytest.raises(R103ApplicationError, match=message):
        build_source_inventory(owl, manifest, qlever_rows=(), row_bound=10)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            '{"ontology_version":"26.07d","ontology_version":"26.07d"}',
            "duplicate JSON key",
        ),
        ("not-json", "invalid JSON evidence"),
    ],
)
def test_source_builder_rejects_noncanonical_manifest_json(
    tmp_path: Path, content: str, message: str
) -> None:
    owl, manifest = _source_files(tmp_path)
    manifest.write_text(content, encoding="utf-8")

    with pytest.raises(R103ApplicationError, match=message):
        build_source_inventory(owl, manifest, qlever_rows=(), row_bound=10)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "bound", "page_size", "message"),
    [
        ((("not-a-code",),), 10, None, "malformed"),
        ((("C100",), ("C100",)), 10, None, "duplicate QLever"),
        ((("C100",), ("C200",)), 1, None, "exceeds bound"),
        ((("C100",), ("C200",)), 10, 0, "exceeds bound"),
    ],
)
def test_candidate_builder_rejects_malformed_duplicate_or_unbounded_rows(
    tmp_path: Path,
    rows: tuple[tuple[str], ...],
    bound: int,
    page_size: int | None,
    message: str,
) -> None:
    owl, manifest = _source_files(tmp_path)

    with pytest.raises(R103ApplicationError, match=message):
        build_candidate_artifact(
            owl,
            manifest,
            qlever_rows=rows,
            row_bound=bound,
            page_size=page_size,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "shape differs"),
        ({}, "shape differs"),
        ({"citations": []}, "citation count"),
        ({"citations": [{"pmid": "wrong"}] * 5}, "validation error"),
    ],
)
def test_corroboration_normalization_rejects_malformed_historical_references(
    tmp_path: Path, payload: object, message: str
) -> None:
    authority = load_authority_artifact(
        GOLDEN / "r103-authority-normalized-26.07d.json"
    )
    historical = tmp_path / "historical.json"
    _rewrite_json(historical, payload)

    with pytest.raises(R103ApplicationError, match=message):
        build_corroboration_normalization(historical, authority)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda text: text + "<unclosed>", "invalid stated RDF/XML"),
        (
            lambda text: text.replace(
                "</rdf:RDF>",
                '<owl:Class rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C12950"/></rdf:RDF>',
            ),
            "duplicate source entity",
        ),
        (
            lambda text: text.replace(
                "<rdfs:label>Embryonic Tissue</rdfs:label>",
                "<rdfs:label>Embryonic Tissue</rdfs:label><rdfs:label>Embryonic Tissue</rdfs:label>",
            ),
            "duplicate source text value",
        ),
        (
            lambda text: text.replace(
                '<owl:someValuesFrom rdf:resource="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C34228"/>',
                "",
            ),
            "malformed source restriction",
        ),
        (
            lambda text: text.replace(
                '<rdf:Description rdf:about="http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1"/>',
                "<n:P97>Unsupported member</n:P97>",
            ),
            "unsupported equivalent intersection member",
        ),
    ],
)
def test_streaming_xml_scan_fails_closed_on_malformed_source_shapes(
    tmp_path: Path, mutation: Callable[[str], str], message: str
) -> None:
    owl, manifest = _source_files(tmp_path)
    owl.write_text(mutation(owl.read_text(encoding="utf-8")), encoding="utf-8")
    _manifest(manifest, owl)

    with pytest.raises(R103ApplicationError, match=message):
        build_source_inventory(owl, manifest, qlever_rows=(), row_bound=10)


@pytest.mark.unit
def test_application_and_corroboration_bindings_have_live_reject_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = load_source_inventory(GOLDEN / "r103-source-inventory-26.07d.json")
    authority = load_authority_artifact(
        GOLDEN / "r103-authority-normalized-26.07d.json"
    )
    arguments = {
        "proposal_registry_path": REGISTRY,
        "migration_path": MIGRATION,
        "oracle_path": ORACLE,
    }
    with pytest.raises(R103ApplicationError, match="source assertions are incomplete"):
        build_applied_policy_report(
            inventory=inventory.model_copy(update={"rows": inventory.rows[:2]}),
            authority=authority,
            **arguments,
        )
    with pytest.raises(R103ApplicationError, match="authority/source binding"):
        build_applied_policy_report(
            inventory=inventory,
            authority=authority.model_copy(
                update={"source_inventory_identity": "0" * 64}
            ),
            **arguments,
        )
    monkeypatch.setitem(
        application_module.UNSUPPORTED_FILLERS_BY_CONCEPT_ROLE,
        ("C3264", "R103"),
        frozenset(),
    )
    with pytest.raises(R103ApplicationError, match="policy differs from authority"):
        build_applied_policy_report(
            inventory=inventory,
            authority=authority,
            **arguments,
        )

    with pytest.raises(R103ApplicationError, match="corroboration authority binding"):
        load_corroboration_normalization(
            GOLDEN / "r103-corroboration-normalized-26.07d.json",
            authority.model_copy(update={"artifact_identity": "0" * 64}),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("artifact", "loader", "mutation", "message"),
    [
        (
            "r103-source-inventory-26.07d.json",
            load_source_inventory,
            lambda value: value.update(row_count=99),
            "counts differ",
        ),
        (
            "r103-source-inventory-26.07d.json",
            load_source_inventory,
            lambda value: value["rows"][0].update(member_position=99),
            "member/path mismatch",
        ),
        (
            "r103-source-inventory-26.07d.json",
            load_source_inventory,
            lambda value: value["rows"][0].update(subject_labels=["tampered"]),
            "source row identity differs",
        ),
        (
            "r103-source-inventory-26.07d.json",
            load_source_inventory,
            lambda value: value.update(artifact_identity="0" * 64),
            "source inventory identity differs",
        ),
        (
            "r103-c12950-candidates-26.07d.json",
            load_candidate_artifact,
            lambda value: value.update(candidate_count=99),
            "counts differ",
        ),
        (
            "r103-c12950-candidates-26.07d.json",
            load_candidate_artifact,
            lambda value: value["candidates"][0]["path"].__setitem__(0, "C999"),
            "path does not bind",
        ),
        (
            "r103-c12950-candidates-26.07d.json",
            load_candidate_artifact,
            lambda value: value["candidates"].reverse(),
            "duplicate or unordered",
        ),
        (
            "r103-c12950-candidates-26.07d.json",
            load_candidate_artifact,
            lambda value: value["candidates"][0].update(path_identity="0" * 64),
            "candidate path identity differs",
        ),
        (
            "r103-c12950-candidates-26.07d.json",
            load_candidate_artifact,
            lambda value: value["candidates"][0].update(labels=["tampered"]),
            "candidate row identity differs",
        ),
        (
            "r103-c12950-candidates-26.07d.json",
            load_candidate_artifact,
            lambda value: value.update(artifact_identity="0" * 64),
            "candidate artifact identity differs",
        ),
        (
            "r103-authority-normalized-26.07d.json",
            load_authority_artifact,
            lambda value: value["entries"][0].update(
                transcription_authority="explicit-human-instruction"
            ),
            "discriminator",
        ),
        (
            "r103-authority-normalized-26.07d.json",
            load_authority_artifact,
            lambda value: value["entries"].reverse(),
            "authority inventory differs",
        ),
        (
            "r103-authority-normalized-26.07d.json",
            load_authority_artifact,
            lambda value: value["entries"][0].update(
                superseded_predecessor_decision_identity="0" * 64
            ),
            "discriminator",
        ),
        (
            "r103-authority-normalized-26.07d.json",
            load_authority_artifact,
            lambda value: value.update(artifact_identity="0" * 64),
            "authority artifact identity differs",
        ),
        (
            "r103-corroboration-normalized-26.07d.json",
            lambda path: load_corroboration_normalization(
                path,
                load_authority_artifact(
                    GOLDEN / "r103-authority-normalized-26.07d.json"
                ),
            ),
            lambda value: value["references"][1].update(
                pmid=value["references"][0]["pmid"]
            ),
            "duplicate corroboration PMID",
        ),
        (
            "r103-corroboration-normalized-26.07d.json",
            lambda path: load_corroboration_normalization(
                path,
                load_authority_artifact(
                    GOLDEN / "r103-authority-normalized-26.07d.json"
                ),
            ),
            lambda value: value.update(artifact_identity="0" * 64),
            "corroboration normalization identity differs",
        ),
        (
            "r103-applied-policy-26.07d.json",
            load_applied_policy_report,
            lambda value: value.update(oracle_file_sha256_after="0" * 64),
            "oracle changed",
        ),
        (
            "r103-applied-policy-26.07d.json",
            load_applied_policy_report,
            lambda value: value.update(artifact_identity="0" * 64),
            "applied-policy report identity differs",
        ),
    ],
)
def test_artifact_loaders_reject_semantic_tampering_before_identity_checks(
    tmp_path: Path,
    artifact: str,
    loader: Callable[[Path], object],
    mutation: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    payload = json.loads((GOLDEN / artifact).read_text(encoding="utf-8"))
    mutation(payload)
    changed = tmp_path / artifact
    _rewrite_json(changed, payload)

    with pytest.raises(R103ApplicationError, match=message):
        loader(changed)


@pytest.mark.unit
def test_authority_corroboration_and_application_are_source_bound_and_fail_closed(
    tmp_path: Path,
) -> None:
    inventory = load_source_inventory(GOLDEN / "r103-source-inventory-26.07d.json")
    authority = build_authority_artifact(REV1, REV2, MIGRATION, inventory)
    assert [entry.authority_kind for entry in authority.entries] == [
        "carried-forward-predecessor-decision",
        "new-human-decision",
        "carried-forward-predecessor-decision",
    ]
    assert authority.entries[1].transcription_authority == "explicit-human-instruction"
    assert authority.entries[1].software_authorship is False
    authority_path = tmp_path / "authority.json"
    write_artifact(authority_path, authority)
    assert load_authority_artifact(authority_path) == authority

    corroboration = build_corroboration_normalization(
        HISTORICAL_CORROBORATION, authority
    )
    assert corroboration.upstream_verified is False
    assert corroboration.evidence_kind == "reviewer-supplied-references"
    assert not hasattr(corroboration, "verified_date")
    corroboration_path = tmp_path / "corroboration.json"
    write_artifact(corroboration_path, corroboration)
    assert (
        load_corroboration_normalization(corroboration_path, authority) == corroboration
    )

    report = build_applied_policy_report(
        inventory=inventory,
        authority=authority,
        proposal_registry_path=REGISTRY,
        migration_path=MIGRATION,
        oracle_path=ORACLE,
    )
    assert report.authorization is False
    assert report.publication_attempted is False
    assert report.nci_adoption_inferred is False
    assert report.c3264_source_retrievable is True
    assert report.c3264_effective_projected is False
    assert report.c2860_effective_projected is True
    assert report.c3716_effective_projected is True
    assert report.exclusion_propagated is False
    report_path = tmp_path / "application.json"
    write_artifact(report_path, report)
    assert load_applied_policy_report(report_path) == report

    tampered = json.loads(authority_path.read_text())
    tampered["entries"][0]["predecessor_decision_identity"] = "0" * 64
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(R103ApplicationError):
        load_authority_artifact(tampered_path)
