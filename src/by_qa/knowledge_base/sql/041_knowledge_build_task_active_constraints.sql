ALTER TABLE knowledge_build_task
    ALTER COLUMN origin SET NOT NULL,
    ALTER COLUMN execution_mode SET NOT NULL,
    ALTER COLUMN file_path_snapshot SET NOT NULL,
    ALTER COLUMN input_is_deleted SET NOT NULL,
    ALTER COLUMN build_profile SET NOT NULL,
    ALTER COLUMN build_profile_hash SET NOT NULL,
    ALTER COLUMN current_stage SET NOT NULL;

ALTER TABLE knowledge_build_task
    ADD CONSTRAINT chk_knowledge_build_task_origin
        CHECK (origin IN ('API', 'ENTITY_DISCOVERY', 'ENTITY_ENRICH')),
    ADD CONSTRAINT chk_knowledge_build_task_execution_mode
        CHECK (execution_mode IN ('BACKGROUND', 'INLINE')),
    ADD CONSTRAINT chk_knowledge_build_task_status
        CHECK (
            status IN (
                'pending',
                'running',
                'succeeded',
                'failed',
                'skipped',
                'unsupported',
                'complete'
            )
            AND (
                status <> 'complete'
                OR build_profile @> '{"legacy":true}'::jsonb
            )
        ),
    ADD CONSTRAINT chk_knowledge_build_task_stage
        CHECK (
            current_stage IN (
                'accepted',
                'extracting',
                'chunking',
                'embedding',
                'committing'
            )
        ),
    ADD CONSTRAINT chk_knowledge_build_task_progress
        CHECK (progress >= 0 AND progress <= 100),
    ADD CONSTRAINT chk_knowledge_build_task_profile_hash
        CHECK (char_length(build_profile_hash) = 64),
    ADD CONSTRAINT chk_knowledge_build_task_lease
        CHECK (
            (
                status = 'running'
                AND (
                    (
                        execution_mode = 'BACKGROUND'
                        AND worker_id IS NOT NULL
                        AND lease_token IS NOT NULL
                        AND heartbeat_at IS NOT NULL
                        AND lease_expires_at IS NOT NULL
                    )
                    OR (
                        execution_mode = 'INLINE'
                        AND worker_id IS NULL
                        AND lease_token IS NULL
                        AND heartbeat_at IS NULL
                        AND lease_expires_at IS NULL
                    )
                )
            )
            OR (
                status <> 'running'
                AND worker_id IS NULL
                AND lease_token IS NULL
                AND heartbeat_at IS NULL
                AND lease_expires_at IS NULL
            )
        ),
    ADD CONSTRAINT chk_knowledge_build_task_protocol_shape
        CHECK (
            build_profile @> '{"legacy":true}'::jsonb
            OR (
                input_checksum IS NOT NULL
                AND file_path_snapshot <> ''
                AND (
                    (
                        execution_mode = 'BACKGROUND'
                        AND origin = 'API'
                        AND batch_id IS NOT NULL
                        AND parent_semantic_task_id IS NULL
                    )
                    OR (
                        execution_mode = 'INLINE'
                        AND origin IN ('ENTITY_DISCOVERY', 'ENTITY_ENRICH')
                        AND batch_id IS NULL
                        AND parent_semantic_task_id IS NOT NULL
                    )
                )
            )
        );

DROP INDEX IF EXISTS uq_knowledge_build_task_running_per_file;

CREATE UNIQUE INDEX uq_knowledge_build_task_active_per_file
    ON knowledge_build_task (fs_entry_id)
    WHERE status IN ('pending', 'running');

CREATE INDEX idx_knowledge_build_task_claim
    ON knowledge_build_task (priority DESC, created_at, kid)
    WHERE status = 'pending' AND execution_mode = 'BACKGROUND';

CREATE INDEX idx_knowledge_build_task_expired_lease
    ON knowledge_build_task (lease_expires_at, kid)
    WHERE status = 'running' AND execution_mode = 'BACKGROUND';

CREATE INDEX idx_knowledge_build_task_batch_status
    ON knowledge_build_task (batch_id, status, kid)
    WHERE batch_id IS NOT NULL;

CREATE INDEX idx_knowledge_build_task_reuse
    ON knowledge_build_task (
        fs_entry_id,
        input_checksum,
        input_is_deleted,
        build_profile_hash,
        status,
        created_at DESC,
        kid DESC
    );
