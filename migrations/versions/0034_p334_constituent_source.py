"""Allow P334-derived HistologyAnchor provenance (#467, owner approved)."""

from alembic import op

revision = "0034_p334_constituent_source"
down_revision = "0033_xref_candidate_contexts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE decomp_constituent
        DROP CONSTRAINT ck_decomp_constituent_source_roles,
        ADD CONSTRAINT ck_decomp_constituent_source_roles CHECK (
            jsonb_typeof(source_roles) = 'array'
            AND jsonb_path_query_array(
                source_roles, '$[*] ? (@ like_regex "^R[0-9]+$")'
            ) = source_roles
            AND (
                (axis_source = 'role' AND jsonb_array_length(source_roles) > 0)
                OR (axis_source IN ('parent', 'nlp') AND source_roles = '[]'::jsonb)
                OR (axis_source = 'p334' AND axis = 'op:HistologyAnchor'
                    AND source_roles = '[]'::jsonb)
            )
        )
    """)


def downgrade() -> None:
    # Refuse if P334 rows exist rather than deleting or relabelling their provenance.
    op.execute("""
        ALTER TABLE decomp_constituent
        DROP CONSTRAINT ck_decomp_constituent_source_roles,
        ADD CONSTRAINT ck_decomp_constituent_source_roles CHECK (
            jsonb_typeof(source_roles) = 'array'
            AND jsonb_path_query_array(
                source_roles, '$[*] ? (@ like_regex "^R[0-9]+$")'
            ) = source_roles
            AND (
                (axis_source = 'role' AND jsonb_array_length(source_roles) > 0)
                OR (axis_source IN ('parent', 'nlp') AND source_roles = '[]'::jsonb)
            )
        )
    """)
