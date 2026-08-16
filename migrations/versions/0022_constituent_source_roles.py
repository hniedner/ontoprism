"""Preserve every NCIt source role for a normalized constituent.

Revision ID: 0022_constituent_source_roles
Revises: 0021_source_occurrences
Create Date: 2026-08-15
"""

from alembic import op

revision: str = "0022_constituent_source_roles"
down_revision: str | None = "0021_source_occurrences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE decomp_constituent ADD COLUMN source_roles jsonb")
    op.execute(
        """
        UPDATE decomp_constituent
        SET source_roles = CASE
            WHEN source_role IS NOT NULL THEN jsonb_build_array(source_role)
            WHEN axis_source = 'role' AND axis ~ '^R[0-9]+$'
                THEN jsonb_build_array(axis)
            ELSE '[]'::jsonb
        END
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_constituent
            ALTER COLUMN source_roles SET NOT NULL,
            ADD CONSTRAINT ck_decomp_constituent_source_roles
                CHECK (
                    jsonb_typeof(source_roles) = 'array'
                    AND jsonb_path_query_array(
                        source_roles,
                        '$[*] ? (@ like_regex "^R[0-9]+$")'
                    ) = source_roles
                    AND (
                        (axis_source = 'role' AND jsonb_array_length(source_roles) > 0)
                        OR
                        (
                            axis_source IN ('parent', 'nlp')
                            AND source_roles = '[]'::jsonb
                        )
                    )
                ),
            DROP CONSTRAINT ck_decomp_constituent_source_role,
            DROP COLUMN source_role
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM decomp_constituent
                WHERE jsonb_array_length(source_roles) > 1
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade source_roles with multi-role constituents';
            END IF;
        END
        $$;
        """
    )
    op.execute("ALTER TABLE decomp_constituent ADD COLUMN source_role text")
    op.execute(
        """
        UPDATE decomp_constituent
        SET source_role = source_roles->>0
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_constituent
            ADD CONSTRAINT ck_decomp_constituent_source_role
                CHECK (source_role IS NULL OR source_role ~ '^R[0-9]+$'),
            DROP CONSTRAINT ck_decomp_constituent_source_roles,
            DROP COLUMN source_roles
        """
    )
