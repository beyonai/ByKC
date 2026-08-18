-- Additive KnowledgeEntity processing-task storage.
-- Keep the historical knowledge_build_task table dedicated to FILE_BUILD.
CREATE TABLE IF NOT EXISTS knowledge_semantic_processing_task (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL
        REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    fs_entry_id bigint NOT NULL
        REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    task_type varchar(32) NOT NULL,
    batch_id varchar(64),
    status varchar(32) NOT NULL,
    current_stage varchar(32),
    progress smallint NOT NULL DEFAULT 0,
    input_fingerprint varchar(128),
    input_checksum varchar(128),
    method_version varchar(64),
    index_version varchar(64),
    request_params jsonb,
    result_payload jsonb,
    error_code varchar(64),
    error_message text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
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
        CHECK (progress >= 0 AND progress <= 100)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_semantic_task_active_per_file
    ON knowledge_semantic_processing_task (fs_entry_id, task_type)
    WHERE status IN ('pending', 'running');

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

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_task_batch
    ON knowledge_semantic_processing_task (batch_id, created_at, kid)
    WHERE batch_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_semantic_task_idempotency
    ON knowledge_semantic_processing_task (
        task_type,
        fs_entry_id,
        input_fingerprint,
        status
    )
    WHERE input_fingerprint IS NOT NULL;
