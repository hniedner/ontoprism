"""Exercise the issue #283 split QLever/Postgres curation topology."""

# The schema identifier is a module constant, never input; assertions deliberately
# make this executable research contract fail closed.
# ruff: noqa: PLR2004, S101, S608

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
from typing import Any

import asyncpg
import rdflib

from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.sparql_http_client import (
    SparqlEndpointProfile,
    SparqlHttpClient,
)

DSN = os.environ.get(
    "ISSUE283_POSTGRES_DSN",
    "postgresql://ontoprism:ontoprism@127.0.0.1:5433/ontoprism",
)
QLEVER_URL = os.environ.get("ISSUE283_QLEVER_URL", "http://127.0.0.1:7302")
SCHEMA = "issue283_curation_bakeoff"
PROPOSAL_ID = "MINT-issue283-oncology-concept"
APPROVAL_OPERATION = "issue283-local-approval"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"


def canonical_identity(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def proposal_payload(*, note: str) -> dict[str, Any]:
    return {
        "kind": "concept",
        "preferred_name": "Research-Evidenced Oncology Concept",
        "definition": "Disposable issue #283 curation topology contract.",
        "parent_concepts": ["C3262"],
        "role_assertions": [
            {
                "filler": "C12922",
                "property": "R105",
            }
        ],
        "semantic_types": ["Neoplastic Process"],
        "revision_note": note,
    }


def projection_rdf(source_identity: str) -> str:
    return f"""@prefix ncit: <{NCIT_NS}> .
@prefix op: <https://w3id.org/ontoprism/vocab#> .
@prefix owl: <{OWL}> .
@prefix rdfs: <{RDFS}> .
@prefix prov: <http://www.w3.org/ns/prov#> .

op:{PROPOSAL_ID} a owl:Class ;
  rdfs:label "Research-Evidenced Oncology Concept" ;
  rdfs:subClassOf ncit:C3262,
    [ a owl:Restriction ; owl:onProperty ncit:R105 ;
      owl:someValuesFrom ncit:C12922 ] ;
  op:proposalStatus "locally-approved" ;
  op:sourceIdentity "{source_identity}" ;
  prov:wasDerivedFrom <https://pubmed.ncbi.nlm.nih.gov/12345678/>,
    <https://clinicaltrials.gov/study/NCT01234567> .
"""


async def base_source_contract() -> tuple[str, str]:
    profile = SparqlEndpointProfile.for_qlever(
        QLEVER_URL,
        named_graphs=(STATED_GRAPH_IRI,),
    )
    async with SparqlHttpClient(profile, query_timeout=10.0) as client:
        versions = await client.select_once(
            f"PREFIX owl: <{OWL}> SELECT ?version WHERE {{ "
            "?ontology a owl:Ontology ; owl:versionInfo ?version } LIMIT 2",
            required_variables={"version"},
        )
        default_counts = await client.select_once(
            "SELECT (COUNT(*) AS ?count) WHERE { ?subject ?predicate ?object }",
            required_variables={"count"},
        )
        stated_counts = await client.select_once(
            "SELECT (COUNT(*) AS ?count) WHERE { "
            f"GRAPH <{STATED_GRAPH_IRI}> {{ ?subject ?predicate ?object }} }}",
            required_variables={"count"},
        )
        parent = await client.select_once(
            f"PREFIX rdfs: <{RDFS}> SELECT ?label WHERE {{ "
            f"<{NCIT_NS}C3262> rdfs:label ?label }} LIMIT 2",
            required_variables={"label"},
        )
    source = {
        "default_count": int(default_counts[0]["count"] or "-1"),
        "engine": "qlever-65f84b4",
        "stated_count": int(stated_counts[0]["count"] or "-1"),
        "stated_graph": STATED_GRAPH_IRI,
        "version": versions[0]["version"],
    }
    return canonical_identity(source), str(parent[0]["label"])


async def create_schema(connection: asyncpg.Connection) -> None:
    await connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    await connection.execute(f"CREATE SCHEMA {SCHEMA}")
    await connection.execute(
        f"""
        CREATE TABLE {SCHEMA}.proposal (
            proposal_id text PRIMARY KEY,
            kind text NOT NULL CHECK (kind IN ('concept', 'relation')),
            source_identity text NOT NULL CHECK (
                source_identity ~ '^[0-9a-f]{{64}}$'
            ),
            lifecycle text NOT NULL CHECK (lifecycle IN (
                'proposed', 'locally-approved', 'submitted', 'accepted-in-ncit'
            )),
            revision integer NOT NULL CHECK (revision > 0),
            payload jsonb NOT NULL,
            projection_rdf text,
            projection_revision integer,
            CHECK (
                (projection_rdf IS NULL AND projection_revision IS NULL)
                OR projection_revision = revision
            )
        );
        CREATE TABLE {SCHEMA}.proposal_revision (
            proposal_id text NOT NULL,
            revision integer NOT NULL,
            lifecycle text NOT NULL,
            actor text NOT NULL,
            operation_id text NOT NULL UNIQUE,
            payload jsonb NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (proposal_id, revision),
            FOREIGN KEY (proposal_id) REFERENCES {SCHEMA}.proposal(proposal_id)
        );
        CREATE TABLE {SCHEMA}.proposal_evidence (
            proposal_id text NOT NULL,
            revision integer NOT NULL,
            evidence_id text NOT NULL,
            evidence_kind text NOT NULL CHECK (
                evidence_kind IN ('publication', 'clinical-trial')
            ),
            source_identifier text NOT NULL,
            source_url text NOT NULL,
            provenance jsonb NOT NULL,
            PRIMARY KEY (proposal_id, evidence_id),
            FOREIGN KEY (proposal_id, revision)
                REFERENCES {SCHEMA}.proposal_revision(proposal_id, revision)
        );
        CREATE FUNCTION {SCHEMA}.enforce_proposal_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.source_identity IS DISTINCT FROM OLD.source_identity
               OR NEW.kind IS DISTINCT FROM OLD.kind THEN
                RAISE EXCEPTION 'proposal source identity and kind are immutable';
            END IF;
            IF NEW.revision <> OLD.revision + 1 THEN
                RAISE EXCEPTION 'proposal revision must advance exactly once';
            END IF;
            IF NOT (
                (OLD.lifecycle = 'proposed' AND NEW.lifecycle IN (
                    'proposed', 'locally-approved'
                ))
                OR (OLD.lifecycle = 'locally-approved'
                    AND NEW.lifecycle = 'submitted')
                OR (OLD.lifecycle = 'submitted'
                    AND NEW.lifecycle = 'accepted-in-ncit')
            ) THEN
                RAISE EXCEPTION 'invalid proposal lifecycle transition';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER enforce_proposal_revision
            BEFORE UPDATE ON {SCHEMA}.proposal
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.enforce_proposal_revision();
        """
    )


async def create_proposal(connection: asyncpg.Connection, source_identity: str) -> None:
    payload = proposal_payload(note="initial proposal")
    async with connection.transaction():
        await connection.execute(
            f"INSERT INTO {SCHEMA}.proposal "
            "(proposal_id, kind, source_identity, lifecycle, revision, payload) "
            "VALUES ($1, 'concept', $2, 'proposed', 1, $3::jsonb)",
            PROPOSAL_ID,
            source_identity,
            json.dumps(payload),
        )
        await connection.execute(
            f"INSERT INTO {SCHEMA}.proposal_revision "
            "(proposal_id, revision, lifecycle, actor, operation_id, payload) "
            "VALUES ($1, 1, 'proposed', 'curator-a', 'issue283-create', $2::jsonb)",
            PROPOSAL_ID,
            json.dumps(payload),
        )
        await connection.executemany(
            f"INSERT INTO {SCHEMA}.proposal_evidence "
            "(proposal_id, revision, evidence_id, evidence_kind, "
            "source_identifier, source_url, provenance) "
            "VALUES ($1, 1, $2, $3, $4, $5, $6::jsonb)",
            [
                (
                    PROPOSAL_ID,
                    "pubmed-12345678",
                    "publication",
                    "PMID:12345678",
                    "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                    json.dumps({"assertion": "supports proposed class"}),
                ),
                (
                    PROPOSAL_ID,
                    "trial-nct01234567",
                    "clinical-trial",
                    "NCT01234567",
                    "https://clinicaltrials.gov/study/NCT01234567",
                    json.dumps({"assertion": "corroborates domain use"}),
                ),
            ],
        )


async def competing_revision(actor: str) -> bool:
    connection = await asyncpg.connect(DSN)
    payload = proposal_payload(note=f"revision from {actor}")
    try:
        async with connection.transaction():
            updated = await connection.fetchrow(
                f"UPDATE {SCHEMA}.proposal SET revision = 2, payload = $1::jsonb "
                "WHERE proposal_id = $2 AND revision = 1 RETURNING revision",
                json.dumps(payload),
                PROPOSAL_ID,
            )
            if updated is None:
                return False
            await connection.execute(
                f"INSERT INTO {SCHEMA}.proposal_revision "
                "(proposal_id, revision, lifecycle, actor, operation_id, payload) "
                "VALUES ($1, 2, 'proposed', $2, $3, $4::jsonb)",
                PROPOSAL_ID,
                actor,
                f"issue283-revise-{actor}",
                json.dumps(payload),
            )
        return True
    finally:
        await connection.close()


async def approve_with_lost_ack(
    connection: asyncpg.Connection, source_identity: str
) -> None:
    payload = proposal_payload(note="locally approved by SME")
    rdf = projection_rdf(source_identity)
    async with connection.transaction():
        updated = await connection.fetchrow(
            f"UPDATE {SCHEMA}.proposal SET revision = 3, "
            "lifecycle = 'locally-approved', payload = $1::jsonb, "
            "projection_rdf = $2, projection_revision = 3 "
            "WHERE proposal_id = $3 AND revision = 2 RETURNING revision",
            json.dumps(payload),
            rdf,
            PROPOSAL_ID,
        )
        if updated is None:
            raise AssertionError("approval optimistic revision unexpectedly lost")
        await connection.execute(
            f"INSERT INTO {SCHEMA}.proposal_revision "
            "(proposal_id, revision, lifecycle, actor, operation_id, payload) "
            "VALUES ($1, 3, 'locally-approved', 'sme-a', $2, $3::jsonb)",
            PROPOSAL_ID,
            APPROVAL_OPERATION,
            json.dumps(payload),
        )
    raise ConnectionError("simulated response loss after committed approval")


async def read_contract(
    connection: asyncpg.Connection,
    *,
    source_identity: str,
    parent_label: str,
) -> dict[str, Any]:
    listing = await connection.fetch(
        f"SELECT proposal_id, lifecycle, revision FROM {SCHEMA}.proposal "
        "ORDER BY proposal_id"
    )
    detail = await connection.fetchrow(
        f"SELECT proposal_id, source_identity, lifecycle, revision, payload, "
        f"projection_rdf, projection_revision FROM {SCHEMA}.proposal "
        "WHERE proposal_id = $1",
        PROPOSAL_ID,
    )
    history = await connection.fetch(
        f"SELECT revision, lifecycle, actor, operation_id "
        f"FROM {SCHEMA}.proposal_revision WHERE proposal_id = $1 "
        "ORDER BY revision",
        PROPOSAL_ID,
    )
    evidence = await connection.fetch(
        f"SELECT evidence_kind, source_identifier FROM {SCHEMA}.proposal_evidence "
        "WHERE proposal_id = $1 ORDER BY evidence_kind",
        PROPOSAL_ID,
    )
    if detail is None:
        raise AssertionError("proposal detail disappeared")
    rdf_payload = str(detail["projection_rdf"])
    graph = rdflib.Graph().parse(data=rdf_payload, format="turtle")
    proposal_subject = f"https://w3id.org/ontoprism/vocab#{PROPOSAL_ID}"
    composed_graph = {
        "edges": [
            {
                "source": proposal_subject,
                "target": f"{NCIT_NS}C3262",
                "type": f"{RDFS}subClassOf",
            }
        ],
        "nodes": [
            {"id": f"{NCIT_NS}C3262", "label": parent_label, "plane": "base"},
            {
                "id": proposal_subject,
                "label": "Research-Evidenced Oncology Concept",
                "plane": "proposal-overlay",
            },
        ],
    }
    overlay = {
        "projection_identity": canonical_identity(rdf_payload),
        "proposal_id": PROPOSAL_ID,
        "revision": detail["revision"],
    }
    combined_identity = canonical_identity(
        {"base_source_identity": source_identity, "overlay": overlay}
    )
    assert detail["source_identity"] == source_identity
    assert detail["lifecycle"] == "locally-approved"
    assert detail["revision"] == detail["projection_revision"] == 3
    assert [(row["revision"], row["lifecycle"]) for row in history] == [
        (1, "proposed"),
        (2, "proposed"),
        (3, "locally-approved"),
    ]
    assert {row["evidence_kind"] for row in evidence} == {
        "publication",
        "clinical-trial",
    }
    assert len(graph) == 11
    return {
        "combined_identity": combined_identity,
        "composed_edges": len(composed_graph["edges"]),
        "composed_nodes": len(composed_graph["nodes"]),
        "evidence": [dict(row) for row in evidence],
        "history": [dict(row) for row in history],
        "list": [dict(row) for row in listing],
        "projection_triples": len(graph),
        "revision": detail["revision"],
    }


async def initialize() -> None:
    source_identity, parent_label = await base_source_contract()
    connection = await asyncpg.connect(DSN)
    try:
        await create_schema(connection)
        await create_proposal(connection, source_identity)
    finally:
        await connection.close()
    winners = await asyncio.gather(
        competing_revision("writer-a"), competing_revision("writer-b")
    )
    assert sum(winners) == 1
    connection = await asyncpg.connect(DSN)
    try:
        with contextlib.suppress(ConnectionError):
            await approve_with_lost_ack(connection, source_identity)
    finally:
        await connection.close()
    connection = await asyncpg.connect(DSN)
    try:
        reconciled = await connection.fetchval(
            f"SELECT revision FROM {SCHEMA}.proposal_revision WHERE operation_id = $1",
            APPROVAL_OPERATION,
        )
        assert reconciled == 3
        result = await read_contract(
            connection,
            source_identity=source_identity,
            parent_label=parent_label,
        )
    finally:
        await connection.close()
    print(
        json.dumps(
            {
                "ambiguous_result_reconciled": reconciled,
                "conflict_winners": winners,
                "mode": "initialize",
                "source_identity": source_identity,
                **result,
            },
            sort_keys=True,
        )
    )


async def verify_only() -> None:
    source_identity, parent_label = await base_source_contract()
    connection = await asyncpg.connect(DSN)
    try:
        result = await read_contract(
            connection,
            source_identity=source_identity,
            parent_label=parent_label,
        )
    finally:
        await connection.close()
    print(
        json.dumps(
            {
                "mode": "verify-only",
                "source_identity": source_identity,
                **result,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(verify_only() if "verify-only" in sys.argv else initialize())
