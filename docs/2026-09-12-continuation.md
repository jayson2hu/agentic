# L2 接手审计与继续开发记录（2026-09-12）

## 检出与环境

- 仓库：`jayson2hu/agentic`，位于整个 CodePick 项目目录下，与 `deepdata`、`seek_data`、`pickblog` 和 `codepick-docs` 同级。
- 接手提交：`c05be050228872a429c9fda0be77dda1fa613327`，提交说明 `Prepare CodePick L2 for portable GitHub development`；上一提交 `a50dfb9` 为 L2 初始功能交付。
- 本次使用独立 `.venv`、Python 3.12.14，安装 `.[dev]`。主要实际版本为 LangGraph 1.2.11、Arq 0.28.0、SQLAlchemy 2.0.52、pytest 9.1.1、Ruff 0.16.7、mypy 2.3.1。
- 此记录以本次代码与测试为准；历史 `docs/progress.md` 和 `docs/issues.md` 中的已验收结果属于原开发环境，不代表新机器已经通过相同外部服务验收。

## 本次完成的开发

1. 修复 `EventConsumer` 在管道开始前就记录已处理内容的问题。现仅在完整调用成功返回后记入实例内去重集合。L1 临时不可用或评分已经写入后翻译失败，重新投递仍可继续执行；成功后的同实例重复消息仍被忽略。
2. 修复 `ScoreEnqueuer` 在 Redis 入队前记录成功、以及不同实例和 helper 调用之间无法去重的问题。现使用 Arq 原生 `_job_id=score:{content_id}`，让 Redis 对共享队列执行原子唯一性检查；Arq 返回 `None` 时返回 `False`，连接异常继续上抛供调用方重试。
3. 入队器不再永久记忆内容 ID。Arq 仅在对应 job/result key 保留期间阻止相同任务，保留结束后可再次入队。Arq 默认结果保留期为 3600 秒；本次未修改其默认值。
4. 新增 6 个回归测试，覆盖 Redis 异常重试、多实例及并发 helper 去重、保留期后重新入队、非法事件拒绝、L1 异常重试及部分写入后的管道恢复。修改前已复现丢重试与重复入队；修改后通过。
5. 将 README 的旧机器绝对路径替换为从当前仓库根目录直接使用 `.venv` 的 PowerShell 命令。
6. 解决当前依赖范围内新工具暴露的已有导入格式与类型问题：明确 worker 类变量、seed JSON 返回类型、直接导入 feature verifier，并移除已经不需要的 Arq import ignore。未更改评分与推荐逻辑。

## 本机验证结果

| 检查 | 本次结果 |
| --- | --- |
| 原始完整 pytest 与 80% 覆盖率门槛 | 32 passed，0 skipped，84.24% |
| 修复后最终完整 pytest 与覆盖率门槛 | 38 passed，0 skipped，84.32% |
| Ruff | PASS |
| mypy strict | PASS，39 个源文件 |
| `judgment_graph.scripts.smoke` | `L2 PIPELINE: PASS` |
| `judgment_graph.scripts.feature_matrix` | `L2 FEATURE MATRIX: PASS` |
| `judgment_graph.scripts.verify_contracts` | `L2 CONTRACTS: PASS` |
| Alembic `upgrade head --sql` | PASS，仅生成 SQL，未应用迁移 |
| integration_check | PARTIAL：PostgreSQL 与 Redis 各 1 项 SKIP，packaged worker PASS |
| release_check | PENDING_ENV |

`127.0.0.1:54329` 的 PostgreSQL 和 `127.0.0.1:6389` 的 Redis 均不可达，Docker 引擎未运行。本次没有实际 Redis 入队/worker 消费验证，也没有 PostgreSQL 在线迁移验证。回归中的共享队列替身模拟 Arq 的 `None` 判重和保留期语义；同时核对了安装的 Arq 0.28.0 `enqueue_job` 源码中的 Redis WATCH/MULTI 行为。已有集成脚本的 Redis 检查只是 ping，packaged worker 是直接函数调用，并不能代表 Redis 队列已经端到端消费。

## 跨层实际状态与剩余开发

L2 离线评分、标签、双语产物、复核、推荐和伴读函数基线已经可运行。默认仍为 StubAnalysisProvider、FakeLLM 和内存仓储；E0–E9 文件/契约检查通过不等于整个四层平台已联通。

- **L1 → L2 未实接。** SQL adapter 要求存在 `content_items` 和 `content_base_analysis` 两张表，按内容 ID 联结；L1 当前实现的是 `InMemoryContentBaseAnalysisRepository`，没有实际 SQL 持久化后端。adapter 读取 `tags/topics`，而 L1 产物使用 `base_tags`；正文读取 `text/body/content`，而实际 L0 使用 `clean_text_ref`；来源读取 `source/source_name/url`，而实际 L0 使用 `source_id` 与 `canonical_url`。需要统一落库与字段映射，再以真实相邻层产物增加联合契约测试。
- **L2 → L3 未实接。** L2 只有 `service.py` 中的 `recommend` 和 `companion` Python 函数，没有 L3 HTTP provider 所需的 `/content`、`/content/{id}`、`/recommend` 和 `/companion` 路由。需要补查询/HTTP 边界或经平台契约审查后选择另一统一接入方式。
- **状态、完成事件和运行时接线仍不持久。** SQL 仓储的评分、翻译、复核和领域表写 SQL，但 `statuses`、`_outbox`、`cost_units` 仍保存在实例内存。worker 与 service 默认各建独立的内存仓储；还没有生产共享状态、持久 outbox 或完整事件 relay。
- **版本重处理尚未实现闭环。** 冻结事件只有 `content_id`，无 `graph_version`；`reprocess_needed` 仅列出 rubric 版本变化的内容，当前状态机仍不允许 `COMPLETED → WAIT_SCORE`。本次入队修复消除了入队器永久吞消息，但没有宣称旧内容的新版本重评分已经可用；EventConsumer 的成功去重仍仅在实例内。

下一阶段优先做 L1 SQL 产物与 L2 输入兼容、持久化状态/outbox 和 L3 查询边界，然后用独立测试服务完成四层集成。真实模型能力仍需另外配置和验收；本次只使用 FakeLLM。
