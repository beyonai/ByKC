CREATE TABLE IF NOT EXISTS knowledge_build_task (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    task_type varchar(32) NOT NULL DEFAULT 'FILE_BUILD',
    batch_id varchar(64),
    status varchar(32) NOT NULL,
    current_step varchar(32),
    progress smallint,
    input_fingerprint varchar(128),
    input_checksum varchar(128),
    definition_version varchar(64),
    enrich_version varchar(64),
    method_version varchar(64),
    index_version varchar(64),
    request_params jsonb,
    result_payload jsonb,
    error_code varchar(64),
    error_message text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW()
);

-- The bootstrap SQL is also the in-place migration path for deployments that
-- already have the historical FILE_BUILD-only table.
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
