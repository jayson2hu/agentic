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

严格本地集成检查使用仅绑定 localhost 的测试服务：

```sh
docker compose -f docker-compose.integration.yml up -d --wait
L2_INTEGRATION_STRICT=1 python -m judgment_graph.scripts.integration_check
```

## 交接范围

提交包括当前源码、测试、迁移、配置示例与项目文档。依赖目录、构建产物、本地数据库、采集运行数据、日志和凭据不随仓库分发，需要在新环境重新安装或配置。

各层状态与验收证据见项目 README 和 docs；本文提供恢复开发的入口，不代表本次发布重新完成生产环境验收。
