# knowledgeItems-references

## 功能描述

查询指定知识文件的引用关系，包括该文件引用的目标和引用该文件的来源。接口用于引用分析、影响评估和文档关系展示，不等同于实体语义关系查询。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/references` |
| 兼容路径 | `/api/v1/knowledge-items/references` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

查询指定文件的 Markdown 引用关系。兼容别名：`POST /api/v1/knowledge-items/references`。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 查询对象文件路径，以 `/` 开头，不包括知识库名称 |
| `direction` | string | 否 | 查询方向：`inbound`、`outbound`、`all`；默认 `inbound` |

`direction` 语义：

- `inbound`：查询“谁引用了 `filePath`”。
- `outbound`：查询“`filePath` 引用了谁”。
- `all`：同时返回 inbound 和 outbound。

响应体固定包含 `inbound` 与 `outbound` 两个数组；未被本次 `direction` 请求的方向返回空数组。

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/附件/请假单模板.docx",
  "direction": "all"
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "inbound": [
      {
        "sourcePath": "/制度/人事/请假制度.md",
        "originalTarget": "./附件/请假单模板.docx",
        "targetSuffix": "",
        "targetPath": "/制度/人事/附件/请假单模板.docx",
        "status": "resolved"
      }
    ],
    "outbound": [
      {
        "sourcePath": "/制度/人事/附件/请假单模板.docx",
        "originalTarget": "../员工手册.md#请假",
        "targetSuffix": "#请假",
        "targetPath": "/制度/员工手册.md",
        "status": "resolved"
      }
    ]
  }
}
```

`inbound` 元素字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `sourcePath` | string | 包含该 Markdown 引用的来源文件路径 |
| `originalTarget` | string | 来源 Markdown 中原始写入的引用目标 |
| `targetSuffix` | string | 引用目标中的后缀片段；无后缀时为空字符串 |
| `targetPath` | string | 当前匹配到的目标路径；broken 引用返回删除时记录的目标路径 |
| `status` | string | 引用状态，可能为 `resolved`、`unresolved` 或 `broken` |

`outbound` 元素字段同 inbound；其中 `sourcePath` 固定为本次请求的 `filePath`。

查询语义：

- `inbound` 且 `filePath` 指向当前存在的文件时，按当前文件对应的 target id 查询 `resolved` 引用；目标文件移动后仍可通过当前路径查询。
- `inbound` 且目标文件尚未上传或已删除时，按引用表中的 `target_path` 查询 `unresolved` / `broken` 引用。
- `outbound` 按 `filePath` 定位 source 文件，返回该 Markdown 文件中登记的可管理文件引用；resolved 引用的 `targetPath` 输出目标当前路径，unresolved / broken 引用输出引用表中的待匹配路径或删除前路径。
- `all` 同时执行 inbound 与 outbound，并分别写入 `resultObject.inbound` 和 `resultObject.outbound`。
- 默认不返回已删除 source 文件产生的引用；outbound 查询的 source 文件不存在或已删除时返回空数组。
- 该接口只查询引用关系，不读取或修改 Markdown 内容。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

实际 `resultMsg` 会说明参数、资源状态或依赖失败原因；请勿只根据文案分支处理。

## 特殊逻辑

- 该接口是 Markdown 物理引用视图，不等价于 KnowledgeEntity 逻辑语义关系。
- `inbound`、`outbound`、`all` 分别查询入向、出向或双向引用。
- 引用状态可为 `resolved`、`unresolved` 或 `broken`。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。
- `sourcePath` 中每个元素都必须是以 `/` 开头的库内文件或目录路径。

---

[返回 API 导航](../README.md)
