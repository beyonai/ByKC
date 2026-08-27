ALTER TABLE knowledge_semantic_processing_task
    ADD COLUMN protocol_version varchar(64) NULL;

ALTER TABLE knowledge_entity
    ADD COLUMN object_kind varchar(16) NOT NULL DEFAULT 'ENTITY',
    ADD COLUMN description text NULL;

ALTER TABLE knowledge_entity
    DROP CONSTRAINT IF EXISTS knowledge_entity_subject_entity_id_fkey;

ALTER TABLE knowledge_entity
    ADD CONSTRAINT knowledge_entity_subject_entity_id_fkey
    FOREIGN KEY (subject_entity_id)
    REFERENCES knowledge_entity(kid)
    ON DELETE CASCADE;

ALTER TABLE knowledge_entity
    DROP CONSTRAINT IF EXISTS chk_knowledge_entity_name_role_shape;

ALTER TABLE knowledge_entity
    DROP COLUMN local_name,
    DROP COLUMN normalized_local_name;

ALTER TABLE knowledge_entity
    ADD CONSTRAINT chk_knowledge_entity_name_role_shape CHECK (
        (
            name_role = 'canonical'
            AND canonical_entity_id IS NULL
        )
        OR
        (
            name_role = 'alias'
            AND canonical_entity_id IS NOT NULL
            AND fs_entry_id IS NULL
            AND subject_entity_id IS NULL
            AND entity_type IS NULL
        )
    );

ALTER TABLE knowledge_entity
    ADD CONSTRAINT chk_knowledge_entity_object_kind CHECK (
        (object_kind = 'ENTITY')
        OR
        (
            object_kind = 'TOPIC'
            AND name_role = 'canonical'
            AND canonical_entity_id IS NULL
            AND fs_entry_id IS NULL
            AND subject_entity_id IS NOT NULL
            AND entity_type IS NULL
        )
    );

ALTER TABLE knowledge_entity
    ADD CONSTRAINT chk_knowledge_entity_description_shape CHECK (
        description IS NULL
        OR (
            object_kind = 'ENTITY'
            AND name_role = 'canonical'
        )
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_entity_topic_owner_name
    ON knowledge_entity (
        knowledge_base_id,
        subject_entity_id,
        normalized_entity_name
    )
    WHERE object_kind = 'TOPIC';

CREATE INDEX IF NOT EXISTS idx_knowledge_entity_object_kind
    ON knowledge_entity (knowledge_base_id, object_kind, kid);
