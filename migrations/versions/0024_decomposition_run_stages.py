"""Add update-protected decomposition stages and residual filler work.

Revision ID: 0024_decomposition_run_stages
Revises: 0023_occurrence_dispositions
Create Date: 2026-09-09
"""

from alembic import op

revision: str = "0024_decomposition_run_stages"
down_revision: str | None = "0023_occurrence_dispositions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE decomp_run_stage (
            run_id text NOT NULL REFERENCES decomp_run(id) ON DELETE CASCADE,
            stage text NOT NULL CHECK (stage IN (
                'preflight', 'concept-workset', 'residual-classification',
                'metrics', 'artifact', 'publication'
            )),
            ordinal smallint NOT NULL CHECK (ordinal BETWEEN 0 AND 5),
            state text NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'running', 'complete', 'failed')),
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            claim_token uuid,
            input_identity text CHECK (
                input_identity IS NULL OR input_identity ~ '^[0-9a-f]{64}$'
            ),
            output_identity text CHECK (
                output_identity IS NULL OR output_identity ~ '^[0-9a-f]{64}$'
            ),
            output_payload jsonb,
            started_at timestamptz,
            finished_at timestamptz,
            failed_at timestamptz,
            error_type text CHECK (
                error_type IS NULL OR char_length(error_type) BETWEEN 1 AND 128
            ),
            error_message text CHECK (
                error_message IS NULL OR char_length(error_message) BETWEEN 1 AND 1000
            ),
            PRIMARY KEY (run_id, stage),
            UNIQUE (run_id, ordinal),
            CHECK (jsonb_typeof(output_payload) = 'object' OR output_payload IS NULL),
            CHECK (
                (state = 'pending' AND attempt_count = 0 AND claim_token IS NULL
                    AND input_identity IS NULL AND output_identity IS NULL
                    AND output_payload IS NULL AND started_at IS NULL
                    AND finished_at IS NULL AND failed_at IS NULL
                    AND error_type IS NULL AND error_message IS NULL)
                OR
                (state = 'running' AND attempt_count > 0 AND claim_token IS NOT NULL
                    AND input_identity IS NOT NULL AND output_identity IS NULL
                    AND output_payload IS NULL AND started_at IS NOT NULL
                    AND finished_at IS NULL AND failed_at IS NULL
                    AND error_type IS NULL AND error_message IS NULL)
                OR
                (state = 'complete' AND attempt_count > 0 AND claim_token IS NULL
                    AND input_identity IS NOT NULL AND output_identity IS NOT NULL
                    AND output_payload IS NOT NULL AND started_at IS NOT NULL
                    AND finished_at IS NOT NULL AND failed_at IS NULL
                    AND error_type IS NULL AND error_message IS NULL)
                OR
                (state = 'failed' AND attempt_count > 0 AND claim_token IS NULL
                    AND input_identity IS NOT NULL AND output_identity IS NULL
                    AND output_payload IS NULL AND started_at IS NOT NULL
                    AND finished_at IS NULL AND failed_at IS NOT NULL
                    AND error_type IS NOT NULL AND error_message IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_decomp_completed_stage() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.state = 'complete' AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'completed decomposition stage is immutable';
            END IF;
            IF OLD.input_identity IS NOT NULL
               AND NEW.input_identity IS DISTINCT FROM OLD.input_identity THEN
                RAISE EXCEPTION 'decomposition stage input identity is immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER decomp_completed_stage_immutable
        BEFORE UPDATE ON decomp_run_stage FOR EACH ROW
        EXECUTE FUNCTION protect_decomp_completed_stage()
        """
    )
    op.execute(
        """
        CREATE TABLE decomp_residual_filler (
            run_id text NOT NULL REFERENCES decomp_run(id) ON DELETE CASCADE,
            filler_code text NOT NULL CHECK (filler_code ~ '^C[0-9]+$'),
            ordinal integer NOT NULL CHECK (ordinal >= 0),
            state text NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'running', 'complete', 'failed')),
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            claim_token uuid,
            source_identity text NOT NULL CHECK (source_identity ~ '^[0-9a-f]{64}$'),
            definition_identity text CHECK (
                definition_identity IS NULL OR definition_identity ~ '^[0-9a-f]{64}$'
            ),
            detector_identity text NOT NULL CHECK (
                detector_identity ~ '^[0-9a-f]{64}$'
            ),
            classification text CHECK (
                classification IS NULL OR classification IN
                    ('atomic', 'precoordinated', 'unknown')
            ),
            unsupported_reason text,
            claimed_at timestamptz,
            completed_at timestamptz,
            failed_at timestamptz,
            error_type text CHECK (
                error_type IS NULL OR char_length(error_type) BETWEEN 1 AND 128
            ),
            error_message text CHECK (
                error_message IS NULL OR char_length(error_message) BETWEEN 1 AND 1000
            ),
            PRIMARY KEY (run_id, filler_code),
            UNIQUE (run_id, ordinal),
            CHECK ((classification = 'unknown') = (unsupported_reason IS NOT NULL)),
            CHECK (
                (state = 'pending' AND attempt_count = 0 AND claim_token IS NULL
                    AND definition_identity IS NULL AND classification IS NULL
                    AND unsupported_reason IS NULL AND claimed_at IS NULL
                    AND completed_at IS NULL AND failed_at IS NULL
                    AND error_type IS NULL AND error_message IS NULL)
                OR
                (state = 'running' AND attempt_count > 0 AND claim_token IS NOT NULL
                    AND definition_identity IS NULL AND classification IS NULL
                    AND unsupported_reason IS NULL AND claimed_at IS NOT NULL
                    AND completed_at IS NULL AND failed_at IS NULL
                    AND error_type IS NULL AND error_message IS NULL)
                OR
                (state = 'complete' AND attempt_count > 0 AND claim_token IS NULL
                    AND definition_identity IS NOT NULL AND classification IS NOT NULL
                    AND claimed_at IS NULL AND completed_at IS NOT NULL
                    AND failed_at IS NULL AND error_type IS NULL
                    AND error_message IS NULL)
                OR
                (state = 'failed' AND attempt_count > 0 AND claim_token IS NULL
                    AND definition_identity IS NULL AND classification IS NULL
                    AND unsupported_reason IS NULL AND claimed_at IS NULL
                    AND completed_at IS NULL AND failed_at IS NOT NULL
                    AND error_type IS NOT NULL AND error_message IS NOT NULL)
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decomp_residual_filler")
    op.execute(
        "DROP TRIGGER IF EXISTS decomp_completed_stage_immutable ON decomp_run_stage"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_decomp_completed_stage()")
    op.execute("DROP TABLE IF EXISTS decomp_run_stage")
