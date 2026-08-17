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
