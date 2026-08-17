CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_build_task_running_per_file
    ON knowledge_build_task (fs_entry_id, task_type)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_latest_by_file
    ON knowledge_build_task (fs_entry_id, task_type, created_at DESC, kid DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_kb_file
    ON knowledge_build_task (knowledge_base_id, fs_entry_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_kb_type_latest
    ON knowledge_build_task (
        knowledge_base_id,
        task_type,
        created_at DESC,
        kid DESC
    );

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_kb_file_type_latest
    ON knowledge_build_task (
        knowledge_base_id,
        fs_entry_id,
        task_type,
        created_at DESC,
        kid DESC
    );

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_batch
    ON knowledge_build_task (batch_id, created_at, kid)
    WHERE batch_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_idempotency
    ON knowledge_build_task (task_type, fs_entry_id, input_fingerprint, status)
    WHERE input_fingerprint IS NOT NULL;
