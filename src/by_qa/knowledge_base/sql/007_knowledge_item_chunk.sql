CREATE TABLE IF NOT EXISTS knowledge_item_chunk (
    kid bigserial PRIMARY KEY,
    knowledge_item_id bigint NOT NULL REFERENCES knowledge_item(kid) ON DELETE CASCADE,
    knowledge_item_version_id bigint NOT NULL REFERENCES knowledge_item_version(kid) ON DELETE CASCADE,
    chunk_no integer NOT NULL,
    start_line integer NOT NULL,
    end_line integer NOT NULL,
    char_start integer,
    char_end integer,
    chunk_text text NOT NULL,
    search_text tsvector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_item_chunk_version_no
        UNIQUE (knowledge_item_version_id, chunk_no),
    CONSTRAINT chk_knowledge_item_chunk_line_range
        CHECK (start_line >= 1 AND end_line >= start_line)
);
