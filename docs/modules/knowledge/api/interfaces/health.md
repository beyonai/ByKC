# health

## 功能描述

检查当前服务进程是否可访问并正常响应。该接口主要供部署探针、负载均衡器和运维监控判断服务存活状态。

检查服务进程是否能够响应 HTTP 请求。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/health` |
| 请求体 | 无 |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Accept` | 否 | `application/json` | 期望返回 JSON |

## 请求示例

```http
GET /health HTTP/1.1
Host: localhost:8000
Accept: application/json
```

## 成功响应示例

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"ok"}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `status` | string | 是 | 服务存活状态；正常时为 `ok` |

该接口不使用知识模块统一 JSON 信封，因此没有 `resultObject.data` 字段。

## 特殊逻辑

- 路径不包含 `/api/v1`。
- 不访问 OpenGauss、MinIO、Redis、LLM 或 Embedding 服务。
- 只证明 HTTP 进程可响应，不等价于知识模块所有依赖已就绪。
- 不使用知识模块的 `resultCode/resultMsg/resultObject` 响应信封。

## 失败响应

该处理器本身没有业务失败分支。如进程不可达，连接层会返回超时、连接拒绝或网关 `5xx`，不保证 JSON 格式。

以下是网关无法连接服务进程时的示意响应，具体文案由部署环境决定：

```http
HTTP/1.1 503 Service Unavailable
Content-Type: text/plain

upstream service unavailable
```

---

[返回 API 导航](../README.md)
