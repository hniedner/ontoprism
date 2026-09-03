"""Persist source restriction occurrences and constituent links.

Revision ID: 0021_source_occurrences
Revises: 0020_icdo_record_consistency
Create Date: 2026-08-15
"""

from alembic import op

revision: str = "0021_source_occurrences"
down_revision: str | None = "0020_icdo_record_consistency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE decomp_source_occurrence (
            run_id text NOT NULL,
            concept_code text NOT NULL,
            occurrence_id text NOT NULL CHECK (occurrence_id ~ '^[0-9a-f]{64}$'),
            source_fact_id text NOT NULL CHECK (source_fact_id ~ '^[0-9a-f]{64}$'),
            source_group_id text NOT NULL CHECK (source_group_id ~ '^[0-9a-f]{64}$'),
            anchor_code text NOT NULL CHECK (anchor_code ~ '^C[0-9]+$'),
            depth integer NOT NULL CHECK (depth >= 0),
            role_code text NOT NULL CHECK (role_code ~ '^R[0-9]+$'),
            filler_code text NOT NULL CHECK (filler_code ~ '^C[0-9]+$'),
            structural_path integer[] NOT NULL CHECK (
                cardinality(structural_path) > 0 AND 0 <= ALL(structural_path)
            ),
            member_position integer NOT NULL CHECK (
                member_position >= 0
                AND member_position = structural_path[cardinality(structural_path)]
            ),
            PRIMARY KEY (run_id, concept_code, occurrence_id),
            UNIQUE (run_id, concept_code, anchor_code, structural_path),
            FOREIGN KEY (run_id, concept_code, source_fact_id)
                REFERENCES decomp_definition_fact(run_id, concept_code, fact_id)
                ON DELETE CASCADE,
            FOREIGN KEY (run_id, concept_code, source_group_id)
                REFERENCES decomp_definition_group(run_id, concept_code, group_id)
                ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE decomp_constituent_occurrence (
            run_id text NOT NULL,
            concept_code text NOT NULL,
            axis text NOT NULL,
            filler_code text NOT NULL,
            occurrence_id text NOT NULL,
            PRIMARY KEY (
                run_id, concept_code, axis, filler_code, occurrence_id
            ),
            FOREIGN KEY (run_id, concept_code, axis, filler_code)
                REFERENCES decomp_constituent(
                    run_id, concept_code, axis, filler_code
                ) ON DELETE CASCADE,
            FOREIGN KEY (run_id, concept_code, occurrence_id)
                REFERENCES decomp_source_occurrence(
                    run_id, concept_code, occurrence_id
                ) ON DELETE CASCADE
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decomp_constituent_occurrence")
    op.execute("DROP TABLE IF EXISTS decomp_source_occurrence")
