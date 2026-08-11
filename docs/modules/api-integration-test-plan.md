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
- `待补` 表示已纳入计划，但当前仓库尚无对应集成测试
- `已弃用` 表示对应路由已移除，场景不再适用

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
| 31 | 内容管理员 | 默认允许导入相同内容 | 同一知识库先后导入 checksum 相同、路径不同的文件，均不传 `skipIfDuplicate` | `skipIfDuplicate` 默认 `false`，两个文件均导入成功 | 已写 |
| 32 | 内容管理员 | 按知识库阻止重复内容导入 | 同一知识库再次导入相同内容并传 `skipIfDuplicate=true` | 返回重复 checksum 提示并包含已存在文件路径；目标路径不产生文件记录 | 已写 |
| 33 | 内容管理员 | 重复内容判断保持知识库隔离 | 在另一个知识库导入相同内容并传 `skipIfDuplicate=true` | 跨知识库不视为重复，导入成功 | 已写 |
| 34 | 内容管理员 | zip 批量导入逐项识别重复内容 | zip 中的文件与同知识库已有文件 checksum 相同，传 `skipIfDuplicate=true` | 接口批次正常返回；重复项标记失败并给出重复 checksum 提示，不落盘该项 | 已写 |

## 文档更新与时间线场景总表

说明：

- 这一组场景覆盖 `POST /api/v1/knowledgeItems/update`；接口仅更新一个已存在文件，不支持 zip、移动、重命名或改变文件格式，且更新后不自动触发构建。
- 已写的场景在 `tests/knowledge_base/integration/test_kb_api_stateful_integration.py` 中执行，使用真实 MinIO 对象存储；文档切分和向量检索使用测试替身，以保持接口状态断言稳定。
- 时间线目前只有写入和异步回填，没有查询接口；测试通过数据库读取核对写入结果。

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| UDT1 | 内容管理员 | 更新已构建 Markdown 并清理派生状态 | `import -> fileToMarkdownIndex -> search -> knowledgeItems/update -> downloadFile -> fileBuildStatus -> search -> metadata/get` | 原始对象被真实 MinIO 内容覆盖；旧 chunk/检索结果和构建记录清理；不自动创建构建任务；新 front matter 覆盖同名字段，文件中缺失的既有字段保留 | 已写 |
| UDT2 | 内容管理员 | 更新 Markdown 的稳定引用并写入时间线 | `import targets -> import+build source -> knowledgeItems/update(source) -> knowledgeItems/references -> DB timeline` | 更新后旧 outbound 引用消失、新引用解析正确；时间线关联 `fs_entry_id`、记录旧/新文件大小并写入规则摘要或 LLM 摘要；不触发构建 | 已写 |
| UDT3 | 内容管理员 | 更新二进制文件与参数校验 | `import png -> knowledgeItems/update(png) -> downloadFile -> invalid update requests` | 二进制原始字节被覆盖；不调用 LLM；zip、文件格式变更、文件不存在及构建中更新均返回 HTTP 200 + `resultCode=-1`，构建中提示为 `File is being built and cannot be updated` | 已写 |
| UDT4 | 内容管理员 | Markdown 时间线异步 LLM 回填与降级 | `import md -> update -> async backfill -> DB timeline` | 首先写入规则摘要；LLM 成功后同一条时间线更新为 `LLM` 摘要；LLM 报错时保留规则摘要，不影响更新接口成功 | 已写 |
| UDT5 | 内容管理员 | `processFrontMatter=false` 更新 Markdown | `import(md 含 front matter) -> update(processFrontMatter=false) -> metadata/get -> downloadFile` | 原始 Markdown 被替换，但 front matter 不解析且既有元数据不被意外覆盖 | 已写 |
| UDT6 | 内容管理员 | 更新时描述字段的三态语义 | `import(description=old) -> update(未传/空串/新值) -> listDir 或 metadata/get` | 未传保留旧描述；空串清空；非空值覆盖 | 已写 |
| UDT7 | 内容管理员 | 更新请求与路径校验 | `update(root path / dot segment / dotdot segment / 缺失 multipart field)` | 所有非法请求均遵循 HTTP 200 错误信封；路径不会产生别名或越界访问 | 已写 |
| UDT8 | 内容管理员 | 同一文件并发更新与文件级锁 | 两个并发 `knowledgeItems/update` 请求指向同一文件 | 更新串行化；每次时间线准确对应一次提交；对象内容、`fs_entry` 与时间线不交叉或丢失 | 已写 |
| UDT9 | 存储运维 | 更新时存储/数据库失败的补偿 | 模拟原对象覆盖失败、数据库提交失败及 rollback 失败 | 不产生半更新；数据库失败后原对象字节恢复到原 locator；rollback 失败也尝试恢复并返回原始失败 | 已写（rollback 失败仍由单元测试覆盖） |
| UDT10 | 存储实现方 | 路径映射存储的原 locator 覆盖 | 使用 UserFS 等按路径映射的 provider：`import -> update -> downloadFile -> 文件系统检查` | 不生成新 key；原始文件仍在既有 locator 被覆盖，更新后可下载 | 已写 |
| UDT11 | 内容管理员 | 使用当前文件签名进行乐观更新 | `metadata/get(fileSignature) -> update(referSignature=current) -> metadata/get` | 更新成功；原始对象内容、`updatedAt` 和 `fileSignature` 一致切换到新版本 | 已写 |
| UDT12 | 内容管理员 | 旧文件签名拒绝更新且不产生半更新 | 先成功更新一次，再用旧 `referSignature` 更新 | 返回文件签名不一致；对象字节和数据库中的 `fileSignature` 均保持为最近成功版本 | 已写 |
| UDT13 | 内容管理员 | 更新时阻止同库其他文件的重复内容 | `import A,B -> update A(content=B, skipIfDuplicate=true)` | 返回重复 checksum 提示并包含 B 的路径；A 的对象和数据库状态不变 | 已写 |
| UDT14 | 内容管理员 | 重复判断排除当前文件自身 | `update A(content=A, referSignature=current, skipIfDuplicate=true)` | 当前文件不与自身冲突，更新成功 | 已写 |

### checksum 并发锁范围

说明：

- checksum 防重使用事务级 advisory lock，锁键范围为 `(knowledge_base_id, checksum)`；事务提交、回滚或连接异常结束时由数据库释放。
- 本组场景直接使用真实 OpenGauss 连接验证阻塞关系，避免仅通过串行 HTTP 请求得到假阳性。

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| CLK1 | 存储实现方 | 同知识库同 checksum 串行化 | 连接 A 获取 `(KB-A, checksum-X)` 锁，连接 B 获取相同范围锁 | B 在 A 事务结束前阻塞；A 回滚后 B 自动获得锁 | 已写 |
| CLK2 | 存储实现方 | 同知识库不同 checksum 可并行 | 两个连接分别获取 `(KB-A, checksum-X)` 与 `(KB-A, checksum-Y)` | 第二把锁无需等待第一笔事务结束 | 已写 |
| CLK3 | 存储实现方 | 不同知识库相同 checksum 可并行 | 两个连接分别获取 `(KB-A, checksum-X)` 与 `(KB-B, checksum-X)` | 第二把锁无需等待第一笔事务结束 | 已写 |

## zip 批量导入与引用改写场景总表

说明：

- 这一组场景覆盖 `/api/v1/knowledgeItems/import` 的 zip 包批量上传与 markdown 引用改写能力（入参不变，按 `fileContent.filename` 是否 `.zip` 分流）。
- zip 模式下：非 markdown 文件先并发上传（阶段一），markdown 文件后并发上传（阶段二，先改写引用再上传）；已存在文件先软删后重传（覆盖）；不支持的文件类型构建时置「不支持构建」状态。
- 出参由空 `resultObject` 改为 `{data:[{filePath,success,error}], summary:{total,succeeded,failed}}`。
- 编号前缀 `Z` 代表 zip 批量导入；均在 `tests/knowledge_base/integration/test_kb_api_stateful_integration.py`，走真实 HTTP + OpenGauss + MinIO。

### 单文件分流与出参

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| Z1 | 内容管理员 | 单文件上传返回清单出参 | `knowledgeItems/import(单个 md)` | `resultObject.data` 为含 1 项的列表（`filePath/success/error`），`summary.total=1` | 已写 |
| Z2 | 内容管理员 | 单文件 md 引用改写（非 zip） | `import 图片资源 -> import md(引用图片)` | 引用改写为 KB 绝对路径；`downloadFile` 取回的原始 md 含改写结果 | 已写 |
| Z3 | 内容管理员 | 单文件 `..` 路径拒绝 | `import filePath=/../escape.md` | `resultCode=-1` `resultMsg=unsafe path`；不创建文件 | 已写 |

### zip 批量上传主链路

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| Z4 | 内容管理员 | zip happy path 改写 | `import zip(png + md 引用 png)` | 两者成功、`summary.succeeded=2`；md 引用改写为 KB 绝对路径 | 已写 |
| Z5 | 内容管理员 | 覆盖成功替换旧内容 | `import md(OLD) -> import zip(同路径 md=NEW)` | 旧文件被软删并以新内容替换；`downloadFile` 返回 NEW，不含 OLD | 已写 |
| Z6 | 内容管理员 | 非 md 二进制字节完整 | `import zip(png 二进制)` | `downloadFile` 返回的原始字节与上传字节逐字节一致 | 已写 |
| Z7 | 内容管理员 | 两阶段顺序（非 md 先于 md） | `import zip(2 png + 2 md)` | 响应 `data` 中所有非 md 项索引 < 所有 md 项索引 | 已写 |
| Z8 | 内容管理员 | 嵌套目录自动创建 | `import zip(a/b/c.md)` | 中间目录 `a`、`b` 自动创建；`downloadFile /target/a/b/c.md` 返回内容 | 已写 |
| Z9 | 内容管理员 | zip 内 md front matter 持久化 | `import zip(md 含 YAML front matter) -> metadata/get` | front matter 字段被 `processFrontMatter` 解析并写入元数据 | 已写 |
| Z10 | 内容管理员 | 8 路并发全量成功 | `import zip(8 png + 8 md，每个 md 引用各自 png)` | 16 项全部成功；md 引用改写正确；png 字节完整 | 已写 |

### zip 引用改写：能替换 / 不能替换

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| Z11 | 内容管理员 | 能替换：`..` 相对 + 链接形式 + 锚点保留 | `import zip(img/x.png + other.md + sub/doc.md 引用 ../img/x.png、../other.md、../img/x.png#section)` | 三种引用均改写为 KB 绝对路径（`/t/img/x.png`、`/t/other.md`、`/t/img/x.png#section`） | 已写 |
| Z12 | 内容管理员 | 不能替换：缺失/外部/锚点/逃根 | `import zip(doc.md 含 missing.png、https URL、#anchor、../../../x.png)` | 四种引用全部保持原样（`downloadFile` 返回原始 md 字节不变） | 已写 |

### zip 异常与防护

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| Z13 | 内容管理员 | zip 不安全路径条目拒绝 | `import zip(../escape.md + real.md)` | `../escape.md` 记为失败（`error` 含 unsafe）；`real.md` 成功；逃逸路径不创建文件 | 已写 |
| Z14 | 内容管理员 | 非法 zip 拒绝 | `import filename=.zip 但内容非法` | `resultCode=-1` `resultMsg=invalid zip file` | 已写 |
| Z15 | 内容管理员 | zip-bomb / 超大条目路由层拒绝 | `import zip(单条目超 per-entry 解压上限)`（monkeypatch 小 cap） | `resultCode=-1` `resultMsg=zip too large`；不创建文件 | 已写 |
| Z16 | 内容管理员 | malformed md 覆盖不删原文件（H1） | `import md(VALID) -> import zip(同路径 malformed UTF-8 md)` | malformed 条目记为失败；原 VALID 文件仍可下载（改写在 delete 之前） | 已写 |

### 构建侧适配

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| Z17 | 内容管理员 | 不支持类型构建置「不支持构建」 | `import png -> fileToMarkdownIndex -> fileBuildStatus` | 构建任务 `status=unsupported`（不抛错、不写 chunks） | 已写 |

## 稳定 Markdown 引用与移动场景总表

说明：

- 这一组场景覆盖稳定 Markdown 资源引用方案：上传/导入时登记 `knowledge_file_reference` 并写入内部 token，读出口按当前文件树解析为对外路径。
- 覆盖接口包括 `knowledgeItems/import`、`fileToMarkdownIndex`、`readFile`、`downloadFile`、`knowledgeItems/search`、`knowledgeItems/move`、`knowledgeItems/delete`、`directories/delete`、`knowledgeItems/references`。
- 编号前缀 `R` 代表 stable reference；当前接口级集成测试均在 `tests/knowledge_base/integration/test_kb_api_stateful_integration.py`。

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| R1 | 内容管理员 | 已存在目标引用在读取和检索时解析为当前路径 | `import /resolved/b.md -> import+build /resolved/a.md(引用 b.md) -> readFile -> knowledgeItems/search` | `readFile` 和 `search.chunkText` 输出 `(/resolved/b.md)`；不泄漏 `byqa-ref://` 内部 token | 已写 |
| R2 | 内容管理员 | unresolved 引用在目标补传后自动恢复 | `import+build /pending/a.md(引用 b.md) -> readFile -> import /pending/b.md -> readFile` | 目标缺失时回退原始写法 `(b.md)`；目标上传后输出 `(/pending/b.md)`；无需重建 source markdown | 已写 |
| R3 | 内容管理员 | 删除目标文件后引用断链并回退原始写法 | `R2 -> knowledgeItems/delete(/pending/b.md) -> readFile -> knowledgeItems/search` | 删除后 `readFile` 和 `search.chunkText` 回退 `(b.md)`；不输出已删除目标路径；不泄漏 `byqa-ref://` | 已写 |
| R4 | 内容管理员 | 同路径重新上传后 broken 引用恢复 | `R3 -> import /pending/b.md -> readFile -> knowledgeItems/search` | 重新上传同路径后输出 `(/pending/b.md)`；检索 chunk 文本也解析为恢复后的路径 | 已写 |
| R5 | 目录管理员 | 移动目标文件后读出口跟随新路径且不重建 chunk | `import target -> import+build source(引用 target) -> knowledgeItems/move(sourcePath=[target], targetFilePath=...) -> readFile -> search` | `targetFilePath` 前缀目录自动创建；`readFile` 和 `search.chunkText` 输出移动后的路径；chunking 调用次数不增加 | 已写 |
| R6 | 目录管理员 | 移动目标目录子树后引用和检索投影同步更新 | `import /tree/sub/* -> import+build refs -> knowledgeItems/move(sourcePath=[/tree], targetDirectoryPath=/archive/auto) -> readFile -> search -> listDir` | `targetDirectoryPath` 不存在时自动建目录；子树引用输出 `/archive/auto/tree/...`；search `filePath` 使用移动后的路径；不重建 chunk | 已写 |
| R7 | 内容管理员 | 移动 source markdown 不重算 unresolved 待匹配路径 | `import+build /pending-source/source.md(引用 missing.md) -> knowledgeItems/move(... targetFilePath=/new/source/path/source.md) -> import /pending-source/missing.md -> readFile` | source 移动后 `readFile` 仍先回退 `(missing.md)`；补传旧待匹配路径 `/pending-source/missing.md` 后解析为旧待匹配路径，不改为 `/new/source/path/missing.md` | 已写 |
| R8 | 内容管理员 | zip 内 md-to-md 引用入库并可通过 references 查询 | `import zip(b.md,a.md 引用 b.md) -> fileToMarkdownIndex(a.md) -> readFile(a.md) -> knowledgeItems/references(filePath=/zip/b.md)` | `readFile` 输出 `(/zip/b.md)`；`references.resultObject.inbound` 返回 source/originalTarget/targetPath/status=resolved | 已写 |
| R9 | 目录管理员 | 删除目录子树时子树内目标的 inbound 引用统一标 broken | `import targets 子树 -> import+build sources 引用子树文件 -> directories/delete(target dir) -> readFile(sources) -> knowledgeItems/references(filePath=deleted paths)` | 指向子树内每个被删文件的 inbound 引用都变为 `status=broken`，写入删除前 `targetPath`；读出口回退原始写法 | 已写 |
| R10 | 普通使用者 | 下载 Markdown 时解析 stable reference token | `import target -> import+build source -> downloadFile(source) -> move/delete/restore target -> downloadFile(source)` | markdown 下载内容与 `readFile` 一致：resolved/moved/restored 输出当前路径，broken 回退 original target，任何阶段不泄漏 `byqa-ref://` | 已写 |
| R11 | 普通使用者 | query/fragment suffix 只拼接一次 | `import b.md -> import+build a.md(引用 b.md?download=1#intro) -> readFile/search/download -> move/delete/restore b.md` | resolved 输出当前 `targetPath + targetSuffix`；broken 回退 `originalTarget`；不重复拼接 `targetSuffix`；references 返回 `targetSuffix` | 已写 |
| R12 | 普通使用者 | 行窗口读取先切片再解析 token | `import b.md -> import+build a.md(第2行引用 b.md) -> readFile(startLine=2,endLine=2)` | 只返回第 2 行，且该行引用已解析；不包含相邻行；不泄漏 `byqa-ref://` | 已写 |
| R13 | 内容管理员 | references 支持 outbound/all 并过滤已删除 source | `import target -> import+build source(引用 target 和 missing) -> references(source,direction=all/outbound) -> delete source -> references(target,inbound)` | outbound 返回 resolved + unresolved；all 同时返回 inbound/outbound；删除 source 后 target inbound 为空 | 已写 |
| R14 | 内容管理员 | 目录链接不登记为 stable file reference | `create directory -> import+build source(链接目录) -> references(source,outbound) -> move directory -> readFile(source)` | 目录链接保持原始 markdown target；outbound 为空；移动目录不改写目录链接 | 已写 |
| R15 | 目录管理员 | 批量移动多个目标文件后引用同步，非法 move 保持原子 | `import targets -> import+build source(引用两个 target) -> knowledgeItems/move(sourcePath=[a,b],targetDirectoryPath=...) -> invalid move` | 两个引用都输出新路径；search chunkText 同步；非法 move 返回失败且引用输出保持不变 | 已写 |
| R16 | 内容管理员 | 路径归一化与 pending 补偿一致 | `import+build source(引用 ./b%20file.md#intro 和逃根路径) -> import /norm/b file.md -> readFile/search/references(outbound)` | URL decode 后按 `/norm/b file.md` 补偿 resolved；逃根路径不入引用表；search 不泄漏 token | 已写 |
| R17 | 检索使用者 | 真实分片路径不切开 stable reference token | `使用真实 DocumentChunkingService 小 chunk_size -> import+build source(含 stable token) -> search` | 搜索结果中 stable reference 已解析，且不出现半截或完整 `byqa-ref://` token | 已写 |

## 元数据与 DSL 检索场景总表

说明：

- 这一组场景覆盖单文件元数据批量更新、Markdown front matter、纯元数据检索、DSL 升级版 chunk/file 检索的端到端调用链。
- 文件元数据写入统一使用 `POST /api/v1/knowledgeItems/metadata/update`；单次请求只操作一个文件，可批量处理不同属性。
- `metadata/get` 返回自定义元数据及请求的系统字段值。
- 错误响应统一使用文档化信封：HTTP 200 + `resultCode="-1"` + `resultMsg="..."`（包括 Pydantic 校验失败）。
- 本组新增场景落在 `tests/knowledge_base/integration/test_kb_api_stateful_integration.py`。

### 文件元数据增量更新

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| MU1 | 内容管理员 | 单文件批量新增、修改和删除 | `import(front matter) -> metadata/update(set/append/unset) -> metadata/get -> downloadFile` | 字符串与时间值正确写入；list 追加去重；unset 后属性不再返回；下载的 front matter 与查询一致 | 已写 |
| MU2 | 内容管理员 | remove、clear 与属性类型变更 | `import -> metadata/update(remove/clear/set new type) -> metadata/get` | remove 忽略不存在元素；clear 返回空列表；set 可修改属性类型 | 已写 |
| MU3 | 内容管理员 | 重试相同请求 | 连续两次执行相同 `remove/clear/set/unset` 批次 | 两次均成功，最终元数据不重复、不丢失 | 已写 |
| MU4 | 内容管理员 | 批量原子失败 | `metadata/update(set valid + append missing list)` | 整批返回失败，前面的 set 不保留 | 已写 |
| MU5 | 内容管理员 | 请求形式和资源校验 | 未知 KB/文件、只读系统字段、同属性重复操作、非法 operation/valueType/value；另提交 101 个不同属性操作 | 非法请求返回 HTTP 200 错误信封；101 个操作的非空批次成功 | 已写 |
| MU6 | 内容管理员 | 并发追加同一文件的列表属性 | 两个并发 `metadata/update append tags` | 两个请求均成功，最终列表包含两次追加值，无丢失更新 | 已写 |

### Markdown front matter

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| MF1 | 内容管理员 | 导入 front matter 后查询元数据 | `knowledgeItems/import(md with front matter) -> metadata/get` | string、datetime、stringList 值及类型正确 | 已写 |
| MF2 | 内容管理员 | 更新元数据后下载 Markdown | `import -> metadata/update -> downloadFile` | 下载内容包含最新 front matter，正文保持不变 | 已写 |

### 删除联动

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| MD1 | 内容管理员 | 删除文件后元数据失效 | `import(front matter) -> knowledgeItems/delete` | 文件删除成功，不再存在有效元数据 | 已写 |
| MD2 | 目录管理员 | 删除目录后子树元数据失效 | `import multiple files with front matter -> directories/delete` | 子树文件的元数据全部失效 | 已写 |
| MD3 | 知识库管理员 | 删除知识库后元数据失效 | `import(front matter) -> knowledgeBases/delete` | 知识库下不再存在有效元数据 | 已写 |

### metadataSearch 接口约束

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M8.a | DSL 调用方 | where 必填 | 不传 where | 文档化信封 | 已写 |
| M8.b | DSL 调用方 | where 为空对象 | `where={}` | DSL_VALIDATION_ERROR / INVALID_BOOLEAN_NODE | 已写 |
| M8.c | DSL 调用方 | 保留 topK 兼容语义 | 不传 `pageSize`，仅传或省略 `topK` | `pageSize` 未提供时使用 `topK`，两者均省略时每页默认 500 条 | 已写 |
| M8.d | DSL 调用方 | topK 上限 10000 | `topK=10001` 拒绝；`topK=10000` 通过 | 文档化信封 / 200 | 已写 |
| M8.e | DSL 调用方 | topK 0 / 负数 | `topK=0/-1` | 文档化信封 | 已写 |
| M8.f | DSL 调用方 | knCodeList 缩范围 | 两 KB 命中，knCodeList=[A] | 仅返 A | 已写 |
| M8.g | DSL 调用方 | knCodeList 含未知 KB | `knCodeList=[ghost]` | `resultCode=-1` `"knowledge base not found"` | 已写 |
| M8.h | DSL 调用方 | metadataFieldList 返回控制 | `metadataFieldList=[keep]` | 仅返 keep | 已写 |
| M8.i | DSL 调用方 | knCodeList 必填非空 | 不传 / `knCodeList=[]` | 文档化信封 | 已写 |
| M8.j | DSL 调用方 | metadataSearch 分页与总数 | 三个命中文件，依次请求 `pageNum=1/2/3,pageSize=2` | 返回 `data/total/pageNum/pageSize`；前两页无重复且覆盖全部命中，越界页 `data=[]`、`total` 保持 3 | 已写 |
| M8.k | DSL 调用方 | 按更新时间从旧到新稳定排序 | 导入 A/B/C，更新 A 后重新分页查询 | 结果按 `updatedAt ASC`；A 移到最后；分页前后顺序一致 | 已写 |

### DSL 算子矩阵

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M9.eq | DSL 调用方 | eq | `eq status active` | 命中 status=active 文件 | 已写 |
| M9.ne | DSL 调用方 | ne | `ne status active` | 命中 status≠active 且属性存在的文件 | 已写 |
| M9.in | DSL 调用方 | in | `in status [active,pending]` | 命中其一 | 已写 |
| M9.contains | DSL 调用方 | contains | `contains tags contract` | 命中 tags 含 contract 的文件 | 已写 |
| M9.exists | DSL 调用方 | exists | `exists archived` | 命中所有设置过 archived 的文件 | 已写 |
| M9.gt | DSL 调用方 | gt number | `gt priority 5` | 命中 >5 | 已写 |
| M9.gte | DSL 调用方 | gte number | `gte priority 5` | 含等号 | 已写 |
| M9.lt | DSL 调用方 | lt number | `lt priority 5` | 命中 <5 | 已写 |
| M9.lte | DSL 调用方 | lte number | `lte priority 5` | 含等号 | 已写 |
| M9.gt-dt | DSL 调用方 | gt datetime | `gt publishedAt 2026-02-01...Z` | 时间窗口命中 | 已写 |
| M9.prefix | DSL 调用方 | prefix string | `prefix status "act"` | 命中 status 以 "act" 开头的文件 | 待补 |
| M9.wildcard | DSL 调用方 | wildcard string | `wildcard status "act*"` | 命中 status 匹配通配符的文件 | 待补 |
| M9.and | DSL 调用方 | and 平铺 | `and [eq, contains]` | 取交集 | 已写 |
| M9.or | DSL 调用方 | or 平铺 | `or [eq, eq]` | 取并集 | 已写 |
| M9.not | DSL 调用方 | not 包叶子 | `not eq status archived` | 排除 archived 文件 | 已写 |
| M9.nest1 | DSL 调用方 | and(or, leaf) 二层 | active/pending 且 priority>3 | 交集 | 已写 |
| M9.nest2 | DSL 调用方 | or(not, leaf) 二层 | not exists archived 或 status=active | 并集 | 已写 |
| M9.nest3 | DSL 调用方 | 三层嵌套（depth=3 边界） | `and[or[and[eq,contains]]]` | 通过；命中 active+hr | 已写 |
| M9.demor | DSL 调用方 | 德摩根等价 | `not(or[A,B]) ≡ and[not A, not B]` | 两侧命中集合相同 | 已写 |
| M9.prefix-fn | DSL 调用方 | prefix 系统字段 fileName | `prefix fileName "F"` | 命中 fileName 以 "F" 开头的文件 | 待补 |
| M9.wildcard-fn | DSL 调用方 | wildcard 系统字段 fileName | `wildcard fileName "F?.md"` | 命中 F1.md..F6.md 不命中 F5.pdf | 待补 |

### DSL 校验错误

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M10.a | DSL 调用方 | string 字段传 number | `eq status 1` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.b | DSL 调用方 | number 字段传 string | `eq priority "5"` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.c | DSL 调用方 | number 字段传 bool | `eq priority true` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.d | DSL 调用方 | datetime 非 ISO8601 | `gt publishedAt "yesterday"` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.e | DSL 调用方 | exists 携带 value | `exists{... value:"x"}` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.f | DSL 调用方 | in 用于 stringList | `in tags ["hr"]` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.g | DSL 调用方 | contains 用于非 stringList | `contains status "active"` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.h | DSL 调用方 | gt 用于 string | `gt status "active"` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.prefix-ns | DSL 调用方 | prefix 用于非 string 字段 | `prefix priority "1"` | INVALID_FIELD_VALUE_TYPE | 待补 |
| M10.wildcard-ns | DSL 调用方 | wildcard 用于非 string 字段 | `wildcard priority "1*"` | INVALID_FIELD_VALUE_TYPE | 待补 |
| M10.i | DSL 调用方 | in.value 空数组 | `in status []` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.j | DSL 调用方 | in.value 数组项类型不一致 | `in priority [1,"two"]` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M10.k | DSL 调用方 | 节点对象多于一个 key | `{eq:..., ne:...}` | INVALID_BOOLEAN_NODE | 已写 |
| M10.l | DSL 调用方 | and 操作数空数组 | `{and:[]}` | INVALID_BOOLEAN_NODE | 已写 |
| M10.m | DSL 调用方 | not 操作数为数组 | `{not:[...]}` | INVALID_BOOLEAN_NODE | 已写 |
| M10.n | DSL 调用方 | 未知算子 | `{between: ...}` | UNSUPPORTED_OPERATOR | 已写 |
| M10.o | DSL 调用方 | 未知 fieldName | `{eq:{fieldName:'ghost', value:'x'}}` | UNKNOWN_FIELD | 已写 |
| M11.a | DSL 调用方 | 嵌套深度 4 | 四层嵌套布尔 | TOO_DEEP_BOOLEAN_NESTING | 已写 |
| M11.b | DSL 调用方 | 叶子条件 13 | `and: 13 个 leaf` | TOO_MANY_CONDITIONS | 已写 |
| M11.c | DSL 调用方 | 多错误同时返回 | unknown_field + 类型错 | errorList ≥ 2 条 | 已写 |

### 系统字段进 DSL（metadataSearch）

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M12.a | DSL 调用方 | in fileType | `in fileType ["md","pdf"]` | 命中扩展名匹配文件 | 已写 |
| M12.b | DSL 调用方 | eq fileName | `eq fileName "note.txt"` | 精确命中 | 已写 |
| M12.c | DSL 调用方 | gt fileSize | `gt fileSize 1000` | 命中大文件 | 已写 |
| M12.d | DSL 调用方 | gt createdAt | `gt createdAt ISO8601` | 时间窗口命中 | 已写 |
| M12.e | DSL 调用方 | contains 用于系统字段 | `contains fileType "md"` | INVALID_FIELD_VALUE_TYPE | 已写 |
| M12.f | DSL 调用方 | metadataSearch 系统+自定义混合 | `and: [eq custom status active, in fileType ["md"]]` | 仅 .md 且 status=active 的文件命中 | 已写 |
| M12.g | DSL 调用方 | eq fileSignature | `metadata/get(fileSignature) -> metadataSearch where eq fileSignature` | 仅精确命中 checksum 相同的文件，并可通过 `metadataFieldList` 返回签名值 | 已写 |
| M12.fp-eq | DSL 调用方 | eq filePath 精确匹配 | `eq filePath "/dsl/F1.md"` | 仅命中 `/dsl/F1.md` | 已写 |
| M12.fp-prefix | DSL 调用方 | prefix filePath 目录过滤 | `prefix filePath "/dsl/"` | 命中 `/dsl/` 下所有文件含子目录，不含 `/other/` | 已写 |
| M12.fp-wildcard | DSL 调用方 | wildcard filePath 单级 | `wildcard filePath "/dsl/F?.md"` | 命中 F1–F6.md，不含 F5.pdf 和 nested | 已写 |
| M12.fp-wildcard-pen | DSL 调用方 | wildcard filePath `*` 穿透 `/` | `wildcard filePath "/dsl/F?.*"` | 命中 F1–F6.md + F5.pdf + nested.txt | 已写 |
| M12.fp-files-only | DSL 调用方 | filePath 仅返回 FILE | `prefix filePath "/"` | 仅返回 FILE 条目，不含 DIRECTORY | 已写 |
| M12.fp-no-match | DSL 调用方 | wildcard filePath 无命中 | `wildcard filePath "/dsl/X*"` | 空集 | 已写 |
| M12.fp-create | DSL 调用方 | virtual_path 创建时赋值 | `import file -> eq filePath` | 创建文件后 filePath 精确可查 | 已写 |
| M12.fp-rename | DSL 调用方 | virtual_path 目录改名联动 | `rename dir -> prefix filePath new/old` | 子树文件迁移到新路径，旧路径空集 | 已写 |


### 升级版 chunk 检索 / 文件级检索 / 兼容字段

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M13.a | DSL 调用方 | 三 mode × where 都生效 | `search` 三种 mode + where eq status active | 命中目标文件 | 已写 |
| M13.b | DSL 调用方 | 不传 metadataFieldList 不返 metadata | `search`（无 metadataFieldList） | metadata=None | 已写 |
| M13.c | DSL 调用方 | metadataFieldList 限制返回字段 | `search metadataFieldList=[keep]` | 仅含 keep | 已写 |
| M13.d | DSL 调用方 | where 圈定为空 | `search where eq status archived` | data=[] | 已写 |
| M13.e | DSL 调用方 | topK 边界 | `search topK=0/-1/缺失` | 文档化信封 | 已写 |
| M13.f | DSL 调用方 | system field in fileType（chunk） | `search where in fileType ["md"]` | 仅 md 文件命中 | 已写 |
| M13.g | DSL 调用方 | custom + system 合取（chunk） | `search where and:[custom, gt fileSize]` | 两端都满足才命中 | 已写 |
| M13.h | DSL 调用方 | where 进入召回 SQL（前过滤证明） | top1=A → 加 `where 排除 A` → top1=B | B 上位证明 where 是前过滤而非后过滤 | 已写 |
| M14.a | DSL 调用方 | fileTypeList 单独使用 | `search fileTypeList=["md"]` | md 命中、txt 不中 | 已写 |
| M14.b | DSL 调用方 | fileTypeList 与 where 合取 | `fileTypeList=["md"]` + `where in fileType ["txt"]` | 交集为空 | 已写 |
| M15.a | DSL 调用方 | searchFile 同 filePath 不重复 | 单文件 ≥2 chunk 命中 → searchFile | 同 filePath ==1 次（前置确认 chunk 多命中） | 已写 |
| M15.b | DSL 调用方 | searchFile + where + metadataFieldList | searchFile + active 过滤 + metadata 返回 | 命中 + metadata.value=active | 已写 |
| M15.c | DSL 调用方 | searchFile knCodeList 必填非空 | 不传 / `knCodeList=[]` | 文档化信封 | 已写 |
| M15.d | DSL 调用方 | system field in fileType（file） | `searchFile where in fileType ["md","txt"]` | 文件级聚合后扩展名过滤生效 | 已写 |
| M15.e | DSL 调用方 | system field gt createdAt（file） | `searchFile where gt createdAt past/future` | 时间窗口命中/不中 | 已写 |
| M15.f | DSL 调用方 | searchFile 系统+自定义混合 | `and: [eq custom status active, in fileType ["md","txt"]]`,收紧 fileType 后取空 | 自定义+系统两侧都生效 | 已写 |

## knowledge_build 场景总表

> **已弃用：** `knowledge_build` 独立路由（`file-to-markdown`、`build-markdown-index`、`file-to-markdown-index`）已全部移除。构建功能已整合到 `/api/v1/fileToMarkdownIndex`，作为 `knowledge_base` 模块的一部分。以下场景仅作历史参考。

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| A | 构建调用方 | ~~解析单个文件为 markdown~~ | ~~`file-to-markdown`~~ | ~~路由已移除~~ | 已弃用 |
| B | 构建调用方 | ~~从 markdown 构建 chunks~~ | ~~`build-markdown-index`~~ | ~~路由已移除~~ | 已弃用 |
| C | 构建调用方 | ~~一步式与两步式构建结果一致~~ | ~~`file-to-markdown -> build-markdown-index` 对比 `file-to-markdown-index`~~ | ~~路由已移除~~ | 已弃用 |
| D | 构建调用方 | ~~组合接口失败时正确短路~~ | ~~`file-to-markdown-index`~~ | ~~路由已移除~~ | 已弃用 |
| E | 构建调用方 | ~~构建链路异常可预测~~ | ~~覆盖不支持文件类型、非法 base64、空 markdown、未配置、embedding 异常~~ | ~~路由已移除~~ | 已弃用 |

## UserFS 本地文件系统存储场景总表

> **背景：** 当 `BY_QA_STORAGE_PROVIDER` 配置为路径耦合型 provider（`storage_path_bound_to_logical_path=True`，如 UserFS），外部存储路径与知识库逻辑路径绑定。目录改名/删除需同步移动或清理远端文件。原始文件和 Markdown 的存储定位规则由 provider 的 `build_original_location` / `build_markdown_location` 决定。
>
> **本组场景的 provider 路径约定（示例）：**
> - 原始文件：`{root}/{kb_code}/raw/{file_path}`
> - Markdown 文件：`{root}/{kb_code}/md/{file_path}.md`
> - 其中 `{root}` 为 provider 配置的存储根目录，`{file_path}` 为知识库内逻辑路径（含前导 `/`）。
>
> **验证方式：** 每个场景的操作完成后，除校验 API 返回结果外，还需直接检查本地文件系统（`os.path.exists`、`os.listdir`、文件内容比对等），确认存储路径与文件内容符合预期。
>
> 编号前缀 `U` 代表 UserFS。

### 基础写入与读取路径验证

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| U1 | 内容管理员 | 导入文件后验证原始文件落盘路径 | `knowledgeBases/create -> knowledgeItems/import(/docs/readme.md)` | 文件系统存在 `{root}/{kb_code}/raw/docs/readme.md`；文件内容与上传一致；`listDir` 可见该文件 | 已写 |
| U2 | 内容管理员 | 构建索引后验证 Markdown 落盘路径 | `knowledgeBases/create -> knowledgeItems/import(/docs/readme.md) -> fileToMarkdownIndex` | 文件系统存在 `{root}/{kb_code}/md/docs/readme.md.md`；内容为解析后的 Markdown 文本；`readFile` 可读取 | 已写 |
| U3 | 内容管理员 | 非 ASCII 文件名落盘路径 | `knowledgeBases/create -> knowledgeItems/import(/docs/中文文件.md) -> fileToMarkdownIndex` | 原始文件与 Markdown 文件名保留中文；路径可被 `os.path.exists` 正确识别；`listDir` / `readFile` 正常 | 已写 |
| U4 | 内容管理员 | 无扩展名文件落盘路径 | `knowledgeBases/create -> knowledgeItems/import(/docs/README)` | 原始文件路径无 suffix，存储 key 不含多余 `.`；`listDir` 可见 | 已写 |
| U5 | 普通使用者 | 下载原始文件从正确路径读取 | `knowledgeBases/create -> knowledgeItems/import -> downloadFile` | 返回字节流与 `{root}/{kb_code}/raw/{file_path}` 内容一致 | 已写 |
| U6 | 普通使用者 | 读取 Markdown 从正确路径读取 | `knowledgeBases/create -> knowledgeItems/import -> fileToMarkdownIndex -> readFile` | 返回文本与 `{root}/{kb_code}/md/{file_path}.md` 内容一致；行窗口截取正确 | 已写 |

### 多级目录路径验证

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| U7 | 目录管理员 | 多级目录导入后验证深层路径 | `create /A/B/C -> knowledgeItems/import(/A/B/C/file.md) -> fileToMarkdownIndex` | 原始文件位于 `{root}/{kb_code}/raw/A/B/C/file.md`；Markdown 位于 `{root}/{kb_code}/md/A/B/C/file.md.md`；中间目录在文件系统中存在（如有目录创建语义） | 已写 |
| U8 | 目录管理员 | 同文件名不同目录路径隔离 | `create /dir1 -> /dir2 -> knowledgeItems/import(/dir1/readme.md) -> knowledgeItems/import(/dir2/readme.md)` | 两个原始文件分别位于 `raw/dir1/readme.md` 和 `raw/dir2/readme.md`；内容各自独立；`listDir` 各自可见 | 已写 |
| U9 | 目录管理员 | 不同 KB 同名文件路径隔离 | `knowledgeBases/create KB1 -> knowledgeBases/create KB2 -> import /readme.md 到 KB1 -> import /readme.md 到 KB2` | KB1 文件在 `{root}/KB1/raw/readme.md`；KB2 文件在 `{root}/KB2/raw/readme.md`；互不干扰 | 已写 |

### 删除联动路径验证

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| U10 | 内容管理员 | 删除单文件后存储文件被移除 | `knowledgeBases/create -> knowledgeItems/import(/docs/a.md) -> fileToMarkdownIndex -> knowledgeItems/delete` | `{root}/{kb_code}/raw/docs/a.md` 不存在；`{root}/{kb_code}/md/docs/a.md.md` 不存在；API 返回软删成功 | 已写 |
| U11 | 目录管理员 | 删除目录后子树存储文件全部移除 | `create /A/B -> knowledgeItems/import(/A/B/file.md) -> fileToMarkdownIndex -> directories/delete(/A/B)` | `{root}/{kb_code}/raw/A/B/` 下所有文件不存在；`{root}/{kb_code}/md/A/B/` 下所有文件不存在；API 各接口不可见 | 已写 |
| U12 | 目录管理员 | 删除非空目录仅移除子树文件不误删兄弟 | `create /A/B -> create /A/C -> 各 import 文件 -> directories/delete(/A/B)` | `raw/A/B/` 下文件删除；`raw/A/C/` 下文件完好；`listDir(/A/C)` 仍可见 | 已写 |
| U13 | 知识库管理员 | 删除知识库后存储文件全部移除 | `knowledgeBases/create -> import 多文件 -> knowledgeBases/delete` | `{root}/{kb_code}/` 下所有 raw 和 md 文件不存在 | 已写 |

### 目录改名路径迁移验证

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| U14 | 目录管理员 | 改名后原始文件路径迁移 | `create /old -> knowledgeItems/import(/old/file.md) -> fileToMarkdownIndex -> directories/update(/old -> /new)` | `{root}/{kb_code}/raw/old/file.md` 不存在；`{root}/{kb_code}/raw/new/file.md` 存在且内容不变；`downloadFile(/new/file.md)` 正常 | 已写 |
| U15 | 目录管理员 | 改名后 Markdown 文件路径迁移 | 同 U14 | `{root}/{kb_code}/md/old/file.md.md` 不存在；`{root}/{kb_code}/md/new/file.md.md` 存在；`readFile(/new/file.md)` 返回原 Markdown 内容 | 已写 |
| U16 | 目录管理员 | 中间层改名联动深层文件路径迁移 | `create /A/B/C -> knowledgeItems/import(/A/B/C/file.md) -> fileToMarkdownIndex -> directories/update(/A/B -> /A/X)` | raw 与 md 下 `A/B/C/` → `A/X/C/`；`A/B/` 路径不存在；`A/X/C/file.md` 存在 | 已写 |
| U17 | 目录管理员 | 改名后旧路径不可读新路径可读 | 同 U16 | `downloadFile(/A/B/C/file.md)` 报 not found；`downloadFile(/A/X/C/file.md)` 返回文件内容 | 已写 |
| U18 | 目录管理员 | 连续两次改名路径链式迁移 | `create /A -> import -> rename A→B -> rename B→C` | `raw/A/` 和 `raw/B/` 不存在；`raw/C/` 存在 | 已写 |
| U19 | 目录管理员 | 改名后 API 浏览路径同步 | `create /old -> import -> rename old→new -> listDir` | `listDir` 中显示 `/new`，不显示 `/old`；子文件路径前缀正确 | 已写 |

### 存储状态与 API 一致性验证

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| U20 | 跨接口一致性 | 存储路径与 `downloadFile` 内容一致 | `knowledgeItems/import(/x.txt, content="hello") -> downloadFile` | `downloadFile` 返回 `b"hello"`；直接读 `{root}/{kb_code}/raw/x.txt` 也是 `b"hello"` | 已写 |
| U21 | 跨接口一致性 | 存储路径与 `readFile` Markdown 内容一致 | `import(/x.md, content="# hi") -> fileToMarkdownIndex -> readFile` | `readFile` 返回的 markdown 与 `{root}/{kb_code}/md/x.md.md` 文件内容一致 | 已写 |
| U22 | 跨接口一致性 | 删除后存储、浏览、检索三方一致 | `import -> fileToMarkdownIndex -> knowledgeItems/delete -> listDir -> knowledgeItems/search -> 文件系统检查` | API 不可见 + 存储文件不存在 + 检索不命中 | 已写 |

### 异常与边界

| 编号 | 用户角色 | 用户目标 | 典型调用链 | 核心预期 | 状态 |
| --- | --- | --- | --- | --- | --- |
| U23 | 存储运维 | 存储写入失败时 DB 不残留 | `knowledgeItems/import`（模拟 UserFS 写盘失败） | DB 中无该文件记录；文件系统中无残留文件；API 返回错误 | 已写 |
| U24 | 存储运维 | DB 提交失败时存储补偿清理 | `knowledgeItems/import`（模拟 commit 失败） | 已写入文件系统的原始文件被清理；文件系统无残留 | 已写 |
| U25 | 存储运维 | 目录改名时部分 move 失败后回滚 | `directories/update`（模拟第二个文件 move 失败） | 已移动的第一个文件被反向 move 回原路径；DB 不变；旧路径文件可读 | 已写 |
| U26 | 存储运维 | 并发导入同路径文件 | 两个请求同时 `import /same/path/file.md` | 仅一个成功；文件系统只有一个文件；DB 只有一条记录 | 已写 |
| U27 | 存储运维 | 存储根目录不存在时 `ensure_ready` 自动创建 | 启动服务（UserFS provider，`{root}` 不存在） | provider 自动创建根目录；后续 import 正常 | 已写 |

## 当前已落测试文件

| 文件 | 覆盖重点 | 状态 |
| --- | --- | --- |
| `tests/knowledge_build/integration/test_api_integration.py` | ~~`knowledge_build` 三接口正常/异常与组合链路等价性~~ | 已弃用（`knowledge_build` 独立路由已移除） |
| `tests/knowledge_base/integration/test_kb_api_stateful_integration.py` | 混合导入构建（`knowledgeItems/import` + `fileToMarkdownIndex`）、知识库改名、单文件/目录删除、多级目录改名删除、读取窗口校验、`downloadFile` 下载、真实搜索链路与失败保护；文档更新 UDT1–UDT8、UDT11–UDT14；文件元数据 MU1–MU6、MF1–MF2、MD1–MD3（批量操作、原子失败、幂等、校验、front matter、删除联动和并发更新）；metadataSearch 分页、系统字段与精确过滤；导入重复内容 31–34；真实 OpenGauss checksum 锁范围 CLK1–CLK3；zip 批量导入与引用改写 Z1–Z17；稳定 Markdown 引用 R1–R17 | 有效 |
| `tests/knowledge_base/integration/test_userfs_batch1.py` | U1–U9:基础读写路径、多级目录隔离、跨 KB 隔离 | 有效 |
| `tests/knowledge_base/integration/test_userfs_batch2.py` | U10–U18:删除联动、目录改名路径迁移 | 有效 |
| `tests/knowledge_base/integration/test_userfs_batch3.py` | U19–U27:跨接口一致性、异常补偿与边界（含 U24 commit 失败清理）；UDT9–UDT10：更新存储写失败/数据库提交失败的原对象补偿、路径映射存储原 locator 覆盖 | 有效 |

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
