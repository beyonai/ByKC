CREATE INDEX IF NOT EXISTS idx_knowledge_item_version_fs_entry
ON knowledge_item_version (fs_entry_id, version);
