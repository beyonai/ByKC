CREATE TABLE IF NOT EXISTS knowledge_chunk (
    kid bigserial PRIMARY KEY,
    fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    chunk_no integer NOT NULL,
    start_line integer NOT NULL,
    end_line integer NOT NULL,
    chunk_text text NOT NULL,
    search_text tsvector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_chunk_file_no
        UNIQUE (fs_entry_id, chunk_no),
    CONSTRAINT chk_knowledge_chunk_line_range
        CHECK (start_line >= 1 AND end_line >= start_line)
);
