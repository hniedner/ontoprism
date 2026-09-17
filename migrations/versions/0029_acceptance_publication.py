"""Journal evidence-closed accepted projection publication.

Revision ID: 0029_acceptance_publication
Revises: 0028_distinct_group_identities
Create Date: 2026-09-17
"""

from alembic import op

revision: str = "0029_acceptance_publication"
down_revision: str | None = "0028_distinct_group_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE decomp_acceptance_publication (
            acceptance_identity text PRIMARY KEY
                CHECK (acceptance_identity ~ '^[0-9a-f]{64}$'),
            intent jsonb NOT NULL CHECK (jsonb_typeof(intent) = 'object'),
            state text NOT NULL
                CHECK (state IN ('publishing', 'published', 'failed')),
            receipt jsonb CHECK (receipt IS NULL OR jsonb_typeof(receipt) = 'object'),
            error_type text,
            error_message text,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (
                (state = 'published' AND receipt IS NOT NULL
                    AND error_type IS NULL AND error_message IS NULL)
                OR (state = 'publishing' AND receipt IS NULL
                    AND error_type IS NULL AND error_message IS NULL)
                OR (state = 'failed' AND receipt IS NULL
                    AND error_type IS NOT NULL AND error_message IS NOT NULL)
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE decomp_acceptance_publication")
