# 知识构建处理流程

## 总流程

```mermaid
flowchart TD
    A["客户端请求"] --> B{"接口类型"}
    B -->|file-to-markdown| C["文件解析"]
    B -->|build-markdown-index| D["markdown 切片 + embedding"]
    B -->|file-to-markdown-index| E["文件解析后进入切片构建"]
    C --> F["返回 md_content"]
    D --> G["返回 chunks"]
    E --> H["返回 md_content + chunks"]
```

说明：

- 当前模块提供 3 个示例接口
- 处理目标是输出构建中间产物
- 不直接负责知识库落库

## 文件解析流程

```mermaid
flowchart TD
    A["base64 文件内容"] --> B["校验文件类型"]
    B --> C["base64 解码"]
    C --> D["DocumentChunkingService.extract_text_from_file"]
    D --> E{"文件类型"}
    E -->|pdf| F["PyMuPDF"]
    E -->|docx| G["python-docx"]
    E -->|pptx| H["python-pptx"]
    E -->|xlsx| I["openpyxl"]
    F --> J["markdown/text"]
    G --> J
    H --> J
    I --> J
```

说明：

- 文件解析是示例级实现
- 目标是尽快拿到可构建文本
- 复杂版式和高保真解析不在当前范围内

## 构建流程

```mermaid
flowchart TD
    A["markdown content"] --> B["按文件类型选择切片策略"]
    B --> C["MarkdownHeaderTextSplitter / RecursiveCharacterTextSplitter"]
    C --> D["补充 line range / char range"]
    D --> E["批量调用 embedding API"]
    E --> F["输出 chunk payload 列表"]
```

说明：

- markdown 优先按标题切片
- 纯文本走通用字符切片
- 最终输出统一的 chunk payload

## 组合流程

```mermaid
flowchart TD
    A["file-to-markdown-index"] --> B["先解析文件"]
    B --> C["得到 md_content"]
    C --> D["再执行切片与 embedding"]
    D --> E["返回 md_content + chunks"]
```

说明：

- 这个接口适合快速联调
- 更像示例入口，不一定适合生产长链路调用

## 推荐生产链路

```mermaid
flowchart TD
    A["第三方知识构建平台"] --> B["文档解析 / 清洗 / 切片 / embedding"]
    B --> C["产出 markdown + chunks + metadata"]
    C --> D["knowledge_base 导入接口"]
    D --> E["知识库管理与检索"]
```

说明：

- 生产检索效果通常更多取决于构建质量
- 当前 `knowledge_build` 主要用于示例和参考
- 若追求更稳定的检索效果，建议优先接入第三方知识构建能力
