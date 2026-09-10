# 上传并发与目录唯一性

目录重复创建返回同一目录；文件重复创建保留原有契约：同路径仅一个请求成功，其他请求返回文件路径已存在。ZIP 的已有文件覆盖规则不变。

上传先以短事务确保父目录和新目录元数据已提交，再开始文件事务。文件事务沿路径读取父目录并持有共享行锁，禁止在上传过程中修改祖先目录，但允许其他文件上传共享这些目录。不再使用 `knowledge_fs_entry` 全表写锁。

目录重命名、移动和删除先获取目标行的排他锁，再读取子树，确保包含刚提交的上传文件。对正在变更的同一目录仍可能等待；同路径文件和同知识库相同 checksum 仍按现有规则竞争。

文件存储调用仍在文件事务内。文件上传失败会回滚文件及其引用，并按现有逻辑清理对象；已提交的父目录及其元数据会保留，不自动删除。ZIP 仍同步返回，这次修复不提供请求级幂等键或后台任务。

## 数据库升级

新增迁移 `042_knowledge_fs_entry_upload_uniqueness.sql` 补齐旧库可能缺失的同级、顶层有效节点唯一索引。不修改已有迁移文件。若存在历史重复有效节点，迁移失败，保留数据，需要先整理重复目录的子节点、元数据和引用后重新升级。

发布前确认实际索引为有效唯一索引，并检查重复节点：

```sql
SELECT knowledge_base_id, parent_entry_id, name, count(*)
FROM knowledge_fs_entry
WHERE is_deleted = false AND is_root = false
GROUP BY knowledge_base_id, parent_entry_id, name
HAVING count(*) > 1;
```

## 集成验证

`tests/knowledge_base/integration/test_upload_concurrency_real_integration.py` 使用真实 OpenGauss、生产仓储及服务，独立随机 schema，在结束后删除该 schema。存储适配器为可控制的测试实现，用事件精确挂起外部写入，不模拟数据库锁。

覆盖目录 24 路并发重用、文件 16 路同路径竞争、失败重试、软删除后重建、文件目录重名、创建者回滚、祖先目录保护、真实目录重命名/移动/删除、缺失索引升级和历史重复数据拒绝升级。

ZIP 场景分别导入 1 个和 3 个 ZIP，每个 513 个文件。保持一个存储请求挂起时，要求同目录、同知识库其他目录、其他知识库的三个上传探针完成，且至少 100 次存储写入完成；检查不存在全表 `ShareRowExclusiveLock`。释放存储后，要求全部 ZIP 文件成功，路径没有重复。

使用项目自己的本地数据库配置运行，代理环境变量置空：

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= \
no_proxy=127.0.0.1,localhost http_proxy= https_proxy= \
PYTHONPATH=.:src .venv/bin/python -m pytest \
  tests/knowledge_base/integration/test_upload_concurrency_real_integration.py -q
```

这证明的是大量文件及慢存储条件下的数据库并发行为，不是 Java HTTP 存储链路或生产吞吐量压测。

## 本次验证结果（2026-09-09）

- 新增真实 OpenGauss 集成测试：15 passed，20.91 秒。
- 既有 stateful API 回归（`zip or directory or parent or duplicate_checksum`）：35 passed，覆盖 ZIP 覆盖、引用、目录元数据及校验语义。
- 知识库单元测试：892 passed、6 skipped。
- 对照实验通过临时 pytest 插件仅恢复文件创建的旧全表锁；单 ZIP 非阻塞用例如预期失败，探针在 `LOCK TABLE` 处等待并超时。插件未写入生产代码。
