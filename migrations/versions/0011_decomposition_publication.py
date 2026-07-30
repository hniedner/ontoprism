"""Journal decomposition publication separately from processing (#147).

Revision ID: 0011_decomposition_publication
Revises: 0010_constituent_source_role
Create Date: 2026-07-30
"""

from alembic import op

revision: str = "0011_decomposition_publication"
down_revision: str | None = "0010_constituent_source_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_run
            ADD COLUMN publication_state text,
            ADD COLUMN publication_attempt_count integer NOT NULL DEFAULT 0,
            ADD COLUMN representation_identity text,
            ADD COLUMN publication_artifact_path text,
            ADD COLUMN publication_built_at timestamptz,
            ADD COLUMN publication_started_at timestamptz,
            ADD COLUMN publication_finished_at timestamptz,
            ADD COLUMN publication_error_type text,
            ADD COLUMN publication_error_message text
        """
    )
    op.execute(
        """
        UPDATE decomp_run
        SET publication_state = CASE
            WHEN status = 'complete' THEN 'legacy'
            WHEN fingerprint ->> 'schema_version' = '1'
                AND fingerprint ->> 'output_mode' = 'none'
                THEN 'not_requested'
            ELSE 'pending'
        END
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_run
            ALTER COLUMN publication_state SET NOT NULL,
            ADD CONSTRAINT ck_decomp_run_publication_state
                CHECK (publication_state IN (
                    'legacy', 'not_requested', 'pending', 'publishing',
                    'failed', 'published'
                )),
            ADD CONSTRAINT ck_decomp_run_publication_attempt_count
                CHECK (publication_attempt_count >= 0),
            ADD CONSTRAINT ck_decomp_run_representation_identity
                CHECK (
                    representation_identity IS NULL
                    OR representation_identity ~ '^[0-9a-f]{64}$'
                ),
            ADD CONSTRAINT ck_decomp_run_publication_error_bounds
                CHECK (
                    (publication_error_type IS NULL
                        OR char_length(publication_error_type) BETWEEN 1 AND 128)
                    AND
                    (publication_error_message IS NULL
                        OR char_length(publication_error_message) BETWEEN 1 AND 1000)
                ),
            ADD CONSTRAINT ck_decomp_run_publication_shape
                CHECK (
                    (
                        publication_state IN ('legacy', 'not_requested', 'pending')
                        AND publication_attempt_count = 0
                        AND representation_identity IS NULL
                        AND publication_artifact_path IS NULL
                        AND publication_built_at IS NULL
                        AND publication_started_at IS NULL
                        AND publication_finished_at IS NULL
                        AND publication_error_type IS NULL
                        AND publication_error_message IS NULL
                    )
                    OR
                    (
                        publication_state = 'publishing'
                        AND publication_attempt_count > 0
                        AND representation_identity IS NOT NULL
                        AND publication_artifact_path IS NOT NULL
                        AND publication_artifact_path <> ''
                        AND publication_built_at IS NOT NULL
                        AND publication_started_at IS NOT NULL
                        AND publication_finished_at IS NULL
                        AND publication_error_type IS NULL
                        AND publication_error_message IS NULL
                    )
                    OR
                    (
                        publication_state = 'failed'
                        AND publication_attempt_count > 0
                        AND representation_identity IS NOT NULL
                        AND publication_artifact_path IS NOT NULL
                        AND publication_artifact_path <> ''
                        AND publication_built_at IS NOT NULL
                        AND publication_started_at IS NOT NULL
                        AND publication_finished_at IS NULL
                        AND publication_error_type IS NOT NULL
                        AND publication_error_message IS NOT NULL
                    )
                    OR
                    (
                        publication_state = 'published'
                        AND publication_attempt_count > 0
                        AND representation_identity IS NOT NULL
                        AND publication_artifact_path IS NOT NULL
                        AND publication_artifact_path <> ''
                        AND publication_built_at IS NOT NULL
                        AND publication_started_at IS NOT NULL
                        AND publication_finished_at IS NOT NULL
                        AND publication_error_type IS NULL
                        AND publication_error_message IS NULL
                    )
                ),
            ADD CONSTRAINT ck_decomp_run_processing_publication_state
                CHECK (
                    (
                        status = 'complete'
                        AND publication_state IN (
                            'legacy', 'not_requested', 'published'
                        )
                    )
                    OR
                    (
                        status = 'running'
                        AND publication_state IN (
                            'not_requested', 'pending', 'publishing', 'failed'
                        )
                    )
                    OR
                    (
                        status = 'failed'
                        AND publication_state IN (
                            'not_requested', 'pending', 'failed'
                        )
                    )
                )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_decomp_run_identity_update()
        RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF (NEW.id, NEW.branch, NEW.ncit_version, NEW.started_at,
                NEW.source_identity, NEW.fingerprint, NEW.fingerprint_sha256,
                NEW.emitted_at)
               IS DISTINCT FROM
               (OLD.id, OLD.branch, OLD.ncit_version, OLD.started_at,
                OLD.source_identity, OLD.fingerprint, OLD.fingerprint_sha256,
                OLD.emitted_at) THEN
                RAISE EXCEPTION 'decomposition run identity is immutable';
            END IF;
            IF OLD.representation_identity IS NOT NULL
               AND (
                    NEW.representation_identity,
                    NEW.publication_artifact_path,
                    NEW.publication_built_at
               ) IS DISTINCT FROM (
                    OLD.representation_identity,
                    OLD.publication_artifact_path,
                    OLD.publication_built_at
               ) THEN
                RAISE EXCEPTION 'decomposition publication identity is immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_run
            DROP CONSTRAINT IF EXISTS ck_decomp_run_processing_publication_state,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_publication_shape,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_publication_error_bounds,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_representation_identity,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_publication_attempt_count,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_publication_state,
            DROP COLUMN IF EXISTS publication_error_message,
            DROP COLUMN IF EXISTS publication_error_type,
            DROP COLUMN IF EXISTS publication_finished_at,
            DROP COLUMN IF EXISTS publication_started_at,
            DROP COLUMN IF EXISTS publication_built_at,
            DROP COLUMN IF EXISTS publication_artifact_path,
            DROP COLUMN IF EXISTS representation_identity,
            DROP COLUMN IF EXISTS publication_attempt_count,
            DROP COLUMN IF EXISTS publication_state
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_decomp_run_identity_update()
        RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF (NEW.id, NEW.branch, NEW.ncit_version, NEW.started_at,
                NEW.source_identity, NEW.fingerprint, NEW.fingerprint_sha256,
                NEW.emitted_at)
               IS DISTINCT FROM
               (OLD.id, OLD.branch, OLD.ncit_version, OLD.started_at,
                OLD.source_identity, OLD.fingerprint, OLD.fingerprint_sha256,
                OLD.emitted_at) THEN
                RAISE EXCEPTION 'decomposition run identity is immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )
