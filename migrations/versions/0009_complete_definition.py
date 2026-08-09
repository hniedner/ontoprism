"""Persist the complete stated decomposition definition (#153).

Revision ID: 0009_complete_definition
Revises: 0008_decomposition_run_lifecycle
Create Date: 2026-07-30
"""

from alembic import op

revision: str = "0009_complete_definition"
down_revision: str | None = "0008_decomposition_run_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_constituent
            ADD COLUMN source_definition_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD CONSTRAINT ck_decomp_constituent_source_definition_ids
                CHECK (
                    jsonb_typeof(source_definition_ids) = 'array'
                    AND NOT (
                        source_definition_ids @?
                            '$[*] ? (!(@.type() == "string"))'
                    )
                    AND NOT (
                        source_definition_ids @?
                            '$[*] ? (!(@ like_regex "^[0-9a-f]{64}$"))'
                    )
                )
        """
    )
    op.execute(
        """
        CREATE TABLE decomp_definition_fact (
            run_id text NOT NULL,
            concept_code text NOT NULL,
            fact_id text NOT NULL
                CHECK (fact_id ~ '^[0-9a-f]{64}$'),
            anchor_code text NOT NULL
                CHECK (anchor_code ~ '^C[0-9]+$'),
            group_id text NOT NULL
                CHECK (group_id ~ '^[0-9a-f]{64}$'),
            depth integer NOT NULL CHECK (depth >= 0),
            fact_kind text NOT NULL
                CHECK (fact_kind IN ('genus', 'restriction')),
            genus_code text,
            is_defined boolean,
            role_code text,
            filler_code text,
            PRIMARY KEY (run_id, concept_code, fact_id),
            FOREIGN KEY (run_id, concept_code)
                REFERENCES decomp_work_item(run_id, concept_code)
                ON DELETE CASCADE,
            CHECK (
                (
                    fact_kind = 'genus'
                    AND genus_code ~ '^C[0-9]+$'
                    AND is_defined IS NOT NULL
                    AND role_code IS NULL
                    AND filler_code IS NULL
                )
                OR
                (
                    fact_kind = 'restriction'
                    AND genus_code IS NULL
                    AND is_defined IS NULL
                    AND role_code ~ '^R[0-9]+$'
                    AND filler_code ~ '^C[0-9]+$'
                )
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decomp_definition_fact")
    op.execute(
        """
        ALTER TABLE decomp_constituent
            DROP CONSTRAINT IF EXISTS
                ck_decomp_constituent_source_definition_ids,
            DROP COLUMN IF EXISTS source_definition_ids
        """
    )
