UPDATE knowledge_build_task task
SET origin = 'API',
    execution_mode = 'BACKGROUND',
    file_path_snapshot = fs.virtual_path,
    input_checksum = CASE
        WHEN task.status = 'complete'
         AND fs.checksum IS NOT NULL
         AND fs.is_deleted = false
         AND fs.markdown_bucket_name IS NOT NULL
         AND fs.markdown_object_key IS NOT NULL
         AND EXISTS (
             SELECT 1
             FROM knowledge_chunk chunk
             WHERE chunk.fs_entry_id = task.fs_entry_id
         )
         AND NOT EXISTS (
             SELECT 1
             FROM knowledge_chunk chunk
             LEFT JOIN {{ embedding_table_name }} embedding
               ON embedding.chunk_id = chunk.kid
             LEFT JOIN knowledge_chunk_retrieval_mv retrieval
               ON retrieval.chunk_id = chunk.kid
             WHERE chunk.fs_entry_id = task.fs_entry_id
               AND (
                   embedding.chunk_id IS NULL
                   OR retrieval.chunk_id IS NULL
               )
         )
            THEN fs.checksum
        ELSE NULL
    END,
    input_is_deleted = fs.is_deleted,
    build_profile = '{"legacy":true,"profileVersion":0}'::jsonb,
    build_profile_hash =
        'f84b54d9fa9d1074b14f3f6c2e318315034c92a291a92267162f7c436b12b12f',
    status = CASE
        WHEN task.status = 'complete' THEN 'succeeded'
        WHEN task.status = 'running' THEN 'failed'
        ELSE task.status
    END,
    current_stage = CASE task.current_step
        WHEN 'markdown' THEN 'extracting'
        WHEN 'chunking' THEN 'chunking'
        WHEN 'vectorizing' THEN 'embedding'
        WHEN 'complete' THEN 'committing'
        ELSE 'accepted'
    END,
    progress = CASE
        WHEN task.status IN ('running', 'complete', 'failed', 'unsupported') THEN 100
        WHEN task.current_step = 'vectorizing' THEN 55
        WHEN task.current_step = 'chunking' THEN 35
        WHEN task.current_step = 'markdown' THEN 10
        ELSE 0
    END,
    error_code = CASE
        WHEN task.status = 'running' THEN 'MIGRATION_INTERRUPTED'
        WHEN task.status = 'unsupported' THEN 'UNSUPPORTED_FILE_TYPE'
        WHEN task.status = 'failed' THEN 'BUILD_FAILED'
        ELSE NULL
    END,
    error_message = CASE
        WHEN task.status = 'running'
            THEN 'Build interrupted by background-runner migration'
        ELSE task.error_message
    END,
    failure_kind = CASE
        WHEN task.status = 'running' THEN 'MIGRATION_INTERRUPTED'
        ELSE NULL
    END,
    outcome_uncertain = CASE
        WHEN task.status = 'running' THEN true
        ELSE false
    END,
    finished_at = CASE
        WHEN task.status = 'running' THEN NOW()
        ELSE task.finished_at
    END,
    updated_at = NOW()
FROM knowledge_fs_entry fs
WHERE fs.kid = task.fs_entry_id;
