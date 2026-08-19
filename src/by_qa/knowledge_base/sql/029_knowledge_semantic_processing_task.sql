-- KnowledgeEntity batch and per-file task storage.
-- Keep knowledge_build_task dedicated to FILE_BUILD and link it additively below.
CREATE TABLE IF NOT EXISTS knowledge_semantic_processing_batch (
    batch_id varchar(64) PRIMARY KEY,
    knowledge_base_id bigint NOT NULL
        REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    task_type varchar(32) NOT NULL,
    scope varchar(16) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'processing',
    total_count integer NOT NULL DEFAULT 0,
    completed_count integer NOT NULL DEFAULT 0,
    version bigint NOT NULL DEFAULT 0,
    extra_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_semantic_batch_task_type
        CHECK (task_type IN ('ENTITY_DISCOVERY', 'DOCUMENT_ENRICH')),
    CONSTRAINT chk_knowledge_semantic_batch_scope
        CHECK (scope IN ('SINGLE_FILE', 'WHOLE_KB')),
    CONSTRAINT chk_knowledge_semantic_batch_status
        CHECK (status IN ('processing', 'completed')),
    CONSTRAINT chk_knowledge_semantic_batch_counts
        CHECK (
            total_count >= 0
            AND completed_count >= 0
            AND completed_count <= total_count
        ),
    CONSTRAINT chk_knowledge_semantic_batch_completion
        CHECK (status <> 'completed' OR completed_count = total_count)
);

CREATE TABLE IF NOT EXISTS knowledge_semantic_processing_task (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL
        REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    fs_entry_id bigint
        REFERENCES knowledge_fs_entry(kid) ON DELETE SET NULL,
    task_type varchar(32) NOT NULL,
    batch_id varchar(64) NOT NULL
        REFERENCES knowledge_semantic_processing_batch(batch_id) ON DELETE CASCADE,
    file_path_snapshot text NOT NULL,
    status varchar(32) NOT NULL,
    current_stage varchar(32),
    progress smallint NOT NULL DEFAULT 0,
    input_fingerprint varchar(128),
    input_checksum varchar(128),
    method_version varchar(64),
    index_version varchar(64),
    request_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    extra_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    result_payload jsonb,
    error_code varchar(64),
    error_message text,
    failure_kind varchar(32),
    outcome_uncertain boolean NOT NULL DEFAULT false,
    worker_id varchar(160),
    lease_token varchar(64),
    heartbeat_at timestamptz,
    lease_expires_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_semantic_task_batch_file
        UNIQUE (batch_id, fs_entry_id),
    CONSTRAINT chk_knowledge_semantic_task_type
        CHECK (task_type IN ('ENTITY_DISCOVERY', 'DOCUMENT_ENRICH')),
    CONSTRAINT chk_knowledge_semantic_task_status
        CHECK (
            status IN (
                'pending',
                'running',
                'succeeded',
                'failed',
                'cancelled',
                'skipped'
            )
        ),
    CONSTRAINT chk_knowledge_semantic_task_progress
        CHECK (progress >= 0 AND progress <= 100),
    CONSTRAINT chk_knowledge_semantic_task_lease
        CHECK (
            status = 'running'
            OR (
                worker_id IS NULL
                AND lease_token IS NULL
                AND heartbeat_at IS NULL
                AND lease_expires_at IS NULL
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_semantic_task_active_per_file
    ON knowledge_semantic_processing_task (fs_entry_id, task_type)
    WHERE status IN ('pending', 'running') AND fs_entry_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_task_claim
    ON knowledge_semantic_processing_task (status, created_at, kid)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_task_expired_lease
    ON knowledge_semantic_processing_task (lease_expires_at, kid)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_task_kb_type_latest
    ON knowledge_semantic_processing_task (
        knowledge_base_id,
        task_type,
        created_at DESC,
        kid DESC
    );

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_task_kb_file_type_latest
    ON knowledge_semantic_processing_task (
        knowledge_base_id,
        fs_entry_id,
        task_type,
        created_at DESC,
        kid DESC
    );

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_task_batch_status
    ON knowledge_semantic_processing_task (batch_id, status, kid);

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_task_idempotency
    ON knowledge_semantic_processing_task (
        task_type,
        fs_entry_id,
        input_fingerprint,
        status
    )
    WHERE input_fingerprint IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_batch_kb_created
    ON knowledge_semantic_processing_batch (
        knowledge_base_id,
        created_at DESC,
        batch_id
    );

ALTER TABLE knowledge_build_task
    ADD COLUMN parent_semantic_task_id bigint
        REFERENCES knowledge_semantic_processing_task(kid) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_build_task_parent_semantic
    ON knowledge_build_task (parent_semantic_task_id)
    WHERE parent_semantic_task_id IS NOT NULL;
