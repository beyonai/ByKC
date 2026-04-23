CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_build_task_running_per_file
    ON knowledge_build_task (fs_entry_id)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_latest_by_file
    ON knowledge_build_task (fs_entry_id, created_at DESC, kid DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_kb_file
    ON knowledge_build_task (knowledge_base_id, fs_entry_id);
