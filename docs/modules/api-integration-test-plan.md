# 接口级集成测试场景方案

本文档从用户旅程出发，整理 `knowledge_base` 与 `knowledge_build` 的接口级集成测试场景，方便直接查看“有哪些场景、覆盖了什么、哪些已经落代码”。

参考依据：

- `docs/modules/knowledge/api.md`
- `src/by_qa/knowledge_base/api/routes.py`
- `src/by_qa/knowledge_build/api/routes.py`
- `src/by_qa/knowledge_base/api/schemas.py`
- `src/by_qa/knowledge_build/api/schemas.py`

说明：

- `状态` 分为 `已写`、`已写部分`、`待补`
- `已写` 表示当前仓库已经有对应集成测试代码
- `已写部分` 表示该用户场景只覆盖了其中一部分链路
- 本轮只写方案和测试代码，不执行测试

## 多级目录专项场景总表

说明：

- 这组场景专门看多级目录树，不只看单层目录
- 重点验证祖先节点、父节点、子节点、孙节点之间的状态联动

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| D1 | 目录管理员 | 创建三级目录树 | `knowledgeBases/create -> create /A -> create /A/B -> create /A/B/C -> list_dir(kb root) -> list_dir(/A) -> list_dir(/A/B)` | 每层只返回直接子节点；祖先层级可逐级展开；路径结构稳定 | 已写 |
| D2 | 目录管理员 | 在多级目录最深层导入文件 | `create /A/B/C -> knowledge-items/import(/A/B/C/file.md) -> list_dir(/A/B/C) -> glob(A/**)` | 深层文件能被准确列出和匹配，祖先层路径解析正常 | 已写 |
| D3 | 目录管理员 | 重命名中间层目录并联动整棵子树 | `create /A/B/C -> import file -> rename B to B2 -> list_dir(/A) -> list_dir(/A/B2) -> list_dir(/A/B2/C) -> read-file old/new -> glob old/new` | 中间层改名后，所有后代路径同步变化；旧路径全失效；新路径全生效 | 已写 |
| D4 | 目录管理员 | 删除中间层目录并删除整棵子树 | `create /A/B/C -> import files -> delete B -> list_dir(/A) -> list_dir(/A/B) -> read-file -> search` | 删除中间层后，`B` 及其所有后代一起消失 | 已写 |
| D5 | 目录管理员 | 多级目录同级重名冲突 | `create /A/B1 -> create /A/B2 -> rename B2 to B1` | 返回 `KB_DIRECTORY_NAME_CONFLICT`；祖先和后代结构保持原样 | 已写 |
| D6 | 普通使用者 | 多级目录 glob/读取一致 | `create multi-level tree -> import files at different levels -> glob(pattern) -> read-file` | `glob` 命中的任意路径都能被 `read-file` 读取；深层路径无歧义 | 已写 |

## knowledge_base 场景总表

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | 知识库管理员 | 创建空知识库 | `knowledgeBases/create -> list_dir(/)` | 创建后根层级可见；重复创建冲突；非法请求报标准错误 | 已写 |
| 2 | 知识库管理员 | 修改知识库基础信息 | `knowledgeBases/create -> knowledgeBases/update -> list_dir(/)` | 修改 `kb_name` 后根目录名称同步变化；旧名称路径失效 | 已写 |
| 3 | 知识库管理员 | 删除知识库 | `knowledgeBases/create -> directories/create -> knowledge-items/import -> knowledgeBases/delete -> list_dir(/) -> read-file -> search` | 删除后根层级不可见；文件不可读；内容不可检索 | 已写 |
| 4 | 目录管理员 | 创建单层目录 | `knowledgeBases/create -> directories/create -> list_dir(kb root)` | 父层级能看到新目录；重复路径冲突；父目录缺失时报错 | 已写 |
| 5 | 目录管理员 | 创建多层目录树 | `create /A -> create /A/B -> create /A/B/C -> list_dir(/A) -> list_dir(/A/B) -> glob(A/*)` | 每层只返回直接子节点；`glob` 与目录结构一致 | 已写 |
| 6 | 目录管理员 | 目录改名影响整棵子树 | `create parent -> create child -> import file -> update child name -> list_dir/glob/read-file old&new` | 新路径生效；旧路径失效；子文件随目录路径变化 | 已写 |
| 7 | 目录管理员 | 删除非空目录 | `create dir -> import file -> directories/delete -> list_dir -> glob -> read-file -> search` | 整个子树从浏览、读取、检索里一起消失 | 已写 |
| 8 | 目录管理员 | 目录同级重名冲突 | `create /A/B1 -> create /A/B2 -> update B2 to B1 -> list_dir(/A)` | 返回 `KB_DIRECTORY_NAME_CONFLICT`；目录树保持不变 | 已写 |
| 9 | 内容管理员 | 用真实知识构建结果原子导入单文件 | `file-to-markdown -> build-markdown-index -> knowledgeBases/create -> directories/create -> knowledge-items/import -> list_dir -> read-file(markdown) -> read-file(original) -> search` | `knowledge_build` 产出的 markdown/chunks 可被 `knowledge-items/import` 正常消费；导入后目录可见、可读、可检索，元数据一致 | 已写 |
| 10 | 内容管理员 | 用真实知识构建结果分步写入单文件 | `file-to-markdown -> knowledgeBases/create -> directories/create -> write-file -> build-markdown-index(md_content) -> write-index -> read-file(markdown/original) -> search` | 先可读原文件，后可用构建出的 markdown/chunks 完成索引，最终读取与检索一致 | 已写 |
| 11 | 内容管理员 | 比较原子导入与分步写入的最终行为 | `file-to-markdown-index -> knowledge-items/import` 对比 `file-to-markdown -> write-file -> build-markdown-index -> write-index -> read-file -> search` | 两条链路最终在 `list_dir/read-file/search` 上表现一致 | 已写 |
| 12 | 内容管理员 | 路径绑定冲突 | `import A:/x.md -> import B:/x.md` | 第二次写入失败；原绑定不变 | 已写 |
| 13 | 内容管理员 | 删除单文件 | `import -> list_dir -> read-file -> knowledge-items/delete -> list_dir -> read-file -> search` | 删除后目录不可见、文件不可读、内容不可检索 | 已写 |
| 14 | 内容管理员 | 软删除编码占用 | `import -> delete -> import same file_code` | 返回 soft-deleted conflict | 已写 |
| 15 | 普通使用者 | 根目录浏览 | `create multiple kb -> list_dir(/)` | 返回所有知识库根节点，名称正确 | 已写 |
| 16 | 普通使用者 | 多层目录浏览 | `create tree -> list_dir(root) -> list_dir(child) -> list_dir(file path)` | 目录返回直接子项；文件路径按约定返回单文件结果 | 已写 |
| 17 | 普通使用者 | glob 模式浏览 | `import files -> glob(pattern) -> rename/delete -> glob(pattern again)` | 匹配结果与目录结构一致；状态变化后同步变化 | 已写 |
| 18 | 普通使用者 | 读取 markdown 全量内容 | `knowledge-items/import -> read-file(markdown full)` | 返回完整 markdown；`reached_eof=true` | 已写 |
| 19 | 普通使用者 | 读取 markdown 行窗口 | `knowledge-items/import -> read-file(start_line,end_line)` | 返回指定行范围；`reached_eof` 正确；非法窗口报错 | 已写 |
| 20 | 普通使用者 | 读取 original 文件 | `write-file/import -> read-file(original)` | 返回原文件 URL，不返回 markdown 文本 | 已写 |
| 20A | 普通使用者 | 下载中文文件名的 Markdown 原文件 | `knowledge-items/import(中文文件名) -> download-file` | 返回原始字节流；`Content-Disposition` 对非 ASCII 文件名安全；`Content-Type=text/markdown` | 已写 |
| 20B | 普通使用者 | 下载二进制 PDF 原文件 | `file-to-markdown -> build-markdown-index -> knowledge-items/import(pdf) -> download-file` | 返回原始 PDF 字节流；`Content-Type=application/pdf`；下载文件名正确 | 已写 |
| 21 | 检索使用者 | 单文件命中检索 | `knowledge-items/import -> knowledge-items/search` | 返回对应 chunk；路径、版本、chunk 编号正确 | 已写 |
| 22 | 检索使用者 | 过滤条件检索 | `import multiple files -> search with kb/source/type filters` | 仅返回符合过滤条件的结果 | 已写 |
| 23 | 检索使用者 | 删除后的检索收敛 | `import -> search hit -> delete file/dir -> search again` | 已删除内容不再命中 | 已写 |
| 24 | 检索使用者 | 目录改名后的检索路径更新 | `import in old dir -> search -> rename dir -> search again` | 内容仍命中，但 `file_path` 更新为新路径 | 已写 |
| 25 | 跨接口一致性 | 浏览、读取、检索一致 | `import -> list_dir -> read-file -> search` | 可见文件一定可读；搜索结果路径可被读取 | 已写 |
| 26 | 跨接口一致性 | 原子导入与分步写入行为一致 | `file-to-markdown-index -> knowledge-items/import` 对比 `file-to-markdown -> write-file -> build-markdown-index -> write-index` | 两种带真实知识构建步骤的链路最终对外表现一致 | 已写 |
| 27 | 跨接口一致性 | 改名或删除后的全局一致性 | `rename/delete -> list_dir -> glob -> read-file -> search` | 所有读接口观察到的状态一致 | 已写 |
| 28 | 异常与恢复 | 请求参数不合法 | 覆盖缺少必填、空字符串、重复 `chunk_no`、非法 line window 等 | 返回统一请求校验或业务校验错误 | 已写 |
| 29 | 异常与恢复 | 运行时依赖未配置 | 覆盖 KB runtime/fetch runtime/embedding 配置缺失 | 返回 `configuration_error` 风格错误 | 已写 |
| 30 | 异常与恢复 | 构建或落库失败不留下半成功状态 | `file-to-markdown/build-markdown-index` 失败后不进入写入，或 `write-file success -> write-index failure`，或 `import failure` | 不留下可见但不可读、可检索但不可读等异常状态 | 已写 |

## knowledge_build 场景总表

说明：

- `knowledge_build` 仍保留独立场景表，但它只描述“构建接口本身是否稳定”
- 真正面向用户的主业务链路，已经合并进上面的 `knowledge_base` 场景总表

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| A | 构建调用方 | 解析单个文件为 markdown | `file-to-markdown` | 返回 `md_content`；不支持类型、非法 base64、未配置服务时返回正确错误 | 已写 |
| B | 构建调用方 | 从 markdown 构建 chunks | `build-markdown-index` | 返回 chunks；空内容和依赖异常返回正确错误 | 已写 |
| C | 构建调用方 | 一步式与两步式构建结果一致 | `file-to-markdown -> build-markdown-index` 对比 `file-to-markdown-index` | `md_content` 和 `chunks` 一致 | 已写 |
| D | 构建调用方 | 组合接口失败时正确短路 | `file-to-markdown-index` | 解析失败时直接返回解析错误；切片失败时返回切片错误 | 已写 |
| E | 构建调用方 | 构建链路异常可预测 | 覆盖不支持文件类型、非法 base64、空 markdown、未配置、embedding 异常 | 错误信封稳定 | 已写 |

## 当前已落测试文件

| 文件 | 覆盖重点 |
| --- | --- |
| [tests/knowledge_build/integration/test_api_integration.py](/Users/jialangli/code/workspace/by-qa/tests/knowledge_build/integration/test_api_integration.py) | `knowledge_build` 三接口正常/异常与组合链路等价性 |
| [tests/knowledge_base/integration/test_kb_api_stateful_integration.py](/Users/jialangli/code/workspace/by-qa/tests/knowledge_base/integration/test_kb_api_stateful_integration.py) | 混合构建导入、分步写入、知识库改名、单文件/目录删除、多级目录改名删除、读取窗口校验、`download-file` 的中文文件名/二进制文件下载、真实搜索链路与失败保护 |

## 下一轮优先补充建议

| 优先级 | 场景 | 原因 |
| --- | --- | --- |
| P1 | 搜索过滤组合扩展 | 当前已覆盖基础多 KB/source/type 组合，后续可继续补更复杂组合 |
| P1 | 配置异常覆盖面扩展 | 当前已覆盖 `create-kb`、`list_dir`、`read-file`、`write-file`、`search`，后续可继续补更多接口 |
| P2 | 失败保护扩展 | 当前已覆盖 build-index/import/write-index 失败，后续可继续补更多失败点 |
| P2 | 生命周期冲突扩展 | 当前已覆盖路径绑定、软删除复用，后续可继续补更多版本化冲突 |
