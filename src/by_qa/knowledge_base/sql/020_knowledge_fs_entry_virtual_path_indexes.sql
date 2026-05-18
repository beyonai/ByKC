CREATE INDEX IF NOT EXISTS idx_knowledge_fs_entry_virtual_path
    ON knowledge_fs_entry (virtual_path text_pattern_ops)
    WHERE is_deleted = false;
