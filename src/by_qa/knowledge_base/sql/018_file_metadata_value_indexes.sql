CREATE UNIQUE INDEX IF NOT EXISTS uq_file_metadata_value_active
ON knowledge_file_metadata_value (fs_entry_id, property_def_id)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_kb_property
ON knowledge_file_metadata_value (knowledge_base_id, property_def_id)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_property_string
ON knowledge_file_metadata_value (property_def_id, value_string)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_property_number
ON knowledge_file_metadata_value (property_def_id, value_number)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_property_boolean
ON knowledge_file_metadata_value (property_def_id, value_boolean)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_property_datetime
ON knowledge_file_metadata_value (property_def_id, value_datetime)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_string_list_gin
ON knowledge_file_metadata_value
USING GIN (value_string_list)
WHERE is_deleted = false;
