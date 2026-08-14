"""Generation-atomic, typed xref publication.

Revision ID: 0018_xref_generations
Revises: 0017_uberon_search
Create Date: 2026-08-12
"""

from alembic import op

revision: str = "0018_xref_generations"
down_revision: str | None = "0017_uberon_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE xref_run ADD CONSTRAINT uq_xref_run_id_source UNIQUE (id, source)"
    )
    op.execute("ALTER TABLE concept_xref RENAME TO concept_xref_legacy")
    op.execute(
        """CREATE TABLE xref_generation (
          id text NOT NULL CHECK (id ~ '^[0-9a-f]{64}$'),
          source text NOT NULL CHECK (source IN (
            'uberon-cl','uberon-cl-promotion','uberon-publisher-xref',
            'ncit-p334-icdo32')),
          content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          source_metadata jsonb NOT NULL CHECK (
            jsonb_typeof(source_metadata) = 'object' AND
            jsonb_typeof(source_metadata->'source') = 'string' AND
            source_metadata->>'source' = source AND
            CASE source
              WHEN 'uberon-cl' THEN
                source_metadata ?& ARRAY['ncit_source_identity',
                  'uberon_source_identity',
                  'uberon_serving_identity'] AND
                source_metadata - ARRAY['source','ncit_source_identity',
                  'uberon_source_identity','uberon_serving_identity'] = '{}'::jsonb AND
                jsonb_typeof(source_metadata->'ncit_source_identity') = 'string' AND
                source_metadata->>'ncit_source_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'uberon_source_identity') = 'string' AND
                source_metadata->>'uberon_source_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'uberon_serving_identity') = 'string' AND
                source_metadata->>'uberon_serving_identity' ~ '^[0-9a-f]{64}$'
              WHEN 'uberon-cl-promotion' THEN
                source_metadata ?& ARRAY['ncit_source_identity',
                  'uberon_source_identity',
                  'uberon_serving_identity'] AND
                source_metadata - ARRAY['source','ncit_source_identity',
                  'uberon_source_identity','uberon_serving_identity'] = '{}'::jsonb AND
                jsonb_typeof(source_metadata->'ncit_source_identity') = 'string' AND
                source_metadata->>'ncit_source_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'uberon_source_identity') = 'string' AND
                source_metadata->>'uberon_source_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'uberon_serving_identity') = 'string' AND
                source_metadata->>'uberon_serving_identity' ~ '^[0-9a-f]{64}$'
              WHEN 'uberon-publisher-xref' THEN
                source_metadata ?& ARRAY['ncit_source_identity',
                  'uberon_source_identity',
                  'uberon_serving_identity','uberon_assertion_identity',
                  'ncit_target_identity'] AND
                source_metadata - ARRAY['source','ncit_source_identity',
                  'uberon_source_identity','uberon_serving_identity',
                  'uberon_assertion_identity','ncit_target_identity'] = '{}'::jsonb AND
                jsonb_typeof(source_metadata->'ncit_source_identity') = 'string' AND
                source_metadata->>'ncit_source_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'uberon_source_identity') = 'string' AND
                source_metadata->>'uberon_source_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'uberon_serving_identity') = 'string' AND
                source_metadata->>'uberon_serving_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->
                  'uberon_assertion_identity') = 'string' AND
                source_metadata->>'uberon_assertion_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'ncit_target_identity') = 'string' AND
                source_metadata->>'ncit_target_identity' ~ '^[0-9a-f]{64}$'
              WHEN 'ncit-p334-icdo32' THEN
                source_metadata ?& ARRAY['ncit_source_identity',
                  'icdo_generation_identity',
                  'icdo_serving_identity','ncit_p334_identity']
                    AND source_metadata - ARRAY['source','ncit_source_identity',
                    'icdo_generation_identity','icdo_serving_identity',
                    'ncit_p334_identity'] = '{}'::jsonb AND
                jsonb_typeof(source_metadata->'ncit_source_identity') = 'string' AND
                source_metadata->>'ncit_source_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'icdo_generation_identity') = 'string' AND
                source_metadata->>'icdo_generation_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'icdo_serving_identity') = 'string' AND
                source_metadata->>'icdo_serving_identity' ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(source_metadata->'ncit_p334_identity') = 'string' AND
                source_metadata->>'ncit_p334_identity' ~ '^[0-9a-f]{64}$'
              ELSE false
            END),
          graph_iri text NOT NULL UNIQUE,
          run_id text NOT NULL,
          state text NOT NULL CHECK (state IN ('prepared', 'published')),
          created_at timestamptz NOT NULL DEFAULT now(),
          published_at timestamptz,
          PRIMARY KEY (source, id),
          UNIQUE (id),
          FOREIGN KEY (run_id, source) REFERENCES xref_run(id, source)
        )"""
    )
    op.execute(
        """CREATE TABLE xref_active_generation (
          source text PRIMARY KEY,
          generation_id text NOT NULL UNIQUE,
          activated_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (source, generation_id) REFERENCES xref_generation(source, id)
        )"""
    )
    op.execute(
        """CREATE TABLE concept_xref (
           generation_id text NOT NULL,
           generation_source text NOT NULL,
          run_id text NOT NULL,
          subject_system text NOT NULL,
          subject_version text NOT NULL,
          subject_id text NOT NULL,
           predicate_id text NOT NULL CHECK (predicate_id IN (
             'http://www.w3.org/2004/02/skos/core#exactMatch',
             'http://www.w3.org/2004/02/skos/core#closeMatch',
             'http://www.w3.org/2004/02/skos/core#broadMatch',
             'http://www.w3.org/2004/02/skos/core#narrowMatch',
             'http://www.w3.org/2004/02/skos/core#relatedMatch')),
          object_system text NOT NULL,
          object_version text NOT NULL,
          object_id text NOT NULL,
          mapping_justification text NOT NULL,
          confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
           lifecycle_state text NOT NULL CHECK (lifecycle_state IN (
             'proposed','validated','active','quarantined','retired')),
          review_status text NOT NULL,
          author text NOT NULL,
          evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
           CHECK (
              (generation_source = 'uberon-cl-promotion'
                AND subject_system = 'ncit' AND object_system = 'uberon-cl') OR
             (generation_source = 'uberon-cl'
               AND subject_system = 'ncit' AND object_system = 'uberon-cl') OR
             (generation_source = 'uberon-publisher-xref'
               AND subject_system = 'uberon-cl' AND object_system = 'ncit') OR
             (generation_source = 'ncit-p334-icdo32'
               AND subject_system = 'ncit' AND object_system = 'icdo')
           ),
           FOREIGN KEY (generation_source, generation_id)
             REFERENCES xref_generation(source, id),
           FOREIGN KEY (run_id, generation_source)
             REFERENCES xref_run(id, source),
           PRIMARY KEY (
            generation_id, subject_system, subject_version, subject_id,
            predicate_id, object_system, object_version, object_id
          )
        )"""
    )
    op.execute(
        "CREATE INDEX idx_concept_xref_forward ON concept_xref "
        "(subject_system, subject_version, subject_id, generation_id) "
        "INCLUDE (object_system, object_version, object_id, predicate_id, "
        "lifecycle_state, confidence)"
    )
    op.execute(
        "CREATE INDEX idx_concept_xref_reverse ON concept_xref "
        "(object_system, object_version, object_id, generation_id) "
        "INCLUDE (subject_system, subject_version, subject_id, predicate_id, "
        "lifecycle_state, confidence)"
    )
    op.execute(
        """CREATE TABLE xref_activation_history (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          source text NOT NULL,
          generation_id text NOT NULL,
          predecessor_id text,
          activated_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (source, generation_id) REFERENCES xref_generation(source, id),
          FOREIGN KEY (source, predecessor_id) REFERENCES xref_generation(source, id)
        )"""
    )
    op.execute(
        """CREATE TABLE xref_legacy_quarantine (
          quarantined_at timestamptz NOT NULL DEFAULT now(),
          reason text NOT NULL,
          legacy_row jsonb NOT NULL
        )"""
    )
    op.execute(
        "INSERT INTO xref_legacy_quarantine (reason, legacy_row) "
        "SELECT 'missing typed endpoint generation identity', to_jsonb(old) "
        "FROM concept_xref_legacy AS old"
    )
    op.execute("DROP TABLE concept_xref_legacy")


def downgrade() -> None:
    op.execute("ALTER TABLE concept_xref RENAME TO concept_xref_generation")
    op.execute(
        """CREATE TABLE concept_xref (
          run_id text NOT NULL REFERENCES xref_run(id),
          subject_id text NOT NULL,
          predicate_id text NOT NULL,
          object_id text NOT NULL,
          mapping_justification text NOT NULL,
          confidence double precision NOT NULL,
          subject_source_version text NOT NULL,
          object_source_version text NOT NULL,
          lifecycle_state text NOT NULL DEFAULT 'proposed',
          review_status text NOT NULL DEFAULT 'unreviewed',
          author text NOT NULL DEFAULT '',
          evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
          PRIMARY KEY (run_id, subject_id, predicate_id, object_id)
        )"""
    )
    op.execute("DROP TABLE concept_xref_generation")
    op.execute("DROP TABLE xref_activation_history")
    op.execute("DROP TABLE xref_active_generation")
    op.execute("DROP TABLE xref_generation")
    op.execute("DROP TABLE xref_legacy_quarantine")
    op.execute("ALTER TABLE xref_run DROP CONSTRAINT uq_xref_run_id_source")
