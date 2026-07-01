DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_metadata_value'
          AND column_name = 'property_name'
    ) THEN
        EXECUTE 'ALTER TABLE knowledge_file_metadata_value ADD COLUMN property_name varchar(128)';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_metadata_value'
          AND column_name = 'value_type'
    ) THEN
        EXECUTE 'ALTER TABLE knowledge_file_metadata_value ADD COLUMN value_type varchar(32)';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_metadata_value'
          AND column_name = 'property_def_id'
    ) AND EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_metadata_property_def'
    ) THEN
        EXECUTE '
            UPDATE knowledge_file_metadata_value v
            SET property_name = d.property_name,
                value_type = d.value_type
            FROM knowledge_metadata_property_def d
            WHERE v.property_def_id = d.kid
              AND (v.property_name IS NULL OR v.value_type IS NULL)
        ';
    END IF;
END $$;

UPDATE knowledge_file_metadata_value
SET property_name = COALESCE(property_name, 'unknown'),
    value_type = COALESCE(
        value_type,
        CASE
            WHEN value_string_list IS NOT NULL THEN 'stringList'
            WHEN value_number IS NOT NULL THEN 'number'
            WHEN value_boolean IS NOT NULL THEN 'boolean'
            WHEN value_datetime IS NOT NULL THEN 'datetime'
            ELSE 'string'
        END
    );

ALTER TABLE knowledge_file_metadata_value
ALTER COLUMN property_name SET NOT NULL;

ALTER TABLE knowledge_file_metadata_value
ALTER COLUMN value_type SET NOT NULL;

DROP INDEX IF EXISTS uq_file_metadata_value_active;
DROP INDEX IF EXISTS idx_file_metadata_value_kb_property;
DROP INDEX IF EXISTS idx_file_metadata_value_property_string;
DROP INDEX IF EXISTS idx_file_metadata_value_property_number;
DROP INDEX IF EXISTS idx_file_metadata_value_property_boolean;
DROP INDEX IF EXISTS idx_file_metadata_value_property_datetime;

CREATE UNIQUE INDEX IF NOT EXISTS uq_file_metadata_value_active
ON knowledge_file_metadata_value (fs_entry_id, property_name, value_type)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_kb_property
ON knowledge_file_metadata_value (knowledge_base_id, property_name, value_type)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_property_string
ON knowledge_file_metadata_value (property_name, value_type, value_string)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_property_number
ON knowledge_file_metadata_value (property_name, value_type, value_number)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_property_boolean
ON knowledge_file_metadata_value (property_name, value_type, value_boolean)
WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_file_metadata_value_property_datetime
ON knowledge_file_metadata_value (property_name, value_type, value_datetime)
WHERE is_deleted = false;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_metadata_value'
          AND column_name = 'property_def_id'
    ) THEN
        EXECUTE 'ALTER TABLE knowledge_file_metadata_value DROP COLUMN property_def_id CASCADE';
    END IF;
END $$;

DROP TABLE IF EXISTS knowledge_metadata_property_def CASCADE;
