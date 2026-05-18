WITH RECURSIVE path_walker AS (
    SELECT
        kid,
        parent_entry_id,
        name,
        is_root,
        CASE
            WHEN is_root THEN '/'
            ELSE '/' || name
        END AS computed_path
    FROM knowledge_fs_entry
    WHERE is_root = true
       OR (parent_entry_id IS NULL AND is_root = false)
    UNION ALL
    SELECT child.kid, child.parent_entry_id, child.name, child.is_root,
           CASE WHEN parent.computed_path = '/'
                THEN '/' || child.name
                ELSE parent.computed_path || '/' || child.name
           END
    FROM knowledge_fs_entry child
    JOIN path_walker parent ON child.parent_entry_id = parent.kid
)
UPDATE knowledge_fs_entry fe
SET virtual_path = pw.computed_path
FROM path_walker pw
WHERE fe.kid = pw.kid
  AND fe.virtual_path IS NULL;
