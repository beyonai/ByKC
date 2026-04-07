CREATE TABLE IF NOT EXISTS {{ embedding_table_name }} (
    kid bigserial PRIMARY KEY,
    chunk_id bigint NOT NULL UNIQUE REFERENCES knowledge_item_chunk(kid) ON DELETE CASCADE,
    embedding vector({{ embedding_dimension }}) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW()
);
