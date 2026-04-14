CREATE TABLE IF NOT EXISTS knowledge_fs_entry (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    parent_entry_id bigint REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    entry_type varchar(16) NOT NULL,
    is_root boolean NOT NULL DEFAULT false,
    name varchar(512) NOT NULL,
    path_ltree ltree NOT NULL,
    depth integer NOT NULL DEFAULT 0,
    description text,
    file_bucket_name varchar(128),
    file_object_key varchar(1024),
    markdown_bucket_name varchar(128),
    markdown_object_key varchar(1024),
    file_size bigint,
    mime_type varchar(128),
    checksum varchar(128),
    line_count integer,
    is_deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_fs_entry_type
        CHECK (entry_type IN ('DIRECTORY', 'FILE')),
    CONSTRAINT chk_knowledge_fs_entry_root
        CHECK (
            (is_root = true AND parent_entry_id IS NULL AND depth = 0)
            OR
            (
                is_root = false
                AND (
                    (parent_entry_id IS NULL AND depth = 1)
                    OR
                    (parent_entry_id IS NOT NULL AND depth >= 1)
                )
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_sibling_name_active
    ON knowledge_fs_entry (knowledge_base_id, parent_entry_id, name)
    WHERE is_deleted = false;
