"""Persist occurrence-level routing and collapse dispositions.

Revision ID: 0023_occurrence_dispositions
Revises: 0022_constituent_source_roles
Create Date: 2026-09-07
"""

from alembic import op

revision: str = "0023_occurrence_dispositions"
down_revision: str | None = "0022_constituent_source_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE decomp_occurrence_disposition (
            run_id text NOT NULL,
            concept_code text NOT NULL,
            occurrence_id text NOT NULL,
            source_fact_id text NOT NULL,
            disposition text NOT NULL CHECK (disposition IN (
                'retained-routed', 'retained-unknown', 'collapsed-is-a',
                'collapsed-r82', 'retained-policy-veto'
            )),
            normalized_axis text NOT NULL CHECK (
                normalized_axis ~ '^(op:[A-Za-z][A-Za-z0-9]*|R[0-9]+)$'
            ),
            source_filler text NOT NULL CHECK (source_filler ~ '^C[0-9]+$'),
            retained_filler text NOT NULL CHECK (retained_filler ~ '^C[0-9]+$'),
            semantic_route text NOT NULL,
            semantic_type text,
            r82_part text,
            r82_whole text,
            policy_decision_identity text CHECK (
                policy_decision_identity IS NULL OR
                policy_decision_identity ~ '^[0-9a-f]{64}$'
            ),
            PRIMARY KEY (run_id, concept_code, occurrence_id),
            FOREIGN KEY (run_id, concept_code, occurrence_id)
                REFERENCES decomp_source_occurrence(run_id, concept_code, occurrence_id)
                ON DELETE CASCADE,
            FOREIGN KEY (run_id, concept_code, source_fact_id)
                REFERENCES decomp_definition_fact(run_id, concept_code, fact_id)
                ON DELETE CASCADE,
            CHECK ((disposition = 'collapsed-r82') =
                (r82_part IS NOT NULL AND r82_whole IS NOT NULL)),
            CHECK ((disposition = 'retained-policy-veto') =
                (policy_decision_identity IS NOT NULL))
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decomp_occurrence_disposition")
