CREATE TABLE IF NOT EXISTS knowledge_entity (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL
        REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    fs_entry_id bigint NULL UNIQUE
        REFERENCES knowledge_fs_entry(kid) ON DELETE SET NULL,
    canonical_entity_id bigint NULL
        REFERENCES knowledge_entity(kid) ON DELETE CASCADE,
    name_role varchar(16) NOT NULL
        CHECK (name_role IN ('canonical', 'alias')),
    entity_name text NOT NULL,
    normalized_entity_name text NOT NULL,
    local_name text NULL,
    normalized_local_name text NULL,
    subject_entity_id bigint NULL
        REFERENCES knowledge_entity(kid) ON DELETE SET NULL,
    entity_type varchar(64) NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_entity_name_role_shape CHECK (
        (
            name_role = 'canonical'
            AND canonical_entity_id IS NULL
            AND local_name IS NOT NULL
            AND normalized_local_name IS NOT NULL
        )
        OR
        (
            name_role = 'alias'
            AND canonical_entity_id IS NOT NULL
            AND fs_entry_id IS NULL
            AND local_name IS NULL
            AND normalized_local_name IS NULL
            AND subject_entity_id IS NULL
            AND entity_type IS NULL
        )
    ),
    CONSTRAINT uq_knowledge_entity_alias_surface
        UNIQUE (canonical_entity_id, normalized_entity_name)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_entity_normalized_name
    ON knowledge_entity (knowledge_base_id, normalized_entity_name);

CREATE INDEX IF NOT EXISTS idx_knowledge_entity_subject_type
    ON knowledge_entity (knowledge_base_id, subject_entity_id, entity_type)
    WHERE name_role = 'canonical';

CREATE INDEX IF NOT EXISTS idx_knowledge_entity_canonical
    ON knowledge_entity (canonical_entity_id)
    WHERE canonical_entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_entity_fs_entry
    ON knowledge_entity (fs_entry_id)
    WHERE fs_entry_id IS NOT NULL;
