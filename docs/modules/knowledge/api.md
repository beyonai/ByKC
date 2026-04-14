# 知识模块接口说明

## 文档范围

本文档定义知识模块对外开放的接口。

- Base URL：`/api/v1`
- 协议：`HTTP`
- 默认返回：`application/json`
- 上传文档接口请求体：`multipart/form-data`
- 下载文件接口成功响应：`application/octet-stream`

## 通用约定

### 成功响应

除下载文件接口外，其余接口统一使用如下响应信封：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

说明：

- `resultCode`：接口状态码，`0` 表示成功，`-1` 表示失败
- `resultMsg`：接口返回说明
- `resultObject`：业务返回体；无额外返回时可省略或返回空对象

### 失败响应

失败时返回 JSON，结构如下：

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

### 路径字段约定

- `knCode`：知识库编码
- `directoryPath`：目录路径，以 `/` 开头，不包含知识库名称
- `filePath`：文件路径，以 `/` 开头，不包含知识库名称
- `name`：目录遍历或模式匹配结果中的完整路径

## 接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeBases/create` | 创建知识库 |
| `POST` | `/api/v1/knowledgeBases/update` | 修改知识库 |
| `POST` | `/api/v1/knowledgeBases/delete` | 删除知识库 |
| `POST` | `/api/v1/directories/create` | 创建目录 |
| `POST` | `/api/v1/directories/update` | 修改目录 |
| `POST` | `/api/v1/directories/delete` | 删除目录 |
| `POST` | `/api/v1/knowledgeItems/import` | 上传文档 |
| `POST` | `/api/v1/knowledgeItems/delete` | 删除文档 |
| `POST` | `/api/v1/listDir` | 获取目录内容 |
| `POST` | `/api/v1/glob` | 按路径模式匹配 |
| `POST` | `/api/v1/readFile` | 读取文件内容 |
| `POST` | `/api/v1/downloadFile` | 下载原始文件 |
| `POST` | `/api/v1/fileToMarkdownIndex` | 触发知识构建 |
| `POST` | `/api/v1/knowledge-items/search` | 知识检索 |

## 知识库管理

### `POST /api/v1/knowledgeBases/create`

创建知识库。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knName` | string | 是 | 知识库名称 |
| `knDescription` | string | 否 | 知识库描述 |

请求示例：

```json
{
  "knName": "人力制度知识库",
  "knDescription": "公司人事制度与流程文档"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knCode": "KN202604140001",
    "knName": "人力制度知识库",
    "knDescription": "公司人事制度与流程文档"
  }
}
```

### `POST /api/v1/knowledgeBases/update`

修改知识库名称或描述。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `knName` | string | 否 | 新知识库名称 |
| `knDescription` | string | 否 | 新知识库描述 |

请求示例：

```json
{
  "knCode": "KN202604140001",
  "knName": "人力制度知识库（新版）",
  "knDescription": "更新后的公司人事制度与流程文档"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success"
}
```

### `POST /api/v1/knowledgeBases/delete`

删除指定知识库。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |

请求示例：

```json
{
  "knCode": "KN202604140001"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success"
}
```

## 目录管理

### `POST /api/v1/directories/create`

在指定知识库下面创建目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需创建的目录路径，以 `/` 开头，不包括知识库名称，支持递归创建 |
| `directoryDescription` | string | 否 | 目录描述 |

### `POST /api/v1/directories/update`

修改指定知识库的目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需要修改的目录路径，以 `/` 开头，不包括知识库名称 |
| `directoryName` | string | 是 | 新目录名称，仅修改 `directoryPath` 最后一个层级的名称 |

### `POST /api/v1/directories/delete`

删除指定知识库的目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需删除的目录路径，以 `/` 开头，不包括知识库名称 |

## 文档管理

### `POST /api/v1/knowledgeItems/import`

将文档上传到指定知识库下面。

请求体：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 上传到知识库后的文件全路径，以 `/` 开头，不包括知识库名称 |
| `fileDescription` | string | 否 | 文件描述 |
| `fileContent` | file | 是 | 文件二进制内容 |

表单示例：

```bash
curl -X POST http://localhost:8000/api/v1/knowledgeItems/import \
  -F "knCode=KN202604140001" \
  -F "filePath=/Policies/Holiday/leave-policy.pdf" \
  -F "fileDescription=请假制度原文" \
  -F "fileContent=@./leave-policy.pdf"
```

### `POST /api/v1/knowledgeItems/delete`

删除指定知识库下面的文档。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需删除的文档全路径，以 `/` 开头，不包括知识库名称 |

## 目录与文件读取

### `POST /api/v1/listDir`

获取指定知识库目录下的所有文件和文件夹。

### `POST /api/v1/glob`

基于路径模式匹配查找指定知识库下面的文件或目录。

### `POST /api/v1/readFile`

根据文件路径读取指定知识库下的原始文件内容，并以 Markdown 文本形式返回。

### `POST /api/v1/downloadFile`

根据文件路径下载指定知识库下的原始文件。

成功响应：

- `200 OK`
- `Content-Type: application/octet-stream`
- 响应体为原始文件二进制字节流

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found",
  "resultObject": {}
}
```

## 知识构建

### `POST /api/v1/fileToMarkdownIndex`

根据文件路径异步构建指定知识库下的文件，自动完成原始文件转 Markdown、切片和切片向量化处理。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需构建的文档全路径，以 `/` 开头，不包括知识库名称 |

## 知识检索

### `POST /api/v1/knowledge-items/search`

根据用户提问召回对应的知识切片。
