# API 通用约定

## 服务信息

| 项目 | 值 |
| --- | --- |
| Base URL | `/api/v1` |
| 协议 | HTTP |
| 默认请求/响应 | `application/json` |
| 文件上传 | `multipart/form-data` |
| 文件下载 | 成功时返回文件字节流 |

## 成功响应

除下载文件接口外，统一使用响应信封：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `resultCode` | string | `0` 表示成功，`-1` 表示失败 |
| `resultMsg` | string | 结果说明；异步受理通常为 `accepted` |
| `resultObject` | object | 业务返回体；无数据时为空对象 |

## 失败响应

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

HTTP 状态码用于区分运输结果，`resultCode` 用于保持业务协议兼容。常见状态码：

| HTTP 状态码 | 语义 |
| --- | --- |
| `200` | 成功或可识别的业务失败 |
| `202` | 异步任务已受理 |
| `422` | 请求结构或参数校验失败 |
| `503` | 知识模块依赖未配置或不可用 |
| `500` | 未预期的服务端异常 |

## 兼容路径

以下接口同时接受 `knowledgeItems` 和 `knowledge-items` 路径：

- `import`
- `update`
- `delete`
- `move`
- `search`
- `references`

文档使用 `knowledgeItems` 作为主路径。

## 分页

使用分页的接口通常接受 `pageNum` 和 `pageSize`，页码从 `1` 开始。具体上限以接口文档和服务端校验为准。

## 相关文档

- [API 导航](README.md)
- [元数据与 Agent DSL](metadata-and-dsl.md)
- [异步实体处理](entity-processing.md)
