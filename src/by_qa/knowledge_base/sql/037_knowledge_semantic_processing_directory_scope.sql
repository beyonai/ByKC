ALTER TABLE knowledge_semantic_processing_batch
    DROP CONSTRAINT IF EXISTS chk_knowledge_semantic_batch_scope;

ALTER TABLE knowledge_semantic_processing_batch
    ADD CONSTRAINT chk_knowledge_semantic_batch_scope
        CHECK (scope IN ('SINGLE_FILE', 'DIRECTORY', 'WHOLE_KB'));
