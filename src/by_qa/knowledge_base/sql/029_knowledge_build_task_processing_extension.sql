-- Idempotent upgrade for databases created before knowledge_build_task became
-- the shared per-file processing task registry.
ALTER TABLE knowledge_build_task
    ADD COLUMN IF NOT EXISTS task_type varchar(32) NOT NULL DEFAULT 'FILE_BUILD';

ALTER TABLE knowledge_build_task
    ADD COLUMN IF NOT EXISTS batch_id varchar(64),
    ADD COLUMN IF NOT EXISTS progress smallint,
    ADD COLUMN IF NOT EXISTS input_fingerprint varchar(128),
    ADD COLUMN IF NOT EXISTS input_checksum varchar(128),
    ADD COLUMN IF NOT EXISTS definition_version varchar(64),
    ADD COLUMN IF NOT EXISTS enrich_version varchar(64),
    ADD COLUMN IF NOT EXISTS method_version varchar(64),
    ADD COLUMN IF NOT EXISTS index_version varchar(64),
    ADD COLUMN IF NOT EXISTS request_params jsonb,
    ADD COLUMN IF NOT EXISTS result_payload jsonb,
    ADD COLUMN IF NOT EXISTS error_code varchar(64);

DROP INDEX IF EXISTS uq_knowledge_build_task_running_per_file;

CREATE UNIQUE INDEX uq_knowledge_build_task_running_per_file
    ON knowledge_build_task (fs_entry_id, task_type)
    WHERE status = 'running';

DROP INDEX IF EXISTS idx_knowledge_build_task_latest_by_file;

CREATE INDEX idx_knowledge_build_task_latest_by_file
    ON knowledge_build_task (fs_entry_id, task_type, created_at DESC, kid DESC);

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
