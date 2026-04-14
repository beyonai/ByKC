# 知识模块处理流程

相关文档：

- [api.md](./api.md)
- [framework.md](./framework.md)
- [design.md](./design.md)
- [minio.md](./minio.md)

## 总流程

```mermaid
flowchart TD
    A["创建知识库"] --> B["创建目录"]
    B --> C["上传文档"]
    C --> D["写入 MinIO 原始文件"]
    D --> E["触发知识构建"]
    E --> F["文件解析为 Markdown"]
    F --> G["写入 Markdown 对象"]
    G --> H["Markdown 切片"]
    H --> I["切片向量化"]
    I --> J["写入元数据与检索投影"]
    J --> K["读取 / 下载 / 检索"]
```

## 导入与入库流程

```mermaid
flowchart TD
    A["knowledgeItems/import"] --> B["生成 import_request_id"]
    B --> C["原始文件上传到 MinIO tmp 前缀"]
    C --> D["写入 knowledge_item / version / chunk 等数据库记录"]
    D --> E["事务成功后晋升正式对象键"]
    E --> F["导入完成"]
```

说明：

- 上传接口接收表单文件
- 先写临时对象，再完成数据库入库
- 正式对象键只在事务成功后可见

## 文件解析流程

```mermaid
flowchart TD
    A["原始文件对象"] --> B["读取文件内容"]
    B --> C["DocumentChunkingService.extract_text_from_file"]
    C --> D{"文件类型"}
    D -->|txt/md| E0["UTF-8 解码"]
    D -->|csv| E1["csv reader"]
    D -->|pdf| E2["PyMuPDF"]
    D -->|docx| E3["python-docx"]
    D -->|pptx| E4["python-pptx"]
    D -->|xlsx| E5["openpyxl"]
    E0 --> F["Markdown / text"]
    E1 --> F
    E2 --> F
    E3 --> F
    E4 --> F
    E5 --> F
```

说明：

- 目标是尽快拿到可构建文本
- 复杂版式和高保真解析不在当前范围内

## 构建流程

```mermaid
flowchart TD
    A["Markdown content"] --> B["按文件类型选择切片策略"]
    B --> C["MarkdownHeaderTextSplitter / RecursiveCharacterTextSplitter"]
    C --> D["补充 line range / char range"]
    D --> E["批量调用 embedding API"]
    E --> F["输出 chunk payload 列表"]
    F --> G["写入 knowledge_item_chunk 与 embedding 表"]
```

说明：

- Markdown 优先按标题切片
- 纯文本走通用字符切片
- 最终输出统一的 chunk payload

## 文件读取流程

```mermaid
flowchart TD
    A["readFile"] --> B["定位 current_version_id"]
    B --> C["查询 knowledge_fetch_cache_index"]
    C -->|命中| D["直接读取本地缓存文件"]
    C -->|未命中| E["从 MinIO 下载 Markdown 对象"]
    E --> F["刷新缓存索引"]
    D --> G["按 startLine/endLine 截取"]
    F --> G
    G --> H["返回 Markdown 文本"]
```

## 文件下载流程

```mermaid
flowchart TD
    A["downloadFile"] --> B["定位版本记录中的原始对象"]
    B --> C["读取原始文件对象"]
    C --> D["直接返回 application/octet-stream"]
```

## 检索流程

```mermaid
flowchart TD
    A["knowledge-items/search"] --> B["解析 query 与过滤条件"]
    B --> C["文本召回"]
    B --> D["向量召回"]
    C --> E["服务层融合排序"]
    D --> E
    E --> F["返回 chunk 命中列表"]
```
