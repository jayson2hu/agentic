# L2 Completion Outbox Relay

日期：2026-09-16

## 交付

- `20260916_0004` 为 `judgment_outbox` 增加稳定 `event_id`、尝试次数、
  `sent_at`、`acked_at`、`dead_lettered_at` 和 `last_error`，并回填历史事件 ID。
- relay 以条件更新领取待发送行，避免并行进程重复领取；崩溃后的租约超时会以相同
  ID 重投。
- Redis 信封包含 `topic`、`payload` 和 `idempotency_key`；ACK 超时后允许相同
  ID 重投，消费者必须幂等处理。
- 发布失败立即释放领取供重试；缺失 ACK 超时重投；达到上限后持久 dead-letter。
- 下游持久接收后将事件 ID 写入 ACK 队列，relay 再原子记录 `acked_at`。

## 验收

- L2 全套：113 passed，覆盖率 81.46%，Ruff、mypy、smoke、contracts PASS。
- SQLite + Redis：`content.completed:501-r0` 发布并 ACK，状态持久化 PASS。
- PostgreSQL 16 + Redis 7：迁移升级/降级、发布、ACK 持久化 PASS。
- 正式严格检查新增：
  `PASS: completion-relay - PostgreSQL outbox -> Redis -> ACK persisted`。

Docker 服务仅绑定 `127.0.0.1:54329` 和 `127.0.0.1:6389`；验收后数据库降级到
base，容器和网络已删除。未调用真实模型或业务数据库。

## 边界

本轮实现可靠投递协议和 relay，不包含真实 L3 或其他业务消费者。ACK 验收由严格
集成检查模拟“持久接收后确认”。生产接入仍需明确消费者所有权、监控 dead-letter、
告警、队列保留期和长时 soak。
