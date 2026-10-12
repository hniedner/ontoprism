"""Allow the engine's proposed fillers in the ledger (owner #474)."""

from alembic import op
from sqlalchemy import text

from ontolib.decomposition.models import FILLER_CODE_PATTERN

revision = "0036_ledger_mint_fillers"
down_revision = "0035_assertion_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("assertion_evidence_filler_code_check", "assertion_evidence")
    op.create_check_constraint(
        "assertion_evidence_filler_code_check",
        "assertion_evidence",
        text("filler_code ~ :pattern").bindparams(pattern=FILLER_CODE_PATTERN),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE assertion_evidence IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM assertion_evidence
                   WHERE filler_code LIKE 'MINT-%') THEN
            RAISE EXCEPTION 'refusing downgrade while MINT assertion fillers exist';
        END IF;
    END $$""")
    op.drop_constraint("assertion_evidence_filler_code_check", "assertion_evidence")
    op.create_check_constraint(
        "assertion_evidence_filler_code_check",
        "assertion_evidence",
        "filler_code ~ '^C[0-9]+$'",
    )
