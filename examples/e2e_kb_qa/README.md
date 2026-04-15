# Packaged E2E KB Example

这个示例按 `pip install by-qa[all]` 的使用方式组织，脚本都可以直接执行：

- `bash ./start_kb_service.sh`
- `python ./run_kb_flow.py`
- `python ./run_instant_qa.py`

## 1. 安装

```bash
pip install by-qa[all]
```

## 2. 进入示例目录

下面的命令都默认在当前目录执行：

```bash
cd examples/e2e_kb_qa
```

## 3. 配置 `.env`

先在当前示例目录准备 `.env`：

```bash
cp ../../.env.example .env
```

然后至少确认这些变量已经在 `.env` 中配置好：

- `HOST`
- `PORT`
- `SERVICE_NAME`
- `EMBEDDING_BASE_URL`
- `EMBEDDING_API_KEY`
- `EMBEDDING_MODEL_NAME`
- `EMBEDDING_DIMENSION`
- `LLM_API_KEY`
- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_USERNAME`
- `REDIS_PASSWORD`
- `REDIS_DATABASE`
- `KB_OPENGAUSS_DSN`
- `KB_MINIO_ENDPOINT`
- `KB_MINIO_ACCESS_KEY`
- `KB_MINIO_SECRET_KEY`
- `KB_MINIO_BUCKET`
- `KB_MINIO_MARKDOWN_BUCKET`
- `KB_MINIO_SECURE`

示例专用参数通过脚本入参控制，不需要写进 `.env.example`。默认运行目录会落在：

```text
examples/e2e_kb_qa/.runtime
```

## 4. 拉起服务

在第一个终端执行：

```bash
bash ./start_kb_service.sh
```

如果要显式覆盖运行目录，可以通过脚本参数传入：

```bash
bash ./start_kb_service.sh \
  --runtime-dir ./.runtime
```

这个脚本会读取当前目录下的 `.env`，然后：

- 构建并启动 openGauss / MinIO
- 执行初始化脚本
- 启动 `by-qa` FastAPI 服务
- 在应用生命周期里输出启动配置摘要并完成服务注册

## 5. 执行知识构建、入库与目录查询

在第二个终端执行：

```bash
python ./run_kb_flow.py
```

文档来源是一个目录，必须通过 `--dir` 显式传入。

也可以通过入参覆盖示例专用配置：

```bash
python ./run_kb_flow.py \
  --dir /absolute/path/to/your-documents \
  --runtime-dir ./.runtime
```

这个脚本会依次调用：

- `/api/v1/fileToMarkdownIndex`
- `/api/v1/knowledgeItems/import`
- `/api/v1/listDir`
- `/api/v1/glob`

它会按文件名顺序遍历目录中的所有受支持文件，并逐个完成知识构建与导入。

## 6. 执行即时问答

在第三个终端执行：

```bash
python ./run_instant_qa.py --query "根据员工手册，员工请假审批需要提前多久提交？"
```

默认会直接流式输出回答内容。

也可以自定义问题：

```bash
python ./run_instant_qa.py --query "员工忘记打卡后应该怎么处理？"
```

如果你希望把三个脚本都指向同一个运行目录，可以显式传入：

```bash
python ./run_instant_qa.py \
  --runtime-dir ./.runtime \
  --query "根据员工手册，员工请假审批需要提前多久提交？"
```

如果你只想在最后一次性看到答案，可以加：

```bash
python ./run_instant_qa.py \
  --no-stream \
  --query "根据员工手册，员工请假审批需要提前多久提交？"
```

如果你想同时看到检索和节点事件，可以加：

```bash
python ./run_instant_qa.py \
  --verbose-events \
  --query "根据员工手册，员工请假审批需要提前多久提交？"
```

## 7. 重置数据

如果你想重置当前环境里的知识库数据表和 MinIO 对象，可以在示例目录执行：

```bash
set -a
source ./.env
set +a
python ../../scripts/reset_kb_data.py
```

这个命令会清掉当前 `.env` 配置下的全部知识库数据，不只这一套示例数据。
