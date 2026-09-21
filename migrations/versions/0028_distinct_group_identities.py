"""Separate ambiguity, source-structure, and normalized projection groups.

Revision ID: 0028_distinct_group_identities
Revises: 0027_full_run_admission
Create Date: 2026-09-15
"""

from alembic import op

revision: str = "0028_distinct_group_identities"
down_revision: str | None = "0027_full_run_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION is_canonical_sha256_jsonb_array(value jsonb)
        RETURNS boolean IMMUTABLE LANGUAGE plpgsql AS $$
        DECLARE
            item jsonb;
            previous text := NULL;
            current text;
        BEGIN
            IF jsonb_typeof(value) != 'array' THEN
                RETURN false;
            END IF;
            FOR item IN SELECT * FROM jsonb_array_elements(value) LOOP
                IF jsonb_typeof(item) != 'string' THEN
                    RETURN false;
                END IF;
                current := item #>> '{}';
                IF current !~ '^[0-9a-f]{64}$'
                   OR (previous IS NOT NULL AND current <= previous) THEN
                    RETURN false;
                END IF;
                previous := current;
            END LOOP;
            RETURN true;
        END $$
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_constituent
        ADD COLUMN axis_ambiguous boolean NOT NULL DEFAULT false,
        ADD COLUMN source_group_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN normalized_group_id text,
        ADD COLUMN normalized_group_label text
        """
    )
    op.execute(
        """
        UPDATE decomp_constituent
        SET axis_ambiguous = relationship_group IS NOT NULL
        """
    )
    op.execute("ALTER TABLE decomp_constituent DROP COLUMN relationship_group")
    op.execute(
        """
        UPDATE decomp_constituent c
        SET source_group_ids = (
            SELECT COALESCE(
                jsonb_agg(source_group_id ORDER BY source_group_id), '[]'::jsonb
            )
            FROM (
                SELECT DISTINCT source_group_id
                FROM (
                    SELECT so.source_group_id
                    FROM decomp_constituent_occurrence co
                    JOIN decomp_source_occurrence so
                      ON so.run_id = co.run_id
                     AND so.concept_code = co.concept_code
                     AND so.occurrence_id = co.occurrence_id
                    WHERE co.run_id = c.run_id
                      AND co.concept_code = c.concept_code
                      AND co.axis = c.axis
                      AND co.filler_code = c.filler_code
                    UNION ALL
                    SELECT df.group_id
                    FROM jsonb_array_elements_text(c.source_definition_ids) source_id
                    JOIN decomp_definition_fact df
                      ON df.run_id = c.run_id
                     AND df.concept_code = c.concept_code
                     AND df.fact_id = source_id
                ) candidates
            ) canonical
        )
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_constituent
        ALTER COLUMN source_group_ids DROP DEFAULT,
        ADD CONSTRAINT ck_decomp_constituent_source_group_ids CHECK (
            is_canonical_sha256_jsonb_array(source_group_ids)
        ),
        ADD CONSTRAINT ck_decomp_constituent_normalized_group CHECK (
            (normalized_group_id IS NULL AND normalized_group_label IS NULL)
            OR (normalized_group_id ~ '^[0-9a-f]{64}$'
                AND length(normalized_group_label) > 0)
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE decomp_constituent ADD COLUMN relationship_group text")
    op.execute(
        """
        UPDATE decomp_constituent
        SET relationship_group = axis
        WHERE axis_ambiguous
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_constituent
        DROP CONSTRAINT ck_decomp_constituent_source_group_ids,
        DROP CONSTRAINT ck_decomp_constituent_normalized_group,
        DROP COLUMN axis_ambiguous,
        DROP COLUMN source_group_ids,
        DROP COLUMN normalized_group_id,
        DROP COLUMN normalized_group_label
        """
    )
    op.execute("DROP FUNCTION is_canonical_sha256_jsonb_array(jsonb)")
