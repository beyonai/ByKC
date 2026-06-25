CREATE UNIQUE INDEX IF NOT EXISTS uq_metadata_property_def_name_active
ON knowledge_metadata_property_def (property_name)
WHERE is_deleted = false;
