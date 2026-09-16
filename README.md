# CodePick L2 Judgment & Agents

[异地开发指南](DEVELOPMENT.md) · [2026-09-12 接手与验证](docs/2026-09-12-continuation.md) · [M1 SQL 联通与持久化](docs/2026-09-12-m1-integration.md) · [平台总文档与关联仓库](https://github.com/jayson2hu/codepick-docs)

This repository implements the L2 service against the frozen contract:

- input event: `content.analyzed {content_id, run_id, revision}`
- L1 access: `AnalysisProvider.get_for_run(content_id, run_id) -> BaseAnalysis`
- products: `content_vertical_scores`, `content_translations`, `content.completed`
- request-time APIs: `recommend(user_id, vertical, limit)`, `companion(content_id, question)`

Development is standalone. The default path uses `StubAnalysisProvider` fixtures and `FakeLLM`; it does not call real L1 or a real model.

## Versioned Event Processing

L2 accepts each newer L1 `revision` exactly once, reads the matching historical
`run_id` from `l1_processing_runs`, reopens the content for scoring, and emits a
separate `content.completed` event for that revision. A delayed older revision is
ignored; an in-flight older task cannot write products after a newer revision is
accepted.

Bridge L1 Redis events into Arq:

```bash
L2_REDIS_URL=redis://127.0.0.1:6379/0 \
L2_EVENT_QUEUE=codepick:l1:events \
.venv/bin/python -m judgment_graph.scripts.consume_events
```

Run the worker with the same Redis and SQL configuration:

```bash
L2_REDIS_URL=redis://127.0.0.1:6379/0 \
L2_DATABASE_URL=sqlite:////tmp/codepick/l2.db \
L2_L1_DATABASE_URL=sqlite:////tmp/codepick/l1.db \
L2_ANALYSIS_PROVIDER=sqlalchemy \
.venv/bin/arq judgment_graph.workers.scoring.worker.WorkerSettings
```

## M2 HTTP Service

L2 now exposes the persisted M1 results to L3 through FastAPI:

- `GET /content`
- `GET /content/{id}`
- `GET /recommend`
- `GET /companion`

The HTTP process and scoring worker share `L2_DATABASE_URL`; document fields are
read from the versioned L1 snapshot configured by `L2_L1_DATABASE_URL`. Start it
locally with independent SQLite databases:

```bash
L2_DATABASE_URL=sqlite:////tmp/codepick-m2/l2.db \
L2_L1_DATABASE_URL=sqlite:////tmp/codepick-m2/l1.db \
L2_HTTP_HOST=127.0.0.1 \
L2_HTTP_PORT=8200 \
.venv/bin/python -m judgment_graph.scripts.run_http
```

`L2_API_KEY` optionally enables bearer authentication. `L2_CORS_ORIGINS` accepts
a comma-separated allowlist; the local reader normally uses its same-origin proxy
instead. Missing configuration returns 503 `configuration_error`, unavailable SQL
storage returns retryable 503, a missing content ID returns 404, and a completed L2
record whose L1 snapshot is missing returns 502 `upstream_data_error`.

## L1 Provider Switch

The L1 boundary is selected by `judgment_graph.input.create_analysis_provider`.
By default it returns `StubAnalysisProvider`. To read a real L1 database at runtime,
set:

```bash
L2_ANALYSIS_PROVIDER=sqlalchemy
L2_L1_DATABASE_URL=postgresql+psycopg://...
```

The SQLAlchemy adapter now reads the versioned `content_base_analysis` snapshot
written by L1. This path does not require L0 tables or upstream Python imports.
It validates content identity, status, source, body, tags, language and embeddings.
The legacy two-table adapter remains for existing test schemas. See the
[M1 contract and verification](docs/2026-09-12-m1-integration.md).

## Commands

Windows PowerShell, from the repository root after installing `.[dev]` in `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pytest packages/judgment_graph/tests --cov=judgment_graph --cov-report=term-missing --cov-fail-under=80
.\.venv\Scripts\python.exe -m ruff check packages/judgment_graph
.\.venv\Scripts\python.exe -m mypy packages/judgment_graph/judgment_graph
.\.venv\Scripts\python.exe -m judgment_graph.scripts.smoke
.\.venv\Scripts\python.exe -m judgment_graph.scripts.feature_matrix
.\.venv\Scripts\python.exe -m judgment_graph.scripts.verify_contracts
.\.venv\Scripts\python.exe -m judgment_graph.scripts.audit_dod
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head --sql
```

POSIX/CI with `make`:

```bash
make test
make lint
make typecheck
make l2-smoke
make l2-feature-matrix
make l2-contracts
make l2-audit
make l2-integration
make l2-release-check
```

`make l2-smoke` prints `L2 PIPELINE: PASS` when the full offline pipeline passes.

## Persistence

The SQLAlchemy metadata and Alembic migration declare only L2-owned tables:

- `verticals`
- `content_vertical_scores`
- `content_translations`
- `review_queue`
- `content_judgment_state`
- `judgment_outbox`

Migration `20260912_0002` adds durable lifecycle/cost state and completion events.
Migration `20260916_0003` adds accepted L1 run/revision state and versioned completion
uniqueness. Migration `20260916_0004` adds stable completion event IDs, delivery
attempts, sent/ACK timestamps, errors, and persistent dead-letter state. Completed
products and delivery lifecycle remain queryable after recreating the repository in
another process.

The default smoke path uses `InMemoryJudgmentRepository`. M1 adds persistent SQL
status, costs and completion events alongside scores/translations; use the new
Alembic revisions when upgrading an existing L2 database. Completed, cancelled and
review-pending duplicate tasks preserve their state across restarts; newer L1
revisions explicitly rescore while stale events remain no-ops. The completion relay
claims pending rows, publishes stable envelopes to Redis, retries publish failures or
missing ACKs, and dead-letters exhausted events. Integration can switch to
`SqlAlchemyJudgmentRepository` without changing graph or L1 provider code.

Run one relay cycle with:

```bash
L2_DATABASE_URL=postgresql+psycopg://... \
L2_REDIS_URL=redis://127.0.0.1:6379/0 \
python -m judgment_graph.scripts.relay_completed --once
```

The default event queue is `codepick:l2:events`; after durably accepting an envelope,
a downstream consumer must `RPUSH` its `idempotency_key` to
`codepick:l2:events:acks`. Configure names, ACK timeout, attempts, and batch size with
`L2_COMPLETION_QUEUE`, `L2_COMPLETION_ACK_QUEUE`,
`L2_COMPLETION_ACK_TIMEOUT_SEC`, `L2_COMPLETION_MAX_ATTEMPTS`, and
`L2_COMPLETION_BATCH_SIZE`. No production downstream consumer is enabled by default.

`verify_contracts` checks that only the allowed L2 table names are declared, no L3 imports
exist in `judgment_graph`, and every E0-E9 feature has implementation and self-test
artifacts listed in `judgment_graph.scripts.feature_matrix`.

`audit_dod` prints PASS/PENDING status per DoD item. Current pending items require external
PostgreSQL/pgvector and Redis/Arq runtime services.

`release_check` aggregates DoD audit and integration results. It prints
`L2 RELEASE CHECK: PASS` only when live PostgreSQL and Redis checks also pass;
otherwise it reports the remaining `PENDING_ENV` services.

## Integration Environment

Use Docker Compose to run the optional integration services:

```bash
docker compose -f docker-compose.integration.yml up -d
make l2-integration
docker compose -f docker-compose.integration.yml down -v
```

The integration script checks:

- live Alembic upgrade/downgrade against PostgreSQL with pgvector
- Redis connectivity for Arq runtime readiness
- packaged Arq worker contract for legacy `score(ctx, content_id)` and versioned
  `score(ctx, content_id, run_id, revision)`

In CI, `.github/workflows/l2.yml` runs the same check with service containers. Override
service endpoints with `L2_TEST_POSTGRES_HOST`, `L2_TEST_POSTGRES_PORT`,
`L2_TEST_REDIS_HOST`, and `L2_TEST_REDIS_PORT` when needed. Set
`L2_INTEGRATION_STRICT=1` to fail when PostgreSQL or Redis checks are skipped.

## Request-Time Contract

The frozen request APIs are exposed from `judgment_graph.service`:

```python
recommend(user_id: int, vertical: str, limit: int)
companion(content_id: int, question: str)
```

Use `configure_service(...)` to inject the real provider, LLM/router, and repository at runtime.
