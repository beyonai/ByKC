DELETE FROM {{ entity_embedding_table_name }}
WHERE representation = 'local_name';

ALTER TABLE {{ entity_embedding_table_name }}
    ADD CONSTRAINT chk_knowledge_entity_embedding_representation_full
    CHECK (representation = 'full');
