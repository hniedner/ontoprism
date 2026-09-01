"""Operator-readiness contracts for the isolated enhanced-NCIt showcase."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest
from rdflib import Graph

from ontolib.decomposition.enhanced_showcase import (
    SHOWCASE_GRAPH_IRI,
    ShowcasePolicyError,
    load_packaged_showcase_decision_set,
    serialize_showcase_decision_graph,
)
from ontolib.decomposition.showcase_readiness import (
    activate_showcase_readiness,
    verify_showcase_readiness,
)

if TYPE_CHECKING:
    from pathlib import Path


class _StoredShowcaseClient:
    def __init__(
        self,
        *,
        corrupt_code: str | None = None,
        source_versions: list[dict[str, str]] | None = None,
        foreign_triple: bool = False,
    ) -> None:
        policy = load_packaged_showcase_decision_set()
        self.rows = {
            concept.code: [
                {
                    "payload": json.dumps(
                        decision.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                }
                for decision in concept.decisions
            ]
            for concept in policy.concepts
        }
        if corrupt_code is not None:
            self.rows[corrupt_code] = self.rows[corrupt_code][:-1]
        self.select_calls: list[str] = []
        self.loads: list[tuple[bytes, str, str | None, bool]] = []
        self.updates: list[str] = []
        self.source_versions = (
            [{"version": "26.07d"}] if source_versions is None else source_versions
        )
        graph = Graph().parse(
            data=serialize_showcase_decision_graph(policy), format="turtle"
        )
        self.graph_rows = [
            {"s": str(subject), "p": str(predicate), "o": str(value)}
            for subject, predicate, value in graph
        ]
        if corrupt_code is not None:
            corrupt_subject = next(
                row["s"]
                for row in self.graph_rows
                if f"/decision/{corrupt_code}-" in row["s"]
            )
            self.graph_rows = [
                row for row in self.graph_rows if row["s"] != corrupt_subject
            ]
        if foreign_triple:
            self.graph_rows.append(
                {"s": "urn:foreign", "p": "urn:predicate", "o": "urn:value"}
            )

    async def select(self, query: str) -> list[dict[str, str]]:
        self.select_calls.append(query)
        if "owl:versionInfo" in query:
            return self.source_versions
        if "SELECT ?s ?p ?o" in query:
            return self.graph_rows
        code = next(code for code in self.rows if code in query)
        return self.rows[code]

    async def load(
        self,
        data: bytes,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None:
        self.loads.append((data, content_type, graph_iri, replace))

    async def update(self, update: str) -> None:
        self.updates.append(update)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verification_reads_exact_graph_once_and_writes_bound_report(
    tmp_path: Path,
) -> None:
    client = _StoredShowcaseClient()
    output = tmp_path / "tmp/m1-6-enhanced-showcase-readiness.json"
    report = await verify_showcase_readiness(
        client,
        output=output,
        git_head="a" * 40,
        producing_command="pdm run agent-replay verify-enhanced-ncit-showcase",
    )

    policy = load_packaged_showcase_decision_set()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == report.model_dump(mode="json")
    closure_queries = [
        query for query in client.select_calls if "SELECT ?s ?p ?o" in query
    ]
    assert len(closure_queries) == 1
    assert "LIMIT 396" in closure_queries[0]
    assert sum("owl:versionInfo" in query for query in client.select_calls) == 1
    assert client.loads == []
    assert client.updates == []
    assert payload["showcase_complete"] is True
    assert payload["local_graph_activated"] is True
    assert payload["api_ready"] is True
    assert payload["production_ready"] is False
    assert payload["scientific_publication_ready"] is False
    assert payload["equivalence_established"] is False
    assert payload["nci_adoption_asserted"] is False
    assert (
        payload["decision_set_identity"]
        == policy.decision_set_identity
        == ("53753b3130864138949cc5d9856d42c7018afd9083295bbfeef5528d00af8c67")
    )
    assert payload["representation"] == "enhanced-ncit-showcase"
    assert payload["graph_iri"] == SHOWCASE_GRAPH_IRI
    assert payload["source_release"] == "26.07d"
    assert payload["concept_count"] == 7
    assert payload["candidate_count"] == 79
    assert payload["concept_candidate_counts"] == {
        "C100054": 7,
        "C102870": 6,
        "C198031": 9,
        "C27262": 8,
        "C35756": 22,
        "C4791": 12,
        "C6135": 15,
    }
    assert payload["disposition_counts"] == {
        "exclude": 14,
        "include": 41,
        "unresolved-visible": 24,
    }
    assert payload["authority_counts"] == {
        "locally-approved": 2,
        "project-provisional": 45,
        "source-stated": 32,
    }
    assert payload["support_counts"] == {
        "peer-reviewed-not-found": 7,
        "peer-reviewed-supported": 40,
        "project-inference": 23,
        "source-stated": 70,
    }
    assert payload["collapse_policy_overlaps"] == {
        "concept_roots": 0,
        "runtime_keys": 0,
        "source_occurrences": 0,
    }
    identity_payload = {
        key: value for key, value in payload.items() if key != "report_identity"
    }
    assert (
        payload["report_identity"]
        == hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode(
                "ascii"
            )
        ).hexdigest()
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_storage_fails_closed_without_a_report(tmp_path: Path) -> None:
    output = tmp_path / "tmp/m1-6-enhanced-showcase-readiness.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"stale":true}\n', encoding="utf-8")

    with pytest.raises(ShowcasePolicyError, match="exact packaged graph"):
        await verify_showcase_readiness(
            _StoredShowcaseClient(corrupt_code="C35756"),
            output=output,
            git_head="a" * 40,
            producing_command="pdm run agent-replay verify-enhanced-ncit-showcase",
        )

    assert not output.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_foreign_graph_content_fails_closed_without_a_report(
    tmp_path: Path,
) -> None:
    output = tmp_path / "tmp/m1-6-enhanced-showcase-readiness.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"stale":true}\n', encoding="utf-8")

    with pytest.raises(
        ShowcasePolicyError, match=r"closure budgets|exact packaged graph"
    ):
        await verify_showcase_readiness(
            _StoredShowcaseClient(foreign_triple=True),
            output=output,
            git_head="a" * 40,
            producing_command="pdm run agent-replay verify-enhanced-ncit-showcase",
        )

    assert not output.exists()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["duplicate", "malformed", "stale"])
async def test_invalid_storage_rows_fail_closed_without_a_report(
    tmp_path: Path, corruption: str
) -> None:
    client = _StoredShowcaseClient()
    rows = client.graph_rows
    if corruption == "duplicate":
        rows.append(rows[0].copy())
    elif corruption == "malformed":
        rows[0] = {"unexpected": "not-a-triple"}
    else:
        rows[0] = {**rows[0], "o": "stale stored value"}
    output = tmp_path / "tmp/m1-6-enhanced-showcase-readiness.json"

    with pytest.raises(ShowcasePolicyError, match="stored showcase"):
        await verify_showcase_readiness(
            client,
            output=output,
            git_head="a" * 40,
            producing_command="pdm run agent-replay verify-enhanced-ncit-showcase",
        )

    assert not output.exists()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "versions",
    [[], [{"version": "26.08a"}], [{"version": "26.07d"}, {"version": "26.07d"}]],
)
async def test_missing_duplicate_or_wrong_source_release_fails_closed(
    tmp_path: Path, versions: list[dict[str, str]]
) -> None:
    output = tmp_path / "tmp/m1-6-enhanced-showcase-readiness.json"

    with pytest.raises(ShowcasePolicyError, match="source release"):
        await verify_showcase_readiness(
            _StoredShowcaseClient(source_versions=versions),
            output=output,
            git_head="a" * 40,
            producing_command="pdm run agent-replay verify-enhanced-ncit-showcase",
        )

    assert not output.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activation_uses_one_scoped_load_and_update_then_complete_readback(
    tmp_path: Path,
) -> None:
    client = _StoredShowcaseClient()
    output = tmp_path / "tmp/m1-6-enhanced-showcase-readiness.json"
    await activate_showcase_readiness(
        client,
        output=output,
        git_head="b" * 40,
        producing_command="pdm run agent-replay activate-enhanced-ncit-showcase",
    )

    assert len(client.loads) == 1
    assert client.loads[0][0].decode("utf-8") == serialize_showcase_decision_graph(
        load_packaged_showcase_decision_set()
    )
    assert client.loads[0][1] == "text/turtle"
    assert client.loads[0][2] is not None
    assert client.loads[0][2].startswith(f"{SHOWCASE_GRAPH_IRI}/staging/")
    assert client.loads[0][3] is True
    assert len(client.updates) == 1
    assert (
        "CLEAR GRAPH "
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-decomposed.owl>;"
        not in client.updates[0]
    )
    assert "Thesaurus-stated.owl" not in client.updates[0]
    assert (
        len([query for query in client.select_calls if "SELECT ?s ?p ?o" in query]) == 1
    )
    assert sum("owl:versionInfo" in query for query in client.select_calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_activation_cleans_only_its_owned_staging_graph(
    tmp_path: Path,
) -> None:
    class _FailingUpdateClient(_StoredShowcaseClient):
        async def update(self, update: str) -> None:
            self.updates.append(update)
            if len(self.updates) == 1:
                raise ConnectionError("replacement failed")

    client = _FailingUpdateClient()
    output = tmp_path / "tmp/m1-6-enhanced-showcase-readiness.json"

    with pytest.raises(ConnectionError, match="replacement failed"):
        await activate_showcase_readiness(
            client,
            output=output,
            git_head="b" * 40,
            producing_command="pdm run agent-replay activate-enhanced-ncit-showcase",
        )

    assert len(client.updates) == 2
    assert client.updates[1].startswith(
        f"DROP SILENT GRAPH <{SHOWCASE_GRAPH_IRI}/staging/"
    )
    assert SHOWCASE_GRAPH_IRI + ">" not in client.updates[1]
    assert not output.exists()
