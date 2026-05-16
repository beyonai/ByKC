# 接口级集成测试场景方案

本文档从用户旅程出发，整理 `knowledge_base` 的接口级集成测试场景，方便直接查看”有哪些场景、覆盖了什么、哪些已经落代码”。

> **注意：** 所有路由已统一使用 camelCase URL，响应统一使用 `resultCode`/`resultMsg`/`resultObject` 信封格式。`knowledge_build` 独立路由已全部移除，构建功能整合到 `/api/v1/fileToMarkdownIndex`。

参考依据：

- `docs/modules/knowledge/api.md`
- `src/by_qa/knowledge_base/api/routes.py`
- `src/by_qa/knowledge_base/api/schemas.py`

说明：

- `状态` 分为 `已写`、`已写部分`、`待补`、`已弃用`
- `已写` 表示当前仓库已经有对应集成测试代码
- `已写部分` 表示该用户场景只覆盖了其中一部分链路
- `已弃用` 表示对应路由已移除，场景不再适用
- 本轮只写方案和测试代码，不执行测试

## 多级目录专项场景总表

说明：

- 这组场景专门看多级目录树，不只看单层目录
- 重点验证祖先节点、父节点、子节点、孙节点之间的状态联动

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| D1 | 目录管理员 | 创建三级目录树 | `knowledgeBases/create -> create /A -> create /A/B -> create /A/B/C -> listDir(kb root) -> listDir(/A) -> listDir(/A/B)` | 每层只返回直接子节点；祖先层级可逐级展开；路径结构稳定 | 已写 |
| D2 | 目录管理员 | 在多级目录最深层导入文件 | `create /A/B/C -> knowledgeItems/import(/A/B/C/file.md) -> listDir(/A/B/C) -> glob(A/**)` | 深层文件能被准确列出和匹配，祖先层路径解析正常 | 已写 |
| D3 | 目录管理员 | 重命名中间层目录并联动整棵子树 | `create /A/B/C -> knowledgeItems/import -> rename B to B2 -> listDir(/A) -> listDir(/A/B2) -> listDir(/A/B2/C) -> readFile old/new -> glob old/new` | 中间层改名后，所有后代路径同步变化；旧路径全失效；新路径全生效 | 已写 |
| D4 | 目录管理员 | 删除中间层目录并删除整棵子树 | `create /A/B/C -> knowledgeItems/import -> delete B -> listDir(/A) -> listDir(/A/B) -> readFile -> knowledgeItems/search` | 删除中间层后，`B` 及其所有后代一起消失 | 已写 |
| D5 | 目录管理员 | 多级目录同级重名冲突 | `create /A/B1 -> create /A/B2 -> rename B2 to B1` | 返回 `KB_DIRECTORY_NAME_CONFLICT`；祖先和后代结构保持原样 | 已写 |
| D6 | 普通使用者 | 多级目录 glob/读取一致 | `create multi-level tree -> knowledgeItems/import at different levels -> glob(pattern) -> readFile` | `glob` 命中的任意路径都能被 `readFile` 读取（需先通过 `fileToMarkdownIndex` 构建）；深层路径无歧义 | 已写 |

## knowledge_base 场景总表

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | 知识库管理员 | 创建空知识库 | `knowledgeBases/create -> listDir(/)` | 创建后根层级可见；重复创建冲突；非法请求报标准错误 | 已写 |
| 2 | 知识库管理员 | 修改知识库基础信息 | `knowledgeBases/create -> knowledgeBases/update -> listDir(/)` | 修改 `kb_name` 后根目录名称同步变化；旧名称路径失效 | 已写 |
| 3 | 知识库管理员 | 删除知识库 | `knowledgeBases/create -> directories/create -> knowledgeItems/import -> knowledgeBases/delete -> listDir(/) -> readFile -> knowledgeItems/search` | 删除后根层级不可见；文件不可读；内容不可检索 | 已写 |
| 4 | 目录管理员 | 创建单层目录 | `knowledgeBases/create -> directories/create -> listDir(kb root)` | 父层级能看到新目录；重复路径冲突；父目录缺失时报错 | 已写 |
| 5 | 目录管理员 | 创建多层目录树 | `create /A -> create /A/B -> create /A/B/C -> listDir(/A) -> listDir(/A/B) -> glob(A/*)` | 每层只返回直接子节点；`glob` 与目录结构一致 | 已写 |
| 6 | 目录管理员 | 目录改名影响整棵子树 | `create parent -> create child -> knowledgeItems/import -> update child name -> listDir/glob/readFile old&new` | 新路径生效；旧路径失效；子文件随目录路径变化 | 已写 |
| 7 | 目录管理员 | 删除非空目录 | `create dir -> knowledgeItems/import -> directories/delete -> listDir -> glob -> readFile -> knowledgeItems/search` | 整个子树从浏览、读取、检索里一起消失 | 已写 |
| 8 | 目录管理员 | 目录同级重名冲突 | `create /A/B1 -> create /A/B2 -> update B2 to B1 -> listDir(/A)` | 返回 `KB_DIRECTORY_NAME_CONFLICT`；目录树保持不变 | 已写 |
| 9 | 内容管理员 | 导入单文件并构建索引 | `knowledgeBases/create -> directories/create -> knowledgeItems/import -> fileToMarkdownIndex -> listDir -> readFile(markdown) -> downloadFile(original) -> knowledgeItems/search` | 导入后通过 `fileToMarkdownIndex` 构建；接口成功受理后最终可读、可下载、可检索 | 已写 |
| 9A | 内容管理员 | 查询异步构建状态 | `knowledgeItems/import -> fileToMarkdownIndex -> fileBuildStatus` | `fileToMarkdownIndex` 立即返回受理成功；`fileBuildStatus` 返回 `status/currentStep`，构建完成后为 `complete/complete`，并携带 `statusDict/stepDict` | 已写 |
| 9B | 内容管理员 | 构建中重复提交同一文件 | `knowledgeItems/import -> fileToMarkdownIndex(first running) -> fileBuildStatus -> fileToMarkdownIndex(second)` | 首次请求创建 `running` 任务；状态查询返回 `running`；重复提交返回 `resultCode=-1` 和“已有构建任务”错误提示 | 已写 |
| 9C | 内容管理员 | 构建失败后重新触发构建 | `knowledgeItems/import -> fileToMarkdownIndex(fail) -> fileBuildStatus -> fileToMarkdownIndex(retry) -> fileBuildStatus` | 失败后状态查询返回 `failed`；再次触发允许重建；重试成功后状态变为 `complete/complete` | 已写 |
| 10 | 内容管理员 | ~~用真实知识构建结果分步写入单文件~~ | ~~`write-file -> write-index`~~ | ~~`write-file`/`write-index` 路由已移除，分步写入链路不再存在~~ | 已弃用 |
| 11 | 内容管理员 | ~~比较原子导入与分步写入的最终行为~~ | ~~`write-file -> write-index` 对比 `knowledgeItems/import`~~ | ~~`write-file`/`write-index` 路由已移除，无需比较~~ | 已弃用 |
| 12 | 内容管理员 | 路径绑定冲突 | `knowledgeItems/import A:/x.md -> knowledgeItems/import B:/x.md` | 第二次写入失败；原绑定不变 | 已写 |
| 13 | 内容管理员 | 删除单文件 | `knowledgeItems/import -> listDir -> readFile -> knowledgeItems/delete -> listDir -> readFile -> knowledgeItems/search` | 删除后目录不可见、文件不可读、内容不可检索 | 已写 |
| 14 | 内容管理员 | 软删除路径占用 | `knowledgeItems/import -> knowledgeItems/delete -> knowledgeItems/import same path` | 已改为基于路径的模型；验证软删除后重新导入同路径的行为 | 已写 |
| 15 | 普通使用者 | 根目录浏览 | `create multiple kb -> listDir(/)` | 返回所有知识库根节点，名称正确 | 已写 |
| 16 | 普通使用者 | 多层目录浏览 | `create tree -> listDir(root) -> listDir(child) -> listDir(file path)` | 目录返回直接子项；文件路径按约定返回单文件结果 | 已写 |
| 17 | 普通使用者 | glob 模式浏览 | `knowledgeItems/import -> glob(pattern) -> rename/delete -> glob(pattern again)` | 匹配结果与目录结构一致；状态变化后同步变化 | 已写 |
| 18 | 普通使用者 | 读取 markdown 全量内容 | `knowledgeItems/import -> fileToMarkdownIndex -> readFile(full)` | 返回完整 markdown（需先构建）；`reached_eof=true` | 已写 |
| 19 | 普通使用者 | 读取 markdown 行窗口 | `knowledgeItems/import -> fileToMarkdownIndex -> readFile(startLine,endLine)` | 返回指定行范围；`reached_eof` 正确；非法窗口报错；未构建时返回 "file not built" 错误 | 已写 |
| 20 | 普通使用者 | 读取构建后的 markdown / 下载原文件 | `knowledgeItems/import -> fileToMarkdownIndex -> readFile` 读取已构建 markdown；`downloadFile` 获取原始文件 | `readFile` 仅返回已构建的 markdown（未构建时返回 "file not built" 错误）；原始文件通过 `downloadFile` 下载 | 已写 |
| 20A | 普通使用者 | 下载中文文件名的 Markdown 原文件 | `knowledgeItems/import(中文文件名) -> downloadFile` | 返回原始字节流；`Content-Disposition` 对非 ASCII 文件名安全；`Content-Type=text/markdown` | 已写 |
| 20B | 普通使用者 | 下载二进制 PDF 原文件 | `knowledgeItems/import(pdf) -> fileToMarkdownIndex -> downloadFile` | 返回原始 PDF 字节流；`Content-Type=application/pdf`；下载文件名正确 | 已写 |
| 21 | 检索使用者 | 单文件命中检索 | `knowledgeItems/import -> fileToMarkdownIndex -> knowledgeItems/search` | 返回对应 chunk；路径、版本、chunk 编号正确 | 已写 |
| 22 | 检索使用者 | 过滤条件检索 | `knowledgeItems/import multiple files -> fileToMarkdownIndex -> knowledgeItems/search with knCodeList/source/type filters` | 仅返回符合过滤条件的结果 | 已写 |
| 23 | 检索使用者 | 删除后的检索收敛 | `knowledgeItems/import -> fileToMarkdownIndex -> knowledgeItems/search hit -> knowledgeItems/delete -> knowledgeItems/search again` | 已删除内容不再命中 | 已写 |
| 24 | 检索使用者 | 目录改名后的检索路径更新 | `knowledgeItems/import -> fileToMarkdownIndex -> knowledgeItems/search -> directories/update -> knowledgeItems/search again` | 内容仍命中，但 `filePath` 更新为新路径 | 已写 |
| 25 | 跨接口一致性 | 浏览、读取、检索一致 | `knowledgeItems/import -> fileToMarkdownIndex -> listDir -> readFile -> knowledgeItems/search` | 可见文件一定可读（已构建）；搜索结果路径可被读取 | 已写 |
| 26 | 跨接口一致性 | ~~原子导入与分步写入行为一致~~ | ~~`write-file -> write-index` 对比 `knowledgeItems/import`~~ | ~~`write-file`/`write-index` 路由已移除，分步写入链路不再存在~~ | 已弃用 |
| 27 | 跨接口一致性 | 改名或删除后的全局一致性 | `rename/delete -> listDir -> glob -> readFile -> knowledgeItems/search` | 所有读接口观察到的状态一致 | 已写 |
| 28 | 异常与恢复 | 请求参数不合法 | 覆盖缺少必填、空字符串、重复 `chunk_no`、非法 line window 等 | 返回统一请求校验或业务校验错误 | 已写 |
| 29 | 异常与恢复 | 运行时依赖未配置 | 覆盖 KB runtime/fetch runtime/embedding 配置缺失 | 返回 `configuration_error` 风格错误 | 已写 |
| 30 | 异常与恢复 | 构建或落库失败不留下半成功状态 | `knowledgeItems/import failure` 或 `fileToMarkdownIndex failure` | 不留下可见但不可读、可检索但不可读等异常状态 | 已写 |

## 元数据与 DSL 检索场景总表

说明：

- 这一组场景覆盖元数据属性定义、文件元数据增量更新、纯元数据检索、DSL 升级版 chunk/file 检索的端到端调用链。
- 系统字段（`fileName`/`fileType`/`fileSize`/`mimeType`/`createdAt`/`updatedAt`）不需要 `metadataProperties/create`，但其余自定义属性必须先注册再使用。

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M1 | 元数据管理员 | 属性定义生命周期 | `metadataProperties/create -> metadataProperties/list -> metadataProperties/delete -> metadataProperties/list` | 创建后可见；重复创建冲突；系统字段同名拒绝；删除后从 list 中消失 | 待补 |
| M2 | 元数据管理员 | 批量创建原子性 | `metadataProperties/batchCreate (含一项与既有冲突)` | 整批回滚，不留下任何成功项 | 待补 |
| M3 | 元数据管理员 | 引用计数保护 | `metadataProperties/create -> knowledgeItems/metadata/update set -> metadataProperties/delete -> 释放引用 -> metadataProperties/delete` | 被引用时拒绝删除；释放后可删除 | 待补 |
| M4 | 内容管理员 | 标量元数据增量更新 | `metadata/update set/unset -> metadata/get` | `set` 覆盖、`unset` 移除；非法操作类型拒绝；返回值含 valueType+value | 待补 |
| M5 | 内容管理员 | 列表元数据增量更新 | `metadata/update append/remove/clear -> metadata/get` | `append` 去重追加；`remove` 容忍不存在元素；`clear` 置空保留属性 | 待补 |
| M6 | 内容管理员 | YAML front matter 自动注入 | `knowledgeItems/import (md with front matter) -> metadata/get -> readFile/search` | 已注册字段自动写入；未注册字段拒绝导入；front matter 不出现在切分内容/检索结果中 | 待补 |
| M7 | 内容管理员 | 删除联动清理 | `metadata/update -> knowledgeItems/delete -> metadataSearch/metadataFields/list` | 删除文件后元数据从所有读接口消失；目录删除/KB 删除等价表现 | 待补 |
| M8 | DSL 调用方 | 纯元数据检索（必传 where） | `metadataSearch where { in/exists/eq } -> 返回文件列表` | where 必填；不传 topK 默认返回 500；topK 上限 10000；topK 0/<0/>10000 拒绝 | 待补 |
| M9 | DSL 调用方 | DSL 算子覆盖 | `metadataSearch` 分别使用 `eq/ne/in/contains/exists/gt/gte/lt/lte/and/or/not` | 命中行为符合算子语义；contains 仅 stringList；in 不接受 stringList；exists 不携带 value 否则报错 | 待补 |
| M10 | DSL 调用方 | DSL 类型校验失败 | 对 `string` 字段传 `number` 等不匹配值；datetime 传非 ISO8601 | 返回 `INVALID_FIELD_VALUE_TYPE`，含 path/code/message | 待补 |
| M11 | DSL 调用方 | DSL 复杂度上限 | 嵌套深度超过 3 / 叶子条件超过 12 | 返回 `TOO_DEEP_BOOLEAN_NESTING` 或 `TOO_MANY_CONDITIONS` | 待补 |
| M12 | DSL 调用方 | 系统字段进 DSL | `metadataSearch where {in: { fieldName: "fileType", value: ["md"] }}` | 命中文件名以 .md 结尾的文件；fileSize/createdAt/updatedAt 等以正确类型校验 | 待补 |
| M13 | DSL 调用方 | 升级版 chunk 检索 | `knowledgeItems/search` 同时传 `query + where + metadataFieldList` | 先按 where 圈定 fs_entry 再 chunk 召回；返回结果含 metadata 字段 | 待补 |
| M14 | DSL 调用方 | fileTypeList 向下兼容 | `knowledgeItems/search` 仅传 `fileTypeList` 等价于传 `where` 的 `in fileType` | 旧调用方行为不变；与 `where` 同时存在时合取 | 待补 |
| M15 | DSL 调用方 | 文件级语义检索 | `knowledgeItems/searchFile` query+where+metadataFieldList | 候选 chunk 聚合到 file；保证一个 filePath 不重复出现；返回 metadata | 待补 |
| M16 | 跨接口一致性 | 元数据 + 检索一致 | `metadata/update -> search where eq -> metadata/get` | 命中文件的元数据值与 `metadata/get` 一致；删除元数据后不再被 where 命中 | 待补 |
| M17 | DSL 调用方 | 已删除文件不污染检索 | `metadata/update -> knowledgeItems/delete -> metadataSearch/search where 命中条件` | 已软删文件不出现在结果中（涉及 fs_entry.is_deleted 与 metadata_value.is_deleted 双重过滤） | 待补 |

## knowledge_build 场景总表

> **已弃用：** `knowledge_build` 独立路由（`file-to-markdown`、`build-markdown-index`、`file-to-markdown-index`）已全部移除。构建功能已整合到 `/api/v1/fileToMarkdownIndex`，作为 `knowledge_base` 模块的一部分。以下场景仅作历史参考。

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| A | 构建调用方 | ~~解析单个文件为 markdown~~ | ~~`file-to-markdown`~~ | ~~路由已移除~~ | 已弃用 |
| B | 构建调用方 | ~~从 markdown 构建 chunks~~ | ~~`build-markdown-index`~~ | ~~路由已移除~~ | 已弃用 |
| C | 构建调用方 | ~~一步式与两步式构建结果一致~~ | ~~`file-to-markdown -> build-markdown-index` 对比 `file-to-markdown-index`~~ | ~~路由已移除~~ | 已弃用 |
| D | 构建调用方 | ~~组合接口失败时正确短路~~ | ~~`file-to-markdown-index`~~ | ~~路由已移除~~ | 已弃用 |
| E | 构建调用方 | ~~构建链路异常可预测~~ | ~~覆盖不支持文件类型、非法 base64、空 markdown、未配置、embedding 异常~~ | ~~路由已移除~~ | 已弃用 |

## 当前已落测试文件

| 文件 | 覆盖重点 | 状态 |
| --- | --- | --- |
| `tests/knowledge_build/integration/test_api_integration.py` | ~~`knowledge_build` 三接口正常/异常与组合链路等价性~~ | 已弃用（`knowledge_build` 独立路由已移除） |
| `tests/knowledge_base/integration/test_kb_api_stateful_integration.py` | 混合导入构建（`knowledgeItems/import` + `fileToMarkdownIndex`）、知识库改名、单文件/目录删除、多级目录改名删除、读取窗口校验、`downloadFile` 的中文文件名/二进制文件下载、真实搜索链路与失败保护 | 有效 |
| `tests/knowledge_base/integration/test_metadata_api_integration.py` | 元数据属性 CRUD、文件元数据增量更新、metadataSearch、search 升级版 DSL 过滤；M 系列场景按编号在此文件落地或新增 | 有效 |

## 下一轮优先补充建议

| 优先级 | 场景 | 原因 |
| --- | --- | --- |
| P1 | `readFile` 未构建文件错误覆盖 | `readFile` 现在要求文件已通过 `fileToMarkdownIndex` 构建，需验证未构建时返回 "file not built" 错误 |
| P1 | 搜索过滤组合扩展 | 当前已覆盖基础多 `knCodeList`/source/type 组合，后续可继续补更复杂组合 |
| P1 | 配置异常覆盖面扩展 | 当前已覆盖 `knowledgeBases/create`、`listDir`、`readFile`、`knowledgeItems/search`，后续可继续补更多接口 |
| P1 | 清理弃用测试代码 | `test_api_integration.py`（knowledge_build）及场景 10/11/26 对应的测试代码需清理或移除 |
| P2 | `fileToMarkdownIndex` 构建失败保护扩展 | 已覆盖失败状态落库与失败后重试，后续可继续补充切片失败、向量化失败等更细分场景 |
| P2 | 生命周期冲突扩展 | 当前已覆盖路径绑定、软删除复用，后续可继续补更多版本化冲突 |
| P2 | 响应信封格式验证 | 验证所有接口统一使用 `resultCode`/`resultMsg`/`resultObject` 信封格式 |
