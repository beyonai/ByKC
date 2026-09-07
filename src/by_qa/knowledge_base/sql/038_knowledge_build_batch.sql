CREATE TABLE knowledge_build_batch (
    batch_id varchar(64) PRIMARY KEY,
    knowledge_base_id bigint NOT NULL
        REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    task_type varchar(32) NOT NULL DEFAULT 'FILE_BUILD',
    scope varchar(16) NOT NULL,
    target_path_snapshot text NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'pending',
    candidate_count bigint NOT NULL DEFAULT 0,
    eligible_count bigint NOT NULL DEFAULT 0,
    accepted_count bigint NOT NULL DEFAULT 0,
    reused_count bigint NOT NULL DEFAULT 0,
    acceptance_skipped_count bigint NOT NULL DEFAULT 0,
    completed_count bigint NOT NULL DEFAULT 0,
    version bigint NOT NULL DEFAULT 0,
    extra_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_build_batch_task_type
        CHECK (task_type = 'FILE_BUILD'),
    CONSTRAINT chk_knowledge_build_batch_scope
        CHECK (scope IN ('SINGLE_FILE', 'DIRECTORY')),
    CONSTRAINT chk_knowledge_build_batch_status
        CHECK (status IN ('pending', 'processing', 'completed')),
    CONSTRAINT chk_knowledge_build_batch_counts
        CHECK (
            candidate_count >= 0
            AND eligible_count >= 0
            AND accepted_count >= 0
            AND reused_count >= 0
            AND acceptance_skipped_count >= 0
            AND completed_count >= 0
            AND candidate_count = (
                accepted_count + reused_count + acceptance_skipped_count
            )
            AND eligible_count = accepted_count + reused_count
            AND completed_count <= accepted_count
        ),
    CONSTRAINT chk_knowledge_build_batch_completion
        CHECK (status <> 'completed' OR completed_count = accepted_count)
);

CREATE INDEX idx_knowledge_build_batch_kb_created
    ON knowledge_build_batch (knowledge_base_id, created_at DESC, batch_id);

CREATE INDEX idx_knowledge_build_batch_status_created
    ON knowledge_build_batch (status, created_at, batch_id)
    WHERE status IN ('pending', 'processing');
