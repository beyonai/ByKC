# 知识构建模块框架文档

## 模块定位

`by_qa` 的知识域当前拆分为两个并列模块：

- `knowledge_base`：负责知识库管理、导入结果落库和检索
- `knowledge_build`：负责文件解析、切片和 embedding 构建

需要特别说明的是，当前开源仓库中的 `knowledge_build` 更偏向示例实现，适合：

- 本地联调
- 协议参考
- 轻量文档导入验证

如果要保证生产检索效果，建议优先接入第三方知识构建能力，再把构建产物导入当前 `knowledge_base` 模块。

相关文档：

- [framework.md](./framework.md)
- [design.md](./design.md)
- [process.md](./process.md)

## 目录结构

```text
src/by_qa/
├── knowledge_common/
│   ├── exceptions.py
│   └── schemas.py
├── knowledge_build/
│   ├── api/
│   │   ├── routes.py
│   │   └── schemas.py
│   ├── services/
│   │   └── document_chunking_service.py
│   └── runtime.py
└── knowledge_base/
    ├── api/
    ├── repositories/
    ├── services/
    └── infrastructure/
```

## 分层职责

### `knowledge_common`

承载知识域共享的最小公共元素：

- 通用配置异常
- chunk payload

### `knowledge_build.api`

承载知识构建对外开放的 3 个示例接口：

- `/api/v1/file-to-markdown`
- `/api/v1/build-markdown-index`
- `/api/v1/file-to-markdown-index`

### `knowledge_build.services`

承载文档解析、切片和 embedding 构建能力。

### `knowledge_build.runtime`

承载知识构建模块自己的配置校验和服务装配，不再依赖 `knowledge_base` 的运行时装配。

## 安装方式

只安装知识构建模块：

```bash
pip install by-qa[knowledge-build]
```

安装知识库：

```bash
pip install by-qa[knowledge]
```

安装全部模块：

```bash
pip install by-qa[all]
```

## 推荐使用方式

当前 `knowledge_build` 适合作为参考实现和兼容入口。

更推荐的生产使用方式是：

1. 使用第三方文档解析和知识构建能力
2. 输出 markdown、chunk 和 embedding
3. 再通过 `knowledge_base` 的导入接口写入知识库
