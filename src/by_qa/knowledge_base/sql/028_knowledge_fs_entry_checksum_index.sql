CREATE INDEX IF NOT EXISTS idx_knowledge_fs_entry_kb_checksum_active
    ON knowledge_fs_entry (knowledge_base_id, checksum)
    WHERE entry_type = 'FILE'
      AND is_deleted = false
      AND checksum IS NOT NULL;
