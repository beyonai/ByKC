CREATE TABLE IF NOT EXISTS knowledge_fs_entry (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    parent_entry_id bigint REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    entry_type varchar(16) NOT NULL,
    is_root boolean NOT NULL DEFAULT false,
    name varchar(512) NOT NULL,
    full_path text NOT NULL,
    path_ltree ltree NOT NULL,
    depth integer NOT NULL DEFAULT 0,
    status varchar(32) NOT NULL DEFAULT 'ACTIVE',
    is_deleted boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_fs_entry_type
        CHECK (entry_type IN ('DIRECTORY', 'FILE')),
    CONSTRAINT chk_knowledge_fs_entry_status
        CHECK (status IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT chk_knowledge_fs_entry_root
        CHECK (
            (is_root = true AND parent_entry_id IS NULL AND depth = 0)
            OR
            (is_root = false AND depth >= 1)
        ),
    CONSTRAINT chk_knowledge_fs_entry_full_path_root
        CHECK (
            (is_root = true AND full_path = '')
            OR
            (is_root = false AND full_path <> '')
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_full_path_active
    ON knowledge_fs_entry (knowledge_base_id, full_path)
    WHERE is_deleted = false;

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_sibling_name_active
    ON knowledge_fs_entry (knowledge_base_id, parent_entry_id, name)
    WHERE is_deleted = false;
