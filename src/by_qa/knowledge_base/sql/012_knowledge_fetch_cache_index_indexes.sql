CREATE INDEX IF NOT EXISTS idx_fetch_cache_ready_expires
ON knowledge_fetch_cache_index (expires_at)
WHERE cache_status = 'READY';

CREATE INDEX IF NOT EXISTS idx_fetch_cache_status_expires
ON knowledge_fetch_cache_index (cache_status, expires_at);

CREATE INDEX IF NOT EXISTS idx_fetch_cache_fs_entry
ON knowledge_fetch_cache_index (fs_entry_id);

CREATE INDEX IF NOT EXISTS idx_fetch_cache_object_key
ON knowledge_fetch_cache_index (object_key);
