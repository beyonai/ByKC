# fileToMarkdownIndex

> 设计状态：本文描述已经确认的目标契约，业务代码尚未切换到该实现。
> 完整设计见 [文件与目录后台构建设计](../../file-build-background-processing-design.md)。

## 功能描述

为知识库中的指定文件或目录发起完整知识构建，包括转 Markdown、分块、
向量化和检索索引。接口只负责形成目录快照、创建批次和文件任务，不在
HTTP 请求内执行构建。

目录按受理时的文件 ID 清单递归处理。之后新建或移入的文件不加入本批次；
已经纳入的文件即使移动或重命名，仍按文件 ID 继续处理。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/fileToMarkdownIndex` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

## 请求参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 知识库编码 |
| `filePath` | string | 是 | - | 文件或目录路径；`/` 表示知识库根目录 |
| `force` | boolean | 否 | `false` | 是否绕过已完成任务复用；相同活动任务仍复用 |

请求不增加 `directoryPath`。服务端解析 `filePath` 当前指向的资源类型：

- 文件：`scope=SINGLE_FILE`；
- 目录：`scope=DIRECTORY`，递归处理全部子目录；
- 路径不存在：请求失败，不创建 batch。

## 请求示例

单文件：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf",
  "force": false
}
```

目录：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事",
  "force": false
}
```

全库：

```json
{
  "knCode": "1",
  "filePath": "/"
}
```

## 成功响应示例

单文件和目录都返回完整批次摘要：

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "fb-20260903-0001",
    "scope": "DIRECTORY",
    "targetPath": "/制度/人事",
    "taskType": "FILE_BUILD",
    "candidateCount": 80,
    "eligibleCount": 78,
    "acceptedCount": 60,
    "reusedCount": 18,
    "skippedCount": 2,
    "returnedTaskCount": 20,
    "tasksTruncated": true,
    "tasks": [
      {
        "taskId": "12001",
        "status": "PENDING",
        "fileId": "2048",
        "filePathSnapshot": "/制度/人事/请假制度.pdf",
        "reused": false
      },
      {
        "taskId": "11800",
        "status": "SUCCEEDED",
        "fileId": "2049",
        "filePathSnapshot": "/制度/人事/考勤制度.pdf",
        "reused": true
      }
    ]
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | `0` 表示已受理 |
| `resultMsg` | string | 是 | 受理成功时为 `accepted` |
| `resultObject.batchId` | string | 是 | 本次请求的批次 ID |
| `resultObject.scope` | string | 是 | `SINGLE_FILE` 或 `DIRECTORY` |
| `resultObject.targetPath` | string | 是 | 请求路径快照，仅供审计和展示 |
| `resultObject.taskType` | string | 是 | 固定为 `FILE_BUILD` |
| `resultObject.candidateCount` | integer | 是 | 快照内候选文件数 |
| `resultObject.eligibleCount` | integer | 是 | 新建任务数加复用命中数 |
| `resultObject.acceptedCount` | integer | 是 | 本批次新建文件任务数 |
| `resultObject.reusedCount` | integer | 是 | 命中已有任务数 |
| `resultObject.skippedCount` | integer | 是 | 受理前跳过数 |
| `resultObject.returnedTaskCount` | integer | 是 | `tasks` 实际返回数 |
| `resultObject.tasksTruncated` | boolean | 是 | 任务预览是否超过 20 条 |
| `resultObject.tasks` | array[object] | 是 | 新建或复用命中的任务预览 |
| `resultObject.tasks[].taskId` | string | 是 | 新建或命中的已有 task ID |
| `resultObject.tasks[].status` | string | 是 | 当前任务状态 |
| `resultObject.tasks[].fileId` | string | 是 | 稳定文件 ID |
| `resultObject.tasks[].filePathSnapshot` | string | 是 | 非权威受理路径快照 |
| `resultObject.tasks[].reused` | boolean | 是 | 是否命中已有任务 |

计数满足：

```text
candidateCount = acceptedCount + reusedCount + skippedCount
eligibleCount = acceptedCount + reusedCount
```

## 任务复用

复用判断使用：

```text
fileId + checksum + isDeleted + buildProfileHash
```

文件名和路径不参与比较。

- 相同 `PENDING/RUNNING`：命中活动任务；
- 相同 `SUCCEEDED`：仅在当前 Markdown、分块、向量和检索投影仍完整时命中；
- 相同 `UNSUPPORTED`：命中已有不支持结论；
- `FAILED/SKIPPED`：不复用；
- `force=true`：绕过 `SUCCEEDED/UNSUPPORTED`，不绕过相同活动任务。

复用命中不会在当前 batch 新增任务。纯复用 batch 立即完成，不等待已有
活动任务，也不会为当前 batch 重发文件 Callback。

文件存在但缺少 checksum 或原始对象位置时计入 `skippedCount`，原因是
`SOURCE_NOT_READY`。不支持的文件仍会创建任务，再由 Worker 进入
`UNSUPPORTED`，从而保留任务状态和文件 Callback。

## 状态和 Callback

- Batch/task 精确状态通过 `processingBatchStatus` 和
  `processingTaskStatus` 查询，任务类型为 `FILE_BUILD`；
- `fileBuildStatus` 和 `buildResult` 继续提供按当前路径查询最新构建的兼容
  能力；
- 文件任务终态发布 `build.file.completed`（`eventVersion=2`）；
- 批次终态发布 `build.batch.completed`（`eventVersion=2`）；
- Callback 使用服务端配置的统一 Publisher，不接受请求级 URL；
- Callback 为 best-effort，状态查询是最终事实来源。

## 并发变更

构建期间允许用户修改、删除、移动和重命名：

- 内容修改：旧活动任务进入 `SKIPPED/INPUT_STALE`；
- 文件或目录删除：相关活动任务进入 `SKIPPED/SOURCE_DELETED`；
- 新请求输入不同：旧活动任务进入 `SKIPPED/SUPERSEDED`；
- 移动或重命名：任务继续执行；
- 最终提交只使用文件 ID 和数据库 checksum/isDeleted 快照做校验。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "target path not found: /制度/人事",
  "resultObject": {}
}
```

实际 `resultMsg` 会说明参数、知识库或路径错误；请勿只根据文案分支处理。

---

[返回 API 导航](../README.md)
