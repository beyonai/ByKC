-- Backfill the required semantic document classification without reviving the
-- removed metadata-property-definition model. Existing active values are
-- authoritative, regardless of their value type, and are never overwritten.
INSERT INTO knowledge_file_metadata_value (
    fs_entry_id,
    knowledge_base_id,
    property_name,
    value_type,
    value_string,
    is_deleted,
    created_at,
    updated_at
)
SELECT
    file_entry.kid,
    file_entry.knowledge_base_id,
    'documentKind',
    'string',
    CASE
        WHEN file_entry.virtual_path = '/KnowledgeEntity'
          OR file_entry.virtual_path LIKE '/KnowledgeEntity/%'
            THEN 'knowledgeEntity'
        ELSE 'original'
    END,
    FALSE,
    NOW(),
    NOW()
FROM knowledge_fs_entry file_entry
WHERE file_entry.entry_type = 'FILE'
  AND file_entry.is_deleted = FALSE
  AND NOT EXISTS (
      SELECT 1
      FROM knowledge_file_metadata_value existing
      WHERE existing.fs_entry_id = file_entry.kid
        AND existing.property_name = 'documentKind'
        AND existing.is_deleted = FALSE
  );
