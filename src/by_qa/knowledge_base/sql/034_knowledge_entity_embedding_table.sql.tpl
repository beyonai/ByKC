CREATE TABLE IF NOT EXISTS {{ entity_embedding_table_name }} (
    kid bigserial PRIMARY KEY,
    entity_id bigint NOT NULL
        REFERENCES knowledge_entity(kid) ON DELETE CASCADE,
    representation varchar(16) NOT NULL
        CHECK (representation IN ('full', 'local_name')),
    source_content_hash char(64) NOT NULL,
    embedding vector({{ embedding_dimension }}) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_entity_embedding_representation
        UNIQUE (entity_id, representation)
);

CREATE INDEX IF NOT EXISTS {{ entity_embedding_table_name }}_entity_id_idx
    ON {{ entity_embedding_table_name }} (entity_id);
