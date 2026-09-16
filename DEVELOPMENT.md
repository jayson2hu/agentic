# L2 异地开发指南

本仓库属于 [CodePick 四层平台](https://github.com/jayson2hu/codepick-docs)。建议四个代码仓库保持同级目录，以便查阅关联实现；每个项目使用独立虚拟环境。

## 克隆与环境

需要 Git 和 Python 3.12 或更新版本。以下命令都从本仓库根目录执行。

```sh
git clone https://github.com/jayson2hu/agentic.git
cd agentic
python -m venv .venv
```

激活环境：Windows PowerShell 使用 `.venv\Scripts\Activate.ps1`；macOS/Linux 使用 `source .venv/bin/activate`。随后执行：

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m judgment_graph.scripts.smoke
python -m judgment_graph.scripts.verify_contracts
python -m alembic -c alembic.ini upgrade head --sql
```

## 当前运行模式

当前默认使用 StubAnalysisProvider 和 FakeLLM。真实 L1 可通过 L2_ANALYSIS_PROVIDER=sqlalchemy 和 L2_L1_DATABASE_URL 配置。真实模型和完整集成要求见 README 与 docs。上面的 Alembic 命令只生成迁移 SQL，不连接或修改数据库。

M2 HTTP 查询服务从 L2 与 L1 的持久数据库读取，不会回退到 fixture：

```sh
L2_DATABASE_URL=sqlite:////tmp/codepick-m2/l2.db \
L2_L1_DATABASE_URL=sqlite:////tmp/codepick-m2/l1.db \
L2_HTTP_HOST=127.0.0.1 \
L2_HTTP_PORT=8200 \
.venv/bin/python -m judgment_graph.scripts.run_http
```

该服务提供 `/content`、`/content/{id}`、`/recommend` 和 `/companion`。可选
`L2_API_KEY`；跨域场景可设置逗号分隔的 `L2_CORS_ORIGINS`。本机联调应保持
`L2_HTTP_HOST=127.0.0.1`，并让 worker 与 HTTP 使用相同的 `L2_DATABASE_URL`。

列表接口支持最长 200 字符的 `q`，在排序和游标分页前对标题、摘要执行不区分
大小写的包含搜索。例如：

```sh
curl --get http://127.0.0.1:8200/content \
  --data-urlencode 'q=postgres' \
  --data 'limit=10'
```

返回的 `total` 和 `next_cursor` 都基于过滤后的全集。非法游标或排序返回 400，
不存在的详情返回 404，配置缺失、存储故障和 L1 快照缺失分别保持独立错误码。

版本消息模式还需启动 Redis bridge 和 Arq worker：

```sh
L2_REDIS_URL=redis://127.0.0.1:6379/0 \
L2_EVENT_QUEUE=codepick:l1:events \
.venv/bin/python -m judgment_graph.scripts.consume_events

L2_REDIS_URL=redis://127.0.0.1:6379/0 \
L2_DATABASE_URL=sqlite:////tmp/codepick/l2.db \
L2_L1_DATABASE_URL=sqlite:////tmp/codepick/l1.db \
L2_ANALYSIS_PROVIDER=sqlalchemy \
.venv/bin/arq judgment_graph.workers.scoring.worker.WorkerSettings
```

迁移到最新版本时必须包含 `20260916_0004`。`0003` 记录已接受的 L1
`run_id/revision` 并阻止旧任务覆盖新评分；`0004` 为 L2 completion outbox
增加稳定事件 ID、发送/ACK、重试与 dead-letter 状态。

completion relay 与 worker/HTTP 共用 `L2_DATABASE_URL`：

```sh
L2_DATABASE_URL=sqlite:////tmp/codepick/l2.db \
L2_REDIS_URL=redis://127.0.0.1:6379/0 \
L2_COMPLETION_QUEUE=codepick:l2:events \
L2_COMPLETION_ACK_QUEUE=codepick:l2:events:acks \
.venv/bin/python -m judgment_graph.scripts.relay_completed
```

下游只有在事件已持久接收后，才把信封的 `idempotency_key` 写入 ACK 队列。
无 ACK 的事件在超时后以相同 ID 重投，达到最大次数后保留在数据库 dead-letter
状态。正式下游消费者和长期运行监控仍需另行接入。

严格本地集成检查使用仅绑定 localhost 的测试服务：

```sh
docker compose -f docker-compose.integration.yml up -d --wait
L2_INTEGRATION_STRICT=1 python -m judgment_graph.scripts.integration_check
```

## 交接范围

提交包括当前源码、测试、迁移、配置示例与项目文档。依赖目录、构建产物、本地数据库、采集运行数据、日志和凭据不随仓库分发，需要在新环境重新安装或配置。

各层状态与验收证据见项目 README 和 docs；本文提供恢复开发的入口，不代表本次发布重新完成生产环境验收。
