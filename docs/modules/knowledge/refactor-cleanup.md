# 知识模块重构清理清单

本文档用于记录在按 `docs/modules/knowledge/api.md` 逐个实现接口的过程中，已经确认可删除、可简化、或暂时必须保留的旧函数与旧链路。

更新时间：2026-04-14

## 判断基准

当前对外接口以 `docs/modules/knowledge/api.md` 为唯一准则。现行接口仅包括：

- `knowledgeBases/create`
- `knowledgeBases/update`
- `knowledgeBases/delete`
- `directories/create`
- `directories/update`
- `directories/delete`
- `knowledgeItems/import`
- `knowledgeItems/delete`
- `listDir`
- `glob`
- `readFile`
- `downloadFile`
- `fileToMarkdownIndex`
- `knowledge-items/search`

凡是不再属于上述接口、或者仍然依赖旧模型字段（如 `directory_code`、`file_code`、`version`、`source_code`、`type_code`、`current_version_id`）的实现，都列入清理范围。

## 立即可移除

这些函数已经没有生产代码引用，或者其对应的旧接口已不在当前文档中，后续可以优先删除。

### 1. 未被引用的辅助函数

- `src/by_qa/knowledge_base/services/knowledge_base_service.py`
  - `_path_to_regex`
  - 现状：未被任何生产代码调用。
  - 原因：当前 `glob` 已使用 `_match_pattern_segments` 处理路径匹配，不再使用正则路径转换辅助函数。

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_create_knowledge_base_validation_error`
  - 现状：`knowledgeBases/create` 已切到文档化返回信封后，不再调用该函数。
  - 原因：创建知识库已不再使用旧的 `kb_code` 冲突语义映射。

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_update_knowledge_base_validation_error`
  - 现状：`knowledgeBases/update` 已切到文档化返回信封后，不再调用该函数。
  - 原因：修改知识库已经不再走旧的标准错误信封，也不再使用旧的业务错误码映射。

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_delete_knowledge_base_validation_error`
  - 现状：`knowledgeBases/delete` 已切到文档化返回信封后，不再调用该函数。
  - 原因：删除知识库已经不再走旧的标准错误信封，也不再使用旧的业务错误码映射。

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_create_directory_validation_error`
  - 现状：`directories/create` 已切到文档化返回信封后，不再调用该函数。
  - 原因：创建目录已经不再走旧的标准错误信封，也不再使用 `directory_code` 相关的旧业务错误码映射。

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_update_directory_validation_error`
  - 现状：`directories/update` 已切到文档化返回信封后，不再调用该函数。
  - 原因：修改目录已经不再走旧的标准错误信封，也不再使用 `directory_code` 相关的旧业务错误码映射。

- `src/by_qa/knowledge_base/repositories/retrieval_projection_repository.py`
  - `delete_for_knowledge_base`
  - 现状：删除知识库链路已改为直接操作 `knowledge_chunk_retrieval_mv`，不再调用该函数。
  - 原因：该 repository 仍基于旧投影模型命名保留，但 `delete_for_knowledge_base` 已经没有生产调用。

### 2. 仅服务旧接口的路由与映射函数

- `src/by_qa/knowledge_base/api/routes.py`
  - `update_file`
  - `_map_update_file_validation_error`
  - 对应路径：`/api/v1/knowledge-items/update`
  - 原因：当前接口文档不包含“修改文件元数据”接口。

- `src/by_qa/knowledge_base/api/routes.py`
  - `write_file`
  - `_map_write_file_validation_error`
  - 对应路径：`/api/v1/write-file`
  - 原因：当前接口文档不包含 `write-file`。

- `src/by_qa/knowledge_base/api/routes.py`
  - `write_index`
  - `_map_write_index_validation_error`
  - 对应路径：`/api/v1/write-index`
  - 原因：当前接口文档不包含 `write-index`。

- `src/by_qa/knowledge_base/api/routes.py`
  - `import_knowledge_item`
  - `_map_import_validation_error`
  - 对应路径：`/api/v1/knowledge-items/import`
  - 原因：当前文档中的上传接口是 `/api/v1/knowledgeItems/import`，且要求 `multipart/form-data`；现有这条实现仍是旧的 JSON 导入模型。

### 3. 无生产引用的旧根节点辅助查询

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `list_all_root_nodes`
  - 现状：仅仓库测试涉及，生产代码没有引用。
  - 原因：当前路径模型按 `knCode + 相对路径` 工作，不需要“列出所有虚拟根节点”这一能力。

## 待接口迁移完成后删除

这些函数仍然被当前实现调用，但其存在的前提已经不符合现行接口文档。等对应接口迁移到新路径模型后，可整体删除。

### 1. 旧的“知识库名作为虚拟根目录”链路

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `list_root_entries`
  - `list_root_nodes`
  - `_get_root_by_name`
  - `get_virtual_path_by_entry_id`

- `src/by_qa/knowledge_base/services/knowledge_base_service.py`
  - `_list_by_path_pattern`
  - `_list_directory_entries`
  - `_match_pattern_segments`
  - `_project_node`
  - `_normalize_output_item`
  - `_ensure_leading_slash`
  - `_segment_matches_pattern`
  - `_segment_has_pattern`
  - `_expand_directory_contents`
  - `_resolve_virtual_path`
  - `_with_virtual_full_path`
  - `_all_directories`
  - `_normalize_virtual_path`

- 原因：
  - 当前文档明确规定 `directoryPath`、`filePath` 不包含知识库名称。
  - 以上函数仍然围绕“知识库名称暴露为虚拟根目录”建模。
  - 等 `listDir` / `glob` / `readFile` / `downloadFile` 完全改成基于 `knCode + 相对路径` 后，这整套虚拟根路径处理可整体移除或大幅收缩。

### 2. 旧文档编码 / 版本模型链路

- `src/by_qa/knowledge_base/services/knowledge_item_ingestion_service.py`
  - `write_file`
  - `write_index`
  - `import_knowledge_item`
  - `_decode_file_content`
  - `_derive_type_code`

- `src/by_qa/knowledge_base/repositories/knowledge_item_repository.py`
  - `upsert`
  - `get_by_fs_entry_id`
  - `get_any_by_fs_entry_id`
  - `get_by_item_code`
  - `get_any_by_item_code`
  - `update_current_version`
  - `soft_delete_by_item_code`
  - `soft_delete_by_fs_entry_ids`
  - `update_knowledge_item`

- `src/by_qa/knowledge_base/repositories/knowledge_item_version_repository.py`
  - `upsert`
  - `get_by_item_and_version`

- `src/by_qa/knowledge_base/repositories/knowledge_item_chunk_repository.py`
  - `replace_for_version`
  - `replace_embeddings`

- `src/by_qa/knowledge_base/repositories/retrieval_projection_repository.py`
  - `refresh_for_item`
  - `delete_for_item`
  - `delete_for_fs_entry_ids`

- `src/by_qa/knowledge_base/repositories/knowledge_fetch_cache_repository.py`
  - `get_by_version_id`

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `get_current_file_version_by_entry_id`

- 原因：
  - 这批函数都依赖旧模型字段：`file_code`、`item_code`、`version`、`source_code`、`type_code`、`current_version_id`、`knowledge_item_version_id`。
  - 当前文档只保留了路径模型，不再暴露版本化写入接口，也不再暴露文档业务编码。
  - 需要等 `knowledgeItems/import`、`knowledgeItems/delete`、`readFile`、`downloadFile`、`fileToMarkdownIndex`、`search` 都迁移到新模型后再统一删除。

### 3. 旧字段驱动的目录接口实现

- `src/by_qa/knowledge_base/services/knowledge_base_service.py`
  - `create_directory`
  - `delete_directory`
  - `update_directory`

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `create_directory_entry`
  - `get_directory_by_path`
  - `rename_entry`
  - `list_children`
  - `list_child_nodes`
  - `list_subtree_entry_ids`
  - `soft_delete_subtree`

- 原因：
  - 这些接口仍有保留价值，但当前实现还带着 `directory_code`、`source_code`、虚拟根路径等旧约束。
  - 它们不是“直接删除”，而是“等对应接口重写后，删旧实现、留新实现”。

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `get_directory_by_path`

- 原因：
  - 旧实现按“虚拟根目录路径”解析目录。
  - 现已切到按 `knowledge_base_id + 库内相对路径` 解析。
  - 后续可直接删除旧的虚拟根路径说明和相关兼容语义，只保留当前实现。

### 4. 可删除的旧表字段

- `src/by_qa/knowledge_base/sql/`
  - `knowledge_fs_entry.is_root`

- 原因：
  - 当前路径模型已经不再把知识库作为文件树中的一层目录。
  - 顶层目录和顶层文件现在统一由 `parent_entry_id = NULL` 表达。
  - `knowledge_base_id` 已经足以表达库归属，`is_root` 不再承担主模型语义。
  - 该字段目前仍被旧的 root-node 链路引用，待 `ensure_root_entry`、`list_root_entries`、`list_root_nodes` 和旧导入/读取链路迁移完成后可删除。

## 暂保留

这些函数虽然不属于对外接口概念，但在当前实现阶段仍然承担内部职责，暂时不能删。

### 1. 根目录初始化

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `ensure_root_entry`

- 原因：
  - `knowledgeBases/create` 与 `directories/create` 已经不再依赖它。
  - 当前仍只有旧的文件导入 / 写入链路在使用它来保证目录树根节点存在。
  - 虽然现行接口文档不暴露“根目录节点”概念，但在这些旧链路未迁移完成前，它暂时还不能删。

### 2. 现有知识库查询接口

- `src/by_qa/knowledge_base/repositories/knowledge_base_repository.py`
  - `get_by_code`
  - `soft_delete_by_code`
  - `update_knowledge_base`

- 原因：
  - 它们仍被当前的删除知识库、修改知识库、目录和文件相关服务调用。
  - 只有在这些接口全部改成新字段口径后，才能继续缩减这部分 repository 形态。

## 本轮新增结论

本轮在“创建知识库”接口完成后，已确认：

- “创建目录”接口已经不再依赖知识库 root 节点。
- `knowledge_fs_entry.create_directory_entry` 现已按“库内相对路径”建树：
  - 顶层目录直接使用 `parent_entry_id = NULL`
  - `path_ltree` 不再带 `kb_<id>` 前缀
- `ensure_root_entry` 的使用范围已进一步缩小到旧的文件导入 / 写入链路。
- `knowledge_fs_entry.is_root` 已确认不再属于目标模型字段，后续可随 root-node 旧链路一起移除。
- “修改目录”接口已经按 `knCode + directoryPath + directoryName` 收口，不再依赖 `directory_code`。
- `knowledge_base_service.update_directory` 已经脱离 `knowledge_item_repository`，只基于 `knowledge_fs_entry` 路径模型完成目录重命名。
- 目录修改链路里的 `directory_description`、`metadata` 旧语义已确认废弃。

- 新增名称重复校验后，`knowledgeBases/create` 已不再需要旧的 `kb_code` 创建语义。
- `list_root_entries` / `list_root_nodes` / `list_all_root_nodes` 不符合当前文档路径模型，应进入后续删除范围。
- `ensure_root_entry` 仍是内部实现依赖，先保留。

在“修改知识库”接口完成后，新增确认：

- `knowledgeBases/update` 已改为仅处理 `knCode`、`knName`、`knDescription`，不再保留 `metadata` 更新语义。
- 修改知识库成功响应已收敛为仅返回 `resultCode` / `resultMsg`，不再需要旧的业务响应体。
- `_map_update_knowledge_base_validation_error` 已进入可直接删除范围。

在“删除知识库”接口完成后，新增确认：

- `knowledgeBases/delete` 已改为仅处理 `knCode`，成功响应仅返回 `resultCode` / `resultMsg`。
- `_map_delete_knowledge_base_validation_error` 已进入可直接删除范围。
- 删除知识库链路已不再依赖 `knowledge_item_repository`，也不再调用 `retrieval_projection_repository.delete_for_knowledge_base`。
- 删除知识库当前直接清理 `knowledge_chunk_retrieval_mv`，说明旧 `knowledge_item` 主链路已不再是这条接口的运行前提。

在“创建目录”接口完成后，新增确认：

- `directories/create` 已改为仅处理 `knCode`、`directoryPath`、`directoryDescription`。
- `_map_create_directory_validation_error` 已进入可直接删除范围。
- 创建目录链路已不再依赖 `knowledge_item_repository`，目录元数据直接落到 `knowledge_fs_entry`。
- `create_directory_entry` 已开始适配新表结构，去除了对 `status`、`metadata` 列的依赖。
