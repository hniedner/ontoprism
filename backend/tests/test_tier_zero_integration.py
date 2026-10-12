"""Tier-zero import and filler-domain contracts at real Postgres boundaries."""

import json
from datetime import UTC, datetime
from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.evidence_ledger import EvidenceLedger
from ontolib.decomposition.evidence_models import LedgerAssessment
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    GenusDefinitionFact,
    canonical_definition_fact_id,
    canonical_definition_group_id,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    RunFingerprint,
)
from ontolib.decomposition.tier_zero import import_tier_zero

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


async def test_minted_fillers_roundtrip_and_block_downgrade():
    engine = make_engine(get_settings().database_url)
    try:
        assessment = LedgerAssessment.model_validate_json(
            json.dumps(
                {
                    "concept_code": "C474",
                    "axis": "op:StageSystem",
                    "filler_code": "MINT-8512b41a844d",
                    "source_release": "26.07d",
                    "scope_root": "C3262",
                    "status": "provisional",
                    "support": {
                        "axis_source": "nlp",
                        "normalized_group": {
                            "label": "Staging",
                            "members": [
                                {
                                    "axis": "op:StageSystem",
                                    "filler_code": "MINT-8512b41a844d",
                                }
                            ],
                        },
                    },
                }
            )
        )
        ledger = EvidenceLedger(make_sessionmaker(engine))
        await ledger.assess(assessment)
        stored = (await ledger.for_concept("C474"))[0]
        assert stored.filler_code == "MINT-8512b41a844d"
        assert (
            stored.support.normalized_group.members[0].filler_code == stored.filler_code
        )
        assert stored.status == "provisional"
        migration = import_module("migrations.versions.0036_ledger_mint_fillers")

        def downgrade(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()

        with pytest.raises(DBAPIError, match="MINT assertion fillers"):
            async with engine.begin() as connection:
                await connection.run_sync(downgrade)
        assert (await ledger.for_concept("C474"))[0] == stored
    finally:
        await dispose_engine(engine)


async def test_tier_zero_statuses_and_repeated_import():
    engine = make_engine(get_settings().database_url)
    try:
        sessions = make_sessionmaker(engine)
        provenance = ProvenanceStore(sessions)
        fingerprint = RunFingerprint(
            source_identity="a" * 64,
            collapse_policy_identity="0" * 64,
            routing_implementation_identity="1" * 64,
            stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
            branch="neoplasm",
            scope_root="C3262",
            scope_version="stated-genus-subclass-v1",
            semantic_types=("Neoplastic Process",),
            worklist=("C474001",),
            total_limit=None,
            algorithm_version="decomposition-v1",
            config_version="axes-v1",
            walker_max_depth=5,
            output_mode="none",
            load_mode="none",
            emitted_at=datetime(2026, 10, 12, tzinfo=UTC),
        )
        await provenance.create_run("tier-zero-fixture", "26.07d", fingerprint)
        claim = await provenance.claim_work_item("tier-zero-fixture", "C474001")
        assert claim is not None
        group = canonical_definition_group_id("C474001", ["genus:C123:defined"])
        fact = canonical_definition_fact_id(
            "C474001", group, "genus", "C123", "defined"
        )
        await provenance.complete_work_item(
            "tier-zero-fixture",
            "C474001",
            claim,
            decomposition=Decomposition(
                code="C474001",
                semantic_type="Neoplastic Process",
                constituents=(
                    Constituent(
                        axis="R1",
                        filler_code="C123",
                        axis_source="parent",
                        source_definition_ids=(fact,),
                    ),
                    Constituent(
                        axis="op:Morphology",
                        filler_code="C123",
                        axis_source="parent",
                        source_definition_ids=(fact,),
                    ),
                    Constituent(
                        axis="op:StageSystem",
                        filler_code="MINT-8512b41a844d",
                        axis_source="nlp",
                    ),
                ),
                complete_definition=CompleteDefinition(
                    root_code="C474001",
                    facts=(
                        GenusDefinitionFact(
                            fact_id=fact,
                            group_id=group,
                            anchor_code="C474001",
                            depth=0,
                            genus_code="C123",
                            is_defined=True,
                        ),
                    ),
                ),
            ),
            semantic_types=("Neoplastic Process",),
            minted=(),
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE decomp_run SET status='complete',finished_at=now() "
                    "WHERE id='tier-zero-fixture'"
                )
            )
            await connection.execute(
                text(
                    "UPDATE decomp_constituent SET normalized_group_id=:group, "
                    "normalized_group_label='Staging' WHERE run_id='tier-zero-fixture' "
                    "AND axis='op:StageSystem'"
                ),
                {"group": "3" * 64},
            )
        ledger = EvidenceLedger(sessions)
        preview = await import_tier_zero(sessions, "tier-zero-fixture", dry_run=True)
        assert await ledger.for_concept("C474001") == []
        assert preview["support_kinds"] == {"genus-backed": 2, "not-source-backed": 1}
        assert (
            preview["assessment_basis"]
            == "input-only; existing human history not assessed"
        )
        report = await import_tier_zero(sessions, "tier-zero-fixture")
        entries = await ledger.for_concept("C474001")
        by_axis = {entry.axis: entry for entry in entries}
        assert {axis: entry.status for axis, entry in by_axis.items()} == {
            "R1": "evidence-supported",
            "op:Morphology": "provisional",
            "op:StageSystem": "provisional",
        }
        assert (
            by_axis["R1"].evidence[0].statement == "Stated genus at C474001: genus C123"
        )
        assert [
            choice.kind for choice in by_axis["op:Morphology"].support.policy_choices
        ] == ["axis-assignment"]
        assert {
            choice.kind for choice in by_axis["op:StageSystem"].support.policy_choices
        } == {"axis-assignment", "nlp-derivation", "grouping"}
        assert by_axis["op:StageSystem"].support.normalized_group.label == "Staging"
        assert by_axis["op:StageSystem"].evidence == ()
        assert report["mint_provisional"] == 1
        assert report["assessment_basis"] == (
            "stored assessment including human-decision applicability"
        )
        await import_tier_zero(sessions, "tier-zero-fixture")
        assert await ledger.for_concept("C474001") == entries
        with pytest.raises(ValueError, match="completed stored run"):
            await import_tier_zero(sessions, "absent-run")
    finally:
        await dispose_engine(engine)
