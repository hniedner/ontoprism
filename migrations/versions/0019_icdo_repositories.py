"""Certified ICD-O repository generations.

Revision ID: 0019_icdo_repositories
Revises: 0018_xref_generations
Create Date: 2026-08-12
"""

from alembic import op

revision: str = "0019_icdo_repositories"
down_revision: str | None = "0018_xref_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE icdo_generation (
      id text NOT NULL CHECK (id ~ '^[0-9a-f]{64}$'),
      edition text NOT NULL CHECK (edition IN ('3.2','4.0')),
      axis text NOT NULL CHECK (axis IN ('morphology','topography')),
      manifest jsonb NOT NULL,
      PRIMARY KEY (edition, axis, id),
      UNIQUE (id)
    )""")
    op.execute("""CREATE TABLE icdo_record (
      edition text NOT NULL, axis text NOT NULL,
      generation_id text NOT NULL,
      code text NOT NULL, level text NOT NULL,
      behaviour text, search_text text NOT NULL, payload jsonb NOT NULL,
      FOREIGN KEY (edition, axis, generation_id)
        REFERENCES icdo_generation(edition, axis, id),
      PRIMARY KEY (edition, axis, generation_id, code)
    )""")
    op.execute(
        "CREATE INDEX idx_icdo_record_filters ON icdo_record "
        "(generation_id, behaviour, level, code)"
    )
    op.execute("""CREATE TABLE icdo_active_generation (
      edition text NOT NULL, axis text NOT NULL,
      generation_id text NOT NULL UNIQUE,
      activated_at timestamptz NOT NULL,
      PRIMARY KEY (edition, axis),
      CHECK (NOT (edition='3.2' AND axis='topography')),
      FOREIGN KEY (edition, axis, generation_id)
        REFERENCES icdo_generation(edition, axis, id)
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE icdo_active_generation")
    op.execute("DROP TABLE icdo_record")
    op.execute("DROP TABLE icdo_generation")
