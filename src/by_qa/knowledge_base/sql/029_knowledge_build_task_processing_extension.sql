-- Idempotent upgrade for databases created before knowledge_build_task became
-- the shared per-file processing task registry.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'task_type'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN task_type varchar(32);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'batch_id'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN batch_id varchar(64);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'progress'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN progress smallint;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'input_fingerprint'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN input_fingerprint varchar(128);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'input_checksum'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN input_checksum varchar(128);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'definition_version'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN definition_version varchar(64);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'enrich_version'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN enrich_version varchar(64);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'method_version'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN method_version varchar(64);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'index_version'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN index_version varchar(64);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'request_params'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN request_params jsonb;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'result_payload'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN result_payload jsonb;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_build_task'
          AND column_name = 'error_code'
    ) THEN
        ALTER TABLE knowledge_build_task ADD COLUMN error_code varchar(64);
    END IF;
END $$;

UPDATE knowledge_build_task
SET task_type = 'FILE_BUILD'
WHERE task_type IS NULL;

ALTER TABLE knowledge_build_task
    ALTER COLUMN task_type SET DEFAULT 'FILE_BUILD';

ALTER TABLE knowledge_build_task
    ALTER COLUMN task_type SET NOT NULL;

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
