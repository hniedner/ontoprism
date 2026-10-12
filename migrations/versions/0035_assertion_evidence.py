"""Assertion evidence and retained human rationale (#347, owner approved)."""

from alembic import op

revision = "0035_assertion_evidence"
down_revision = "0034_p334_constituent_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE assertion_evidence (
            concept_code text NOT NULL CHECK (concept_code ~ '^C[0-9]+$'),
            axis text NOT NULL CHECK (axis ~ '\\S'),
            filler_code text NOT NULL CHECK (filler_code ~ '^C[0-9]+$'),
            source_release text NOT NULL CHECK (source_release ~ '\\S'),
            scope_root text NOT NULL CHECK (scope_root ~ '^C[0-9]+$'),
            support jsonb NOT NULL CHECK (jsonb_typeof(support) = 'object'),
            status text NOT NULL
                CHECK (status IN ('provisional','evidence-supported','needs-SME')),
            needs_sme_reasons jsonb NOT NULL DEFAULT '[]'
                CHECK (jsonb_typeof(needs_sme_reasons) = 'array'),
            evidence jsonb NOT NULL DEFAULT '[]'
                CHECK (jsonb_typeof(evidence) = 'array'),
            decisions jsonb NOT NULL DEFAULT '[]'
                CHECK (jsonb_typeof(decisions) = 'array'),
            applicability jsonb NOT NULL CHECK (jsonb_typeof(applicability) = 'object'),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (concept_code, axis, filler_code),
            CHECK ((status = 'needs-SME') = (jsonb_array_length(needs_sme_reasons) > 0))
        );
    """)
    op.execute(
        "CREATE INDEX assertion_evidence_status_axis "
        "ON assertion_evidence (status, axis)"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE assertion_evidence IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM assertion_evidence) THEN
                RAISE EXCEPTION 'refusing to drop nonempty assertion evidence ledger';
            END IF;
        END $$;
    """)
    op.execute("DROP TABLE assertion_evidence")
