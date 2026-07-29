"""Source-bound exact decomposition run lifecycle (#179)

Revision ID: 0008_decomposition_run_lifecycle
Revises: 0007_embedding_publication
Create Date: 2026-07-29
"""

from alembic import op

revision: str = "0008_decomposition_run_lifecycle"
down_revision: str | None = "0007_embedding_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_run
            ADD COLUMN source_identity text,
            ADD COLUMN fingerprint jsonb,
            ADD COLUMN fingerprint_sha256 text,
            ADD COLUMN emitted_at timestamptz,
            ADD COLUMN error_type text,
            ADD COLUMN error_message text
        """
    )
    op.execute(
        """
        UPDATE decomp_run SET
            status = CASE
                WHEN status = 'complete' THEN 'complete'
                ELSE 'failed'
            END,
            source_identity = repeat('0', 64),
            fingerprint = jsonb_build_object(
                'schema_version', 0,
                'legacy', true,
                'run_id', id,
                'branch', branch,
                'ncit_version', ncit_version
            ),
            fingerprint_sha256 = repeat('0', 64),
            emitted_at = started_at
        """
    )
    op.execute(
        """
        UPDATE decomp_run SET
            error_type = 'LegacyRun',
            error_message = 'Legacy run predates exact worklist persistence'
        WHERE status = 'failed'
        """
    )
    op.execute(
        """
        UPDATE decomp_run SET finished_at = NULL WHERE status <> 'complete'
        """
    )
    op.execute(
        """
        UPDATE decomp_run
        SET finished_at = COALESCE(finished_at, started_at)
        WHERE status = 'complete'
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_run
            ALTER COLUMN source_identity SET NOT NULL,
            ALTER COLUMN fingerprint SET NOT NULL,
            ALTER COLUMN fingerprint_sha256 SET NOT NULL,
            ALTER COLUMN emitted_at SET NOT NULL,
            ADD CONSTRAINT ck_decomp_run_status
                CHECK (status IN ('running', 'failed', 'complete')),
            ADD CONSTRAINT ck_decomp_run_source_identity
                CHECK (source_identity ~ '^[0-9a-f]{64}$'),
            ADD CONSTRAINT ck_decomp_run_fingerprint_sha256
                CHECK (fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
            ADD CONSTRAINT ck_decomp_run_fingerprint_object
                CHECK (jsonb_typeof(fingerprint) = 'object'),
            ADD CONSTRAINT ck_decomp_run_error_bounds
                CHECK (
                    (error_type IS NULL OR char_length(error_type) BETWEEN 1 AND 128)
                    AND
                    (error_message IS NULL
                        OR char_length(error_message) BETWEEN 1 AND 1000)
                ),
            ADD CONSTRAINT ck_decomp_run_state_shape
                CHECK (
                    (status = 'running' AND finished_at IS NULL
                        AND error_type IS NULL AND error_message IS NULL)
                    OR
                    (status = 'failed' AND finished_at IS NULL
                        AND error_type IS NOT NULL AND error_message IS NOT NULL)
                    OR
                    (status = 'complete' AND finished_at IS NOT NULL
                        AND error_type IS NULL AND error_message IS NULL)
                )
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_decomp_run_identity_update() RETURNS trigger
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
    op.execute(
        """
        CREATE TRIGGER decomp_run_identity_immutable
        BEFORE UPDATE ON decomp_run
        FOR EACH ROW EXECUTE FUNCTION reject_decomp_run_identity_update()
        """
    )
    op.execute(
        """
        CREATE TABLE decomp_work_item (
            run_id text NOT NULL REFERENCES decomp_run(id) ON DELETE CASCADE,
            concept_code text NOT NULL,
            ordinal integer NOT NULL CHECK (ordinal >= 0),
            state text NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'running', 'failed', 'complete')),
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            claim_token uuid,
            claimed_at timestamptz,
            semantic_type text,
            is_decomposed boolean,
            is_residual boolean,
            constituent_count integer CHECK (constituent_count >= 0),
            minted_count integer CHECK (minted_count >= 0),
            error_type text,
            error_message text,
            failed_at timestamptz,
            completed_at timestamptz,
            PRIMARY KEY (run_id, concept_code),
            UNIQUE (run_id, ordinal),
            CHECK (
                (error_type IS NULL OR char_length(error_type) BETWEEN 1 AND 128)
                AND
                (error_message IS NULL OR char_length(error_message) BETWEEN 1 AND 1000)
            ),
            CHECK (
                (state = 'pending' AND attempt_count = 0
                    AND claim_token IS NULL AND claimed_at IS NULL
                    AND semantic_type IS NULL AND is_decomposed IS NULL
                    AND is_residual IS NULL AND constituent_count IS NULL
                    AND minted_count IS NULL AND error_type IS NULL
                    AND error_message IS NULL AND failed_at IS NULL
                    AND completed_at IS NULL)
                OR
                (state = 'running' AND attempt_count > 0
                    AND claim_token IS NOT NULL AND claimed_at IS NOT NULL
                    AND semantic_type IS NULL AND is_decomposed IS NULL
                    AND is_residual IS NULL AND constituent_count IS NULL
                    AND minted_count IS NULL AND error_type IS NULL
                    AND error_message IS NULL AND failed_at IS NULL
                    AND completed_at IS NULL)
                OR
                (state = 'failed' AND attempt_count > 0
                    AND claim_token IS NULL AND claimed_at IS NULL
                    AND semantic_type IS NULL AND is_decomposed IS NULL
                    AND is_residual IS NULL AND constituent_count IS NULL
                    AND minted_count IS NULL AND error_type IS NOT NULL
                    AND error_message IS NOT NULL AND failed_at IS NOT NULL
                    AND completed_at IS NULL)
                OR
                (state = 'complete' AND attempt_count > 0
                    AND claim_token IS NULL AND claimed_at IS NULL
                    AND is_decomposed IS NOT NULL AND is_residual IS NOT NULL
                    AND constituent_count IS NOT NULL AND minted_count IS NOT NULL
                    AND error_type IS NULL AND error_message IS NULL
                    AND failed_at IS NULL AND completed_at IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE decomp_constituent
            ADD COLUMN needs_review boolean NOT NULL DEFAULT false,
            ADD COLUMN relationship_group text
        """
    )
    op.execute(
        """
        CREATE TABLE decomp_minted_proposal (
            run_id text NOT NULL,
            concept_code text NOT NULL,
            proposal_id text NOT NULL,
            axis text NOT NULL,
            label text NOT NULL,
            source_signal text NOT NULL DEFAULT '',
            status text NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'approved', 'rejected')),
            PRIMARY KEY (run_id, concept_code, proposal_id),
            FOREIGN KEY (run_id, concept_code)
                REFERENCES decomp_work_item(run_id, concept_code)
                ON DELETE CASCADE
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decomp_minted_proposal")
    op.execute("DROP TABLE IF EXISTS decomp_work_item")
    op.execute(
        """
        ALTER TABLE decomp_constituent
            DROP COLUMN IF EXISTS relationship_group,
            DROP COLUMN IF EXISTS needs_review
        """
    )
    op.execute("DROP TRIGGER IF EXISTS decomp_run_identity_immutable ON decomp_run")
    op.execute("DROP FUNCTION IF EXISTS reject_decomp_run_identity_update()")
    op.execute(
        """
        ALTER TABLE decomp_run
            DROP CONSTRAINT IF EXISTS ck_decomp_run_state_shape,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_error_bounds,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_fingerprint_object,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_fingerprint_sha256,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_source_identity,
            DROP CONSTRAINT IF EXISTS ck_decomp_run_status,
            DROP COLUMN IF EXISTS error_message,
            DROP COLUMN IF EXISTS error_type,
            DROP COLUMN IF EXISTS emitted_at,
            DROP COLUMN IF EXISTS fingerprint_sha256,
            DROP COLUMN IF EXISTS fingerprint,
            DROP COLUMN IF EXISTS source_identity
        """
    )
