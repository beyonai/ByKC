CREATE TABLE IF NOT EXISTS knowledge_chunk_retrieval_mv (
    chunk_id bigint PRIMARY KEY,
    knowledge_base_id bigint NOT NULL,
    fs_entry_id bigint NOT NULL,
    full_path text NOT NULL,
    chunk_no integer NOT NULL,
    start_line integer NOT NULL,
    end_line integer NOT NULL,
    chunk_text text NOT NULL,
    search_text tsvector NOT NULL
);
