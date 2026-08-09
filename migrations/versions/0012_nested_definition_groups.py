"""Persist canonical nested stated-definition groups (#153).

Revision ID: 0012_nested_definition_groups
Revises: 0011_decomposition_publication
Create Date: 2026-07-30
"""

from alembic import op

revision: str = "0012_nested_definition_groups"
down_revision: str | None = "0011_decomposition_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE decomp_definition_group (
            run_id text NOT NULL,
            concept_code text NOT NULL,
            group_id text NOT NULL
                CHECK (group_id ~ '^[0-9a-f]{64}$'),
            anchor_code text NOT NULL
                CHECK (anchor_code ~ '^C[0-9]+$'),
            depth integer NOT NULL CHECK (depth >= 0),
            is_root boolean NOT NULL,
            PRIMARY KEY (run_id, concept_code, group_id),
            FOREIGN KEY (run_id, concept_code)
                REFERENCES decomp_work_item(run_id, concept_code)
                ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        INSERT INTO decomp_definition_group
            (run_id, concept_code, group_id, anchor_code, depth, is_root)
        SELECT run_id, concept_code, group_id, min(anchor_code), min(depth), true
        FROM decomp_definition_fact
        GROUP BY run_id, concept_code, group_id
        """
    )
    op.execute(
        """
        CREATE TABLE decomp_definition_group_edge (
            run_id text NOT NULL,
            concept_code text NOT NULL,
            parent_group_id text NOT NULL,
            child_group_id text NOT NULL,
            PRIMARY KEY (
                run_id, concept_code, parent_group_id, child_group_id
            ),
            FOREIGN KEY (run_id, concept_code, parent_group_id)
                REFERENCES decomp_definition_group(
                    run_id, concept_code, group_id
                )
                ON DELETE CASCADE,
            FOREIGN KEY (run_id, concept_code, child_group_id)
                REFERENCES decomp_definition_group(
                    run_id, concept_code, group_id
                )
                ON DELETE CASCADE,
            CHECK (parent_group_id <> child_group_id)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_definition_fact
            ADD CONSTRAINT fk_decomp_definition_fact_group
            FOREIGN KEY (run_id, concept_code, group_id)
            REFERENCES decomp_definition_group(run_id, concept_code, group_id)
            ON DELETE CASCADE
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_definition_fact
            DROP CONSTRAINT IF EXISTS fk_decomp_definition_fact_group
        """
    )
    op.execute("DROP TABLE IF EXISTS decomp_definition_group_edge")
    op.execute("DROP TABLE IF EXISTS decomp_definition_group")
