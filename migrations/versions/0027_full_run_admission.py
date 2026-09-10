"""Add database-authoritative full decomposition run admission.

Revision ID: 0027_full_run_admission
Revises: 0026_mixed_specificity_paths
Create Date: 2026-09-10
"""

from alembic import op

revision: str = "0027_full_run_admission"
down_revision: str | None = "0026_mixed_specificity_paths"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decomp_run
        ADD COLUMN execution_identity text,
        ADD CONSTRAINT ck_decomp_run_execution_identity CHECK (
            execution_identity IS NULL
            OR execution_identity ~ '^[0-9a-f]{64}$'
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_decomp_run_admitted_execution
        ON decomp_run (execution_identity)
        WHERE execution_identity IS NOT NULL
          AND status IN ('running', 'failed', 'complete')
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_decomp_run_identity_update()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF (NEW.id, NEW.branch, NEW.ncit_version, NEW.started_at,
                NEW.source_identity, NEW.fingerprint, NEW.fingerprint_sha256,
                NEW.emitted_at, NEW.execution_identity)
               IS DISTINCT FROM
               (OLD.id, OLD.branch, OLD.ncit_version, OLD.started_at,
                OLD.source_identity, OLD.fingerprint, OLD.fingerprint_sha256,
                OLD.emitted_at, OLD.execution_identity) THEN
                RAISE EXCEPTION 'decomposition run identity is immutable';
            END IF;
            IF OLD.representation_identity IS NOT NULL
               AND (NEW.representation_identity, NEW.publication_artifact_path,
                    NEW.publication_built_at)
               IS DISTINCT FROM
                   (OLD.representation_identity, OLD.publication_artifact_path,
                    OLD.publication_built_at) THEN
                RAISE EXCEPTION 'decomposition publication identity is immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_decomp_run_admitted_execution")
    op.execute(
        "ALTER TABLE decomp_run DROP CONSTRAINT ck_decomp_run_execution_identity, "
        "DROP COLUMN execution_identity"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_decomp_run_identity_update()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
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
               AND (NEW.representation_identity, NEW.publication_artifact_path,
                    NEW.publication_built_at)
               IS DISTINCT FROM
                   (OLD.representation_identity, OLD.publication_artifact_path,
                    OLD.publication_built_at) THEN
                RAISE EXCEPTION 'decomposition publication identity is immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )
