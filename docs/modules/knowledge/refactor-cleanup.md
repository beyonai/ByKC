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
  - 原因：当前 `glob` 已改为基于库内相对路径逐层匹配，不再使用正则路径转换辅助函数。

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

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_delete_directory_validation_error`
  - 现状：`directories/delete` 已切到文档化返回信封后，不再调用该函数。
  - 原因：删除目录已经不再走旧的标准错误信封，也不再使用 `directory_code` 相关的旧业务错误码映射。

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_list_dir_validation_error`
  - 现状：`listDir` 已切到文档化返回信封后，该函数已删除。
  - 原因：获取目录内容已经不再走旧的标准错误信封，也不再使用旧路径错误码映射。

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_glob_validation_error`
  - 现状：`glob` 已切到文档化返回信封后，该函数已删除。
  - 原因：路径模式匹配已经不再走旧的标准错误信封，也不再使用旧路径错误码映射。

- `src/by_qa/knowledge_base/api/routes.py`
  - `_map_download_file_validation_error`
  - 现状：`downloadFile` 已切到文档化失败响应后，该函数已删除。
  - 原因：下载文件接口已经不再走旧的标准错误信封，也不再使用旧路径错误码映射。

- `src/by_qa/knowledge_base/repositories/retrieval_projection_repository.py`
  - `delete_for_knowledge_base`
  - 现状：删除知识库链路已改为直接操作 `knowledge_chunk_retrieval_mv`，不再调用该函数。
  - 原因：该 repository 仍基于旧投影模型命名保留，但 `delete_for_knowledge_base` 已经没有生产调用。

- `src/by_qa/knowledge_base/services/knowledge_base_service.py`
  - `_list_by_path_pattern`
  - `_list_directory_entries`
  - `_match_pattern_segments`
  - `_project_node`
  - `_segment_matches_pattern`
  - `_segment_has_pattern`
  - `_expand_directory_contents`
  - `_all_directories`
  - 现状：`listDir` / `glob` 已切到新路径模型后，这组虚拟根路径匹配辅助函数已经没有生产调用。
  - 原因：这组函数只服务旧的虚拟根目录匹配和目录展开逻辑。

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
  - 原因：当前接口文档不包含 `write-index`。`fileToMarkdownIndex` 已实现为 file-to-markdown + write-index 的完整替代。

- `src/by_qa/knowledge_base/api/routes.py`
  - `import_knowledge_item`
  - `_map_import_validation_error`
  - 对应路径：`/api/v1/knowledge-items/import`
  - 原因：当前文档中的上传接口已经实现为 `/api/v1/knowledgeItems/import`，且请求体为 `multipart/form-data`；现有这条旧路由仍是 JSON 导入模型，后续可删除。

### 3. 无生产引用的旧根节点辅助查询

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `list_all_root_nodes`
  - 现状：仅仓库测试涉及，生产代码没有引用。
  - 原因：当前路径模型按 `knCode + 相对路径` 工作，不需要”列出所有虚拟根节点”这一能力。

### 4. 知识构建模块对外接口（已被 fileToMarkdownIndex 完全取代）

- `src/by_qa/knowledge_build/api/routes.py`
  - `/api/v1/file-to-markdown` 路由
  - `/api/v1/build-markdown-index` 路由
  - `/api/v1/file-to-markdown-index` 路由
  - `_parse_file_to_markdown` 辅助函数
  - `_build_chunks` 辅助函数
  - `_success_response` 辅助函数
  - `_error_response` 辅助函数
  - `_normalize_file_type` 辅助函数
  - 现状：`fileToMarkdownIndex` 已由 `knowledge_base` 模块实现，从 MinIO 下载已上传文件后内部调用 `DocumentChunkingService` 完成全流程。
  - 原因：知识构建模块的三个对外接口已完全弃用，不再出现在当前接口文档中。

- `src/by_qa/knowledge_build/api/schemas.py`
  - `FileToMarkdownRequest`
  - `FileToMarkdownResponse`
  - `BuildMarkdownIndexRequest`
  - `BuildMarkdownIndexResponse`
  - `FileToMarkdownIndexRequest`（knowledge_build 版本）
  - `FileToMarkdownIndexResponse`
  - 现状：对应的旧路由不再属于对外接口。
  - 原因：新接口的请求模型已在 `knowledge_base/api/schemas.py` 中定义，不再需要 knowledge_build 版本。

## 待接口迁移完成后删除

这些函数仍然被当前实现调用，但其存在的前提已经不符合现行接口文档。等对应接口迁移到新路径模型后，可整体删除。

### 1. 旧的“知识库名作为虚拟根目录”链路

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `list_root_entries`
  - `list_root_nodes`
  - `_get_root_by_name`
  - `get_virtual_path_by_entry_id`

- `src/by_qa/knowledge_base/services/knowledge_base_service.py`
  - `_normalize_output_item`
  - `_ensure_leading_slash`
  - `_resolve_virtual_path`
  - `_with_virtual_full_path`
  - `_normalize_virtual_path`

- 原因：
  - 当前文档明确规定 `directoryPath`、`filePath` 不包含知识库名称。
  - 以上函数仍然围绕“知识库名称暴露为虚拟根目录”建模。
  - `listDir` / `glob` / `downloadFile` / `readFile` 已切到基于 `knCode + 相对路径` 的新模型。
  - 这整套虚拟根路径处理已无生产路由调用，可整体移除。仅因旧 integration test 通过旧 import 链路间接依赖而暂时保留代码。

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

- `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - `ensure_file_entry`

- 原因：
  - 该函数仍依赖旧的 root-entry 模型和旧列写入方式。
  - 当前公开上传接口已经切到 `create_file_entry + update_file_entry_storage` 新链路。
  - 等旧导入链路移除后，这个函数可以一起删除。

- `src/by_qa/knowledge_base/repositories/knowledge_item_chunk_repository.py`
  - `replace_for_version`
  - 现状：`fileToMarkdownIndex` 新链路已使用 `replace_for_fs_entry`，不再调用此方法。
  - 原因：该方法依赖 `knowledge_item_id` 和 `knowledge_item_version_id`，属于旧版本模型。

- `src/by_qa/knowledge_base/repositories/retrieval_projection_repository.py`
  - `refresh_for_item`
  - `delete_for_item`
  - 现状：`fileToMarkdownIndex` 新链路已使用 `refresh_for_fs_entry`，不再调用这两个方法。
  - 原因：这两个方法依赖 `knowledge_item_id`，属于旧版本模型。

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

- `src/by_qa/knowledge_base/services/knowledge_base_service.py`
  - `_list_directory_entries`

- 原因：
  - `listDir` 已不再调用该函数，而是直接基于 `knowledge_base_id + directoryPath` 和 `list_children_by_parent_entry_id` 列出一层子节点。
  - 当前该函数只剩 `glob` 旧虚拟路径链路仍可能间接依赖，等 `glob` 迁完后可删除。

### 4. 可删除的旧表字段

- `src/by_qa/knowledge_base/sql/`
  - `knowledge_fs_entry.is_root`

- 原因：
  - 当前路径模型已经不再把知识库作为文件树中的一层目录。
  - 顶层目录和顶层文件现在统一由 `parent_entry_id = NULL` 表达。
  - `knowledge_base_id` 已经足以表达库归属，`is_root` 不再承担主模型语义。
  - 该字段当前仅因旧的 root-node 链路尚未完全移除而临时保留。
  - 它不是“可选保留字段”，而是“明确待移除字段”。
  - 待 `ensure_root_entry`、`list_root_entries`、`list_root_nodes`、`ensure_file_entry` 和旧导入/读取链路迁移完成后，应直接从 SQL、代码和测试中删除。

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
- `knowledge_fs_entry.is_root` 的状态现已明确为“必须移除”，不再作为候选字段保留。
- “修改目录”接口已经按 `knCode + directoryPath + directoryName` 收口，不再依赖 `directory_code`。
- `knowledge_base_service.update_directory` 已经脱离 `knowledge_item_repository`，只基于 `knowledge_fs_entry` 路径模型完成目录重命名。
- 目录修改链路里的 `directory_description`、`metadata` 旧语义已确认废弃。
- “删除目录”接口已经按 `knCode + directoryPath` 收口，不再依赖 `directory_code`。
- `knowledge_base_service.delete_directory` 已经脱离 `knowledge_item_repository` 和 `retrieval_projection_repository`，改为：
  - 基于 `knowledge_fs_entry` 路径模型定位目录
  - 基于 `list_subtree_entry_ids` + `soft_delete_subtree` 删除目录树
  - 直接删除 `knowledge_chunk_retrieval_mv` 中对应 `fs_entry_id` 的检索投影
- `knowledge_fs_entry.rename_entry` 现已确认必须同步更新整棵子树的 `path_ltree` 前缀。
  - 否则会出现“目录改名后再创建旧名称目录，两个目录共享同一 `path_ltree` 前缀”的冲突问题。
- `knowledgeItems/import` 已按新规范实现：
  - 路由为 `/api/v1/knowledgeItems/import`
  - 请求体为 `multipart/form-data`
  - 仅上传原始文件并写入 `knowledge_fs_entry` 的文件对象元信息
  - 不再依赖 `knowledge_item_repository`、`knowledge_item_version_repository`、`knowledge_item_chunk_repository`
- `knowledge_fs_entry` 新增并启用了两条当前主链路方法：
  - `create_file_entry`
  - `update_file_entry_storage`
- `create_file_entry` 现已与 `create_directory_entry` 保持一致，支持递归创建缺失父目录。
- 包内 SQL 已同步适配当前路径模型：
  - `knowledge_fs_entry` 顶层节点允许 `parent_entry_id = NULL` 且 `depth = 1`

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

在“删除文档”接口完成后，新增确认：

- `knowledgeItems/delete` 已改为仅处理 `knCode`、`filePath`，不再保留 `file_code` 语义。
- 删除文档主链路已改为基于 `knowledge_fs_entry.get_file_by_path` 按库内相对路径定位文件节点。
- 删除文档已不再依赖 `knowledge_item_repository`、`retrieval_projection_repository`。
- 删除文档当前直接处理：
  - `knowledge_fs_entry` 的逻辑删除
  - `knowledge_chunk_retrieval_mv` 中对应 `fs_entry_id` 的检索投影删除
  - `knowledge_fetch_cache_index` 中对应 `fs_entry_id` 的缓存索引删除
  - MinIO 中原始文件与 markdown 文件对象的清理
- `knowledge_fs_entry.get_file_by_path` 已进入当前主链路，后续可作为 `readFile`、`downloadFile` 等接口的统一路径定位基础。

在“获取目录内容”接口完成后，新增确认：

- `listDir` 已改为仅处理 `knCode`、`directoryPath`，不再保留 `kb_codes`、`path`、虚拟知识库根目录语义。
- `listDir` 已移除 `source_codes`、`type_codes` 旧过滤字段。
- `listDir` 返回已改为文档化信封，并通过 `resultObject.data` 返回目录项。
- `knowledge_base_service.list_dir` 已从 `list_root_entries` / `list_root_nodes` 旧链路切换到：
  - `knowledge_base_repository.get_by_code`
  - `knowledge_fs_entry_repository.get_directory_by_path`
  - `knowledge_fs_entry_repository.list_children_by_parent_entry_id`
- `knowledge_fs_entry_repository.list_children_by_parent_entry_id` 已进入当前主链路，并且只依赖 `knowledge_fs_entry` 当前字段，不再 join `knowledge_item` / `knowledge_item_version`。
- `_map_list_dir_validation_error` 已删除。

在“按路径模式匹配”接口完成后，新增确认：

- `glob` 已改为仅处理 `knCode`、`pathRule`，不再保留 `kb_codes`、`path`、`source_codes`、`type_codes` 旧字段。
- `glob` 返回已改为文档化信封，并通过 `resultObject.data` 返回匹配项。
- `glob` 匹配规则已收敛为：
  - `*` 只匹配单层路径段
  - 不支持 `**` 多层目录匹配
- `knowledge_base_service.glob` 已从 `list_root_nodes` / 虚拟根路径链路切换到：
  - `knowledge_base_repository.get_by_code`
  - `knowledge_fs_entry_repository.list_children_by_parent_entry_id`
- `_map_glob_validation_error` 已删除。

在“下载原始文件”接口完成后，新增确认：

- `downloadFile` 已改为仅处理 `knCode`、`filePath`，不再保留 `kb_codes`、`path` 旧字段。
- `downloadFile` 正常返回保持文件流，不使用 JSON 信封。
- `downloadFile` 失败返回已改为文档化信封。
- `knowledge_base_service.download_file` 已从 `get_current_file_version_by_entry_id` / 虚拟根路径链路切换到：
  - `knowledge_base_repository.get_by_code`
  - `knowledge_fs_entry_repository.get_file_by_path`
  - `knowledge_fs_entry.file_bucket_name`
  - `knowledge_fs_entry.file_object_key`
  - `knowledge_fs_entry.mime_type`
- `_map_download_file_validation_error` 已删除。

在"知识构建"接口完成后，新增确认：

- `fileToMarkdownIndex` 已按新规范实现，路由为 `/api/v1/fileToMarkdownIndex`。
- 入参为 `knCode` + `filePath`，不再接受 base64 文件内容，改为从 MinIO 读取已上传文件。
- 处理流程为：定位文件 → 下载原始文件 → 解析为 Markdown → 切片 + 向量化 → 持久化 chunk/embedding → 刷新检索投影。
- 新链路完全基于 `fs_entry_id`，不依赖 `knowledge_item`、`knowledge_item_version`、`file_code`、`version` 旧模型。
- `knowledge_chunk` 写入使用新增的 `replace_for_fs_entry`，不再使用 `replace_for_version`。
- `knowledge_chunk_retrieval_mv` 刷新使用新增的 `refresh_for_fs_entry`，不再使用 `refresh_for_item`。
- `knowledge_fs_entry` 新增 `update_markdown_metadata` 用于记录 Markdown 文件元信息。
- `DocumentChunkingService` 现在由 `knowledge_base` 模块通过依赖注入使用，不再需要 `knowledge_build` 模块的对外接口。
- `knowledge_build` 模块的三个对外接口（`file-to-markdown`、`build-markdown-index`、`file-to-markdown-index`）已完全弃用。
- `knowledge_base` 模块的 `write-index` 路由和 `write_index` 服务方法已完全被 `fileToMarkdownIndex` 取代。

在"读取文件"接口完成后，新增确认：

- `readFile` 已改为仅处理 `knCode`、`filePath`、`startLine`、`endLine`，不再保留 `kb_codes`、`path`、`content_type` 旧字段。
- `readFile` 仅读取已构建的 Markdown 文件，不再支持 `content_type: "original"` 回退到原始文件 URL。
- 文件未构建时返回 `"file not built: {filePath}"` 错误，而非静默回退。
- `readFile` 返回已改为文档化信封（`resultCode` / `resultMsg` / `resultObject`）。
- `knowledge_base_service.read_file` 已从 `_resolve_virtual_path` / `get_current_file_version_by_entry_id` / 虚拟根路径链路切换到：
  - `knowledge_base_repository.get_by_code`
  - `knowledge_fs_entry_repository.get_file_by_path`
  - `knowledge_fs_entry.markdown_bucket_name`
  - `knowledge_fs_entry.markdown_object_key`
- `_map_read_file_validation_error` 已删除。
- 旧的 `fetch` 方法和 `KnowledgeItemFetchRequest` / `KnowledgeItemFetchResponse` 暂时保留，因其他 integration test 仍通过旧 import 链路间接使用。待旧 import 链路迁移完成后可一并删除。
- 所有旧的"知识库名作为虚拟根目录"链路（`_resolve_virtual_path`、`_normalize_virtual_path`、`_with_virtual_full_path`、`list_root_nodes`、`list_root_entries`）已无生产路由调用，可进入移除范围。
