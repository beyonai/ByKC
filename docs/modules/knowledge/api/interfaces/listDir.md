# listDir

## 功能描述

列出指定知识库目录下的直接子目录和文件。接口提供类似文件系统的目录浏览能力，不递归返回更深层级内容。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/listDir` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

获取指定知识库目录下的所有文件和文件夹。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 目录路径，以 `/` 开头，不包括知识库名称 |

## 请求示例

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事"
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "knCode": "1",
        "name": "/制度/人事/考勤",
        "type": "directory",
        "size": 0
      },
      {
        "knCode": "1",
        "name": "/制度/人事/请假制度.pdf",
        "type": "file",
        "size": 245760
      }
    ]
  }
}
```

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "directory not found: /制度/人事",
  "resultObject": {}
}
```

## 特殊逻辑

- 只返回直接子项，不递归遍历整个子树。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `directoryPath` 必须以 `/` 开头，不包含知识库名称，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
