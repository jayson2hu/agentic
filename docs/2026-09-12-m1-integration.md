# M1：L1 SQL 输入与 L2 可恢复持久化（2026-09-12）

本轮在 `c05be050228872a429c9fda0be77dda1fa613327` 检出基线及上一轮未提交修复上继续开发。目标是让 L1 的实际 SQL 快照进入 L2，并让不同进程重新打开 L2 数据库后仍能读取评分、翻译、状态、成本和完成事件。上一轮的失败重试与 Arq 任务去重修复保留。

## 当前完成范围

| 环节 | 本轮结果 |
| --- | --- |
| L1 → L2 输入 | 实现 schema_version=1 的单表只读适配，不依赖 L0 `content_items`，不导入 L1 包 |
| 内容产物重启读取 | SQL 评分、翻译、复核数据与持久状态联结，重建 engine/repository 后仍可查询完成内容 |
| 完成事件与状态 | 同一事务提交；插入 outbox 失败会回滚完成状态；按事件类型/内容 ID 唯一 |
| 成本计数 | 成功产品写入与计数同事务；失败写入不计数，重试中已成功的重复写入继续计数 |
| 进程重启后重复投递 | 共享 pipeline 在读取 provider/调用模型前检查持久状态；COMPLETED/CANCELLED/WAIT_REVIEW 直接返回 |
| 封存产物保护 | 评分/翻译写事务先条件 UPDATE 取得状态写锁，COMPLETED/CANCELLED/WAIT_REVIEW 拒绝迟到覆盖且不增加成本 |
| 并发状态与审核 | 状态使用旧状态条件更新，审核使用 pending 条件更新；冲突抛 ConcurrentJudgmentUpdateError 并回滚事务 |
| 版本重评分 | 尚未实现；未带版本的 content.analyzed 继续按 content_id 去重，不重新开启终态 |
| HTTP/真实模型/事件 relay | 本轮未实现；默认仍为 Stub/FakeLLM，实际 SQL 通过依赖注入或 provider 环境变量启用 |

## L1 只读契约

`content_base_analysis` 必须包含以下列：

- `schema_version`：Integer，当前只接受 1。
- `content_id`：L1 的字符串主键。L2 现有接口与表使用整数，因此本轮接入只接受无前导零、ASCII 正整数字符串，数值不超过 BIGINT 上限；不透明 L1 fixture ID 会显式拒绝。
- `input_snapshot`：JSON，对应 L1 `GraphState.to_dict()['content']`。
- `analysis`：JSON，对应 L1 `BaseAnalysis.to_dict()`。
- `graph_version`、`content_hash`、`run_id`：非空字符串，用于校验产物来源信息存在。
- `status`：仅 `WAIT_SCORE` 可读；分析对象内部还须 `status=COMPLETED`。
- `updated_at`：更新时间列。L1 增加的其他列不会影响 reader。

| L2 字段 | 来源与规则 |
| --- | --- |
| content_id | 请求 ID、SQL 行、input_snapshot、analysis 的 ID 必须一致且可无损映射整数 |
| title | input_snapshot.title；必须是字符串，允许空标题 |
| text | input_snapshot.body；必须非空，不再访问 L0 clean_text_ref |
| source | input_snapshot.metadata.source.name，缺失时用 source_url；二者均空则拒绝 |
| language | 优先 analysis.lang；为 null/空字符串时才用 input_snapshot.metadata.lang；必须有可用语言 |
| summary / key_points | analysis 中的同名字段；摘要和要点不能为空 |
| entities | analysis.entities；必须为字符串列表，允许空列表 |
| tags | analysis.base_tags；必须为非空字符串列表 |
| embedding | analysis.embedding；非空数值列表，拒绝布尔值、字符串数值、NaN、Infinity 与溢出 |
| exposure | input_snapshot.metadata.exposure；省略时为 0，存在时必须为非负整数 |

reader 首先检查快照列标记。带快照标记但缺契约列、未知 schema 版本、失败/取消行、身份不一致和字段错误均直接失败，不降级为空内容。没有快照标记的历史双表 schema 仍走 legacy adapter，以兼容原有测试；该路径的宽松字段回退不能作为真实 L1 新接口使用。

## L2 新增表与迁移

本轮仅扩展 L2 自有表白名单，不读写或迁移 L0/L1 表：

- `content_judgment_state(content_id, status, cost_units, updated_at)`：持久生命周期与累计产品写入计数。内容 ID 由上游提供，不自动生成；仅直接写产品、尚未进入生命周期的记录可暂时为 null 状态。
- `judgment_outbox(id, event_type, content_id, payload, created_at)`：持久完成事件，具有 `(event_type, content_id)` 唯一约束。当前仍无发送/确认 relay。

增量迁移为 `20260530_0001 → 20260912_0002`，原始已交付迁移保持不变。升级新增两张表；降级只删除这两张新增表，保留原有四张产品表。已有旧评分并没有可恢复的历史内存状态，因此不猜测并回填为 COMPLETED；本轮验收范围是升级后新处理的内容。

在已正确配置 Alembic 目标数据库的环境执行 `python -m alembic -c alembic.ini upgrade head`。SQLite 本地验证可对首次数据库调用 `repository.create_schema()`；已有库的真实增量测试先建立原四表并标记旧 revision，再升级/降级新 revision。初始迁移是 PostgreSQL 专用，不能把 SQLite 的完整首次迁移验收当成已经完成。

## 独立进程使用入口

```python
from sqlalchemy import create_engine
from judgment_graph.graph.build import run_content_pipeline
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from judgment_graph.scripts.seed_verticals import seed_ai_coding_lens

provider = SqlAlchemyAnalysisProvider(create_engine(l1_database_url))
repository = SqlAlchemyJudgmentRepository(create_engine(l2_database_url))
# 首次 SQLite 开发库；已有 PostgreSQL 库先完成上面的增量迁移。
repository.create_schema()
seed_ai_coding_lens(repository)
run_content_pipeline(content_id, "ai-coding", provider, FileLensLoader(), FakeLLM(), repository)

# 在另一个进程中，重新创建 engine/repository 后直接读取：
reopened = SqlAlchemyJudgmentRepository(create_engine(l2_database_url))
print(reopened.get_status(content_id))
print(reopened.completed_scores("ai-coding"))
print(reopened.translation(content_id, "zh"))
print(reopened.outbox())
print(reopened.cost_units)
```

只读 provider 也可通过 `L2_ANALYSIS_PROVIDER=sqlalchemy` 与 `L2_L1_DATABASE_URL` 选择。worker/service 的默认内存仓储没有自动改成生产数据库；调用方须明确注入 SQL 仓储。`statuses` 和 `cost_units` 读取返回数据库当前值的字典快照，修改该字典不会更新数据库。

同内容的冻结事件重复投递会保留已完成、已取消或待人工审核状态；WAIT_SCORE 的失败内容可从现有阶段重新尝试。`set_status(COMPLETED → WAIT_SCORE)` 仍然拒绝。并发条件写失配会回滚本次状态、审核与事件写入，调用方应从新事务重新读取再重试。评分与翻译的写事务首先对 null/WAIT_SCORE 状态执行条件 UPDATE，以确保 SQLite 也先获得写锁；已封存状态不允许迟到任务改写产物或增加成本。没有生命周期状态的开发 seed 写入仍可创建 null 状态后正常保存。

**本轮运行约定同一 content_id 串行处理。** 如果两个图仍同时处于未完成阶段，仍可能在完成前交错写产物；完整处理所有权、租约或原子发布尚未实现。状态条件更新与封存保护不代表全图并发 exactly-once，也不代表分布式队列和模型调用只执行一次。

## 验证记录

本机使用 Python 3.12.14 与仓库独立 `.venv`，最终完整测试 **94 passed，0 skipped，覆盖率 85.68%**（门槛 80%）。Ruff PASS；mypy strict PASS（40 个源文件）；smoke、contracts、feature-matrix 均 PASS；`git diff --check` PASS。

本轮新增 56 项用例，包括 5 项由独立审查补充的真实文件 SQLite、两套 engine/repository 与 barrier 并发回归。迁移测试实际执行 SQLite 增量 upgrade/downgrade 并保留旧产品，另外验证 PostgreSQL 增量 SQL 的 BIGINT/JSONB/唯一约束与仅新增表的降级范围；完整 PostgreSQL 离线生成也通过。

独立审查复现了“过时 WAIT_REVIEW 写覆盖 COMPLETED”“重启后的重复已完成事件失败”和“已运行的迟到重复图改写完成产物”三个问题；分别由条件写、共享 pipeline 持久状态短路与产品事务封存保护修复，复核通过。真实双 engine + LLM barrier 验证 A 完成 83 分/3 成本/1 事件后，B 的迟到 95 分写入会被拒绝，全部产物、状态、成本与事件保持不变。覆盖内容包括：严格快照输入和坏字段、只读 SQL、不同 engine 重建后的读取、完成状态/outbox 原子回滚、产品/成本回滚、人工复核恢复、不同终态重启重复事件/worker/直接 pipeline，以及 SQLite 真并发冲突和增量迁移。

尚未在本机运行 PostgreSQL/Redis 在线服务验收；Docker 引擎及先前专用服务端口不可用。实际相邻仓库多进程验证证据由平台级 M1 联调记录汇总。真实模型仍为后续单独配置与验收。
