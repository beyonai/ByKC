CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_fs_entry
ON knowledge_chunk (fs_entry_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_fs_entry_line
ON knowledge_chunk (fs_entry_id, start_line, end_line);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_search_text
ON knowledge_chunk
USING GIN (search_text);
